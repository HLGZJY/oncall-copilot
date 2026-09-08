Status: resolved
Blocked by: 01

# 02 execute_action 干跑实装（T2 / G1 / G3 / D-39 / D-41）

## 任务

- registry 注入面接 remediation 执行器：harness 侧只定义「执行器接缝 Protocol」（干跑/授权判定接口，住 `src/oncall/harness/tools/execute.py` 新文件或既有注入点），remediation 实现该接缝；组装点 `ingest/app.py create_app` 注入，类型仅 TYPE_CHECKING/Protocol——照 query_metrics + Fetcher 注入先例，**import-linter 静态面不新增 harness→remediation 边（C3）**
- execute_action **干跑 handler 实装**（stub → 干跑模式）：解析 `action`（= `runbook_slug/action_id` 定位器）→ 渲染「将执行的命令清单 + 影响面」（干跑预览）→ 建 **pending 处置提案**（issue 03 落库接缝先行注入或本票 stub 接缝）→ 返回 `ToolResult{ok, 干跑预览 + proposal_id}`
- **未注入时维持原 stub 语义**（error 兜底）——既有测试零回退
- **PermissionGate L2 授权判定器（D-41）**：`gate.check` 保持纯函数；L2 放行条件 = 注入的「处置授权判定器」（判定依据 = 该 incident+action 是否存在 approved 提案）；**循环内 L2 仍只出干跑**（有 approved 提案即放行 = 允许模型二次执行已批准命令，坏）
- `ExecuteActionInput{action, params}` **不扩列**；六工具集合不变（D-23 冻结面）

## 要点

- **推理与执行分离（D-39）**：模型只做「请求处置」决策；批准对象 = 干跑渲染的**具体命令清单**；本票 handler 永不执行任何 demo 侧写操作
- 干跑调用本身即证据步（tool=execute_action）——M4 落库链路复用（D-33/D-34 口径），input = `{action, params}` 原样
- `ToolResult` 形状不变（D-23）；handler 错误路径（action 不存在 / runbook 解析失败 / 参数校验失败）返回 error + meta.reason，不抛原始异常
- 与 loop.py 关系：主循环结构零净增（Loop 只编排不判断，D-39）；本票在 handler 侧完成干跑
- 架构 §3.3 权限分级 L2 语义注记随本票回写（评审后动作第 5 条预告）

## 验收（可机械判定）

- [x] pytest 绿：注入执行器后干跑返回 `ok` + 命令清单 + 影响面 + proposal_id，ToolResult 形状不变
- [x] pytest 绿：干跑后 **demo 侧状态零变化**（执行器注入替身断言零调用 / 快照比对——本票不做真实 subprocess，替身记录调用即证明）
- [x] pytest 绿：未注入路径既有测试零回退（stub error 语义保持）
- [x] pytest 绿：六工具集合 + `ExecuteActionInput` 形状键集合断言不变（D-23 冻结面）
- [x] pytest 绿：L2 授权判定器注入断言——gate.check 纯函数、有/无 approved 提案的放行差异（mock 判定器）
- [x] pytest 绿：action 不存在 / runbook 解析失败 / 参数校验失败 → error + meta.reason（不抛异常）
- [x] 架构守卫全绿（C3 无 harness→remediation 静态边）
- [x] 全量门禁不回退（基线 564/10 → 580/10，仅增不回退）+ ruff 双检

## 落位注记（实现后回填）

- **裁决①（安全面，已实现，须用户确认）**：PermissionGate L2 循环内干跑语义落地——`permission.py` 新增可注入「处置授权判定器」`l2_judge`（D-41），默认 `_loop_l2_dryrun_allow` 对循环内 execute_action 干跑请求判定 **ALLOWED**（只读渲染+建 pending，无 demo 副作用 → 放行到干跑 handler 出预览）；真实执行批准只在确认门/服务层（03/04/05），不经 loop。`LEVEL_DECISIONS[L2]` 由「永远禁止」更新为「经注入判定器裁定」注记。受影响测试更新（有意后果，非回归）：`test_l2_always_denied`→`test_l2_execute_action_dryrun_allowed_in_loop`、`test_l2_denial_audited_*`→audit ALLOWED、`test_execute_action_stub_is_l2_defense_in_depth`→拆为未装配 stub error + 注入干跑 ok 两测、`test_fallback_on_gate_denial`→拆为干跑落证据步 + L2 判定器拒绝 Fallback 两测
- **注入面最终形状**：`src/oncall/harness/tools/execute.py` 导出 `build_execute_action_handler(*, runbook_loader: RunbookLoader|None, proposal_creator: ProposalCreator|None) -> ToolHandler`；未注入任一接缝 → handler 装配缺失返回 `ToolResult{error, meta.reason}`（D-16）。registry 注入面（`register_six_tools` 的 `handlers` dict 已支持按名注入 execute_action）未注入时维持 `execute_action_stub`（reason 更新为「处置执行器未装配」）——既有测试零回退
- **proposal 存根接缝口径**：`ProposalCreator(payload: {runbook_slug, action_id, dry_run_json}) -> str`（返回 proposal_id，本票给确定性占位）；字段语义对齐 issue 03 `remediation_proposals`（slug/action_id/status=pending/dry_run_json），不建表不写库
- **干跑预览结构定稿**（供 05/06 消费）：`{runbook_slug, action_id, action_name, commands:[{step, action, command, impact, runtime_params}], impact}`——command 以「白名单原子操作 + 参数模板」形态渲染（如 `docker.remove_container name=cpu-spike-probe`），`$var` 参数显式标注进 runtime_params（裁决②），命令字符串永不来自 runbook 正文/model（D-42/C4）
- **架构 §3.3 回写措辞**：`docs/architecture/architecture.md` §3.4 防失控三闸第 3 点 L2 语义由「永远禁止」更新为「循环内 L2 = 干跑请求 → 放行出预览；真实执行只在确认门/服务层」
- **裁决记录**：①（L2 循环内干跑 ALLOW，见上）②（`$var` 干跑期显式标注不吞）③（C3 零静态边，本票零 remediation import，类型仅本地结构 Protocol）④（action 定位语义 cpu-spike/stop-stress-and-restore-cpuset，不造新 slug）
- **验收计数**：基线 564/10 → 580/10（净 +16：execute.py 干跑 handler 13 测 + registry/loop/permission L2 语义重写后新增断言），裁决① 更新 4 处既有测试语义（有意后果）
