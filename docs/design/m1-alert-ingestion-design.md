---
title: "M1 告警接入与归一化：设计与开发计划"
summary: "Alertmanager webhook 接入（/ingest）、告警指纹去重合并、上下文拉取三源、alert_events 表、T1–T6 任务拆解与验收口径"
source: docs/plans/m1-execution.md + docs/prd.md §7-M1 + docs/architecture/architecture.md §2/§4/§5 + docs/design/feature-design-template.md + 评审标准来源（见 §7 末「评审依据」）
status: reviewed
updated: 2026-09-06
read_when: 评审 M1 方案时；进入 M1 开发前；被问「M1 告警接入怎么做」时
---

# M1 告警接入与归一化：设计与开发计划

## status

`draft`（草案，讨论中）→ `reviewed`（评审通过，可开工）→ `implemented`（已落地，实测数据已回填）→ `superseded`（被后续设计取代，注明替代文档链接）

- **当前状态**：`reviewed`（2026-09-06 评审通过：§7 G1–G7 全部定案，依据 GitHub 官方标准来源，见 §7 末「评审依据」；D-13 已入 `decisions.md`；增列已回写架构文档 §4）
- **评审人 / 评审日期**：用户授权 AI 执行评审（检索 GitHub 标准逐条比对），2026-09-06；如有异议可推翻定案并回退 `draft`
- **关联 issue**：`.scratch/m1-alert-ingestion/`（spec.md + issues/01–06，与 T1–T6 一一对应）

### 是否已正式进入 agent 阶段的状态说明

**结论：已正式进入 agent 阶段（2026-09-06）**——评审通过（G1–G7 定案）且 `.scratch/m1-alert-ingestion/`（spec.md + issues/01–06）已建立，六票全部 `ready-for-agent`，可按依赖链派工。

**进入 agent 阶段的条件**（评审时逐条核对）：

| # | 条件 | 当前状态 |
|---|---|---|
| ① | M0-03 `resolved`：Alertmanager webhook JSON 契约 + 5–8 条告警规则已实测交付（`deploy/alerts-dump.jsonl` 3 轮 firing/resolved 可重复） | ✅ 已满足 |
| ② | 本文件 §7 开放设计点经评审 grill 拍板（指纹字段/时间窗、变更源边界、事件卡片载体、建表增列等） | ✅ 已满足（2026-09-06 评审定案，见 §7「评审落定决策」） |
| ③ | 依赖侧就绪：M0-04 `resolved` ✅（告警多样性已备）；receiver 切换须 M0-05 黄金集采集完毕或走双写过渡；M0-06 一键复现与 M1 收尾联动 | 🟡 04 ✅ / 05 ⬜ / 06 ⬜（receiver 切换在 T6，不阻塞 01–05） |
| ④ | `.scratch/m1-alert-ingestion/spec.md` + `issues/T1–T6` 建立，按 §8 就绪态逐个标注 | ✅ 已满足（2026-09-06 建立） |
| ⑤ | 每张 `ready-for-agent` 票的验收标准可机械判定（triage 硬门槛）；涉及 §7 开放点的票先留 `ready-for-human` | ✅ 已满足（开放点全部定案，06 票验收可机械判定） |

**✅ 正式进入 agent 阶段（2026-09-06）**：条件 ①②④⑤ 全部满足；唯一外部依赖是 M0-05（仅约束 T6 receiver 切换时点）。issue 01–06 已标 `ready-for-agent`，可按依赖链派工。

## 目标

- **背景 / 触发原因**：W2（9/7–9/13）排期 = M1+M2；M0-03 已把告警送到 `alerts-dump.jsonl`（最小落盘版 receiver，任务原文注明"M1 时替换为 oncall ingest 端点"），M0-04 亦已 resolved（剧本 03–11、规则扩至 8 条）。趁 M0 收尾期（05 黄金集 / 06 一键复现未决）把 M1 设计先行钉下来，M0 一收尾即可无缝派工，不与 W2 的 M2 挤压。
- **要解决的问题**：
  1. **统一接入**：把告警从"落盘文件"升级为 oncall 标准 `POST /ingest` 端点——一条告警 = 一份结构化 `alert_events` 记录，任何告警源（Prometheus/日志规则）走同一契约；
  2. **不淹没人**：告警风暴/同源重复（同规则 + 同实例 + 时间窗内）合并为 1 条归一化告警并暴露 `dedup_count`，为 M2 降噪统计与 M7 评测准备干净分母；
  3. **排障开局有料**：每条归一化告警自动携带上下文（近期指标/服务拓扑/近期变更），即 PRD 口径的"事件卡片带上下文"——后续 M2/M3 不用每次从零取证。
- **不做的事（Non-goals）**：
  - 不做降噪分类与降噪率统计（M2）；「事件/Incident」建档在 M2 判定真实后（架构文档 §5 时序）
  - 零 LLM 调用（M1 全确定性逻辑，规则先行原则的起点）
  - 不做 UI / 面板（M8）；事件卡片以 JSON 契约为载体
  - 不建 incidents / evidence_steps / hypotheses 表（M2/M3/M4 职责）
  - 近期变更源不真实接入，只留占位 adapter（M0 未提供变更数据源）
  - 不引入 Redis（SQLite 起步、M1 低并发）；不追高并发与多租户

## 本阶段涉及的技术类别

| 技术类别 | 涉及技术 | 本阶段用途 | 开放点 |
|---|---|---|---|
| Web / API | FastAPI（async）、Pydantic v2 | `POST /ingest` webhook 端点；Alertmanager v4 JSON 契约校验；事件卡片查询路由 | 端点/字段命名随评审定稿 |
| 告警协议 | Alertmanager webhook JSON（status/labels/annotations/startsAt/endsAt/…） | 归一化入参契约（M0-03 已实测同类 payload） | 内部 canonical 字段映射集 |
| 归一化与指纹去重 | 确定性哈希（sha256）、时间窗合并逻辑 | 告警指纹（规则+实例+时间窗）；同源重复合并 | 指纹字段、时间窗取值 |
| 持久化 | SQLAlchemy 2.0、SQLite | `alert_events` 表建表与读写（对齐架构 §4） | 增列字段、迁移方式 |
| 上下文拉取 | Prometheus HTTP API / PromQL；targets+labels | 近期指标序列、服务拓扑还原 | 近期变更数据源（占位） |
| 工程门禁 | pytest TDD / ruff / mypy / import-linter / coverage | 指纹与归一化纯函数是 M1 的 TDD 接缝；门禁沿用 M0 口径 | — |

## 技术方案

**一句话概括**：把 Alertmanager receiver 从"写 JSONL 文件"换成 oncall `POST /ingest`——Pydantic 校验入参 → 归一化为内部告警结构 → 指纹计算后在时间窗内去重合并落 `alert_events`（SQLite）→ 按需拉取三源上下文组装成"事件卡片"JSON，通过查询接口对外可读；receiver 切换走双写/回退开关，不动 M0-03 既有契约的可回退性。

### 设计的模块

| 模块 | 动作 | 职责 | 目录 | 关联里程碑 |
|---|---|---|---|---|
| webhook 接入与归一化 | 新增 | `/ingest` 端点：Alertmanager JSON → Pydantic 校验 → 归一化为内部 alert 结构 → 落 `alert_events`（先新增，合并见下行） | `src/oncall/ingest/` | M1 → M2/M3 数据入口 |
| 指纹与去重 | 新增 | 稳定哈希（canonical 字段）+ 时间窗查询；重复 firing 合并 1 条（status=deduped、`dedup_count`++） | `src/oncall/ingest/` | M1 → M7 runner 统计 |
| 上下文拉取 | 新增 | 三源：PromQL 近期指标、服务拓扑（targets/labels）、近期变更 adapter（占位空） | `src/oncall/context/` | M1 → M3 取证复用 |
| 事件卡片 JSON | 新增 | 归一化告警 + 三源上下文组装；查询路由（无 UI 阶段的验收载体） | `src/oncall/`（api 层） | M1 → M8 展示 |
| 数据访问与建表 | 新增 | SQLAlchemy model + SQLite `create_all`（dev）；`alert_events` 字段对齐架构 §4 | `src/oncall/db/` | M1 → M2/M4 同源复用 |
| Alertmanager receiver 配置 | 修改 | 目标 URL 从 dump 端点切到 oncall `/ingest`；环境开关控制双写/回退 | `deploy/` | M0-03 交付物 → M1 收编 |

> 目录为建议布局，正式落名在任务拆解（issue 建立）时定；`src/oncall` 只放产品代码（D-12），demo/chaos/deploy 不入包。

### 数据模型变更

| 变更项 | 类型 | 说明 | 迁移方式 |
|---|---|---|---|
| `alert_events` 表 | 新增（M1 建表） | 架构 §4 已冻结列：`id / fingerprint / source / labels_json / fired_at / status(deduped/classified)`；**建议 M1 增列**：`dedup_count`、`last_fired_at`、`resolved_at`、`annotations_json`（存原始告警，供回溯/审计） | SQLAlchemy model + SQLite `create_all`（dev）；Alembic 延至切 MySQL（与 M0 的「M7 时 Alembic/DDL」节奏一致） |
| `alert_events.fingerprint` | 新增（语义定稿） | CONTEXT 定义"规则+实例+时间窗"算出的稳定哈希；**哈希输入只含稳定字段**，防易变字段抖动误分 | 无迁移（计算逻辑在应用层） |
| 六表其余（incidents/evidence_steps/…） | 不建 | 属 M2/M3/M4，M1 不越界 | — |

> ⚠️ 增列 `dedup_count` 等是对架构 §4 冻结模型的扩展，**需评审确认并回写架构文档与 `design/decisions.md`**；一旦被 M2+ 引用就难以改名。

### API 变更

| API | 变更类型 | 请求/响应要点 | 影响调用方 |
|---|---|---|---|
| `POST /ingest` | 新增 | Alertmanager webhook v4 JSON；成功返回 2xx + `{received, deduped}`；按指纹幂等合并 | Alertmanager receiver 配置（`deploy/`） |
| `GET /alerts/{id}/context` | 新增 | 返回事件卡片 JSON：归一化告警本体 + 近期指标 + 服务拓扑 + 近期变更（可为空） | 无 UI 阶段 curl/测试；M8 UI 与 M3 取证 |
| Alertmanager receiver 目标 | 修改 | URL 由 dump 端点 → oncall `/ingest`；**双写开关**（过渡期 dump+DB 同写，切稳后关 dump） | M0-03 的 `deploy/alerts-dump.jsonl`（须可回退，保护 M0-05 黄金集采集） |

## 验收标准

> 可实测、可判定；实测后回填打勾，不得虚构。M1 不含降噪率口径（≥80%/0 漏报在 M2 统一验收）。

- [ ] `/ingest` 接入真实 Alertmanager 通知：同故障 3 连发 → 归一化为 1 条 `alert_events`（`dedup_count=3`），**0 漏收**（每次 firing 都被计入合并，resolved 状态联动正确）
- [ ] `/ingest` 幂等：同一 webhook payload 原样重放 3 次 → 仍归一化为 1 条，`dedup_count` 不变（依据 Alertmanager 对非 2xx 响应做指数退避重试的投递语义，见 §7 评审依据 R1/R3）
- [ ] 批量 payload：单次 webhook 含多条 `alerts[]`（v4 契约为数组 + `truncatedAlerts` 截断字段，见评审依据 R1）→ 逐条归一化落库，不假设单条
- [ ] 事件卡片：经 `GET /alerts/{id}/context` 可取回「告警本体 + 近期指标（近 N 分钟序列）+ 服务拓扑 + 近期变更（可为空）」JSON，字段齐全
- [ ] receiver 切换：双写开关生效（dump 可关可开），关闭 dump 后 ingest 不受影响；切换时点避开 M0-05 黄金集采集
- [ ] 单元测试覆盖约定接缝：指纹纯函数（canonical 稳定性）、归一化映射、时间窗边界；全量 pytest 通过（coverage ≥80% 维持，只算 `src/oncall`）
- [ ] 端到端演练 1 个剧本：注入 → 多告警 → 合并 1 条 → 卡片带上下文，实录（告警名/时间线/dedup_count）回填本节
- [ ] 术语与边界核对：全文措辞与 `CONTEXT.md` 一致；新术语（如「归一化」「事件卡片载体」）登记建议随评审处理，不擅自造词

## 依赖

- **前置依赖**：M0-03 `resolved` ✅（webhook JSON 契约、5–8 条告警规则、`alerts-dump.jsonl` 实测）；M0-04 `resolved` ✅（剧本 03–11、告警规则扩至 8 条，告警多样性就绪）；CONTEXT.md 术语表；工程门禁（pyproject + `tests/test_architecture_guards.py`）；D-12 目录契约——无硬阻塞，Phase A 可在 M0-05/06 收尾期并行开发（不切换 receiver 即可）
- **数据 / 环境依赖**：docker-compose（demo + Prometheus/Loki/Grafana + Alertmanager）可一键复现；Prometheus HTTP API 可查（取近期指标与拓扑）；SQLite dev 库；**零 LLM 依赖**（M1 全确定性，无成本项）
- **后续影响**：为 M2（消费归一化告警，`alert_events.status`: deduped → classified）铺路；上下文三源与 PromQL 客户端被 M3 取证复用；`alert_events` 是 M4 incidents 的外键基座；fingerprint/`dedup_count` 是 M7 评测与降噪统计的分母。**`alert_events` 字段一旦被 M2+ 引用即难以改名——评审重点过数据模型节**

## 开放设计点（评审 grill）

> 以下 G1–G7 已于 2026-09-06 全部评审定案（定案理由与标准来源见各行的「评审依据」及节末「评审依据」汇总）；若被推翻需回退 `draft` 并重开对应 issue。

| # | 开放点 | 推荐默认解 | **评审定案（2026-09-06）** | 评审依据 |
|---|---|---|---|---|
| G1 | 指纹 canonical 字段与时间窗 | `{alertname + instance/service label}` + 10 分钟滑动窗（可配） | **照推荐定案**：canonical = 排序后的稳定 label 子集 `{alertname, job, instance}`（存在者参与）以 `0xFF` 分隔拼接 → sha256 hex 落 `fingerprint`；数值/annotations/generatorURL 等易变字段不入哈希；时间窗默认 10m，配置项 `dedup_window` 可调 | R2（prometheus/common 先例：`SignatureWithoutLabels` 只哈希稳定子集、排序 + SeparatorByte 0xFF 防拼接碰撞）+ R4（`group_interval` 时间窗语义） |
| G2 | 去重合并状态存哪 | `alert_events` 行原地更新（status=deduped、`dedup_count`、`last_fired_at`），不用 Redis | **照推荐定案**：不引 Redis。去重状态持久化在 DB 行内与 Alertmanager nflog 的做法同构 | R3（nflog/DedupStage：去重状态落持久化 notification log）+ R5（SQLite 低流量服务端场景官方适用性声明） |
| G3 | "事件卡片"载体（无 UI） | 归一化告警对象 + `GET /alerts/{id}/context` JSON | **照推荐定案**，并新增硬约束：`/ingest` 必须幂等（同 payload 重放不重复计数）——Alertmanager 对非 2xx 响应指数退避重试，重复投递是常态而非异常 | R1（webhook 契约：2xx 即确认，非 2xx 重试） |
| G4 | 近期变更数据源（M0 未提供） | 指标/拓扑必做；变更源 = 占位 adapter 返回空列表，接口预留 | **照推荐定案**：占位 adapter，接口形状与另两源对齐（列表 + 来源标记），不阻塞 M1 | M0 交付边界（无变更数据源），无外部标准可依 |
| G5 | `alert_events` 增列与迁移方式 | 在架构 §4 基础上增 `dedup_count/last_fired_at/resolved_at/annotations_json`；SQLite `create_all`，Alembic 延至切 MySQL | **照推荐定案**：增列四处；`annotations_json` 同时保存原始 payload 与 Alertmanager 自带 `fingerprint`（全 labels FNV-1a，易变，**不作为主指纹**，仅交叉溯源）；增列已回写架构文档 §4 并登记 D-13 | R1（v4 payload 每条 alert 自带 `fingerprint` 字段）+ R2（全 labels 指纹含易变 label 会抖动，故主指纹必须自算）+ R5（create_all/Alembic 节奏） |
| G6 | receiver 切换与回退 | 过渡期双写（dump + DB），环境开关控制；切稳后关 dump | **照推荐定案**：环境开关控制双写；切换窗口避开 M0-05 黄金集采集；若切换期出现非 2xx，Alertmanager 重试机制天然兜底（补偿窗口） | R1（重试语义）+ R3（通知投递的幂等/补偿设计先例） |
| G7 | 与 M2 的边界 | M1 只去重合并并暴露 `dedup_count`；降噪率 ≥80%/0 漏报在 M2 统一验收 | **照推荐定案**：M1 产出干净分母（`dedup_count`、0 漏收），不做降噪率统计；「事件/Incident」建档仍在 M2 | CONTEXT.md「告警/事件/降噪」语义边界 + PRD §4 验收口径 |

### 评审依据（GitHub / 官方标准来源）

| # | 来源 | 用于 |
|---|---|---|
| R1 | Alertmanager webhook v4 JSON 契约（`version/groupKey/truncatedAlerts/status/alerts[].fingerprint`；2xx 确认、非 2xx 指数退避重试）：`https://prometheus.io/docs/alerting/latest/configuration/#webhook_config`；仓库 `https://github.com/prometheus/alertmanager` | G3/G5/G6 + 幂等与批量验收项 |
| R2 | prometheus/common 指纹算法：FNV-1a 64-bit、label 排序、SeparatorByte(0xFF)、`SignatureWithoutLabels`（排除指定 label 的先例）——`https://github.com/prometheus/common/blob/main/model/signature.go`、`https://github.com/prometheus/common/blob/main/model/fingerprinting.go` | G1/G5 |
| R3 | Alertmanager 去重与通知状态持久化（nflog / DedupStage / needsUpdate 判定）：`https://github.com/prometheus/alertmanager/blob/main/notify/dedup_stage.go`；`https://prometheus.io/docs/alerting/latest/high_availability/` | G2/G6 |
| R4 | 分组时间窗语义（group_wait / group_interval / repeat_interval）：Alertmanager README 路由节 `https://github.com/prometheus/alertmanager` | G1（dedup_window 取值与命名） |
| R5 | SQLite 官方适用场景（低至中流量服务端、企业 RDBMS 替身/测试）：`https://www.sqlite.org/whentouse.html` | G2/G5（SQLite 起步、不引 Redis） |

## 开发计划（任务拆解）

> 节奏：在 M0 收尾期（05 黄金集 / 06 一键复现未决）并行推进 Phase A；目标 W2 前半 2.5–3 天（每日 1–2h），实际以 `docs/plans/roadmap-m0-m9.md` 活跃副本为准。Phase A 的纪律：**不切换 receiver**（oncall `/ingest` 独立联调 + 双写），切换在 T6。

**关键里程碑**：

- **M1-A 接入与归一化**（T1–T3）：webhook → 归一化 → 指纹去重合并可跑通，0 漏收
- **M1-B 上下文与卡片**（T4–T5）：三源上下文可取，"事件卡片"JSON 契约成立
- **M1-C 端到端验收**（T6）：receiver 切换 + 演练 + 实测回填，设计文档翻 `implemented`

| # | 任务 | 内容 | 验收（机械判定，实测回填） | 依赖 | 就绪态（将来） |
|---|---|---|---|---|---|
| T1 | `alert_events` 表与模型 | SQLAlchemy model + SQLite 建表，字段对齐架构 §4 及 G5 增列 | pytest 绿；字段齐全 | —（并行 M0 收尾） | ready-for-agent |
| T2 | `/ingest` webhook + 归一化 | Pydantic 校验 Alertmanager JSON → 归一化落 `alert_events`；双写 dump 保留 | 真实 webhook 打 `/ingest` 2xx 且落库；dump 仍可写 | M0-03 ✅ / T1 | ready-for-agent |
| T3 | 指纹计算 + 去重合并 | sha256 canonical（G1 定案字段 + 0xFF 分隔）+ 时间窗（默认 10m 可配）；重复 firing 合并 1 条 | 同故障重复告警合并 1 条；窗口边界单测绿；同 payload 重放 3 次 `dedup_count` 不变 | T2 | ready-for-agent |
| T4 | 上下文三源 | PromQL 近期指标 + 拓扑还原 + 变更占位（G4 定案） | 返回近 N 分钟序列与拓扑 JSON；变更空不报错 | T2 | ready-for-agent |
| T5 | 事件卡片 JSON 契约 | 上下文 JSON 组装 + 查询路由（G3 定案载体） | GET 返回字段齐全的上下文 JSON | T3 / T4 | ready-for-agent |
| T6 | 端到端与收尾 | receiver 指向 oncall（G6 开关可回退）；注入 1 剧本 → 合并 1 条 → 卡片带上下文 | 端到端可复现；回退开关生效；全量 pytest 绿；本文件验收节与 status 回填 | T1–T5（切换须 M0-05 后） | ready-for-agent |

### 评审落定决策（2026-09-06，评审通过）

- **G1–G7 全部定案**：逐条结论见 §7 表格「评审定案」列，标准来源见「评审依据」R1–R5。
- **新增术语已入 `CONTEXT.md`**：归一化 / Normalization、事件卡片 / Alert Card（AI 按评审授权登记）。
- **`alert_events` 增列已回写**：架构文档 §4 该行追加增列标注；决策登记为 `decisions.md` D-13。
- **衍生设计修订**：① 主指纹自算（canonical 子集 sha256），Alertmanager 自带 `fingerprint` 仅存 `annotations_json` 溯源；② `/ingest` 幂等为硬要求（重试语义）；③ 归一化按 `alerts[]` 批量数组处理，容忍 `truncatedAlerts`。
