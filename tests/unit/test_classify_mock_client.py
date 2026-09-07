"""T1 验收③：LLM client 接缝与 MockLLMClassifier 三种夹具（G3 ①）。

夹具契约（供 issue 03 兜底测试复用）：
- 正常 JSON：返回契约内 `LLMVerdict`（字段可编程）
- 畸形输出：抛 `LLMOutputError`（真实客户端等价于 Pydantic 校验失败）
- 超时：抛 `LLMTimeoutError`（G3：30s 超时，mock 不真等）
"""

from __future__ import annotations

import pytest

from oncall.classify import (
    LLMOutputError,
    LLMTimeoutError,
    LLMVerdict,
    MockLLMClassifier,
)

CARD = {"alert": {"labels": {"alertname": "DemoApiGwHighLatency"}}, "context": {}}


def test_protocol_satisfied() -> None:
    """Mock 实现满足 LLMClassifier 接缝（结构化子类型，无需显式继承）。"""
    client: MockLLMClassifier = MockLLMClassifier()
    assert hasattr(client, "classify")


def test_normal_fixture_returns_programmable_verdict() -> None:
    """正常夹具：固定结论可编程，调用入参被记录（issue 02 断言 0 次 LLM 用）。"""
    client = MockLLMClassifier(
        verdict="false_positive", confidence=0.9, reason="resolved-only 幽灵通知"
    )
    out = client.classify(CARD)
    assert isinstance(out, LLMVerdict)
    assert out.verdict == "false_positive"
    assert out.confidence == 0.9
    assert out.reason == "resolved-only 幽灵通知"
    assert client.calls == [CARD]


def test_normal_fixture_default_is_incident_mid_confidence() -> None:
    """零配置默认可用：incident / 0.8（高于默认阈值，方便组合派生测试）。"""
    out = MockLLMClassifier().classify(CARD)
    assert out.verdict == "incident"
    assert out.confidence == 0.8


def test_malformed_fixture_raises_output_error() -> None:
    """畸形夹具：契约校验失败以 LLMOutputError 表达，供兜底重试测试。"""
    client = MockLLMClassifier(script=[LLMOutputError("非 JSON 输出")])
    with pytest.raises(LLMOutputError):
        client.classify(CARD)


def test_timeout_fixture_raises_timeout_error() -> None:
    """超时夹具：不真等 30s，直接抛 LLMTimeoutError。"""
    client = MockLLMClassifier(script=[LLMTimeoutError("30s 超时")])
    with pytest.raises(LLMTimeoutError):
        client.classify(CARD)


def test_script_is_consumed_in_order_then_default_holds() -> None:
    """剧本按调用序消费，耗尽后稳定回落默认结论（重试→成功类测试的编排基础）。"""
    client = MockLLMClassifier(
        script=[
            LLMOutputError("第一次畸形"),
            LLMVerdict.model_validate(
                {"verdict": "incident", "confidence": 0.9, "reason": "重试后成功"}
            ),
        ]
    )
    with pytest.raises(LLMOutputError):
        client.classify(CARD)
    recovered = client.classify(CARD)
    assert recovered.verdict == "incident"
    assert client.classify(CARD).verdict == "incident"  # 耗尽后稳定默认


def test_confidence_out_of_range_rejected_in_fixture() -> None:
    """夹具同样受契约约束：confidence ∉ [0,1] 不可编程进正常结论。"""
    with pytest.raises(ValueError):
        MockLLMClassifier(confidence=1.5)
