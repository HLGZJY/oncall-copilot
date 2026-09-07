"""M2 降噪分类：分类领域模型与接缝（T1，G3 定案）。

公开面：
- `Verdict` / `ClassificationChannel` / `LLMVerdict` / `ClassificationResult` / `LLMCallMeta`
  —— 领域模型（models.py，D-19 契约）
- `derive_verdict` / `DEFAULT_RISK_CONFIDENCE_THRESHOLD` —— 阈值派生纯函数（deriver.py）
- `LLMClassifier` / `MockLLMClassifier` / `LLMOutputError` / `LLMTimeoutError`
  —— LLM 通道接缝（client.py，真实 client 留 issue 03/07 接）
- `RuleVerdict` / `Rule` / `DEFAULT_RULES` / `run_rule_channel` 等
  —— 规则通道谓词注册表（rules/，G2 定案，命中行 0 次 LLM 调用）
- `LLMChannel` / `build_prompt` / `load_few_shot_samples` / `MOCK_MODEL_PRICING_CNY_PER_1K` 等
  —— LLM 通道（llm/，T3，G3/G8 定案：prompt 组装 + few-shot 样本池 + 兜底编排，mock 先行）
- `ClassifyOptions` / `ClassifySummary` / `classify_alerts`
  —— 双通道编排落库服务（service.py，T4，G4/G5 定案：规则先行 → LLM 通道 → 同事务落库）
"""

from oncall.classify.client import (
    LLMClassifier,
    LLMClassifierError,
    LLMOutputError,
    LLMTimeoutError,
    MockLLMClassifier,
)
from oncall.classify.deriver import DEFAULT_RISK_CONFIDENCE_THRESHOLD, derive_verdict
from oncall.classify.llm import (
    DEFAULT_LLM_TIMEOUT_SECONDS,
    FALLBACK_CONFIDENCE,
    FALLBACK_REASON_OUTPUT_ERROR,
    FALLBACK_REASON_TIMEOUT,
    MAX_OUTPUT_RETRIES,
    MOCK_MODEL_PRICING_CNY_PER_1K,
    VALIDATION_SCENARIO_SLUGS,
    FewShotSample,
    LLMChannel,
    LLMChannelOptions,
    LLMPrompt,
    build_prompt,
    estimate_tokens,
    load_few_shot_samples,
)
from oncall.classify.models import (
    ClassificationChannel,
    ClassificationResult,
    LLMCallMeta,
    LLMVerdict,
    Verdict,
)
from oncall.classify.rules import (
    DEFAULT_RULES,
    MAINTENANCE_WINDOW_RULE,
    RESOLVED_ONLY_GHOST_RULE,
    STALE_REPLAY_RULE,
    MaintenanceWindow,
    Rule,
    RuleContext,
    RuleVerdict,
    run_rule_channel,
)
from oncall.classify.service import (
    ClassifyOptions,
    ClassifyRuntime,
    ClassifySummary,
    classify_alerts,
)

__all__ = [
    "DEFAULT_LLM_TIMEOUT_SECONDS",
    "DEFAULT_RISK_CONFIDENCE_THRESHOLD",
    "DEFAULT_RULES",
    "FALLBACK_CONFIDENCE",
    "FALLBACK_REASON_OUTPUT_ERROR",
    "FALLBACK_REASON_TIMEOUT",
    "MAINTENANCE_WINDOW_RULE",
    "MAX_OUTPUT_RETRIES",
    "MOCK_MODEL_PRICING_CNY_PER_1K",
    "RESOLVED_ONLY_GHOST_RULE",
    "STALE_REPLAY_RULE",
    "VALIDATION_SCENARIO_SLUGS",
    "ClassificationChannel",
    "ClassificationResult",
    "ClassifyOptions",
    "ClassifyRuntime",
    "ClassifySummary",
    "FewShotSample",
    "LLMCallMeta",
    "LLMChannel",
    "LLMChannelOptions",
    "LLMClassifier",
    "LLMClassifierError",
    "LLMOutputError",
    "LLMPrompt",
    "LLMTimeoutError",
    "LLMVerdict",
    "MaintenanceWindow",
    "MockLLMClassifier",
    "Rule",
    "RuleContext",
    "RuleVerdict",
    "Verdict",
    "build_prompt",
    "classify_alerts",
    "derive_verdict",
    "estimate_tokens",
    "load_few_shot_samples",
    "run_rule_channel",
]
