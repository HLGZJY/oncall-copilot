Status: ready-for-agent
Blocked by:

# 06 缺口② 工具 schema 摘要进 system prompt（T6 / G9；D-38）

> 承接 M3 issue 08 注记②（逐字）：「工具入参 schema 不可见：架构 §3.3 承诺『模型可调 tool_help 查详情』的 just-in-time 通道，但 D-23 冻结六工具未含 tool_help，system prompt 只有名称+一句话描述——query_metrics 必填 start/end 全靠盲猜，参数失败后 notice 循环消耗步数。修复建议（二选一，需小评审）：system prompt 附六工具入参 schema 摘要，或补第 7 个 tool_help 工具（改 D-23 面）。」——评审定案选 **A：schema 摘要**（D-38）。

## 任务

- system prompt 附**六工具入参 schema 摘要**：静态模板（A2 落位，模块级模板常量），每工具 2–3 行（工具名 + 必填/可选参数 + 约束值域，如 query_metrics 的 `{promql, start, end, step?}`、search_logs 的 `limit≤100`）
- 预算控制：摘要估 +≈250 tokens，**系统提示 ≤1500 tokens 断言随票钉死**；超限先压缩一句话描述，不砍 schema 本身（它是缺口②的主修复）
- 选 A 即架构 §3.3「tool_help 通道」按意图由 schema 摘要兑现——**架构 §3.3 措辞注记回写随本票**（一行说明，交用户复核）
- 不补第 7 个 tool_help 工具（不改 D-23 冻结面，D-38）

## 要点

- schema 摘要与 ToolRegistry 实际参数校验**同源**：从 registry 注册的参数契约派生或与之逐字对齐，防止文档 schema 与运行时校验漂移
- 直击实测主失败模式（M3 issue 08：参数级失败 + 同参绕圈）——schema 可见后 query_metrics 必填 start/end 不再盲猜
- 既有 mock e2e 3/3 零回退（MockPlanner 不消费 system prompt 语义，但预算变化不得破坏断言）

## 验收（可机械判定）

- [ ] pytest 绿：system prompt 含六工具 schema 摘要（逐工具存在性断言，schema 与 registry 参数契约一致）
- [ ] pytest 绿：系统提示 tokens ≤1500 预算断言（含 opening 后的完整 prompt）
- [ ] pytest 绿：既有 mock e2e 3/3 命中不回退
- [ ] 架构 §3.3 措辞注记落盘（一行 diff）
- [ ] 全量门禁不回退 + ruff 双检 + A2 守卫全绿
