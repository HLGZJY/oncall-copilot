Status: ready-for-agent
Blocked by:

# 05 缺口① opening 视图（T5 / G8；D-37）

> 承接 M3 issue 08 注记①（逐字）：「Planner 视图缺事件锚点：`harness/planner.py` 接缝 docstring 承诺视图 =『记忆摘要 + 事件锚点（D-25 口径）』，但 `loop._decide_with_retry` 实装 view 仅 `{system_prompt, steps, hypotheses, notices}`——模型开局面盲（两轮结论均直呼『未提供告警关联的服务名称及时间窗口』），开局只能盲选 get_topology。这是硬规 6『可观测数据质量决定 AI 上限』的实证：先补上下文，再谈模型能力。修复建议：view 增 `opening`（D-17 卡片精简投影），注意 C6 行数预算（loop.py 已 297 行）。」

## 任务

- `_decide_with_retry` view 增 **`opening` 键** = D-17 卡片精简投影（D-37 定案键集）：`{alertname, instance, job, severity, source, status, fired_at, last_fired_at}` + 三源 `context` 的 `status` 摘要（ok/unavailable 标记，**不含 items 全文**）
- 视图组装整体抽 `src/oncall/harness/context_manager.py` 新函数 `build_decision_view(session, notices, opening)`（现 157 行，预算 +40 行内）；loop.py（297 行贴 C6）**净减**——view 组装 5 行换 1 行调用，C6 达标即拆分预案（不另拆文件）
- opening 数据来源：调查入口的 D-17 卡片（M3 API 已组装，经依赖注入传入 loop，不反向拉 API）

## 要点

- 投影而非全卡片：`context.items` 全文与 steps 摘要重复、挤 token 预算（D-37 理由）
- opening 只补「服务名 + 时间窗 + 源可用性」这类开局锚点信息——实测结论直呼缺的正是这些
- 既有 mock e2e 3 剧本（3/3 命中）必须零回退：MockPlanner 不消费 opening，但 view 形状变化不得破坏 script 回放

## 验收（可机械判定）

- [ ] pytest 绿：`_decide_with_retry` view 含 `opening` 键且键集合精确断言（D-37 定案键集）
- [ ] pytest 绿：`opening` 不含 `context.items`（投影非全卡片断言）
- [ ] pytest 绿：`wc -l` 等价断言——loop.py ≤300 行、context_manager ≤300 行
- [ ] pytest 绿：既有 mock e2e 3/3 命中不回退
- [ ] 全量门禁不回退 + ruff 双检 + 架构守卫（C3/C6/A2）全绿
