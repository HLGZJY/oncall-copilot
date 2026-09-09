Status: resolved
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

- [x] pytest 绿：验收标准节逐条对应断言全绿（机械可判定项全覆盖）
- [x] pytest 绿：mock e2e 2 剧本（cpu-spike + slow-sql）决策脚本驱动全链路通过
- [x] 全量门禁：pytest 基线只增不减（**实测 679 passed / 10 skipped**，票面原写 541/10 系 M3 末期旧值，前序 06 入库后真值为 667/10）+ ruff 双检 + import-linter C3–C6 + A6 bandit 全绿
- [x] 验收节勾选状态回填到设计文档（`- [x]`，实测数据禁虚构；T8 两条不勾）

## 落位注记（实现后回填）

- **断言文件分布**（4 文件，各 ≤300 行）：
  - `tests/e2e/test_m5_acceptance_gates.py`（300 行 / 4 用例）：验收「干跑不落地 + ToolResult 形状」「100% 过确认门」端到端（reject 后 409 / pending→executing 直跳被拒 / 执行器零调用）、「全程留痕」逐字段对账 + 恢复验证恢复侧跨层收口（confirm → run_confirm_chain → incident mitigated）
  - `tests/e2e/test_m5_frozen_face.py`（56 行 / 3 用例）：D-23 六工具/ExecuteActionInput/ToolResult 精确键集合、D-25 三契约列集/字段集、状态机迁移表键集合（pending 无执行出边、终态零出边）
  - `tests/e2e/test_m5_command_gates.py`（73 行 / 2 用例）：第 3 门收口——白名单注入样本/多余参数/白名单外动作执行器层拒绝留审计零执行；runbook 引用白名单外动作（action/rollback）拒绝加载
  - `tests/e2e/test_m5_mock_e2e.py`（225 行 / 4 用例）：2 剧本 mock e2e（golden 剧本驱动 MockPlanner → 干跑建 proposal → confirm approve → 真实 ControlledExecutor + RunbookRecoveryVerifier（FakeFetcher 快照）→ recovered + incident mitigated）；slow-sql 未恢复变体（回滚 `$session_id` 经 runtime_values 注入 → 复验失败 → escalated + incident 保持 investigating，D-28）；golden `remediation` ↔ runbook actions/rollback/verification 字段级对账
- **mock e2e 剧本驱动方式**：`golden_support` 剧本 + MockPlanner 末步换 `execute_action`（action = `runbook_slug/action_id` 定位器）；proposal 落库经 `_db_creator` 闭包接缝（C3 鸭子类型）；替身共享 `test_m5_acceptance_gates` 夹具
- **实测计数**：全量 679 passed / 10 skipped（667 基线 + 净增 12：验收断言 9 + 收口后合并重复 escalated 用例 −1 + mock e2e 4）；ruff check / format --check / import-linter（C3–C6 kept）/ bandit 全绿
- **裁决留痕（契约缺漏修正，用户拍板 2026-09-09）**：cpu-spike runbook 第 3 步原缺 `cores` 参数，与 issue 05 白名单 `docker.restore_cpuset {container, cores}` 契约冲突（真实执行器必拒、T8 活栈会撞同一缺口）。采纳方案：runbook 第 3 步补 `cores: $cpuset_cores` 运行时模板（语义 = 原 cpuset 从记录文件读取、执行期注入，与 issue 05 裁决②一致）；`test_execute_action_dryrun` 对应渲染断言同步更新
- 设计文档验收节：mock 可判定 6 条勾选 + 实测注记；「2 剧本端到端自动处置恢复（T8）」「真实 e2e 回填（T8）」两条不勾归 08
