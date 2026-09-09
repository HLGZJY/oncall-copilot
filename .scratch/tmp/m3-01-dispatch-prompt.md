# M3-01 派工 prompt：调查会话契约与 Planner 接缝（T1）

oncall-copilot M3「自主根因调查循环」第 1 票：调查会话契约（InvestigationSession / EvidenceStep / Hypothesis）+ Planner 接缝（PlannerClient Protocol / PlannerDecision / MockPlanner / 异常族），落新包 `src/oncall/harness/`。**严格 TDD，本票零 LLM 真实调用、零 HTTP、零 SDK import（纯契约 + mock）。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **`8f5ad7c`**，工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**不得回退**）：`python -m pytest tests -q --cov=src/oncall`（当前 **280 passed / 4 skipped，coverage 97.12%**）+ `python -m ruff check .` + `python -m ruff format --check .`；pytest 输出用 `> file; PYEXIT=$?` 取退出码（管道 grep 会吞退出码）
- 容器栈不需要起；不需要任何 LLM API key

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票必用：调查会话 / Investigation Session、证据步 / Evidence Step、决策输出 / Planner Decision、ReAct 循环、Planner、记忆、证据链、假设（confirmed/rejected）；**新术语当场入表并写 `_Avoid_`**）
3. `docs/design/m3-investigation-loop-design.md`（**权威设计，status: reviewed**）——重点：§技术方案（C3 论证四条）、§开放设计点 G1（D-22）/ G4（D-25）、§风险清单 #4/#6、§开发计划 T1 行
4. `docs/design/decisions.md`：**D-22（Planner 输出契约与异常族——本票核心依据）**、D-25（M3/M4 数据边界）、D-03（自研 ReAct 只消费）、D-19（incidents 五字段只消费）；M2 先例 D-07
5. `docs/architecture/architecture.md` §3.1/§3.2（六组件职责边界）+ §4（`evidence_steps` / `hypotheses` 冻结字段定义——**本票契约字段必须逐列对齐**）
6. `docs/architecture/agent-loop-design.md`（每轮协议：`{thought, next_tool, args}` 或 `{conclusion}`；证据链数据形状）
7. **代码先例（照抄结构，不抄业务）**：`src/oncall/classify/client.py`（`MockLLMClassifier` 的 script 回放 + 异常族模式——本票 `MockPlanner` 与 `PlannerOutputError`/`PlannerTimeoutError` 照此先例）+ `src/oncall/classify/models.py`（StrEnum + Pydantic 校验模式）
8. 测试基建：`tests/unit/`（**不是包**，裸 import conftest；autouse 断网 fixture 天然满足本票）、`tests/test_architecture_guards.py`（C6 单文件 ≤300 行 / A2 / A1）

## 2. 任务（权威票面 = `.scratch/m3-investigation-loop/issues/01-session-planner-seams.md`，下方为摘要）

落位 `src/oncall/harness/`（新建包，C3 契约：**禁止 import oncall.ingest / classify / remediation / knowledge / eval / api**）：

- **`session.py` 调查会话契约**：`EvidenceStep`（step_no/thought/tool/input_json/output_json/output_summary/tokens/cost_cny/latency_ms/ts——**字段对齐架构 §4 `evidence_steps` 冻结列，`output_json` 与 `output_summary` 两个都要**）、`Hypothesis`（text/status: `confirmed|rejected|active`/supporting_steps/against_steps——对齐架构 §4 `hypotheses`）、`InvestigationSession`（**唯一可变状态**：incident 锚点 + 步计数 + 状态 `running|concluded|escalated|aborted` + 终止原因 + 证据步/假设集合；组件 frozen、容器可变分离——OpenHands 原则；序列化 roundtrip 即断点恢复工件）
- **`planner.py` Planner 接缝**：`PlannerClient` Protocol（`decide(context_view) -> PlannerDecision`）+ `PlannerDecision`（`{thought, next_tool, args}` 或 `{conclusion}` **二选一互斥**，Pydantic 校验）+ 异常族 `PlannerOutputError`/`PlannerTimeoutError`（**语义逐字对齐 M2 `oncall.classify.client` 的 `LLMOutputError`/`LLMTimeoutError`：畸形重试 ≤2、超时 30s 不重试；harness 禁 import classify，异常在 harness 自持 + 模块 docstring 互引**）
- **`MockPlanner`**：可编程 script 回放（PlannerDecision 与异常实例混排、按序消费、耗尽稳定回落默认结论、调用入参记录可断言）——照 `MockLLMClassifier` 先例

TDD 顺序建议：session 契约 → PlannerDecision 校验 → 异常族 → MockPlanner。

## 3. 边界（勿越）

- 只新建 `src/oncall/harness/`（本票仅 session.py / planner.py / __init__.py）与 `tests/unit/test_harness_session.py`、`tests/unit/test_harness_planner.py`；**不改任何既有生产代码、不改 pyproject**
- 零 LLM 真实调用、零 HTTP、零 SDK import（A1 断网单测天然满足）；不建任何 ORM 表（D-25：M3 不建表，db/models.py 不动）
- holdout/（`datasets/golden/holdout/`）禁读
- C6 单文件 ≤300 行；C8 模块级可变全局禁用；命名一律 CONTEXT.md 词汇（调查会话/证据步/决策输出），不造新词
- 提交规范：中文 + type 前缀，预期 `feat(M3-调查循环): ...`；body 写**为什么**；引用 `.scratch/m3-investigation-loop/issues/01-session-planner-seams.md`；不跳过 hooks

## 4. 已知踩坑（M0–M3 实录）

- ① 同文件多次编辑必须串行，下一回合 grep 复核落盘
- ② ruff：`max-args=5`、PLR0912 分支 ≤12、PLR0913 参数 ≤5、函数 ≤50 语句
- ③ A2：本票无 prompt，但勿在代码内联任何 >200 字符提示文本
- ④ pydantic `mode="json"` UTC 序列化是 `Z` 后缀（EvidenceStep.ts 序列化用例注意）
- ⑤ tests/unit 不是包（裸 import conftest）；pytest 有 autouse 断网 fixture
- ⑥ StrEnum 成员大写、值为小写串，保证 JSON 序列化即枚举值（M2 models.py 先例）

## 5. 验证路径（收尾清单）

- [ ] issue 01 验收五条逐项打勾（契约 roundtrip / session 状态机 / PlannerDecision 两分支互斥校验 / MockPlanner 三夹具按序回放耗尽回落 / 全量门禁绿），在 issue 文件内回填注记
- [ ] 架构守卫全绿：C3（import-linter + test_architecture_guards）、C6、A1/A2
- [ ] 门禁：pytest（基线 280 passed / 4 skipped **只增不减**）+ ruff check + ruff format --check 全绿
- [ ] issue 文件 `Status:` 翻 `resolved`；spec.md 状态节 M3-A 勾选 01
- [ ] 收尾汇报：落位文件清单 + 测试计数 + 契约字段与架构 §4 对齐说明 + 下一票（02 ToolRegistry）就绪确认
