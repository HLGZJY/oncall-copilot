"""M3-05 Verifier 测试（D-26 定案口径）。

五块：规则层四类校验正反例（ToolResult 状态 / args-schema / 引用存在性 /
hallucination）/ 裁决契约 {supported, reason} 两态与零计划产出 / 计数器 ≤3
（第 4 次被拒 + 降级路径）/ 假设流转写回三断言（rejected / confirmed /
引用合法前置）/ Mock 三态夹具混排回放（支持 / 推翻 / 超时，供 T6 复用）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from oncall.harness.planner import PlannerDecision
from oncall.harness.session import (
    EvidenceStep,
    Hypothesis,
    HypothesisStatus,
    InvestigationSession,
)
from oncall.harness.tools.schemas import ToolResult, ToolStatus
from oncall.harness.verifier import (
    FALSIFICATION_PROMPT_TEMPLATE,
    JUDGE_CALL_LIMIT,
    Finding,
    MockVerifierJudge,
    Verifier,
    VerifierError,
    VerifierOutputError,
    VerifierTimeoutError,
    VerifierVerdict,
    check_args_schema,
    check_hallucination,
    check_step_refs,
    check_tool_result,
    run_rule_checks,
)

# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

_TS = datetime(2026, 9, 8, 8, 0, 0, tzinfo=UTC)


def make_step(step_no: int = 1) -> EvidenceStep:
    return EvidenceStep.model_validate(
        {
            "step_no": step_no,
            "thought": "队列堆积，先查消费者延迟",
            "tool": "query_metrics",
            "input_json": {"component": "order-service", "query": "queue_lag"},
            "output_json": {"direction": "up"},
            "output_summary": "queue_lag 持续上升",
            "tokens": 120,
            "cost_cny": 0.0,
            "latency_ms": 300,
            "ts": _TS,
        }
    )


def make_session(step_count: int = 1) -> InvestigationSession:
    session = InvestigationSession(incident_id=1)
    for no in range(1, step_count + 1):
        session.record_step(make_step(step_no=no))
    return session


def make_tool_decision() -> PlannerDecision:
    return PlannerDecision.model_validate(
        {
            "thought": "查消费者延迟",
            "next_tool": "query_metrics",
            "args": {"promql": "queue_lag", "start": _TS.isoformat(), "end": _TS.isoformat()},
        }
    )


def make_conclusion_decision() -> PlannerDecision:
    return PlannerDecision.model_validate({"thought": "收束", "conclusion": "队列堆积"})


def make_hypothesis(**overrides: object) -> Hypothesis:
    payload: dict[str, object] = {
        "text": "消费者延迟导致队列堆积",
        "supporting_steps": [1],
        "against_steps": [],
    }
    payload.update(overrides)
    return Hypothesis.model_validate(payload)


# ---------------------------------------------------------------------------
# 规则层 1：ToolResult 状态检查
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [ToolStatus.OK, ToolStatus.EMPTY])
def test_tool_result_pass(status: ToolStatus) -> None:
    finding = check_tool_result(ToolResult(tool="query_metrics", status=status))
    assert finding.ok


@pytest.mark.parametrize("status", [ToolStatus.ERROR, ToolStatus.UNAVAILABLE])
def test_tool_result_finding(status: ToolStatus) -> None:
    finding = check_tool_result(ToolResult(tool="query_kb", status=status))
    assert not finding.ok
    assert "query_kb" in finding.detail


# ---------------------------------------------------------------------------
# 规则层 2：args 与工具 schema 合法性
# ---------------------------------------------------------------------------


def test_args_schema_ok() -> None:
    assert check_args_schema(make_tool_decision()).ok


def test_args_schema_conclusion_branch_ok() -> None:
    assert check_args_schema(make_conclusion_decision()).ok


def test_args_schema_invalid_args() -> None:
    bad = PlannerDecision.model_validate(
        {"thought": "缺 promql", "next_tool": "query_metrics", "args": {"step": "15s"}}
    )
    finding = check_args_schema(bad)
    assert not finding.ok


def test_args_schema_unknown_tool() -> None:
    bad = PlannerDecision.model_validate(
        {"thought": "未知工具", "next_tool": "no_such_tool", "args": {}}
    )
    finding = check_args_schema(bad)
    assert not finding.ok
    assert "no_such_tool" in finding.detail


# ---------------------------------------------------------------------------
# 规则层 3：假设-证据步引用存在性
# ---------------------------------------------------------------------------


def test_step_refs_ok() -> None:
    session = make_session(step_count=2)
    session.add_hypothesis(make_hypothesis(supporting_steps=[1], against_steps=[2]))
    assert check_step_refs(session).ok


def test_step_refs_dangling() -> None:
    session = make_session(step_count=1)
    session.add_hypothesis(make_hypothesis(against_steps=[9]))
    finding = check_step_refs(session)
    assert not finding.ok
    assert "9" in finding.detail


# ---------------------------------------------------------------------------
# 规则层 4：hallucination 判定（收束分支 + 引用分支都覆盖）
# ---------------------------------------------------------------------------


def test_hallucination_conclusion_without_evidence() -> None:
    session = make_session(step_count=0)
    finding = check_hallucination(session, conclusion="无证据下结论")
    assert not finding.ok


def test_hallucination_conclusion_with_evidence_ok() -> None:
    finding = check_hallucination(make_session(), conclusion="有证据收束")
    assert finding.ok


def test_hallucination_missing_referenced_steps() -> None:
    finding = check_hallucination(make_session(), referenced_steps=[3, 99])
    assert not finding.ok
    assert "99" in finding.detail


def test_hallucination_no_refs_ok() -> None:
    assert check_hallucination(make_session()).ok


# ---------------------------------------------------------------------------
# 规则层组合
# ---------------------------------------------------------------------------


def test_run_rule_checks_composes_five() -> None:
    """M6-T5 起规则层五类校验（新增 kb_evidence_support，知识污染防线②）。"""
    session = make_session()
    session.add_hypothesis(make_hypothesis())
    result = ToolResult(tool="query_metrics", status=ToolStatus.ERROR)
    findings = run_rule_checks(session, make_tool_decision(), result=result, referenced_steps=[1])
    assert [f.check for f in findings] == [
        "tool_result",
        "args_schema",
        "step_refs",
        "hallucination",
        "kb_evidence_support",
    ]
    assert not findings[0].ok
    assert all(f.ok for f in findings[1:])


# ---------------------------------------------------------------------------
# 裁决契约：{supported, reason}，零计划产出
# ---------------------------------------------------------------------------


def test_verdict_contract() -> None:
    verdict = VerifierVerdict.model_validate({"supported": False, "reason": "证据不足"})
    assert verdict.supported is False
    assert verdict.reason == "证据不足"


def test_verdict_forbids_plan_fields() -> None:
    with pytest.raises(ValidationError):
        VerifierVerdict.model_validate(
            {"supported": True, "reason": "r", "next_tool": "query_metrics"}
        )


def test_verdict_requires_reason() -> None:
    with pytest.raises(ValidationError):
        VerifierVerdict.model_validate({"supported": True})


# ---------------------------------------------------------------------------
# 计数器 ≤3：第 4 次被拒 + 降级路径
# ---------------------------------------------------------------------------


def test_judge_call_limit_and_degrade() -> None:
    judge = MockVerifierJudge(
        script=[VerifierVerdict(supported=True, reason="r1")] * JUDGE_CALL_LIMIT
    )
    verifier = Verifier(judge=judge)
    session = make_session()
    hyp = make_hypothesis()
    for _ in range(JUDGE_CALL_LIMIT):
        outcome = verifier.request_judgment(session, hyp, timing="hypothesis")
        assert outcome.verdict is not None
        assert not outcome.degraded
    fourth = verifier.request_judgment(session, hyp, timing="conclusion")
    assert fourth.verdict is None
    assert fourth.degraded
    assert len(judge.calls) == verifier.calls_used(session) == JUDGE_CALL_LIMIT


def test_judge_timeout_degrades_not_raises() -> None:
    verifier = Verifier(judge=MockVerifierJudge(script=[VerifierTimeoutError("超时")]))
    outcome = verifier.request_judgment(make_session(), make_hypothesis(), timing="hypothesis")
    assert outcome.verdict is None
    assert outcome.degraded
    assert issubclass(VerifierTimeoutError, VerifierError)


def test_no_judge_degrades_immediately() -> None:
    verifier = Verifier()
    outcome = verifier.request_judgment(make_session(), make_hypothesis(), timing="hypothesis")
    assert outcome.degraded and outcome.verdict is None


def test_counter_is_per_session() -> None:
    judge = MockVerifierJudge(script=[VerifierVerdict(supported=True, reason="r")])
    verifier = Verifier(judge=judge)
    a, b = make_session(), make_session()
    hyp = make_hypothesis()
    verifier.request_judgment(a, hyp, timing="hypothesis")
    outcome_b = verifier.request_judgment(b, hyp, timing="hypothesis")
    assert outcome_b.verdict is not None
    assert verifier.calls_used(a) == 1 and verifier.calls_used(b) == 1


# ---------------------------------------------------------------------------
# 假设流转写回三断言
# ---------------------------------------------------------------------------


def test_rejected_write_back() -> None:
    session = make_session()
    hyp = make_hypothesis()
    session.add_hypothesis(hyp)
    verifier = Verifier()
    updated = verifier.apply_verdict(
        session, hyp, VerifierVerdict(supported=False, reason="证据矛盾")
    )
    assert session.hypotheses[0] is updated
    assert updated.status is HypothesisStatus.REJECTED
    assert hyp.status is HypothesisStatus.ACTIVE  # 原 frozen 对象未被改写


def test_confirmed_write_back() -> None:
    session = make_session()
    session.add_hypothesis(make_hypothesis())
    verifier = Verifier()
    updated = verifier.apply_verdict(
        session, session.hypotheses[0], VerifierVerdict(supported=True, reason="证据一致")
    )
    assert updated.status is HypothesisStatus.CONFIRMED


def test_write_back_requires_valid_refs() -> None:
    session = make_session()
    hyp = make_hypothesis(supporting_steps=[42])
    session.add_hypothesis(hyp)
    verifier = Verifier()
    with pytest.raises(VerifierError, match="42"):
        verifier.apply_verdict(session, hyp, VerifierVerdict(supported=True, reason="r"))
    assert session.hypotheses[0].status is HypothesisStatus.ACTIVE


# ---------------------------------------------------------------------------
# Mock 三态夹具混排回放
# ---------------------------------------------------------------------------


def test_mock_judge_mixed_script_replay() -> None:
    judge = MockVerifierJudge(
        script=[
            VerifierVerdict(supported=True, reason="证据一致"),
            VerifierVerdict(supported=False, reason="证据矛盾"),
            VerifierTimeoutError("超时"),
        ],
        default=VerifierVerdict(supported=False, reason="剧本耗尽，缺省不通过"),
    )
    session = make_session()
    verifier = Verifier(judge=judge, call_limit=10)  # 放宽上限以单独测剧本耗尽回落
    hyp = make_hypothesis()
    assert verifier.request_judgment(session, hyp, timing="hypothesis").verdict is not None
    assert judge.calls[0]["timing"] == "hypothesis"
    second = verifier.request_judgment(session, hyp, timing="conclusion")
    assert second.verdict is not None and not second.verdict.supported
    third = verifier.request_judgment(session, hyp, timing="hypothesis")
    assert third.degraded  # 超时夹具 → 降级不抛错
    exhausted = verifier.request_judgment(session, hyp, timing="hypothesis")
    assert exhausted.verdict is not None and not exhausted.verdict.supported


def test_mock_judge_output_error_is_verifier_error() -> None:
    assert issubclass(VerifierOutputError, VerifierError)


# ---------------------------------------------------------------------------
# prompt 模板与接缝可注入
# ---------------------------------------------------------------------------


def test_falsification_prompt_is_module_constant_and_standalone() -> None:
    assert len(FALSIFICATION_PROMPT_TEMPLATE) > 200
    rendered = FALSIFICATION_PROMPT_TEMPLATE.format(hypothesis="H", evidence="E")
    assert "证伪" in rendered and "H" in rendered and "E" in rendered


def test_finding_is_plain_structure() -> None:
    finding = Finding(check="x", ok=True, detail="d")
    assert (finding.check, finding.ok, finding.detail) == ("x", True, "d")
