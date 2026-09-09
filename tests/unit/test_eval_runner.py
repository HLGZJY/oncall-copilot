"""M7-T2 验收 · 剧本 runner（m7 issue 02 / D-58 进程内直跑 + mock/真实双档）。

mock 档：MockPlanner 驱动既有 LoopComponents 跑 `run_investigation`，
明细落 eval_runs 第十表（D-62），判定接缝注入 stub（判对错管线归 issue 03）；
真实档：`ONCALL_RUN_M7_EVAL=1` 显式解锁（D-58，M5 issue 08 `ONCALL_RUN_*` 惯例），
env 未设默认 skip——mock 档零真实 API 调用（LLM 一律 mock）。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import StaticPool, create_engine, select
from sqlalchemy.orm import Session

from oncall.db import create_tables
from oncall.db.eval_models import EvalRun
from oncall.eval.golden import GoldenScenario
from oncall.eval.runner import (
    REAL_TIER_ENV,
    Judgment,
    ScenarioCase,
    ScenarioRunSpec,
    require_real_tier,
    run_scenario,
)
from oncall.harness.loop import LoopComponents
from oncall.harness.permission import PermissionGate
from oncall.harness.planner import MockPlanner, PlannerDecision
from oncall.harness.tools.registry import ToolRegistry, register_six_tools
from oncall.harness.tools.schemas import ToolResult, ToolStatus
from oncall.harness.verifier import MockVerifierJudge, Verifier, VerifierError, VerifierVerdict

REAL_DEV = Path("datasets/golden/dev")


def _now() -> datetime:
    return datetime.now(UTC)


def _golden() -> GoldenScenario:
    return GoldenScenario(
        scenario="cpu-spike",
        root_cause="cpu 饱和导致事件循环饥饿",
        remediation="扩容并限流",
    )


def _mock_factory(golden: GoldenScenario, run_idx: int) -> LoopComponents:
    """mock 档装配：取证一步（同源替身）→ 收束结论（确定性，零真实调用）。"""
    anchor = datetime(2026, 9, 6, 6, 28, 21, tzinfo=UTC).isoformat()
    script = [
        PlannerDecision.model_validate(
            {
                "thought": "查询核心指标确认异常方向",
                "next_tool": "query_metrics",
                "args": {"promql": "up", "start": anchor, "end": anchor},
            }
        ),
        PlannerDecision.model_validate({"thought": "证据已足够", "conclusion": golden.root_cause}),
    ]

    def metrics_handler(args: object, *, timeout_seconds: float) -> ToolResult:
        return ToolResult(
            tool="query_metrics",
            status=ToolStatus.OK,
            data={"direction": "up"},
            meta={"source": "mock"},
        )

    def unused_stub(args: object, *, timeout_seconds: float) -> ToolResult:
        """其余取证工具占位（mock 剧本不调用，仅满足六工具注册面）。"""
        return ToolResult(tool="stub", status=ToolStatus.OK, data={}, meta={})

    registry = ToolRegistry(now=_now)
    register_six_tools(
        registry,
        {
            "query_metrics": metrics_handler,
            "search_logs": unused_stub,
            "detect_anomaly": unused_stub,
            "get_topology": unused_stub,
        },
    )
    verdict = VerifierVerdict(supported=True, reason="mock 裁决")
    return LoopComponents(
        planner=MockPlanner(script=script),
        registry=registry,
        gate=PermissionGate(now=_now),
        verifier=Verifier(judge=MockVerifierJudge(default=verdict)),
        now=_now,
    )


def _stub_judger(golden: GoldenScenario, result: object) -> Judgment:
    return Judgment(verdict="top1", judged_by="rule", reason="stub 判定")


def _spec() -> ScenarioRunSpec:
    return ScenarioRunSpec(scenario="cpu-spike", the_set="dev", model="mock-profile")


def _db_session() -> Session:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_tables(engine)
    # expire_on_commit=False：commit 后仍可读已加载属性（runner 返回行供断言）
    return Session(engine, expire_on_commit=False)


# ── 验收①：mock 档单剧本 3 遍跑通、明细落 eval_runs ──


def test_mock_tier_three_runs_persist_detail_rows():
    with _db_session() as session:
        rows = run_scenario(
            case=ScenarioCase(golden=_golden(), spec=_spec()),
            components_factory=_mock_factory,
            judger=_stub_judger,
            db_session=session,
        )
        session.commit()
    assert [row.run_idx for row in rows] == [0, 1, 2]  # D-61 N=3
    assert all(row.verdict == "top1" for row in rows)
    assert all(row.judged_by == "rule" for row in rows)
    assert all(row.scenario == "cpu-spike" for row in rows)
    assert all(row.the_set == "dev" for row in rows)
    assert all(row.model == "mock-profile" for row in rows)
    assert all(row.escalated is False for row in rows)
    assert all(row.duration_s >= 0.0 for row in rows)


def test_mock_tier_consume_investigation_result_columns():
    """消费 InvestigationResult 既有列：步数/tokens/cost/失败模式六值不重造（D-63）。"""
    with _db_session() as session:
        rows = run_scenario(
            case=ScenarioCase(golden=_golden(), spec=_spec()),
            components_factory=_mock_factory,
            judger=_stub_judger,
            db_session=session,
        )
        session.commit()
    assert all(row.step_count == 1 for row in rows)  # 取证一步后收束
    assert all(row.tokens >= 0 and row.cost_cny >= 0.0 for row in rows)
    assert all(row.failure_mode is None for row in rows)  # 假设证实，无失败模式


def test_run_json_detail_traceable():
    """run_json 明细可回溯：结论与终止原因在列（权威数字在上列，D-62）。"""
    with _db_session() as session:
        rows = run_scenario(
            case=ScenarioCase(golden=_golden(), spec=_spec()),
            components_factory=_mock_factory,
            judger=_stub_judger,
            db_session=session,
        )
        session.commit()
    detail = rows[0].run_json
    assert detail["conclusion"] == _golden().root_cause
    assert "stop_reason" in detail
    # 判定痕迹持久化（issue 07 冒烟教训：judge 回退 judge_error 痕迹曾丢失）
    assert detail["judgment"] == {"reason": "stub 判定"}


def test_rows_persisted_via_orm_query():
    """明细真落库：不依赖返回值，ORM 查询可复算（D-62 表是明细权威）。"""
    with _db_session() as session:
        run_scenario(
            case=ScenarioCase(golden=_golden(), spec=_spec()),
            components_factory=_mock_factory,
            judger=_stub_judger,
            db_session=session,
        )
        session.commit()
        persisted = list(session.scalars(select(EvalRun)).all())
    assert len(persisted) == 3


# ── D-61：同剧本 N 遍判定不一致 → unstable 单列不静默平均 ──


def test_unstable_flag_on_verdict_divergence():
    calls: list[int] = []

    def diverging_judger(golden: GoldenScenario, result: object) -> Judgment:
        verdict = "top1" if len(calls) % 2 == 0 else "miss"
        calls.append(len(calls))
        return Judgment(verdict=verdict, judged_by="rule", reason="stub")

    with _db_session() as session:
        rows = run_scenario(
            case=ScenarioCase(golden=_golden(), spec=_spec()),
            components_factory=_mock_factory,
            judger=diverging_judger,
            db_session=session,
        )
        session.commit()
    assert len({row.verdict for row in rows}) > 1
    assert all(row.unstable for row in rows)


# ── issue 07 全量跑批教训：单格异常不炸全批（禁丢弃：error 行可审计）──


def test_single_run_crash_isolated_as_error_row():
    """某遍 run_investigation 抛异常 → 记 error 行（miss/rule/tool_error）不中断其余遍。

    2026-09-09 全量跑批实测：M6-T5 知识污染防线 VerifierError 中途炸批，
    整事务回滚丢 41 分钟真实 spend——禁丢弃纪律要求每格都有痕迹。
    """

    def crashing_factory(golden: GoldenScenario, run_idx: int) -> LoopComponents:
        if run_idx == 1:
            raise VerifierError("假设仅由 query_kb（kb 参考证据）支撑，不可证实")
        return _mock_factory(golden, run_idx)

    with _db_session() as session:
        rows = run_scenario(
            case=ScenarioCase(golden=_golden(), spec=_spec()),
            components_factory=crashing_factory,
            judger=_stub_judger,
            db_session=session,
        )
        session.commit()
    assert [row.run_idx for row in rows] == [0, 1, 2]  # 三遍都有行，不丢格
    error_row = rows[1]
    assert error_row.verdict == "miss"
    assert error_row.judged_by == "rule"
    assert error_row.failure_mode == "tool_error"
    assert "query_kb" in error_row.run_json["error"]  # 痕迹可回溯
    assert rows[0].verdict == "top1" and rows[2].verdict == "top1"  # 好格不受污染


# ── 验收②：真实档 env 门槛（D-58，M5 issue 08 惯例）──


def test_real_tier_gate_raises_without_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(REAL_TIER_ENV, raising=False)
    with pytest.raises(RuntimeError, match=REAL_TIER_ENV):
        require_real_tier()


def test_real_tier_gate_passes_with_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(REAL_TIER_ENV, "1")
    require_real_tier()  # 不抛即通过


@pytest.mark.skipif(
    os.environ.get(REAL_TIER_ENV) != "1",
    reason="真实档须显式 ONCALL_RUN_M7_EVAL=1（D-58；真实装配面由 issue 07 key 门槛票接线）",
)
def test_real_tier_runner_default_skipped():
    require_real_tier()
