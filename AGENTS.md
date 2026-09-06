# AGENTS.md — OnCall Copilot

> 本文件是 Agent 与人协作的"宪法"，**主要依据 `docs/reference/_sources/项目信息.md`（技术地图 + 项目对接）编写**。
> 任何新会话开工前先读这里，再按导航表找资料。
>
> **文档分层**：根目录 = 总纲（本文件 + `README.md` + `CONTEXT.md`）；`docs/` = 细节知识（权威，可频繁更新）；
> `docs/reference/_sources/` = 原始母本（archived，只回溯不引用）。

## 项目简介

**OnCall Copilot**：面向自建服务的 **AI 值班排障智能体**。接入 Prometheus/日志告警后，由大模型**自主决策**完成"降噪分流 → 多源取证 → 根因定位 → SOP 处置（人工确认）→ 恢复验证 → 报告与知识沉淀"，全程**证据链可追溯、效果可评测**。

- **目标水位（白鳝 L0–L5 分级）**：L3 受控自动执行——通过工具自动执行 + 自动检查结果，人工确认门兜底；不追求 L4/L5。
- **技术栈地图（自下而上）**：Python/FastAPI + pytest → Docker Compose / Pumba 混沌注入 / Redis → Prometheus + Loki + Grafana（OTel 标准）→ **自研轻量 ReAct 循环**（Planner + Tool Registry ≥6 工具 + 记忆 + Verifier，不 fork 框架）+ Chroma/pgvector + DeepSeek/Qwen → Vue3 面板。
- **验收硬口径**（实测后回填，不得虚构）：降噪 ≥80% 且 **0 漏报**；根因 Top-1 ≥70% / Top-3 ≥85%；单次调查 ≤15 步 / ≤5 分钟 / ≤¥0.5。

## 快速导航 — 你想做什么 / 去哪里看

### 项目内（权威，日常读这些）

| 你想做什么 | 去哪里看 |
|---|---|
| 对外了解项目 / 快速上手 | `README.md` |
| 查术语权威定义（命名/issue/测试一律用这里的词） | `CONTEXT.md` |
| 查项目定位、痛点、核心闭环、验收口径 | `docs/prd.md` §1–§4 |
| 查 M0–M9 模块职责、技术选型、分步实现 | `docs/prd.md` §5–§7 |
| 查排期与裁剪方案（活跃副本） | `docs/plans/roadmap-m0-m9.md` |
| 查工程纪律（**唯一权威**） | `docs/conventions/`（母本已外置到 `~/.workbuddy/开发规范与工作流程.md`） |
| 查架构 / 主循环 / 技术栈地图 | `docs/architecture/` |
| 查已定决策 / ADR | `docs/design/decisions.md` |
| 查数据集 / 混沌工具 / 面试叙事 | `docs/reference/` |
| 查细节知识（总入口） | `docs/README.md` |
| 查 / 建 issue | `.scratch/`（约定见 `docs/agents/issue-tracker.md`） |
| 查 issue 状态标签取值 | `docs/agents/triage-labels.md` |

### 方法论母本回溯（只读，不是权威）

以下条目指向 `docs/reference/_sources/项目信息.md` 的原始章节。**仅在 docs/ 提炼版语焉不详、需要回溯出处时查阅**，日常开发不要直接引用母本。

| 你想做什么 | 母本内章节 |
|---|---|
| 查 AIOps 术语（告警/事件/证据链/指纹…） | 第 1 部分 · 分层词典（⭐ 为必掌握） |
| 了解方法论四代演进 / L0–L5 成熟度 | 3.1 / 3.2 |
| 设计降噪分类（四层递进） | 3.3 模块 A |
| 选异常检测方案（分层：统计/ML + LLM 语义） | 3.3 模块 B |
| 设计根因定位（假设→取证→证实/推翻） | 3.3 模块 C |
| 做处置自动化（四道闸门 / 白名单） | 3.3 模块 D + 6.3 |
| 做知识库与 RAG（工具，不是架构） | 3.3 模块 E + 4.4 |
| 建评测台（黄金集 / 判对错 / 防泄漏） | 3.3 模块 F + 6.5 |
| 证据链数据结构（可直接抄） | 6.4 |
| 查 M0–M9 各拿什么、三个加分项、面试锚点 | 第 4 部分 · 项目对接 |
| 选数据集 / 开源环境 / 混沌工具 | 附录 B 资源清单 |

### Issue tracker

Local markdown files under `.scratch/`；完整约定见 `docs/agents/issue-tracker.md`，triage 标签词汇见 `docs/agents/triage-labels.md`。

- 一个 feature 一目录：`.scratch/<feature-slug>/`，issue 为 `issues/<NN>-<slug>.md`（`<NN>` 即 issue ID）
- 状态写在文件顶部 `Status:` 行；五个取值：`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`
- **只有验收标准可机械判定的 issue 才允许标 `ready-for-agent`**；涉及架构取舍的一律 `ready-for-human`

## 硬性规则（源自 `docs/reference/_sources/项目信息.md` 的核心结论）

1. **真 Agent，不伪 Agent**：任务步骤、跳转、终止条件全部由模型在每个 step 依据证据动态决定；固定 SOP 只是 Agent 的工具之一，调用时机由模型判断；自带自校验 + 回退重试 + 步数上限（15）。
2. **规则先行、LLM 兜底**：确定性部分（去重/指纹/抑制/聚合）不进模型；模糊地带才给 LLM；不确定的落"风险"而非丢弃——0 漏报的保证。
3. **安全护栏落在系统层，不落在提示词层**：三道防线按有效性排序——只读接口/白名单（最有效）> 人工确认门 > 提示词（无效）。L3 的安全机制必须用**白名单**，不用黑名单。
4. **写操作四道闸门缺一不可**：干跑（打印命令+影响面）→ 人工确认门（API 层拦截）→ 受控执行 → 恢复验证（回查指标，未恢复回退/转人工）。
5. **证据链三必须**：每步可回溯原始工具输出；假设带 confirmed/rejected 状态；UI 一眼看懂（时间线+证据节点）。
6. **可观测数据质量决定 AI 上限**：Agent 调不好先查指标覆盖/日志关键错误/拓扑完整性，别先怪模型。剧本集里必须含 1 类"业务语义层"故障（如版本协议不兼容）。
7. **评测纪律**：开发集与保留集隔离防泄漏；判对错两级（规则匹配 → LLM-as-judge + 人工抽检 20%）；失败模式强制归类（tool_error/plan_error/timeout/hallucination/no_signal）；每次 commit 跑回归。
8. **成本四杠杆**（按有效性）：优化 plan > 规则先行 > 缓存+分层调用 > 减少异构系统；优化看"质量 × 延迟 × 成本"综合。
9. **RAG 定位**：RAG 用于"召回历史事故"，工具调用用于"查当前状态"；RAG 是工具不是架构。
10. **实测数据回填，不得虚构**；M3/M4/M7 不可砍。
11. **高危 git 命令**（`push --force` / `reset --hard` / `clean -f` / `branch -D` / `checkout .`）执行前列影响范围并获确认；不跳过 hooks。
12. **TDD 红绿循环 + 术语纪律**：测试只住约定接缝上；命名一律用 `CONTEXT.md` 词汇，新术语当场入表。

## 提交规范

中文 + type 前缀，body 写**为什么**而非做了什么：

```text
<type>(<scope>): <中文简述>

<body：为什么做这个改动>

<issue 引用，如 Closes .scratch/m3-investigation-loop/issues/03-promql-tool.md>
```

- `type`：`feat` / `fix` / `docs` / `chore` / `refactor` / `test` / `perf` / `ci`
- `scope`：模块名，如 `M3-调查循环`、`M7-评测台`、`M0-环境`
- 诊断类提交必须写明**最终被证实的那个假设**
- issue 引用写**文件路径**（本地 tracker 无数字编号）；关闭 issue 时同步把该文件的 `Status:` 改为对应值
- 提交前 checklist：全量测试 → code-review 双轴（Standards ∥ Spec 分开报告）→ 更新 `CONTEXT.md`（若有新术语）→ 更新相关 issue 状态 → 评估是否写 ADR → 评测回归（涉及时）
