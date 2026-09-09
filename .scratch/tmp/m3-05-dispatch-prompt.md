# M3-05 派工 prompt：Verifier——规则层 + LLM 裁决接缝（T5）

oncall-copilot M3「自主根因调查循环」第 5 票：Verifier，落 `src/oncall/harness/verifier.py`——**规则层**（每步零成本确定性校验：ToolResult 状态 / args 与工具 schema 合法性 / 假设-证据步引用存在性 / hallucination 判定）+ **LLM 裁决接缝**（两个触发时机：新假设提出、收束判定，每次调查 ≤3 次；`VerifierJudge` Protocol + Mock 实现；证伪导向独立 prompt；裁决输出 `{supported: bool, reason}`，不生成新计划）。裁决结果驱动假设状态流转 confirmed / rejected 写回 session.hypotheses。**严格 TDD，本票零 LLM 真实调用、零 HTTP（纯内存组件）；M3-01 会话契约、M3-02 Planner 接缝与工具形状、M3-04 ContextManager 产物直接复用。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **`62e3f6b`**（M3-04 已落 ContextManager），工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**不得回退**）：`python -m pytest tests --cov=src/oncall`（当前 **394 passed / 4 skipped，coverage 97%**，C9 门槛 80%）+ `python -m ruff check .` + `python -m ruff format --check .`；pytest addopts 已带 `-q`，命令行勿再叠 `-q`（叠成 `-qq` 会吞掉 passed 汇总行）；要留退出码用 `> file; PYEXIT=$?`（管道 grep 会吞退出码）
- 容器栈不需要起；不需要任何 LLM API key；零网络（纯内存组件，A1 断网单测天然满足）

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票必用：验证假设组件 / Verifier、调查会话 / Investigation Session、证据步 / Evidence Step、假设 / Hypothesis、决策输出 / Planner Decision、失败模式 / Failure Mode；**新术语当场入表并写 `_Avoid_`**）
3. `docs/design/m3-investigation-loop-design.md`（**权威设计，status: reviewed**）——重点：§开放设计点 **G5 定案行**（规则+LLM 混合、裁决两时机 ≤3 次、证伪导向、裁决不生成计划）+ §开发计划 **T5 行**（验收口径）+ §风险清单表第 4 行（判据 1 依赖决策输入可注入——T6 才兑现，本票勿越）
4. `docs/design/decisions.md`：**D-26（Verifier 形态——本票核心依据）**、D-22（Planner 输出契约与异常族先例）、D-25（内存契约：假设状态写回只动 `session.hypotheses`）、D-27③/④（步数移出与 Fallback 摘要——Verifier 不负责换向决策，只喂证据）
5. `docs/architecture/architecture.md` §3.2（组件职责边界——Verifier 只校验与裁决，不做规划）+ §1（生成者/评判者分离原则）
6. **代码先例（照抄结构，不抄业务）**：
   - `src/oncall/harness/planner.py`（M3-01：**Mock 回放先例——`MockPlanner` 的 script/夹具/耗尽回落模式逐字照抄风格**；异常族自持 `PlannerOutputError`/`PlannerTimeoutError` 的写法——本票裁决侧异常族同法自持，勿 import oncall.classify，C3）
   - `src/oncall/harness/session.py`（M3-01：`EvidenceStep` / `Hypothesis` / `HypothesisStatus` 三态 / `InvestigationSession.record_step`/`add_hypothesis`；注意组件 **frozen**——状态流转是「替换 `session.hypotheses` 列表中的对象」，不是改写对象）
   - `src/oncall/harness/tools/schemas.py`（M3-02：`ToolResult{tool, status, data, meta}` 与六工具入参 schema——规则层 args 合法性校验以 schema 为权威，勿手抄第二份字段清单）
   - `src/oncall/harness/context_manager.py`（M3-04：模板常量落位 + 可注入接缝风格）
   - `src/oncall/classify/llm/prompt.py`（M2：prompt 模板落模块级常量的 A2 先例）
7. 测试基建：`tests/unit/`（裸 import conftest；autouse 断网 fixture）、`tests/test_architecture_guards.py`（C6 单文件 ≤300 行 / A2 / C8）

## 2. 任务（权威票面 = `.scratch/m3-investigation-loop/issues/05-verifier.md`，下方为摘要）

### 规则层（纯函数族，每步零成本）

- 输入 `session + PlannerDecision/ToolResult` → 输出 findings（结构自定但需可断言），四类校验各含正反例：
  1. **ToolResult 状态检查**：ok/empty 放行，error/unavailable 产出 finding（语义对齐 D-23/D-16，unavailable ≠ 失败）
  2. **args 与工具 schema 合法性**：`PlannerDecision.next_tool` 对应 schema 校验（复用 `TOOL_SPECS`，勿手抄第二份）
  3. **假设-证据步引用存在性**：`Hypothesis.supporting_steps` / `against_steps` 指向的 `step_no` 必须存在于 `session.steps`
  4. **hallucination 判定**：结论/假设引用的工具输出在 session 中不存在即检出（收束分支与假设提出分支都要覆盖）

### LLM 裁决接缝（两时机，≤3 次/调查）

- `VerifierJudge` Protocol：输入证伪导向上下文（假设 + 相关证据摘要），输出 `{supported: bool, reason}`（Pydantic 契约，D-22 同款开法）；**Verifier 不产出计划**——返回结构中不得出现 `next_tool` / 计划字段（架构 §3.2，验收硬口径）
- 触发时机：新假设提出时、收束判定时；**计数器 ≤3 次/调查**，超限即拒绝调用并降级为规则层结论（不抛错中断调查）
- Mock 实现（照 `MockPlanner` 夹具风格）：需支持 **支持 / 推翻 / 超时** 三态夹具混排回放，供 T6 编排测试复用
- 证伪导向独立 prompt：模板落**模块级常量**（A2：>200 字符禁内联，照 M2 `classify/llm/prompt.py` 先例）；prompt 语义「证伪导向」= 默认立场是找证据推翻假设，不是证明它

### 裁决结果驱动假设流转

- `supported=False` → 该假设翻 `HypothesisStatus.REJECTED`；`supported=True` → `CONFIRMED`；写回 `session.hypotheses`（组件 frozen：用 `model_copy(update=...)` 重建后按序替换列表，勿原地改写）
- 写回时 `supporting_steps` / `against_steps` 引用合法性由规则层第 3 类校验前置把关

TDD 顺序建议：规则层四类校验正反例 → 裁决契约 `{supported, reason}` 两态校验 → 计数器 ≤3（第 4 次被拒 + 降级路径）→ 假设流转写回三断言（rejected 写回 / confirmed 写回 / 引用合法前置）→ Verifier 零计划产出 → Mock 三态夹具。

## 3. 边界（勿越）

- 只新建 `src/oncall/harness/verifier.py` 与 `tests/unit/test_harness_verifier.py`；**不改 `session.py`/`planner.py`/`registry.py`/`schemas.py`/`context_manager.py`、不改取证四工具、不改 pyproject、不改 M2 既有文件**
- 零 LLM 真实调用、零 SDK import、零 HTTP（本票纯内存）；**C3：禁 import oncall.classify**（异常族/契约异常 harness 自持，照 planner.py 先例）
- 不建任何 ORM 表（D-25）；不做换向决策（Fallback 换向是 T6 主循环职责，D-27④）；不做步数移出（M3-04 已落）
- 裁决计数器归属票面明确「在 session 上」——但 `session.py` 是冻结文件**不可加字段**：实现为 Verifier 持有与 incident 关联的独立计数状态（构造时注入/按 session 实例键控），并在 issue 注记里写明该偏差与理由；若认为必须加字段，先停手在 issue 注记记录，勿擅自改冻结文件
- holdout/（`datasets/golden/holdout/`）禁读
- C6 单文件 ≤300 行（模板与校验表用模块级常量/元组表驱动压缩）；C8 模块级可变全局禁用（计数器封装进类实例，勿落模块级 dict）；A2 模板落模块级常量；E501 行宽 ≤100；命名一律 CONTEXT.md 词汇，不造新词
- 提交规范：中文 + type 前缀，预期 `feat(M3-调查循环): ...`；body 写**为什么**；引用 `.scratch/m3-investigation-loop/issues/05-verifier.md`；不跳过 hooks

## 4. 已知踩坑（M0–M3 实录，沿用 M3-04 增补版）

- ① 同文件多次编辑必须串行，下一回合 grep 复核落盘（并行 Edit 曾丢改动）
- ② ruff：`max-args=5`、PLR0912 分支 ≤12、PLR0913 参数 ≤5、函数 ≤50 语句——多参收拢成 dataclass（照 M3-03 `_Baseline` 先例）
- ③ A2：模板文本 >200 字符必须落模块级常量，勿内联拼接
- ④ pydantic frozen 模型不可变——假设状态流转用 `model_copy(update=...)` 重建替换，不是改写对象
- ⑤ tests/unit 不是包（裸 import conftest）；pytest 有 autouse 断网 fixture
- ⑥ StrEnum 成员大写、值为小写串（`HypothesisStatus` 三值照此，勿新造）
- ⑦ E501 行宽 ≤100，中文 docstring 易超，写完先扫一遍
- ⑧ 写完直接 `ruff format` 过一遍再交；`ruff check --fix` 可吃掉 import 排序
- ⑨ C6 守卫会抓 >300 行文件——prompt 模板与校验清单表驱动压缩
- ⑩ pyproject addopts 已含 `-q`——汇总行计数用单层 `-q` 的输出确认（394→只增不减）
- ⑪ 接缝一律可注入（estimator/judge/count 均走构造参数），测试用恒等/替身钉边界，勿硬编码假设字符分布
- ⑫ 异常族命名对齐 planner.py（如 `VerifierOutputError`/`VerifierTimeoutError`，语义逐字互引），勿 import classify 复用

## 5. 验证路径（收尾清单）

- [ ] issue 05 验收五条逐项打勾（规则层正反例含 hallucination / 裁决 ≤3 次第 4 次被拒+降级 / 假设流转写回 / Verifier 零计划产出 / 全量门禁绿），在 issue 文件内回填注记（含计数器归属偏差说明）
- [ ] 门禁：pytest（基线 394 passed / 4 skipped **只增不减**）+ ruff check + ruff format --check 全绿
- [ ] 架构守卫全绿：C6 / C8 / A1 / A2 / C3（无 oncall.classify import）
- [ ] issue 文件 `Status:` 翻 `resolved`；spec.md 状态节 M3-B 勾选 05
- [ ] 收尾汇报：落位文件清单 + 测试计数 + 计数器归属实现说明 + 下一票（06 主循环 Loop，blocked by 01–05，05 完成后全解除）就绪确认
