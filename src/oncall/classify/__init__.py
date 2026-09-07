"""M2 降噪分类：分类领域模型与接缝（T1，G3 定案）。

公开面：
- `Verdict` / `ClassificationChannel` / `LLMVerdict` / `ClassificationResult` / `LLMCallMeta`
  —— 领域模型（models.py，D-19 契约）
- `derive_verdict` / `DEFAULT_RISK_CONFIDENCE_THRESHOLD` —— 阈值派生纯函数（deriver.py）
- `LLMClassifier` / `MockLLMClassifier` / `LLMOutputError` / `LLMTimeoutError`
  —— LLM 通道接缝（client.py，真实 client 留 issue 03/07 接）
- `RuleVerdict` / `Rule` / `DEFAULT_RULES` / `run_rule_channel` 等
  —— 规则通道谓词注册表（rules/，G2 定案，命中行 0 次 LLM 调用）
"""

from oncall.classify.client import (
    LLMClassifier,
    LLMClassifierError,
    LLMOutputError,
    LLMTimeoutError,
    MockLLMClassifier,
)
from oncall.classify.deriver import DEFAULT_RISK_CONFIDENCE_THRESHOLD, derive_verdict
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

__all__ = [
    "DEFAULT_RISK_CONFIDENCE_THRESHOLD",
    "DEFAULT_RULES",
    "MAINTENANCE_WINDOW_RULE",
    "RESOLVED_ONLY_GHOST_RULE",
    "STALE_REPLAY_RULE",
    "ClassificationChannel",
    "ClassificationResult",
    "LLMCallMeta",
    "LLMClassifier",
    "LLMClassifierError",
    "LLMOutputError",
    "LLMTimeoutError",
    "LLMVerdict",
    "MaintenanceWindow",
    "MockLLMClassifier",
    "Rule",
    "RuleContext",
    "RuleVerdict",
    "Verdict",
    "derive_verdict",
    "run_rule_channel",
]
