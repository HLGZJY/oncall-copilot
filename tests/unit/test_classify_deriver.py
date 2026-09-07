"""T1 验收②：置信度阈值派生纯函数（G3 ④ / D-07 / D-19）。

边界行为钉死：`confidence < 阈值` → risk；**恰等于阈值 → 原判定**（严格小于）；
`risk_confidence_threshold` 可配覆盖（默认 0.7）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from oncall.classify import (
    ClassificationChannel,
    LLMCallMeta,
    LLMVerdict,
    Verdict,
    derive_verdict,
)

META = LLMCallMeta(model="deepseek-chat", tokens=620, cost_cny=0.003)
NOW = datetime(2026, 9, 7, 3, 0, 0, tzinfo=UTC)


def _llm(verdict: str, confidence: float) -> LLMVerdict:
    return LLMVerdict.model_validate(
        {"verdict": verdict, "confidence": confidence, "reason": "few-shot 对齐"}
    )


def test_confidence_below_threshold_derives_risk() -> None:
    """confidence 0.69 < 0.7 → risk（D-07：不确定落风险，不丢弃）。"""
    result = derive_verdict(_llm("incident", 0.69), META, classified_at=NOW)
    assert result.verdict is Verdict.RISK
    assert result.channel is ClassificationChannel.LLM
    assert result.reason == "few-shot 对齐"


def test_confidence_equal_to_threshold_keeps_verdict() -> None:
    """边界钉死：confidence 恰等于阈值 → 原判定（严格小于才派生）。"""
    result = derive_verdict(_llm("incident", 0.7), META, classified_at=NOW)
    assert result.verdict is Verdict.INCIDENT


def test_confidence_above_threshold_keeps_verdict() -> None:
    result = derive_verdict(_llm("false_positive", 0.95), META, classified_at=NOW)
    assert result.verdict is Verdict.FALSE_POSITIVE


def test_zero_confidence_false_positive_still_derives_risk() -> None:
    """0 置信度误报判同样派生 risk——规则先行兜住确定性，LLM 模糊地带不丢。"""
    result = derive_verdict(_llm("false_positive", 0.0), META, classified_at=NOW)
    assert result.verdict is Verdict.RISK


def test_custom_threshold_overrides_default() -> None:
    """阈值可配：0.5 时 confidence 0.6 不再派生 risk。"""
    result = derive_verdict(
        _llm("incident", 0.6), META, risk_confidence_threshold=0.5, classified_at=NOW
    )
    assert result.verdict is Verdict.INCIDENT


def test_custom_threshold_lower_boundary_is_inclusive() -> None:
    """可配阈值下的等值边界同样走严格小于。"""
    result = derive_verdict(
        _llm("incident", 0.5), META, risk_confidence_threshold=0.5, classified_at=NOW
    )
    assert result.verdict is Verdict.INCIDENT


def test_result_carries_audit_fields() -> None:
    """审计全量字段随派生落位（D-19：model/tokens/cost_cny/classified_at）。"""
    result = derive_verdict(_llm("incident", 0.9), META, classified_at=NOW)
    assert result.model == "deepseek-chat"
    assert result.tokens == 620
    assert result.cost_cny == 0.003
    assert result.classified_at == NOW
