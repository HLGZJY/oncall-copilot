Status: ready-for-agent
Blocked by: —

# 01 runbook 契约 + 解析器（T1 / G5 / D-43）

## 任务

- 新建 runbook 文档库 `remediation/runbooks/`（仓根，与 `chaos/` 平行，版本控制 = R3 维护纪律；**不进 src 包**）：2 个处置 runbook 源文件 = `cpu-spike.md` + `slow-sql.md`（G9 选型，内容与 `chaos/scenarios/01-cpu-spike/02-slow-sql` cleanup.sh 处置语义 + golden `expected_remediation` 对齐；`datasets/golden/holdout/` 禁读）
- 新建 `src/oncall/remediation/runbook.py`（行数预算 ≈180）：Markdown → `Runbook` 数据类——frontmatter 切分自写（≈30 行，**零新依赖**，不引 PyYAML）+ 契约校验；契约校验失败即**拒绝加载**（脏 runbook 进不了执行面）
- runbook 库加载面（scan 目录 → 逐文件解析 → 全部通过才产出可用库）

## 要点

- **格式契约（D-43 定案）**：frontmatter YAML 六字段 = `slug / alert_ref / severity / actions[] / rollback[] / verification`
  - `actions[i] = {id, name, steps:[白名单原子操作引用 + 参数模板]}`（id 形如 `kill-lock-session`；steps 引用 D-42 原子操作，动作名不在白名单 → 拒绝加载）
  - `rollback` = 反向原子操作序列（D-45：系统不做逆操作推导，runbook 显式定义）
  - `verification = {promql, condition, window_s}`（D-44：恢复判据显式声明）
  - 正文 = 处置说明（供进模型上下文用，不是执行源）
- **参数模板语法需在本票定稿**：解析器只做结构校验（字段齐 + 引用合法 + 类型对），参数值的正则匹配校验在执行器层（issue 05）；本票在 `steps` 参数模板处留显式接口
- 领域名用 `CONTEXT.md` 权威词：runbook / 处置提案 / 命令白名单 / 干跑 / 恢复判据

## 验收（可机械判定）

- [ ] pytest 绿：2 个 runbook 源文件解析通过，`Runbook` 字段全对（slug/alert_ref/severity/actions/rollback/verification）
- [ ] pytest 绿：契约校验拒绝——字段缺省 / 引用不存在的原子操作 / verification 缺 promql 或 condition / actions 为空——各拒绝加载并报清晰原因
- [ ] pytest 绿：runbook 库加载面 = 目录内任一文件失败即整体不可用（或失败清单返回，以实现票定稿口径）
- [ ] pytest 绿：正文处置说明与 `chaos/scenarios/*/cleanup.sh` + golden `expected_remediation` 语义对齐抽查（关键词级）
- [ ] 全量门禁不回退（基线 541/10）+ ruff 双检 + import-linter C3–C6 绿（remediation 模块新建不破坏 harness 禁列）

## 落位注记（实现后回填）

- （待实现票回填：实际文件行数、契约微调口径、runbook 源文件最终内容）
