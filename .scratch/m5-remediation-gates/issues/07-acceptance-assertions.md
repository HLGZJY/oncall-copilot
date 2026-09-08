Status: ready-for-agent
Blocked by: 01, 02, 03, 04, 05, 06

# 07 验收断言与门禁（T7）

## 任务

把设计文档「验收标准」节逐条转机械断言（照 M4 issue 07 先例），并跑 mock e2e 2 剧本：

- **写操作 100% 过确认门**：全测试内无任何 execute_action 直接执行路径——所有写命令必经「干跑渲染 → pending → 人工 approve → 白名单校验 → 执行」；确认前系统不产生任何 demo 侧副作用
- **干跑不落地**（第 1 门）：execute_action 调用后 demo 侧状态零变化（执行器替身/快照比对），ToolResult 返回命令清单+影响面+proposal_id
- **全程留痕**：proposal 行含干跑 JSON（= 被批准的命令清单）、确认 decision/reason/时间、执行输出摘要、验证结果、回滚状态；执行器每条白名单命令落审计
- **命令白名单拒绝越权**（第 3 门）：白名单外命令/参数（任意 docker rm、shell 注入样本）执行器层拒绝留审计；runbook 引用不存在动作 → 拒绝加载
- **D-23 冻结面不破**：六工具集合、`ExecuteActionInput` 形状、`ToolResult` 形状、loop 主循环结构不变；`EvidenceStep`/`Hypothesis`/`InvestigationSession` 契约不倒改（D-25）
- **mock e2e**：`cpu-spike` + `slow-sql` 2 剧本 mock 决策脚本驱动 → 干跑提案 → mock confirm → 恢复验证 → incident mitigated 全链路断言

## 要点

- 验收断言落位：能独立成测试文件的照各 issue 归属；跨 issue 端到端断言归本票收口（照 M4 T7 先例）
- 门禁基线 **541 passed / 10 skipped** 只增不减 + ruff 双检 + import-linter C3–C6 + A6 bandit 全绿
- 涉 G 取舍的断言按定案说明（D-39–D-48）写，不得引入未评审语义

## 验收（可机械判定）

- [ ] pytest 绿：验收标准节逐条对应断言全绿（机械可判定项全覆盖）
- [ ] pytest 绿：mock e2e 2 剧本（cpu-spike + slow-sql）决策脚本驱动全链路通过
- [ ] 全量门禁：pytest 基线 541/10 只增不减 + ruff 双检 + import-linter + bandit 全绿
- [ ] 验收节勾选状态回填到设计文档（`- [x]`，实测数据禁虚构）

## 落位注记（实现后回填）

- （待实现票回填：断言文件分布、mock e2e 剧本脚本位置、设计文档验收节勾选）
