# M3-08 派工 prompt：端到端 3 剧本验证与收尾（T8）

oncall-copilot M3「自主根因调查循环」第 8 票（收官票）：**mock 端到端 3 剧本**（`cpu-spike` / `slow-sql` / `queue-backlog`）经 `POST /investigate`（M3-07 已落）走至 conclusion，结论根因与 golden `root_cause` 规则匹配级比对；随后 **真实 LLM 实测**（`OpenAIPlannerClient` 经 infra 收口，`MockPlanner` → 真实 client 零改测试热切换）回填步数/耗时/成本/结论；最后设计文档验收节逐条回填、翻 `implemented`。**门禁全程零改既有测试语义；真实调用前用户已确认 key（若未确认，先完成 mock 部分，真实实测停在开工门槛）。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录与文件引用始终用绝对路径）；git 基线 **`f10f1a6`**（M3-07 已落调查入口 API），工作区干净（`.workbuddy` 日志与 `.scratch/tmp/` 除外，勿动）
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（**不得回退**）：`python -m pytest tests --cov=src/oncall`（当前 **462 passed / 4 skipped，coverage 97.92%**，C9 门槛 80%；issue 票面写的 280/97.12% 是 T6 期旧数，以本行实测为准，随新增测试自然上涨）+ `python -m ruff check .` + `python -m ruff format --check .`；pytest addopts 已带 `-q`，命令行勿再叠 `-q`；要留退出码用 `> file; PYEXIT=$?`
- **开工分两段**：
  - **A 段（mock 端到端）**：零 LLM 真实调用、零 HTTP 外呼，可直接开工——golden timeline 转成 Fetcher 替身返回值（D-18 同源纪律），api 测试照 `test_investigation_api.py` / `test_classify_api.py` 先例走 `inproc_asgi` + TestClient
  - **B 段（真实 LLM 实测）**：**开工门槛 = 用户已确认 key 可用且授权真实调用**（env 变量已配、能调通 qwen3.7-flash、余额充足；预估单次调查 ≈¥0.02，上限 ¥0.5）。用户未确认前只做 A 段并在 issue 注记停点，不得擅自起真实调用
- 不需要起容器栈（mock 期）；B 段经 `oncall.infra.http`/`infra.llm` 收口真实外呼（仅集成测试标记，不进默认门禁）
- **允许改动**：`src/oncall/infra/`（真实 Planner client，`infra/llm.py` 283 行贴 C6 上限——拆分落位如 `infra/llm_planner.py` 属 pyproject import-linter ignore 清单变更，**票内过评审后执行**：先在 issue 注记给出拆分方案再动手）、`tests/` 新增文件、`datasets/golden/dev/` 只读消费；**不改 harness 既有文件**（`loop.py` 297 行贴 C6 上限）、不改 `db/models.py`、不改 M2/M3 既有测试语义——发现必须改的，停手在 issue 注记记录

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——本票必用：调查会话 / 事件 / 证据步 / 假设 / 决策输出 / 失败模式 / 转人工 / 调查收尾结构 / **调查报告**；新术语当场入表并写 `_Avoid_`）
3. `.scratch/m3-investigation-loop/issues/08-e2e-validation.md`（**权威票面**，下方为摘要）
4. `docs/design/m3-investigation-loop-design.md`：§验收标准（**逐条实测回填，禁虚构**）+ §开发计划 **T8 行** + §风险清单 #1（Planner 畸形率 >10% 触发 G1 复议——本票实测记录）与 #2（成本测算口径）+ §开放设计点 G9（3 剧本与判分基准）
5. `docs/architecture/agent-loop-design.md`：§证据链数据形状（报告比对口径）
6. `docs/design/decisions.md`：**D-18（golden 同源：mock 工具夹具取自 golden timeline，保证「查到的证据」与剧本同源）**、D-22（决策输出协议）、D-29（G9 评测判分）+ D-23~D-28 落位核对
7. **代码先例（照抄结构，不抄业务）**：
   - `src/oncall/api/investigation.py`（M3-07：`POST /investigate` 消费 `LoopComponents`——本票把 mock 组件换成真实 client 组装）
   - `src/oncall/infra/llm.py`（C4+C5 SDK 收口先例：`OpenAILLMClassifier.from_env()`、`LLMConfigError`；真实 Planner client 照此形制）
   - `tests/integration/`（真实调用测试落位与标记先例——**单独 schedule，不进 CI 默认门禁**，带 token 预算）
   - `tests/unit/test_investigation_api.py`（inproc_asgi + mock 组件注入先例，A 段端到端照此组装）
   - `tests/unit/test_harness_loop.py`（MockPlanner 剧本 / `make_components` 夹具——A 段复用）
   - `datasets/golden/dev/`（3 剧本 timeline + `root_cause` 字段；**`holdout/` 禁读含 raw 切片**）
   - `docs/reference/` 中 R9 单价口径（成本回填用）
8. `docs/agents/issue-tracker.md`（issue 状态流转约定）

## 2. 任务（权威票面 = `.scratch/m3-investigation-loop/issues/08-e2e-validation.md`，下方为摘要）

### A 段：mock 端到端 3 剧本

- `cpu-spike` / `slow-sql` / `queue-backlog` 三剧本：golden timeline → Fetcher 替身返回值（D-18），MockPlanner 剧本驱动经 `POST /investigate` 走至 `termination == "concluded"`
- 结论根因 vs golden `root_cause` **规则匹配级**比对（关键实体 + 动作词；LLM-as-judge 归 M7，本票不做）——逐剧本记录命中/偏差
- 步数 ≤15 / 总时长 ≤5min 实测回填（mock 期 FakeClock 即可测步数；时长用真实时钟记录）
- 每剧本一个集成级测试文件（mock 不进默认门禁亦可，但至少 1 个可重复执行的自动化用例 + 实测记录落 issue 注记）

### B 段：真实 LLM 实测（门槛：用户确认 key）

- `OpenAIPlannerClient` 落 `src/oncall/infra/`（协议实现 `PlannerClient`：`decide(view) -> PlannerDecision`，JSON Mode，畸形重试 ≤2 / 超时 30s 语义对齐 D-22——**harness 零改动**，异常族在 infra 侧转换）
- `infra/llm.py` 283 行贴 C6：拆分方案（如 `llm_planner.py` + pyproject `ignore_imports` 扩清单）**先注记过评审再执行**
- 3 剧本真实调用回填：步数 / 耗时 / 成本（usage × R9 单价，¥0.5 上限比对）/ 结论 + usage 明细；与 mock 结论差异记录
- 每步畸形率实测记录（风险 #1：>10% 触发 G1 复议——只记录并上报，本票不擅自改 G1 定案）
- 单元门禁零真实调用：client 的畸形/超时分支用 mock transport 单测；真实调用测试打 `integration` 标记

### 收尾

- 设计文档 §验收标准逐条打勾回填（禁虚构，实测不到的如实标注）→ frontmatter 翻 `implemented`
- `CONTEXT.md` 新术语核对（本票如无新词则在注记写「无新增」）；`decisions.md` D-22+ 落位核对
- spec.md 状态节 M3-C 勾 08、票面表 08 翻 `resolved`；M3 里程碑整体收尾状态注明

TDD 顺序建议：A 段 golden→Fetcher 替身夹具 → 3 剧本端到端 → 规则匹配判分器 → B 段 client 契约单测（mock transport）→ 真实实测脚本 → 收尾回填。

## 3. 边界（勿越）

- **holdout（`datasets/golden/holdout/`）禁读含 raw 切片**；只碰 `datasets/golden/dev/`
- harness 包内零改动、零 SDK import（C3）；LLM SDK 只许 `infra/` 收口（C4+C5，新文件若 import openai 需扩 pyproject ignore 清单并过评审）
- 不做 M7 评测台、不做 M8 UI、不做 LLM-as-judge；不改 M2/M3 既有测试语义（门禁基线只增不减）
- ruff：C6 单文件 ≤300 行（`infra/llm.py` 已 283——拆分优先）；E501 ≤100；PLR0913 多参收拢 dataclass（照 `LoopComponents`/`InvestigationDeps` 先例）；pydantic 入参契约 extra=forbid
- B 段真实调用前若用户未确认 key：**停在门槛**，issue 注记写明等待项，A 段照常交付
- 提交规范：中文 + type 前缀，预期 `feat(M3-调查循环): ...`（A/B 段可分两笔提交，A 段先提交）；body 写**为什么**；引用 `.scratch/m3-investigation-loop/issues/08-e2e-validation.md`；不跳过 hooks

## 4. 已知踩坑（M0–M3 实录，沿用 M3-07 增补版）

- ① 同文件多次编辑必须**串行**，下一回合 grep 复核落盘（M3-06/M3-07 两次实锤）
- ② ruff：PLR0913/PLR0917 max-args=5——新增注入参数收拢 dataclass；若收拢会破坏既有测试调用面（如 `create_app`），带理由 `# noqa` 并在 issue 注记（M3-07 先例）
- ③ A2：>200 字符模板文本落模块级常量（真实 Planner 的 prompt 模板照 `classify/llm/prompt.py` 先例）
- ④ pydantic frozen 模型不可变——序列化用 `model_dump(mode="json")`
- ⑤ pytest addopts 已含 `-q`——计数用单层 `-q` 输出（462→只增不减）
- ⑥ API 测试带 `pytestmark = pytest.mark.inproc_asgi`（否则 A1 断网 fixture 掐 socket）；真实调用测试打 `integration` 标记走单独 schedule
- ⑦ E501 ≤100，中文 docstring 易超，写完先扫
- ⑧ 交前 `ruff format` 过一遍；注意 format 强制顶层 def 两空行与 `__all__` 魔法逗号展开（新文件行数预算先算，C6 ≤300）
- ⑨ 异常族语义对齐 harness：`PlannerOutputError`/`PlannerTimeoutError` 在 harness 内自持——infra 真实 client 捕获 SDK 异常后**转抛同族**（语义逐字对齐 M2 `LLMOutputError`/`LLMTimeoutError`），勿跨包 import classify
- ⑩ MockPlanner 剧本耗尽稳定回落默认收束——A 段断言前确认剧本长度覆盖被测路径
- ⑪ `session.conclude()/escalate()/abort()` 单次迁移守卫——每次调查新建 session（M3-07 路由已处理）
- ⑫ C3 守卫会抓 harness 内 import api / infra 的 SDK 越界——api → harness 单向合法，SDK 只在 infra
- ⑬ **MockVerifierJudge 缺省裁决 = 证伪导向 rejected**；0 步收束必触 hallucination abort——A 段剧本须先落证据步再收束（M3-07 实录）
- ⑭ golden timeline 时区/字段名先读 schema 再写转换器，勿猜（D-18 夹具同源是 A 段判分可信的前提）

## 5. 验证路径（收尾清单）

- [ ] issue 08 验收五条逐项打勾（A 段 3 剧本 / 步数时长 / B 段实测回填 / 收尾文档 / 全量门禁），issue 注记回填实测数据（步数/耗时/成本/结论/畸形率）与任何边界偏差及理由
- [ ] 门禁：pytest（基线 462 passed / 4 skipped **只增不减**）+ ruff check + ruff format --check 全绿；coverage ≥80%
- [ ] 架构守卫全绿：C3 / C4+C5（SDK 只在 infra）/ C6 / C8 / A1 / A2
- [ ] issue 文件 `Status:` 翻 `resolved`（B 段被 key 门槛阻塞则 `ready-for-human` 并注明等待项）；spec.md 状态节 M3-C 勾 08
- [ ] 收尾汇报：3 剧本逐条命中表（mock vs golden vs 真实）+ 成本/步数/耗时/畸形率实测表 + 设计文档翻 `implemented` 确认 + M3 里程碑收尾总结与 M4 衔接提示
