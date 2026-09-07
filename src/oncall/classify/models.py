"""M2 分类领域模型（G3/G4 定案，decisions.md D-19）。

- `Verdict` 三态枚举冻结：`false_positive / risk / incident`（D-19 不得扩枚举）
- `LLMVerdict` = LLM 通道结构化输出契约：LLM **不直接输出 risk**（G3 ④），
  只产 `false_positive | incident` + confidence + reason，risk 由阈值派生（deriver.py）
- `ClassificationResult` = 分类结论审计全量（落 `alert_events.classification_json`，
  建表在 issue 04，本模块只管结构契约）
- `LLMCallMeta` = 一次 LLM 调用的成本审计元数据（G8 口径）

术语纪律：risk 语义见 CONTEXT.md「风险 / Risk = 分类时无法判定的中间态，
建档观察但不丢弃（0 漏报的保证）」；通道词汇 = 规则通道 / LLM 通道。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Verdict(StrEnum):
    """分类三态出口（D-19 冻结，扩枚举须回 decisions.md 评审）。"""

    FALSE_POSITIVE = "false_positive"
    RISK = "risk"
    INCIDENT = "incident"


class ClassificationChannel(StrEnum):
    """结论来源通道（G4 审计口径）：rule=规则通道 / llm=LLM 通道 / llm_error=LLM 失败兜底。"""

    RULE = "rule"
    LLM = "llm"
    LLM_ERROR = "llm_error"


class LLMVerdict(BaseModel):
    """LLM 通道结构化输出契约（G3 ④，JSON mode + Pydantic 校验）。

    LLM 不直接输出 risk；confidence ∈ [0, 1]，低于阈值由 `derive_verdict`
    派生 risk（decisions.md D-19）。
    """

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict = Field(description="只允许 false_positive / incident")
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)


class LLMCallMeta(BaseModel):
    """单次 LLM 调用成本审计元数据（G8：cost = in_tokens × 输入单价 + out_tokens × 输出单价）。"""

    model_config = ConfigDict(extra="forbid")

    model: str
    tokens: int = Field(ge=0)
    cost_cny: float = Field(ge=0.0)


class ClassificationResult(BaseModel):
    """分类结论审计全量（D-19：`classification_json` 的结构契约，序列化形态稳定）。"""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1)
    channel: ClassificationChannel
    model: str
    tokens: int = Field(ge=0)
    cost_cny: float = Field(ge=0.0)
    classified_at: datetime
