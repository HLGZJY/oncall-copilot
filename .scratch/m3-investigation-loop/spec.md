# m3-investigation-loop · Spec

M3 自主根因调查循环（自研 ReAct）。**权威设计**：`docs/design/m3-investigation-loop-design.md`（status: **reviewed**，2026-09-08 G1–G9 全部评审定案、D-22–D-29 已登记；依据见该文档「评审依据」R1–R9）。

> **派工解冻**：G1–G9 已于 2026-09-08 全部评审定案（用户逐条拍板采纳推荐解，登记 D-22–D-29）；全部 8 票就绪态终审完成，按编号顺序派工。

## 目标

`POST /investigate {incident_id}` 启动自研 ReAct 单循环（D-03）：Planner JSON Mode 结构化输出 `{thought, next_tool, args}` 或 `{conclusion}` → ToolRegistry 执行六工具（30s 超时/重试 ≤2/截断 ≤2000 tokens）→ ContextManager 只让摘要进上下文（原始输出留 EvidenceStep）→ Verifier「规则每步 + LLM 低频裁决（≤3 次）」证伪导向校验假设 → 步数 15 硬安全阀兜底 escalate；全部证据以内存契约挂唯一可变状态 `InvestigationSession`，可导出 JSON 报告；失败模式六值强制归类。M3/M4 边界：M3 不建表（`evidence_steps`/`hypotheses` ORM 归 M4），M4 建表后契约不变。

## 关键契约（2026-09-08 评审定案，D-22–D-29；执行时不得擅改）

- **冻结输入只消费不推翻**：D-03（自研 ReAct）/ D-05（L3）/ D-07 / D-08（RAG=工具）/ D-12 / D-13 / D-16（三源形状）/ D-17（卡片 13 键）/ D-18（golden 同源）/ D-19（incidents 五字段 + classification_json）
- **架构冻结约束**：六组件职责（架构 §3.2）/ MAX_STEPS=15 硬编码 / 单工具 30s 重试 ≤2 / 权限三层 L0/L1/L2 / 系统提示 ≤1500 tokens / 输出截断 ≤2000 tokens + 落库指针 / 步数 ≥10 移出已证伪假设 / 失败模式归类（架构 §6）
- **Planner 协议（G1 推荐）**：JSON Mode + Pydantic `PlannerDecision`；`{thought, next_tool, args}` 或 `{conclusion}`；畸形重试 ≤2 → plan_error；超时 30s 不重试；异常族 harness 自持、语义逐字对齐 M2 `LLMOutputError`/`LLMTimeoutError`（C3 不许跨包 import）
- **六工具（G2 推荐）**：统一 `ToolResult{tool, status: ok|empty|error|unavailable, data, meta}`；query_metrics=Prometheus query_range、search_logs=Loki query_range、detect_anomaly=纯统计 v1（G3）、query_kb=unavailable stub（M6 接入）、get_topology=复用 oncall.context、execute_action=L2 永远拒绝 stub（M5 实装）
- **Verifier（G5 推荐）**：规则层每步 + LLM 裁决两时机（新假设提出/收束判定，≤3 次/调查），证伪导向独立 prompt；不生成新计划
- **C3 独立性**：harness 禁 import classify/ingest/api 等；工具层复用 oncall.context（不在禁列）；HTTP 走 `infra.http.Fetcher`（C4）；LLM SDK 只许 infra 收口（C5，真实 Planner client 落位实现票过评审）
- **数据纪律**：全程只碰 `datasets/golden/dev/`（holdout/ 禁读含 raw 切片）；验收 3 剧本 = cpu-spike / slow-sql / queue-backlog，判分基准 golden `root_cause`（D-18）
- **设计期零真实 LLM 调用**（2026-09-08 拍板，照 M2 先例）：mock/估算，不需要 API key；T8 真实调用前需用户确认 key
- **零写操作**：execute_action 为 L2 stub；四道闸门归 M5

## 任务序列

| Issue | 任务 | 对应设计文档 | Blocked by | 就绪态 |
|---|---|---|---|---|
| 01 | 调查会话契约与 Planner 接缝（session 契约 + MockPlanner + 异常族） | T1 | — | resolved（2026-09-08） |
| 02 | ToolRegistry 与权限分级（+ 两 stub 工具） | T2 | 01 | resolved（2026-09-08） |
| 03 | 取证工具实现（metrics/logs/anomaly/topology 四态） | T3 | 02 | resolved（2026-09-08） |
| 04 | ContextManager（摘要模板/预算/证伪假设移出） | T4 | 01 | resolved（2026-09-08） |
| 05 | Verifier（规则层 + LLM 裁决接缝） | T5 | 03, 04 | resolved（2026-09-08） |
| 06 | 主循环 Loop（终止三出口/失败六值归类/防绕圈四机制） | T6 | 01–05 | resolved（2026-09-08） |
| 07 | 调查入口 API（POST /investigate + GET 报告） | T7 | 06 | resolved |
| 08 | 端到端 3 剧本验证 + 真实实测回填收尾 | T8 | 07 | ready-for-agent（真实调用前确认 key） |

## M3/M4/M7 边界（spec 级写清）

- **M4**：`evidence_steps`/`hypotheses` ORM 建表 + 落库 + 报告持久化（替换进程内注册表）+ 导出 Markdown——M3 只冻结内存契约与写入接口
- **M5**：execute_action 实装 + 四道闸门（干跑→确认→执行→恢复验证）——M3 只留 L2 stub 与 PermissionGate 形态
- **M7**：Top-1/Top-3 矩阵、LLM-as-judge、人工抽检 20%、12 剧本全量回归、模型对比——M3 只交付 failure_mode/步数/成本等机械字段与 3 剧本首版验证

## 状态

- [x] G1–G9 评审拍板（2026-09-08 全部采纳推荐解；D-22–D-29 已登记 `decisions.md`，新术语已入 `CONTEXT.md`，设计文档翻 `reviewed`）
- [x] M3-A 内核：01 [x] · 02 [x]（2026-09-08）· 03 [x]（2026-09-08，M3-A 三票收官）
- [x] M3-B 编排：04 [x]（2026-09-08）· 05 [x]（2026-09-08）· 06 [x]（2026-09-08，M3-B 三票收官）
- [ ] M3-C 入口与验收：07 ✅ → 08（真实 LLM 调用需用户确认 key）
- [ ] 设计文档翻 `implemented`（T8 实测回填后）
