"""LLM 通道编排（issue 03 / G3 ④⑤ + G8 成本口径）。

职责链：prompt 组装（记录在 `last_prompt` 供审计与快照测试）→ client 调用
（Mock 先行，真实 client 在 issue 07 经同一 `LLMClassifier` 接口热切换）→
Pydantic 校验出的 `LLMVerdict` → `derive_verdict` 阈值派生终判。

兜底（0 漏报的保证，D-07）：
- 畸形输出（`LLMOutputError`）：重试 ≤2 次（总尝试 3 次）→ 仍失败落 risk，
  channel=llm_error，confidence/reason/tokens/cost 兜底取值有单测钉死；
- 超时（`LLMTimeoutError`，30s 上限）：**不重试**——3×30s 会击穿单次分类
  延迟与成本预算（G8），立即落 risk 不抛出。

成本（G8 估算法口径）：`cost = in_tokens × 输入单价 + out_tokens × 输出单价`，
单价为 mock 快照（取价 2026-09-07，实测回填在 issue 07）；未登记单价的模型
构造期即失败，禁静默 0 成本。畸形/超时失败的输出 tokens 计 0，输入 tokens
按实际尝试次数累计（真实计费行为等价）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from oncall.classify.client import (
    LLMClassifier,
    LLMClassifierError,
    LLMOutputError,
    LLMTimeoutError,
)
from oncall.classify.deriver import DEFAULT_RISK_CONFIDENCE_THRESHOLD, derive_verdict
from oncall.classify.llm.fewshot import DEFAULT_DEV_DIR, load_few_shot_samples
from oncall.classify.llm.prompt import LLMPrompt, build_prompt, estimate_tokens
from oncall.classify.models import (
    ClassificationChannel,
    ClassificationResult,
    LLMCallMeta,
    Verdict,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime as DateTime
    from pathlib import Path

    from oncall.classify.llm.fewshot import FewShotSample

__all__ = [
    "DEFAULT_LLM_TIMEOUT_SECONDS",
    "FALLBACK_CONFIDENCE",
    "FALLBACK_REASON_OUTPUT_ERROR",
    "FALLBACK_REASON_TIMEOUT",
    "MAX_OUTPUT_RETRIES",
    "MOCK_MODEL_PRICING_CNY_PER_1K",
    "LLMChannel",
    "LLMChannelOptions",
]

#: G3 ⑤ 定案：单次调用超时 30s（真实 client 生效；mock 直接抛 LLMTimeoutError 不真等）
DEFAULT_LLM_TIMEOUT_SECONDS = 30.0

#: 畸形输出重试上限（G3 ⑤：重试 ≤2 次，总尝试 = 1 + 2 = 3）
MAX_OUTPUT_RETRIES = 2

#: 落 risk 兜底置信度：无可信信号即为 0（单测钉死，issue 04 落库消费）
FALLBACK_CONFIDENCE = 0.0

#: 兜底 reason（单测钉死；措辞面向 M8 风险观察 tab 直读）
FALLBACK_REASON_OUTPUT_ERROR = "LLM 通道失败：输出畸形，重试 2 次后仍无法解析，落风险兜底"
FALLBACK_REASON_TIMEOUT = "LLM 通道失败：调用超时（30s），落风险兜底"

#: mock 单价快照（CNY / 千 tokens，取价 2026-09-07，估算法口径；实测回填 issue 07）
MOCK_MODEL_PRICING_CNY_PER_1K: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.002, 0.003),
    "qwen-plus": (0.0008, 0.002),
}


def _pricing(model: str) -> tuple[float, float]:
    try:
        return MOCK_MODEL_PRICING_CNY_PER_1K[model]
    except KeyError:
        raise ValueError(
            f"模型 {model!r} 未登记 mock 单价（MOCK_MODEL_PRICING_CNY_PER_1K），"
            "估算法口径禁止静默 0 成本"
        ) from None


@dataclass(frozen=True)
class LLMChannelOptions:
    """LLM 通道调优参数（风险阈值 / 超时 / 时间戳注入；缺省即 G3 定案值）。"""

    risk_confidence_threshold: float = DEFAULT_RISK_CONFIDENCE_THRESHOLD
    timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    classified_at: DateTime | None = None


class LLMChannel:
    """LLM 通道：prompt 组装 + 重试/超时兜底 + 阈值派生 + 成本记录的编排层。

    本类只消费 issue 01 定案的公开面（`LLMClassifier` 接缝 / `derive_verdict`），
    不推翻任何契约。双模型（DeepSeek-chat / Qwen-plus）经同一接口热切换。
    """

    def __init__(
        self,
        client: LLMClassifier,
        *,
        model: str,
        samples: Sequence[FewShotSample] | None = None,
        options: LLMChannelOptions | None = None,
        dev_dir: Path | None = None,
    ) -> None:
        _pricing(model)  # 构造期校验单价登记（fail-fast）
        opts = options or LLMChannelOptions()
        self._client = client
        self._model = model
        self._samples = (
            list(samples)
            if samples is not None
            else load_few_shot_samples(dev_dir or DEFAULT_DEV_DIR)
        )
        self._risk_confidence_threshold = opts.risk_confidence_threshold
        self._timeout_seconds = opts.timeout_seconds
        self._classified_at = opts.classified_at
        #: 最近一次调用的 prompt（审计 + 快照测试 + issue 07 真实 client 直用）
        self.last_prompt: LLMPrompt | None = None
        #: 最近一次调用的成本元数据（G8 审计口径）
        self.last_meta: LLMCallMeta | None = None

    def classify(self, alert_card: Mapping[str, Any]) -> ClassificationResult:
        """规则未决告警 → LLM 分诊 → 派生终判；任何失败兜底落 risk，不抛出。"""
        prompt = build_prompt(alert_card, self._samples)
        self.last_prompt = prompt
        in_tokens = estimate_tokens(prompt.system + prompt.user)
        in_price, out_price = _pricing(self._model)

        attempts = 0
        while True:
            attempts += 1
            try:
                llm_verdict = self._client.classify(alert_card)
            except LLMOutputError:
                if attempts <= MAX_OUTPUT_RETRIES:
                    continue
                return self._fallback(
                    FALLBACK_REASON_OUTPUT_ERROR,
                    in_tokens=in_tokens * attempts,
                    in_price=in_price,
                )
            except LLMClassifierError as exc:
                # 超时等非畸形失败：不重试，直接兜底（3×30s 会击穿延迟/成本预算）
                reason = (
                    FALLBACK_REASON_TIMEOUT
                    if isinstance(exc, LLMTimeoutError)
                    else FALLBACK_REASON_OUTPUT_ERROR
                )
                return self._fallback(
                    reason,
                    in_tokens=in_tokens * attempts,
                    in_price=in_price,
                )
            out_tokens = estimate_tokens(json_dumps_verdict(llm_verdict))
            meta = LLMCallMeta(
                model=self._model,
                tokens=in_tokens + out_tokens,
                cost_cny=round((in_tokens * in_price + out_tokens * out_price) / 1000, 6),
            )
            self.last_meta = meta
            return derive_verdict(
                llm_verdict,
                meta,
                risk_confidence_threshold=self._risk_confidence_threshold,
                classified_at=self._classified_at or datetime.now(UTC),
            )

    def _fallback(self, reason: str, *, in_tokens: int, in_price: float) -> ClassificationResult:
        meta = LLMCallMeta(
            model=self._model,
            tokens=in_tokens,
            cost_cny=round(in_tokens * in_price / 1000, 6),
        )
        self.last_meta = meta
        return ClassificationResult(
            verdict=Verdict.RISK,
            confidence=FALLBACK_CONFIDENCE,
            reason=reason,
            channel=ClassificationChannel.LLM_ERROR,
            model=self._model,
            tokens=meta.tokens,
            cost_cny=meta.cost_cny,
            classified_at=self._classified_at or datetime.now(UTC),
        )


def json_dumps_verdict(verdict: Any) -> str:
    """输出 tokens 估算基准：契约字段序列化（估算法口径）。"""
    return json.dumps(verdict.model_dump(mode="json"), ensure_ascii=False)
