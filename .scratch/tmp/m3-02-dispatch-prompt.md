# M3-02 派工 prompt：ToolRegistry 与权限分级（T2）

oncall-copilot M3「自主根因调查循环」第 2 票：ToolRegistry（注册/参数校验/30s 超时/重试 ≤2/输出截断 ≤2000 tokens/执行埋点）+ PermissionGate（L0/L1/L2 权限分级）+ 六工具注册面（query_kb 与 execute_action 两 stub）。落 `src/oncall/harness/tools/` 与 `src/oncall/harness/permission.py`。**严格 TDD，本票零 LLM 真实调用、零 HTTP、零 SDK import（真实数据源在 issue 03 接）。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **`b8ecc45`**（M3-01 已落 harness 包），工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**不得回退**）：`python -m pytest tests -q --cov=src/oncall`（当前 **314 passed / 4 skipped，coverage 97.37%**）+ `python -m ruff check .` + `python -m ruff format --check .`；pytest 输出用 `> file; PYEXIT=$?` 取退出码（管道 grep 会吞退出码）
- 容器栈不需要起；不需要任何 LLM API key
- 上一票（M3-01）产物可直接复用：`src/oncall/harness/`（session.py 的 `EvidenceStep` 埋点字段 / planner.py 的异常族先例）

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票必用：工具 / Tool、工具结果 / Tool Result（`{tool, status: ok|empty|error|unavailable, data, meta}`，D-23）、证据步 / Evidence Step、调查会话；**新术语当场入表并写 `_Avoid_`**）
3. `docs/design/m3-investigation-loop-design.md`（**权威设计，status: reviewed**）——重点：§技术方案 G2（六工具精确边界与 I/O 形状、工具层落位与依赖注入）+ §开发计划 T2 行 + §风险清单 R3/R4/R7
4. `docs/design/decisions.md`：**D-23（六工具统一返回形状与 stub 边界——本票核心依据）**、D-08（RAG 是工具不是架构 → query_kb unavailable stub）、D-16（unavailable 语义：依赖缺失 ≠ 调查失败，永不抛原始异常）、D-28（失败模式归类 `tool_error`）
5. `docs/architecture/architecture.md` §3.2（六组件职责表：ToolRegistry 只做注册/校验/执行/超时/重试/截断/埋点，**不改写语义**）+ §3.4 防失控三闸（**单工具 30s 超时、重试 ≤2、失败归类 tool_error**）+ §3.3（PermissionGate 独立代码路径，不可被 prompt 绕过——推理与权限强制分离）
6. **代码先例（照抄结构，不抄业务）**：
   - `src/oncall/harness/planner.py`（M3-01 异常族与 Protocol 模式；`MockPlanner` script 回放）
   - `src/oncall/harness/session.py`（`EvidenceStep` 埋点字段：tokens/cost_cny/latency_ms/ts——Registry 执行埋点要对齐这些字段）
   - `src/oncall/infra/http.py`（`Fetcher` Protocol 依赖注入先例——本票不实现 HTTP，但工具执行函数必须是可注入 Protocol，issue 03 才挂真实 Fetcher）
   - `src/oncall/classify/client.py`（异常族语义先例）
7. 测试基建：`tests/unit/`（裸 import conftest；autouse 断网 fixture 天然满足本票）、`tests/test_architecture_guards.py`（C6 单文件 ≤300 行 / A1 / A2 / C8）

## 2. 任务（权威票面 = `.scratch/m3-investigation-loop/issues/02-tool-registry-permission.md`，下方为摘要）

### `src/oncall/harness/tools/registry.py` ToolRegistry

- **注册面**：六工具名集合精确匹配（`query_metrics` / `search_logs` / `detect_anomaly` / `get_topology` / `query_kb` / `execute_action`）；每工具注册 名称 / 入参 schema / 权限层级（L0/L1/L2）/ 执行函数
- **四取证工具**（query_metrics/search_logs/detect_anomaly/get_topology）：本票**只注册元数据 + 可注入执行函数接口**，业务实现留 issue 03（T3）；执行函数以 Protocol 注入（照 `infra/http.py` 的 `Fetcher` 先例），测试用 fake handler
- **参数校验**：按注册 schema 校验入参（Pydantic 模型或等价机制），非法入参拒绝且错误信息指导下一步（R7 Anthropic 工具铁律）
- **执行防护**：单工具 30s 超时（**可注入时钟/超时函数，测试零真实等待**）、失败重试 ≤2（超时与传输错误重试，语义照架构 §3.4）、重试耗尽归 `tool_error`（D-28）
- **输出截断**：>2000 tokens 触发——只动「进上下文的摘要」，`output_json` 完整留 session（G4 指针语义）；摘要带 `[truncated, full at step N]` 指针信息；token 估算口径若设计未定，用「字符数 ÷ 4」保守估算并在 docstring 写明，**勿引新依赖**
- **执行埋点**：tokens/cost_cny/latency_ms/ts 产出对齐 `EvidenceStep` 字段，主循环（issue 06）可直接落步

### `src/oncall/harness/permission.py` PermissionGate

- L0 只读自动放行 / L1 写需 API 确认（拦截并留待确认态）/ L2 永远禁止
- **独立代码路径**，与 Planner 输出解耦——不可被 prompt 绕过（架构 §3.2）
- 拒绝与拦截均留**审计记录**（含工具名与入参）

### 两个 stub 工具（D-23）

- `query_kb`：调用即返回 `ToolResult{status: "unavailable", meta.reason: "M6 未建库"}`（D-08：注册面完整、真实 RAG 留 M6）
- `execute_action`：权限标 **L2**，经 PermissionGate 调用即被拒并留审计记录（M5 实装四道闸门）

TDD 顺序建议：ToolResult/注册与查询 → 参数校验 → 超时重试 → 截断指针 → PermissionGate → 两 stub 行为。

## 3. 边界（勿越）

- 只新建 `src/oncall/harness/tools/`（本票仅 `__init__.py` / `registry.py`，如需 schema 文件可拆 `schemas.py`）与 `src/oncall/harness/permission.py`，及 `tests/unit/test_harness_tool_registry.py`、`tests/unit/test_harness_permission.py`；**不改任何既有生产代码（含 M3-01 三文件）、不改 pyproject**
- 零 LLM 真实调用、零 HTTP、零 SDK import（A1 断网单测天然满足；真实数据源 issue 03 才接）；不建任何 ORM 表（D-25）
- holdout/（`datasets/golden/holdout/`）禁读
- C6 单文件 ≤300 行（registry.py 超限就拆 schemas.py，勿硬塞）；C8 模块级可变全局禁用；命名一律 CONTEXT.md 词汇（工具/工具结果），不造新词
- 提交规范：中文 + type 前缀，预期 `feat(M3-调查循环): ...`；body 写**为什么**；引用 `.scratch/m3-investigation-loop/issues/02-tool-registry-permission.md`；不跳过 hooks

## 4. 已知踩坑（M0–M3 实录，M3-01 新增 ⑦⑧）

- ① 同文件多次编辑必须串行，下一回合 grep 复核落盘
- ② ruff：`max-args=5`、PLR0912 分支 ≤12、PLR0913 参数 ≤5、函数 ≤50 语句——Registry.execute 入参多就收拢成 dataclass
- ③ A2：本票无 prompt，勿在代码内联任何 >200 字符提示文本
- ④ pydantic `mode="json"` UTC 序列化是 `Z` 后缀
- ⑤ tests/unit 不是包（裸 import conftest）；pytest 有 autouse 断网 fixture
- ⑥ StrEnum 成员大写、值为小写串（ToolResult.status 四值照此）
- ⑦ E501 行宽 ≤100，中文字段 description 容易超，Field 换行写
- ⑧ ruff format 会重排跨行调用，写完直接 `ruff format` 过一遍再交，别手拧

## 5. 验证路径（收尾清单）

- [ ] issue 02 验收六条逐项打勾（注册六名精确匹配 / 超时重试归 tool_error / 截断+指针+session 完整 / PermissionGate 三级 / 两 stub 行为 / 全量门禁绿），在 issue 文件内回填注记
- [ ] 架构守卫全绿：C3 / C6 / A1/A2 / C8
- [ ] 门禁：pytest（基线 314 passed / 4 skipped **只增不减**）+ ruff check + ruff format --check 全绿
- [ ] issue 文件 `Status:` 翻 `resolved`；spec.md 状态节 M3-A 勾选 02
- [ ] 收尾汇报：落位文件清单 + 测试计数 + ToolResult 形状与 D-23 对齐说明 + 下一票（03 取证工具实现）就绪确认
