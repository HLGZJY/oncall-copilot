# OnCall Copilot

**面向自建服务的 AI 值班排障智能体。** 接入 Prometheus/日志告警后，由大模型**自主决策**完成「降噪分流 → 多源取证 → 根因定位 → SOP 处置（人工确认）→ 恢复验证 → 报告与知识沉淀」，全程**证据链可追溯、效果可评测**。

英文简历可用名：Agentic SRE Copilot / On-call Incident Agent。

> **当前状态**：M0（环境与故障注入）未开工，代码尚未开始编写。本仓库目前以设计文档与知识库为主。

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

## 快速开始

> 环境尚未搭建（M0 未开工）。以下命令是目标形态，待 M0 落地后生效。

```bash
git clone <repo-url> && cd oncall-copilot

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

docker compose up -d          # 拉起 demo 业务系统 + Prometheus/Grafana/Loki
pytest                        # 跑测试
```

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
