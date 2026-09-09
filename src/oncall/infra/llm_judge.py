"""真实 judge client（m7 issue 07 / C4+C5 SDK 收口，与 llm_planner 同构）。

实现 M7 LLM-as-judge 的真实调用面：judge 契约（JudgeInput → JudgeOutput）
冻结在 `oncall.eval.judging`，本模块只做「system/user → 结构化 JSON dict」
的收口——不 import eval 层契约模型，保持 infra 零业务依赖方向。

异常契约与 `infra/llm.py` 逐字对齐（D-07：mock 与真实实现抛同一异常族）：
- 畸形输出（空内容 / 非 JSON / 围栏内也提取不到 JSON 对象）→ `LLMOutputError`；
- 超时 → `LLMTimeoutError`（不重试，防击穿评测预算）；
- 其余 SDK 失败（连接/鉴权/限流）→ 基类 `LLMClassifierError`（同样不重试）。

配置走 `ONCALL_JUDGE_LLM_*` env 前缀（防自评，与被评模型 profile 物理隔离），
由 `oncall.eval.judging.resolve_judge_config` 映射为 `LLMClientConfig` 后注入。

JSON 宽容：模型偶发 ```json 围栏/前后缀说明——json.loads 失败时宽容抽取首个
`{...}` 对象再解析（判定不因格式抖动丢弃）；仍失败才 `LLMOutputError`。

计量：每步真实 usage 留存 `last_usage` 与累积 `usage_log`，供 issue 07 成本
实测回填（单价 env 化，见 entry `ONCALL_JUDGE_LLM_PRICE_*`）。
"""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING, Any

import structlog
from openai import APITimeoutError, OpenAI, OpenAIError

from oncall.classify.client import LLMClassifierError, LLMOutputError, LLMTimeoutError
from oncall.infra.llm import (
    JSON_MODE,
    ChatClient,
    LLMClientConfig,
    LLMUsage,
    _usage_of,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["JUDGE_ENV_PREFIX", "JUDGE_ENV_SUFFIXES", "OpenAIJudgeClient", "judge_config_from_env"]

logger = structlog.get_logger(__name__)

#: judge 独立 env 前缀（防自评）：与被评模型 profile `ONCALL_LLM_PROFILE_*`
#: 物理隔离——真实 judge 装配只能经本前缀读配置，不许回落被评模型。
JUDGE_ENV_PREFIX = "ONCALL_JUDGE_LLM"

#: judge env 尾词：与 profile 切换面同词汇（BASE_URL/MODEL/API_KEY/TIMEOUT_SECONDS）
JUDGE_ENV_SUFFIXES = ("BASE_URL", "MODEL", "API_KEY", "TIMEOUT_SECONDS")


def judge_config_from_env(env: Mapping[str, str] | None = None) -> LLMClientConfig:
    """`ONCALL_JUDGE_LLM_<尾词>` → `ONCALL_LLM_<尾词>` 映射后复用 `from_env`。

    与 `report.resolve_profile` 同构：切换面单源（LLMClientConfig.from_env），
    不改 infra/llm.py 冻结面；缺任一必需项 fail-fast（LLMConfigError，禁静默
    回退 mock——「看起来成功」的 0 成本判定比失败更危险）。
    """
    environ = os.environ if env is None else env
    mapped = {
        "ONCALL_LLM_" + tail: value
        for tail in JUDGE_ENV_SUFFIXES
        if (value := environ.get(f"{JUDGE_ENV_PREFIX}_{tail}")) is not None
    }
    return LLMClientConfig.from_env(mapped)


class OpenAIJudgeClient:
    """真实 LLM-as-judge client：OpenAI 兼容端点 JSON Mode，零硬编码模型名。"""

    def __init__(self, config: LLMClientConfig, *, client: ChatClient | None = None) -> None:
        self._config = config
        self._client: ChatClient = client or OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            max_retries=0,  # 重试语义归编排层统一控制（M2 同款教训）
        )
        #: 最近一次真实 usage（成本实测回填源）
        self.last_usage: LLMUsage | None = None
        #: 全部真实 usage 累积（judge 总成本 = Σ usage × 单价 env）
        self.usage_log: list[LLMUsage] = []

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> OpenAIJudgeClient:
        """按环境变量装配（`ONCALL_JUDGE_LLM_*` 前缀映射）；缺项 fail-fast。"""
        return cls(judge_config_from_env(env))

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def timeout_seconds(self) -> float:
        return self._config.timeout_seconds

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        """system/user → 结构化 JSON dict；失败一律表达为契约内异常，不吞错。"""
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format=JSON_MODE,
                timeout=self._config.timeout_seconds,
            )
        except APITimeoutError as exc:
            raise LLMTimeoutError(
                f"judge 调用超时（{self._config.timeout_seconds}s）: {exc}"
            ) from exc
        except OpenAIError as exc:
            raise LLMClassifierError(f"judge 调用失败（传输/鉴权/限流）: {exc}") from exc
        usage = _usage_of(response, self._config.model, time.perf_counter() - started)
        self.last_usage = usage
        self.usage_log.append(usage)
        logger.info(
            "judge_call",
            model=usage.model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            latency_seconds=usage.latency_seconds,
        )
        return _parse_json_object(_content_of(response))


def _content_of(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise LLMOutputError("judge 返回空 choices，无法产出结构化判定")
    return str(choices[0].message.content or "")


def _parse_json_object(content: str) -> dict[str, Any]:
    """响应文本 → dict：JSON Mode 主路径 + 围栏宽容抽取，畸形一律 LLMOutputError。"""
    if not content.strip():
        raise LLMOutputError("judge 返回空内容，无法解析为契约 JSON")
    payload = _loads_or_extract(content)
    if not isinstance(payload, dict):
        raise LLMOutputError(f"judge 输出不是 JSON 对象: {content[:200]}")
    return payload


def _loads_or_extract(content: str) -> Any:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        raise LLMOutputError(f"judge 输出非合法 JSON: {content[:200]}")
    try:
        return json.loads(content[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMOutputError(f"judge 输出非合法 JSON: {content[:200]}") from exc
