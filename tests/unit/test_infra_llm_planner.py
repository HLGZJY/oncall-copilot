"""issue 08（T8-B）：真实 Planner client（oncall.infra.llm_planner）契约单测。

断网纪律（A1）：注入 FakeChatClient 替身走 `chat.completions.create` 接缝，
openai 只 import 异常类型用于断言映射，不构造 SDK client、零真实调用。
异常映射三条必须各有一测钉死（畸形 → PlannerOutputError 重试 ≤2 / 超时 →
PlannerTimeoutError 不重试 / 其余 SDK 失败 → 基类 PlannerError 冒泡）——
MockPlanner → 真实 client 零改测试热切换的前提。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from openai import APITimeoutError, OpenAIError

from oncall.harness.planner import (
    PlannerClient,
    PlannerDecision,
    PlannerError,
    PlannerOutputError,
    PlannerTimeoutError,
)
from oncall.infra.llm import LLMClientConfig, LLMConfigError
from oncall.infra.llm_planner import OpenAIPlannerClient

VIEW: dict[str, Any] = {
    "system_prompt": "系统提示（harness 权威协议文本）",
    "steps": ["组件=a｜指标=b｜异常方向=up｜时间窗=[t1 ~ t2]"],
    "hypotheses": ["假设一"],
    "notices": [],
}

TOOL_JSON = '{"thought": "查 CPU 饱和", "next_tool": "query_metrics", "args": {"promql": "up"}}'
CONCLUSION_JSON = '{"thought": "收束", "conclusion": "CPU 饱和定案"}'


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
    def __init__(self, content: str | None, *, prompt: int = 1500, completion: int = 60) -> None:
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


def _client(result: Any) -> OpenAIPlannerClient:
    config = LLMClientConfig(
        api_key="sk-unit-test",
        base_url="https://example.invalid/compatible-mode/v1",
        model="qwen3.7-flash",
    )
    return OpenAIPlannerClient(config, client=FakeChatClient(result))


class _FakeRequest:
    url = "https://example.invalid"


def test_satisfies_planner_protocol() -> None:
    """真实 client 满足 PlannerClient 接缝（热切换的结构化子类型前提）。"""
    assert isinstance(_client(_Response(TOOL_JSON)), PlannerClient)


def test_success_tool_branch_records_usage() -> None:
    """选工具分支：校验出 PlannerDecision，真实 usage 留存并累积。"""
    client = _client(_Response(TOOL_JSON, prompt=3200, completion=85))
    decision = client.decide(VIEW)
    assert decision.next_tool == "query_metrics"
    assert decision.args == {"promql": "up"}
    assert decision.conclusion is None
    assert client.last_usage is not None
    assert client.last_usage.total_tokens == 3285
    assert len(client.usage_log) == 1


def test_success_conclusion_branch() -> None:
    decision = _client(_Response(CONCLUSION_JSON)).decide(VIEW)
    assert decision.conclusion == "CPU 饱和定案"
    assert decision.next_tool is None


def test_request_forces_json_mode_and_harness_system_prompt() -> None:
    """JSON Mode + 关思考 + 协议权威在 harness system_prompt（infra 不持第二份）。"""
    client = _client(_Response(TOOL_JSON))
    client.decide(VIEW)
    call = client._client.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["extra_body"] == {"enable_thinking": False}
    assert call["timeout"] == 30.0
    assert call["messages"][0] == {"role": "system", "content": VIEW["system_prompt"]}
    user = call["messages"][1]["content"]
    assert '"steps"' in user and '"hypotheses"' in user and '"notices"' in user


@pytest.mark.parametrize(
    "content",
    ["这不是 JSON", '{"thought": "两分支同时出现", "next_tool": "x", "conclusion": "y"}', ""],
)
def test_malformed_output_raises_planner_output_error(content: str) -> None:
    """畸形（非 JSON / 契约互斥违约 / 空内容）→ PlannerOutputError（主循环重试 ≤2）。"""
    with pytest.raises(PlannerOutputError):
        _client(_Response(content)).decide(VIEW)


def test_empty_choices_raises_output_error() -> None:
    response = _Response(TOOL_JSON)
    response.choices = []
    with pytest.raises(PlannerOutputError, match="空 choices"):
        _client(response).decide(VIEW)


def test_timeout_maps_to_planner_timeout_error() -> None:
    """超时 → PlannerTimeoutError（主循环不重试，防 3×30s 击穿预算）。"""
    with pytest.raises(PlannerTimeoutError):
        _client(APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))).decide(
            VIEW
        )


def test_other_sdk_error_maps_to_base_planner_error() -> None:
    """连接/鉴权/限流 → 基类 PlannerError（不重试，冒泡 API 层 500 兜底）。"""
    with pytest.raises(PlannerError, match="传输/鉴权/限流"):
        _client(OpenAIError("boom")).decide(VIEW)


def test_from_env_missing_config_fails_fast() -> None:
    """缺配置 fail-fast（禁静默回退产出 0 成本结论，G8）。"""
    with pytest.raises(LLMConfigError):
        OpenAIPlannerClient.from_env(env={})


def test_from_env_loads_triple() -> None:
    env = {
        "ONCALL_LLM_API_KEY": "sk-x",
        "ONCALL_LLM_BASE_URL": "https://example.invalid/v1",
        "ONCALL_LLM_MODEL": "qwen3.7-flash",
    }
    client = OpenAIPlannerClient.from_env(env=env)
    assert client.model == "qwen3.7-flash"
    assert client.timeout_seconds == 30.0


def test_planner_decision_rejects_extra_fields() -> None:
    """D-22 契约 extra=forbid：模型多吐字段同样算畸形（防协议漂移）。"""
    with pytest.raises(PlannerOutputError):
        _client(_Response('{"thought": "t", "next_tool": "x", "args": {}, "bonus": 1}')).decide(
            VIEW
        )


def test_decision_type_is_frozen_contract() -> None:
    """返回值即 harness PlannerDecision（同一 Pydantic 契约，无第二套形状）。"""
    decision: PlannerDecision = _client(_Response(TOOL_JSON)).decide(VIEW)
    assert decision.model_config.get("extra") == "forbid"
