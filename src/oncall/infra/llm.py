"""LLM SDK 收口接缝（架构守卫 C4+C5：openai SDK 全仓只许 import 在本文件）。

与 `oncall.infra.http` 同构：业务层不直接碰 LLM SDK，注入实现 `LLMClassifier`
协议的对象——生产是本文件的 `OpenAILLMClassifier`，测试与 M7 矩阵用
`MockLLMClassifier`，二者经 `oncall.classify.client.LLMClassifier` 热切换。

异常契约与 mock **逐字对齐**（编排层据此决定重试或落 risk，D-07）：
- 畸形输出（非 JSON / 契约校验失败）→ `LLMOutputError`（编排层重试 ≤2 次）；
- 超时（G3 ⑤ 定案 30s）→ `LLMTimeoutError`（不重试，防击穿延迟/成本预算）；
- 其余 SDK 失败（连接/鉴权/限流）→ 基类 `LLMClassifierError`（同样不重试：
  鉴权错重试 3 次只是放大故障，与超时的处置理由一致）。

配置走三个 `ONCALL_LLM_*` 环境变量的 `from_env` 模式，**缺任一项构造期
fail-fast**（`LLMConfigError`）——禁静默回退到 mock（G8：未登记/未配置的模型
不许产出「看起来成功」的 0 成本结论）。

计量：真实响应的 `usage` 与往返延迟留存在 `last_usage`，供 issue 07 成本实测
回填。`LLMChannel` 落库的 `tokens/cost_cny` 仍是 G8 估算法口径（粗估），
**两者分列不混算**——实测报告里「估算」与「真实 usage」各记一列。

qwen3 系兼容模式特性（实测 2026-09-08，写入注释防回退）：
- JSON Mode：`response_format={"type": "json_object"}`（OpenAI 兼容端点生效）；
- 关闭思考模式：`extra_body={"enable_thinking": False}`——qwen3 默认思考，
  思考 tokens 计入输出计费且会拖长延迟，分诊属简单任务，关掉。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import structlog
from openai import APITimeoutError, OpenAI, OpenAIError
from pydantic import ValidationError

from oncall.classify.client import (
    LLMClassifierError,
    LLMOutputError,
    LLMTimeoutError,
)
from oncall.classify.llm.fewshot import DEFAULT_DEV_DIR, load_few_shot_samples
from oncall.classify.llm.prompt import LLMPrompt, build_prompt
from oncall.classify.models import LLMVerdict

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from oncall.classify.llm.fewshot import FewShotSample

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "LLMClientConfig",
    "LLMConfigError",
    "LLMUsage",
    "OpenAILLMClassifier",
]

API_KEY_ENV = "ONCALL_LLM_API_KEY"
BASE_URL_ENV = "ONCALL_LLM_BASE_URL"
MODEL_ENV = "ONCALL_LLM_MODEL"
DEV_DIR_ENV = "ONCALL_GOLDEN_DEV_DIR"
TIMEOUT_ENV = "ONCALL_LLM_TIMEOUT_SECONDS"

#: G3 ⑤ 定案：单次调用超时 30s（与 `DEFAULT_LLM_TIMEOUT_SECONDS` 同源）
DEFAULT_TIMEOUT_SECONDS = 30.0

#: JSON Mode 开法（OpenAI 兼容端点标准参数）
JSON_MODE = {"type": "json_object"}

#: qwen3 兼容模式关闭思考（实测 2026-09-08：不关则思考 tokens 计入输出计费）
DISABLE_THINKING = {"enable_thinking": False}


#: 计量日志：真实 usage 只活在 SDK 响应里，`classification_json` 是 G8 估算口径
#: （契约冻结，不为实测改字段）。成本审计因此走 structlog 结构化日志——容器
#: `docker logs` 是唯一既不破坏落库契约、又能留存每次真实调用 usage 的通道。
logger = structlog.get_logger(__name__)


class LLMConfigError(RuntimeError):
    """真实 LLM client 配置缺失（构造期 fail-fast），不属分类失败契约。"""


@dataclass(frozen=True)
class LLMUsage:
    """一次真实调用的计量留存（issue 07 成本实测口径；估算口径不落这里）。"""

    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_seconds: float


@dataclass(frozen=True)
class LLMClientConfig:
    """真实 client 配置三件套 + 超时/样本目录（缺省即 G3 定案值）。"""

    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    dev_dir: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LLMClientConfig:
        """环境变量加载；缺任一必需项即 `LLMConfigError`（禁静默回退）。"""
        environ = os.environ if env is None else env
        api_key = (environ.get(API_KEY_ENV) or "").strip()
        base_url = (environ.get(BASE_URL_ENV) or "").strip()
        model = (environ.get(MODEL_ENV) or "").strip()
        missing = [
            name
            for name, value in (
                (API_KEY_ENV, api_key),
                (BASE_URL_ENV, base_url),
                (MODEL_ENV, model),
            )
            if not value
        ]
        if missing:
            raise LLMConfigError(
                "真实 LLM client 未配置完整，缺：" + ", ".join(missing) + "（/classify 将落 503）"
            )
        raw_dev_dir = (environ.get(DEV_DIR_ENV) or "").strip()
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=_positive_number(environ.get(TIMEOUT_ENV), DEFAULT_TIMEOUT_SECONDS),
            dev_dir=Path(raw_dev_dir) if raw_dev_dir else None,
        )


class _ChatCompletions(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class _Chat(Protocol):
    completions: _ChatCompletions


class ChatClient(Protocol):
    """openai SDK 收口接缝：测试注入替身只需实现 `chat.completions.create`。"""

    chat: _Chat


class OpenAILLMClassifier:
    """真实 LLM 分类 client：百炼（DashScope）OpenAI 兼容端点，qwen3.7-flash。

    实现 `LLMClassifier` 接缝（duck typing + `runtime_checkable` 协议可判定）：
    输入事件卡片（D-17 JSON 契约）→ 组装 prompt（与 `LLMChannel` 同款
    `build_prompt`，few-shot 样本构造期加载一次）→ JSON Mode 调用 →
    Pydantic 校验出 `LLMVerdict`。
    """

    def __init__(
        self,
        config: LLMClientConfig,
        *,
        samples: Sequence[FewShotSample] | None = None,
        client: ChatClient | None = None,
    ) -> None:
        self._config = config
        self._samples = (
            list(samples)
            if samples is not None
            else load_few_shot_samples(config.dev_dir or DEFAULT_DEV_DIR)
        )
        self._client: ChatClient = client or OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            # SDK 默认 max_retries=2：读超时后**静默重发**，实测（2026-09-08 场景 1
            # 复现）出现 31.5s 才返回、绕开编排层「超时不重试」的契约。重试语义
            # 归 `LLMChannel`（畸形重试 ≤2 / 超时不重试）统一控制，SDK 层关掉。
            max_retries=0,
        )
        #: 最近一次 prompt（与通道层 `last_prompt` 同形状，审计可比对）
        self.last_prompt: LLMPrompt | None = None
        #: 最近一次真实 usage（成本实测回填源；估算口径不落这里）
        self.last_usage: LLMUsage | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> OpenAILLMClassifier:
        """按环境变量装配；配置不全直接抛 `LLMConfigError`（fail-fast）。"""
        config = LLMClientConfig.from_env(env)
        return cls(config)

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def timeout_seconds(self) -> float:
        return self._config.timeout_seconds

    @property
    def samples(self) -> list[FewShotSample]:
        return list(self._samples)

    def classify(self, alert_card: Mapping[str, Any]) -> LLMVerdict:
        """事件卡片 → 结构化三态结论；失败一律表达为契约内异常，不吞错。"""
        prompt = build_prompt(alert_card, self._samples)
        self.last_prompt = prompt
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ],
                response_format=JSON_MODE,
                extra_body=DISABLE_THINKING,
                timeout=self._config.timeout_seconds,
            )
        except APITimeoutError as exc:
            raise LLMTimeoutError(
                f"LLM 调用超时（{self._config.timeout_seconds}s）: {exc}"
            ) from exc
        except OpenAIError as exc:
            raise LLMClassifierError(f"LLM 调用失败（传输/鉴权/限流）: {exc}") from exc
        usage = _usage_of(response, self._config.model, time.perf_counter() - started)
        self.last_usage = usage
        logger.info(
            "llm_call",
            model=usage.model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            latency_seconds=usage.latency_seconds,
        )
        return _parse_verdict(_content_of(response))


def _content_of(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise LLMOutputError("LLM 返回空 choices，无法产出结构化结论")
    return str(choices[0].message.content or "")


def _parse_verdict(content: str) -> LLMVerdict:
    if not content.strip():
        raise LLMOutputError("LLM 返回空内容，无法解析为契约 JSON")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMOutputError(f"LLM 输出非合法 JSON: {content[:200]}") from exc
    try:
        return LLMVerdict.model_validate(payload)
    except ValidationError as exc:
        raise LLMOutputError(f"LLM 输出未通过 LLMVerdict 契约校验: {exc}") from exc


def _usage_of(response: Any, model: str, latency_seconds: float) -> LLMUsage:
    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or 0) or prompt_tokens + completion_tokens
    return LLMUsage(
        model=str(getattr(response, "model", "") or model),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total,
        latency_seconds=round(latency_seconds, 3),
    )


def _positive_number(raw: str | None, default: float) -> float:
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default
