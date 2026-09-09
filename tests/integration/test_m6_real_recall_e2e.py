"""M6-07 真实召回 e2e（T7 / 环境门槛票）：本地 bge-small-zh-v1.5 + 活 demo 栈同故障注入两遍。

**花钱 + 活栈开关**：默认跳过，`ONCALL_RUN_KB_E2E=1` 才执行（照 M2-07 / M3-08 /
M4-08 / M5-T8 先例；真实面含 LLM classify + LLM Planner 调用，成本上限 ¥0.5）。
运行方式（先加载 LLM 三件套）::

    source ~/.oncall-llm-env && ONCALL_RUN_KB_E2E=1 python -m pytest \
        tests/integration/test_m6_real_recall_e2e.py -s

**真实面划分（禁虚构，逐面登记）**：
- 告警链路（真实）：chaos/scenarios/02-slow-sql 注入活 demo 栈 → Prometheus 规则
  → Alertmanager → compose oncall /ingest 落 alert_events（共享 sqlite，
  compose oncall 容器保持 M1 稳态形态，本测试零改动其行为）；
- 分类面（真实）：POST /classify 走规则先行 + 真实 LLM 通道（qwen3.7-flash）
  → incidents 1:1 建档；
- 调查面（真实）：POST /investigate 走真实 OpenAIPlannerClient（M3-T8 实装）
  + 真实取证工具（query_metrics/topology 打 127.0.0.1:9090、search_logs 打
  127.0.0.1:3100）+ kb 开局召回（D-54 确定性前置，不占步数预算）；
  Verifier 裁决接缝留 mock（default=证实，M4-08 先例，真判官归 M7）；
- 处置面（执行/验证真实，干跑触发确定性——M5-T8 同款纪律）：execute_action
  干跑经真实 handler（runbook 库 + proposal 落库接缝）由驱动脚本确定性触发
  （M4-08 实测真实 Planner 从不自发 execute_action，为保处置链可重复不赌
  Planner 自主），confirm approve → 真实 ControlledExecutor（docker/mysql
  CLI 打 demo 容器）→ 真实 RunbookRecoveryVerifier（Prometheus 观察窗轮询，
  M5 轮询代理先例）→ recovered → incident mitigated → D-56 同步触发 kb 入库；
- KB 面（真实）：本地 bge-small-zh-v1.5（D-51，512 维，CPU）真实嵌入 +
  InMemoryVectorStore（D-55 权威在 kb_chunks 表，索引可重建）；第二遍同故障
  复现 → 开局召回指纹精确命中（D-57 缓存复用出口）→ 报告 `reused_from`
  引用历史案例（首步即引用，不重查不建新调查）；向量召回得分/耗时以真实
  retriever.search 实测回填。

**留痕**：实测记录落 `.scratch/tmp/m6-07-real-recall-e2e.json`（不进版本库）。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from m5_real_support import (
    LIVE_PROM_URL,
    LiveServer,
    PollingRecoveryVerifier,
    curl_json,
    mysql_lock_session_id,
    prom_instant,
    run_bash,
    wait_alert_firing,
)
from sqlalchemy import create_engine, desc, select
from sqlalchemy.orm import Session

from oncall.api.investigation import InvestigationDeps
from oncall.api.remediation import RemediationDeps
from oncall.context.config import ContextConfig
from oncall.db import AlertEvent, Incident, create_tables
from oncall.db.models import Investigation as InvestigationRow
from oncall.db.models import KbChunk as KbChunkRow
from oncall.harness.loop import LoopComponents
from oncall.harness.permission import PermissionGate
from oncall.harness.tools.anomaly import detect_anomaly_handler
from oncall.harness.tools.execute import build_execute_action_handler
from oncall.harness.tools.logs import build_search_logs_handler
from oncall.harness.tools.metrics import build_query_metrics_handler
from oncall.harness.tools.registry import ToolRegistry, register_six_tools
from oncall.harness.tools.topology import build_get_topology_handler
from oncall.harness.verifier import MockVerifierJudge, Verifier, VerifierVerdict
from oncall.infra.http import HttpxFetcher
from oncall.infra.llm_planner import OpenAIPlannerClient
from oncall.ingest.app import create_app
from oncall.knowledge.pipeline import KnowledgePipeline
from oncall.knowledge.retriever import build_query_kb_handler
from oncall.knowledge.store import InMemoryVectorStore
from oncall.remediation import service as remediation_service
from oncall.remediation.executor import ControlledExecutor
from oncall.remediation.runbook import load_runbook_library
from oncall.remediation.verifier import RunbookRecoveryVerifier

logging.basicConfig(level=logging.INFO)  # D-56 best-effort 失败走 std logging，必须可见

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ONCALL_RUN_KB_E2E") != "1",
        reason="真实召回 e2e 须显式 ONCALL_RUN_KB_E2E=1（花钱 + 活栈门槛，开工前需用户确认）",
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = REPO_ROOT / ".scratch" / "tmp" / "m6-07-real-recall-e2e.json"
RUNBOOKS_DIR = REPO_ROOT / "remediation" / "runbooks"
CHAOS_DIR = REPO_ROOT / "chaos" / "scenarios"
DB_PATH = REPO_ROOT / "oncall.db"  # compose oncall 同款库（/app/repo 挂载即此文件）

MODEL_DIR = Path(
    os.environ.get("ONCALL_BGE_MODEL_DIR", r"C:\Users\heguo\.cache\oncall-models\bge-small-zh-v1.5")
)
ALERTNAME = "DemoDbPoolSaturated"  # slow-sql 锚定告警（golden 口径）
SCENARIO = "slow-sql"
LOCATOR = "slow-sql/kill-lock-session"
MYSQL_CONTAINER = "oncall-demo-mysql-1"
DRIVER_PORT = 8124  # M5 用 8123，错开
DEDUP_WINDOW_S = int(
    os.environ.get("ONCALL_DEDUP_WINDOW_SECONDS", "600")
)  # compose oncall 同款缺省
LIVE_LOKI_URL = "http://127.0.0.1:3100"

#: 干跑 proposal 落库接缝的「当前轮 incident_id」（驱动单轮串行，无并发）
_CURRENT: dict[str, Any] = {"engine": None, "incident_id": None}


class _BgeEmbedder:
    """真实 bge-small-zh-v1.5 嵌入器（D-51）：本地目录加载，512 维归一化向量。"""

    dimension = 512

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self._model = SentenceTransformer(str(MODEL_DIR))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=True).tolist()


def _chaos_script(scenario: str, name: str) -> Path:
    matches = sorted(CHAOS_DIR.glob(f"*-{scenario}"))
    assert len(matches) == 1, f"chaos 剧本目录不唯一：{scenario}"
    return matches[0] / name


def _make_engine() -> Any:
    """共享活栈库（compose oncall 同一 sqlite 文件）；timeout 兜容器侧短暂写锁。"""
    engine = create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    create_tables(engine)
    return engine


def _proposal_creator(payload: dict[str, Any]) -> str:
    """proposal 存根接缝：干跑落库到共享库（incident 由驱动按轮注入）。"""
    with Session(_CURRENT["engine"]) as session:
        return str(
            remediation_service.create_proposal(
                session,
                incident_id=_CURRENT["incident_id"],
                runbook_slug=payload["runbook_slug"],
                action_id=payload["action_id"],
                dry_run_json=payload["dry_run_json"],
            )
        )


def _build_components(kb_pipeline: KnowledgePipeline) -> LoopComponents:
    """真实调查组件（照 M4-08 / m5_real_support 装配先例；取证面全真）。

    query_metrics/topology 打宿主映射口 9090、search_logs 打 3100（compose
    发布口）；query_kb 挂真实 RAG 检索器（D-23 stub 兑现）；execute_action
    挂真实干跑 handler（runbook 库 + proposal 落库接缝）。
    """
    fetcher = HttpxFetcher()
    config = ContextConfig.from_env({"ONCALL_PROMETHEUS_URL": LIVE_PROM_URL})
    registry = ToolRegistry(now=lambda: datetime.now(UTC))
    register_six_tools(
        registry,
        {
            "query_metrics": build_query_metrics_handler(fetcher, config=config),
            "search_logs": build_search_logs_handler(
                fetcher, loki_url=LIVE_LOKI_URL, timeout=5.0, anchor=datetime.now(UTC)
            ),
            "detect_anomaly": detect_anomaly_handler,
            "get_topology": build_get_topology_handler(fetcher, config=config),
            "query_kb": build_query_kb_handler(kb_pipeline.retriever()),
            "execute_action": build_execute_action_handler(
                runbook_loader=load_runbook_library(RUNBOOKS_DIR).get,
                proposal_creator=_proposal_creator,
            ),
        },
    )
    return LoopComponents(
        planner=OpenAIPlannerClient.from_env(),  # 真实 Planner（M3-T8 实装）
        registry=registry,
        gate=PermissionGate(now=lambda: datetime.now(UTC)),
        verifier=Verifier(
            judge=MockVerifierJudge(
                default=VerifierVerdict(
                    supported=True,
                    reason="M6-T7 真实 e2e：裁决接缝留 mock（M4-08 先例 default=证实）",
                )
            )
        ),
        now=lambda: datetime.now(UTC),
    )


def _wait_alert_row(engine: Any, *, min_id: int, timeout_s: float = 120.0) -> AlertEvent:
    """轮询共享库等 compose oncall 收进锚定告警的新行（AM group_wait 5s + web 延迟）。"""
    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        with Session(engine) as session:
            row = session.scalar(
                select(AlertEvent).where(AlertEvent.id > min_id).order_by(desc(AlertEvent.id))
            )
        if row is not None and (row.labels_json or {}).get("alertname") == ALERTNAME:
            return row
        time.sleep(2.0)
    raise AssertionError(f"{timeout_s}s 内未见新 {ALERTNAME} 告警行落库（ingest 链路异常）")


def _classify_and_get_incident(base: str, engine: Any, alert_id: int) -> int:
    """真实分类（规则先行 + LLM 兜底）→ 取 1:1 建档 incident_id。"""
    summary = curl_json("POST", f"{base}/classify", payload={"alert_id": alert_id})
    assert summary["incident"] >= 1, f"锚定告警未判 incident：{summary}"
    with Session(engine) as session:
        incident = session.scalar(select(Incident).order_by(desc(Incident.id)).limit(1))
        assert incident is not None and alert_id in (incident.alert_ids or []), (
            f"最新 incident 不含 alert_id={alert_id}"
        )
        return incident.id


def _kb_chunks(engine: Any, incident_id: int) -> list[KbChunkRow]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(KbChunkRow).where(
                    KbChunkRow.incident_id == incident_id,
                    KbChunkRow.superseded_at.is_(None),
                )
            )
        )


def _wait_dedup_expiry(engine: Any) -> float:
    """等 compose oncall 指纹去重窗过期（缺省 600s），保证第二遍产生新告警行。

    同指纹窗内会被 compose oncall 合并去重（不产生新 alert_events 行），
    幂等 classify 会跳过已 classified 行 → 建不出第二遍 incident。返回实际
    等待秒数（如实回填，禁虚构）。
    """
    with Session(engine) as session:
        latest = session.scalar(
            select(AlertEvent).order_by(desc(AlertEvent.last_fired_at)).limit(1)
        )
    if latest is None or latest.last_fired_at is None:
        return 0.0
    last_fired = latest.last_fired_at
    if last_fired.tzinfo is None:  # sqlite 落库丢 tzinfo，按 UTC 补齐再比
        last_fired = last_fired.replace(tzinfo=UTC)
    elapsed = (datetime.now(UTC) - last_fired).total_seconds()
    remaining = DEDUP_WINDOW_S - elapsed + 5  # +5s 抓取/评估延迟余量
    if remaining > 0:
        time.sleep(remaining)
        return round(remaining, 1)
    return 0.0


def _inject_and_alert(
    engine: Any, *, min_alert_id: int, round_no: int
) -> tuple[dict[str, Any], int]:
    """净态注入 → 等告警 firing → 等 compose oncall 落新告警行（两轮共用前半段）。

    classify 需驱动服务器就绪后执行（/classify 走真实 LLM 通道），由调用方衔接。
    """
    run_bash(_chaos_script(SCENARIO, "cleanup.sh"))  # 清残留，保证净态注入
    record: dict[str, Any] = {"round": round_no}
    run_bash(_chaos_script(SCENARIO, "inject.sh"), env_extra={"DURATION": "420"})
    record["alert_wait_s"] = wait_alert_firing(HttpxFetcher(), ALERTNAME)
    row = _wait_alert_row(engine, min_id=min_alert_id)
    record["alert_id"] = row.id
    return record, row.id


def _round1_closed_loop(
    engine: Any,
    components: LoopComponents,
    kb_pipeline: KnowledgePipeline,
    *,
    min_alert_id: int,
) -> dict[str, Any]:
    """第一遍：真实分类 → 真实调查 → 干跑 → 真实确认链 → D-56 入库。"""
    library = load_runbook_library(RUNBOOKS_DIR)
    pool_size = prom_instant(HttpxFetcher(), "demo_db_pool_size")
    assert pool_size is not None, "demo_db_pool_size 无样本（活栈门槛）"
    r1, r1_alert_id = _inject_and_alert(engine, min_alert_id=min_alert_id, round_no=1)
    # 执行器 runtime（session_id 实查 processlist，M5-T8 踩坑口径）
    executor = ControlledExecutor(
        runtime_values={"session_id": mysql_lock_session_id(MYSQL_CONTAINER)}
    )
    verifier = PollingRecoveryVerifier(
        RunbookRecoveryVerifier(
            HttpxFetcher(),
            base_url=LIVE_PROM_URL,
            timeout=5.0,
            runbooks=library,
            reference_values={"db_pool_size": pool_size},
        )
    )
    app = create_app(
        engine,
        investigation=InvestigationDeps(components=components),
        remediation=RemediationDeps(
            executor=executor, verifier=verifier, runbooks=library, kb_pipeline=kb_pipeline
        ),
        kb_pipeline=kb_pipeline,
    )
    server = LiveServer(app, DRIVER_PORT)
    base = f"http://127.0.0.1:{DRIVER_PORT}"
    try:
        # 真实分类（规则先行 + LLM 兜底）→ 建档
        _CURRENT["incident_id"] = _classify_and_get_incident(base, engine, r1_alert_id)
        r1["incident_id"] = _CURRENT["incident_id"]

        # 真实调查（真实 Planner + 真实取证 + 开局召回前置）；瞬态失败重试一次
        started = time.monotonic()
        report = curl_json(
            "POST", f"{base}/investigate", payload={"incident_id": r1["incident_id"]}
        )
        if "termination" not in report:  # API 层 500 结构化兜底（调查行留 running）
            report = curl_json(
                "POST", f"{base}/investigate", payload={"incident_id": r1["incident_id"]}
            )
        r1["investigate_s"] = round(time.monotonic() - started, 1)
        assert "termination" in report, f"调查两次未收尾：{report}"
        r1["investigation_status"] = report.get("termination")
        r1["conclusion"] = (report.get("conclusion") or "")[:200]

        # 干跑（确定性触发，M5-T8 同款纪律）→ proposal pending
        dry = components.registry.execute("execute_action", {"action": LOCATOR, "params": {}})
        assert dry.result.status.value == "ok", f"干跑失败：{dry.result.meta}"
        with Session(engine) as session:
            proposals = remediation_service.list_by_incident(session, r1["incident_id"])
            assert proposals, "干跑未产生 proposal"
            proposal_id = proposals[-1].id

        # 真实确认链：approve → 受控执行 → 恢复验证 → recovered/mitigated → kb 入库
        started = time.monotonic()
        confirmed = curl_json(
            "POST",
            f"{base}/remediations/{proposal_id}/confirm",
            payload={"decision": "approve", "reason": "M6-T7 real recall e2e r1"},
        )
        r1["confirm_s"] = round(time.monotonic() - started, 1)
        r1["proposal_status"] = confirmed["status"]
        assert confirmed["status"] == "recovered", confirmed
        with Session(engine) as session:
            assert session.get(Incident, r1["incident_id"]).status == "mitigated"

        chunks = _kb_chunks(engine, r1["incident_id"])
        r1["kb_ingest_mode"] = "auto(D-56)"
        if not chunks:  # 自动触发失败（best-effort 语义）→ 合法 API 回退面，如实记录
            curl_json("POST", f"{base}/kb/ingest/{r1['incident_id']}")
            r1["kb_ingest_mode"] = "manual-fallback(/kb/ingest)"
            chunks = _kb_chunks(engine, r1["incident_id"])
        assert chunks, "kb_chunks 未落库（自动 + 回退均未触发）"
        r1["kb_chunks"] = len(chunks)
        r1["kb_sections"] = sorted({c.section for c in chunks})
    finally:
        server.stop()
        run_bash(_chaos_script(SCENARIO, "cleanup.sh"))
    return r1


def _round2_reuse(
    engine: Any,
    components: LoopComponents,
    kb_pipeline: KnowledgePipeline,
    *,
    r1_incident_id: int,
    min_alert_id: int,
) -> dict[str, Any]:
    """第二遍：等去重窗过期 → 同故障复现 → 开局召回首步引用历史案例（D-57）。"""
    r2: dict[str, Any] = {"round": 2}
    r2["dedup_wait_s"] = _wait_dedup_expiry(engine)
    with Session(engine) as session:
        min_id_r2 = (
            session.scalar(select(AlertEvent.id).order_by(desc(AlertEvent.id)).limit(1)) or 0
        )
    r2_body, r2_alert_id = _inject_and_alert(
        engine, min_alert_id=max(min_alert_id, min_id_r2), round_no=2
    )
    r2.update(r2_body)

    app2 = create_app(
        engine,
        investigation=InvestigationDeps(components=components),
        remediation=RemediationDeps(),  # 复用出口不触处置链
        kb_pipeline=kb_pipeline,
    )
    server2 = LiveServer(app2, DRIVER_PORT)
    base2 = f"http://127.0.0.1:{DRIVER_PORT}"
    try:
        _CURRENT["incident_id"] = _classify_and_get_incident(base2, engine, r2_alert_id)
        r2["incident_id"] = _CURRENT["incident_id"]
        started = time.monotonic()
        report2 = curl_json(
            "POST", f"{base2}/investigate", payload={"incident_id": r2["incident_id"]}
        )
        r2["investigate_s"] = round(time.monotonic() - started, 1)
        # D-57 缓存复用出口：reused_from 指向第一遍事件 = 首步引用历史案例
        assert report2.get("reused_from") == r1_incident_id, (
            f"第二遍未走缓存复用出口：keys={sorted(report2)}"
        )
        r2["reused_from"] = report2["reused_from"]
        with Session(engine) as session:
            new_invs = list(
                session.scalars(
                    select(InvestigationRow).where(
                        InvestigationRow.incident_id == r2["incident_id"]
                    )
                )
            )
        r2["no_reinvestigation"] = len(new_invs) == 0
    finally:
        server2.stop()
        run_bash(_chaos_script(SCENARIO, "cleanup.sh"))

    # ── 真实向量召回得分/耗时（retriever.search，禁虚构）──
    query = f"{ALERTNAME}@mysql tasks 表写锁 连接池打满"
    started = time.monotonic()
    hits = kb_pipeline.retriever().search(query, top_k=3)
    r2["vector_recall"] = {
        "query": query,
        "latency_s": round(time.monotonic() - started, 3),
        "hits": [
            {"incident_id": h.incident_id, "score": h.score, "section": h.section} for h in hits
        ],
    }
    assert hits, "向量召回零命中"
    return r2


def test_real_recall_e2e_same_fault_twice() -> None:
    """两遍注入主链：第一遍闭环入库 → 第二遍开局召回首步引用历史案例（D-57）。"""
    # ── 活栈 + 模型预检 ──
    fetcher = HttpxFetcher()
    assert prom_instant(fetcher, "up") is not None, "Prometheus 不可查（活栈门槛）"
    pool_size = prom_instant(fetcher, "demo_db_pool_size")
    assert pool_size is not None, "demo_db_pool_size 无样本（活栈门槛）"
    assert MODEL_DIR.is_dir(), f"本地模型缺失：{MODEL_DIR}"

    engine = _make_engine()
    _CURRENT["engine"] = engine

    # ── KB 管线（真实 bge + 内存索引；权威在 kb_chunks 表，索引可重建）──
    kb_pipeline = KnowledgePipeline(engine, _BgeEmbedder(), InMemoryVectorStore())
    components = _build_components(kb_pipeline)
    load_runbook_library(RUNBOOKS_DIR)

    with Session(engine) as session:
        min_alert_id = (
            session.scalar(select(AlertEvent.id).order_by(desc(AlertEvent.id)).limit(1)) or 0
        )

    try:
        _wait_dedup_expiry(engine)  # 上轮告警去重窗内会合并，先等窗过期保证新行
        r1 = _round1_closed_loop(engine, components, kb_pipeline, min_alert_id=min_alert_id)
        r2 = _round2_reuse(
            engine,
            components,
            kb_pipeline,
            r1_incident_id=r1["incident_id"],
            min_alert_id=min_alert_id,
        )
    finally:
        run_bash(_chaos_script(SCENARIO, "cleanup.sh"))

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps({"rounds": [r1, r2]}, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8",
    )
