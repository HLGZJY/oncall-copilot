"""T1 验收①：verdict 三态枚举与 ClassificationResult 序列化/反序列化。

契约来源：docs/design/m2-denoise-classify-design.md G3/G4、decisions.md D-19、
CONTEXT.md「风险 / Risk = 分类时无法判定的中间态，建档观察但不丢弃」。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from oncall.classify import ClassificationChannel, ClassificationResult, Verdict


def test_verdict_has_exactly_three_states() -> None:
    """verdict 三态冻结：false_positive / risk / incident（D-19，不得扩枚举）。"""
    assert {v.value for v in Verdict} == {"false_positive", "risk", "incident"}


def test_channel_has_exactly_three_values() -> None:
    """channel 三值冻结：rule / llm / llm_error（G4 审计口径）。"""
    assert {c.value for c in ClassificationChannel} == {"rule", "llm", "llm_error"}


def _result(**overrides: object) -> ClassificationResult:
    fields: dict[str, object] = {
        "verdict": Verdict.INCIDENT,
        "confidence": 0.9,
        "reason": "指标与日志三源交叉证实 CPU 飙高",
        "channel": ClassificationChannel.LLM,
        "model": "mock-classifier",
        "tokens": 620,
        "cost_cny": 0.003,
        "classified_at": datetime(2026, 9, 7, 3, 0, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return ClassificationResult.model_validate(fields)


def test_result_roundtrip_serialization() -> None:
    """序列化 → 反序列化逐字段守恒（classification_json 落库的契约前提）。"""
    result = _result()
    payload = result.model_dump(mode="json")
    assert payload["verdict"] == "incident"
    assert payload["channel"] == "llm"
    restored = ClassificationResult.model_validate(payload)
    assert restored == result


def test_result_json_str_roundtrip() -> None:
    """JSON 字符串级 roundtrip（SQLite JSON1 存取形态）。"""
    result = _result()
    restored = ClassificationResult.model_validate_json(result.model_dump_json())
    assert restored == result


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("verdict", "unknown"),
        ("channel", "human"),
    ],
)
def test_result_rejects_bad_enum(field: str, bad: str) -> None:
    """非法枚举值在契约层就被拒（脏数据不进 classification_json）。"""
    with pytest.raises(ValidationError):
        _result(**{field: bad})


@pytest.mark.parametrize("bad_confidence", [-0.1, 1.1])
def test_result_rejects_confidence_out_of_range(bad_confidence: float) -> None:
    """confidence 必须 ∈ [0, 1]（G3 输出契约）。"""
    with pytest.raises(ValidationError):
        _result(confidence=bad_confidence)
