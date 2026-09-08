---
title: "M3 自主根因调查循环：设计与开发计划"
summary: "以 incident_id 为入口的自研 ReAct 循环：Planner（JSON mode 结构化决策）+ ToolRegistry（六工具）+ ContextManager + Verifier（规则+LLM 混合）+ PermissionGate；M3/M4 边界（内存证据契约，不建表）；G1–G9 开放点评审与 T1–T8 拆票"
source: docs/prd.md §4/§7-M3 + docs/architecture/architecture.md §1/§3/§4/§5/§6 + docs/architecture/agent-loop-design.md + docs/design/m2-denoise-classify-design.md（先例）+ docs/design/decisions.md D-03/05/07/08/12/13/16/17/18/19/21 + 评审标准来源（见「评审依据」R1–R9）
status: draft
updated: 2026-09-08
read_when: 评审 M3 方案时；进入 M3 开发前；被问「M3 自主调查循环怎么做」时
---

# M3 自主根因调查循环：设计与开发计划

## status

`draft`（草案，讨论中）→ `reviewed`（评审通过，可开工）→ `implemented`（已落地，实测数据已回填）→ `superseded`（被后续设计取代，注明替代文档链接）

- **当前状态**：`draft`（2026-09-08 草案完成，待 G1–G9 评审拍板；用户保留逐条推翻权）
- **评审人 / 评审日期**：（评审通过后回填；流程照 M2 先例——AI 检索官方标准逐条给推荐解与依据，用户逐条拍板，全部定案后翻 `reviewed` 并登记 D-22+）
- **关联 issue**：`.scratch/m3-investigation-loop/`（spec.md + issues/01–08，与 T1–T8 一一对应）
- **设计期口径**：LLM 全 mock（2026-09-08 拍板，照 M2 先例）——零真实调用、不需要 API key、不读 `$HOME/.oncall-llm-env`；真实调用与实测回填留实现票（T8，开工前需用户确认 key）

## 目标

- **背景 / 触发原因**：M2 已全部收官（七票 resolved，降噪率/漏报/成本三口径实测回填）。W3 排期 = M3 自主调查 v1（核心攻坚）；本项目差异化三支柱（自主闭环 / 证据链 / 评测）的第一根支柱从此开工。M2 已交付干净入口：`incidents` 表（G5 最小集，`alert_ids[0]` = primary anchor）+ `GET /incidents` 查询面 + D-17 事件卡片（三源上下文 D-16 形状）。
- **要解决的问题**：
  1. **自主调查**：以 `incident_id` 为入口启动自研轻量 ReAct 循环（D-03）——Planner 每步依据证据动态决定「下一步查什么」，证据链三必须全程成立；
  2. **六工具取证**：query_metrics / search_logs / detect_anomaly / query_kb / get_topology / execute_action 注册为工具，I/O 形状一次到位（工具返回是 M4/M7/M8 的输入）；
  3. **假设收敛与可信终止**：假设带 confirmed/rejected 状态、防绕圈、步数上限 15 硬安全阀、失败模式六值强制归类——评测台（M7）可直接消费；
  4. **证据链内存契约**：M3 定义 `EvidenceStep` / `Hypothesis` 契约与写入接口（内存持有），M4 建表落库时契约不变，只把内存对象替换为行 id。
- **不做的事（Non-goals）**：
  - **证据链落库归 M4**：M3 只定义内存契约与写入接口，不建 `evidence_steps` / `hypotheses` ORM 表、不改 `db/models.py`（G4）
  - **评测打分归 M7**：M3 只交付机械归类字段（failure_mode）与 3 剧本首版验证；Top-1/Top-3 矩阵、LLM-as-judge、人工抽检是 M7 设施
  - **处置执行归 M5**：`execute_action` 只留 stub，走 PermissionGate L2（永远拒绝）形态；四道闸门（干跑→确认→执行→恢复验证）不实现
  - **不做多 Agent**（架构 §8）、不做 UI（M8）、不做知识库实体（M6——`query_kb` 为 unavailable stub，G2）
  - **不做跨告警聚合**：调查以单 incident 为入口（`alert_ids[0]` 锚定），M2 G1 遗留的「事件级归并」如需要按 decisions.md 评审另议
  - **不推翻冻结契约**：D-12/13/16/17/19 只消费不推翻（字段名不得改动）；D-18 golden 同源纪律照搬
  - **设计期零真实 LLM 调用**（全 mock）；holdout/ 禁读不变

## 本阶段涉及的技术类别

| 技术类别 | 涉及技术 | 本阶段用途 | 开放点 |
|---|---|---|---|
| LLM API | qwen3.7-flash（OpenAI-compatible，JSON Mode，M2 同款接缝口径） | Planner 每步决策 + Verifier 低频证伪裁决；设计期 mock | 输出契约与 tool-calling 取舍（G1） |
| 自研 ReAct 循环 | Planner / ToolRegistry / ContextManager / Verifier / PermissionGate / Loop 六组件 | 调查主循环（架构 §3 已定骨架，M3 落地） | 终止语义与防绕圈（G6/G7） |
| 可观测 API | Prometheus `/api/v1/query_range`、Loki `/loki/api/v1/query_range`（经 `oncall.infra.http` 收口） | query_metrics / search_logs / get_topology 取证 | I/O 形状（G2） |
| 统计异常检测 | 纯统计（z-score / 分位数 / 环比突变，stdlib） | detect_anomaly 工具 v1 | IsolationForest 与 sklearn 依赖治理（G3） |
| 数据底座 | `datasets/golden/dev/`（12 剧本）、chaos/scenarios（12 个） | 验收 3 剧本 + mock 工具夹具 | 剧本选择与判分基准（G9） |
| 工程门禁 | pytest TDD / ruff / import-linter C3–C5 / 架构守卫 C6/A2 | 循环骨架、工具形状、mock 回放是 TDD 接缝 | 测试策略（G8） |

## 技术方案

**一句话概括**：`POST /investigate {incident_id}` 启动自研 ReAct 单循环——Planner 以 JSON Mode 结构化输出 `{thought, next_tool, args}` 或 `{conclusion}`，ToolRegistry 执行六工具（30s 超时、重试 ≤2、输出截断 ≤2000 tokens），ContextManager 只让证据摘要进上下文（原始输出留 EvidenceStep），Verifier 以「规则每步 + LLM 低频裁决」证伪导向校验假设，步数 15 硬安全阀兜底 `escalate_to_human`；全部证据以内存契约（EvidenceStep/Hypothesis）挂在唯一可变状态 `InvestigationSession` 上，可导出 JSON 报告，M4 建表后契约不变。

### 设计的模块

| 模块 | 动作 | 职责 | 目录 | 关联里程碑 |
|---|---|---|---|---|
| 调查会话契约 | 新增 | `InvestigationSession`（唯一可变状态，OpenHands 原则）+ `EvidenceStep` / `Hypothesis`（Pydantic frozen，字段对齐架构 §4）；序列化即断点恢复工件 | `src/oncall/harness/session.py` | M3 → M4 落库复用 |
| Planner 接缝 | 新增 | `PlannerClient` Protocol + `PlannerDecision` 校验（`{thought, next_tool, args}` \| `{conclusion}`）+ `MockPlanner`（可编程 script 回放，照 M2 `MockLLMClassifier` 先例）+ 异常族 `PlannerOutputError` / `PlannerTimeoutError`（语义逐字对齐 M2 `LLMOutputError` / `LLMTimeoutError`，**不跨包 import**，见 C3 论证） | `src/oncall/harness/planner.py` | M3 → M7 矩阵 |
| 工具注册与权限 | 新增 | ToolRegistry（注册/参数校验/30s 超时/重试 ≤2/输出截断/埋点）+ PermissionGate（L0 只读放行 / L1 写需 API 确认 / L2 永远禁止；`execute_action` 标 L2） | `src/oncall/harness/tools/registry.py` + `permission.py` | M3 → M5 实装 |
| 六工具 | 新增 | query_metrics / search_logs / detect_anomaly / query_kb（unavailable stub）/ get_topology / execute_action（L2 stub）；统一返回 `ToolResult{tool, status: ok\|empty\|error\|unavailable, data, meta}` | `src/oncall/harness/tools/` | M3 → M5/M6 接入 |
| 上下文管理 | 新增 | 摘要模板固定（组件/指标/异常方向/时间窗）、系统提示 ≤1500 tokens、步数 ≥10 移出已证伪假设 | `src/oncall/harness/context_manager.py` | M3 |
| Verifier | 新增 | 规则层（每步零成本确定性校验）+ LLM 裁决接缝（新假设提出 / 收束判定两个时机，≤3 次/调查，证伪导向独立 prompt） | `src/oncall/harness/verifier.py` | M3 → M7 judge 分离 |
| 主循环 | 新增 | 推进 step、终止三出口、失败模式六值归类、escalate_to_human | `src/oncall/harness/loop.py` | M3 |
| 调查入口 API | 新增 | `POST /investigate` + `GET /investigations/{incident_id}`（报告查询，进程内注册表） | `src/oncall/api/` | M3 → M7 runner / M8 |
| 真实 Planner client | 新增（实现票） | `OpenAIPlannerClient`——LLM SDK 必须 infra 收口（C4+C5）；`infra/llm.py` 已 283 行逼近 C6 上限，拆分落位（如 `infra/llm_planner.py` + 扩 import-linter ignore 清单）在实现票过评审后定 | `src/oncall/infra/` | M3 实现票 |

### C3 论证：harness 独立性怎么保住

pyproject C3 契约：`oncall.harness` 禁止 import `oncall.ingest / classify / remediation / knowledge / eval / api`。本设计的落位：

1. **工具层复用 `oncall.context`（不在 C3 禁列）**：get_topology 直接复用 `collect_context` / `service_topology` / `PromClient`；query_metrics 经 `PromClient` 查 query_range——M1 已验证的三源实现零重建；
2. **HTTP 一律经 `oncall.infra.http.Fetcher`**（C4 收口，Loki 查询同走此接缝）；LLM SDK 只许 infra 收口（C5）；
3. **Planner 异常契约族在 harness 内自持**：M2 的 `LLMOutputError` / `LLMTimeoutError` 定义在 `oncall.classify.client`，harness import 它会撞 C3——因此 harness 定义 `PlannerOutputError` / `PlannerTimeoutError`，**语义逐字对齐**（畸形输出重试 ≤2、超时 30s 不重试），文档互相引用，不造第二套语义；
4. **依赖注入解耦**：Planner / Verifier LLM 裁决 / Fetcher / 时钟全部 Protocol 注入，harness 包内零 SDK import、天然满足 A1 断网单测。

### 数据模型变更

| 变更项 | 类型 | 说明 | 迁移方式 |
|---|---|---|---|
| `EvidenceStep` / `Hypothesis` / `InvestigationSession` | 新增（**内存契约，不建表**） | Pydantic，字段名对齐架构 §4 `evidence_steps` / `hypotheses` 冻结列定义（`output_json` 与 `output_summary` **两个都要**）；`InvestigationSession` 序列化即断点恢复工件 | M4 建 ORM 表：契约不变，内存对象替换为行 id（G4） |
| `db/models.py` / 建表 | **无变更** | M3 不建表、不改 models（M4 职责） | — |
| `incidents.status` | 语义消费（不扩枚举） | 调查中保持 `investigating`；`mitigated` / `closed` 流转归 M5 | 无迁移 |

### API 变更

| API | 变更类型 | 请求/响应要点 | 影响调用方 |
|---|---|---|---|
| `POST /investigate` | 新增 | 入参 `{incident_id}`；以 `alert_ids[0]` 锚定 D-17 事件卡片开局；同步 v1（≤5min 内返回）；响应 `InvestigationResult`（conclusion/confidence/steps/hypotheses/failure_mode/termination）；incident 不存在 → 404。**零写操作**（execute_action 为 L2 stub），四道闸门不适用 | M7 runner 驱动、M8 |
| `GET /investigations/{incident_id}` | 新增 | 从进程内报告注册表读最近一次调查报告（M4 落库后改读表）；无 UI 阶段的证据链可查载体，escalated 报告同经此出口 | M4 导出、M8 时间线 |

## 验收标准

> 可实测、可判定；实测后回填打勾，不得虚构（实现票 T8 完成时回填本节）。设计期全 mock，成本口径为 R9 单价估算。

- [ ] **3 剧本 mock 端到端自主收束**（PRD §7-M3）：`cpu-spike` / `slow-sql` / `queue-backlog` 经 `POST /investigate` 走至 conclusion，结论根因与 golden `root_cause` 规则匹配级命中（关键实体+动作词；LLM-as-judge 归 M7）
- [ ] **步数 ≤15 / 总时长 ≤5 分钟**：3 剧本实测回填（mock 期即可测步数与时长）
- [ ] **LLM 成本 ≤¥0.5**：真实调用轮按 usage × R9 单价实测回填；设计期估算 ≈¥0.02/次调查（15 步 Planner + ≤3 次 Verifier 裁决，见 §风险清单测算），有 20× 余量
- [ ] **证据链三必须**：每步 `output_json` + `output_summary` 双存；假设带 `confirmed`/`rejected`；`InvestigationSession` 可导出 JSON 报告（steps/hypotheses/conclusion/confidence，照 agent-loop-design 数据形状）
- [ ] **失败模式六值可逐值触发**：`tool_error` / `plan_error` / `timeout` / `hallucination` / `no_signal` / `premature_stop` 各至少 1 个单测钉死判定规则（G7）
- [ ] **防伪 Agent 三判据机械断言全绿**（G8 三组单测：步骤序列由 Planner 输出决定 / 不调 get_topology 亦正常收束 / 推翻换假设 + 重试回退 + 第 15 步强制 escalated）
- [ ] **架构守卫全绿**：C3（harness 零 import classify/ingest/api 等）+ C4/C5（HTTP/LLM SDK 只在 infra）+ C6（单文件 ≤300 行）+ A2（prompt 模板落位）+ import-linter
- [ ] **全量测试门禁不回退**：pytest + ruff check + ruff format --check 绿，coverage ≥80%（基线 **280 passed / 4 skipped / 97.12%**）
- [ ] **真实 LLM 实测回填**（T8，开工前需用户确认 key）：3 剧本真实调用回填步数/耗时/成本/结论，首版 Top-1 结果记录（口径复核归 M7）

## 依赖

- **前置依赖**：M2 七票全部 resolved ✅（incidents 建档 + `GET /incidents` + D-17 卡片 + D-16 三源上下文）；D-03（自研 ReAct）/ D-05（L3）/ D-08（RAG=工具）只消费不推翻；CONTEXT.md 术语纪律
- **数据 / 环境依赖**：`datasets/golden/dev/`（验收剧本与 mock 夹具来源；**holdout/ 禁读**）；chaos/scenarios 注入脚本；SQLite dev 库；9 容器 compose 栈（真实实测轮用，mock 期不需要）；LLM API key（仅 T8 真实调用前向用户确认）
- **后续影响**：`InvestigationSession` 契约是 M4 落库与导出的直接输入；failure_mode / 步数 / 成本字段是 M7 评测矩阵的列；`POST /investigate` 是 M7 runner 的驱动入口；PermissionGate 形态是 M5 四道闸门的前置

## 开放设计点（评审 grill）

> 以下 G1–G9 为草案推荐解（2026-09-08），**待用户逐条拍板**；全部定案后本表增「评审定案」列、翻 `reviewed`、登记 `decisions.md`（从 D-22 起）、新术语入 `CONTEXT.md`。若某条被推翻，仅回退受影响 issue 的就绪态，不整票作废。

| # | 开放点 | 推荐默认解 | 理由 | 依据 |
|---|---|---|---|---|
| G1 | Planner 结构化输出契约：原生 tool-calling vs JSON mode；畸形输出重试与 M2 异常契约的关系 | **JSON Mode + Pydantic `PlannerDecision`**（M2 同款开法：`response_format={"type":"json_object"}` + `enable_thinking:false`），协议 `{thought, next_tool, args}` 或 `{conclusion}`（架构 §3.1 已定，精确 schema 本条冻结）；畸形输出 → `PlannerOutputError` 重试 ≤2 后归类 `plan_error`；超时 30s → `PlannerTimeoutError` 不重试（照 M2 G3⑤）；异常族在 harness 内自持、语义逐字对齐 M2 契约（C3 不许跨包 import）。原生 tool-calling 留 M7 模型矩阵备选 | M2 实测 JSON Mode 22 次真实调用 21 次一次通过；mock/异常/重试基建可整套复用先例；本协议是「决策 JSON 由 Harness 解析后经 PermissionGate 执行」而非 SDK 原生派发——推理与权限强制分离在不同代码路径上（架构 §1 总原则）。tool-calling 支持度已非阻塞项（R2：百炼官方清单确认 Qwen3.7-Flash 系列支持 FC），但设计期零真实调用无法验证其在长 prompt 下的 tool_calls 稳定性，不做无实测依据的默认 | R1（OpenAI：连接工具场景推荐 function calling，模型响应用结构化 response_format；JSON mode 无 schema 保证需下游校验）+ R2 + R6（最简模式起步）+ M2 issue 07 实测 |
| G2 | 六工具精确边界与 I/O 形状；工具层落位与依赖注入（C3） | 统一返回 `ToolResult{tool, status: ok\|empty\|error\|unavailable, data, meta}`（unavailable 语义对齐 D-16）。① query_metrics：入参 `{promql, start, end, step?}`，时间缺省锚 `last_fired_at` ± D-16 窗口 → `/api/v1/query_range`（series 上限 20，matrix 截断）；② search_logs：入参 `{selector(LogQL), start, end?, limit≤100, direction?}` → `/loki/api/v1/query_range`（R4：limit 默认 100、默认 backward）；③ detect_anomaly：入参 `{values[], timestamps[]}`（见 G3）；④ query_kb：v1 stub 返回 `{status: "unavailable", meta.reason: "M6 未建库"}`（D-08：RAG 是工具不是架构，注册面完整）；⑤ get_topology：复用 `oncall.context` 三源；⑥ execute_action：stub，PermissionGate 标 **L2 永远拒绝**。工具层落 `oncall/harness/tools/`，HTTP 走 `infra.http.Fetcher`，全部依赖 Protocol 注入 | 领域直查而非原语收敛（架构 §1 已定取舍：运维取证要确定性结果）；oncall.context 不在 C3 禁列 → M1 三源实现零重建；工具返回是 M4/M7/M8 的输入，形状一次到位不再改 | R3 + R4 + R7（Anthropic 工具铁律：少而精、高信号、错误信息指导下一步）+ D-16 + D-08 |
| G3 | detect_anomaly 方法选型与依赖治理：PRD「统计+IsolationForest」是否 v1 双通道齐上、sklearn 是否进 pyproject | **v1 纯统计轻量版**（基线窗口 z-score + 分位数/IQR + 环比突变，stdlib `statistics` 实现，零新依赖）；IsolationForest（sklearn）延后至 M7 前按需评审引入（含 C2 版本区间与 pip-audit 面） | R5 官方文档：IF 无时序感知、小样本下 bootstrap 关闭导致各树同数据、多样性不足——直接喂短时序原始点只能发现极值异常；统计版对 12 剧本规模够用且可解释（证据链叙事友好）；sklearn+numpy/scipy 传递依赖扩大审计面，30s 单工具超时内统计版更可控。PRD 双通道口径拆两阶段兑现，不是砍 | R5 + PRD §7-M3 + 母本模块 B 分层（统计先行、ML 兜底）+ C2 依赖治理 |
| G4 | 记忆/上下文管理与 M3/M4 数据边界：≤2000 tokens 截断 +「落库指针」在 M4 未建表时如何表达；evidence_steps/hypotheses 是 M3 内存持有还是直接建表 | **M3 内存契约、不建表**：`EvidenceStep` / `Hypothesis`（Pydantic frozen，字段对齐架构 §4）挂在唯一可变状态 `InvestigationSession`；上下文窗口只进 `output_summary`（≤2000 tokens 截断），超出部分完整留 session，窗内放 `[truncated, full at step N]` 指针——**M3 指针 = EvidenceStep 对象引用，M4 建表后替换为行 id，接口不变**；步数 ≥10 ContextManager 移出已证伪假设主上下文（落 session 保留）；M4 建 ORM 表时契约不倒改 | 架构 §4 已冻结 M3 产出字段定义（output_json 与 output_summary 两个都要）——冻结契约形状即满足「每步可回溯」；M3 建表会让票面膨胀且 M4 导出/迁移语义未定，先定契约后定存储符合「M3 只定义内存契约与写入接口」的 Non-goal 划线 | 架构 §3.3/§4 + R8（OpenHands 单一状态对象原则，架构 §1 已采）+ D-19（incidents 五字段只消费） |
| G5 | Verifier 形态：纯规则 / LLM-as-verifier 每步 / 规则+LLM 混合 | **规则+LLM 混合**：规则层每步零成本确定性校验（ToolResult 状态、args 合法性、假设-证据步引用存在性、结论引用的工具输出确实存在→可判 hallucination）；LLM 裁决只在**新假设提出**与**收束判定**两个时机调用（≤3 次/调查），证伪导向独立 prompt（生成者与评判者分离）；Verifier 不生成新计划（架构 §3.2） | 每步 LLM-as-verifier = 双倍 LLM 成本且无证据支撑其收益（R6：复杂度必须有可测收益）；纯规则对「证据是否支持假设」的语义判断不足；混合与 ≤¥0.5 口径兼容（测算见风险清单）；架构 §1 已明确「不独立评判 Agent 进主循环、Verifier 轻量版」 | R6 + 架构 §1（Harness Design：生成者/评判者分离）+ §3.2 + D-03 |
| G6 | 防绕圈与假设收敛：重复调用检测、假设去重、证伪假设移出触发、回退语义 | 四机制：① 重复调用检测——同 (tool, args 规范化哈希) 连续 ≥2 次或累计 ≥3 次 → Harness 注入循环警告进下轮 prompt，再犯 → 熔断终止归类 `plan_error`；② 假设去重——新假设与既有假设文本规范化后相似（纯规则匹配）→ 拒绝入池并提示换向；③ 步数 ≥10 移出已证伪假设主上下文（架构 §3.3 定值）；④ Fallback——工具重试耗尽/假设被推翻时，Planner 收到结构化失败摘要（错误信息指导下一步，R7）**自行换向**，Harness 不硬编码换向顺序 | 防失控三闸是 Harness 职责（架构 §1 总原则：模型决定做什么，Harness 决定允不允许）；换向决策留给模型是「真 Agent」判据的一部分，Harness 只供差分信息 | R6 + R7 + 架构 §3.4 + 硬规则 2（规则先行） |
| G7 | 终止语义与失败模式归类：六值判定规则；escalate_to_human 在无 UI 阶段的落点 | 终止三出口：① Planner 输出 `{conclusion}`（正常）；② 步数 = 15 → `escalate_to_human`；③ Harness 熔断（绕圈触发 / 总时长 >5min）。失败模式六值落 `InvestigationResult.failure_mode`：`tool_error`（工具重试耗尽且无可用 Fallback）/ `plan_error`（Planner 畸形重试耗尽或绕圈熔断）/ `timeout`（单步或总时长超限）/ `hallucination`（规则层检出引用不存在的证据步）/ `no_signal`（三源 unavailable 且无假设可证实而收束）/ `premature_stop`（步数 <5 且无 confirmed 假设即 conclusion——M3 预标注，M7 评测复核）。escalate 无 UI 落点：`session.status=escalated` + incident 保持 `investigating` + 已取证证据链经 `GET /investigations/{id}` 可查 | 架构 §6 失败模式归类口径（五类 + premature_stop）必须机械可判，否则 M7 矩阵列无法生成；无 UI 阶段「转人工」的最小完备形态 = 状态 + 可查报告 | 架构 §3.4/§6 + PRD §4 + R8 |
| G8 | 测试策略：Planner 决策序列 mock；防伪 Agent 三判据如何变成可机械断言 | `MockPlanner` 可编程 script 回放（照 M2 `MockLLMClassifier`：PlannerDecision 与异常实例混排、耗尽稳定回落默认）；三判据断言：① **步骤/终止由模型定**——同一 incident 注入两个不同脚本（A=多步查证后收束，B=1 步直接收束）→ 步序列与结论随 Planner 输出改变；② **SOP 只是工具**——构造「全程不调 get_topology 亦正常收束」的脚本路径；③ **自校验+回退+步数上限**——「假设被推翻→换假设」「工具失败重试≤2→Fallback」「第 15 步强制 escalated」三单测 | 设计期 mock-only 拍板口径；「防伪」不能停留在文档声明，要转成行为差异断言（判据 1 的两脚本对照是关键——决策输入相同则输出必须相同，输入不同则允许分叉） | D-03 + agent-loop-design（防伪三判据）+ M2 先例 + 硬规则 12（TDD） |
| G9 | 验收剧本选择与根因判分基准：与 M2 三剧本复用还是错开；判分用 golden 哪个字段 | **3 剧本 = `cpu-spike`（CPU）+ `slow-sql`(慢 SQL) + `queue-backlog`（队列堆积）**——PRD §7-M3 点名三类，与 M2 复用 slow-sql 一个、错开两个；判分基准 = golden **`root_cause`** 字段（D-18 纪律：与 scenario `expected_root_cause` 逐字一致，同源可信）；M3 判定用规则匹配级（关键实体+动作词），语义判分（LLM-as-judge + 人工抽检 20%）归 M7；12 剧本全量矩阵归 M7 | PRD 硬要求三类覆盖；复用 slow-sql 少搭一套 mock 工具夹具、错开两个扩覆盖面；D-18 教训（标注错=评测全错）要求基准字段必须是同源校验过的那个 | PRD §7-M3 + D-18 + D-21 + M2 G9 先例（规则匹配级先行、judge 留 M7） |

### 评审依据（官方标准来源）

> 取用日期均为 2026-09-08。检索时注意区分各家口径（DeepSeek / 百炼 qwen / OpenAI 官方文档对 JSON mode、tool-calling、定价各自表述），引用处已注明。

| # | 来源 | 用于 |
|---|---|---|
| R1 | OpenAI 官方《Structured model outputs》：function calling 与 response_format 的官方选择指引（「连接模型与工具 → function calling；结构化模型对用户的响应 → response_format」）；JSON mode 无 schema 保证、需下游校验：`https://platform.openai.com/docs/guides/structured-outputs` | G1 |
| R2 | 阿里云百炼官方《Function Calling》：支持模型清单**明确含 Qwen3.7-Flash 系列**；`tool_choice`（auto/required/none/强制指定）与 `parallel_tool_calls` 语义：`https://www.alibabacloud.com/help/zh/model-studio/qwen-function-calling`（国内站 `https://help.aliyun.com/zh/model-studio/qwen-function-calling` 同文） | G1（tool-calling 备选可行性与延迟理由） |
| R3 | Prometheus 官方 HTTP API：`/api/v1/query_range`（query/start/end/step 必填、timeout 参数、resultType=matrix、values 矩阵、limit 截断、503=查询超时）：`https://prometheus.io/docs/prometheus/latest/querying/api/` | G2（query_metrics 形状） |
| R4 | Grafana Loki 官方 HTTP API：`/loki/api/v1/query_range`（LogQL 流选择器、limit 默认 100、direction 默认 backward、纳秒/RFC3339 时间）：`https://grafana.com/docs/loki/latest/reference/api/` | G2（search_logs 形状） |
| R5 | scikit-learn 官方 IsolationForest 文档（小样本下 bootstrap 关闭 → 各树同数据多样性不足；无时序感知，直接喂时序点只能发现极值异常；`contamination`/`predict` ±1/`decision_function` 语义）：`https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html` | G3 |
| R6 | Anthropic《Building Effective Agents》：最简模式起步、复杂度必须有可测量收益（M2 R1 同源）：`https://www.anthropic.com/research/building-effective-agents` | G1/G5/G6 |
| R7 | Anthropic《Writing Tools for Agents》：工具设计铁律——少而精 > 多而重叠、返回高信号数据、错误信息要指导下一步而非抛堆栈（架构文档 §1 已引同一来源）：`https://www.anthropic.com/engineering/writing-tools-for-agents` | G2/G6 |
| R8 | 仓内契约（非外部来源）：D-03（自研 ReAct）/ D-05（L3）/ D-08（RAG=工具）/ D-12/13/16/17/19（冻结输入契约）/ D-18（golden 同源）/ D-21（数据纪律）+ 架构 §1/§3/§4/§6 + agent-loop-design（防伪三判据/证据链形状）+ PRD §4/§7-M3 | 全部 G 点「只消费不推翻」的输入 |
| R9 | qwen3.7-flash 单价实测口径（M2 issue 07 实测：输入 0.00024 / 输出 0.00096 元每千 tokens，百炼牌价取价 2026-09-08；每模型 100 万 tokens / 90 天免费额度） | 成本测算（风险清单） |

## 开发计划（任务拆解）

> 节奏：W3（M2 已收尾，可立即派工）；目标 3–4 天（每日 1–2h）。TDD 红绿循环照硬规则 12：会话契约、Planner 协议、ToolResult 形状、失败归类是约定接缝。**本票（设计票）不开工实现**：tracker 建完即止，派工由用户另行下发。
> 就绪态判据（照 M2 先例）：涉及主循环结构、Verifier 形态等架构取舍的 → `ready-for-human`（T5/T6）；验收可机械判定的 → `ready-for-agent`。

**关键里程碑**：

- **M3-A 内核**（T1–T3）：会话契约 + Planner 接缝（mock）+ ToolRegistry 与六工具形状可跑通
- **M3-B 编排**（T4–T6）：上下文管理 + Verifier + 主循环（终止/归类/防绕圈）
- **M3-C 入口与验收**（T7–T8）：调查 API + 3 剧本端到端验证 + 真实实测回填 + 翻 `implemented`

| # | 任务 | 内容 | 验收（机械判定，实测回填） | 依赖 | 就绪态 |
|---|---|---|---|---|---|
| T1 | 调查会话契约与 Planner 接缝 | `InvestigationSession`/`EvidenceStep`/`Hypothesis`（G4 契约）；`PlannerClient` Protocol + `PlannerDecision` 校验 + `MockPlanner`（script 回放）+ 异常族（G1） | pytest 绿：契约序列化 roundtrip、`PlannerDecision` 两分支校验、MockPlanner 三夹具（正常/畸形/超时）按序回放耗尽回落默认 | — | ready-for-agent |
| T2 | ToolRegistry 与权限分级 | Registry（注册/参数校验/30s 超时/重试 ≤2/输出截断 ≤2000 tokens/埋点）+ PermissionGate（L0/L1/L2）+ 六工具注册面；query_kb unavailable stub、execute_action L2 stub | pytest 绿：超时重试计数、截断+指针行为、L2 工具调用被拒且留审计记录、未知工具名拒绝 | T1 | ready-for-agent |
| T3 | 取证工具实现 | query_metrics（query_range 封装）/ search_logs（Loki）/ detect_anomaly（纯统计 v1）/ get_topology（复用 context 三源）的 I/O 形状与降级（unavailable 不抛错） | pytest 绿：每工具 ok/empty/error/unavailable 四态单测；时间锚缺省 `last_fired_at`；断网单测全绿 | T2 | ready-for-agent |
| T4 | ContextManager | 摘要模板固定、系统提示 ≤1500 tokens、工具输出 ≤2000 tokens 截断+指针、步数 ≥10 移出已证伪假设 | pytest 绿：预算边界用例（截断触发/指针可回溯/移出后 session 仍保留原证据） | T1 | ready-for-agent |
| T5 | Verifier（形态按 G5 定案） | 规则层（每步确定性校验，hallucination 判定）+ LLM 裁决接缝（新假设提出/收束判定，≤3 次/调查，证伪导向 prompt，mock） | pytest 绿：规则层各校验正反例；裁决调用次数上限断言；Verifier 不产出计划 | T3 / T4 | **ready-for-human**（Verifier 形态） |
| T6 | 主循环 Loop | 推进 step、终止三出口、失败模式六值归类、escalate_to_human、防绕圈四机制（G6/G7） | pytest 绿：防伪三判据三组单测（G8）；六值 failure_mode 各 ≥1 个触发用例；15 步硬安全阀 | T1–T5 | **ready-for-human**（主循环结构） |
| T7 | 调查入口 API | `POST /investigate`（404 语义/同步返回）+ `GET /investigations/{incident_id}`（进程内报告注册表）；报告 JSON 形状照 agent-loop-design | pytest 绿：endpoints 契约测试；404；escalated 报告可查 | T6 | ready-for-agent |
| T8 | 端到端 3 剧本验证与收尾 | 3 剧本 mock 端到端 → 真实 LLM 实测（**需用户确认 key**，照 M2 issue 07 流程）→ 验收节逐条回填 → 设计文档翻 `implemented` → 新术语入 CONTEXT / D-22+ 落位核对 | 3 剧本 conclusion 与 golden root_cause 规则匹配级命中；步数/时长/成本实测回填；全量 pytest + ruff 绿不回退 | T7 | ready-for-agent（真实调用前确认 key） |

## 风险清单（评审随附，交用户复核后才可派工实现票）

| # | 风险 | 现状与对策 |
|---|---|---|
| 1 | qwen3.7-flash 长 prompt 下 JSON Mode 稳定性 | M2 实测均 prompt ≈1713 tokens 时 21/22 一次通过；M3 每步 prompt 随证据增长（步均估 3–4k tokens），退化风险未知。对策：畸形重试 ≤2 + 失败归类 plan_error 兜底；T8 实测记录每步畸形率，若 >10% 触发 G1 复议（tool-calling 备选已确认官方支持，R2） |
| 2 | 15 步循环下 ≤¥0.5 可行性 | 测算（R9 单价）：15 步 Planner（步均 in ≈3.5k / out ≈200 tokens）累计 ≈52k in + 3k out ≈ ¥0.015；Verifier ≤3 次 ≈ ¥0.003 → **单次调查 ≈¥0.02，余量 20×+**。实测回填后如逼近上限，优先杠杆：压缩步均 prompt（记忆摘要更激进）而非砍步数 |
| 3 | detect_anomaly 依赖治理 | v1 纯 stdlib 统计零新依赖（G3）；sklearn 引入需过 C2 版本区间 + pip-audit 评审，IsolationForest 短时序局限（R5）意味着收益存疑——默认不引入 |
| 4 | 防伪 Agent 断言可测性 | 三判据已转成三组行为差异单测（G8）；判据 1 的「两脚本对照」依赖 PlannerDecision 输入差分路径，实现票 T6 需保证决策输入可注入（ContextManager 输出可替换） |
| 5 | `infra/llm.py` 已 283 行逼近 C6 上限 | 真实 Planner client 无法塞进现有文件；拆分落位（新文件 + 扩 C4/C5 ignore 清单）属 pyproject 变更，实现票内过评审后执行，设计期不动 |
| 6 | A2 prompt 落位 | Planner system prompt / Verifier 证伪 prompt 走模块级模板（照 M2 `classify/llm/prompt.py` 先例），实现票按架构守卫现状落位，禁业务代码内联 >200 字符 prompt |
| 7 | 同步 `POST /investigate` 的 5min 占用 | v1 同步（单进程 dev 语义）；如 uvicorn worker 被长调查阻塞，M7 前再评估后台任务化（不在 M3 范围） |

## 评审后动作（定案时执行，本草案不含）

1. 本文件 G 表增「评审定案」列 + 翻 `reviewed`（回填评审人/日期）
2. `docs/design/decisions.md` 登记 D-22+（逐条过 ADR 三判据自检：难以逆转 / 无上下文会意外 / 真实权衡）
3. `CONTEXT.md` 新术语入表（建议候选：**调查会话 / InvestigationSession**、**证据步 / Evidence Step**、**工具结果 / Tool Result**（含四态 status）、**决策输出 / Planner Decision**，含 `_Avoid_` 列）
4. tracker 就绪态终审：T5/T6 是否因定案转 `ready-for-agent`
5. 架构文档如需回写（如 §4 注明 M3 内存契约），注明「实现票执行」
