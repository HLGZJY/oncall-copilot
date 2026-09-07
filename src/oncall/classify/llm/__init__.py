"""M2 LLM 通道（T3，G3/G8 定案）：prompt 组装 + few-shot 样本池 + 编排兜底。

公开面：
- `LLMChannel` —— 编排层：prompt 组装 → client 调用 → 阈值派生；
  畸形输出重试 ≤2 次、超时不重试，失败一律落 risk（channel=llm_error，0 漏报兜底）
- `LLMPrompt` / `build_prompt` / `estimate_tokens` —— prompt 组装与粗估（G8 估算法）
- `FewShotSample` / `load_few_shot_samples` / `VALIDATION_SCENARIO_SLUGS`
  —— 样本池 loader（只读 dev 集、排除 3 验证剧本、holdout 禁看）
- `MOCK_MODEL_PRICING_CNY_PER_1K` / `DEFAULT_LLM_TIMEOUT_SECONDS` / `MAX_OUTPUT_RETRIES`
  —— 成本与兜底常量（G3 ⑤ / G8 估算法口径，实测回填在 issue 07）

真实 client（DeepSeek-chat / Qwen-plus）经 issue 01 的 `LLMClassifier` 接口
在 issue 07 接入，本包编排层不变。
"""

from oncall.classify.llm.channel import (
    DEFAULT_LLM_TIMEOUT_SECONDS,
    FALLBACK_CONFIDENCE,
    FALLBACK_REASON_OUTPUT_ERROR,
    FALLBACK_REASON_TIMEOUT,
    MAX_OUTPUT_RETRIES,
    MOCK_MODEL_PRICING_CNY_PER_1K,
    LLMChannel,
    LLMChannelOptions,
)
from oncall.classify.llm.fewshot import (
    DEFAULT_PER_CLASS_K,
    VALIDATION_SCENARIO_SLUGS,
    FewShotSample,
    load_few_shot_samples,
)
from oncall.classify.llm.prompt import LLMPrompt, build_prompt, estimate_tokens

__all__ = [
    "DEFAULT_LLM_TIMEOUT_SECONDS",
    "DEFAULT_PER_CLASS_K",
    "FALLBACK_CONFIDENCE",
    "FALLBACK_REASON_OUTPUT_ERROR",
    "FALLBACK_REASON_TIMEOUT",
    "MAX_OUTPUT_RETRIES",
    "MOCK_MODEL_PRICING_CNY_PER_1K",
    "VALIDATION_SCENARIO_SLUGS",
    "FewShotSample",
    "LLMChannel",
    "LLMChannelOptions",
    "LLMPrompt",
    "build_prompt",
    "estimate_tokens",
    "load_few_shot_samples",
]
