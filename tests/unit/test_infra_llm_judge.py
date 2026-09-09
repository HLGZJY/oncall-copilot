"""M7-T7 验收 · 真实 judge client（oncall.infra.llm_judge）契约单测。

与 test_infra_llm_planner 同构：异常映射逐字钉死（D-07——映射错了编排层
重试/降级就失效），只 import openai 异常类型、构造 FakeChatClient 替身，
零真实 API 调用（A1 断网守卫同源；本文件已登记 SDK_EXCEPTION_MAPPING_TESTS）。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from openai import APITimeoutError, OpenAIError

from oncall.classify.client import LLMClassifierError, LLMOutputError, LLMTimeoutError
from oncall.infra.llm import LLMClientConfig, LLMConfigError
from oncall.infra.llm_judge import OpenAIJudgeClient

_CONFIG = LLMClientConfig(api_key="k", base_url="https://example/v1", model="m")


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Msg(content)


class _Usage:
    prompt_tokens = 10
    completion_tokens = 5
    total_tokens = 15


class _Resp:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]
        self.usage = _Usage()
        self.model = "m"


class _Completions:
    def __init__(self, result: Any) -> None:
        self._result = result

    def create(self, **kwargs: Any) -> Any:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeChatClient:
    def __init__(self, result: Any) -> None:
        self.chat = type("Chat", (), {"completions": _Completions(result)})()


def test_chat_json_returns_parsed_dict():
    client = OpenAIJudgeClient(_CONFIG, client=FakeChatClient(_Resp('{"verdict": "top1"}')))
    got = client.chat_json("系统提示", "用户输入")
    assert got == {"verdict": "top1"}


def test_chat_json_tolerates_fenced_json():
    """模型偶发 ```json 围栏/前后缀：宽容抽取首个 JSON 对象（判定不因格式抖动丢弃）。"""
    client = OpenAIJudgeClient(
        _CONFIG, client=FakeChatClient(_Resp('前置说明\n```json\n{"verdict": "miss"}\n```\n'))
    )
    assert client.chat_json("s", "u") == {"verdict": "miss"}


def test_empty_choices_and_content_raise_llm_output_error():
    client = OpenAIJudgeClient(_CONFIG, client=FakeChatClient(_Resp("")))
    with pytest.raises(LLMOutputError, match="空"):
        client.chat_json("s", "u")
    with pytest.raises(LLMOutputError, match="JSON"):
        OpenAIJudgeClient(_CONFIG, client=FakeChatClient(_Resp("完全不是 JSON"))).chat_json(
            "s", "u"
        )


def test_timeout_maps_to_llm_timeout_error():
    err = APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))
    client = OpenAIJudgeClient(_CONFIG, client=FakeChatClient(err))
    with pytest.raises(LLMTimeoutError):
        client.chat_json("s", "u")


def test_other_sdk_errors_map_to_llm_classifier_error():
    client = OpenAIJudgeClient(_CONFIG, client=FakeChatClient(OpenAIError("boom")))
    with pytest.raises(LLMClassifierError):
        client.chat_json("s", "u")


def test_usage_log_accumulates_real_usage():
    client = OpenAIJudgeClient(_CONFIG, client=FakeChatClient(_Resp('{"verdict": "top3"}')))
    client.chat_json("s", "u")
    client.chat_json("s", "u")
    assert len(client.usage_log) == 2
    assert client.usage_log[-1].total_tokens == 15
    assert client.last_usage is client.usage_log[-1]


def test_request_uses_json_mode_and_config_timeout():
    seen: dict[str, Any] = {}

    class _Spy:
        chat = type("Chat", (), {"completions": None})()

    def _create(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return _Resp('{"verdict": "top1"}')

    _Spy.chat.completions = type("C", (), {"create": staticmethod(_create)})()
    client = OpenAIJudgeClient(_CONFIG, client=_Spy())  # type: ignore[arg-type]
    client.chat_json("sys", "user")
    assert seen["model"] == "m"
    assert seen["response_format"] == {"type": "json_object"}
    assert seen["timeout"] == _CONFIG.timeout_seconds
    assert seen["messages"][0]["role"] == "system"


def test_from_env_missing_raises_llm_config_error():
    with pytest.raises(LLMConfigError):
        OpenAIJudgeClient.from_env(env={})
