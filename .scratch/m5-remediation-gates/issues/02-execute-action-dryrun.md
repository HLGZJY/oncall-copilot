Status: ready-for-agent
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

- [ ] pytest 绿：注入执行器后干跑返回 `ok` + 命令清单 + 影响面 + proposal_id，ToolResult 形状不变
- [ ] pytest 绿：干跑后 **demo 侧状态零变化**（执行器注入替身断言零调用 / 快照比对——本票不做真实 subprocess，替身记录调用即证明）
- [ ] pytest 绿：未注入路径既有测试零回退（stub error 语义保持）
- [ ] pytest 绿：六工具集合 + `ExecuteActionInput` 形状键集合断言不变（D-23 冻结面）
- [ ] pytest 绿：L2 授权判定器注入断言——gate.check 纯函数、有/无 approved 提案的放行差异（mock 判定器）
- [ ] pytest 绿：action 不存在 / runbook 解析失败 / 参数校验失败 → error + meta.reason（不抛异常）
- [ ] 架构守卫全绿（C3 无 harness→remediation 静态边）
- [ ] 全量门禁不回退（基线 541/10）+ ruff 双检

## 落位注记（实现后回填）

- （待实现票回填：注入面最终形状、proposal 接缝口径、回写架构 §3.3 的措辞）
