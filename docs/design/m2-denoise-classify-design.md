---
title: "M2 降噪分类：设计与开发计划"
summary: "消费 status=deduped 告警 → 规则通道 + LLM 通道双通道三态分类（误报/风险/真实事件）→ incidents 建档 → 降噪统计口径与 3 剧本验收；T1–T7 任务拆解"
source: docs/prd.md §4/§7-M2 + docs/architecture/architecture.md §2/§4/§5/§6 + docs/design/m1-alert-ingestion-design.md + docs/design/decisions.md D-07/D-13~D-18 + docs/reference/_sources/项目信息.md §3.3 模块 A + 评审标准来源（见 §7 末「评审依据」）
status: reviewed
updated: 2026-09-07
read_when: 评审 M2 方案时；进入 M2 开发前；被问「M2 降噪分类怎么做」时
---

# M2 降噪分类：设计与开发计划

## status

`draft`（草案，讨论中）→ `reviewed`（评审通过，可开工）→ `implemented`（已落地，实测数据已回填）→ `superseded`（被后续设计取代，注明替代文档链接）

- **当前状态**：`reviewed`（2026-09-07 G1–G9 评审定案，依据官方标准来源，见 §7 末「评审依据」R1–R8；D-19/D-20/D-21 已入 `decisions.md`；新术语已入 `CONTEXT.md`。如用户对任一定案有异议，回退 `draft` 重开对应 G 点）
- **评审人 / 评审日期**：用户授权 AI 执行评审（检索官方标准逐条比对），2026-09-07；用户保留推翻权
- **关联 issue**：`.scratch/m2-noise-reduction/`（spec.md + issues/01–07，与 T1–T7 一一对应）

### 是否已正式进入 agent 阶段的状态说明

**结论：评审已通过（2026-09-07），tracker 已建立**。issue 01–05、07 验收可机械判定，标 `ready-for-agent`；issue 06（误报剧本入 golden/dev + golden 标注字段扩展，涉及 D-12/D-18 契约面）涉架构取舍，留 `ready-for-human`。

**进入 agent 阶段的条件**（评审时逐条核对）：

| # | 条件 | 当前状态 |
|---|---|---|
| ① | M1 六票全部 `resolved`：`alert_events`（D-13/D-14/D-15）+ 事件卡片（D-17）+ 三源上下文（D-16）已交付，receiver 已切 oncall 终态档 | ✅ 已满足 |
| ② | 本文件 §7 开放设计点经评审 grill 拍板 | ✅ 已满足（G1–G9 定案，见「评审落定决策」） |
| ③ | 数据底座：`datasets/golden/dev/` 11 剧本 + holdout 隔离纪律成立 | ✅ 已满足（M2 全程只碰 dev/，holdout/ 禁看） |
| ④ | LLM API key：**设计会话口径 = 全部 mock/估算法**（用户 2026-09-07 拍板），真实调用与成本实测留到实现票（issue 07），开工前需用户确认 key | 🟡 不阻塞 01–06；仅 issue 07 真实调用前需确认 |
| ⑤ | `.scratch/m2-noise-reduction/` spec + issues 建立、就绪态标注完成 | ✅ 已满足 |

## 目标

- **背景 / 触发原因**：W2 排期 = M1+M2；M1 已交付干净分母（`alert_events.status=deduped`、`dedup_count`、0 漏收实测），M2 在其上叠 加「分类层」，补齐验收硬口径「降噪 ≥80% 且 0 漏报」中 M1 未覆盖的部分。
- **要解决的问题**：
  1. **三态分类**：消费去重后的告警，经「规则通道（确定性预筛）+ LLM 通道（few-shot 兜底）」判为 误报 → 归档 / 风险 → 建档观察（不丢弃，D-07）/ 真实事件 → 建 incidents 档（架构 §5 时序「真实事件建档 → M3」）；
  2. **降噪可核算**：定义降噪率与漏报的统计口径（含 D-14 时间窗桶边界的归并规则），让「≥80% 且 0 漏报」可实测、不 flaky；
  3. **误报可验证**：剧本集内构造 1 类「历史上误报的告警」（规则误触发），3 剧本验证含 1 误报被正确识别（PRD §7-M2 验收）。
- **不做的事（Non-goals）**：
  - **不做独立抑制层（层 2）与聚合层（层 3）**（G1 定案）：四层递进中第 1 层去重已由 M1 完成，M2 只做第 4 层分类 + 统计层归并；PRD §7-M2 只点名「规则预筛 + LLM 分类」双通道
  - 不做跨告警聚合建事件（同一故障多类告警 1:1 建多条 incident，归并优化留 M3 调查入口再议，G5）
  - 不做调查循环（M3）、不做处置（M5）、不做 UI（M8）、不做知识库（M6）
  - **零写操作**：M2 无任何处置动作（那是 M5 四道闸门的事）
  - 不做告警源扩展（Prometheus/日志规则接入仍是 M1 同款单入口）
  - 不做 LLM-as-judge 与人工抽检设施（M7 评测台职责，M2 只预留分类正确性的机械比对）
  - 设计期不做真实 LLM 调用（mock/估算，实测留实现票）

## 本阶段涉及的技术类别

| 技术类别 | 涉及技术 | 本阶段用途 | 开放点 |
|---|---|---|---|
| LLM API | DeepSeek-chat / Qwen-plus（OpenAI-compatible，PRD §6 选型）、JSON mode | LLM 通道 few-shot 分类；设计期 mock client + 成本估算法 | 结构化输出契约、置信度阈值（G3） |
| 规则预筛 | 纯 Python 谓词注册表 | 规则通道：确定性误报模式判定，规则先行 | 规则来源与形态（G2） |
| 持久化 | SQLAlchemy 2.0、SQLite（JSON1） | `alert_events` 增列 + `incidents` 建表 | 落库形态（G4/G5） |
| 数据底座 | `datasets/golden/dev/`（11 剧本）、chaos/scenarios | few-shot 样本源、3 剧本验证、误报剧本 | 误报样本来源（G7） |
| 工程门禁 | pytest TDD / ruff / coverage | 分类谓词、统计归并、输出契约校验是 TDD 接缝 | — |

## 技术方案

**一句话概括**：对 `alert_events.status=deduped` 的告警行按「规则谓词注册表先行 → LLM few-shot 兜底 → 置信度阈值派生风险」三级判定，结果落 `alert_events.classification_json`（status 翻 classified），真实事件按 1:1 建 `incidents` 最小档；统计层按「逻辑告警」归并 D-14 桶边界分行后核算降噪率与漏报，`POST /classify` 独立入口驱动（ingest 主链路保持零 LLM）。

### 设计的模块

| 模块 | 动作 | 职责 | 目录 | 关联里程碑 |
|---|---|---|---|---|
| 分类领域模型与接缝 | 新增 | 三态 verdict 枚举、分类结论结构、LLM client 接缝（Mock 实现先行，真实 client 实现票接） | `src/oncall/classify/` | M2 → M7 mock 复用 |
| 规则通道 | 新增 | Python 谓词注册表（G2）：每条规则 = 名称 + 谓词 + 判定；命中误报模式 → false_positive 直判；未决 → 交 LLM | `src/oncall/classify/rules/` | M2 → 后续剧本扩规则 |
| LLM 通道 | 新增 | few-shot prompt 组装（只取 dev 集）+ JSON mode 结构化输出校验 + 超时/重试/失败落风险 | `src/oncall/classify/llm/` | M2 → M7 模型矩阵 |
| 分类服务与落库 | 新增 | 双通道编排（G4）：判定 → `classification_json` 落库 + status 翻 classified；真实 → incidents 1:1 建档（G5） | `src/oncall/classify/` + `src/oncall/db/` | M2 → M3/M8 消费 |
| 统计口径 | 新增 | 逻辑告警归并（G6）+ 降噪率/漏报核算 + 分类报告输出 | `src/oncall/classify/stats.py` | M2 → M7 评测台 |
| 分类入口 API | 新增 | `POST /classify`（按 id/批量/全部）；`GET /alerts` 扩 verdict 过滤 | `src/oncall/api/` | M2 → M7 runner 驱动 |

> 目录为建议布局，正式落名在 issue 派工时定；`src/oncall` 只放产品代码（D-12）。

### 数据模型变更

| 变更项 | 类型 | 说明 | 迁移方式 |
|---|---|---|---|
| `alert_events.classification_json` | 新增增列 | 分类结论全量审计：`{verdict, confidence, reason, channel(rule/llm/llm_error), model, tokens, cost_cny, classified_at}`；verdict 三态 `false_positive / risk / incident`；未分类行为 NULL | **走 D-13 同款增列评审流程**，登记 D-19 并回写架构文档 §4；SQLite `create_all`（dev），Alembic 延至切 MySQL |
| `alert_events.status` | 语义消费（不扩枚举） | 架构 §4 已冻结 `deduped → classified` 两值；三态出口存在 `classification_json.verdict`，status 只表达「是否已分类」 | 无迁移 |
| `incidents` 表 | 新增建表 | M2 最小集（G5）：`id / alert_ids(JSON 数组) / severity / status(investigating) / created_at`；对齐架构 §4 六表规划，M3 以 `incident_id` 为调查入口 | SQLAlchemy model + SQLite `create_all` |
| golden 标注扩展 | 数据文件扩展（非 schema 破坏） | `alert_timeline` 条目增**可选**字段 `classification`（`incident`（缺省）/`false_positive`）；dev 集新增 1 个误报类剧本 | D-18「三字段逐字一致」纪律不变；扩展走 issue 06（`ready-for-human`），holdout 同步延至 M7 前 |

> ⚠️ `classification_json` 增列一旦被 M3+（调查入口）/M8（误报/风险 tab）引用即难改名——评审重点过 G4/G5。

### API 变更

| API | 变更类型 | 请求/响应要点 | 影响调用方 |
|---|---|---|---|
| `POST /classify` | 新增 | 入参：`{alert_id}` 或 `{batch: "all"\|"pending"}`；对 `status=deduped` 行执行双通道分类；响应 `{classified, false_positive, risk, incident, llm_calls}`；**独立入口、不串联进 `/ingest`**（ingest 主链路保持零 LLM、0 漏收不被外部依赖拖垮，G4） | M2 统计/M7 runner 驱动；无 UI 阶段 curl |
| `GET /alerts` | 修改 | 增 `verdict` 过滤参数（json_extract 查 `classification_json`，R7） | M8 误报归档/风险观察两个 tab 的数据源 |
| `GET /incidents`（列表） | 新增 | 列出 M2 建档的真实事件（M3 调查入口查询用） | M3 Harness |

## 验收标准

> 可实测、可判定；实测后回填打勾，不得虚构（实现票完成时回填本节）。降噪率与漏报按 G6 定案口径核算。

- [ ] 3 剧本验证分类正确（PRD §7-M2）：`slow-sql`（基础设施）+ `protocol-mismatch`（业务语义层）+ 新误报剧本（G7）各注入 1 轮 → 全部逻辑告警的 verdict 与 golden 标注一致（含「历史上误报的告警」被识别为误报），0 漏报
- [ ] 降噪率按 D-20 口径核算并回填实测值：`降噪率 = (R − I) / R`，R = 有效 firing 投递数（Σ `dedup_count`，D-15 口径），I = 判为 incident 的逻辑告警数；误报剧本贡献归档量
- [ ] 漏报 = 0：golden 标注 incident 的逻辑告警中，被判 `false_positive` 的数量为 0（`risk` 不算漏报但单列报告）
- [ ] D-14 桶边界不 flaky：跨两个 10m 桶的同源连发（M1 实录行为）在统计层归并为 1 个逻辑告警；「3 连发合并 1 条」类口径验收稳定通过
- [ ] 规则先行可证：规则可判定的行 0 次 LLM 调用（响应计数 `llm_calls` 可证）；规则未决行全部进入 LLM 通道或落风险
- [ ] LLM 结构化输出契约：JSON mode + Pydantic 校验；畸形输出重试 ≤2 次 → 仍失败落 `risk`（channel=llm_error）——0 漏报兜底（D-07）
- [ ] incidents 建档：真实告警 → `incidents` 行字段齐全（G5 最小集），`GET /incidents` 可查，M3 可消费
- [ ] LLM 成本实测回填（issue 07，真实调用需用户确认 key）：单次分类成本 ≤ 上限（G8），按 `cost = in_tokens × 单价 + out_tokens × 单价` 核算并回填实测值
- [ ] 单元测试覆盖约定接缝：谓词规则、统计归并（桶边界）、输出契约校验、风险派生阈值；全量 pytest 通过（coverage ≥80%，只算 `src/oncall`）

## 依赖

- **前置依赖**：M1 六票全部 `resolved` ✅（`alert_events` D-13/D-14/D-15 + 事件卡片 D-17 + 三源上下文 D-16）；D-07（不确定落风险）、D-18（golden 同源纪律）只消费不推翻；CONTEXT.md 术语纪律
- **数据 / 环境依赖**：`datasets/golden/dev/` 11 剧本（few-shot 与 3 剧本验证的数据底座；**holdout/ 禁看**）；chaos/scenarios 注入脚本库；SQLite dev 库；LLM API key（DeepSeek/Qwen，实现票真实调用前向用户确认；设计期 mock + 估算）；9 容器 compose 栈（真实注入演练）
- **后续影响**：`incidents` 档是 M3 调查入口（Harness 以 `incident_id` 启动）；`classification_json` 是 M8「误报归档 / 风险观察」tab 的数据源；逻辑告警归并与降噪率口径被 M7 评测台直接复用；分类正确性进 M7 回归

## 开放设计点（评审 grill）

> 以下 G1–G9 已于 2026-09-07 全部评审定案（定案理由与标准来源见各行「评审依据」及节末汇总表）；若被推翻需回退 `draft` 并重开对应 issue。

| # | 开放点 | 推荐默认解 | **评审定案（2026-09-07）** | 评审依据 |
|---|---|---|---|---|
| G1 | 抑制（层 2）/聚合（层 3）做不做 | 不做独立层；M2 = 分类层 | **定案：M2 只做四层递进的第 4 层（分类）+ G6 统计层归并**。层 1 去重 M1 已完成；层 2/3 不进 M2——PRD §7-M2 只点名「规则预筛 + LLM 分类」双通道，11 剧本规模下抑制/聚合的降噪增量小、复杂度成本大；「下游挂上游告警抑制」类模式如确有需要，可**以规则谓词形式**进规则通道（表达力等价），但不建独立抑制引擎。聚合留待 M3 调查入口按需议（事件级归并）。 Alertmanager `inhibit_rule` 语义是先例参考而非实现目标 | R5（inhibit_rule 语义）+ 母本 §3.3 模块 A（四层递进）+ PRD §7-M2 边界 |
| G2 | 规则通道形态 | 纯 Python 谓词注册表 | **定案：Python 谓词注册表，不引规则引擎、不做声明式 DSL**。每条规则 = `{name, predicate(alert_event, context) -> RuleVerdict}`，RuleVerdict ∈ {`false_positive`(带 reason), `pass`(交 LLM)}——规则只做「误报直判」与「放行」两种判定，**不判真实**（防规则把真事件误杀，真实判定必须过证据面）。误报模式规则化标准：**能写成确定性谓词的才进规则通道**，语义模糊一律交 LLM。初始规则集：① resolved-only 幽灵通知（无对应 firing，M1 实录 4 条）；② 维护窗口/静默期；③ 重放/迟到期失效告警（endsAt 早于当前且已恢复）。规则数预期个位数，注册表可单测，规则名入 CONTEXT 词汇 | R1（最简模式起步、复杂度需可测收益）+ 母本模块 A（规则先行） |
| G3 | LLM 通道：模型/prompt/输出契约 | DeepSeek-chat + Qwen-plus，few-shot 只取 dev | **定案**：① 模型走 PRD §6 选型 **DeepSeek-chat 与 Qwen-plus 双模型**，经统一 OpenAI-compatible 接缝可热切换（M7 ≥2 模型口径的预留），实现为接口 + Mock 先行（设计期零真实调用）；② few-shot 样本**只取 `datasets/golden/dev/`**，且**排除 3 验证剧本**（防自证泄漏），每态 K=2–3 条；③ prompt 结构：系统角色（SRE 分诊员）+ 三态定义（含 D-07 风险语义）+ few-shot + 事件卡片 JSON（D-17 形状，上下文时间锚 `last_fired_at`）+ 输出协议；④ **结构化输出契约**：`{verdict: "false_positive"\|"incident", confidence: float∈[0,1], reason: string}`，JSON mode + Pydantic 校验；**LLM 不直接输出 risk**——`confidence < risk_confidence_threshold`（默认 0.7，可配）时派生 risk（D-07 兜底）；⑤ 超时 30s / 重试 ≤2 / 失败落 risk（channel=llm_error） | R2（DeepSeek JSON mode 官方文档）+ R4（few-shot 一致性官方指引）+ D-07 |
| G4 | 分类落库形态 | 增列 `classification_json`，status 不扩枚举 | **定案**：`alert_events` 增列 `classification_json`（审计全量：verdict/confidence/reason/channel/model/tokens/cost/classified_at），**status 枚举照架构 §4 `deduped → classified` 不动**——三态出口存 verdict，status 只表达是否已分类，避免扩枚举推翻冻结模型；查询走 SQLite JSON1 `json_extract`（11 剧本规模无需索引与独立 verdict 列）。增列走 **D-13 同款评审流程**（登记 D-19 + 回写架构文档 §4） | R7（SQLite JSON1）+ D-13 增列流程先例 |
| G5 | incidents 最小集与建档粒度 | 照架构 §4 五字段，1:1 建档 | **定案**：M2 最小集 = `id / alert_ids(JSON 数组) / severity / status('investigating') / created_at`，对齐架构 §4 六表规划（M4 `evidence_steps.incident_id` 外键衔接、M3 调查入口）。**建档粒度 1:1**：一行判定真实的告警 → 一条 incident（`alert_ids` 单元素数组，数组结构为未来归并预留）；跨告警聚合明确 Non-goal（G1）。`severity` 取告警 `labels.severity`（缺省 warning）；`alert_ids[0]` 即 primary anchor，M3 取证从该行的 D-17 事件卡片开局 | 架构 §4 六表规划 + D-17 卡片契约 |
| G6 | 统计口径 + D-14 桶边界 | 统计层「逻辑告警」归并 | **定案（登记 D-20）**：① 单位 = **逻辑告警 / Logical Alert**：canonical label 子集（`{alertname, job, instance}`，同 D-14 指纹输入不含桶）相同、且相邻两行 `前.last_fired_at` 与 `后.fired_at` 间隔 ≤ `dedup_window` 的行，传递归并为 1 个逻辑告警——**M1 落库行为不动（D-14 不改）**，归并只发生在统计层；② `R`（原始量）= 有效 firing 投递数 = Σ `dedup_count`（D-15 口径，含 resolved-only 补计）；③ **降噪率 = (R − I) / R**，I = 判为 incident 的逻辑告警数；其中 (R − 行数) 为 M1 重复合并贡献，FP + risk 为 M2 归档/观察贡献；④ **漏报** = golden 标注 incident 的逻辑告警被判 `false_positive` 的数量，必须 = 0（risk 不算漏报，单列观察）；⑤ 判真标准 = golden `classification` 标注（G7 扩展后），标注错 = 评测全错（D-18 教训） | D-14/D-15（M1 落库与计数口径，只消费）+ 母本模块 A（降噪率+漏报率双指标）+ issue 03 实录（跨桶开 2 行已证） |
| G7 | 误报样本来源与 3 剧本选择 | dev 集新增 1 个误报类剧本 | **定案（登记 D-21）**：① 误报样本来源 = **dev 集新增 1 个误报类剧本** `false-positive-flap`（建议名）：注入脚本不注入业务故障，仅临时下调某条 Prometheus 告警规则阈值使其在正常水位误触发（配置漂移型误报，真实运维高频场景），恢复规则后 resolved；② golden 扩展：`alert_timeline` 条目增**可选**字段 `classification`（`incident` 缺省 / `false_positive`），向后兼容（D-18 十字段与三字段逐字一致纪律不动，该字段只影响 M2/M7 判分）；③ **3 验证剧本 = `slow-sql`（基础设施）+ `protocol-mismatch`（业务语义层，PRD 硬要求含 1 类）+ `false-positive-flap`（历史误报）**；④ 误报剧本入 dev/ 与 chaos/scenarios 走 issue 06（`ready-for-human`，D-12/D-18 契约面）；holdout 同步延至 M7 前，M2 期间 holdout 禁看不变 | D-18（golden 同源纪律）+ PRD §7-M2（3 剧本含 1 历史误报） |
| G8 | 成本预算 | 单次 LLM 分类成本上限 + 估算法 | **定案**：① 上限：**单次 LLM 分类调用 ≤ ¥0.05**（估算口径，实现票实测回填）；prompt 总量 ≤ 4k tokens（系统 + few-shot + 卡片），输出 ≤ 300 tokens；② 核算公式 `cost = in_tokens × 输入单价 + out_tokens × 输出单价`，单价取 R3/R4 官方定价页快照（设计期标注取价日期）；③ 分层调用结构保证 LLM 只处理规则未决行（成本杠杆 2「规则先行」）；全量 11 剧本 × 3 通道分类的评测总成本预算 ≤ ¥2（估算）；④ **设计会话口径：全部 mock/估算法（用户 2026-09-07 拍板），零真实调用**；issue 07 真实调用前需用户确认 key 并回填实测 | R3（DeepSeek 官方定价页）+ R4 之外的成本杠杆（母本 8：优化 plan > 规则先行 > 缓存+分层调用） |
| G9 | 与 M7 评测台衔接 | 3 剧本机械比对先行，judge 留 M7 | **定案**：① M2 验收 = **规则匹配级**：verdict vs golden `classification` 逐逻辑告警机械比对（可自动判定），LLM-as-judge + 人工抽检 20% 是 M7 设施，M2 不建；② M2 交付的统计模块（逻辑告警归并 + 降噪率/漏报）作为 M7 runner 的分类指标函数直接复用，接口按「输入评测运行数据 → 输出指标字典」设计；③ `MockLLMClassifier` 保留进实现，M7 跑矩阵前可零成本回归结构；④ dev/holdout 隔离纪律照搬：M2 全程只碰 dev/（含 few-shot、误报剧本、验证），holdout/ 一律禁看（含 raw 切片） | 架构 §6（双集隔离 + 判对错两级）+ D-18 |

### 评审依据（官方标准来源）

| # | 来源 | 用于 |
|---|---|---|
| R1 | Anthropic《Building Effective Agents》：最简模式起步、复杂度必须有可测量收益（架构文档 §1 已引同一来源）：`https://www.anthropic.com/research/building-effective-agents` | G1/G2（不做抑制/聚合独立层、谓词注册表而非规则引擎） |
| R2 | DeepSeek 官方 JSON Mode / 结构化输出文档：`https://api-docs.deepseek.com/guides/json_mode`（OpenAI-compatible `response_format`） | G3（结构化输出契约） |
| R3 | DeepSeek 官方定价页：`https://api-docs.deepseek.com/quick_start/pricing` | G8（成本估算单价快照来源） |
| R4 | Anthropic 官方 prompt engineering：multi-shot prompting 提升分类一致性：`https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/multi-shot-prompting`；Qwen/DashScope 定价见阿里云百炼 `https://help.aliyun.com/zh/model-studio/`（G3 双模型与 G8 取价） | G3/G8 |
| R5 | Alertmanager `inhibit_rule` 抑制语义（父子抑制先例，供 G1 论证「语义可由规则谓词等价表达」）：`https://prometheus.io/docs/alerting/latest/configuration/#inhibit_rule` | G1 |
| R6 | M1 评审依据 R1（Alertmanager webhook 2xx 确认/非 2xx 重试语义，`m1-alert-ingestion-design.md` §7）：ingest 主链路保持零 LLM、分类独立入口的时序解耦理由 | G4（`POST /classify` 不串联 `/ingest`） |
| R7 | SQLite JSON1 扩展（`json_extract` 查询 classification_json）：`https://www.sqlite.org/json1.html` | G4（不建独立 verdict 列/索引的理由） |
| R8 | 仓内契约（非外部来源）：D-07（不确定落风险）/ D-13（增列流程）/ D-14（指纹含时间窗桶）/ D-15（计数口径）/ D-17（卡片 13 键契约）/ D-18（golden 同源纪律），见 `docs/design/decisions.md` | G2–G7 各处「只消费不推翻」的输入契约 |

## 开发计划（任务拆解）

> 节奏：W2 后半（M1 已收尾，可立即派工）；目标 2.5–3 天（每日 1–2h）。TDD 红绿循环照硬规则 12：谓词、统计归并、输出契约是约定接缝。issue 01–05、07 标 `ready-for-agent`，issue 06 标 `ready-for-human`。

**关键里程碑**：

- **M2-A 分类内核**（T1–T3）：接缝 + 规则通道 + LLM 通道（mock）可跑通，输出契约稳定
- **M2-B 落库与统计**（T4–T5）：三态落库 + incidents 建档 + 降噪率/漏报核算
- **M2-C 数据底座与验收**（T6–T7）：误报剧本入 dev（T6，人工）→ 3 剧本端到端验证 + 实测回填 + 翻 `implemented`（T7）

| # | 任务 | 内容 | 验收（机械判定，实测回填） | 依赖 | 就绪态（将来） |
|---|---|---|---|---|---|
| T1 | 分类领域模型与接缝 | verdict 三态枚举、`ClassificationResult` 结构、LLM client 接口 + Mock 实现、`risk_confidence_threshold` 配置 | pytest 绿：三态枚举与阈值派生单测、Mock client 契约单测 | — | ready-for-agent |
| T2 | 规则通道 | 谓词注册表 + 初始规则集（resolved-only 幽灵 / 维护窗口 / 重放失效）；规则只判误报或放行 | pytest 绿：每条规则正反例单测；规则命中行 0 次 LLM 调用 | T1 | ready-for-agent |
| T3 | LLM 通道 | prompt 组装（few-shot 只取 dev、排除 3 验证剧本）+ JSON mode 输出 Pydantic 校验 + 超时/重试/失败落 risk + 成本字段记录（mock 值） | pytest 绿：畸形输出重试→risk 单测；few-shot loader 泄漏守卫测试（断言 3 验证剧本与 holdout 不在样本池） | T1 | ready-for-agent |
| T4 | 落库与 incidents 建档 | `alert_events.classification_json` 增列 + status 翻 classified；`incidents` 最小表；`POST /classify`（独立入口）+ `GET /alerts?verdict=` + `GET /incidents` | pytest 绿：三态落库字段齐全；incidents 1:1 建档；endpoints 契约测试；增列回写架构文档 §4 | T2 / T3 | ready-for-agent |
| T5 | 统计口径 | 逻辑告警归并（D-14 桶边界传递归并）+ 降噪率/漏报核算 + 分类报告字典输出 | pytest 绿：跨桶 2 行归并 1 逻辑告警单测；降噪率公式用构造数据核算比对 | T4 | ready-for-agent |
| T6 | 误报剧本 + golden 扩展 | chaos/scenarios 新增阈值漂移误报剧本；dev/ 新增 `false-positive-flap` golden（标注 classification: false_positive）；校验器兼容可选字段 | `golden_matches_scenario` 通过；`load_golden_tree` R6 校验绿；缺省 classification=incident 向后兼容 | —（与 T1–T5 并行） | **ready-for-human**（D-12/D-18 契约面扩展） |
| T7 | 端到端 3 剧本验证与收尾 | 真实栈注入 3 剧本 → ingest → classify → 统计报告；LLM 真实调用（**需用户确认 key**）成本实测回填；验收节逐条回填；设计文档翻 `implemented` | 3 剧本 verdict 与 golden 一致、0 漏报；降噪率实测回填；规则先行计数可证；全量 pytest + ruff 绿 | T4 / T5 / T6 | ready-for-agent（真实调用前确认 key） |

### 评审落定决策（2026-09-07，评审通过）

- **G1–G9 全部定案**：逐条结论见 §7 表格「评审定案」列，标准来源见「评审依据」R1–R8。
- **新决策已登记**：D-19（落库形态与三态出口）、D-20（降噪统计口径与逻辑告警）、D-21（误报样本与 golden 扩展）→ `docs/design/decisions.md`。
- **新术语已入 `CONTEXT.md`**：逻辑告警 / Logical Alert、规则通道 / Rule Channel、LLM 通道 / LLM Channel、误报剧本 / False-Positive Scenario。
- **架构文档回写待实现票执行**：`alert_events` 增列 `classification_json` 与 `incidents` 表建立后，回写架构文档 §4（issue 04）。
- **成本口径**：设计期全部 mock/估算法；issue 07 真实调用前向用户确认 key，实测数据回填验收节，禁虚构。
