"""LLM 通道编排测试（issue 03 / G3 ④⑤ + G8 成本口径）。

钉死的行为：
- 正常路径：Pydantic 校验通过 → derive_verdict 派生终判（channel=llm）
- 畸形输出：重试 ≤2 次（总尝试 3 次）→ 仍失败落 risk（channel=llm_error），
  confidence/reason/tokens/cost 兜底取值逐项断言
- 超时：30s 上限（mock 直接抛 LLMTimeoutError 不真等），立即落 risk 不重试、不抛出
- 成本：G8 估算法 cost = in_tokens × 输入单价 + out_tokens × 输出单价（mock 单价）
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from oncall.classify import (
    ClassificationChannel,
    LLMOutputError,
    LLMTimeoutError,
    LLMVerdict,
    MockLLMClassifier,
    Verdict,
)
from oncall.classify.llm import DEFAULT_LLM_TIMEOUT_SECONDS, LLMChannel, LLMChannelOptions
from oncall.classify.llm.channel import (
    FALLBACK_REASON_OUTPUT_ERROR,
    FALLBACK_REASON_TIMEOUT,
    MOCK_MODEL_PRICING_CNY_PER_1K,
)
from oncall.classify.llm.fewshot import FewShotSample
from oncall.classify.llm.prompt import estimate_tokens

CARD: dict[str, Any] = {
    "alert": {
        "id": 1,
        "labels": {"alertname": "DemoApiGwHighLatency", "job": "api-gw"},
        "fired_at": "2026-09-07T06:28:21Z",
        "last_fired_at": "2026-09-07T06:31:21Z",
        "dedup_count": 3,
    },
    "context": {},
    "generated_at": "2026-09-07T06:31:30Z",
}

SAMPLES = [
    FewShotSample(
        scenario="cache-avalanche",
        alert_card={"alert": {"labels": {"alertname": "RedisHitRateLow"}, "fired_at": "t1"}},
        verdict="incident",
        reason="缓存命中率骤降导致 DB 压力升高",
    )
]

MODEL = "deepseek-chat"


def fixed_at() -> datetime:
    return datetime(2026, 9, 7, 6, 31, 30, tzinfo=UTC)


def make_channel(mock: MockLLMClassifier, *, model: str = MODEL) -> LLMChannel:
    return LLMChannel(
        mock,
        model=model,
        samples=SAMPLES,
        options=LLMChannelOptions(classified_at=fixed_at()),
    )


def test_happy_path_incident() -> None:
    channel = make_channel(MockLLMClassifier())  # 默认 incident / 0.8

    result = channel.classify(CARD)

    assert result.verdict is Verdict.INCIDENT
    assert result.channel is ClassificationChannel.LLM
    assert result.model == MODEL
    assert result.confidence == 0.8
    assert result.tokens > 0
    assert result.cost_cny > 0
    # G8 审计：LLMCallMeta 与结论字段一致
    assert channel.last_meta is not None
    assert channel.last_meta.model == MODEL
    assert channel.last_meta.tokens == result.tokens
    assert channel.last_meta.cost_cny == result.cost_cny


def test_low_confidence_derives_risk_via_llm_channel() -> None:
    channel = make_channel(MockLLMClassifier(confidence=0.5))

    result = channel.classify(CARD)

    assert result.verdict is Verdict.RISK
    assert result.channel is ClassificationChannel.LLM  # 派生 risk ≠ llm_error 兜底


def test_cost_formula_mock_pricing() -> None:
    """G8 估算法：cost = (in_tokens × in_price + out_tokens × out_price) / 1000。"""
    channel = make_channel(MockLLMClassifier())

    result = channel.classify(CARD)

    in_price, out_price = MOCK_MODEL_PRICING_CNY_PER_1K[MODEL]
    prompt_tokens = estimate_tokens(channel.last_prompt.system + channel.last_prompt.user)
    out_tokens = result.tokens - prompt_tokens
    expected = round((prompt_tokens * in_price + out_tokens * out_price) / 1000, 6)

    assert result.tokens == prompt_tokens + out_tokens
    assert result.cost_cny == expected


def test_malformed_output_retries_then_succeeds() -> None:
    verdict = LLMVerdict(verdict=Verdict.INCIDENT, confidence=0.85, reason="恢复成功")
    mock = MockLLMClassifier(script=[LLMOutputError("非 JSON"), verdict])
    channel = make_channel(mock)

    result = channel.classify(CARD)

    assert len(mock.calls) == 2  # 首次失败 + 1 次重试
    assert result.verdict is Verdict.INCIDENT
    assert result.channel is ClassificationChannel.LLM


def test_malformed_output_exhausted_falls_to_risk_with_pinned_fallbacks() -> None:
    mock = MockLLMClassifier(
        script=[LLMOutputError("坏 1"), LLMOutputError("坏 2"), LLMOutputError("坏 3")]
    )
    channel = make_channel(mock)

    result = channel.classify(CARD)

    assert len(mock.calls) == 3  # 首次 + 重试 ≤2 次，共 3 次尝试
    assert result.verdict is Verdict.RISK
    assert result.channel is ClassificationChannel.LLM_ERROR
    # 兜底取值钉死（0 漏报保证，issue 04 落库直接消费这些值）
    assert result.confidence == 0.0
    assert result.reason == FALLBACK_REASON_OUTPUT_ERROR
    in_price, _ = MOCK_MODEL_PRICING_CNY_PER_1K[MODEL]
    prompt_tokens = estimate_tokens(channel.last_prompt.system + channel.last_prompt.user)
    assert result.tokens == prompt_tokens * 3  # 3 次尝试均消耗输入 tokens
    assert result.cost_cny == round(prompt_tokens * 3 * in_price / 1000, 6)


def test_timeout_falls_to_risk_without_retry() -> None:
    mock = MockLLMClassifier(script=[LLMTimeoutError("30s 超时")])
    channel = make_channel(mock)

    result = channel.classify(CARD)

    assert len(mock.calls) == 1  # 超时不重试：3×30s 会击穿单次分类成本/延迟预算
    assert result.verdict is Verdict.RISK
    assert result.channel is ClassificationChannel.LLM_ERROR
    assert result.confidence == 0.0
    assert result.reason == FALLBACK_REASON_TIMEOUT
    in_price, _ = MOCK_MODEL_PRICING_CNY_PER_1K[MODEL]
    prompt_tokens = estimate_tokens(channel.last_prompt.system + channel.last_prompt.user)
    assert result.tokens == prompt_tokens
    assert result.cost_cny == round(prompt_tokens * in_price / 1000, 6)


def test_timeout_budget_constant_is_g3_decision() -> None:
    assert DEFAULT_LLM_TIMEOUT_SECONDS == 30.0  # G3 ⑤ 定案


def test_unknown_model_pricing_rejected() -> None:
    """未登记单价的模型在构造期即失败（估算法口径必须显式登记，禁静默 0 成本）。"""
    with pytest.raises(ValueError, match="单价"):
        LLMChannel(
            MockLLMClassifier(),
            model="gpt-4o",
            samples=SAMPLES,
            options=LLMChannelOptions(classified_at=fixed_at()),
        )
