Status: resolved
Blocked by: 01

# 02 ToolRegistry 与权限分级（T2 / G2·G6 推荐）

## 任务

- **ToolRegistry**（`src/oncall/harness/tools/registry.py`）：工具注册（名称/入参 schema/权限层级/执行函数）、参数校验、单工具 30s 超时、失败重试 ≤2（超时与传输错误的重试语义照架构 §3.4：失败归类 `tool_error`）、输出截断 ≤2000 tokens（超出留 session + 返回摘要与 `[truncated, full at step N]` 指针信息）、执行埋点（tokens/cost/latency 进 EvidenceStep 所需字段）
- **PermissionGate**（`src/oncall/harness/permission.py`）：L0 只读自动放行 / L1 写需 API 确认 / L2 永远禁止；独立代码路径，不可被 prompt 绕过（架构 §3.2）
- **两个 stub 工具**：`query_kb` → `ToolResult{status: "unavailable", meta.reason: "M6 未建库"}`（D-08）；`execute_action` → 权限标 L2，调用即被拒并留审计记录（M5 实装四道闸门）

## 要点

- 统一返回 `ToolResult{tool, status: ok|empty|error|unavailable, data, meta}`（G2）；unavailable 语义对齐 D-16，工具层永不向上抛原始异常
- 超时/重试计数必须可注入时钟或超时函数（测试不真等 30s）
- 截断只动「进上下文的摘要」，`output_json` 完整留 session（G4 指针语义）
- C3：本票零 HTTP、零 SDK（真实数据源在 issue 03）；A1 断网单测天然满足

## 验收（可机械判定）

- [x] Registry 注册/查询单测绿（注册六工具名集合精确匹配；未知工具名拒绝且错误信息指导下一步）——`TOOL_NAMES` 精确六名；未知名抛 `UnknownToolError` 并列出可用工具（R7）
- [x] 超时与重试单测绿（重试 ≤2 后归类 tool_error；可注入超时函数，零真实等待）——超时值经 `timeout_seconds` 传给 handler 侧兑现；超时/传输错误重试耗尽 → `meta.failure_mode="tool_error"`
- [x] 输出截断单测绿（>2000 tokens 触发截断 + 指针信息 + session 保留完整输出）——token 估算 = 字符数 ÷ 4；指针 `[truncated, full at step N]`；`result.data` 完整返回（主循环落 session）
- [x] PermissionGate 单测绿（L0 放行 / L1 拦截留待确认 / L2 拒绝且审计记录含工具名与入参）——另钉死 check 签名无 planner/prompt 通道（架构 §3.3）
- [x] execute_action 与 query_kb stub 行为单测绿（L2 拒绝 / unavailable 返回）——`query_kb` → unavailable + `meta.reason="M6 未建库"`；`execute_action` 标 L2 + stub 纵深防御
- [x] 全量 pytest + ruff 双检绿——343 passed / 4 skipped（基线 314 + 29），coverage 97.35%；ruff check + format --check 全绿；C6（registry.py 279 行）等架构守卫全绿

> 注记（2026-09-08 落地）：落位 `src/oncall/harness/tools/`（`__init__.py`/`schemas.py`/`registry.py`）+ `src/oncall/harness/permission.py`；测试 `tests/unit/test_harness_tool_registry.py`（22 例）+ `tests/unit/test_harness_permission.py`（7 例，含权限测试中 stub 联动断言拆至 registry 测试）。四取证工具执行函数以 `ToolHandler` Protocol 注入（照 infra/http.py Fetcher 先例），issue 03 挂真实数据源。
