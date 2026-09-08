"""真实 Planner client（issue 08 / T8-B；D-22 JSON Mode 契约，C4+C5 SDK 收口）。

与 `infra/llm.py`（M2 分类 client）同构：实现 `oncall.harness.planner` 的
`PlannerClient` 接缝，`MockPlanner` → 本实现零改测试热切换（harness 零改动）。

prompt 组装边界：**输出协议唯一权威在 harness `build_system_prompt`**（D-22，
A2 模板落位纪律）——本模块只把视图切开（system = 视图携带的系统提示，
user = 步骤/假设/警告 JSON），不持有第二份协议文本。

异常契约（踩坑⑨：infra 侧转换，语义逐字对齐 harness 异常族）：
- 畸形输出（空内容 / 非 JSON / `PlannerDecision` 契约不过）→ `PlannerOutputError`
  （harness 主循环重试 ≤2 后归类 plan_error，D-22）；
- 超时（30s 上限，SDK 层 `max_retries=0` 关闭静默重发——M2 实测绕开契约的坑）
  → `PlannerTimeoutError`（不重试）；
- 其余 SDK 失败（连接/鉴权/限流）→ 基类 `PlannerError`（不重试，冒泡 API 层
  500 结构化兜底——与 M2「非畸形不重试」的处置理由一致）。

计量：每步真实 usage 留存 `last_usage` 与累积 `usage_log`，供 issue 08 成本
实测回填（R9 单价：输入 0.00024 / 输出 0.00096 元每千 tokens）与风险 #1
畸形率统计。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import structlog
from openai import APITimeoutError, OpenAI, OpenAIError
from pydantic import ValidationError

from oncall.harness.planner import (
    PlannerDecision,
    PlannerError,
    PlannerOutputError,
    PlannerTimeoutError,
)
from oncall.infra.llm import (
    DISABLE_THINKING,
    JSON_MODE,
    ChatClient,
    LLMClientConfig,
    LLMUsage,
    _usage_of,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["OpenAIPlannerClient"]

logger = structlog.get_logger(__name__)

_USER_TAIL = "请依据以上调查会话视图，按输出协议只输出一个 JSON 对象。"


class OpenAIPlannerClient:
    """真实调查 Planner：百炼（DashScope）OpenAI 兼容端点，qwen3.7-flash。

    输入调查会话视图（harness `_decide_with_retry` 组装：system_prompt /
    steps / hypotheses / notices）→ JSON Mode 调用 → Pydantic 校验出
    `PlannerDecision`；失败一律表达为 harness 契约内异常，不吞错。
    """

    def __init__(self, config: LLMClientConfig, *, client: ChatClient | None = None) -> None:
        self._config = config
        self._client: ChatClient = client or OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            max_retries=0,  # 重试语义归 harness 主循环统一控制（M2 同款教训）
        )
        #: 最近一次真实 usage（成本实测回填源）
        self.last_usage: LLMUsage | None = None
        #: 全部真实 usage 累积（单次调查总成本 = Σ usage × R9 单价）
        self.usage_log: list[LLMUsage] = []

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> OpenAIPlannerClient:
        """按环境变量装配（与 M2 分类 client 同一套 `ONCALL_LLM_*`）；缺项 fail-fast。"""
        return cls(LLMClientConfig.from_env(env))

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def timeout_seconds(self) -> float:
        return self._config.timeout_seconds

    def decide(self, context_view: Mapping[str, Any]) -> PlannerDecision:
        """调查会话视图 → 结构化决策；失败一律表达为 PlannerError 族。"""
        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self._config.model,
                messages=_messages_of(context_view),
                response_format=JSON_MODE,
                extra_body=DISABLE_THINKING,
                timeout=self._config.timeout_seconds,
            )
        except APITimeoutError as exc:
            raise PlannerTimeoutError(
                f"Planner 调用超时（{self._config.timeout_seconds}s）: {exc}"
            ) from exc
        except OpenAIError as exc:
            raise PlannerError(f"Planner 调用失败（传输/鉴权/限流）: {exc}") from exc
        usage = _usage_of(response, self._config.model, time.perf_counter() - started)
        self.last_usage = usage
        self.usage_log.append(usage)
        logger.info(
            "planner_call",
            model=usage.model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            latency_seconds=usage.latency_seconds,
        )
        return _parse_decision(_content_of(response))


def _messages_of(context_view: Mapping[str, Any]) -> list[dict[str, str]]:
    """视图 → messages：协议在 system（harness 权威），调查状态在 user。"""
    user_view = {
        key: context_view[key] for key in ("steps", "hypotheses", "notices") if key in context_view
    }
    user_content = json.dumps(user_view, ensure_ascii=False, sort_keys=True)
    return [
        {"role": "system", "content": str(context_view.get("system_prompt", ""))},
        {"role": "user", "content": f"{user_content}\n\n{_USER_TAIL}"},
    ]


def _content_of(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise PlannerOutputError("Planner 返回空 choices，无法产出决策")
    return str(choices[0].message.content or "")


def _parse_decision(content: str) -> PlannerDecision:
    """响应文本 → PlannerDecision；畸形一律 `PlannerOutputError`（主循环重试口径）。"""
    if not content.strip():
        raise PlannerOutputError("Planner 返回空内容，无法解析为决策 JSON")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise PlannerOutputError(f"Planner 输出非合法 JSON: {content[:200]}") from exc
    try:
        return PlannerDecision.model_validate(payload)
    except ValidationError as exc:
        raise PlannerOutputError(f"Planner 输出未通过 PlannerDecision 契约校验: {exc}") from exc
