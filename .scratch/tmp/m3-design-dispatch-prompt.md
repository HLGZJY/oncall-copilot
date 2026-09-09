# M3 · 设计文档编写派工 prompt

oncall-copilot M3「自主根因调查循环（自研 ReAct）」：设计文档编写 + G 点评审 + tracker 建立（纯设计票，零生产代码改动）

M2 已全部收官（七票 resolved，降噪率/漏报/成本三口径实测回填，`implemented`）。本票启动 M3：**只做设计，不开工实现**。三件套交付——① `docs/design/m3-investigation-loop-design.md`（照仓内模板，status 起 `draft`）；② 开放设计点 G1–Gn 评审（AI 检索官方标准逐条给推荐解与依据，用户逐条拍板、保留推翻权，全部定案后翻 `reviewed`）；③ `.scratch/m3-investigation-loop/` tracker（spec.md + issues/01..NN，标好就绪态）。**设计期 LLM 全 mock（2026-09-08 拍板，照 M2 先例），零真实调用、不需要 API key。**

## 0. 环境与仓库

- 仓库：`F:\Git repository\oncall-copilot`（工作目录始终用绝对路径）；git 基线 `18f4f9d`，工作区干净
- Python 解释器（venv，勿用系统 python）：`C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
- 门禁基线（本票原则上不碰代码，收尾跑一遍确认无破坏即可）：`python -m pytest tests -q --cov=src/oncall`（当前 **280 passed / 4 skipped，coverage 97.12%**）+ `python -m ruff check .` + `python -m ruff format --check .`
- 容器栈不需要起（本票无端到端实测）；在线检索 G 点依据可直接访问官方文档（Anthropic / OpenAI / Prometheus / Grafana Loki / 阿里云百炼），标注 URL 与取用日期

## 1. 必读（按顺序）

1. `AGENTS.md`（12 条硬性规则 + 提交规范 + 文档分层导航）
2. `CONTEXT.md`（术语权威——M3 相关必读：ReAct 循环 / 工具 / Planner / Verifier / 记忆 / 步数上限=15 / 回退 / 证据链 / 假设（confirmed/rejected）/ RAG=工具 / L3 水位；新术语当场入表并写 `_Avoid_`）
3. `docs/design/decisions.md`：**D-03（自研轻量 ReAct，不 fork 框架）**、D-05（L3 受控自动执行）、D-08（RAG 只用于召回历史事故，是工具不是架构）、D-10（tracker=.scratch）、**D-12 / D-13 / D-16 / D-17 / D-19（M3 的冻结输入契约——只消费不推翻）**
4. `docs/architecture/architecture.md`：§3（六组件 Loop/Planner/ToolRegistry/ContextManager/Verifier/PermissionGate + 防失控三闸：MAX_STEPS=15 硬编码、单工具超时 30s 重试 ≤2、权限三层 L0 只读放行/L1 写需 API 确认/L2 永远禁止；上下文管理：工具输出 ≤2000 tokens 截断+落库指针、系统提示 ≤1500、步数 ≥10 移出已证伪假设）+ §4（六表中 M3 产出 `evidence_steps` / `hypotheses` 的字段定义，`output_json` 与 `output_summary` 两个都要）+ §5（时序：以 incident 为调查入口）+ §6（评测衔接与失败模式五类）
5. `docs/architecture/agent-loop-design.md`（工具清单 ≥6：query_metrics / search_logs / detect_anomaly / query_kb / get_topology / execute_action；每轮协议；**防伪 Agent 三判据**；证据链三必须：每步可回溯原始工具输出、假设带 confirmed/rejected、报告可导出 JSON/Markdown）
6. `docs/prd.md`：§3 硬规则（真 Agent 不伪 Agent、规则先行 LLM 兜底、成本四杠杆）、§4 验收硬口径（Top-1 ≥70% / Top-3 ≥85%、≤15 步 / ≤5 分钟 / ≤¥0.5）、§5-M3 行、§6 选型（OpenAI-compatible + 结构化输出）、§7-M3 实现步骤（6 工具定义、循环协议、验收 3 剧本）
7. `docs/plans/roadmap-m0-m9.md`（M3 条目 + W3「M3 自主调查 v1」/ W4「M3 完善 + M4 证据链」切分 + **M3/M4/M7 不可砍**的裁剪纪律）
8. `docs/design/feature-design-template.md`（设计文档骨架，逐节照写）
9. `docs/design/m2-denoise-classify-design.md`（**同仓最成熟先例**：学它的 G 点表写法（开放点/推荐默认解/评审定案/评审依据 R1–Rn）、验收节「可机械判定、实测回填」的写法、status 生命周期 draft→reviewed→implemented）
10. `pyproject.toml` 的 import-linter 契约：**C3——`oncall.harness` 禁止 import oncall.ingest / classify / remediation / knowledge / eval / api**（harness 独立性的物理保证，M3 模块布局必须满足）；C4+C5（HTTP/LLM SDK 只许 infra 收口）；C9（coverage ≥80%）+ `tests/test_architecture_guards.py` 守卫清单（C6 单文件 ≤300 行 / A2 prompt 模板 / A1 断网单测 / R6）
11. 代码接缝（M3 从哪里起步）：`src/oncall/api/card.py`（`build_alert_card` = D-17 卡片、`list_incidents`）、`src/oncall/context/`（三源拉取现有实现，M3 工具层可复用什么）、`src/oncall/infra/llm.py`（LLM 收口：`LLMClientConfig.from_env` + 异常契约 + `last_usage` 计量）、`src/oncall/db/models.py`（`Incident`，`alert_ids[0]` = primary anchor）、`src/oncall/classify/client.py`（Mock 可编程 script 回放先例，M3 Planner mock 照此）
12. 数据底座盘点：`datasets/golden/dev/` 12 剧本、`holdout/` 11 份（**只看文件名，禁读内容**）、`chaos/scenarios/` 12 个剧本；注意 `HOLDOUT_SYNC_PENDING` 过渡豁免（false-positive-flap 未同步 holdout，M7 前待办）

## 2. 范围与交付（三件套）

**① 设计文档 `docs/design/m3-investigation-loop-design.md`**

- 照 feature-design-template 逐节写：status（draft，含评审人/日期占位）→ 目标（背景/要解决的问题/**Non-goals**）→ 技术方案（一句话概括 + 模块表 + 数据模型变更 + API 变更）→ 验收标准（**逐条可机械判定，禁虚构**）→ 依赖 → 开放设计点 G1–Gn
- **Non-goals 必写**：证据链落库归 M4（M3 只定义内存契约与写入接口）、评测打分归 M7、处置执行归 M5（M3 只留 `execute_action` stub，走 PermissionGate 形态但不实现四道闸门）、不做多 Agent（架构 §8）、不做 UI、设计期零真实 LLM 调用（全 mock）
- 冻结契约**只消费不推翻**：D-17（事件卡片 13 键）/ D-19（incidents 五字段 + classification_json）/ D-16（三源形状）/ D-13（alert_events 增列）/ D-12（scenario 契约）——字段名不得改动，改动须回 decisions.md 评审
- 硬口径直接对齐：单次调查 ≤15 步 / ≤5 分钟 / LLM 成本 ≤¥0.5；根因 Top-1 ≥70% / Top-3 ≥85%；证据链三必须；Planner 输出协议 `{thought, next_tool, args}` 或 `{conclusion}`（架构文档已定，精确 schema 是 G 点）
- 模块布局必须满足 C3：调查循环主体放 `oncall/harness`（或论证后的等价包名），**不得 import classify / ingest / api 等**——工具层放哪个包、依赖如何注入（Protocol 接缝）要在设计里论证清楚

**② G 点评审（核心交付，6–9 条）**

每条给四栏：开放点 / 推荐默认解 / 理由 / 依据（官方标准来源 R1–Rn，标注 URL 与取用日期）。建议覆盖（可增删合并）：

- Planner 结构化输出的精确契约：OpenAI tool-calling vs JSON mode（qwen3.7-flash 兼容端点实测 JSON Mode 可用，tool-calling 支持度待查证）；畸形输出重试策略与 M2 `LLMOutputError`/`LLMTimeoutError` 异常契约的关系（建议复用同一契约族）
- 六工具的精确边界与 I/O 形状（尤其 query_metrics 的 PromQL 接口、search_logs 的 Loki 查询形态、query_kb 的 RAG 检索方式）；`detect_anomaly` 的方法选型（PRD：统计 + IsolationForest）与依赖治理（sklearn/numpy 进 pyproject 的 C2 版本区间，还是先纯统计轻量版）
- 记忆/上下文窗口管理：架构约束（≤2000 tokens 截断 + **落库指针**）在 M4 未建表时如何表达——M3/M4 边界的关键取舍（建议：M3 以内存 EvidenceStep 对象为「指针」，M4 建表后替换为 id）
- `evidence_steps` / `hypotheses`：M3 只定义 Pydantic/dataclass 契约 + 内存持有（M4 建 ORM 表）vs M3 直接建表——定谁、为什么
- Verifier 形态：纯规则 / LLM-as-verifier 每步调用 / 规则+LLM 混合——幻觉风险与 ≤¥0.5 成本口径的权衡（推荐混合：规则可判的确定性校验 + 低频 LLM 裁决）
- 防绕圈与假设收敛：重复工具调用检测、假设去重、步数 ≥10 移除已证伪假设的触发条件、回退（Fallback）语义
- 终止语义与失败模式归类：tool_error / plan_error / timeout / hallucination / no_signal / premature_stop 的判定规则；超步 `escalate_to_human` 在 M3 的落点形态（无 UI 阶段是什么）
- 测试策略：Planner 决策序列 mock（可编程 script 回放，照 M2 `MockLLMClassifier` 先例）；**防伪 Agent 三判据如何变成可机械断言**（这是本设计的测试难点，重点写）
- M3 验收剧本选择：PRD §7 说 CPU / 慢SQL / 队列堆积——与 M2 三验证剧本（slow-sql / protocol-mismatch / false-positive-flap）复用还是错开；根因判分基准用 golden 哪个字段（root_cause？D-18 纪律）

出草案后**逐条征询用户拍板**（用户保留推翻权）；全部定案后：文档翻 `reviewed`、定案结论登记 `docs/design/decisions.md`（从 **D-22** 起编号）、新术语入 `CONTEXT.md`。

**③ tracker `.scratch/m3-investigation-loop/`**

- `spec.md`（目标 / 关键契约 / 任务序列 / 状态节，照 M2 spec 先例）+ `issues/01..NN-<slug>.md`（一票一文件，顶部 `Status:` + `Blocked by:`）
- 粒度照 M2 T1–T7：按「分类内核 → 编排 → 数据/验收」的节奏拆 5–8 票；**涉及主循环结构、工具清单、Verifier 形态等架构取舍的一律 `ready-for-human`**；验收可机械判定的（如单工具形状、mock 回放测试）标 `ready-for-agent`
- **本票不开工实现**：tracker 建完即止，派工由用户另行下发

## 3. 边界（勿越）

- 不改 `src/` 任何生产代码、不改 pyproject、不动容器栈；本票只新增 docs/ 与 .scratch/ 文件
- 不推翻任何冻结契约（D-12/13/16/17/19）；新决策从 D-22 起编号，不悄悄改旧 D
- 设计期零真实 LLM 调用：不需要 API key，**不读 `$HOME/.oncall-llm-env`**，不发起任何真实请求
- `datasets/golden/holdout/` 禁读（含 raw 切片）
- 提交规范：中文 + type 前缀（本票预期 `docs(M3-调查循环): ...`），body 写**为什么**；引用文档路径而非 issue 编号（tracker 尚在建立中）；不跳过 hooks，高危 git 命令先确认

## 4. 已知踩坑（01–07 实录）

- ① 同文件多次编辑必须串行 + 下一回合 grep 复核落盘
- ② ruff `max-args=5`、PLR0912 分支 ≤12；C6 单文件 ≤300 行（设计若附代码骨架示例须留意）
- ③ A2 守卫：prompt 一律放 `prompts/` 模板文件，业务代码禁止内联 >200 字符 prompt——设计文档里的 Planner system prompt 草案要写明落位方式
- ④ pytest 有 autouse 断网 fixture；tests/unit 不是包（裸 import conftest）；`pytest | grep` 会吞退出码，门禁用 `> file; PYEXIT=$?`
- ⑤ Git Bash heredoc 长文本可能报 unexpected EOF 但已执行——执行后 `git status` 核实
- ⑥ pydantic `mode="json"` UTC 序列化是 `Z` 后缀（如设计涉及时间字段示例）
- ⑦ 检索官方标准时注意区分 DeepSeek 与百炼/qwen 的文档口径（成本单价、JSON mode、tool-calling 支持度各家不同），引用时注明是哪家的口径

## 5. 验证路径（收尾清单）

- 设计文档各节齐全（照模板逐节），G1–Gn 全部定案（有「评审定案」列 + 依据 R1–Rn），验收标准逐条可机械判定、零虚构
- `decisions.md` 增 D-22+（含三条 ADR 判据自检：难以逆转 / 无上下文会意外 / 真实权衡）；`CONTEXT.md` 新术语入表（含 `_Avoid_`）；架构文档如需回写，注明「实现票执行」
- tracker 建立：spec.md + issues 全部有 Status / Blocked by；`ready-for-agent` 判据（验收可机械判定）逐票核对；M3/M4/M7 边界在 spec 里写清
- 门禁无破坏：pytest / ruff 全绿（基线 280 passed / 4 skipped / 97.12% 不回退）；工作区干净（.scratch/tmp 临时文件清理）
- 提交历史结构化（文档与 tracker 可分提交）；收尾汇报：设计文档 + G 点定案表 + 拆票表 + **M3 风险清单**（至少覆盖：qwen3.7-flash 的 tool-calling/长 prompt JSON mode 稳定性、15 步循环下 ≤¥0.5 的可行性测算、detect_anomaly 依赖引入的治理影响、防伪 Agent 断言的可测性）——交用户复核后才可派工实现票
