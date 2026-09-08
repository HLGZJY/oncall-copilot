---
title: "M4 证据链与过程存储：设计与开发计划"
summary: "把 M3 内存契约（EvidenceStep/Hypothesis/InvestigationSession）落库为 ORM 三表（investigations + evidence_steps + hypotheses）：步进即写 100% 落库、事故-证据一对多、导出 JSON/Markdown 报告；承接 issue 08 两个结构缺口修复（opening 视图 + 工具 schema 可见）与真实实测重跑；G1–G9 开放点评审与 T1–T8 拆票"
source: docs/prd.md §4/§7-M4 + docs/architecture/architecture.md §3.3/§4 + docs/architecture/agent-loop-design.md + docs/design/m3-investigation-loop-design.md（形制模板与 M3/M4 边界定案）+ docs/design/decisions.md D-17/19/22/23/25/28/29 + .scratch/m3-investigation-loop/issues/08-e2e-validation.md（两结构缺口注记）+ 评审标准来源（见「评审依据」R1–R5）
status: reviewed
updated: 2026-09-08
read_when: 评审 M4 方案时；进入 M4 开发前；被问「证据链怎么落库/怎么保证可审计」时
---

# M4 证据链与过程存储：设计与开发计划

## status

`draft`（草案，讨论中）→ `reviewed`（评审通过，可拆票）→ `implemented`（已落地，验收回填）→ `superseded`（被后续设计取代，注明替代文档链接）

- **当前状态**：`reviewed`（2026-09-08 G1–G9 评审定案——用户逐条拍板，全部采纳推荐默认解；定案已登记 `decisions.md` D-30–D-38，新术语已入 `CONTEXT.md`）
  - 上一状态 `draft`（2026-09-08 草案完成，提交 `a9feeef`）；无更早状态
- **评审人 / 评审日期**：用户逐条拍板（G1–G9 全部采纳推荐默认解），2026-09-08；评审依据由 AI 检索官方标准提供（R1–R5，含 URL 与取用日期），用户保留推翻权（推翻须回退 `draft` 并重开对应 issue）
- **关联 issue**：`.scratch/m4-evidence-chain/`（spec.md + issues/01–08，与 T1–T8 一一对应；2026-09-08 拆票完成，T1–T8 全部 `ready-for-agent`）
- **设计期口径**：本票零写码、零建表、零真实调用（LLM 全 mock 口径照 M2/M3 先例）；真实调用留实现票 T8，开工前需用户确认 key（照 M3 issue 08 先例）

## 目标

- **背景 / 触发原因**：M3 8/8 resolved 收官（mock 3/3 命中；真实实测 Top-1 首版 0/3，两个结构缺口已注记 issue 08）。W4 前半 = M4 证据链与过程存储——差异化三支柱的第二根，且 M3 已把落库输入备齐：`InvestigationSession` / `EvidenceStep` / `Hypothesis` 内存契约（D-25 冻结）、收尾结构 `InvestigationResult`（D-28）、进程内报告注册表与 `GET /investigations/{incident_id}` 出口。
- **要解决的问题**（PRD §7-M4 三件事展开）：
  1. **逐步落库**：每步 `{step, thought, tool, input, output, ts, cost}` 100% 落库——M3 步进时证据只存内存（进程重启即失），「可审计」要求落库持久且任意一步可回溯原始工具输出；
  2. **事故-证据一对多模型**：一条 incident 对应一次完整调查的多条证据步与假设，外键锚 `incidents.id`（M2 已建「一」端）；
  3. **导出报告**：一次完整调查可导出 JSON/Markdown 报告（JSON 照 agent-loop-design 数据形状），escalated 报告同经报告出口可查（D-28 转人工不是丢弃）。
- **M3/M4 边界（D-25 定案，逐字承接）**：内存契约不变——`EvidenceStep` / `Hypothesis` / `InvestigationSession` 契约不倒改，M4 建表落库时只把内存对象替换为行 id，`[truncated, full at step N]` 指针从对象引用换成行 id、接口不变。
- **承接 issue 08 两个结构缺口**（注记逐字承接，作为 M4 范围内的修复方案开放点交评审）：
  - **缺口① Planner 视图缺事件锚点**："`harness/planner.py` 接缝 docstring 承诺视图 =「记忆摘要 + 事件锚点（D-25 口径）」，但 `loop._decide_with_retry` 实装 view 仅 `{system_prompt, steps, hypotheses, notices}`——模型开局面盲（两轮结论均直呼「未提供告警关联的服务名称及时间窗口」），开局只能盲选 get_topology。这是硬规 6「可观测数据质量决定 AI 上限」的实证：先补上下文，再谈模型能力。修复建议：view 增 `opening`（D-17 卡片精简投影），注意 C6 行数预算（loop.py 已 297 行）。"
  - **缺口② 工具入参 schema 不可见**："架构 §3.3 承诺「模型可调 tool_help 查详情」的 just-in-time 通道，但 D-23 冻结六工具未含 tool_help，system prompt 只有名称+一句话描述——query_metrics 必填 start/end 全靠盲猜，参数失败后 notice 循环消耗步数。修复建议（二选一，需小评审）：system prompt 附六工具入参 schema 摘要，或补第 7 个 tool_help 工具（改 D-23 面）。"
  - 两缺口修复后**重跑真实实测**（真实调用开工前需用户确认 key）列为实现票 T8。
- **不做的事（Non-goals）**：
  - **不做 M6 报告生成**：时间线美化、处置记录、改进建议、历史报告向量化入库归 M6；本票 Markdown 只做证据链数据的最小版格式导出（G7）
  - **不做 M7 评测 runner**：`eval_runs` 表、Top-1/Top-3 矩阵、LLM-as-judge 归 M7；本票只保证 failure_mode/步数/成本字段落库可取（M7 矩阵列直接来源）
  - **不做 UI 页面**：Vue3 时间线/证据链查看归 M8；`GET /investigations/{id}` JSON 即无 UI 阶段载体
  - **不推翻冻结契约**：D-17/19/22/23/28 只消费不推翻；`incidents` 五字段（D-19）不扩列；`EvidenceStep`/`Hypothesis` 字段名不倒改（D-25）
  - **不做 Alembic 迁移**：dev 单库延续 `create_all`（D-13 先例），Alembic 延至切 MySQL
  - **设计期零真实 LLM 调用**；`datasets/golden/holdout/` 禁读不变

## 本阶段涉及的技术类别

| 技术类别 | 涉及技术 | 本阶段用途 | 开放点 |
|---|---|---|---|
| 数据底座 | SQLite 现库 `oncall.db` + SQLAlchemy DeclarativeBase（M2 先例） | 证据步/假设/调查记录三表落库 | 存储选型与会话级字段落点（G1/G2） |
| ORM 落库接缝 | session→DB 写入接缝（新落 `src/oncall/db/` 或 `harness/`，C3 论证见技术方案） | 步进即写、行 id 替换内存指针 | 写入时机与失败语义（G4） |
| 报告导出 | `build_report` 现形状 + str 模板 Markdown（零新依赖） | JSON/Markdown 双格式导出 | 形状权威与 Markdown 落点（G6/G7） |
| Planner 上下文 | ContextManager 视图组装 + D-17 事件卡片投影 | 缺口① opening 视图修复 | 投影键集与 C6 拆分预案（G8） |
| 工具 schema 可见性 | system prompt 静态模板（A2 落位） | 缺口② 修复 | schema 摘要 vs tool_help 工具（G9） |
| 工程门禁 | pytest TDD / ruff / import-linter C3–C5 / C6 ≤300 行 / A2 | 落库契约、100% 落库断言、回溯断言是 TDD 接缝 | 测试策略沿用 M3 方法（不设 G） |

## 技术方案

**一句话概括**：M3 调查循环的每一步在 `record_step` 的同时把 `EvidenceStep` 写入 `evidence_steps` 表（步进即写、每步一个事务），假设入池/裁决同步写 `hypotheses` 表，调查收尾把会话级字段（终态/结论/failure_mode/步数/tokens/成本合计）写入 `investigations` 表；`GET /investigations/{incident_id}` 从进程内注册表改读库，导出 JSON 报告（形状照 M3 `build_report` 契约）与最小版 Markdown 报告；同票修复 issue 08 两个结构缺口（opening 视图 + 工具 schema 可见）并重跑真实实测。

### 设计的模块

| 模块 | 动作 | 职责 | 目录 | 关联里程碑 |
|---|---|---|---|---|
| ORM 三表 | 新增 | `investigations`（会话级，incident_id 1:1 覆盖语义）/ `evidence_steps`（架构 §4 冻结列）/ `hypotheses`（架构 §4 冻结列）；`create_all` 幂等建表 | `src/oncall/db/models.py`（现 89 行，扩后 ≈210 行 < C6） | M4 → M7 eval_runs 外键衔接 |
| 证据仓库写入接缝 | 新增 | `session.record_step` / `add_hypothesis` / 终态方法与 DB 写在同一点：步进即写（G4）；落库后证据步行 id 回填供指针替换（D-25 接口不变） | `src/oncall/db/evidence_repo.py`（新文件，行数预算 ≈150） | M4 |
| 报告读库与导出 | 改造 | `GET /investigations/{incident_id}` 注册表换读库（D-25 承诺）；JSON 导出照 `build_report` 形状（G6）；Markdown 最小版导出（G7） | `src/oncall/api/investigation.py`（现 164 行）+ `src/oncall/db/views.py`（现 54 行，扩序列化器） | M4 → M6/M8 |
| opening 视图（缺口①） | 改造 | view 增 `opening` 键 = D-17 卡片精简投影（G8）；视图组装抽 `build_decision_view` 落 context_manager，loop.py 瘦身保 C6 | `src/oncall/harness/context_manager.py`（现 157 行）+ `loop.py`（现 297 行） | M4 → M7 实测重跑 |
| 工具 schema 摘要（缺口②） | 改造 | 按 G9 定案：system prompt 附六工具入参 schema 摘要（推荐）或补 tool_help 工具；A2 模板落位 | `src/oncall/harness/context_manager.py` | M4 → M7 实测重跑 |
| 真实实测重跑 | 实现票 | 两缺口修复后 3 剧本真实调用重跑（Top-1 口径复核归 M7），步数/耗时/成本回填 | `tests/integration/test_planner_real_e2e.py` 沿用 | M4 收尾（T8） |

### C3/C6 论证：落库接缝落位与行数预算

1. **落库接缝落 `db` 层不在 C3 禁列**：pyproject C3 契约 `oncall.harness` 禁止 import `oncall.ingest / classify / remediation / knowledge / eval / api`——`oncall.db` 不在禁列，harness 侧经注入接缝调用 `db.evidence_repo` 合法（依赖方向 harness → db 单向，与 api → classify → db 同构）；API 层换读库走既有 `db` import，无新依赖边。
2. **loop.py 297 行贴 C6 上限**：缺口① 组装逻辑**不放 loop.py**——视图组装整体抽到 `context_manager.build_decision_view(session, notices, opening)`（context_manager 157 行，预算 +40 行内），loop.py 净减（view 组装 5 行换 1 行调用），C6 达标即拆分预案（不另拆文件）。
3. **models.py 89 行扩三表后 ≈210 行**，单文件可容纳；若评审要求拆 `models_investigation.py`，import-linter 面同步登记（实现票内过评审后执行）。

### 数据模型变更

| 变更项 | 类型 | 说明 | 迁移方式 |
|---|---|---|---|
| `investigations` | **新增表（第七表，超架构 §4 六表清单，须评审 + 回写架构）** | 会话级一行：`id, incident_id(FK), status(running/concluded/escalated/aborted), stop_reason, conclusion, failure_mode, step_count, total_tokens, total_cost_cny, started_at, finished_at`；`incident_id` 唯一约束 = 同 incident 重复调查覆盖旧行（对齐现注册表覆盖语义，D-25「M4 落库后换读表」）；历史多次调查归 M7 `eval_runs` 另表 | `create_all`（dev），Alembic 延至 MySQL |
| `evidence_steps` | 新增表（架构 §4 冻结列照抄） | `id, incident_id(FK), step_no, thought, tool, input_json, output_json, output_summary, tokens, cost, latency_ms, ts`——`output_json` 存原始输出（可回溯）与 `output_summary` 双存（Anthropic：「不能只存摘要」）；input 侧口径见 G3 | 同上 |
| `hypotheses` | 新增表（架构 §4 冻结列照抄） | `id, incident_id(FK), text, status(confirmed/rejected/active), supporting_steps[], against_steps[]` | 同上 |
| `EvidenceStep` / `Hypothesis` / `InvestigationSession` 内存契约 | **无变更**（D-25：契约不倒改） | 内存对象 → 行：落库后行 id 替换指针，接口不变 | — |
| `incidents` | **无变更**（D-19 五字段冻结） | `incidents.id` 即事故-证据一对多的「一」端，三表 FK 锚定 | — |

### API 变更

| API | 变更类型 | 请求/响应要点 | 影响调用方 |
|---|---|---|---|
| `GET /investigations/{incident_id}` | 改造 | 进程内注册表 → **读库**（D-25 承诺）；响应 JSON 形状照 `build_report` 现契约（键集合契约测试已精确守卫），G6 定形状权威；无调查记录 → 404 | M7 runner / M8 时间线 |
| `GET /investigations/{incident_id}/report.md` | 新增 | Markdown 最小版导出（G7）：`text/markdown`，str 模板拼接 steps/hypotheses/conclusion/termination/failure_mode，零新依赖 | M6 报告生成接手前的验收载体 |
| `POST /investigate` | 语义增强（形状不变） | 落库后收尾即持久——escalated/aborted 的已取证部分同落库可查（D-28）；响应仍为报告 JSON | 不变 |

## 验收标准

> 可实测、可判定；实测后回填打勾，不得虚构（实现票 T8 完成时回填本节）。设计期全 mock，成本口径引用 M3 实测值（真实轮 ≈¥0.008/6 次、步数 2–5，来源 M3 issue 08 注记 2026-09-08）。

- [ ] **一次完整调查 100% 落库**（PRD §4 过程可信口径）：mock 3 剧本（`cpu-spike` / `slow-sql` / `queue-backlog`，D-29 沿用）经 `POST /investigate` 走至 conclusion 后，`evidence_steps` 行数 = 会话步数、`hypotheses` 行数 = 假设池大小、`investigations` 一行且终态一致——单测机械断言
- [ ] **任意一步可回溯原始工具输出**：对落库后任一 `evidence_steps` 行，`output_json` 与该步 `ToolResult`（status/data/meta）逐字段一致；escalated/aborted 会话的已取证部分同样可查（D-28）——单测机械断言
- [ ] **导出 JSON 与 agent-loop-design 数据形状比对一致**（按 G6 定案口径）：键集合契约测试精确守卫（照 D-17 `test_alert_card_api.py` 先例）
- [ ] **Markdown 导出可用**：`report.md` 返回 200 + `text/markdown`，包含全部步/假设/结论/终态——契约测试
- [ ] **M3/M4 边界不破**：`EvidenceStep` / `Hypothesis` / `InvestigationSession` 契约字段与 M3 一致（import + 键集合断言），指针接口不变（D-25）
- [ ] **缺口① opening 视图**：`_decide_with_retry` view 含 `opening` 键（D-17 精简投影，键集合断言）；context_manager 组装 + loop.py 瘦身后 C6 断言全绿
- [ ] **缺口② schema 可见性**（按 G9 定案）：system prompt 含六工具入参 schema 摘要（或 tool_help 工具注册）；系统提示 ≤1500 tokens 预算断言不破
- [ ] **重跑真实实测**（T8，需用户确认 key）：3 剧本真实调用回填步数/耗时/成本/结论，Top-1 结果如实记录（口径复核归 M7）；参数级失败率对比 M3 基线记录
- [ ] **全量门禁只增不减**：pytest + ruff check + ruff format --check 绿，coverage ≥80%（基线 **479 passed / 7 skipped / 98.05%**，随新增测试自然上涨）

## 依赖

- **前置依赖**：M3 8/8 resolved ✅（`InvestigationSession`/`EvidenceStep`/`Hypothesis` 契约 + `InvestigationResult` 收尾结构 + 报告 API + D-17 卡片）；M2 `incidents` 表（一对多「一」端）；D-25（M3/M4 边界）/ D-28（终止语义与 escalate 落点）只消费不推翻
- **数据 / 环境依赖**：SQLite dev 库 `oncall.db`；`datasets/golden/dev/`（mock 3 剧本夹具，**holdout/ 禁读**）；LLM API key（仅 T8 真实调用前向用户确认）
- **后续影响**：`evidence_steps`/`investigations` 落库数据是 M7 `eval_runs` 矩阵列（failure_mode/步数/成本）与 M6 报告生成、M8 时间线的直接输入；`GET /investigations` 读库后进程内注册表可退役；两缺口修复是 M7 模型矩阵评测的前置（上下文质量决定 AI 上限，硬规 6）

## 开放设计点（评审 grill）

> 以下 G1–G9 已于 **2026-09-08 全部评审定案**：用户逐条拍板，**全部采纳推荐默认解**（定案结论 = 各行「推荐默认解」列，不再另设定案列）；标准来源见各行「依据」及节末「评审依据」R1–R5。定案已登记 `decisions.md` **D-30–D-38**（G1→D-30 / G2→D-31 / G3→D-32 / G4→D-33 / G5→D-34 / G6→D-35 / G7→D-36 / G8→D-37 / G9→D-38）。若被推翻需回退 `draft` 并重开对应 issue。

| # | 开放点 | 推荐默认解 | 理由 | 依据 |
|---|---|---|---|---|
| G1 | 落库存储选型：现库 `oncall.db` 加表 vs 新建独立证据库 | **现库加表**：SQLAlchemy `Base`（M2 先例）声明三表，`create_all` 幂等建表，Alembic 延至切 MySQL（D-13 先例） | `incidents.id` 外键即一对多「一」端——同库才能单事务保证证据步与事件状态一致（R1：SQLite ACID，单机崩溃/断电不丢已提交事务）；新库跨库无事务边界，join/一致性成本白付；15 步规模无性能分库动机 | R1 + R2 + D-13 |
| G2 | 表模型：架构 §4 只冻结 `evidence_steps`/`hypotheses` 两表，会话级字段（终态/结论/failure_mode/步数/成本合计）落哪 | **新增 `investigations` 表（第七表）**：incident_id 1:1 + 唯一约束（重复调查覆盖旧行，对齐现注册表覆盖语义）；随本票回写架构 §4 六表 → 七表清单 | D-19 冻结 `incidents` 五字段不扩列；从证据表推导会话级字段（stop_reason/conclusion/failure_mode）无处安放且推导是伪权威；M7 `eval_runs` 是「每剧本每轮一行」的评测行，与「每次调查一行」语义不同，不混表；**超出架构 §4 六表规划是显式偏差，须评审确认后回写**（M3 评审后动作已预告「M4 落库时再回写 §4」） | 架构 §4 + D-19 + D-25 + D-28 |
| G3 | 双存回溯口径：PRD §7-M4 写 `input_summary`，架构 §4 冻结列只有 `input_json`——input 侧是否设摘要列 | **input 只落 `input_json` 全文，不设 `input_summary` 列**：output 侧 `output_json`/`output_summary` 双存照冻结不动；PRD 的 `input_summary` 口径由 `input_json` 等价承接（设计文档写映射说明，PRD 总纲不改） | Planner args 本身是小对象（几十 tokens 级），对 input 再摘要无收益反添一处可漂移的冗余；「可回溯」要求 input 全文落库（R2：审计记录 what 字段记原始请求对象）；架构 §4 冻结列「两个都要」约束的是 output 侧——以冻结契约为准，消除 PRD 措辞与冻结列的歧义 | R2 + 架构 §4 + D-25 |
| G4 | 写入时机：步进即写（每 `record_step` 同事务 INSERT）vs 收尾批量写 | **步进即写**：`record_step` / `add_hypothesis` / 终态方法与 DB 写在同一点，每步一个事务；落库异常不静默吞——按 Harness 熔断语义冒泡归类 `tool_error` | 「100% 落库」口径只有步进即写能成立：批量写在崩溃/熔断时丢已取证步，恰是 escalate（转人工不是丢弃，D-28）最需要可查的时刻；R1：SQLite 单步事务在 15 步规模下开销可忽略（单机毫秒级）；写失败静默 = 证据链假绿，违反可审计底线 | R1 + R2 + D-28 |
| G5 | 成本字段来源：`EvidenceStep.cost_cny` 现恒 0（工具执行无 LLM 消耗），真实成本在 Planner/Verifier 的 `LLMUsage`——步级摊销还是会话级汇总 | **会话级汇总落 `investigations` 表**（`total_tokens`/`total_cost_cny`，来源 = Planner `usage_log` + Verifier 裁决 usage 汇总，即 D-28 收尾结构 total 两字段）；步级 `tokens`/`cost_cny` 维持现语义（工具埋点），不做步级摊销 | Verifier 裁决跨步归属模糊，摊进步是伪精度；D-28 收尾结构已有会话级 total——落库即取，零新增口径；M7 `eval_runs` 的 cost/steps 列直接读会话级 | D-28 + D-22（Planner usage 口径）+ issue 08 usage 明细先例 |
| G6 | 导出 JSON 形状权威：agent-loop-design §证据链数据形状示例键名（`step`/`input`/`cost`/`supporting`/`against`）与 M3 已实现契约（`step_no`/`input_json`/`cost_cny`/`supporting_steps`/`against_steps`）不一致——以哪个为准 | **以 M3 `build_report` 现形状为准**（键集合契约测试已精确守卫），**同步修订 agent-loop-design 示例对齐冻结契约**；不做导出层键名映射 | 键名一次到位不再改（D-17 先例）；架构 §4 冻结列名才是权威，文档示例是示意——修文档消除双权威（M0 教训：双权威必漂移）；映射层 = 每个消费方复制一份键名翻译，长期维护负资产 | 架构 §4 + D-25 + D-17（键集合守卫先例） |
| G7 | Markdown 导出落点：本票最小版 vs 留 M6 报告生成 | **本票最小版**：str 模板拼接（零新依赖），端点 `GET /investigations/{incident_id}/report.md` 返回 `text/markdown`；内容 = 证据链数据直出（步表/假设表/结论/终态/failure_mode），时间线美化与改进建议归 M6 | PRD §7-M4 验收硬口径「可导出 JSON/Markdown」属 M4 范围；M6 是内容生成（时间线/处置/建议），格式导出与内容美化分离——最小版是 M6 的输入而非重复；R4：最简模式起步，复杂度必须有可测收益 | PRD §7-M4/§4 + R4 |
| G8 | 缺口① 修复方案：opening 投影键集与 C6 拆分预案 | **view 增 `opening` 键** = D-17 卡片精简投影：`{alertname, instance, job, severity, source, status, fired_at, last_fired_at}`（labels 全量，小对象直用）+ 三源 `context` 的 `status` 摘要（ok/unavailable 标记，不含 items 全文）；组装抽 `context_manager.build_decision_view(session, notices, opening)`，loop.py 瘦身 | 逐字承接 issue 08 注记①（「模型开局面盲……先补上下文」）；投影而非全卡片：卡片 `context.items` 全文进 view 会与 steps 摘要重复、挤 token 预算——只补「服务名 + 时间窗 + 源可用性」这类开局锚点信息（实测结论直呼缺的正是这些）；C6：组装移出 loop.py（297 行）后净减，无需拆文件 | issue 08 注记① + D-16/D-17 + planner.py 接缝 docstring（D-25 口径） |
| G9 | 缺口② 修复方案：system prompt 附六工具入参 schema 摘要（A）vs 补第 7 个 tool_help 工具（B，改 D-23 冻结面） | **A：system prompt 附 schema 摘要**——静态模板（A2 落位），每工具 2–3 行（工具名 + 必填/可选参数 + 约束值域），预算 +≈250 tokens，≤1500 tokens 预算断言随票钉死；选 A 即架构 §3.3「tool_help 通道」按意图由 schema 摘要兑现，架构措辞随票回写注记 | R3：工具可用性的第一现场是描述与参数说明——预防（看得见 schema）优于事后查询（tool_help 还要消耗一步调用）；B 改 D-23 冻结面（六工具是 M3 定案契约）且步数是稀缺资源（≤15）；实测主失败是参数级失败——schema 摘要直击该失败模式；A 无运行时成本、B 有 | issue 08 注记② + R3 + D-23 + D-22（A2 模板先例） |

### 评审依据（官方标准来源）

> 取用日期均为 2026-09-08。仓内契约（架构 §4、agent-loop-design、PRD、D-17/19/22/23/25/28/29、issue 08 注记、CONTEXT.md）是全部 G 点「只消费不推翻」的输入；外部来源仅两处方法论引用 + 一处存储语义。

| # | 来源 | 用于 |
|---|---|---|
| R1 | SQLite 官方《SQLite is Transactional》：ACID 语义——单库事务在程序崩溃/系统崩溃/断电下「要么全发生要么不发生」：`https://sqlite.org/transactional.html` | G1（同库事务一致性）/ G4（步进即写的持久性依据） |
| R2 | OWASP《Logging Cheat Sheet》：审计记录字段口径（when/where/who/what + 原始请求对象留痕）与 audit trail 完整性要求：`https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html` | G3（input 全文落库）/ G4（100% 落库 = 审计底线） |
| R3 | Anthropic《Writing Tools for Agents》：工具描述与参数说明是模型正确调用的第一现场、错误信息要指导下一步：`https://www.anthropic.com/engineering/writing-tools-for-agents` | G8/G9（schema 可见性） |
| R4 | Anthropic《Building Effective Agents》：最简模式起步、复杂度必须有可测量收益（M2/M3 R6 同源）：`https://www.anthropic.com/research/building-effective-agents` | G7（最小版 Markdown）/ G5（不做步级摊销） |
| R5 | 仓内契约（非外部来源）：PRD §4/§7-M4 + 架构 §3.3/§4 + agent-loop-design §证据链数据形状 + decisions.md D-17/19/22/23/25/28/29 + issue 08 注记（两结构缺口逐字承接）+ CONTEXT.md 术语 | 全部 G 点 |

## 开发计划（任务拆解）

> 节奏：W4 前半；目标 2–3 天（每日 1–2h）。TDD 红绿循环照硬规则 12：三表 ORM 契约、步进即写接缝、报告键集合、opening 投影键集是约定接缝。
> 就绪态判据（照 M3 先例）：**G1–G9 已于 2026-09-08 全部定案，T1–T8 转 `ready-for-agent`**（T8 附 key 门槛：真实调用前需用户确认，照 M3 issue 08 流程）。拆票建 `.scratch/m4-evidence-chain/` 归评审后动作（见节末）。

**关键里程碑**：

- **M4-A 落库内核**（T1–T2）：三表 ORM + 步进即写接缝（行 id 替换指针）
- **M4-B 导出与出口**（T3–T4）：报告读库 + JSON 形状定案 + Markdown 最小版
- **M4-C 缺口修复与验收**（T5–T8）：opening 视图 + schema 摘要 + 验收断言 + 真实实测重跑收尾

| # | 任务 | 内容 | 验收（机械判定，实测回填） | 依赖 | 就绪态 |
|---|---|---|---|---|---|
| T1 | ORM 三表 | `investigations`/`evidence_steps`/`hypotheses`（G1/G2/G3 定案形状）；`create_all` 幂等；CHECK 约束照 M2 先例（status 枚举落 DB 层） | pytest 绿：三表建表幂等、FK/唯一约束生效、冻结列字段与架构 §4 逐字段一致断言 | G1–G3 | ready-for-agent |
| T2 | 证据仓库写入接缝 | `db/evidence_repo.py`：`record_step`/`add_hypothesis`/终态与 DB 写同点（G4 步进即写）；落库后行 id 回填；落库异常冒泡归类 `tool_error`；C3 论证落位 | pytest 绿：步进即写逐行断言、escalated/aborted 已取证部分完整、行 id 指针接口不变（D-25）、写失败熔断路径 | T1 | ready-for-agent |
| T3 | 报告读库与 JSON 定案 | `GET /investigations/{incident_id}` 注册表换读库；JSON 形状按 G6 定案（含 agent-loop-design 修订回写）；`investigations` 会话级字段入报告 | pytest 绿：报告键集合精确守卫、404 语义（无记录）、escalated 报告读库可查、重复调查覆盖旧行 | T2 | ready-for-agent |
| T4 | Markdown 最小版导出 | `GET /investigations/{incident_id}/report.md`（G7）；str 模板零新依赖 | pytest 绿：200 + `text/markdown`、内容含全部步/假设/结论/终态 | T3 | ready-for-agent |
| T5 | 缺口① opening 视图 | `context_manager.build_decision_view`（G8 投影键集）+ loop.py 瘦身；C6 断言 | pytest 绿：view 含 `opening` 键集合断言、loop.py ≤300 行、既有 mock e2e 3/3 不回退 | G8 | ready-for-agent |
| T6 | 缺口② schema 可见性 | 按 G9 定案：system prompt 附六工具 schema 摘要（A2 模板落位）或 tool_help 工具 | pytest 绿：schema 摘要存在断言、系统提示 ≤1500 tokens、（选 B 时）第 7 工具注册与 D-23 面变更登记 | G9 | ready-for-agent |
| T7 | 验收断言与门禁 | 「验收标准」节逐条转机械断言（100% 落库/回溯/JSON 比对/边界不破）；架构守卫（C3/C4/C5/C6/A2）全绿 | pytest 绿：验收节断言全绿；全量门禁基线 479/7/98.05% 只增不减 | T1–T6 | ready-for-agent |
| T8 | 真实实测重跑与收尾 | 两缺口修复后 3 剧本真实调用重跑（**开工前需用户确认 key**，照 M3 issue 08 流程）；步数/耗时/成本/Top-1 如实回填 → 设计文档翻 `implemented`；新术语入 CONTEXT；D-30+ 落位核对 | 实测回填完整（禁虚构）；参数级失败率对比 M3 基线记录；全量 pytest + ruff 双检绿 | T5/T6/T7 | ready-for-agent（真实调用前确认 key） |

## 风险清单（评审随附，交用户复核后才可派工实现票）

| # | 风险 | 现状与对策 |
|---|---|---|
| 1 | system prompt 预算：schema 摘要 +≈250 tokens 挤占 ≤1500 上限 | 当前模板余量待实测（T6 预算断言钉死）；超限时先压缩一句话描述再考虑挪 schema 进 opening（不砍 schema 本身——它是缺口②的主修复） |
| 2 | C6 行数：loop.py 297 行贴上限 | 组装移出（G8 拆分预案）后净减；若 T5 实测仍超限，预案 B = view 组装独立 `harness/view.py`（import-linter 同包无新边） |
| 3 | SQLite JSON 列查询性能 | 沿 D-19 先例：11 剧本规模不预优化，`output_json` 不建索引；实测成为瓶颈再议（M7 规模评估时复核） |
| 4 | 重复调查覆盖 vs 历史保留 | 推荐覆盖语义（G2）对齐现注册表；M7 起多轮评测数据落 `eval_runs` 另表——若评审要求保留历史，改 `investigations` 去唯一约束 + `GET` 取最新行，票面不变 |
| 5 | 落库异常语义 | G4 定案：写失败不静默吞，熔断归类 `tool_error`——SQLite 本地写失败概率极低，但静默 = 证据链假绿，宁可 fail fast |
| 6 | agent-loop-design 修订 = 文档权威变更 | G6 定案含文档回写（示例键名对齐冻结契约），属消除双权威的修正而非契约变更——回写 diff 随 T3 提交，交用户复核 |
| 7 | 真实实测成本 | M3 实测口径：两轮 6 次合计 ≈¥0.008（单次最高 ¥0.004，R9 单价，来源 issue 08 注记 2026-09-08）；重跑同规模成本可忽略，key 门槛照旧 |

## 评审后动作（2026-09-08 定案当日已执行）

1. ✅ G 表定案说明回填 + 本文件翻 `reviewed`（评审人/日期已填）
2. ✅ `docs/design/decisions.md` 登记 **D-30–D-38**（逐条过 ADR 三判据自检：难以逆转 / 无上下文会意外 / 真实权衡；**注意表格行格式**照现有表续行）
3. ✅ `CONTEXT.md` 新术语入表（调查记录 / 证据仓库，含 `_Avoid_`）
4. ✅ `.scratch/m4-evidence-chain/` 拆票完成（spec.md + issues/01–08 与 T1–T8 一一对应；T1–T8 全部 `ready-for-agent`，T8 附 key 门槛；标签约定照 issue-tracker.md）
5. ⬜ 架构文档回写随实现票执行：§4 六表 → 七表（G2/D-31）；§3.3 tool_help 措辞按意图兑现注记（G9/D-38）；agent-loop-design 示例键名修订（G6/D-35，随 T3 提交交用户复核）
6. ✅ `docs/README.md` 索引行已随草案新增（M4 设计文档条目）
