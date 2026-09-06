# OnCall Copilot

**面向自建服务的 AI 值班排障智能体。** 接入 Prometheus/日志告警后，由大模型**自主决策**完成「降噪分流 → 多源取证 → 根因定位 → SOP 处置（人工确认）→ 恢复验证 → 报告与知识沉淀」，全程**证据链可追溯、效果可评测**。

英文简历可用名：Agentic SRE Copilot / On-call Incident Agent。

> **当前状态**：M0（环境与故障注入）已落地——demo 业务系统、Prometheus/Loki/Grafana 遥测、8 条告警规则、11 个故障剧本均可一键复现（见下方快速开始）；黄金评测集分批采集中，进度见 `.scratch/m0-environment/issues/05-*.md`。M1（告警接入）开发中。

---

## 它解决什么问题

- **告警疲劳**：海量重复/误报警情淹没真实故障。
- **取证低效**：故障发生时人工在多个系统间来回切换查指标、翻日志，MTTR 长。
- **经验不沉淀**：专家的排障经验只存在个人脑子里，人走了就没了。

**Demo 场景**：一套 docker-compose 自建业务系统（api-gw → 异步 worker → MySQL/Redis + 日志），配 8+ 种可注入故障剧本；Agent 值班，告警来了自动干活。

## 核心自主决策闭环

```
告警进入 ──► ①分类器(规则+LLM) ──► 误报:归档(记日志)      ┌──────────────────────────────┐
                                    │ 风险:建档观察          │  Agent 主循环（LLM 自主决策）    │
                                    ▼ 真实事件 ────────────► │  观察当前证据 → 提出假设 → 选择   │
 事件上下文(指标/日志/变更/知识) ◄────────── 取证工具 ─────── │  下一步动作(调用工具/查询/执行)  │
       │(PromQL/日志搜索/时序异常/KB检索)                    │  校验结果 → 假设被证实或推翻       │
       ▼                                                    │  失败→回退换假设；超步数→求助人类  │
 ②根因报告(带证据链) ──► ③处置:SOP文档→可执行工具           │  每步 thought/tool/result 落库   │
        │                 (干跑→人工确认→执行→指标验证恢复)    └──────────────────────────────┘
        ▼
 ④复盘:结构化事故报告 ──► 知识库(RAG) ──► 下次同类告警秒级召回历史解法
```

**这不是固定工作流**：任务步骤、跳转、终止条件全部由模型在每个 step 依据证据动态决定；固定 SOP 只是 Agent 的"工具"之一，何时调用由模型判断。

## 验收硬口径

> 以下为目标值，**实测后回填，不得虚构**。

| 维度 | 口径 |
|---|---|
| 告警降噪 | 剧本集内降噪 ≥80%，且**0 漏报**真实事件 |
| 根因定位 | 自主调查 Top-1 命中 ≥70%、Top-3 ≥85% |
| 效率 | 单次调查 ≤15 步 / ≤5 分钟 |
| 成本 | 单次调查 LLM 成本 ≤¥0.5 |
| 安全 | 写操作 100% 走「干跑 → 人工确认 → 受控执行 → 恢复验证」四道闸门 |
| 可信 | 每步 thought/tool/input/output 100% 落库，可导出 JSON/Markdown 证据链报告 |
| 评测 | ≥2 个 LLM × ≥8 剧本矩阵报告（命中率/步数/耗时/成本/失败模式） |

## 技术栈

| 层 | 选型 |
|---|---|
| 语言 / 后端 | Python 3.11+ / FastAPI（async + SSE） |
| Agent 编排 | **自研轻量 ReAct 循环**（Planner-Tool-Verifier），不 fork 框架 |
| LLM | DeepSeek-chat / Qwen-plus（OpenAI-compatible 抽象，可热切换） |
| 向量库 | Chroma（起步）→ 可演进 pgvector |
| 可观测栈 | Prometheus + Loki + Grafana（OTel 标准） |
| 存储 | SQLite 起步 → MySQL；Redis（缓存/队列） |
| 混沌注入 | Pumba / Chaos Mesh |
| 前端 | Vue3 轻量面板（时间线 / 证据链 / 报告） |
| 工程 | Docker Compose + GitHub Actions（pytest + lint） |

目标自主性水位：**L3 受控自动执行**（工具自动执行 + 自动检查结果，人工确认门兜底）。

## 快速开始：复现「注入 → 告警」全流程

> 前置：Docker Desktop（WSL2 后端）在跑；跑测试需 Python 3.11 虚拟环境。
> `docker compose` 插件与 standalone `docker-compose` 均可，Makefile 会自动探测。

```bash
git clone <repo-url> && cd oncall-copilot

# 1) 测试环境（Python 3.11）
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 2) 一键拉起全栈：demo 业务系统 + Prometheus/Loki/Grafana + 告警链路
make up
curl http://127.0.0.1:8000/health
#    → {"status":"ok","db":"up","redis":"up","queue_depth":...} 即健康
#    Grafana: http://127.0.0.1:3000（匿名 Admin）；Prometheus: http://127.0.0.1:9090

# 3) （可选）打一段温和业务流量
make demo-load

# 4) 注入故障剧本，1~2 分钟后观察告警
make inject SCENARIO=01-cpu-spike
curl 'http://127.0.0.1:9090/api/v1/query?query=ALERTS'
#    → alertname=DemoApiGwHighLatency, alertstate=firing
#    Alertmanager webhook 同步落盘：deploy/alerts-dump.jsonl
#    Grafana 看板（compose_service=api-gw）可看到 P95 异常曲线；Loki 可查结构化日志

# 5) 清理注入，告警在 for 窗口过后自动 resolved
make cleanup SCENARIO=01-cpu-spike

# 6) 跑测试（ruff + pytest）
make test
```

**Makefile 入口一览**：

| 命令 | 作用 |
|---|---|
| `make up` / `make down` | 拉起 / 停止全栈（down 保留数据卷） |
| `make demo-load [DURATION_SEC=60]` | 向 `POST /tasks` 打温和业务负载（默认强度不触发告警） |
| `make inject SCENARIO=<剧本>` | 注入故障剧本；接受目录名（`01-cpu-spike`）或 slug（`cpu-spike`） |
| `make cleanup SCENARIO=<剧本>` | 清理剧本注入 |
| `make collect-run SCENARIO=<剧本> RUN=r1` | 黄金集单轮采集（注入→告警→清理→按 dump 实测落档） |
| `make test` | `ruff check` + `ruff format --check` + 全量 pytest |

**故障剧本库**：`chaos/scenarios/` 下 11 个剧本，覆盖资源/网络/业务/负载/故障/语义层六大类（含 11-protocol-mismatch 业务语义层故障），每个剧本自带 `inject.sh` + `cleanup.sh` + `scenario.yaml` 元数据。注入后对应的告警规则见 `deploy/prometheus/rules.yml`，"剧本 ↔ 期望告警"映射在各自 `scenario.yaml` 的 `expected_alerts` 字段。

**黄金评测集**：分批采集中，进度见 `.scratch/m0-environment/issues/05-*.md`；目录约定与双集隔离（dev/holdout）见 `docs/design/m0-environment-design.md`。

**演练录屏**：（占位，待黄金集采集完成后录制）

## 文档导航

| 你想了解 | 去哪看 |
|---|---|
| 项目要做什么、做到什么算达标 | [`docs/prd.md`](docs/prd.md) |
| 术语的权威定义（命名一律用这里的词） | [`CONTEXT.md`](CONTEXT.md) |
| Agent 与人协作的规则、硬性纪律 | [`AGENTS.md`](AGENTS.md) |
| 全部细节知识（架构/约定/决策/计划/资料） | [`docs/README.md`](docs/README.md) |

**开发前必读**：[`AGENTS.md`](AGENTS.md)（12 条硬性规则 + 提交规范）。

## 开发纪律速览

- 提交信息：中文 + type 前缀，如 `feat(M3-调查循环): 接入 PromQL 取证工具`；body 写**为什么**而非做了什么。
- 诊断类提交必须写明**最终被证实的那个假设**。
- 命名一律用 [`CONTEXT.md`](CONTEXT.md) 的词汇，新术语当场入表。
- issue 追踪：本地 markdown 文件，位于 `.scratch/`，与代码同版本控制；约定见 [`docs/agents/issue-tracker.md`](docs/agents/issue-tracker.md)。
- 实测数据回填，不得虚构；**M3 / M4 / M7 不可砍**（自主闭环、证据链、评测 = 差异化的三根支柱）。
