---
title: "CONTEXT.md — 共享语言"
summary: "OnCall Copilot 的术语唯一权威：命名（代码/issue/测试/文档）一律用这里的词，新术语当场入表"
status: active
updated: 2026-09-07
read_when: 命名拿不准时；写 issue / 测试名 / 提交信息时；新增概念时
---

# CONTEXT.md — 共享语言

> **这是本项目术语的唯一权威。** 变量、函数、文件、issue、测试名、提交信息里出现的概念，一律用本表的词。
> 一个概念只允许有一个名字——同义词写在 `_Avoid_` 列，见到就换掉，不要创造第二个叫法。
>
> **职责边界**：本文件 = **定义**（短，供命名与对齐）。`docs/reference/glossary-resources.md` = **索引 + 外部资源**（数据集、工具、学习路径），它不重复定义，只回指这里。

---

## 一、领域概念

| 术语（中/英） | 定义 | _Avoid_ |
|---|---|---|
| 告警 / Alert | 监控规则触发的异常信号，本系统的**输入** | 报警、警告、事件 |
| 事件 / Incident | 经降噪分类判定为真实故障后建立的**排障对象**，一条事件对应一次完整调查 | 告警、故障、工单 |
| 风险 / Risk | 分类时无法判定的中间态，建档观察**但不丢弃**（0 漏报的保证） | 未知、待定 |
| 降噪 / Denoising | 把重复与误报警情归并或归档，只让真实事件进入调查 | 过滤（暗示丢弃）、清洗 |
| 告警指纹 / Fingerprint | 由规则+实例+时间窗算出的稳定哈希，用于把同源重复告警合并为一条 | 去重键、ID、签名 |
| 逻辑告警 / Logical Alert | 跨时间窗桶边界分行落库的同源连发，在**统计层**传递归并出的单一计数单位（canonical label 子集相同 + 相邻行间隔 ≤ `dedup_window`）；M1 落库行为不动（D-14），归并只在统计层 | 合并行、虚拟告警、聚合告警 |
| 归一化 / Normalization | 把外部告警源（Alertmanager webhook 等）的原始 payload 校验并映射为内部统一告警结构的过程，M1 的确定性环节 | 转换、标准化（口径过泛） |
| 事件卡片 / Alert Card | 归一化告警 + 三源上下文（近期指标/服务拓扑/近期变更）组装成的 JSON 契约，无 UI 阶段的验收载体；**不是**「事件/Incident」（M2 判定真实后才建档） | 事件（与 Incident 混用）、卡片 |
| 告警风暴 / Alert Storm | 同一根因在短时间窗内引发的大量告警 | 告警泛滥 |
| 根因定位 / RCA | 从多源取证中推断并证实导致故障的根本原因 | 故障定位、问题排查 |
| 证据链 / Evidence Chain | 调查中每一步 thought/tool/input/output 的有序落库记录，可回溯、可导出 | 日志、trace（trace 是分布式链路追踪，另一回事） |
| 假设 / Hypothesis | Agent 提出的待验证猜测，状态为 `confirmed` / `rejected` | 结论、猜想 |
| MTTR | 平均修复时间（Mean Time To Repair） | — |

## 二、Agent 架构

| 术语（中/英） | 定义 | _Avoid_ |
|---|---|---|
| ReAct 循环 | 本系统自研的主循环：观察证据 → 提出假设 → 选工具 → 执行 → 校验 → 决定继续或收束 | 工作流、DAG、编排（都暗示固定流程） |
| 工具 / Tool | 注册在 Tool Registry 中、可被 Agent 调用的能力单元（≥6 类） | 函数、API、插件 |
| Planner | 主循环中决定"下一步做什么"的决策组件 | 调度器、路由器 |
| Verifier | 判断当前证据是否足以证实或推翻假设的校验组件 | 校验器、裁判 |
| 记忆 / Memory | 本轮调查的**证据摘要**（非跨轮长期记忆） | 上下文、历史 |
| 步数上限 / Step Budget | 单次调查允许的最大循环轮数（本项目 **15**），超出即转人工 | 超时、重试次数 |
| 回退 / Fallback | 假设被推翻或工具失败时，换方向重新取证的策略 | 重试（重试是同一动作再来一次） |
| RAG | 检索增强生成。本项目**只用于召回历史事故**，是 Agent 的一个工具，不是架构 | 知识库（作为架构时） |
| 规则通道 / Rule Channel | M2 双通道中的确定性谓词预筛层：每条规则只判「误报直判」或「放行」，**不判真实**；能写成确定性谓词的才进规则通道 | 规则引擎、预分类、过滤器 |
| 规则名 / Rule Name | 规则通道中每条谓词规则的稳定标识，统计报告按其归因（落 `reason` 前缀 `[name]`）；初始集：`resolved_only_ghost`（resolved-only 幽灵通知）/ `maintenance_window`（维护窗口/静默期）/ `stale_replay`（重放/迟到期已失效） | 规则 ID、规则编号 |
| LLM 通道 / LLM Channel | M2 双通道中的 few-shot 兜底分类层：只处理规则未决的告警，结构化输出 `{verdict, confidence, reason}`，置信度低于阈值落风险 | 大模型分类、智能分类 |
| 双通道编排 / Dual-Channel Orchestration | M2 的分类编排顺序（issue 04 / G4）：**规则通道先行（命中即直判落库）→ 未决行进 LLM 通道 → 同一事务落 `classification_json` + status 翻 classified**；只由独立入口 `POST /classify` 触发，不串联 `/ingest`（R6）；已 classified 行跳过（幂等） | 分类流水线、串联分类 |
| few-shot 样本池 / Few-Shot Pool | LLM 通道 prompt 的示例样本集合：loader 只从 `datasets/golden/dev/` 取、按 golden 可选 `classification` 字段分组（缺省 incident）、每态 K 条；确定性产出（场景名字典序） | 示例库、模板池 |
| 验证剧本 / Validation Scenario | M2 端到端验收用的 3 个剧本：`slow-sql`（基础设施）/ `protocol-mismatch`（业务语义层）/ `false-positive-flap`（历史误报，issue 06 落地）；**永不进入 few-shot 样本池**（防自证泄漏） | 验收集（holdout 是另一个概念） |
| L0–L5 成熟度 | Agent 自主性分级（白鳝分级）。本项目目标水位 **L3 受控自动执行** | — |

## 三、安全与处置

| 术语（中/英） | 定义 | _Avoid_ |
|---|---|---|
| 白名单 / Allowlist | 安全护栏机制：只放行**显式许可**的操作 | 黑名单（本项目明确不用黑名单） |
| 三道防线 | 按有效性排序：只读接口/白名单 > 人工确认门 > 提示词 | — |
| 四道闸门 / Four Gates | 写操作必经：干跑 → 人工确认 → 受控执行 → 恢复验证 | 审批流程 |
| 干跑 / Dry-run | 第一道闸门：只打印将执行的命令与影响面，不实际执行 | 预演、试运行 |
| 人工确认门 / Human Confirmation Gate | 第二道闸门：在 **API 层**拦截，等人工批准后才执行 | 二次确认弹窗、审批流 |
| 恢复验证 / Recovery Verification | 第四道闸门：处置后自动回查指标确认恢复，未恢复则回退或转人工 | 健康检查 |
| Runbook / SOP | Markdown 定义的**处置**流程文档，被解析成可执行工具 | 剧本（剧本是故障场景） |
| 故障剧本 / Chaos Scenario | 预定义的**故障注入场景**（CPU 飚高/慢 SQL 等），含注入脚本 + 触发告警 + 预标注根因 | Runbook、用例 |
| 混沌注入 / Chaos Injection | 主动注入故障以产生真实告警与数据（Pumba / Chaos Mesh） | 故障模拟 |

## 四、评测

| 术语（中/英） | 定义 | _Avoid_ |
|---|---|---|
| 黄金集 / Golden Set | 预标注正确根因与期望处置的评测集 | 测试集（测代码 vs 测 Agent 行为） |
| 评测台 / Eval Harness | 跑剧本、对答案、出指标矩阵（命中/步数/耗时/成本/失败模式）的设施 | 评测框架 |
| Top-1 / Top-3 命中率 | 正确根因位于模型排序前 1 / 前 3 位的比例 | 准确率 |
| 失败模式 / Failure Mode | 失败**强制**归为五类之一：`tool_error` / `plan_error` / `timeout` / `hallucination` / `no_signal` | 错误类型 |
| 开发集 / 保留集 | 评测隔离：开发集调参，保留集只用于最终结论，防泄漏 | 训练集/测试集 |
| 降噪率 / Denoise Rate | 降噪效果核心指标：(R − I) / R，R = Σ `dedup_count`（有效 firing 投递数，D-15 口径，不是行数），I = 判为 incident 的逻辑告警数；R=0 不除零，报告标注「无有效投递」（D-20） | 噪声过滤率、压缩比 |
| 漏报 / Missed Alert | golden 标注 incident 的逻辑告警被判 false_positive 的数量，验收硬口径必须 = 0；risk 不算漏报（D-07 中间态），单列 risk_observed 观察（D-20） | 漏判、漏检 |
| 误报误判 / False Alarm | golden 标注 false_positive 的逻辑告警被判 incident 的数量——「冤枉真告警」的另一半口径，与漏报相对，单列观察 | 误杀、反向漏报 |

## 五、可观测

| 术语（中/英） | 定义 | _Avoid_ |
|---|---|---|
| PromQL | Prometheus 查询语言，查指标工具的底层 | — |
| RED / USE | 指标方法论：RED 面向请求（Rate/Errors/Duration），USE 面向资源（Utilization/Saturation/Errors） | — |
| OTel | OpenTelemetry，遥测数据标准 | — |
| 三支柱 | Metrics / Logs / Traces | 三遥测 |
| 业务语义层故障 | 指标日志都正常但业务已出错的故障（如版本协议不兼容）——剧本集必须包含 1 类 | — |

---

## 六、协作与追踪

| 术语（中/英） | 定义 | _Avoid_ |
|---|---|---|
| Issue 追踪器 / Issue tracker | 承载本仓库 issue 的工具，当前是 `.scratch/` 下的 markdown 文件 | backlog manager、backlog 后端 |
| Issue | 追踪器中的一个工作单元（bug / task / spec / 切片）。编号即文件名前缀 `<NN>`，如 `03` | ticket（仅在引用外部系统时才用）、待办 |
| Triage 角色 / Triage role | 状态机标签，一个 Issue **同时只带一个**；取值见 `docs/agents/triage-labels.md` | 优先级、状态（"状态"过于笼统） |
| 就绪可派工 / ready-for-agent | 验收标准可机械判定、可直接交给 Agent 离线执行的 Issue 状态 | 待开发、ready |
| 阻塞边 / Blocked by | Issue 顶部声明依赖的其他 issue 编号，全部 resolved 后才解除 | 依赖（过于笼统） |
| 前沿 / Frontier | 当前 open + 未阻塞 + 未认领的 issue 集合，编号小的优先 | 待办列表、任务池 |

## 如何新增术语

1. **当场入表**：会话中产生了新概念，立刻写进对应分类，不要等攒一批。
2. **先查后造**：造新词前先在本表搜一遍，能复用就复用。
3. **写清 `_Avoid_`**：新术语必须列出它要取代的旧叫法，否则纪律落不了地。
4. **同步 AGENTS.md 与测试**：若术语影响了硬规则（`AGENTS.md` 第 12 条）或测试命名，一并更新。
