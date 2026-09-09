# M5-01 派工 prompt：runbook 契约 + 解析器（T1 / G5；D-43）

oncall-copilot M5「处置与恢复验证（四道闸门）」第 1 票：建 runbook 文档库 2 源文件 + `src/oncall/remediation/runbook.py` 解析器（Markdown frontmatter → `Runbook` 数据类，契约校验失败即拒绝加载）。**严格 TDD；本票零执行面（无 subprocess、无 demo 容器操作）、零 LLM 调用、零 HTTP；不建白名单模块（allowlist 归 05）、不建 proposal 表（归 03）。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **`4b75cb1`**（M5 拆票收官），工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**不得回退**）：pytest 当前 **541 passed / 10 skipped**（10 skipped 含 3 个花钱开关真实 e2e + 7 常规 skip）+ `ruff check .` + `ruff format --check .` + import-linter C3–C6（`tests/test_architecture_guards.py`）
- pytest 统计行：pyproject addopts 已含 `-q`，**命令行勿再传 `-q`**；统计行用 `> file; PYEXIT=$?` 重定向 + 退出码核对（管道 grep 吞退出码）
- 不需要 demo 栈 / LLM key / 建表；SQLite 现库不涉及（本票零落库）

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票必用：Runbook / SOP、命令白名单（动作引用语义）、恢复判据、处置提案；**新术语当场入表并写 `_Avoid_`**）
3. `.scratch/m5-remediation-gates/issues/01-runbook-contract-parser.md`（**权威票面**，下方为摘要）+ `.scratch/m5-remediation-gates/spec.md`「关键契约」节
4. `docs/design/m5-remediation-gates-design.md`（**权威设计，status: reviewed**）——重点：§技术方案-C3/C6 论证（remediation 新包已在 C3 禁列预置，harness 不得 import 它）、§模块表 runbook 行、G5 定案、§开发计划 T1 行、§开放设计点 G4（原子操作语义——动作引用指向哪）
5. `docs/design/decisions.md`：**D-43（runbook 六字段契约 + 解析落 `src/oncall/remediation/runbook.py`）**、D-42（静态原子操作表——本票只引用其**动作名语义**，模块本体归 05）、D-44（verification 判据显式声明）、D-45（rollback 显式定义）；冻结面 D-23（六工具形状，本票不碰工具层）
6. **runbook 内容对齐素材（权威源）**：`chaos/scenarios/01-cpu-spike/cleanup.sh` + `02-slow-sql/cleanup.sh`（处置动作语义 = 这两份 cleanup）+ `datasets/golden/dev/cpu-spike.yaml` + `slow-sql.yaml`（`remediation` 字段——恢复判据与动作描述的黄金源；`holdout/` 禁读）
7. YAML 解析先例：`src/oncall/scenarios/schema.py`（`import yaml` + safe_load，M0 先例）——**仓库 pyproject 已含 `pyyaml>=6.0,<7`（M0 起），runbook frontmatter 解析复用既有依赖，见 §4 裁决①**
8. demo 容器实名（cleanup.sh 默认值）：`oncall-demo-api-gw-1`（cpu-spike）/ `oncall-demo-mysql-1`（slow-sql）——runbook 原子操作参数模板对齐这些实名

## 2. 任务（权威票面 = `.scratch/m5-remediation-gates/issues/01-runbook-contract-parser.md`，下方为摘要）

新增文件（**不动任何既有 src/ 文件**）：
1. `remediation/runbooks/cpu-spike.md` + `slow-sql.md`（**仓根新目录** `remediation/runbooks/`，与 `chaos/` 平行；设计已定，含文档库加载面读取它）
2. `src/oncall/remediation/__init__.py` + `src/oncall/remediation/runbook.py`（行数预算 ≈180）
3. `tests/unit/test_remediation_runbook.py`（单测）

**runbook 格式契约（D-43 定案）**：frontmatter YAML 六字段 = `slug / alert_ref / severity / actions[] / rollback[] / verification`
- `actions[i] = {id, name, steps:[白名单原子操作引用 + 参数模板]}`——动作引用形如 `mysql.kill_session` / `docker.restore_cpuset`（命名空间.操作两级）
- `rollback` = 反向原子操作序列；**`[]` 显式空 = 无回滚预案，验证失败直接转人工**（cpu-spike 处置即恢复无回滚预案，`rollback: []`；slow-sql 回滚 = KILL 幂等重试——具体形态照 cleanup.sh 幂等语义由你定稿并回填注记）
- `verification = {promql, condition, window_s}`——**判据取 golden `remediation` 字段实测回落值**（cpu-spike：非 DB 路径 P95 ≤0.05s；slow-sql：连接池占用回落 + 锚定告警 resolved 观察窗 ≈60s，golden 的 ≤35s 排空口径折算进 condition/window，票面注记写清折算）
- 正文 = 处置说明（供进模型上下文，不是执行源——执行只消费白名单引用）

**解析器（runbook.py）**：Markdown → `Runbook` 数据类（Pydantic 照仓库先例）；契约校验失败即**拒绝加载**（脏 runbook 进不了执行面）——字段缺省 / actions 为空 / 引用不存在的原子操作 / verification 缺 promql 或 condition 均拒绝并报清晰原因。

**runbook 库加载面**：scan `remediation/runbooks/` → 逐文件解析 → 全部通过才产出可用库（或失败清单，以你定稿口径 + 测试钉死）。

TDD 顺序建议：frontmatter 切分/解析通过 → 六字段全对 → 契约校验拒绝（字段缺省 / actions 空 / verification 缺字段 / 动作引用不存在）→ 库加载面（成功路径 + 单文件失败）。

## 3. 边界（勿越）

- **只新增**：`remediation/runbooks/` 2 源文件 + `src/oncall/remediation/` 包 + `tests/unit/test_remediation_runbook.py`；**不改任何既有生产代码、不改 pyproject、不改架构文档、不改 decisions/CONTEXT（新术语除外）**
- 不建 allowlist 模块（05 票）、不建 proposal 表（03 票）、不写 service 状态机（03 票）——runbook.py 只做「解析 + 结构/引用校验 + 数据类」，不含处置编排逻辑
- **harness 零接触**：本票模块不被 harness import（C3 禁列预置 `oncall.remediation`）；六工具集合/`ToolResult` 形状不碰（D-23 冻结面）
- 零 LLM 真实调用、零 HTTP、零 subprocess；`datasets/golden/holdout/` 禁读
- C6 单文件 ≤300 行（runbook.py ≈180）；C8 模块级可变全局禁用；命名一律 CONTEXT.md 词汇，不造新词
- 提交规范：中文 + type 前缀，预期 `feat(M5-处置闸门): ...`；body 写**为什么**；引用 `.scratch/m5-remediation-gates/issues/01-runbook-contract-parser.md`；不跳过 hooks

## 4. 已知踩坑（M0–M5 实录 + 本票裁决）

- ① **YAML 解析裁决（D-43 字面 vs 既有依赖）**：D-43 写「frontmatter 切分自写（≈30 行）不引 PyYAML」，但其本质约束是 C2「零**新**依赖」；实查 pyproject 已含 `pyyaml>=6.0,<7`（M0 scenarios 在用，`scenarios/schema.py` 先例）→ **推荐：runbook frontmatter 用 `yaml.safe_load` 解析既有依赖，不新增包**（满足 C2 字面）；自写 YAML 子集解析器是易错技术债且无收益。**偏离 D-43 技术假设须在 issue Comments 留痕 + 收尾汇报提请用户确认**（一句话补记 D-43 理由栏「复用既有 pyyaml 不新增依赖」，决策结论不动）
- ② **allowlist 依赖顺序（验收「引用不存在的原子操作拒绝加载」）**：allowlist 模块在 issue 05 才建，本票需解析期校验动作引用存在性 → **裁决：runbook.py 内维护最小「动作族注册表」常量**（动作名集合，如 `docker: {remove_container, restore_cpuset}, mysql: {kill_session}`，与 cleanup.sh 处置动作语义对齐）；issue 05 allowlist.py import 该常量扩展参数模板 + 正则（两处动作名一致性由 05 断言）。该裁决是 D-42/G4 的落位细节（静态原子操作表形态不变），不推翻决策
- ③ **rollback 空值语义**：cpu-spike 处置即恢复（cleanup.sh 无「再失败可回滚」预案）→ `rollback: []` 合法且契约**显式允许空**；空 rollback 的恢复验证失败路径 = 直接转人工（D-28，不是 bug）。契约校验只拒绝「字段缺失」不拒绝「显式空」
- ④ 仓根 `remediation/` 目录与 src 包 `oncall.remediation` 同名不同层——目录无 `__init__.py` 不是包，不冲突；但新目录需 `mkdir`
- ⑤ 同文件多次编辑必须串行，下一回合 grep 复核落盘
- ⑥ ruff：`max-args=5`、PLR0912 分支 ≤12、函数 ≤50 语句；pytest 统计行用重定向 + 退出码，勿传 `-q`
- ⑦ 验收字段语义：`alert_ref` 对齐 golden `alert_timeline[].alert_name`（DemoApiGwHighLatency / DemoDbPoolSaturated）与 demo 告警规则名一致——写 runbook 源文件时照抄 golden，别自造告警名
- ⑧ 参数模板字段本票只做**结构校验**（类型/层级对），正则匹配校验在执行器层（05 票）——runbook.py 不为 05 预实现正则

## 5. 验证路径（收尾清单）

- [ ] issue 01 验收逐项打勾 + 在 issue 文件内回填落位注记（runbook 源文件最终内容摘要、动作族注册表定稿、rollback 空值裁定、yaml 复用偏离记录）
- [ ] 架构守卫全绿：`tests/test_architecture_guards.py`（C3 remediation 包不破坏 harness 禁列 / C6 ≤300）
- [ ] 门禁：pytest（基线 541 passed / 10 skipped **只增不减**）+ ruff check + ruff format --check 全绿
- [ ] issue 文件 `Status:` 翻 `resolved`；`.scratch/m5-remediation-gates/spec.md` 任务序列表 01 行勾选
- [ ] 收尾汇报：落位文件清单 + 测试计数 + runbook 六字段契约样例 + 动作族注册表内容 + 裁决记录（①/②/③）+ 下一票（02 execute_action 干跑实装，blocked by 01 已解除）就绪确认
