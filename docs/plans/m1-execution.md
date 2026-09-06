---
title: "M1 执行细案：告警接入与归一化"
summary: "Webhook/ingest 端点、指纹去重合并、上下文拉取三源的执行步骤、T1–T6 任务拆解与验收口径"
source: docs/design/m1-alert-ingestion-design.md + docs/prd.md §7-M1
status: seed
updated: 2026-09-06
read_when: W2 执行 M1 时；想快速了解 M1 怎么落地时
---

# M1 执行细案

> **设计与任务拆解**：见 `docs/design/m1-alert-ingestion-design.md`（含模块划分、T1–T6 任务分解、开放决策点）；本文件保留执行口径原文。
>
> **状态说明**：M1 **已正式进入 agent 阶段（2026-09-06 评审通过）**——设计文档翻 `reviewed`、G1–G7 定案，`.scratch/m1-alert-ingestion/`（spec + issues/01–06）已建立，六票全部 `ready-for-agent`；唯一外部依赖 M0-05 仅约束 T6 切 receiver 时点。

## 技术类别与边界

| 类别 | 涉及技术 | 本阶段用途 | 备注 |
|---|---|---|---|
| Web/API | FastAPI async + Pydantic | `POST /ingest` webhook 端点，校验 Alertmanager 告警 JSON 并归一化 | M0-03 已交付 webhook 契约（`alerts-dump.jsonl`） |
| 归一化与去重 | 确定性哈希 + 时间窗 | 告警指纹计算、同源重复合并（status=deduped） | 已定案（G1：canonical 子集 sha256 + 10m 窗，见设计文档 §7） |
| 持久化 | SQLAlchemy 2.0 + SQLite | `alert_events` 表（对齐架构文档 §4 六表模型） | Alembic 延至切 MySQL 时引入 |
| 上下文拉取 | Prometheus HTTP / PromQL、targets+labels | 近期指标、服务拓扑还原；近期变更 adapter 占位 | 变更源 M0 未提供，返回空列表 |
| 工程门禁 | pytest TDD / ruff+mypy / import-linter / coverage | 沿用 M0 口径（coverage 只算 `src/oncall`） | — |

**边界（Non-goals 速记）**：M1 零 LLM 调用；不建 incidents/evidence 表（属 M2/M3/M4）；不算降噪率（≥80%/0 漏报归 M2 统一验收）；不做 UI（M8）。PRD 措辞「同故障重复告警合并为 1 事件 / 事件卡片带上下文」在本阶段均指**归一化告警对象 + 上下文 JSON 契约**——正式「事件/Incident」建档在 M2 分类之后。

## 接入与归一化步骤（约 W2 前半，每日 1–2h）

与 M0 收尾（05 黄金集、06 一键复现）并行时的关键纪律：**Phase A（T1–T3）期间不切换 Alertmanager receiver**（保持落 `alerts-dump.jsonl`），oncall `/ingest` 独立联调；receiver 切换放到 T6，且须在 M0-05 黄金集采集完毕后执行（或走双写过渡，可回退）。

1. `alert_events` 表与模型（SQLAlchemy，字段对齐架构文档 §4）→ SQLite 建表
2. `/ingest` 端点：Pydantic 校验 Alertmanager payload → 归一化落库（先只新增）
3. 指纹计算 + 时间窗去重合并：同指纹重复 firing 合并为 1 条，`dedup_count` 累加
4. 上下文拉取：PromQL 近期指标 + targets/labels 拓扑还原 + 变更 adapter（占位空）
5. 事件卡片 JSON 组装与查询接口（无 UI 阶段的验收载体）
6. receiver 切换 + 端到端演练 + README/设计文档实测回填

## 指纹去重约定（待 grill，先用推荐默认值）

- **canonical 字段**：`{alertname + instance/service label}`；数值、描述、annotations 等易变字段不参与哈希（防抖动误分）
- **时间窗**：同指纹在窗口（默认 10 分钟，可配）内再次 firing → 合并
- **落库**：`alert_events` 行原地更新（status=deduped、`dedup_count`++、`last_fired_at`）
- ✅ 以上取值已于 2026-09-06 评审定案（设计文档 §7「评审落定决策」，标准来源 R1–R5），冻结为执行契约，擅改须回退设计文档 `draft`

## 上下文拉取三源

| 源 | M1 动作 | 降级 |
|---|---|---|
| 近期指标 | Prometheus HTTP 查询近 N 分钟相关序列（PromQL） | 查询失败 → 返回空 + error 字段，不中断 |
| 服务拓扑 | 从告警 labels 与 Prometheus targets/up 还原服务关系 | 无拓扑数据 → 最小单节点对象 |
| 近期变更 | **占位 adapter**（M0 未提供变更数据源，接口预留） | 恒返回空列表，不报错 |

## 任务拆解 T1–T6（将来 issue 拆分蓝本）

| # | 任务 | 内容 | 验收（机械判定，实测回填） | 依赖 | 就绪态（将来） |
|---|---|---|---|---|---|
| T1 | `alert_events` 表与模型 | SQLAlchemy model + SQLite 建表，字段对齐架构 §4 | pytest 绿；字段齐全 | —（并行 M0 收尾） | ready-for-agent |
| T2 | `/ingest` webhook + 归一化 | Pydantic 校验 Alertmanager JSON → 落 `alert_events`；双写 dump 保留 | 真实 webhook 打 `/ingest` 2xx 且落库；dump 仍可写 | M0-03 ✅ / T1 | ready-for-agent |
| T3 | 指纹计算 + 去重合并 | sha256 canonical + 时间窗；合并重复 firing | 同故障重复告警合并 1 条；窗口边界单测绿 | T2 | ready-for-agent（issue 03） |
| T4 | 上下文三源 | PromQL 近期指标 + 拓扑还原 + 变更占位 | 返回近 N 分钟序列与拓扑 JSON；变更空不报错 | T2 | ready-for-agent（issue 04） |
| T5 | 事件卡片 JSON 契约 | 上下文 JSON 组装 + 查询接口 | GET 返回字段齐全的上下文 JSON | T3 / T4 | ready-for-agent（issue 05） |
| T6 | 端到端与收尾 | receiver 指向 oncall（可回退）；注入 1 剧本 → 合并 1 条 → 卡片带上下文 | 端到端可复现；回退开关生效；全量 pytest 绿 | T1–T5（切换须 M0-05 后） | ready-for-agent |

## 验收标准

- [ ] 同故障重复告警被合并为 1 条归一化告警（`dedup_count ≥ 2`），0 漏收
- [ ] 归一化告警可通过查询接口取回「事件卡片」JSON：告警本体 + 近期指标 + 服务拓扑 + 变更（可为空）
- [ ] `/ingest` 对真实 Alertmanager payload 返回 2xx 并落库；双写期间 `alerts-dump.jsonl` 持续可写
- [ ] receiver 切换后保留回退开关；切换时点避开 M0-05 黄金集采集
- [ ] 全量 pytest 通过（coverage ≥80% 维持，只算 `src/oncall`）
- [ ] 实测数据回填本文与设计文档（含一次端到端演练的告警/合并记录）
