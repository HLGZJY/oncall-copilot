# M3-06 派工 prompt：主循环 Loop——终止语义与失败归类（T6）

oncall-copilot M3「自主根因调查循环」第 6 票：主循环 Loop，落 `src/oncall/harness/loop.py`——照架构 §3.1 骨架组装 M3-B 全部组件（Planner 决策 → PermissionGate 校验 → ToolRegistry 执行 → session.record_step（EvidenceStep 100% 记录）→ ContextManager 窗口 → Verifier 校验 → 步数检查），实现**终止三出口**（conclusion 收束 / 15 步 escalated / Harness 熔断）、**失败模式六值归类**（`tool_error` / `plan_error` / `timeout` / `hallucination` / `no_signal` / `premature_stop`）、**防绕圈四机制**（重复调用检测 / 假设去重 / 步数 ≥10 移出证伪假设 / Fallback 摘要喂回）。**严格 TDD，本票零 LLM 真实调用、零 HTTP（MockPlanner + MockVerifierJudge 编排）；防伪三判据三组行为差异单测（G8）是本票灵魂——同一 incident 换脚本 → 步序列与结论跟着变。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **`8d35eac`**（M3-05 已落 Verifier），工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**不得回退**）：`python -m pytest tests --cov=src/oncall`（当前 **423 passed / 4 skipped，coverage 97.53%**，C9 门槛 80%）+ `python -m ruff check .` + `python -m ruff format --check .`；pytest addopts 已带 `-q`，命令行勿再叠 `-q`（叠成 `-qq` 会吞掉 passed 汇总行）；要留退出码用 `> file; PYEXIT=$?`（管道 grep 会吞退出码）
- 容器栈不需要起；不需要任何 LLM API key；零网络（纯内存 + mock 编排，A1 断网单测天然满足）
- **本票允许新增公共依赖 import**：`permission.py`（PermissionGate/PermissionLevel）、`tools/registry.py`（ToolRegistry/register_six_tools/ToolExecution）——但**不改这些文件**（见 §3 边界）

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票必用：调查会话 / Investigation Session、证据步 / Evidence Step、假设 / Hypothesis、决策输出 / Planner Decision、工具结果 / Tool Result、失败模式 / Failure Mode、转人工 / escalate to human；**新术语当场入表并写 `_Avoid_`**）
3. `docs/design/m3-investigation-loop-design.md`（**权威设计，status: reviewed**）——重点：§开放设计点 **G6 定案行**（防绕圈四机制，D-27）+ **G7 定案行**（终止三出口与六值判定规则，D-28）+ **G8 定案行**（三判据如何变成可机械断言的三组单测）+ §开发计划 **T6 行**（验收口径）+ §风险清单表第 4 行（防伪 Agent 断言可测性——判据 1 的「两脚本对照」依赖**决策输入可注入**，本票必须兑现）
4. `docs/design/decisions.md`：**D-27（防绕圈四机制——本票核心依据）**、**D-28（终止语义与六值归类——premature_stop 是 M3 预标注，M7 复核）**、D-22（Planner 异常族：畸形重试 ≤2 → plan_error、超时 30s 不重试 → timeout）、D-26（Verifier 形态——Loop 只消费裁决结果，不替 Verifier 做语义判断）、D-25（内存契约：不建表）
5. `docs/architecture/architecture.md`：§3.1（**主循环骨架图——照此组装，推理与权限分离在不同代码路径**）+ §3.2（组件职责边界——**Loop 不做业务判断**）+ §3.4（防失控三闸：15 步硬编码于 Harness、总时长 >5min、白名单）
6. **代码先例（照抄结构，不抄业务）**：
   - `src/oncall/harness/verifier.py`（M3-05：`Verifier.request_judgment` / `apply_verdict` / `run_rule_checks` 是本票的裁决消费面；`MockVerifierJudge` 三态夹具直接复用）
   - `src/oncall/harness/planner.py`（M3-01：`MockPlanner` script 回放——G8 三组单测全靠它编脚本）
   - `src/oncall/harness/session.py`（M3-01：`record_step` 步号连续 / `add_hypothesis` / `conclude` / `escalate` / `abort`——**终止语义只准走这三个终态方法**）
   - `src/oncall/harness/tools/registry.py`（M3-02/T3：`ToolRegistry.execute` 返回 `ToolExecution{result, output_summary, tokens, cost_cny, latency_ms, ts, truncated}`——主循环直接落 EvidenceStep；超时/重试已在 Registry 内，Loop 不重复实现）
   - `src/oncall/harness/permission.py`（M3-02：PermissionGate 校验落位——L2 拒绝留审计记录，归 `plan_error`？否——看票面 §2 归类表）
   - `src/oncall/harness/context_manager.py`（M3-04：`build_system_prompt` / `visible_hypotheses` / `truncate_to_budget`）
   - `tests/unit/test_harness_verifier.py`（M3-05：夹具风格 `make_step` / `make_session` / `make_hypothesis`——本票测试夹具同法起手）
7. 测试基建：`tests/unit/`（裸 import conftest；autouse 断网 fixture）、`tests/test_architecture_guards.py`（C6 单文件 ≤300 行 / A2 / C8 / C3）

## 2. 任务（权威票面 = `.scratch/m3-investigation-loop/issues/06-investigation-loop.md`，下方为摘要）

### 主循环骨架（照架构 §3.1，Loop 只编排不判断）

- 入口形如 `run_investigation(session, planner, registry, gate, verifier, context, *, max_steps=MAX_STEPS, budget...) -> InvestigationResult`（组件全部构造注入——**决策输入可注入是 G8 判据 1 的前提**，勿硬编码任何组件）
- 每步：`planner.decide(上下文视图)` → 选工具分支走 `gate.check → registry.execute → session.record_step(EvidenceStep 100% 记录 output_json + output_summary)` → `verifier.run_rule_checks`；收束分支走裁决时机二（收束判定）
- **MAX_STEPS = 15 硬编码于 Harness 常量，不进 prompt**（架构 §3.4）

### 终止三出口（G7/D-28）

1. Planner `{conclusion}` → 裁决时机二 → `session.conclude()`；**步数 <5 且无 confirmed 假设 → failure_mode 标 `premature_stop`（预标注，不阻断收束）**
2. 步数 = 15 → `session.escalate()`（status=escalated；无 UI 落点 = escalated 状态 + 证据链留在 session 可导出，T7 API 查）
3. Harness 熔断（绕圈再犯 / 总时长 >5min）→ 熔断原因归类 `plan_error` 或 `timeout`，session 按语义走 `escalate()` 或 `abort()`

### 失败模式六值归类（D-28，落 `InvestigationResult.failure_mode`）

- `tool_error`：工具重试耗尽且无可用 Fallback；`plan_error`：Planner 畸形重试 ≤2 耗尽或绕圈熔断；`timeout`：单步 30s 或总时长 >5min；`hallucination`：Verifier 规则层检出引用不存在的证据步；`no_signal`：三源 unavailable 且无可用证据；`premature_stop`：见上
- 六值判定规则**机械可判**（M7 矩阵列直接生成），归类结果与 `InvestigationResult` 其余字段（结论 / 步数 / 成本 / 假设终态）一并在收尾结构中返回

### 防绕圈四机制（G6/D-27）

1. **重复调用检测**：同 (tool, args 规范化哈希) 连续 ≥2 或累计 ≥3 → 注入循环警告进下一轮 prompt → 再犯熔断归 `plan_error`（规范化哈希 = args 排序后序列化，纯函数可单测）
2. **假设去重**：新假设与既有假设规范化文本相似 → 拒绝入池并提示换向（纯规则匹配，判定函数可注入）
3. **步数 ≥10 移出已证伪假设**：调 `context_manager.visible_hypotheses`（T4 已落，勿重写）
4. **Fallback**：工具重试耗尽 / 假设被推翻时，构造**结构化失败摘要**喂回 Planner 自行换向——Harness 不硬编码换向顺序（D-27④）

TDD 顺序建议：循环骨架两脚本对照（G8 判据 1）→ 终止三出口各一 → 六值归类逐值触发 → 防绕圈四机制逐个 → EvidenceStep 100% 记录断言 → premature_stop 边界（<5 / ≥5 对照）。

## 3. 边界（勿越）

- 只新建 `src/oncall/harness/loop.py` 与 `tests/unit/test_harness_loop.py`（测试文件超 300 行可拆 `test_harness_loop_mechanisms.py`，先例见既有拆法）；**不改 `session.py`/`planner.py`/`verifier.py`/`context_manager.py`/`registry.py`/`permission.py`/`schemas.py`、不改取证四工具、不改 pyproject、不改 M2 既有文件**——发现必须改的，停手在 issue 注记记录
- 零 LLM 真实调用、零 SDK import、零 HTTP；**C3：禁 import oncall.classify**
- 不建任何 ORM 表（D-25）；不做真实 Planner client（infra 侧 T8）；不做 T7 API（报告注册表与 `POST /investigate` 是下一票）；holdout（`datasets/golden/holdout/`）禁读
- 循环体**零业务判断**（架构 §3.2）——工具语义、换向顺序、SOP 一概不进 Loop；六值归类判据全部来自机械可观测状态（步数 / 异常类型 / 计数器 / Verifier findings）
- ruff：PLR0912 分支 ≤12、函数 ≤50 语句、max-args=5——循环体按组件拆私有函数，多参收拢 dataclass（照 M3-03 `_Baseline` 先例）；C6 单文件 ≤300 行；C8 模块级可变全局禁用（重复检测计数器封装进循环状态类）；E501 行宽 ≤100
- 提交规范：中文 + type 前缀，预期 `feat(M3-调查循环): ...`；body 写**为什么**；引用 `.scratch/m3-investigation-loop/issues/06-investigation-loop.md`；不跳过 hooks

## 4. 已知踩坑（M0–M3 实录，沿用 M3-05 增补版）

- ① 同文件多次编辑必须**串行**，下一回合 grep 复核落盘（M3-05 再次实锤：并行 Edit 6 处丢 5 处）
- ② ruff：PLR0912 ≤12 / PLR0913 ≤5 / 函数 ≤50 语句 / max-args=5——循环骨架多组件注入天然多参，收拢成 dataclass
- ③ A2：模板文本 >200 字符必须落模块级常量（Fallback 失败摘要模板、循环警告文本都算）
- ④ pydantic frozen 模型不可变——假设流转照 verifier.py `model_copy(update=...)` 先例
- ⑤ tests/unit 不是包（裸 import conftest）；autouse 断网 fixture
- ⑥ StrEnum 成员大写、值为小串；`SessionStatus` 四态 / `HypothesisStatus` 三态勿新造
- ⑦ E501 ≤100，中文 docstring 易超，写完先扫
- ⑧ 交前 `ruff format` 过一遍；`ruff check --fix` 可吃 import 排序与 `__all__` 排序（RUF022）
- ⑨ C6 守卫抓 >300 行——loop.py 大概率顶格，模板/判定表落模块级常量压缩；超了先压缩注释与 docstring，**勿放宽守卫**
- ⑩ pyproject addopts 已含 `-q`——计数用单层 `-q` 输出（423→只增不减）
- ⑪ 接缝一律可注入：planner / registry / gate / verifier / context / 时钟（总时长判定用可注入 clock，测试造不出真实 5min）——**勿在测试里真 sleep**
- ⑫ 异常族与归类语义对齐 planner.py / verifier.py 逐字互引，勿 import classify
- ⑬ MockPlanner 剧本耗尽会稳定回落默认收束——G8 各组脚本断言前先确认剧本长度覆盖被测路径，别让「耗尽回落」顶替了被测行为
- ⑭ `session.conclude()/escalate()/abort()` 有 `_ensure_running` 单次迁移守卫——终止路径写重了会 ValueError，收尾只走一处

## 5. 验证路径（收尾清单）

- [ ] issue 06 验收六条逐项打勾（G8 三组单测 / 六值各 ≥1 用例 / 终止三出口 / 防绕圈四机制 / EvidenceStep 100% 记录 / 全量门禁绿），在 issue 文件内回填注记（含任何边界偏差与理由）
- [ ] 门禁：pytest（基线 423 passed / 4 skipped **只增不减**）+ ruff check + ruff format --check 全绿
- [ ] 架构守卫全绿：C6 / C8 / A1 / A2 / C3
- [ ] issue 文件 `Status:` 翻 `resolved`；spec.md 状态节 M3-B 勾选 06（M3-B 三票收官）
- [ ] 收尾汇报：落位文件清单 + 测试计数 + 六值归类判定表（值 → 机械判据 → 触发用例名）+ 下一票（07 调查入口 API，blocked by 06，完成后解除）就绪确认
