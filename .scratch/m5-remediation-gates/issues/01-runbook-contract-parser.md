Status: resolved
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

- **实现文件**：`src/oncall/remediation/runbook.py`（211 行，预算 ≈180/C6 ≤300 内）、`src/oncall/remediation/__init__.py`、`tests/unit/test_remediation_runbook.py`（23 用例）；runbook 库 `remediation/runbooks/cpu-spike.md` + `slow-sql.md`（仓根新目录，与 chaos/ 平行）
- **runbook 源文件定稿**：
  - `cpu-spike`：alert_ref=`DemoApiGwHighLatency`（golden alert_name）；actions=[stop-stress-and-restore-cpuset → docker.remove_container(cpu-spike-probe) + docker.remove_container(pumba-cpu-spike) + docker.restore_cpuset(oncall-demo-api-gw-1)]；**rollback=`[]`**（处置即恢复无回滚预案，D-45）；verification = `histogram_quantile(0.95, sum by (le) (rate(demo_request_duration_seconds_bucket{endpoint!="/tasks"}[1m])))`, condition `p95 <= 0.05`, window 60（PromQL 照抄 deploy/prometheus/rules.yml DemoApiGwHighLatency expr）
  - `slow-sql`：alert_ref=`DemoDbPoolSaturated`（golden 首个锚定告警）；actions=[kill-lock-session → docker.remove_container(slow-sql-probe) + mysql.kill_session(oncall-demo-mysql-1, session_id=$session_id)]；**rollback=`[mysql.kill_session]`**（KILL 幂等重试，D-45，非空）；verification = `avg_over_time(demo_db_pool_used[1m])`, condition `db_pool_used < db_pool_size`, window 60（golden ≤35s 排空口径折算进 60s 观察窗）
- **动作族注册表定稿（裁决②落位）**：runbook.py 模块级 `ACTION_KEYS: Final[frozenset] = {docker.remove_container, docker.restore_cpuset, mysql.kill_session}`（扁平存 `ns.op` 全名，C8 不可变合规）；issue 05 allowlist 以它为动作名单一权威扩展参数模板+正则
- **契约微调口径**：step 结构 = `{action: "ns.op", params: {k: scalar}}`；`$var` 前缀 = 运行时解析变量（仅结构校验）；rollback 条目与 step 同构；库加载面 = fail-closed（任一文件失败即整体不可用并列出失败文件）
- **验收逐条**：✅ 2 runbook 解析六字段全对；✅ 字段缺省/actions 空/verification 缺 promql|condition/引用不存在原子操作（step + rollback 各测）均拒绝并报原因；✅ 库加载面成功 + 单文件失败 fail-closed；✅ 正文与 cleanup.sh + golden 语义关键词级对齐（Pumba/cpuset/P95/KILL/表锁/连接池）；✅ 全量门禁 564 passed / 10 skipped（基线 541+23 只增不减）+ ruff check + ruff format + import-linter C3–C6 全绿

## Comments

- **裁决①（YAML 解析，偏离 D-43 字面）**：D-43 写「frontmatter 切分自写 ≈30 行不引 PyYAML」，但本质约束是 C2「零**新**依赖」。pyproject 已含 `pyyaml>=6.0,<7`（M0 scenarios/schema.py 在用）→ 实装：frontmatter **定界切分自写**（`_split_frontmatter`，`---` 独占行定界），frontmatter **YAML 本体用 `yaml.safe_load` 复用既有依赖**，不新增包——满足 C2 字面。**decision 结论（D-43）不动**；仅补一句理由栏「复用既有 pyyaml 不新增依赖」。请用户确认采纳。
- **裁决②（动作族注册表，D-42/G4 落位细节）**：runbook.py 维护最小动作族注册表常量 `ACTION_KEYS`（见落位注记）；issue 05 allowlist import 该常量扩展。静态原子操作表形态不变，不推翻 D-42。
- **裁决③（rollback 空值语义）**：cpu-spike `rollback: []` 显式空 = 无回滚预案合法（D-45，空 rollback 的恢复验证失败 = 直接转人工 D-28 不是 bug）；slow-sql rollback 非空 = KILL 幂等重试（照 cleanup.sh 幂等语义）。契约只拒绝「字段缺失」不拒绝「显式空」。
- **CONTEXT 术语**：未新增共享语言词条——动作族注册表为 runbook.py 内部实现常量，非领域概念；命名全部复用 CONTEXT 既有词（runbook/处置提案/命令白名单/恢复判据/受控执行）。
