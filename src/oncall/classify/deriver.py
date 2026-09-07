"""置信度阈值派生（G3 ④ 定案 / D-07 兜底）：risk 不由 LLM 直出。

纯函数：LLM 产出 `{verdict: false_positive|incident, confidence, reason}` 后，
`confidence < risk_confidence_threshold`（默认 0.7，可配）→ 派生 risk；
**严格小于**——恰等于阈值视为置信达标，维持原判定（边界行为已被单测钉死）。

为什么这样定：D-07「不确定落风险，不丢弃」是 0 漏报的保证。LLM 只产两态，
模糊地带统一收敛到 risk 中间态（建档观察），既不冤枉真事件也不放走疑似故障。
规则通道直判的结论不经本函数（规则只判误报，确定性部分不进 LLM 也不进派生）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from oncall.classify.models import (
    ClassificationChannel,
    ClassificationResult,
    LLMCallMeta,
    LLMVerdict,
    Verdict,
)

DEFAULT_RISK_CONFIDENCE_THRESHOLD = 0.7

__all__ = ["DEFAULT_RISK_CONFIDENCE_THRESHOLD", "derive_verdict"]


def derive_verdict(
    llm_verdict: LLMVerdict,
    meta: LLMCallMeta,
    *,
    risk_confidence_threshold: float = DEFAULT_RISK_CONFIDENCE_THRESHOLD,
    classified_at: datetime | None = None,
) -> ClassificationResult:
    """阈值派生：低置信 → risk，否则维持 LLM 原判定。

    `classified_at` 缺省取当前 UTC 时间；测试与批量回放可显式注入以保持确定性。
    """
    verdict = (
        Verdict.RISK if llm_verdict.confidence < risk_confidence_threshold else llm_verdict.verdict
    )
    return ClassificationResult(
        verdict=verdict,
        confidence=llm_verdict.confidence,
        reason=llm_verdict.reason,
        channel=ClassificationChannel.LLM,
        model=meta.model,
        tokens=meta.tokens,
        cost_cny=meta.cost_cny,
        classified_at=classified_at or datetime.now(UTC),
    )
