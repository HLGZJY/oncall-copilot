# m6-report-kb · Spec

M6 报告与知识库（闭环报告 + RAG 历史召回）（W5 后半，PRD §7-M6）。**权威设计**：`docs/design/m6-report-kb-design.md`（status: **reviewed**，2026-09-09 G1–G9 评审定案；评审依据见该文档 R1–R5）。

> **派工解冻**：G1–G9 已于 2026-09-09 全部评审定案（用户逐条拍板采纳推荐解，登记 `decisions.md` **D-49–D-57**，新术语入 `CONTEXT.md`）；按编号顺序派工。T7 真实召回 e2e 需**本地 embedding 模型 + 活 demo 栈同故障复现两遍**（环境门槛非 key 门槛），**开工前需用户确认环境就绪**。

## 目标

兑现 D-23 `query_kb` stub 为真实 RAG：调查终态且 incident `mitigated`（恢复验证实证）后，闭环报告按章节确定性拼装（读库禁虚构）并切块向量化入库（SQLite 第九表 `kb_chunks` 为权威 + Chroma 可重建索引）；新调查开局做一次确定性相似召回，历史案例作为**参考证据**（source=kb，非事实本身）进入证据链；相同指纹 + 实证闭环走缓存复用不重查。**知识污染三道防线**正面回答 PRD 追问点。

## 关键契约（2026-09-09 评审定案，D-49–D-57；执行时不得擅改）

- **冻结输入只消费不推翻**：D-08（RAG 是工具不是架构）/ D-23（六工具集合、`QueryKbInput{query, top_k}`、`ToolResult` 形状只消费不推翻——本票实装既有 stub）/ D-25/D-31/D-46（evidence_steps/investigations/remediation_proposals 冻结）/ D-35（报告 JSON 形状权威 = build_report 现契约）/ D-36（report.md 最小版 = 升级基线）
- **报告（D-49/G1）**：读库拼装不落报告表；每个数据点经 `kb_chunks.source_meta_json` 锚点可回溯库行；改进建议节 LLM 生成走双门槛门控（D-50/G2——env 开关缺省关，测试全走占位文案；真实调用是 key 门槛票单独拍板）
- **存储（D-55/G7 + D-52/G4 + D-51/G3）**：第九表 `kb_chunks`（id/incident_id FK/investigation_id FK nullable/section/seq/text/source_meta_json/hit_count/created_at/superseded_at）为权威，Chroma 只存向量索引可全量重建；embedding 拟用本地 bge-small-zh-v1.5——**新增依赖（sentence-transformers/chromadb）随 T3 依赖冒烟票走显式评审**，代码以 Embedder/VectorStore Protocol 可注换，Mock 驱动全部测试
- **切块（D-53/G5）**：五类 section（opening_card/timeline/root_cause/remediation/suggestions），报告结构即切分边界
- **召回（D-54/G6）**：开局确定性召回**不是 Agent 步**（与 collect_context 同构前置组装，不占 15 步预算）；query_kb 保持 Planner 自主；kb 节点标注 source=kb 语义为参考
- **入库（D-56/G8）**：门槛 = incident `mitigated`；未实证永不入库；触发 = 处置链收尾同步触发（失败落日志重试不阻塞）
- **缓存（D-57/G9）**：指纹精确命中（D-14 含桶）+ 源 mitigated → 复用整报告出口 `reused_from` 不重查；复用不更新 hit_count；未命中向量相似只作参考
- **污染防线**：①入库门槛（G8）②kb 证据不可独立证实假设（Verifier 规则层约束）③重复调查覆盖 investigations 旧行时 kb 旧块同步 `superseded_at`
- **架构守卫**：C3（harness 禁 import kb——query_kb 走 registry 既有 handler 注入接缝，Protocol/TYPE_CHECKING）/ C6 ≤300 行 / A6 bandit
- **数据纪律**：实测数字禁虚构；报告文本中每个数据点可回溯；holdout/ 禁读
- **TDD + mock-only**：MockEmbedder 确定性向量驱动测试；门禁基线 **682 passed / 12 skipped** 只增不减 + ruff 双检 + import-linter + bandit

## 任务序列

| Issue | 任务 | 对应设计文档 | Blocked by | 就绪态 |
|---|---|---|---|---|
| 01 | kb_chunks 第九表 + kb 模块骨架 + frozen-face 契约测试 | T1 | — | ready-for-agent |
| 02 | 闭环报告拼装器 + report.md 出口升级 + 建议节占位 | T2 | 01 | ready-for-agent |
| 03 | 切块 + 向量化 + 入库管线（依赖冒烟） | T3 | 01 | ready-for-agent |
| 04 | query_kb 实装 + 开局召回（缓存复用 + 向量参考） | T4 | 03 | ready-for-agent |
| 05 | 污染防线 + Verifier 纪律 | T5 | 04 | ready-for-agent |
| 06 | 门禁收口 + 架构 §4/§5 回写 + 文档 | T6 | 05 | ready-for-agent |
| 07 | 真实召回 e2e（环境门槛） | T7 | 06 | ready-for-agent（附门槛：本地 embedding 模型 + 活 demo 栈，开工前需用户确认） |
