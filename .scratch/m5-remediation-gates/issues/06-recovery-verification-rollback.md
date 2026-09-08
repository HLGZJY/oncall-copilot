Status: ready-for-agent
Blocked by: 05

# 06 恢复验证 + 回滚（T6 / G6 / G7 / D-44 / D-45）

## 任务

- 新建 `src/oncall/remediation/verifier.py`（行数预算 ≈120）：**恢复验证器**——runbook `verification` PromQL 回查判恢复（复用 `context/promql` Fetcher 接缝注入）；恢复/未恢复机械判定
  - 判据 = runbook verification 显式声明（D-44）：`{promql, condition, window_s}`
  - cpu-spike：非 DB 路径 P95 ≤0.05s（以 runbook 定稿值为准，取剧本实测回落值）
  - slow-sql：`demo_db_pool_used` 回落池上限内 + 锚定告警 resolved 观察窗 60s（以 runbook 定稿值为准）
- 未恢复路径（D-45）：自动执行 runbook 显式定义 `rollback`（白名单原子操作序列，经 issue 05 执行器）→ 仍失败 → **转人工**（proposal 落 `escalated`、incident 保持 investigating——D-28 转人工不是丢弃）
- 恢复路径：proposal 收尾 `recovered` + incident 翻 `mitigated`（既有枚举流转，D-19/D-46）

## 要点

- 验证结果落 `verify_result_json`（promql 结果 + condition 判定 + 时间窗）供 M7 处置成功率矩阵
- 回滚同样过白名单 + 留痕（D-45：rollback 引用白名单原子操作，与 actions 同执行面）
- **不做逆操作推导**（D-45）——rollback 只来自 runbook 显式定义
- Fetcher 注入替身断言两条路径（恢复/未恢复→回滚→转人工）；不依赖活 Prometheus（活栈真实验证留 issue 08）
- 领域名：恢复判据 / 恢复验证 / 转人工（CONTEXT.md 权威词）

## 验收（可机械判定）

- [ ] pytest 绿：恢复路径——Fetcher 替身返回「判据满足」→ proposal 收尾 `recovered` + incident 翻 `mitigated`
- [ ] pytest 绿：未恢复路径——Fetcher 替身返回「判据不满足」→ 自动执行 rollback（替身断言 rollback 命令过白名单执行器）→ 回滚后复验仍失败 → proposal 落 `escalated` + incident 保持 `investigating`
- [ ] pytest 绿：回滚成功后复验通过 → proposal 收尾 `recovered`（或 rollback_status 落 `rolled_back` + recovered，以状态机定稿为准）
- [ ] pytest 绿：`verify_result_json` 字段全落（promql 结果/condition 判定/window）
- [ ] pytest 绿：rollback 命令白名单校验失败 → 不执行 + 审计 + 转人工（不回滚的兜底）
- [ ] 全量门禁不回退（基线 541/10）+ ruff 双检

## 落位注记（实现后回填）

- （待实现票回填：verifier 判定形状、rollback_status 取值、恢复窗口实测值）
