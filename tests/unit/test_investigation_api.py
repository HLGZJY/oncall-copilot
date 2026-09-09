"""调查入口 API 契约测试（issue 07 / M3-T7）。

契约来源：docs/design/m3-investigation-loop-design.md §API 变更表 +
docs/architecture/agent-loop-design.md §证据链数据形状 +
decisions.md D-28（escalated 报告可查 = 转人工落点）/ D-25（进程内注册表，
M4 换读表）/ D-19（status 枚举冻结；status ≠ investigating 仍允许调查）。

覆盖：
- POST /investigate：200 报告键集合精确匹配 / incident 不存在 404 /
  extra=forbid 422 / mock Planner 脚本驱动端到端（steps/hypotheses 序列化）
- 开局锚点：卡片以 alert_ids[0] 组装、时间锚 last_fired_at（D-17）
- GET /investigations/{id}（M4-T3 换读三表，D-25 注册表退役 / D-35 形状权威）：
  有报告 200（与 POST 内存导出逐字段 roundtrip 一致）/ 无报告 404 /
  escalated 可查 / 同 incident 重复调查读到最新一次 / running 行 404
- GET /investigations/{id}/report.md（M4-T4 / D-36 / G7）：200 + text/markdown
  数据直出（步/假设/头部摘要；output_json 不进正文）/ 无记录与 running 行 404 /
  escalated 可导出 / 与 JSON 报告同源对账
- 语义：status ≠ investigating 仍允许调查；缺省组件 503；非预期异常 500 结构化
本票零 LLM 真实调用、零 HTTP 外呼：harness 组件全 mock（照 T6 先例），
API 测试 inproc_asgi（进程内 ASGI，无真实网络 IO，A1 断网不适用）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine, select
from sqlalchemy.orm import Session

from oncall.api.investigation import InvestigationDeps
from oncall.db import AlertEvent, Incident, create_tables
from oncall.db.models import Investigation
from oncall.harness.loop import LoopComponents
from oncall.harness.permission import PermissionGate
from oncall.harness.planner import MockPlanner, PlannerDecision
from oncall.harness.tools.registry import ToolRegistry, register_six_tools
from oncall.harness.tools.schemas import ToolResult, ToolStatus
from oncall.harness.verifier import MockVerifierJudge, Verifier, VerifierVerdict
from oncall.ingest.app import create_app

pytestmark = pytest.mark.inproc_asgi

NOW = datetime(2026, 9, 8, 8, 0, 0, tzinfo=UTC)
FIRED_AT = datetime(2026, 9, 6, 6, 28, 21, tzinfo=UTC)

REPORT_KEYS = {
    "incident_id",
    "termination",
    "conclusion",
    "failure_mode",
    "step_count",
    "total_tokens",
    "total_cost_cny",
    "stop_reason",
    "confidence",
    "opening_card",
    "steps",
    "hypotheses",
}
STEP_KEYS = {
    "step_no",
    "thought",
    "tool",
    "input_json",
    "output_json",
    "output_summary",
    "tokens",
    "cost_cny",
    "latency_ms",
    "ts",
}
HYPOTHESIS_KEYS = {"text", "status", "supporting_steps", "against_steps"}


# ---------------------------------------------------------------------------
# 夹具（照 tests/unit/test_harness_loop.py T6 先例整套复用）
# ---------------------------------------------------------------------------


def _make_handler(tool: str) -> Any:
    def handler(args: Any, *, timeout_seconds: float) -> ToolResult:
        return ToolResult(tool=tool, status=ToolStatus.OK, data={"direction": "up"}, meta={})

    return handler


DEFAULT_HANDLERS: dict[str, Any] = {
    "query_metrics": _make_handler("query_metrics"),
    "search_logs": _make_handler("search_logs"),
    "detect_anomaly": _make_handler("detect_anomaly"),
    "get_topology": _make_handler("get_topology"),
}


def make_components(
    planner: Any,
    *,
    judge_script: list[Any] | None = None,
    judge_default: VerifierVerdict | None = None,
) -> LoopComponents:
    now = lambda: NOW  # noqa: E731  # 测试时钟：mock 期固定即时
    registry = ToolRegistry(now=now)
    register_six_tools(registry, DEFAULT_HANDLERS)
    return LoopComponents(
        planner=planner,
        registry=registry,
        gate=PermissionGate(now=now),
        verifier=Verifier(judge=MockVerifierJudge(script=judge_script, default=judge_default)),
        now=now,
    )


def tool_decision(
    tool: str = "query_metrics",
    *,
    thought: str = "查消费者延迟",
    args: dict[str, Any] | None = None,
) -> PlannerDecision:
    payload: dict[str, Any] = {
        "thought": thought,
        "next_tool": tool,
        "args": args or {"promql": "queue_lag", "start": NOW.isoformat(), "end": NOW.isoformat()},
    }
    return PlannerDecision.model_validate(payload)


def conclusion_decision(text: str) -> PlannerDecision:
    return PlannerDecision.model_validate({"thought": "收束", "conclusion": text})


def make_client(
    script: list[Any] | None = None, *, judge_default: VerifierVerdict | None = None
) -> tuple[TestClient, Any]:
    """组装被测 app：mock harness 组件注入，opening_builder 走组装点默认兜底。

    script 为 None 时不注入组件（/investigate 落 503 分支用）。
    """
    engine = _make_engine()
    components = (
        None
        if script is None
        else make_components(MockPlanner(script=script), judge_default=judge_default)
    )
    app = create_app(engine, investigation=InvestigationDeps(components=components))
    return TestClient(app), engine


def _client_with_components(components: Any) -> tuple[TestClient, Any]:
    engine = _make_engine()
    app = create_app(engine, investigation=InvestigationDeps(components=components))
    return TestClient(app), engine


def _make_engine() -> Any:
    """内存 SQLite 引擎（StaticPool 共享连接，照 test_classify_api 先例）。"""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_tables(engine)
    return engine


def _seed_incident(
    engine: Any, *, n_alerts: int = 1, status: str = "investigating"
) -> tuple[int, list[int]]:
    """建档：n_alerts 条告警 + 1 条 incident（alert_ids 保持顺序以验锚点）。"""
    with Session(engine) as session:
        alert_ids = []
        for i in range(n_alerts):
            row = AlertEvent(
                fingerprint=f"fp-{i}",
                source="alertmanager",
                labels_json={
                    "alertname": "DemoApiGwHighLatency",
                    "job": "api-gw",
                    "severity": "critical",
                },
                annotations_json={},
                fired_at=FIRED_AT,
                status="deduped",
            )
            session.add(row)
            session.flush()
            alert_ids.append(row.id)
        incident = Incident(alert_ids=alert_ids, severity="critical", status=status)
        session.add(incident)
        session.commit()
        return incident.id, alert_ids


class _BoomPlanner:
    """非 PlannerError 族异常源：验证 API 层 500 兜底。"""

    def decide(self, context_view: Any) -> PlannerDecision:
        raise RuntimeError("boom")


# ---------------------------------------------------------------------------
# POST /investigate 契约
# ---------------------------------------------------------------------------


class TestPostInvestigate:
    def test_mock_script_end_to_end_returns_report_shape(self):
        script = [tool_decision(), conclusion_decision("队列堆积导致告警")]
        client, engine = make_client(script)
        incident_id, _alert_ids = _seed_incident(engine)

        resp = client.post("/investigate", json={"incident_id": incident_id})

        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == REPORT_KEYS  # 键集合精确匹配守卫
        assert body["incident_id"] == incident_id
        assert body["termination"] == "concluded"
        assert body["conclusion"] == "队列堆积导致告警"
        assert body["failure_mode"] == "premature_stop"  # 1 步收束无 confirmed → 预标注
        assert body["stop_reason"] == "队列堆积导致告警"
        assert body["confidence"] == 0.0  # 唯一假设被缺省裁决推翻 → 规则值 0.0
        assert body["step_count"] == 1
        assert body["total_tokens"] >= 0
        assert body["total_cost_cny"] >= 0.0
        (step,) = body["steps"]
        assert set(step) == STEP_KEYS
        assert step["step_no"] == 1
        assert step["tool"] == "query_metrics"
        assert isinstance(step["output_json"], dict)
        assert step["output_summary"]
        # 工具步 thought 入池，缺省裁决（证伪导向）→ rejected
        assert [h["status"] for h in body["hypotheses"]] == ["rejected"]
        assert all(set(h) == HYPOTHESIS_KEYS for h in body["hypotheses"])

    def test_confidence_is_confirmed_ratio_of_decided(self):
        script = [
            tool_decision(thought="假设一：消费者延迟"),
            tool_decision(thought="假设二：CPU 饱和"),
            conclusion_decision("CPU 饱和定案"),
        ]
        components = make_components(
            MockPlanner(script=script),
            judge_script=[VerifierVerdict(supported=False, reason="证据不支持")],
            judge_default=VerifierVerdict(supported=True, reason="支持"),
        )
        client, engine = _client_with_components(components)
        incident_id, _ = _seed_incident(engine)

        resp = client.post("/investigate", json={"incident_id": incident_id})

        assert resp.status_code == 200
        body = resp.json()
        assert body["confidence"] == 0.5  # 一 rejected（脚本裁决）+ 一 confirmed
        statuses = {h["text"]: h["status"] for h in body["hypotheses"]}
        assert statuses == {"假设一：消费者延迟": "rejected", "假设二：CPU 饱和": "confirmed"}
        assert all(set(h) == HYPOTHESIS_KEYS for h in body["hypotheses"])

    def test_unknown_incident_returns_404(self):
        client, _engine = make_client([conclusion_decision("x")])
        resp = client.post("/investigate", json={"incident_id": 999})
        assert resp.status_code == 404

    def test_extra_field_rejected_422(self):
        client, engine = make_client([conclusion_decision("x")])
        incident_id, _ = _seed_incident(engine)
        resp = client.post("/investigate", json={"incident_id": incident_id, "batch": "all"})
        assert resp.status_code == 422

    def test_missing_components_returns_503(self):
        """组件缺省 None → 503（不静默降级到 mock，照 classify_runtime 先例）。"""
        client, engine = make_client(None)
        incident_id, _ = _seed_incident(engine)
        resp = client.post("/investigate", json={"incident_id": incident_id})
        assert resp.status_code == 503

    def test_unexpected_harness_exception_returns_structured_500(self):
        """主循环抛非 PlannerError 族异常 → 500 + 结构化错误体，不泄漏堆栈。"""
        client, engine = _client_with_components(_BoomPlanner())
        incident_id, _ = _seed_incident(engine)

        resp = client.post("/investigate", json={"incident_id": incident_id})

        assert resp.status_code == 500
        assert set(resp.json()) == {"detail"}
        assert "RuntimeError" not in resp.json()["detail"]
        assert "traceback" not in resp.json()["detail"].lower()

    def test_incident_not_investigating_still_investigates(self):
        """D-19：status ≠ investigating 仍允许调查，报告如实记录，不回写行。"""
        client, engine = make_client([tool_decision(), conclusion_decision("mitigated 事件复盘")])
        incident_id, _ = _seed_incident(engine, status="mitigated")

        resp = client.post("/investigate", json={"incident_id": incident_id})

        assert resp.status_code == 200
        assert resp.json()["termination"] == "concluded"
        with Session(engine) as session:
            assert session.get(Incident, incident_id).status == "mitigated"  # 不回写


# ---------------------------------------------------------------------------
# 开局锚点（D-17 事件卡片，alert_ids[0] + last_fired_at）
# ---------------------------------------------------------------------------


class TestOpeningAnchor:
    def test_opening_card_built_from_first_alert_with_last_fired_anchor(self):
        script = [tool_decision(), conclusion_decision("收束")]
        client, engine = make_client(script)
        incident_id, alert_ids = _seed_incident(engine, n_alerts=2)

        resp = client.post("/investigate", json={"incident_id": incident_id})

        assert resp.status_code == 200
        card = resp.json()["opening_card"]
        assert card["alert"]["id"] == alert_ids[0]  # primary anchor（D-19）
        assert card["alert"]["last_fired_at"] == FIRED_AT.isoformat()  # 时间锚
        assert set(card) == {"alert", "context", "generated_at"}  # D-17 键面完整
        assert card["generated_at"]

    def test_opening_projection_reaches_decision_view(self):
        """M4-05 注入链 e2e：API opening_builder 产出卡片 → run_investigation → Planner 视图。"""
        script = [tool_decision(), conclusion_decision("收束")]
        planner = MockPlanner(script=script)
        client, engine = _client_with_components(make_components(planner))
        incident_id, _ = _seed_incident(engine)

        resp = client.post("/investigate", json={"incident_id": incident_id})

        assert resp.status_code == 200
        opening = planner.calls[0]["opening"]
        assert set(opening) == {
            "alertname",
            "instance",
            "job",
            "severity",
            "source",
            "status",
            "fired_at",
            "last_fired_at",
            "context_status",
        }
        assert opening["alertname"] == "DemoApiGwHighLatency"
        assert opening["job"] == "api-gw"
        assert opening["severity"] == "critical"
        assert opening["instance"] is None  # 种子告警无 instance → 容缺 None
        assert opening["fired_at"] == FIRED_AT.isoformat()
        assert "items" not in opening  # 投影非全卡片（D-37）
        assert set(opening["context_status"]) == {"metrics", "topology", "changes"}


# ---------------------------------------------------------------------------
# GET /investigations/{incident_id} 契约
# ---------------------------------------------------------------------------


class TestGetInvestigationReport:
    def test_get_returns_latest_report_after_post(self):
        client, engine = make_client([conclusion_decision("第一次收束")])
        incident_id, _ = _seed_incident(engine)
        posted = client.post("/investigate", json={"incident_id": incident_id})

        resp = client.get(f"/investigations/{incident_id}")

        assert resp.status_code == 200
        assert set(resp.json()) == REPORT_KEYS
        assert resp.json() == posted.json()

    def test_get_without_report_returns_404(self):
        client, _engine = make_client([conclusion_decision("x")])
        resp = client.get("/investigations/999")
        assert resp.status_code == 404

    def test_escalated_report_queryable_via_get(self):
        """D-28：第 15 步强制 escalated，报告同经 GET 出口（转人工落点）。"""
        script = [
            tool_decision(
                args={"promql": f"m{i}", "start": NOW.isoformat(), "end": NOW.isoformat()}
            )
            for i in range(20)
        ]
        client, engine = make_client(script)
        incident_id, _ = _seed_incident(engine)
        client.post("/investigate", json={"incident_id": incident_id})

        resp = client.get(f"/investigations/{incident_id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["termination"] == "escalated"
        assert body["step_count"] == 15
        assert body["stop_reason"] and "15" in body["stop_reason"]

    def test_repeated_investigation_overwrites_previous_report(self):
        """D-25/D-31 容量语义：同 incident 重复调查覆盖旧行，GET 读到最新一次。"""
        client, engine = make_client(
            [
                tool_decision(),
                conclusion_decision("第一次收束"),
                tool_decision(),
                conclusion_decision("第二次收束"),
            ]
        )
        incident_id, _ = _seed_incident(engine)
        client.post("/investigate", json={"incident_id": incident_id})

        second = client.post("/investigate", json={"incident_id": incident_id})

        assert second.status_code == 200
        assert second.json()["conclusion"] == "第二次收束"  # 新会话重跑 → 覆盖旧报告
        assert client.get(f"/investigations/{incident_id}").json() == second.json()

    def test_roundtrip_matches_in_memory_export_field_by_field(self):
        """M4-T3 核心回归锚：写库 → GET 读库报告与内存导出（build_report 口径）逐字段一致。

        含 opening_card（随行留存的 generated_at 不漂移）与 confidence（读库
        侧按 hypotheses 行内 status 重算，口径同内存）。
        """
        script = [
            tool_decision(thought="假设一：消费者延迟"),
            tool_decision(thought="假设二：CPU 饱和"),
            conclusion_decision("CPU 饱和定案"),
        ]
        components = make_components(
            MockPlanner(script=script),
            judge_script=[VerifierVerdict(supported=False, reason="证据不支持")],
            judge_default=VerifierVerdict(supported=True, reason="支持"),
        )
        client, engine = _client_with_components(components)
        incident_id, _ = _seed_incident(engine)
        posted = client.post("/investigate", json={"incident_id": incident_id})
        assert posted.status_code == 200

        resp = client.get(f"/investigations/{incident_id}")

        assert resp.status_code == 200
        got = resp.json()
        assert set(got) == REPORT_KEYS
        for key in posted.json():  # 逐字段对账，失败时报出键名
            assert got[key] == posted.json()[key], f"roundtrip 字段不一致: {key}"
        assert got["confidence"] == 0.5  # 一 rejected + 一 confirmed（读库重算）
        # 数据来源钉死为库：opening_card 与 investigations 行留存逐键一致
        with Session(engine) as session:
            row = session.scalar(
                select(Investigation).where(Investigation.incident_id == incident_id)
            )
            assert row.opening_card_json == got["opening_card"]

    def test_running_investigation_row_returns_404(self):
        """行存在但 status=running（调查未收尾，终态字段未落）→ 404，语义同无记录。"""
        client, engine = make_client([conclusion_decision("x")])
        incident_id, _ = _seed_incident(engine)
        with Session(engine) as session:
            session.add(Investigation(incident_id=incident_id, status="running"))
            session.commit()

        resp = client.get(f"/investigations/{incident_id}")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /investigations/{incident_id}/report.md 契约（M4-T4 / D-36 / G7）
# ---------------------------------------------------------------------------


class TestGetMarkdownReport:
    def test_markdown_export_returns_200_text_markdown_with_full_content(self):
        """200 + text/markdown；内容 = 全部步 + 全部假设 + 结论/终态/failure_mode。"""
        script = [
            tool_decision(thought="假设一：消费者延迟"),
            tool_decision(thought="假设二：CPU 饱和"),
            conclusion_decision("CPU 饱和定案"),
        ]
        components = make_components(
            MockPlanner(script=script),
            judge_script=[VerifierVerdict(supported=False, reason="证据不支持")],
            judge_default=VerifierVerdict(supported=True, reason="支持"),
        )
        client, engine = _client_with_components(components)
        incident_id, _ = _seed_incident(engine)
        posted = client.post("/investigate", json={"incident_id": incident_id})
        assert posted.status_code == 200

        resp = client.get(f"/investigations/{incident_id}/report.md")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/markdown")
        text = resp.text
        # 步数 = 库内行数（2 个工具步），格式票面钉死 `## Step N — {tool}`
        assert text.count("## Step ") == 2
        assert "## Step 1 — query_metrics" in text
        assert "## Step 2 — query_metrics" in text
        # 头部摘要：结论 / 终态 / failure_mode / 成本汇总（数据直出，None 原样）
        assert "CPU 饱和定案" in text
        assert "termination: concluded" in text
        assert "failure_mode: None" in text
        assert "step_count: 2" in text
        assert "total_tokens: " in text
        assert "total_cost_cny: " in text
        # 全部假设：status + supporting/against 步号（一 rejected 一 confirmed）
        assert "- [rejected] 假设一：消费者延迟（支持步: 1｜反对步: 无）" in text
        assert "- [confirmed] 假设二：CPU 饱和（支持步: 2｜反对步: 无）" in text
        # D-35 分工：output_json 原始全文不进正文（可回溯走 JSON 报告）
        assert "output_json" not in text
        # ts 与 JSON 报告同口径（Z 后缀，踩坑⑦）
        assert "ts: 2026-09-08T08:00:00Z" in text

    def test_markdown_content_matches_json_report_data(self):
        """Markdown 与 JSON 报告同源（共用读库路径）：关键字段逐一对账。"""
        script = [tool_decision(), conclusion_decision("队列堆积导致告警")]
        client, engine = make_client(script)
        incident_id, _ = _seed_incident(engine)
        client.post("/investigate", json={"incident_id": incident_id})
        body = client.get(f"/investigations/{incident_id}").json()

        resp = client.get(f"/investigations/{incident_id}/report.md")

        text = resp.text
        assert f"incident_id={body['incident_id']}" in text
        assert f"total_tokens: {body['total_tokens']}" in text
        assert f"conclusion: {body['conclusion']}" in text
        for step in body["steps"]:
            assert f"## Step {step['step_no']} — {step['tool']}" in text
            assert step["output_summary"] in text

    def test_escalated_report_exportable_via_markdown(self):
        """D-28 延伸：escalated 报告同经 Markdown 出口（转人工落点可带走）。"""
        script = [
            tool_decision(
                args={"promql": f"m{i}", "start": NOW.isoformat(), "end": NOW.isoformat()}
            )
            for i in range(20)
        ]
        client, engine = make_client(script)
        incident_id, _ = _seed_incident(engine)
        client.post("/investigate", json={"incident_id": incident_id})

        resp = client.get(f"/investigations/{incident_id}/report.md")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/markdown")
        text = resp.text
        assert "termination: escalated" in text
        assert text.count("## Step ") == 15
        assert "step_count: 15" in text

    def test_markdown_without_report_returns_404(self):
        client, _engine = make_client([conclusion_decision("x")])
        resp = client.get("/investigations/999/report.md")
        assert resp.status_code == 404

    def test_markdown_running_row_returns_404(self):
        """running 行 404 语义与 JSON GET 一致（共用 _load_report_body）。"""
        client, engine = make_client([conclusion_decision("x")])
        incident_id, _ = _seed_incident(engine)
        with Session(engine) as session:
            session.add(Investigation(incident_id=incident_id, status="running"))
            session.commit()

        resp = client.get(f"/investigations/{incident_id}/report.md")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# M6-T4 / D-57：开局召回缓存复用出口（POST /investigate 行为扩展，契约不破）
# ---------------------------------------------------------------------------


class TestOpeningRecallReuse:
    def test_fingerprint_canonical_hit_returns_reused_report_without_rerun(self):
        """canonical 子集命中 + 源 mitigated → 复用既有报告（reused_from），不重查不建新调查。"""
        from oncall.knowledge.embedder import MockEmbedder
        from oncall.knowledge.pipeline import KnowledgePipeline
        from oncall.knowledge.retriever import KbRetriever
        from oncall.knowledge.store import InMemoryVectorStore

        engine = _make_engine()
        pipeline = KnowledgePipeline(engine, MockEmbedder(), InMemoryVectorStore())
        with Session(engine) as session:
            src_alert = AlertEvent(
                fingerprint="fp-src",
                source="alertmanager",
                labels_json={"alertname": "DemoApiGwHighLatency", "job": "api-gw", "severity": "critical"},
                annotations_json={},
                fired_at=FIRED_AT,
                status="deduped",
            )
            session.add(src_alert)
            session.flush()
            src_incident = Incident(alert_ids=[src_alert.id], status="mitigated")
            session.add(src_incident)
            session.flush()
            session.add(
                Investigation(
                    incident_id=src_incident.id,
                    status="concluded",
                    conclusion="CPU 飙高",
                    step_count=2,
                )
            )
            session.commit()
            pipeline.ingest_incident(src_incident.id, session)
            session.commit()
            # 新事件：不同指纹行（新时间窗桶）、同 canonical labels（同源故障再来）
            new_alert = AlertEvent(
                fingerprint="fp-new",
                source="alertmanager",
                labels_json={"alertname": "DemoApiGwHighLatency", "job": "api-gw", "severity": "critical"},
                annotations_json={},
                fired_at=FIRED_AT,
                status="deduped",
            )
            session.add(new_alert)
            session.flush()
            new_incident = Incident(alert_ids=[new_alert.id], status="investigating")
            session.add(new_incident)
            session.commit()
            new_id = new_incident.id
            src_id = src_incident.id

        retriever = KbRetriever(engine, MockEmbedder(), InMemoryVectorStore())
        components = make_components(
            MockPlanner(script=[conclusion_decision("不应被走到")]),
        )
        app = create_app(
            engine, investigation=InvestigationDeps(components=components, kb_retriever=retriever)
        )
        client = TestClient(app)
        resp = client.post("/investigate", json={"incident_id": new_id})
        assert resp.status_code == 200
        body = resp.json()
        assert body["reused_from"] == src_id
        assert body["conclusion"] == "CPU 飙高"

    def test_kb_reference_key_absent_without_kb_retriever(self):
        """未注入 kb_retriever → 报告 JSON 无 kb_reference 键（D-35 契约不破）。"""
        client, engine = _client_with_components(
            make_components(MockPlanner(script=[conclusion_decision("收束")]))
        )
        incident_id, _ = _seed_incident(engine, status="investigating")
        resp = client.post("/investigate", json={"incident_id": incident_id})
        assert resp.status_code == 200
        assert "kb_reference" not in resp.json()
