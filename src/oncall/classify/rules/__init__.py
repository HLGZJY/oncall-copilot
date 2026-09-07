"""规则通道（T2，G2 定案）：确定性误报模式谓词注册表。

公开面：
- `RuleVerdict` —— 单条规则判定：误报直判（带 reason）/ 放行（规则不判真实）
- `Rule` / `DEFAULT_RULES` —— 注册表条目与默认规则集（规则名是稳定契约）
- `RESOLVED_ONLY_GHOST_RULE` / `MAINTENANCE_WINDOW_RULE` / `STALE_REPLAY_RULE`
  —— 初始规则集 3 条（G2 定案），可单独取用做正反例测试与归因
- `RuleContext` / `MaintenanceWindow` —— 判定环境：当前时间 + 静默窗口
- `run_rule_channel` —— 编排入口：命中 → `ClassificationResult`（channel=rule）；
  全部 pass → None（交 LLM 通道，命中行 0 次 LLM 调用）
"""

from oncall.classify.rules.registry import (
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
    "DEFAULT_RULES",
    "MAINTENANCE_WINDOW_RULE",
    "RESOLVED_ONLY_GHOST_RULE",
    "STALE_REPLAY_RULE",
    "MaintenanceWindow",
    "Rule",
    "RuleContext",
    "RuleVerdict",
    "run_rule_channel",
]
