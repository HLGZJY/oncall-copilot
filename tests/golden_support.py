"""M3-08 端到端验收共享夹具与判分器（issue 08 / T8；A 段 mock 与 B 段真实同口径）。

- **golden 同源纪律（D-18）**：Fetcher 替身返回值只取自 `datasets/golden/dev/<scenario>.yaml`
  的实测切片（alert_timeline / labels / runs 时间窗）——「查到的证据」与剧本同源；
  **`root_cause` / `investigation_path` / `remediation` 三字段绝不进证据面**
  （B 段真实 Planner 是被评对象，不能把答案喂进 prompt）。
- **判分基准（D-29）**：结论 vs golden `root_cause` 规则匹配级比对——关键实体
  （全部命中）+ 动作词（命中其一）；LLM-as-judge + 人工抽检归 M7，本模块不越界。
- MockPlanner 剧本自 golden `investigation_path` 派生（每步一个工具决策），
  收束结论 = golden `root_cause` 逐字（同源自证，验证判分器与链路本身）；
  MockVerifierJudge 裁决剧本：前 n-1 步假设证伪排除、末步证实（模拟排查收敛）。
- 实测记录（步数/时长）落 `.scratch/tmp/m3-08-*-e2e-report.json` 供 issue 注记回填，
  `.scratch/tmp/` 不进版本库。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from oncall.harness.loop import LoopComponents
from oncall.harness.permission import PermissionGate
from oncall.harness.planner import MockPlanner, PlannerDecision
from oncall.harness.tools.registry import ToolRegistry, register_six_tools
from oncall.harness.tools.schemas import ToolResult, ToolStatus
from oncall.harness.verifier import MockVerifierJudge, Verifier, VerifierVerdict

GOLDEN_DEV_DIR = Path(__file__).resolve().parents[1] / "datasets" / "golden" / "dev"

#: 判分规则（D-29 规则匹配级）：关键实体须全部出现（大小写不敏感），
#: 动作词命中其一即算。实体/动作词取自 golden root_cause 的骨干语义，
#: 以「正确结论必然包含」为准绳收紧，避免宽放导致假命中。
MATCH_RULES: dict[str, dict[str, list[str]]] = {
    "cpu-spike": {"entities": ["cpu"], "actions": ["饱和", "打满", "占满", "跑满"]},
    "slow-sql": {"entities": ["tasks", "锁"], "actions": ["持有", "阻塞", "打满"]},
    "queue-backlog": {"entities": ["队列"], "actions": ["堆积", "积压"]},
}


def load_golden(scenario: str) -> dict[str, Any]:
    """加载 dev 剧本标注（只读 datasets/golden/dev/；holdout/ 禁读）。"""
    path = GOLDEN_DEV_DIR / f"{scenario}.yaml"
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def golden_time_anchor(doc: dict[str, Any]) -> datetime:
    """时间锚（D-17 口径）：首个 run 的首条告警 fired_at。"""
    return datetime.fromisoformat(doc["runs"][0]["alert_timeline"][0]["fired_at"])


def match_root_cause(conclusion: str | None, scenario: str) -> dict[str, Any]:
    """规则匹配级判分：关键实体全中 + 动作词命中其一（D-29；语义判分归 M7）。"""
    rules = MATCH_RULES[scenario]
    text = (conclusion or "").lower()
    entities_hit = [e for e in rules["entities"] if e in text]
    actions_hit = [a for a in rules["actions"] if a in text]
    return {
        "hit": len(entities_hit) == len(rules["entities"]) and bool(actions_hit),
        "entities_hit": entities_hit,
        "entities_missing": [e for e in rules["entities"] if e not in text],
        "actions_hit": actions_hit,
    }


def _timeline_entries(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """展平全部 runs 的告警时间线（stub 证据的唯一数据源，D-18）。"""
    return [
        {"run": i, **entry} for i, run in enumerate(doc["runs"]) for entry in run["alert_timeline"]
    ]


def _window_overlaps(entry: dict[str, Any], start: datetime, end: datetime) -> bool:
    fired = datetime.fromisoformat(entry["fired_at"])
    resolved = datetime.fromisoformat(entry["resolved_at"])
    return fired <= end and resolved >= start


def _direction_in_window(doc: dict[str, Any], start: datetime, end: datetime) -> str:
    """异常方向由 golden 时间窗推导：窗口压到任一 firing 区间 → up，否则 flat。"""
    hit = any(_window_overlaps(entry, start, end) for entry in _timeline_entries(doc))
    return "up" if hit else "flat"


def make_golden_handlers(doc: dict[str, Any]) -> dict[str, Any]:
    """取证四工具 Fetcher 替身：返回值全部派生自 golden timeline（D-18 同源）。

    每个返回值都带完整 `timeline`（runs 时间窗 + 告警条目）——真实 Planner 的
    调查视图缺事件锚点（harness 缺口，issue 08 注记记录），替身侧以「任意一次
    成功调用即可见全量 golden 上下文」补偿，保证评价公平；仍不含
    root_cause / investigation_path / remediation（不把答案喂给被评对象）。
    """

    def timeline_evidence() -> dict[str, Any]:
        return {
            "runs": [
                {"started_at": run["started_at"], "recovered_at": run["recovered_at"]}
                for run in doc["runs"]
            ],
            "alerts": [
                {
                    "alert_name": entry["alert_name"],
                    "labels": entry["labels"],
                    "fired_at": entry["fired_at"],
                    "resolved_at": entry["resolved_at"],
                }
                for entry in _timeline_entries(doc)
            ],
        }

    def make(tool: str, payload_key: str, build: Any) -> Any:
        def handler(args: BaseModel, *, timeout_seconds: float) -> ToolResult:
            return ToolResult(
                tool=tool,
                status=ToolStatus.OK,
                data={
                    "scenario": doc["scenario"],
                    "timeline": timeline_evidence(),
                    payload_key: build(args),
                },
                meta={"source": "golden-dev-timeline"},
            )

        return handler

    def metrics_payload(args: Any) -> dict[str, Any]:
        start = args.start if hasattr(args, "start") else golden_time_anchor(doc)
        end = args.end if hasattr(args, "end") else start
        window_hits = [
            {"alert_name": e["alert_name"], "labels": e["labels"], "fired_at": e["fired_at"]}
            for e in _timeline_entries(doc)
            if _window_overlaps(e, start, end)
        ]
        return {
            "promql": getattr(args, "promql", "n/a"),
            "direction": _direction_in_window(doc, start, end),
            "alerts_in_window": window_hits,
        }

    def logs_payload(args: Any) -> dict[str, Any]:
        return {
            "selector": getattr(args, "selector", "n/a"),
            "lines": [
                f"{e['fired_at']} {e['alert_name']} fired labels={json.dumps(e['labels'])}"
                for e in _timeline_entries(doc)
            ],
        }

    def anomaly_payload(args: Any) -> dict[str, Any]:
        values = getattr(args, "values", [])
        return {
            "direction": _direction_in_window(doc, golden_time_anchor(doc), golden_time_anchor(doc))
            if values
            else "flat",
            "anomaly_windows": [
                {"alert_name": e["alert_name"], "fired_at": e["fired_at"], "run": e["run"]}
                for e in _timeline_entries(doc)
            ],
        }

    def topology_payload(args: Any) -> dict[str, Any]:
        jobs = sorted({e["labels"].get("job", "n/a") for e in _timeline_entries(doc)})
        instances = sorted({e["labels"].get("instance", "n/a") for e in _timeline_entries(doc)})
        return {
            "services": jobs,
            "instances": instances,
            "alert_names": sorted({e["alert_name"] for e in _timeline_entries(doc)}),
        }

    return {
        "query_metrics": make("query_metrics", "metrics", metrics_payload),
        "search_logs": make("search_logs", "logs", logs_payload),
        "detect_anomaly": make("detect_anomaly", "anomaly", anomaly_payload),
        "get_topology": make("get_topology", "topology", topology_payload),
    }


#: mock 剧本的工具与查询词（fixture 常量：按 golden investigation_path 步骤语义对应；
#: 查询名为真实指标族，不构成证据面内容）
MOCK_STEP_QUERIES: dict[str, list[tuple[str, str]]] = {
    "cpu-spike": [
        ("query_metrics", "container_cpu_usage_seconds_total"),
        ("query_metrics", "demo_api_gw_http_request_duration_seconds_p95"),
        ("search_logs", '{job="api-gw"}'),
    ],
    "slow-sql": [
        ("query_metrics", "demo_mysql_pool_in_use"),
        ("query_metrics", "demo_tasks_http_request_duration_seconds_p95"),
        ("search_logs", '{job="api-gw"} mysql'),
    ],
    "queue-backlog": [
        ("query_metrics", "demo_celery_queue_depth"),
        ("query_metrics", "demo_tasks_submitted_total"),
        ("search_logs", '{job="api-gw"} celery'),
    ],
}


def make_mock_planner(doc: dict[str, Any]) -> MockPlanner:
    """MockPlanner 剧本自 investigation_path 派生，收束结论 = golden root_cause 逐字。"""
    anchor = golden_time_anchor(doc)
    iso = anchor.isoformat()
    steps = doc["investigation_path"]
    plan = MOCK_STEP_QUERIES[doc["scenario"]]
    script: list[PlannerDecision] = []
    for i, thought in enumerate(steps):
        tool, query = plan[i]
        if tool == "query_metrics":
            args: dict[str, Any] = {"promql": query, "start": iso, "end": iso}
        elif tool == "search_logs":
            args = {"selector": query, "start": iso, "end": iso}
        else:
            args = {"service": "api-gw"}
        script.append(
            PlannerDecision.model_validate({"thought": thought, "next_tool": tool, "args": args})
        )
    conclusion = PlannerDecision.model_validate(
        {"thought": "证据已足够", "conclusion": doc["root_cause"]}
    )
    script.append(conclusion)
    return MockPlanner(script=script)


def make_e2e_components(planner: Any, doc: dict[str, Any]) -> LoopComponents:
    """组装 LoopComponents：golden 替身 + 裁决剧本（前 n-1 步证伪、末步证实）。

    时钟用真实 UTC now：步骤 ts 可信、时长闸（300s）按真实耗时生效——
    mock 替身即时返回不会误触，B 段真实调查超时熔断语义保持开启。
    """

    def now() -> datetime:
        return datetime.now(UTC)

    registry = ToolRegistry(now=now)
    register_six_tools(registry, make_golden_handlers(doc))
    n_steps = len(doc["investigation_path"])
    verdicts = [VerifierVerdict(supported=False, reason="证据不足以支持该方向")] * (n_steps - 1) + [
        VerifierVerdict(supported=True, reason="证据支持该假设")
    ]
    return LoopComponents(
        planner=planner,
        registry=registry,
        gate=PermissionGate(now=now),
        verifier=Verifier(judge=MockVerifierJudge(script=verdicts, default=verdicts[-1])),
        now=now,
    )


def make_real_components(planner: Any, doc: dict[str, Any]) -> LoopComponents:
    """B 段真实 Planner 组装：golden 替身不变，裁决接缝留 mock（default=证实）。

    判分范围仅 Planner（issue 08 注记）：假设裁决默认证实以让调查正常收敛，
    假设质量复核（真判官 + 人工抽检 20%）归 M7。时钟真实 UTC：时长闸按
    真实耗时生效，真实调查超 300s 熔断转人工的语义保持开启。
    """

    def now() -> datetime:
        return datetime.now(UTC)

    registry = ToolRegistry(now=now)
    register_six_tools(registry, make_golden_handlers(doc))
    verdict = VerifierVerdict(
        supported=True, reason="T8 裁决接缝留 mock（判分范围仅 Planner，M7 换真判官）"
    )
    return LoopComponents(
        planner=planner,
        registry=registry,
        gate=PermissionGate(now=now),
        verifier=Verifier(judge=MockVerifierJudge(default=verdict)),
        now=now,
    )


def append_report(path: Path, record: dict[str, Any]) -> None:
    """实测记录追加写（验收回填源：步数/时长/成本/结论/判分）。"""
    existing: list[dict[str, Any]] = []
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    existing.append(record)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
