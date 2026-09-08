"""T2 验收测试：证据仓库写入接缝（m4 issue 02）。

契约来源：docs/design/m4-evidence-chain-design.md §技术方案/§C3-C6 论证 +
decisions.md D-33（步进即写、写失败不静默吞、冒泡归类 tool_error）/
D-34（成本会话级汇总 = InvestigationResult.total_* 两字段落库即取）/
D-25（M3 内存契约不倒改，行 id 在接缝层映射，指针接口不变）/ D-31（覆盖语义）。

覆盖（对应 issue 02 验收六条）：
- begin 建 running 行；同 incident 二次调查覆盖旧行（库内 = 最近一次调查）
- 步进即写逐行落库，evidence_steps 与 session.steps 逐字段一致（非批量）
- escalated/aborted 已取证部分完整落库 + investigations 终态行正确
- 行 id 回填后内存契约键集合不变（D-25）
- 落库异常注入：先库后内存顺序钉死——熔断归类 tool_error、无静默、该步不进 session
- 全链路（loop 接线）：3 步调查 + conclusion 全量落库
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from oncall.db import Incident, create_tables
from oncall.db.evidence_repo import EvidenceRepository, persist_hypothesis, persist_step
from oncall.db.models import EvidenceStep as EvidenceStepRow
from oncall.db.models import Hypothesis as HypothesisRow
from oncall.db.models import Investigation
from oncall.harness.loop import FailureMode, InvestigationResult, LoopComponents, run_investigation
from oncall.harness.permission import PermissionGate
from oncall.harness.planner import MockPlanner, PlannerDecision
from oncall.harness.session import (
    EvidenceStep,
    Hypothesis,
    HypothesisStatus,
    InvestigationSession,
    SessionStatus,
)
from oncall.harness.tools.registry import ToolRegistry, register_six_tools
from oncall.harness.tools.schemas import ToolResult, ToolStatus
from oncall.harness.verifier import MockVerifierJudge, Verifier, VerifierVerdict

STEP_TS = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
FINISHED_AT = datetime(2026, 9, 8, 12, 5, 0, tzinfo=UTC)
NOW = FINISHED_AT  # 测试时钟：mock 期固定即时

# D-25 内存契约键集合（不含 id/incident_id 库列）——回填后不得被倒改
MEMORY_STEP_KEYS = {
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


# ---------------------------------------------------------------------------
# 夹具与构造器（engine PRAGMA 模式照 test_db_evidence_models.py 先例）
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    create_tables(engine)
    return engine


@pytest.fixture()
def incident_id(engine):
    with Session(engine) as session:
        incident = Incident(alert_ids=["a" * 64])
        session.add(incident)
        session.commit()
        return incident.id


def make_memory_step(step_no: int = 1, **overrides: Any) -> EvidenceStep:
    fields: dict[str, Any] = {
        "step_no": step_no,
        "thought": f"假设{step_no}：api-gw 延迟抬升，查 CPU 水位",
        "tool": "query_metrics",
        "input_json": {"query": "rate(process_cpu_seconds_total[5m])"},
        "output_json": {"status": "success", "values": [0.83]},
        "output_summary": f"api-gw-1 CPU {60 + step_no}%",
        "tokens": 10 * step_no,
        "cost_cny": 0.001 * step_no,
        "latency_ms": 100 + step_no,
        "ts": STEP_TS,
    }
    fields.update(overrides)
    return EvidenceStep.model_validate(fields)


def make_result(session: InvestigationSession, **overrides: Any) -> InvestigationResult:
    """由会话构造收尾结构（字段口径 = loop._build_result，D-28）。"""
    fields: dict[str, Any] = {
        "incident_id": session.incident_id,
        "status": session.status,
        "conclusion": None,
        "failure_mode": None,
        "step_count": session.step_count,
        "total_tokens": sum(s.tokens for s in session.steps),
        "total_cost_cny": sum(s.cost_cny for s in session.steps),
        "stop_reason": session.stop_reason,
        "steps": list(session.steps),
        "hypotheses": list(session.hypotheses),
    }
    fields.update(overrides)
    return InvestigationResult(**fields)


def _aware(dt: datetime) -> datetime:
    """SQLite 取回时间无 tzinfo，统一补 UTC 后比较。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _investigation_row(engine) -> Investigation:
    with Session(engine) as session:
        return session.scalars(select(Investigation)).one()


class _BrokenRepo:
    """落库异常注入替身：record_step/add_hypothesis 必炸（钉 D-33 熔断语义）。"""

    def record_step(self, _session: Any, _step: Any) -> int:
        msg = "模拟落库故障"
        raise RuntimeError(msg)

    def add_hypothesis(self, _session: Any, _hypothesis: Any) -> int:
        msg = "模拟落库故障"
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# loop 接线用组件（照 test_harness_loop.make_components 先例，mock-only）
# ---------------------------------------------------------------------------


def _handler(tool: str) -> Any:
    def handler(args: Any, *, timeout_seconds: float) -> ToolResult:
        return ToolResult(tool=tool, status=ToolStatus.OK, data={"direction": "up"}, meta={})

    return handler


DEFAULT_HANDLERS: dict[str, Any] = {
    "query_metrics": _handler("query_metrics"),
    "search_logs": _handler("search_logs"),
    "detect_anomaly": _handler("detect_anomaly"),
    "get_topology": _handler("get_topology"),
}


def make_loop_components(script: list[Any]) -> LoopComponents:
    now = lambda: NOW  # noqa: E731
    registry = ToolRegistry(now=now)
    register_six_tools(registry, DEFAULT_HANDLERS)
    return LoopComponents(
        planner=MockPlanner(script=script),
        registry=registry,
        gate=PermissionGate(now=now),
        verifier=Verifier(
            judge=MockVerifierJudge(
                script=None, default=VerifierVerdict(supported=True, reason="mock 证据支持")
            )
        ),
        now=now,
    )


def tool_decision(thought: str, *, promql: str = "queue_lag") -> PlannerDecision:
    """promql 逐决策差异化：同参重复会触发 D-27 重复熔断，干扰落库断言。"""
    return PlannerDecision.model_validate(
        {
            "thought": thought,
            "next_tool": "query_metrics",
            "args": {"promql": promql, "start": NOW.isoformat(), "end": NOW.isoformat()},
        }
    )


def conclusion_decision(text: str) -> PlannerDecision:
    return PlannerDecision.model_validate({"thought": "收束", "conclusion": text})


def assert_steps_match_rows(rows: list[EvidenceStepRow], steps: list[EvidenceStep]) -> None:
    """验收①：evidence_steps 与 session.steps 逐字段一致。"""
    assert [r.step_no for r in rows] == [s.step_no for s in steps]
    for row, step in zip(rows, steps, strict=True):
        assert row.thought == step.thought
        assert row.tool == step.tool
        assert row.input_json == dict(step.input_json)
        assert row.output_json == dict(step.output_json)
        assert row.output_summary == step.output_summary
        assert row.tokens == step.tokens
        assert row.cost == step.cost_cny  # 列名 cost（§4 冻结）↔ 契约字段 cost_cny
        assert row.latency_ms == step.latency_ms
        assert _aware(row.ts) == step.ts


# ---------------------------------------------------------------------------
# begin：running 行 + 覆盖清理（D-31）
# ---------------------------------------------------------------------------


class TestBegin:
    def test_begin_creates_running_row(self, engine, incident_id):
        repo = EvidenceRepository(engine)
        repo.begin(InvestigationSession(incident_id=incident_id))

        row = _investigation_row(engine)
        assert row.incident_id == incident_id
        assert row.status == "running"
        assert row.step_count == 0
        assert row.total_tokens == 0
        assert row.total_cost_cny == 0.0
        assert row.stop_reason is None
        assert row.conclusion is None
        assert row.failure_mode is None
        assert row.finished_at is None

    def test_begin_second_investigation_overwrites_old_rows(self, engine, incident_id):
        """验收⑤：同 incident 二次调查覆盖——旧证据/假设/调查行清理，库内只剩最近一次。"""
        first_session = InvestigationSession(incident_id=incident_id)
        repo = EvidenceRepository(engine)
        repo.begin(first_session)
        persist_step(repo, first_session, make_memory_step(1))
        persist_hypothesis(repo, first_session, Hypothesis(text="第一轮假设"))

        second_session = InvestigationSession(incident_id=incident_id)
        repo.begin(second_session)
        persist_step(repo, second_session, make_memory_step(1, thought="第二轮唯一一步"))

        with Session(engine) as session:
            steps = session.scalars(select(EvidenceStepRow)).all()
            hyps = session.scalars(select(HypothesisRow)).all()
            invs = session.scalars(select(Investigation)).all()
        assert [r.thought for r in steps] == ["第二轮唯一一步"]
        assert hyps == []
        assert len(invs) == 1 and invs[0].status == "running"


# ---------------------------------------------------------------------------
# 步进即写 + 行 id 回填（D-33 / D-25）
# ---------------------------------------------------------------------------


class TestStepWriteSeam:
    def test_steps_persisted_row_by_row_not_batch(self, engine, incident_id):
        """验收①：每 persist 一步立即可查（步进即写非收尾批量）。"""
        session = InvestigationSession(incident_id=incident_id)
        repo = EvidenceRepository(engine)
        repo.begin(session)
        for step_no in (1, 2, 3):
            persist_step(repo, session, make_memory_step(step_no))
            with Session(engine) as db:
                assert len(db.scalars(select(EvidenceStepRow)).all()) == step_no

        with Session(engine) as db:
            rows = db.scalars(select(EvidenceStepRow).order_by(EvidenceStepRow.step_no)).all()
        assert_steps_match_rows(rows, session.steps)

    def test_row_id_backfill_keeps_memory_contract_untouched(self, engine, incident_id):
        """验收③：行 id 在接缝层映射；内存契约键集合不变（D-25 指针接口不破）。"""
        session = InvestigationSession(incident_id=incident_id)
        repo = EvidenceRepository(engine)
        repo.begin(session)
        row_id = persist_step(repo, session, make_memory_step(1))
        assert row_id is True  # persist 成功；行 id 经 repo 对账表查询
        step_row_id = repo.step_row_id(1)

        assert step_row_id is not None and step_row_id > 0
        assert repo.step_row_id(99) is None
        assert set(session.steps[0].model_dump()) == MEMORY_STEP_KEYS

    def test_persist_step_without_repo_writes_memory_only(self, incident_id):
        """缺省 None 不写库（踩坑⑦）：既有 loop 单测零回退的语义保证。"""
        session = InvestigationSession(incident_id=incident_id)
        assert persist_step(None, session, make_memory_step(1)) is True
        assert session.step_count == 1


# ---------------------------------------------------------------------------
# 假设入池 + 状态同步（D-33 / 踩坑⑤ StrEnum 归一）
# ---------------------------------------------------------------------------


class TestHypothesisWriteSeam:
    def test_hypothesis_persisted_with_lowercase_status(self, engine, incident_id):
        session = InvestigationSession(incident_id=incident_id)
        repo = EvidenceRepository(engine)
        repo.begin(session)
        assert (
            persist_hypothesis(
                repo,
                session,
                Hypothesis(text="CPU 饥饿导致阻塞", supporting_steps=[1], against_steps=[2]),
            )
            is True
        )

        with Session(engine) as db:
            row = db.scalars(select(HypothesisRow)).one()
        assert row.status == "active"  # StrEnum 归一小写串，对齐 CHECK 约束
        assert row.supporting_steps == [1]
        assert row.against_steps == [2]
        assert repo.hypothesis_row_id(0) == row.id

    def test_finalize_syncs_hypothesis_verdict_status(self, engine, incident_id):
        """裁决流转（verifier 按序替换 frozen 对象）在 finalize 时同步到库。"""
        session = InvestigationSession(incident_id=incident_id)
        repo = EvidenceRepository(engine)
        repo.begin(session)
        persist_hypothesis(repo, session, Hypothesis(text="CPU 饥饿导致阻塞"))
        updated = session.hypotheses[0].model_copy(update={"status": HypothesisStatus.CONFIRMED})
        session.hypotheses = [updated]
        repo.finalize(
            make_result(session, conclusion="已定位", status=SessionStatus.CONCLUDED),
            finished_at=FINISHED_AT,
        )

        with Session(engine) as db:
            row = db.scalars(select(HypothesisRow)).one()
        assert row.status == "confirmed"


# ---------------------------------------------------------------------------
# 终态收尾（D-28 / D-34）
# ---------------------------------------------------------------------------


class TestFinalize:
    def test_concluded_finalize_writes_session_level_fields(self, engine, incident_id):
        """验收②：concluded——终态/结论/failure_mode/步数 + D-34 会话级成本汇总。"""
        session = InvestigationSession(incident_id=incident_id)
        repo = EvidenceRepository(engine)
        repo.begin(session)
        persist_step(repo, session, make_memory_step(1))
        persist_step(repo, session, make_memory_step(2))
        session.conclude("内存泄漏导致 OOM")
        repo.finalize(
            make_result(
                session,
                conclusion="内存泄漏导致 OOM",
                failure_mode=FailureMode.PREMATURE_STOP,
            ),
            finished_at=FINISHED_AT,
        )

        row = _investigation_row(engine)
        assert row.status == "concluded"
        assert row.conclusion == "内存泄漏导致 OOM"
        assert row.stop_reason == "内存泄漏导致 OOM"
        assert row.failure_mode == "premature_stop"
        assert row.step_count == 2
        assert row.total_tokens == 30  # 10 + 20（D-34：收尾结构 total 落库即取）
        assert row.total_cost_cny == pytest.approx(0.003)
        assert _aware(row.finished_at) == FINISHED_AT

    def test_escalated_via_loop_persists_evidence_gathered(self, engine, incident_id):
        """验收②：escalated——已取证部分完整落库（D-28 转人工不是丢弃）。"""
        session = InvestigationSession(incident_id=incident_id)
        repo = EvidenceRepository(engine)
        repo.begin(session)
        result = run_investigation(
            session,
            replace(
                make_loop_components(
                    [
                        tool_decision("查消费者延迟", promql="lag"),
                        tool_decision("查内存水位", promql="mem"),
                    ]
                ),
                evidence=repo,
            ),
            max_steps=2,
        )
        repo.finalize(result, finished_at=FINISHED_AT)

        assert result.status is SessionStatus.ESCALATED
        assert result.step_count == 2
        with Session(engine) as db:
            rows = db.scalars(select(EvidenceStepRow).order_by(EvidenceStepRow.step_no)).all()
        assert_steps_match_rows(rows, session.steps)
        row = _investigation_row(engine)
        assert row.status == "escalated"
        assert row.step_count == 2
        assert "步数达上限" in (row.stop_reason or "")

    def test_aborted_finalize_keeps_gathered_evidence(self, engine, incident_id):
        session = InvestigationSession(incident_id=incident_id)
        repo = EvidenceRepository(engine)
        repo.begin(session)
        persist_step(repo, session, make_memory_step(1))
        session.abort("总时长超限")
        repo.finalize(
            make_result(session, failure_mode=FailureMode.TIMEOUT),
            finished_at=FINISHED_AT,
        )

        with Session(engine) as db:
            rows = db.scalars(select(EvidenceStepRow)).all()
        assert len(rows) == 1
        row = _investigation_row(engine)
        assert row.status == "aborted"
        assert row.failure_mode == "timeout"


# ---------------------------------------------------------------------------
# 写失败不静默吞（D-33 / 踩坑⑩：先库后内存用测试钉死）
# ---------------------------------------------------------------------------


class TestWriteFailureCircuitBreak:
    def test_persist_step_failure_aborts_and_step_not_in_session(self, incident_id):
        """落库故障注入：返回 False、会话 abort、该步不进 session（无双头状态）。"""
        session = InvestigationSession(incident_id=incident_id)
        assert persist_step(_BrokenRepo(), session, make_memory_step(1)) is False
        assert session.status is SessionStatus.ABORTED
        assert "证据落库失败" in (session.stop_reason or "")
        assert session.steps == []

    def test_persist_hypothesis_failure_aborts_and_not_in_pool(self, incident_id):
        session = InvestigationSession(incident_id=incident_id)
        assert persist_hypothesis(_BrokenRepo(), session, Hypothesis(text="假设")) is False
        assert session.status is SessionStatus.ABORTED
        assert session.hypotheses == []

    def test_loop_write_failure_classifies_tool_error(self, incident_id):
        """踩坑⑩全链路钉死：熔断归类 tool_error、无静默、该步未进 session。"""
        session = InvestigationSession(incident_id=incident_id)
        components = replace(
            make_loop_components([tool_decision("查消费者延迟")]), evidence=_BrokenRepo()
        )
        result = run_investigation(session, components)

        assert result.failure_mode == FailureMode.TOOL_ERROR
        assert result.status is SessionStatus.ABORTED
        assert result.step_count == 0
        assert session.steps == []

    def test_real_db_failure_bubbles_not_silent(self, engine, incident_id):
        """真实 DB 故障（表被删）同样冒泡熔断，不静默吞。"""
        session = InvestigationSession(incident_id=incident_id)
        repo = EvidenceRepository(engine)
        repo.begin(session)
        EvidenceStepRow.__table__.drop(engine)
        assert persist_step(repo, session, make_memory_step(1)) is False
        assert session.status is SessionStatus.ABORTED
        assert session.steps == []


# ---------------------------------------------------------------------------
# 全链路（loop 接线）：3 步调查 + conclusion 全量落库（验收①端到端）
# ---------------------------------------------------------------------------


class TestLoopFullPersistence:
    def test_three_step_concluded_investigation_fully_persisted(self, engine, incident_id):
        session = InvestigationSession(incident_id=incident_id)
        repo = EvidenceRepository(engine)
        repo.begin(session)
        script = [
            tool_decision("查消费者延迟是否抬升", promql="lag"),
            tool_decision("查内存水位是否异常", promql="mem"),
            tool_decision("查磁盘 IO 是否打满", promql="disk"),
            conclusion_decision("队列堆积导致消费延迟"),
        ]
        result = run_investigation(session, replace(make_loop_components(script), evidence=repo))
        repo.finalize(result, finished_at=FINISHED_AT)

        assert result.status is SessionStatus.CONCLUDED
        assert result.step_count == 3
        with Session(engine) as db:
            rows = db.scalars(select(EvidenceStepRow).order_by(EvidenceStepRow.step_no)).all()
            hyps = db.scalars(select(HypothesisRow).order_by(HypothesisRow.id)).all()
        assert_steps_match_rows(rows, session.steps)
        assert len(hyps) == 3
        assert [h.status for h in hyps] == ["confirmed"] * 3  # mock judge 全数支持，裁决同步落库
        row = _investigation_row(engine)
        assert row.status == "concluded"
        assert row.conclusion == "队列堆积导致消费延迟"
        assert row.step_count == 3
        assert row.total_tokens == result.total_tokens
        assert row.total_cost_cny == pytest.approx(result.total_cost_cny)
        assert row.finished_at is not None
