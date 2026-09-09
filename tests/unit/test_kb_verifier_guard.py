"""M6-T5 验收 · 知识污染防线②：kb 证据不可独立证实假设（m6 issue 05 / D-08/D-54）。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from oncall.harness.planner import PlannerDecision
from oncall.harness.session import EvidenceStep, Hypothesis, HypothesisStatus, InvestigationSession
from oncall.harness.verifier import (
    MockVerifierJudge,
    Verifier,
    VerifierError,
    VerifierVerdict,
    check_kb_evidence_support,
    run_rule_checks,
)

_TS = datetime(2026, 9, 8, 8, 0, 0, tzinfo=UTC)

CONCLUSION = PlannerDecision.model_validate({"thought": "收束", "conclusion": "CPU 飙高"})


def _step(step_no: int, tool: str) -> EvidenceStep:
    return EvidenceStep.model_validate(
        {
            "step_no": step_no,
            "thought": "取证",
            "tool": tool,
            "input_json": {},
            "output_json": {},
            "output_summary": "摘要",
            "tokens": 100,
            "cost_cny": 0.0,
            "latency_ms": 100,
            "ts": _TS,
        }
    )


def _session(*steps: EvidenceStep, **hyp_kwargs) -> InvestigationSession:
    session = InvestigationSession(incident_id=1)
    session.steps = list(steps)
    if hyp_kwargs:
        session.hypotheses = [Hypothesis.model_validate(hyp_kwargs)]
    return session


def test_kb_only_support_rejected_by_rule_check() -> None:
    """confirmed 假设仅由 query_kb 步支撑 → 校验 5 不通过。"""
    session = _session(
        _step(1, "query_kb"),
        _step(2, "query_kb"),
        text="历史同款是 CPU 飙高",
        status="confirmed",
        supporting_steps=[1, 2],
        against_steps=[],
    )
    finding = check_kb_evidence_support(session)
    assert not finding.ok
    assert "仅由 query_kb 支撑" in finding.detail
    findings = run_rule_checks(session, CONCLUSION)
    assert any(f.check == "kb_evidence_support" and not f.ok for f in findings)


def test_mixed_support_passes() -> None:
    """kb 步 + 本源证据步共同支撑 → 通过（kb 只作参考的合法形态）。"""
    session = _session(
        _step(1, "query_kb"),
        _step(2, "query_metrics"),
        text="CPU 飙高",
        status="confirmed",
        supporting_steps=[1, 2],
        against_steps=[],
    )
    finding = check_kb_evidence_support(session)
    assert finding.ok


def test_apply_verdict_blocks_kb_only_confirm() -> None:
    """裁决 confirmed 但支撑全为 kb → apply_verdict 拒绝流转（机械拦截面）。"""
    verifier = Verifier(
        judge=MockVerifierJudge(default=VerifierVerdict(supported=True, reason="r"))
    )
    session = _session(_step(1, "query_kb"))
    hyp = Hypothesis.model_validate(
        {"text": "历史同款", "status": "active", "supporting_steps": [1], "against_steps": []}
    )
    session.hypotheses = [hyp]
    with pytest.raises(VerifierError):
        verifier.apply_verdict(session, hyp, VerifierVerdict(supported=True, reason="x"))
    assert session.hypotheses[0].status is HypothesisStatus.ACTIVE


def test_active_or_empty_support_not_flagged() -> None:
    """active 假设 / 零支撑 confirmed 不在防线②射程（归校验 3/4 管）。"""
    session = _session(_step(1, "query_kb"))
    finding = check_kb_evidence_support(session)
    assert finding.ok
