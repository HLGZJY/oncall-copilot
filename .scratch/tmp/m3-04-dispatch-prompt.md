# M3-04 派工 prompt：ContextManager（T4）

oncall-copilot M3「自主根因调查循环」第 4 票：上下文预算管理 ContextManager，落 `src/oncall/harness/context_manager.py`——摘要模板固定（架构 §3.3 四要素）、工具输出 ≤2000 tokens 截断 + 指针、系统提示 ≤1500 tokens、步数 ≥10 移出已证伪假设主上下文（落 session 保留）。**严格 TDD，本票零 LLM 真实调用、零 HTTP（纯内存组件）；M3-01 会话契约与 M3-03 取证工具产物直接复用。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **`dee271e`**（M3-03 已落取证四工具），工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**不得回退**）：`python -m pytest tests -q --cov=src/oncall`（当前 **373 passed / 4 skipped，coverage 97.33%**）+ `python -m ruff check .` + `python -m ruff format --check .`；pytest 输出用 `> file; PYEXIT=$?` 取退出码（管道 grep 会吞退出码）
- 容器栈不需要起；不需要任何 LLM API key；零网络（纯内存组件，A1 断网单测天然满足）

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票必用：调查会话 / Investigation Session、证据步 / Evidence Step、假设 / Hypothesis、上下文窗 / Context Window；**新术语当场入表并写 `_Avoid_`**）
3. `docs/design/m3-investigation-loop-design.md`（**权威设计，status: reviewed**）——重点：§技术方案 G4（截断 + 指针语义 + 步数 ≥10 移出）+ §开发计划 T4 行 + §风险清单 R4（决策输入可注入——ContextManager 输出可替换是 G8 判据 1 的前提）
4. `docs/architecture/architecture.md` §3.3（**摘要模板四要素定值：组件/指标/异常方向/时间窗**；步数 ≥10 移出已证伪假设定值）+ §3.2（ContextManager 职责边界——只做预算与摘要，不生成计划、不改写工具语义）
5. `docs/design/decisions.md`：**D-25（M3 内存契约 + ≤2000 tokens 截断 + `[truncated, full at step N]` 指针——本票核心依据）**、D-22（Planner 输出契约——系统提示要写输出协议 `{thought, next_tool, args}` | `{conclusion}`）、D-23（六工具形状——工具一览的事实来源）
6. **代码先例（照抄结构，不抄业务）**：
   - `src/oncall/harness/session.py`（M3-01：`EvidenceStep` / `Hypothesis` / `InvestigationSession`——`output_json` 与 `output_summary` 双存、`HypothesisStatus` 三态、`step_count`；移出语义操作 `session.hypotheses` 时注意组件 frozen、容器可变）
   - `src/oncall/harness/tools/registry.py`（M3-02：`TOOL_SPECS` 六工具元数据——系统提示「工具一览」从 `name/description` 生成，**勿手抄第二份工具清单**；`_summarize` 的截断+指针先例——语义对齐但**勿改 Registry**）
   - `src/oncall/classify/llm/prompt.py`（M2：prompt 模板落模块级常量的 A2 先例）
   - `src/oncall/harness/tools/sources.py`（M3-03：共享 helper 落位风格）
7. 测试基建：`tests/unit/`（裸 import conftest；autouse 断网 fixture）、`tests/test_architecture_guards.py`（C6 单文件 ≤300 行 / A2 prompt 落位 / C8）

## 2. 任务（权威票面 = `.scratch/m3-investigation-loop/issues/04-context-manager.md`，下方为摘要）

### 摘要模板（纯函数，架构 §3.3 四要素定值）

- 输入 EvidenceStep/ToolResult → 输出摘要串：**组件 / 指标 / 异常方向 / 时间窗**四要素齐全；同输入同输出（确定性，压缩 KV cache 重复前缀——模板文本固定、变量值填空）
- 纯函数无副作用，TDD 接缝；模板文本落**模块级常量**（A2：禁业务代码内联 >200 字符 prompt 拼接，照 M2 `classify/llm/prompt.py` 先例）

### 工具输出预算（≤2000 tokens 截断 + 指针）

- 单次工具输出进上下文 ≤2000 tokens；超出只截「进上下文的摘要」，`output_json`（原始输出）完整落 session（G4 指针语义：M3 指针 = EvidenceStep 对象引用，M4 建表后替换为行 id，接口不变）
- 摘要带 `[truncated, full at step N]` 指针（与 Registry `_summarize` 语义对齐，勿在工具层/本层造第二套不一致口径）

### 系统提示预算（≤1500 tokens）

- 组装：角色 + 工具一览（名称/一句话/何时用——从 `TOOL_SPECS` 生成）+ 输出协议（D-22：`{thought, next_tool, args}` 或 `{conclusion}` 二选一互斥）；工具详情 just-in-time（不预载，`tool_help` 由 Planner 按需查）
- 组装后整体断言 ≤1500 tokens；时间锚缺省口径提示可复用 M3-03 `sources.default_time_window` 的语义说明（一句带过，不展开）

### 步数 ≥10 移出已证伪假设（架构 §3.3 定值）

- `step_count >= 10` 时，主上下文窗口不再包含 `rejected` 假设；`session.hypotheses` 仍全量保留；active/confirmed 不动
- 依赖注入：session 与步数读取经接口（如传 `InvestigationSession` + 可注入步数阈值），便于 T6 主循环复用与 G8 判据 1「决策输入可注入」

TDD 顺序建议：摘要模板纯函数四要素 → 2000 tokens 截断边界（恰等于不截 / 超出截断+指针可回溯）→ 系统提示 ≤1500 断言 → 移出 rejected 假设三断言（窗口不含 / session 保留 / active+confirmed 不动）。

## 3. 边界（勿越）

- 只新建 `src/oncall/harness/context_manager.py` 与 `tests/unit/test_harness_context_manager.py`（如需拆分按 `test_harness_*` 命名风格）；**不改 `session.py`/`registry.py`/`schemas.py`/`permission.py`、不改取证四工具、不改 pyproject、不改 M2 既有文件**
- 零 LLM 真实调用、零 SDK import、零 HTTP（本票纯内存）；不建任何 ORM 表（D-25）
- **token 估算口径注意**：票面 G8 写「2 字符≈1 token（M2 粗估口径），估算函数可注入替换」；Registry 现行是「字符数 ÷4」（`CHARS_PER_TOKEN=4`）。两口径并存时**不改 Registry**——本票估算函数做成可注入，默认实现按票面 G8 落，并在 issue 注记里写明两口径的适用面（Registry 管工具输出截断、本票管系统提示预算），若测试中发现口径冲突导致预算矛盾，先在 issue 注记记录，勿擅自改冻结文件
- holdout/（`datasets/golden/holdout/`）禁读
- C6 单文件 ≤300 行；C8 模块级可变全局禁用；A2 模板落模块级常量；命名一律 CONTEXT.md 词汇，不造新词
- 提交规范：中文 + type 前缀，预期 `feat(M3-调查循环): ...`；body 写**为什么**；引用 `.scratch/m3-investigation-loop/issues/04-context-manager.md`；不跳过 hooks

## 4. 已知踩坑（M0–M3 实录，M3-03 新增 ⑪）

- ① 同文件多次编辑必须串行，下一回合 grep 复核落盘（M3-03 实录：并行 Edit 曾丢改动）
- ② ruff：`max-args=5`、PLR0912 分支 ≤12、PLR0913 参数 ≤5、函数 ≤50 语句——多参收拢成 dataclass（照 M3-03 `_Baseline` 先例）
- ③ A2：模板文本 >200 字符必须落模块级常量，勿内联拼接
- ④ pydantic frozen 模型不可变——「移出主上下文」是视图层过滤，不是改写 `Hypothesis` 对象
- ⑤ tests/unit 不是包（裸 import conftest）；pytest 有 autouse 断网 fixture
- ⑥ StrEnum 成员大写、值为小写串（`HypothesisStatus` 三值照此，勿新造）
- ⑦ E501 行宽 ≤100，中文 docstring 易超，写完先扫一遍
- ⑧ 写完直接 `ruff format` 过一遍再交，别手拧；`ruff check --fix` 可吃掉 import 排序
- ⑨ C6 守卫会抓 >300 行文件——模板表/工具一览用元组表驱动压缩（照 M3-02 `_TOOL_TABLE` 先例）
- ⑩ 模板四要素变量缺失时（如该步非工具步）要有确定性占位（如 `n/a`），勿让 f-string 输出 `{}` 残片
- ⑪ token 估算函数写成模块级可注入（票面明示），测试里用恒等/放大替身验证边界行为，勿硬编码假设字符分布

## 5. 验证路径（收尾清单）

- [ ] issue 04 验收五条逐项打勾（摘要纯函数四要素 / 截断边界+指针 / 系统提示 ≤1500 / 移出 rejected 三断言 / 全量门禁绿），在 issue 文件内回填注记
- [ ] 摘要生成确定性断言绿：同输入两次调用输出逐字节相同
- [ ] 门禁：pytest（基线 373 passed / 4 skipped **只增不减**）+ ruff check + ruff format --check 全绿
- [ ] 架构守卫全绿：C6 / C8 / A1 / A2
- [ ] issue 文件 `Status:` 翻 `resolved`；spec.md 状态节 M3-B 勾选 04
- [ ] 收尾汇报：落位文件清单 + 测试计数 + token 双口径（÷4 与 2字符≈1token）适用面说明 + 下一票（05 Verifier，blocked by 03✓+04✓）就绪确认
