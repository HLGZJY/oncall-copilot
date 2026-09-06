---
title: "M0 环境与混沌注入：设计与开发计划"
summary: "demo 业务系统 + 遥测 + 告警规则 + 故障剧本库 + 黄金集的任务拆解、目录布局与验收口径"
source: docs/plans/m0-execution.md + docs/prd.md §7-M0 + docs/architecture/architecture.md §4/§6
status: reviewed
updated: 2026-09-06
read_when: 执行 M0 前；评审 M0 方案时
---

# M0 环境与混沌注入：设计与开发计划

## status

`draft`（草案，讨论中）→ `reviewed`（评审通过，可开工）→ `implemented`（已落地，实测数据已回填）

- **当前状态**：`reviewed`（2026-09-06 评审通过，四个开放点按推荐方案落定，见文末）
- **评审人 / 评审日期**：HLGZJY / 2026-09-06
- **关联 issue**：`.scratch/m0-environment/issues/01–06`（T1–T6 一一对应）

## 目标

- **背景 / 触发原因**：仓库开发前准备已完成（工程门禁、文档体系、issue tracker 均就绪），进入 W1 交付。M0 是后续所有里程碑的地基——没有"注入→告警→取证→处置→验证"的活闭环环境，M1–M7 全部无从谈起（D-01）。
- **要解决的问题**：搭起一套可一键复现的**被观测 demo 业务系统**（api-gw → 异步 worker → MySQL/Redis），配齐三支柱遥测、5–8 条告警规则、8+ 故障剧本注入脚本与黄金评测集，使任何后续模块都能在真实数据上开发与评测。
- **不做的事（Non-goals）**：
  - 不做 Agent 本体（M3）与告警接入服务（M1）——M0 只保证"告警能被发出来"，webhook 落到日志文件即可；
  - 不上 OTel Astronomy Shop（D-02，不进关键路径）；不上 K8s/Chaos Mesh（架构文档 §8 明确不做）；
  - 不做 Grafana 精美化看板（1 张能看异常的基础看板即可，P1 可选项）；
  - 不追求 8+ 剧本一次做完——W1 先交付"环境跑通 + 2 剧本"，其余剧本分批补齐（见 §6 分批计划）。

## 技术方案

**一句话概括**：docker-compose 一键拉起"demo 业务系统 + Prometheus/Loki/Grafana + 告警 + 混沌注入脚本库"，每个剧本自带 `scenario.yaml` 元数据与预标注黄金集，形成 M3/M7 的评测基准。

候选方案已在 D-01/D-02 拍板（轻量自建 compose，2–3GB 内存；Astronomy Shop 留给云上迁移演示），此处不再开新分支。Pumba 在 Docker Desktop（WSL2 后端）下以容器方式运行，Windows 宿主机无兼容问题；Locust/k6 同样容器化，避免宿主机装依赖。

### 设计的模块

> **关键边界**：demo 系统与混沌脚本是"被观测目标"，**不属于 `oncall` 包**——`src/oncall` 的 coverage 门禁、import-linter 契约（C3/C4/C5）均不适用于它们。代码放在仓库顶层独立目录，与 Agent 代码物理隔离。

| 模块 | 动作 | 职责 | 目录 | 关联里程碑 |
|---|---|---|---|---|
| demo 业务系统 | 新增 | api-gw 接请求 → Celery worker 消费任务 → MySQL/Redis；暴露 `/health` 与业务指标；日志落盘 | `demo/`（`api-gw/`、`worker/`、共享 `common/`） | M0（被观测对象） |
| 遥测与告警配置 | 新增 | Prometheus 抓取配置、5–8 条告警规则、Loki 采集（promtail 或 docker loki driver，评审定）、Grafana 基础看板、Alertmanager webhook 落盘 | `deploy/`（`prometheus/`、`loki/`、`grafana/`） | M0 → M1 数据源 |
| 故障剧本库 | 新增 | 8+ 注入脚本（bash/python/Pumba/locust），每个剧本一个目录：注入脚本 + `scenario.yaml` 元数据 + 清理脚本 | `chaos/scenarios/<NN>-<slug>/` | M0 → M3/M7 |
| 黄金评测集 | 新增 | 每剧本 ×3 次执行的记录：触发/恢复时间、全部告警（数量/内容/时间）、预标注根因 + 标准排查路径 + 标准处置 | `datasets/golden/<slug>.yaml` | M0 → M7 黄金集 |
| 剧本元数据校验器 | 新增 | 校验 `scenario.yaml` 与黄金集 YAML 的 schema（字段完整性、剧本↔黄金集对应关系）——**这是 M0 里唯一进 `tests/` 的 TDD 接缝** | `src/oncall/scenarios/`（纯离线、可单测） | M0 → M7 runner 复用 |
| 一键编排 | 新增 | 根目录 `docker-compose.yml`（业务系统）+ `docker-compose.observability.yml`（P/L/G 栈，可拆可合）；`make up / make demo-load / make inject SCENARIO=cpu-spike` 入口 | 仓库根目录 | M0（README 一键复现口径） |

### 数据模型变更

> demo 系统自身的 MySQL 业务表（如任务表）随实现定，此处只登记**对 M3/M7 有契约意义的持久化结构**。oncall 六张核心表（`alert_events/incidents/evidence_steps/...`）属 M1/M4，M0 不建。

| 变更项 | 类型 | 说明 | 迁移方式 |
|---|---|---|---|
| `scenario.yaml` schema | 新增 | 字段：`name/fault_type/category/inject/inject_method/cleanup/expected_alerts/expected_root_cause/expected_remediation`（`fault_type` 用词对齐 CONTEXT.md「故障剧本」条目） | 无迁移，文件即数据；schema 由校验器把关 |
| 黄金集标注 YAML | 新增 | 每剧本一份 ×3 run 记录：`runs[].alert_timeline[].{alert_name,labels,fired_at,resolved_at}` + `root_cause` + `investigation_path` + `remediation`；**区分 dev/holdout 目录（双集隔离，架构文档 §6）** | 无迁移；校验器把关 |
| `scenarios` 表（六表之一） | 新增（延后） | 表结构已在架构文档 §4 定稿（id/name/fault_type/inject_script/expected_root_cause/expected_action）；M0 只产出 YAML，**M7 评测台 runner 落地时才建表并从 YAML 导入** | M7 时 Alembic/DDL |

### API变更

| API | 变更类型 | 请求/响应要点 | 影响调用方 |
|---|---|---|---|
| demo `GET /health` | 新增 | 200 + `{status, db, redis, queue_depth}`；故障时可探活异常 | compose healthcheck、人工演练 |
| demo `POST /tasks` | 新增 | 提交异步任务，供压测与制造队列堆积 | `make demo-load`、Locust |
| demo `GET /metrics` | 新增 | prometheus_client 格式：QPS/延迟直方图/队列深度/DB 连接池 | Prometheus 抓取 |
| Alertmanager webhook receiver | 新增（M0 最简版） | 接收告警 → 追加写 JSONL 日志文件（`deploy/alerts-dump.jsonl`）；M1 时替换为 oncall `ingest` 端点 | M1 告警接入 |
| oncall 侧 API | **无变更** | M0 不动 `src/oncall`（仅新增 scenarios 校验器纯函数） | — |

## 验收标准

> 可实测、可判定，实测后回填本节，不得虚构。

- [ ] `docker compose up -d` 一键拉起全部服务，`/health` 全绿（数据库/Redis/队列连通）
- [ ] 人工触发 CPU 飙高故障 → Grafana / PromQL 可见异常曲线 → Alertmanager 触发告警并落 `alerts-dump.jsonl`
- [ ] 剧本库 ≥8 类，覆盖 m0-execution 六大类（资源/网络/业务/负载/故障/语义层），**必须含 1 个业务语义层故障（版本协议不兼容）**——指标日志都正常但业务出错
- [ ] 每个剧本具备：注入脚本 + `scenario.yaml`（过校验器）+ 清理脚本 + 黄金集 ≥3 run 标注
- [ ] 数据质量自查（m0-execution 口径）：每个剧本至少 1 个指标可检异常？关键错误在 Loki 日志里查得到？服务拓扑可从指标 label 还原？
- [ ] 剧本与黄金集 schema 校验器单测通过，全量 pytest 通过（coverage ≥80% 维持）
- [ ] README 快速开始段更新：新人按文档可复现"注入 → 告警"全流程

## 依赖

- **前置依赖**：D-01/D-02（已定）；pyproject 工程门禁（已就绪）；CONTEXT.md 术语表（已就绪）——**无阻塞，可直接开工**
- **数据 / 环境依赖**：Docker Desktop（WSL2 后端，Pumba/容器化 Locust 的前提）；本机 2–3GB 可用内存给该栈；LLM 依赖为零（M0 不调模型）
- **后续影响**：本设计为 M1（webhook 换成 ingest 端点）、M3（取证数据源）、M7（黄金集 + scenarios 表）铺路；`scenario.yaml` 的字段即 M7 runner 的输入契约，**字段名一旦被 M7 引用就难以改名**——评审时重点过这一节

## 开发计划（任务拆解）

> 每日 1–2h 节奏，总量约 1 周。T1–T3 为 Phase A（W1 目标：环境跑通 + 2 剧本），T4–T6 为 Phase B（剧本补齐 + 黄金集）。TDD 红绿循环只用在 T2 的校验器接缝上；T3–T6 是环境/脚本类工作，验收靠集成演练而非单测。

| # | 任务 | 内容 | 验收 | 依赖 |
|---|---|---|---|---|
| T1 | demo 业务系统骨架 | compose + api-gw（FastAPI）+ worker（Celery）+ MySQL/Redis；`/health`、`POST /tasks`、基础业务表 | 一键拉起，任务从提交到消费走通 | — |
| T2 | 遥测埋点 + 校验器（TDD） | prometheus_client 四类指标 + structlog 日志落盘；Loki 采集与 Grafana 看板；`scenario.yaml`/黄金集 schema 校验器（先写测试） | PromQL 能查到四类指标；日志可查；校验器单测绿 | T1 |
| T3 | 告警规则 + 首批 2 剧本 | 5–8 条 Prometheus 告警规则 + Alertmanager webhook 落盘；剧本 01-CPU 飙高（Pumba）、02-慢 SQL（自定义脚本） | **W1 验收演练**：注入 CPU 故障 → PromQL 异常 → 告警落盘 | T2 |
| T4 | 注入框架泛化 + 剩余剧本 | 从 01/02 提炼 `scenario.yaml` 目录约定与清理脚本模式；补齐网络（超时/丢包）、负载（队列堆积/连接打满）、故障（OOM/进程被杀/缓存雪崩）、死锁、语义层（版本不兼容）至 8+ | 每剧本注入→告警→清理可重复执行，schema 校验全过 | T3 |
| T5 | 黄金集生成 | 逐剧本 ×3 run 记录告警时间线 + 预标注（根因/排查路径/处置）；按 dev/holdout 分目录 | 黄金集过校验器；抽 1 个剧本用人工核对标注准确性 | T4 |
| T6 | 一键复现与收尾 | 根 compose 整合、Makefile 入口、README 快速开始 + 演练录屏素材；数据质量自查清单回填本设计文档验收节 | 全部验收项打勾，`status` 翻 `reviewed → implemented` | T5 |

### 评审落定的决策（2026-09-06 评审通过时拍板）

1. **Loki 采集方式**：采用 Docker loki logging driver（compose 内零配置直推），不引入 promtail 采集 agent。
2. **demo/ 与 chaos/ 的 lint 范围**：ruff 覆盖这两个目录，mypy 豁免（非产品代码）；coverage 门禁维持只算 `src/oncall`。落地时同步改 `pyproject.toml` 的 ruff `src`/`include`。
3. **告警规则与剧本一一对应**：T3 产出时维护"剧本 ↔ 告警规则"映射表（进 `scenario.yaml` 的 `expected_alerts` 字段即映射的物理载体），杜绝哑剧本。
4. **`scenario.yaml` 字段名定稿**：`name / fault_type / category / inject / inject_method / cleanup / expected_alerts / expected_root_cause / expected_remediation`——作为 M7 runner 的输入契约冻结；已记入 `design/decisions.md` D-12，后续改动需走 decisions.md 变更。
