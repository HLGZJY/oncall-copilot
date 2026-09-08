"""issue 07：真实 LLM client（oncall.infra.llm）接缝契约单测。

断网纪律（A1）：单测**从不发真实请求**——注入 `FakeChatClient` 替身走
`chat.completions.create` 接缝；openai 只被 import 其异常类型用于断言映射，
不构造 SDK client（`OpenAILLMClassifier(client=...)` 注入替身）。

异常契约与 mock 逐字对齐是热切换的前提：编排层（LLMChannel）据此决定重试或
落 risk。真实 client 若换一套异常语义，D-07 的 0 漏报兜底就失效——所以这三条
映射（畸形/超时/传输）必须各有一条单测钉死。

配置 fail-fast 同理：`from_env` 缺项必须抛 `LLMConfigError`，禁静默降级到
mock 产出「看起来成功」的 0 成本结论（G8）。
"""

from __future__ import annotations

from typing import Any

import pytest
from openai import APIConnectionError, APITimeoutError

from oncall.classify.client import (
    LLMClassifier,
    LLMClassifierError,
    LLMOutputError,
    LLMTimeoutError,
)
from oncall.classify.llm import MOCK_MODEL_PRICING_CNY_PER_1K, LLMChannel
from oncall.classify.llm.fewshot import FewShotSample
from oncall.infra.llm import (
    API_KEY_ENV,
    BASE_URL_ENV,
    DEV_DIR_ENV,
    MODEL_ENV,
    TIMEOUT_ENV,
    LLMClientConfig,
    LLMConfigError,
    OpenAILLMClassifier,
)

CARD: dict[str, Any] = {
    "alert": {"labels": {"alertname": "DemoApiGwHighLatency"}},
    "context": {},
}
REAL_ENV = {
    API_KEY_ENV: "sk-unit-test",
    BASE_URL_ENV: "https://example.invalid/compatible-mode/v1",
    MODEL_ENV: "qwen3.7-flash",
}
VERDICT_JSON = '{"verdict": "incident", "confidence": 0.86, "reason": "P95 越限且持续 1m"}'


class _FakeRequest:
    """openai 异常只把 request 当属性持有（不发请求），替身即可（A1 断网）。"""


class _Message:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str | None) -> None:
        self.message = _Message(content)


class _Usage:
    def __init__(self, prompt: int, completion: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = prompt + completion


class _Response:
    def __init__(self, content: str | None, *, prompt: int = 120, completion: int = 40) -> None:
        self.choices = [_Choice(content)]
        self.usage = _Usage(prompt, completion)
        self.model = "qwen3.7-flash"


class _Completions:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _Chat:
    def __init__(self, completions: _Completions) -> None:
        self.completions = completions


class FakeChatClient:
    """openai SDK 替身：只实现 `chat.completions.create`，记录调用 kwargs。"""

    def __init__(self, result: Any) -> None:
        self._completions = _Completions(result)
        self.chat = _Chat(self._completions)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self._completions.calls


def _client(result: Any, **config_kwargs: Any) -> OpenAILLMClassifier:
    """构造注入替身的真实 client（samples 置空：本文件不依赖 golden 数据）。"""
    config = LLMClientConfig(
        api_key="sk-unit-test",
        base_url="https://example.invalid/compatible-mode/v1",
        model="qwen3.7-flash",
        **config_kwargs,
    )
    return OpenAILLMClassifier(config, samples=[], client=FakeChatClient(result))


def test_satisfies_classifier_protocol() -> None:
    """真实 client 满足 LLMClassifier 接缝（热切换的结构化子类型前提）。"""
    assert isinstance(_client(_Response(VERDICT_JSON)), LLMClassifier)


def test_success_returns_verdict_and_records_real_usage() -> None:
    """正常响应：Pydantic 校验出 LLMVerdict，真实 usage 与延迟留存供成本回填。"""
    client = _client(_Response(VERDICT_JSON, prompt=980, completion=57))
    out = client.classify(CARD)
    assert (out.verdict, out.confidence) == ("incident", 0.86)
    assert client.last_usage is not None
    assert client.last_usage.prompt_tokens == 980
    assert client.last_usage.completion_tokens == 57
    assert client.last_usage.total_tokens == 1037
    assert client.last_usage.latency_seconds >= 0


def test_request_forces_json_mode_and_disables_thinking() -> None:
    """JSON Mode 强制 + qwen3 关闭思考（思考 tokens 计入输出计费且拖长延迟）。"""
    client = _client(_Response(VERDICT_JSON))
    client.classify(CARD)
    call = client._client.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["extra_body"] == {"enable_thinking": False}
    assert call["model"] == "qwen3.7-flash"
    assert call["timeout"] == 30.0
    assert [msg["role"] for msg in call["messages"]] == ["system", "user"]


def test_malformed_json_raises_output_error() -> None:
    """畸形输出 → LLMOutputError（编排层重试 ≤2 次后落 risk）。"""
    with pytest.raises(LLMOutputError, match="非合法 JSON"):
        _client(_Response("这不是 JSON")).classify(CARD)


def test_schema_violation_raises_output_error() -> None:
    """合法 JSON 但越出契约（枚举越界）→ 同样 LLMOutputError。"""
    with pytest.raises(LLMOutputError, match="契约校验"):
        _client(_Response('{"verdict": "unknown", "confidence": 0.9, "reason": "x"}')).classify(
            CARD
        )


@pytest.mark.parametrize("content", ["", "   "])
def test_empty_content_raises_output_error(content: str) -> None:
    """空内容 → LLMOutputError（不许静默当误报/当成功）。"""
    with pytest.raises(LLMOutputError, match="空内容"):
        _client(_Response(content)).classify(CARD)


def test_timeout_maps_to_llm_timeout_error() -> None:
    """超时 → LLMTimeoutError（编排层不重试，防 3×30s 击穿延迟/成本预算）。"""
    client = _client(APITimeoutError(request=_FakeRequest()))
    with pytest.raises(LLMTimeoutError):
        client.classify(CARD)


def test_transport_error_maps_to_base_error_without_retry_semantics() -> None:
    """连接/鉴权/限流 → 基类 LLMClassifierError（不重试：鉴权错重试只是放大故障）。"""
    client = _client(APIConnectionError(message="connect failed", request=_FakeRequest()))
    with pytest.raises(LLMClassifierError) as excinfo:
        client.classify(CARD)
    assert not isinstance(excinfo.value, LLMTimeoutError)


def test_prompt_carries_few_shot_samples() -> None:
    """prompt 由 client 侧组装：few-shot 样本进 user 段（真实调用与通道层同款）。"""
    sample = FewShotSample(scenario="cpu-spike", alert_card=CARD, verdict="incident", reason="r")
    config = LLMClientConfig(
        api_key="sk-unit-test", base_url="https://example.invalid/v1", model="qwen3.7-flash"
    )
    client = OpenAILLMClassifier(
        config, samples=[sample], client=FakeChatClient(_Response(VERDICT_JSON))
    )
    client.classify(CARD)
    assert client.last_prompt is not None
    assert "cpu-spike" in client.last_prompt.user


def test_sdk_client_disables_builtin_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK 内置重试必须关掉（实测 31.5s 绕开「超时不重试」契约）——重试归编排层。"""
    captured: dict[str, Any] = {}

    def fake_openai(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return FakeChatClient(_Response(VERDICT_JSON))

    monkeypatch.setattr("oncall.infra.llm.OpenAI", fake_openai)
    OpenAILLMClassifier.from_env(REAL_ENV)
    assert captured["max_retries"] == 0  # 静默重发会击穿单次分类的延迟预算
    assert captured["timeout"] == 30.0


def test_real_model_pricing_is_registered() -> None:
    """qwen3.7-flash 单价已登记：未登记模型在通道构造期即失败（禁静默 0 成本）。"""
    assert MOCK_MODEL_PRICING_CNY_PER_1K["qwen3.7-flash"] == (0.00024, 0.00096)


def test_hot_swap_into_llm_channel() -> None:
    """真实 client 注入 LLMChannel：mock 与真实经同一接缝热切换，编排层零改动。"""
    channel = LLMChannel(_client(_Response(VERDICT_JSON)), model="qwen3.7-flash", samples=[])
    result = channel.classify(CARD)
    assert result.verdict == "incident"
    assert result.channel == "llm"
    assert result.tokens > 0  # G8 估算法口径（真实 usage 另记在 client.last_usage）
    assert result.cost_cny > 0


def test_from_env_reads_three_vars() -> None:
    """三件套 from_env：key / base_url / model 全读，超时与样本目录可选。"""
    env = {**REAL_ENV, TIMEOUT_ENV: "12.5", DEV_DIR_ENV: "/repo/datasets/golden/dev"}
    config = LLMClientConfig.from_env(env)
    assert (config.api_key, config.model) == ("sk-unit-test", "qwen3.7-flash")
    assert config.timeout_seconds == 12.5
    assert config.dev_dir is not None and config.dev_dir.name == "dev"


@pytest.mark.parametrize("missing", [API_KEY_ENV, BASE_URL_ENV, MODEL_ENV])
def test_from_env_fails_fast_on_missing_var(missing: str) -> None:
    """缺任一项即 fail-fast（LLMConfigError），不静默回退 mock。"""
    env = {key: value for key, value in REAL_ENV.items() if key != missing}
    with pytest.raises(LLMConfigError, match=missing):
        LLMClientConfig.from_env(env)


def test_from_env_falls_back_to_default_timeout() -> None:
    """超时缺省/非法值回落 G3 定案 30s（配置坏了不许用 0 超时打死调用）。"""
    assert LLMClientConfig.from_env(REAL_ENV).timeout_seconds == 30.0
    assert LLMClientConfig.from_env({**REAL_ENV, TIMEOUT_ENV: "abc"}).timeout_seconds == 30.0
    assert LLMClientConfig.from_env({**REAL_ENV, TIMEOUT_ENV: "-1"}).timeout_seconds == 30.0
