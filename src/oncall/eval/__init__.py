"""M7 评测台（m7-eval-bench，D-58–D-65）。

模块分工：
- `golden`：黄金集加载器 + 三条防泄漏守卫（holdout 锁 / few-shot 排除 / 标注完整性）
- `runner`（T2）：剧本 runner（LoopComponents 装配 + mock/真实双档）
- `judging`（T3）：判对错两级（规则匹配 + judge 契约 mock 冻结）
- `metrics`（T4）：指标核算（五列 + 复用/escalated 单列）
- `report`（T5）：报告双产物（JSON + markdown）

架构守卫：C3 已预置 `oncall.harness` 禁 import 本模块（物理单向）；
本模块消费 harness 装配面是合法方向。
"""

from oncall.eval.golden import (
    FewShotLeakError,
    GoldenAnnotationError,
    GoldenScenario,
    HoldoutLockedError,
    filter_fewshot_scenarios,
    load_golden_dir,
)

__all__ = [
    "FewShotLeakError",
    "GoldenAnnotationError",
    "GoldenScenario",
    "HoldoutLockedError",
    "filter_fewshot_scenarios",
    "load_golden_dir",
]
