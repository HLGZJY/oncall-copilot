# M5-02 派工 prompt：execute_action 干跑实装 + PermissionGate L2 授权判定器（T2 / G1+G3 / D-39/D-41）

oncall-copilot M5「处置与恢复验证（四道闸门）」第 2 票：把 D-23 冻结的 `execute_action` **L2 永远拒绝 stub** 实装为**干跑模式 handler**——解析 `action`（`runbook_slug/action_id` 定位器）→ 用 issue 01 的 runbook 解析器渲染「将执行的命令清单 + 影响面」（干跑预览）→ 建 **pending 处置提案**（proposal 落库归 issue 03，本票先接**存根接缝**）→ 返回 `ToolResult{ok, 干跑预览 + proposal_id}`；并把 **PermissionGate L2 授权判定器（D-41）** 落地。**严格 TDD；本票零 demo 写操作、零 subprocess、零 LLM、零 HTTP。** 推理与执行分离（D-39）：本票 handler **永不执行任何 demo 侧写操作**，只出干跑预览 + 提案。

> ⚠️ **本票含一处安全面裁决（见 §4 裁决 ①）**——会改动一条已固化的 loop 级安全测试的前提。实现前先厘清，收尾必须请用户确认，勿静默改。

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **`369a176`**（M5-01 runbook 契约+解析器已合入），工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**不得回退**）：pytest 当前 **564 passed / 10 skipped**（10 skipped 含 3 个花钱开关真实 e2e + 7 常规 skip；基线已含 issue 01 的 23 用例）+ `ruff check .` + `ruff format --check .` + import-linter C3–C6（`tests/test_architecture_guards.py`）
- pytest 统计行：pyproject addopts 已含 `-q`，**命令行勿再传 `-q`**；统计行用 `> file; PYEXIT=$?` 重定向 + 退出码核对（管道 grep 吞退出码）
- 不需要 demo 栈 / LLM key / 建表；SQLite 现库不涉及（本票不落库——proposal 表归 issue 03）

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航；重点第 3/4/11 条——安全护栏落系统层、写操作四道闸门、高危命令纪律）
2. `CONTEXT.md`（术语权威——本票必用：干跑 / 干跑预览 / 处置提案 / 命令白名单 / 推理与执行分离 / 转人工；新术语当场入表并写 `_Avoid_`）
3. `.scratch/m5-remediation-gates/issues/02-execute-action-dryrun.md`（**权威票面**，下方为摘要）+ `.scratch/m5-remediation-gates/spec.md`「关键契约」节
4. `docs/design/m5-remediation-gates-design.md`（**权威设计，status: reviewed**）——重点：§技术方案-处置主流程图、G1（循环内干跑、确认后独立执行）、G3（L2 授权判定器 / 循环内 L2 仍只出干跑 / ExecuteActionInput 不扩列）、§模块表 execute_action 实装 + PermissionGate 语义行、§C3/C6 论证（harness 禁 import remediation，handler 走 registry 注入接缝）、§风险 7（注入接缝不破坏既有测试）、§评审后动作 5（架构 §3.3 L2 语义注记随本票回写）
5. `docs/design/decisions.md`：**D-39（循环内干跑、确认后独立执行，批准对象=干跑渲染命令清单，loop.py 零净增）**、**D-41（L2 授权判定器：gate.check 保持纯函数；循环内 L2 仍只出干跑；ExecuteActionInput 不扩列）**、**D-23（ToolResult/六工具/ExecuteActionInput 冻结面只消费不推翻）**、D-28（终止与转人工）、D-16（unavailable/error 不抛原始异常）；issue 01 裁决①在 D-43 理由栏（frontmatter 复用既有 pyyaml）已补记
6. **本票消费的 issue 01 产物（源码，权威）**：`src/oncall/remediation/runbook.py`（`load_runbook_file` / `load_runbook_library` / `Runbook` / `RunbookValidationError` / `ACTION_KEYS`）+ `remediation/runbooks/{cpu-spike,slow-sql}.md` + `tests/unit/test_remediation_runbook.py`（读它学 runbook 解析契约与 action 定位语义）
7. **harness 现状源码（必读，按依赖序）**：`src/oncall/harness/tools/schemas.py`（`ExecuteActionInput{action, params}` + `ToolResult{tool, status, data, meta}` 冻结形状）→ `src/oncall/harness/tools/registry.py`（`TOOL_NAMES` 六工具、`ToolHandler(args, *, timeout_seconds)->ToolResult` 接缝、`execute_action_stub`、`register_six_tools`、`ToolRegistry.register/execute/level`）→ `src/oncall/harness/tools/__init__.py`（导出面）→ `src/oncall/harness/permission.py`（`PermissionGate.check` 纯函数 + `LEVEL_DECISIONS[L2]=DENIED`）→ `src/oncall/harness/loop.py`（**`LoopComponents{registry, gate,...}` 组件注入面**；`_execute_step` 里 `gate.check(tool, level, args)` → ALLOWED 才 `registry.execute(...)` → 落证据步）→ `src/oncall/api/investigation.py`（`InvestigationDeps{components: LoopComponents}` 调查注入面）
8. **组装注入先例（照 M3/M4）**：`src/oncall/harness/tools/registry.py` 里 Fetcher 注入 + `src/oncall/ingest/app.py` `create_app` 关键字参数注入面（investigation/classify 运行时透传；本票若要接组装点，看它怎么把 handlers/组件装配进 registry）
9. **会被改动的既有测试（关键！见 §4 裁决①）**：`tests/unit/test_harness_permission.py`（`test_l2_always_denied` / `test_l2_denial_audited_with_tool_and_args` / audit 测试）、`tests/unit/test_harness_tool_registry.py`（`test_execute_action_stub_is_l2_defense_in_depth`）、`tests/unit/test_harness_loop_mechanisms.py`（**`test_fallback_on_gate_denial_feeds_summary` 驱动 execute_action 走 loop，断言 L2 拒绝落审计**）
10. **架构回写目标**：`docs/architecture/architecture.md` §3.3 权限分级 L2 语义注记（评审后动作第 5 条预告：随 T2 回写）

## 2. 任务（权威票面 = `.scratch/m5-remediation-gates/issues/02-execute-action-dryrun.md`，下方为摘要）

新增/改动文件（**除既有 execute stub 语义与架构注记外，不动其他既有 src/ 逻辑面**）：

1. **`src/oncall/harness/tools/execute.py`（新文件）**：干跑 handler。入参已过 `ExecuteActionInput` schema 校验（ToolRegistry 兜底）；职责：把 `action`（`runbook_slug/action_id`，如 `cpu-spike/stop-stress-and-restore-cpuset`）解析为 runbook 定位 → `load_runbook_file`/库加载 → 命中 action 的 steps → 渲染「将执行的命令清单 + 影响面」（干跑预览）→ 经注入的「proposal 存根接缝」建 **pending 处置提案** → 返回 `ToolResult{ok, data:{dry_run_preview, proposal_id}, meta:{...}}`
2. **registry 注入面（registry.py 或 tools/__init__.py 少量改动）**：execute_action 从「无脑 stub」改为「可被注入的干跑 handler」。**未注入时维持原 stub error 语义**（既有测试零回退的关键）
3. **`src/oncall/harness/permission.py` L2 授权判定器（D-41）**：`gate.check` **保持纯函数**；L2 判定 = 注入的「处置授权判定器」（dry-run 判定口径见 §4 裁决①）；`LEVEL_DECISIONS[L2]` 的「永远禁止」注记随本票语义更新
4. **干跑逻辑的「proposal 存根接缝」**（proposal 落库表在 issue 03）：本票定义一个最小接缝（Protocol/协议——干跑产出 `{dry_run_preview, proposal_id}`），**不建表不写库**；由 issue 03 落库实现填充
5. **`docs/architecture/architecture.md` §3.3 L2 语义注记回写**（评审后动作预告）
6. **测试**：`tests/unit/test_execute_action_dryrun.py`（新）+ 必要处更新 `test_harness_permission.py` / `test_harness_tool_registry.py` / `test_harness_loop_mechanisms.py`（见 §4 裁决①）

**干跑预览渲染口径（本票定稿）**：给定 runbook action 的 steps（白名单原子操作引用 + 参数模板），渲染成人类可读 + 可锁定的「将执行的命令清单 + 影响面」——清单以原子操作→具体命令形态呈现（如 `docker remove_container name=cpu-spike-probe`），影响面标注 demo 侧副作用（停容器/改 cpuset/KILL 会话等）。**命令字符串永不来自模型自由文本或 runbook 正文**（D-42/C4）；本票只渲染预览，正则匹配校验归 issue 05。参数模板 `$var`（issue 01 定稿语义）在干跑期需被显式标注「运行时解析，执行期注入」，不静默吞掉。

**proposal 存根接缝**：`proposal_id` 由注入的存根返回（本票可给 `pending-<uuid>` 之类的确定性占位或注入假 id），字段语义对齐 issue 03 的 `remediation_proposals`（slug/action_id/status=pending/dry_run_json）——**不建表**。

**错误路径**（验收）：action 不存在 / runbook slug 不在库 / action_id 命中不到 / 参数结构非法 / 未注入存根 → 返回 `ToolResult{status: error, meta:{reason}}`，不抛原始异常（D-16），handler 错误也照 registry `_invoke` 的 handler_crash 兜底。ToolResult 形状不变。

TDD 顺序建议：execute.py 干跑 handler 纯逻辑（给定注入 runbook + 存根 → 返回 ok + 预览 + proposal_id）→ 各错误路径 → registry 注入面（未注入维持 stub）→ PermissionGate L2 授权判定器 + 受影响 loop 测试更新 → 架构注记回写。

## 3. 边界（勿越）

- **执行面零接触**：本票 handler **永不执行任何 demo 侧写操作、零 subprocess、零真实容器操作**——「干跑后 demo 侧状态零变化」用执行器注入替身断言（记录调用即证明，不做真实 subprocess）
- **C3 硬约束**：`oncall.remediation` 在 pyproject C3 禁列，**harness 静态面不得 import remediation**——干跑 handler 经「注入的解析/库加载接缝」（Protocol，TYPE_CHECKING）拿到 Runbook 数据，import-linter 不新增 harness→remediation 静态边；handler 与 remediation 的协作走组装点注入（照 query_metrics + Fetcher 先例）
- **D-23 冻结面不破**：六工具集合、`ExecuteActionInput{action, params}`、`ToolResult` 形状、`loop.py` 主循环结构**只消费不推翻**（loop.py 零净增，D-39）——干跑在 handler 侧完成
- **不越票**：不建 proposal 表（03）、不建确认门 API（04）、不建 allowlist 模块（05）、不建 service 状态机（03）、不写恢复验证（06）——proposal 只留存根接缝
- 零 LLM 真实调用、零 HTTP、零 subprocess；`datasets/golden/holdout/` 禁读
- C6 单文件 ≤300 行（execute.py 预算 ≈180–220）；C8 模块级可变全局禁用；命名一律 CONTEXT.md 词汇（干跑/干跑预览/处置提案/推理与执行分离），不造新词
- 提交规范：中文 + type 前缀，预期 `feat(M5-处置闸门): ...`；body 写**为什么**；引用 `.scratch/m5-remediation-gates/issues/02-execute-action-dryrun.md`；不跳过 hooks

## 4. 已知踩坑 + 裁决（实现前必须厘清，收尾请用户确认）

- **①【安全面裁决 · 本票核心】PermissionGate L2「循环内干跑」如何落位 + 会改一条已固化 loop 安全测试**
  - 现状：`test_harness_loop_mechanisms.py::test_fallback_on_gate_denial_feeds_summary` 驱动 execute_action 走 loop，断言 **L2 拒绝**（audit[-1].decision=="denied"、被拒动作不落证据步、`notices` 有「权限」摘要）；`permission.py LEVEL_DECISIONS[L2]="永远禁止"`；`test_l2_always_denied` 断言 L2 DENIED。
  - 已拍板设计（D-39/D-41，用户已采纳）：**循环内 L2 只出干跑**——Planner 调 execute_action 得到的是干跑预览+proposal_id 供其收束，不是 denial；真实执行走确认门/服务层（03/04/05），不进 loop 工具。
  - **推荐裁决（design-faithful）**：loop 内 execute_action 是「干跑请求」——干跑无 demo 副作用（只读渲染+建 pending），故循环内 L2 对干跑 execute_action 判定 **ALLOWED**（放行到干跑 handler 出预览）；真实执行批准只存在于确认门/服务层，不经 loop。→ 相应地，`test_fallback_on_gate_denial` / `test_l2_always_denied` / `test_execute_action_stub_is_l2_defense_in_depth` **须更新**为新干跑语义（断言干跑结果+proposal_id+**零 demo 写**），这是已拍板 M5 设计的**有意后果，非回归**；「既有测试零回退」指**其他工具**与未注入路径的 stub error 语义。gate.check 仍保持纯函数，判定逻辑抽成可注入的「处置授权判定器」常量/函数（不进 Planner/handler 决策路径，D-41）。**此为安全面语义变更，收尾必须把改动后的 L2 判定 + 更新的测试逐条列出请用户确认。**
- **② 干跑「渲染」与「执行」的一致性（风险 4）**：批准对象 = proposal.dry_run_json 具体命令清单；本票只产出预览，**不实现二次解释执行路径**——预览与将来执行（05/06）的一致性由「proposal.dry_run_json 锁定、执行只消费它」保证。本票把 dry_run_preview 的结构定稿（原子操作→命令 + 影响面），供 05/06 消费。
- **③ C3 零静态边的实现手段**：干跑 handler 需要 Runbook 数据但 harness 禁 import remediation → 用「库加载器接缝」注入（类型仅 TYPE_CHECKING/Protocol，照 `query_metrics handler + Fetcher` 先例：registry handler 签名已收 args+timeout，不依赖数据源 import）。组装点（app.py / 测试 make_components）把 runbook 加载函数注入 handler。
- **④ action 定位语义**：`action = "runbook_slug/action_id"`（如 `cpu-spike/stop-stress-and-restore-cpuset`）。解析：slug → 从 runbook 库定位 slug → 在 `.actions` 里按 `.id` 命中 action。缺 slug 或 id → error + meta.reason。runbook slug 来自 issue 01 库（cpu-spike / slow-sql）；**不造新 slug**。
- **⑤ 未注入路径 stub 保持**：干跑 handler 未注入 runbook 加载器/proposal 存根时，execute_action 回到原 stub error 语义（meta.reason 说明缺装配）——既有 registry mock 测试零回退。
- **⑥ 同文件多次编辑必须串行，下一回合 grep 复核落盘**（规避并行的编辑丢改动）
- **⑦ ruff**：`max-args=5`、PLR0912 分支 ≤12、函数 ≤50 语句、C8 模块级可变全局禁用（判定器/常量用 `Final` + frozenset/不可变）；pytest 统计行用重定向 + 退出码，勿传 `-q`
- **⑧ 架构 §3.3 回写措辞**：architecture.md 权限分级 L2 注记——从「永远禁止（M5 实装前）」改为「循环内干跑、确认后经服务层执行」的当前语义；措辞引用 CONTEXT 术语，勿自造。

## 5. 验证路径（收尾清单）

- [ ] issue 02 验收逐项打勾 + 在 issue 文件内回填落位注记（handler 注入面最终形状、proposal 存根接缝口径、L2 判定更新逐条、回写架构 §3.3 措辞、裁决①是否获用户确认）
- [ ] **§4 裁决① 的改动逐条列给用户确认**（更新了哪些 loop/权限测试、L2 新判定语义、为何是已拍板设计的有意后果）——**未确认不入库收尾**
- [ ] 架构守卫全绿：`tests/test_architecture_guards.py`（C3 remediation 包不破坏 harness 禁列 / C6 ≤300 / C8）
- [ ] 门禁：pytest（基线 564 passed / 10 skipped **只增不减**，裁决①更新测试属有意变更须在汇报中单独列出计数变化）+ ruff check + ruff format --check 全绿
- [ ] issue 文件 `Status:` 翻 `resolved`；`.scratch/m5-remediation-gates/spec.md` 任务序列表 02 行勾选
- [ ] 收尾汇报：落位文件清单 + 测试计数（含裁决①更新数）+ 干跑预览数据结构样例 + L2 判定最终语义 + 架构 §3.3 回写措辞 + 裁决记录（①/②/③/④）+ 下一票（03 proposal 落库与状态机，blocked by 02 已解除）就绪确认
