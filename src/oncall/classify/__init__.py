"""M2 降噪分类：分类领域模型与接缝（T1，G3 定案）。

公开面：
- `Verdict` / `ClassificationChannel` / `LLMVerdict` / `ClassificationResult` / `LLMCallMeta`
  —— 领域模型（models.py，D-19 契约）
- `derive_verdict` / `DEFAULT_RISK_CONFIDENCE_THRESHOLD` —— 阈值派生纯函数（deriver.py）
- `LLMClassifier` / `MockLLMClassifier` / `LLMOutputError` / `LLMTimeoutError`
  —— LLM 通道接缝（client.py，真实 client 留 issue 03/07 接）
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

__all__ = [
    "DEFAULT_RISK_CONFIDENCE_THRESHOLD",
    "ClassificationChannel",
    "ClassificationResult",
    "LLMCallMeta",
    "LLMClassifier",
    "LLMClassifierError",
    "LLMOutputError",
    "LLMTimeoutError",
    "LLMVerdict",
    "MockLLMClassifier",
    "Verdict",
    "derive_verdict",
]
