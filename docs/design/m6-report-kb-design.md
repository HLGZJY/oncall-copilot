---
title: "M6 报告与知识库（闭环报告 + RAG 历史召回）：设计与开发计划"
summary: "兑现 D-23 query_kb stub 为真实 RAG：结构化事故报告（时间线/根因/处置/改进建议）读库拼装 → 章节切块向量化入库（Chroma + SQLite 第九表权威）→ 新调查开局确定性召回历史案例作参考证据（知识污染三道防线）；缓存重复问题判定边界；G1–G9 开放点评审与 T1–T7 拆票预告"
source: docs/prd.md §4/§7-M6 + docs/architecture/architecture.md §3.3/§4/§5 + docs/design/decisions.md D-08/19/23/28/31/35/36/39-48 + docs/design/m5-remediation-gates-design.md（设计文档形制模板）+ src/oncall/harness/tools/registry.py（query_kb stub 现状接缝）+ src/oncall/api/investigation.py（build_report / report.md 出口）+ docs/reference/_sources/项目信息.md 3.3 模块 E（只回溯：知识沉淀六步流程）+ 评审标准来源（见「评审依据」R1–R5）
status: draft
updated: 2026-09-09
read_when: 评审 M6 方案时；进入 M6 开发前；被问「RAG 在 Agent 里怎么用/知识污染怎么防/缓存重复怎么判」时
---

# M6 报告与知识库（闭环报告 + RAG 历史召回）：设计与开发计划

## status

`draft`（草案，讨论中）→ `reviewed`（评审通过，可拆票）→ `implemented`（已落地，验收回填）→ `superseded`（被后续设计取代，注明替代文档链接）

- **当前状态**：`reviewed`（2026-09-09 G1–G9 评审定案——用户逐条拍板，全部采纳推荐默认解；定案已登记 `decisions.md` D-49–D-57，新术语已入 `CONTEXT.md`；tracker 已建 `.scratch/m6-report-kb/`）
  - 上一状态 `draft`（2026-09-09 草案完成，提交 `ff99659`）
- **评审人 / 评审日期**：用户逐条拍板（G1–G9 全部采纳推荐默认解），2026-09-09；评审依据由 AI 检索官方标准提供（R1–R5，含 URL 与取用日期 2026-09-09），用户保留推翻权（推翻须回退 `draft` 并重开对应 issue）
- **设计期口径**：本票零写码、零建表、零真实调用。报告拼装是确定性读库行为（mock 即可测）；embedding 真实调用与「复现同一故障第二次首步引用历史案例」真实 e2e 需**本地 embedding 模型 + 活 demo 栈**（环境门槛，非 key 门槛——改进建议 LLM 润色是唯一可能涉 key 的面，见 G2）；设计期一律 mock。

## 目标

- **背景 / 触发原因**：M5 8/8 resolved 收官（四道闸门 + 第八表 + 活栈真实 e2e）。M6 = PRD §7-M6「报告与知识库」——闭环的最后一段（架构 §5 时序：恢复 → **闭环报告 → 向量化入库(M6)**）。现状两处明确承诺由本票兑现：① registry.py `query_kb_stub` docstring「调用即 unavailable，真实 RAG 留 M6」（D-23）；② D-36「时间线美化与改进建议归 M6」。
- **要解决的问题**（PRD §7-M6 + 母本模块 E 六步流程的 docs/ 提炼口径展开）：
  1. **结构化事故报告**：时间线/根因/处置/改进建议——数据来源 = investigations + evidence_steps + hypotheses + remediation_proposals **全部读库拼装、禁虚构**（P1 教训：timeline 禁捏造）；M4 的 `report.md` 是「证据链数据直出」最小版（D-36），M6 升级为面向人的闭环报告；
  2. **向量化入库**：报告切块 → embedding → 向量库，含增量更新与过期淘汰（investigations 表 incident 1:1 覆盖语义要求知识库同步覆盖，D-31）；
  3. **RAG 召回 = query_kb 工具实装**：召回历史案例作为**参考证据**进入新调查（证据链节点标注 kb 来源，非事实本身）；PRD 验收「复现同一故障第二次，Agent 首步即引用历史案例（可录屏为亮点）」；
  4. **知识污染防护**（PRD 面试追问点，必须正面回答）：RAG 在问答之外的 Agent 场景的不同 = 召回结果是**假设参考输入**而非判据，防历史错误结论自我强化；
  5. **缓存重复问题**：相同指纹事件的调查结果缓存复用与 RAG 召回的边界（母本 4.4 冲突一的落地形态）。
- **不做的事（Non-goals）**：
  - **不加第 7 工具、不改 D-23 冻结面**：六工具集合、`QueryKbInput{query, top_k}`、`ToolResult` 形状只消费不推翻（query_kb 实装即 D-23 预留位，不入新工具）
  - **不推翻 D-08**：RAG 只用于召回历史事故，查当前状态仍走工具调用；RAG 是工具不是架构（知识库召回的编排位置张力见 G6，在 D-08 框架内解决）
  - **不做知识图谱**：母本模块 E 的图谱向不进 v1（架构 §8 明确不做清单）；只做向量召回
  - **不做跨项目泛化知识库**：只沉淀本系统调查产生的闭环报告
  - **不做 M7 评测**：召回质量指标（命中率/引用正确率）只预留接口与数据面，评测矩阵归 M7
  - **不做 M8 UI**：报告展示页归 M8；本票报告出口 = REST + Markdown（D-36 出口升级，不建页面）
  - **不做 Alembic 迁移**：dev 单库延续 `create_all`（D-13/D-30 先例）
  - **设计期零写码**：不改 `src/`、`tests/`、`chaos/`、`datasets/`；`datasets/golden/holdout/` 禁读不变

## 本阶段涉及的技术类别

| 技术类别 | 涉及技术 | 本阶段用途 | 开放点 |
|---|---|---|---|
| 闭环报告拼装 | 读库序列化（复用 `db/views.py` + `build_report` 先例） | 时间线/根因/处置节确定性拼装；改进建议节 LLM 生成（门控） | LLM 润色门槛与落点（G2） |
| 报告切块 | 章节级切分（报告结构即切分边界，零 NLP 依赖） | 向量化入库的切块单元 | 切块粒度（G5） |
| Embedding | 本地中文模型（sentence-transformers / BGE 系）+ Chroma | 报告块向量化与相似检索 | 模型选型（G3）/ 新依赖评审 |
| 向量库 | Chroma（persistent local）vs pgvector | 向量索引与 top_k 召回 | 选型与存储权威划分（G4/G7） |
| 落库 | SQLAlchemy 第九表（显式偏差，随票回写架构 §4） | 知识块文本与元数据权威存储 | 表设计（G7） |
| 召回编排 | 调查开局确定性召回 + query_kb 工具 | 历史案例进新调查 | 触发时机（G6） |
| 知识污染防护 | 入库门槛 + 证据语义降级 + 淘汰机制 | 防 RAG 自我强化（PRD 追问点） | 淘汰机制（G8） |
| 缓存边界 | 指纹精确匹配 vs 向量相似 | 重复调查复用 vs 历史参考召回 | 判定规则（G9） |
| 工程门禁 | pytest TDD / ruff / import-linter C3–C6 / A6 bandit | 报告结构/切块/召回接缝可机械断言 | 测试策略沿用 M3–M5 方法（不设 G） |

## 技术方案

**一句话概括**：调查终态且处置实证恢复（incident `mitigated`）后，把闭环报告按章节确定性拼装（读库禁虚构）并切块向量化入库；新调查开局做一次**确定性相似召回**（比对既有告警指纹与标签，不经 LLM），召回结果作为**参考证据节点**（来源标 `kb`，语义 = 假设参考非判据）注入证据链，Planner 亦可在循环内经 `query_kb`（D-23 stub 实装为真实 RAG）按自然语言深查历史；相同指纹的重复事件走**缓存复用**（直接关联既有报告不重查），向量相似只作参考——两条通道边界清晰、各防一类重复。

### 主流程落位（架构 §5 时序的最后一段）

```
M5 恢复验证通过 → incident 翻 mitigated（D-40 链路）
   ▼
入库触发（G8 门槛）：mitigated 实证才入库——非实证报告永不进知识库
   ①报告拼装：读库组装时间线/根因/处置节（确定性，禁虚构）+ 改进建议节（LLM，门控 G2）
   ②切块（G5）：报告 → 章节 knowledge chunks（开局卡片摘要 / 时间线 / 根因 / 处置 / 建议）
   ③向量化（G3/G4）：embedding → Chroma 索引 + SQLite kb_chunks 权威行（G7）
   ▼
新调查（同源故障再来）
   ①开局召回（G6）：确定性比对指纹/标签 → 相似历史案例作为参考证据节点（source=kb）进证据链
      → PRD 验收「首步即引用历史案例」由该节点承载（非 LLM 行为，可 mock 可录屏）
   ②循环内 query_kb（实装）：Planner 自主深查（L0 只读全放行），ToolResult{ok, data: kb_hits}
   ③Verifier 纪律：kb 来源证据单独计数，**不足独立证实假设**（知识污染第二道防线）
```

**关键设计支点**：

1. **知识污染三道防线**（正面回答 PRD 追问点；R5 是外部标准印证——OWASP 已把「向量与嵌入弱点 / 知识库投毒」列为 LLM 应用 Top 10 风险之一）：
   - **第一道（入库门槛）**：只有 incident 到达 `mitigated`（恢复验证实证）的报告才入库——错误调查结论没有入库通道；pending/escalated/failed 一律不入。
   - **第二道（证据语义降级）**：kb 召回结果在证据链中标注 `source=kb`、语义为「历史相似案例（参考）」；Verifier 规则层约束——**kb 证据不能作为唯一支撑证实假设**，必须与当前调查的本源证据（指标/日志/拓扑）共同支撑（假设-证据引用存在性校验扩展，D-25/D-26 接缝）。历史案例只作假设参考不作判据。
   - **第三道（淘汰与覆盖）**：同一 incident 重复调查覆盖 `investigations` 旧行（D-31），知识库同步覆盖旧块（不叠加）；召回命中但后续调查推翻其根因结论时，旧块随重调查覆盖自动失效；命中质量反馈接口预留 M7（低命中率淘汰，母本模块 E 闭环的最后一段，接口预留本票不实现）。
2. **缓存重复问题判定规则**（PRD §7-M6 列名问题，边界一刀切在指纹上）：
   - **指纹精确命中**（D-14 指纹含时间窗桶，跨桶同源由统计层逻辑告警归并口径对齐 D-20）：该告警此前已产生 `mitigated` 闭环 → **缓存复用**：不建新调查，incident 直接关联既有报告与知识条目，调查出口标注 `reused_from`——确定性规则，不经 LLM 不经向量；
   - **指纹未命中但向量相似**（top_k ≥ 阈值）：不缓存、正常开调查，召回结果只作参考证据——相似 ≠ 相同，宁多查一次（0 漏报方向的保守选择，与 D-14「桶边界保守不漏收」同向）；
   - 判定落点 = 调查入口（`POST /investigate` 前置确定性检查），不是 Planner 决策——缓存是系统优化不是 Agent 行为，硬规 1 的「模型自主」不适用。
3. **召回触发时机的张力与解**（G6 核心）：硬规 1 要求步骤由模型自主决定；但 PRD 验收要求「首步即引用」。解 = **开局召回不是「步」**——与 `collect_context`（D-16 确定性三源上下文前置组装）同构：调查开局做一次确定性指纹/标签相似比对，命中即作为参考证据随开局锚点注入（不占 15 步预算、不经 LLM）；`query_kb` 工具保持 Planner 自主决策用于深查。这样 PRD 验收是确定性系统行为（mock 可断言、可录屏），而「要不要进一步查历史」仍是模型自主。
4. **C3 依赖方向**：`oncall.knowledge`（C3 预留位 `oncall.knowledge`，pyproject 零改动）（新模块，命名预留给 pyproject C3 禁列评审）与 remediation 同构——harness 侧 query_kb 只留 handler 注入接缝（`QueryKbInput` → 检索函数 Protocol），kb 实现经 `app.py` 组装注入；依赖方向 kb → db（第九表）、api → kb（报告出口与入库触发）合法。embedding/Chroma 依赖只进 kb 模块，harness/api 零感知。

### 设计的模块

| 模块 | 动作 | 职责 | 目录 | 关联里程碑 |
|---|---|---|---|---|
| 闭环报告拼装器 | 新增 | 读库拼装：时间线（alert_events 实测 firing 序列）/ 根因（hypotheses confirmed + supporting steps）/ 处置（remediation_proposals 全链）/ 改进建议（LLM 门控节）；Markdown 渲染升级 `report.md`（D-36 出口的 M6 版） | `src/oncall/knowledge/report.py`（预算 ≈200 行） | M6 → M7 评测样本源 / M8 报告页 |
| 报告切块器 | 新增 | 闭环报告 → 章节 knowledge chunks（块 = 章节边界 + 开局卡片摘要，G5）；每块带 incident_id/section/seq 元数据 | `src/oncall/knowledge/chunking.py`（预算 ≈80 行） | M6 |
| 向量化与索引 | 新增 | embedding 封装（可注入：测试用 MockEmbedder / 真实模型）+ Chroma persistent 客户端（G4）+ 增量 upsert / 覆盖删除 | `src/oncall/knowledge/embedder.py` + `src/oncall/knowledge/store.py`（预算 ≈220 行） | M6 → M7 召回质量评测 |
| query_kb 实装 | 改造 | stub → 真实 RAG handler：QueryKbInput{query, top_k} → 向量检索 → ToolResult{ok, data: kb_hits[]（含 incident_id/section/text/score/source=kb）}；未注入检索函数时维持 unavailable stub 语义（既有测试零回退） | `src/oncall/harness/tools/registry.py`（handler 注入面）+ `src/oncall/knowledge/retriever.py`（预算 ≈100 行） | M6 |
| 开局召回器 | 新增 | 调查入口前置：指纹精确命中 → 缓存复用出口（G9）；未命中 → 标签/故障类型相似比对 → kb_hits 作为参考证据节点（source=kb）随 opening 注入 | `src/oncall/knowledge/recall.py`（预算 ≈120 行） | M6 |
| 入库编排 | 新增 | mitigated 事件后触发（G8）：拼装 → 切块 → 向量化 → 落库；investigations 覆盖时同步覆盖 kb 旧行（D-31 衔接）；未实证不入库 | `src/oncall/knowledge/pipeline.py`（预算 ≈150 行） | M6 |
| 报告出口 API | 改造 | `GET /investigations/{incident_id}/report.md` 升级为闭环报告版（新增时间线美化/处置节/建议节）；`GET /kb/incidents/{incident_id}` 知识条目查询（M7/M8 数据面） | `src/oncall/api/kb.py`（新文件，预算 ≈120 行） | M6 → M8 |

### C3/C6 论证

1. **harness → kb 零静态依赖**：query_kb 沿用 M5 execute_action 同款 handler 注入接缝先例（registry 既有 handlers 映射 + `app.py` 组装注入）；kb 检索函数以 Protocol/TYPE_CHECKING 声明，import-linter 静态面不新增 harness→kb 边（pyproject C3 禁列随票增补 `oncall.knowledge`（C3 预留位 `oncall.knowledge`，pyproject 零改动），与 `oncall.remediation` 先例一致）。
2. **api → kb / kb → db 单向合法**：报告出口与入库触发在 api 层 import kb；kb import `oncall.db`（第九表落库）合法；remediation → kb 不需要（入库读 remediation_proposals 表，走 db 层，不 import remediation 模块）。
3. **C6 行数预算**：模块表最大 report.py ≈200 < 300；registry.py 增 handler 注入 ≈15 行（现 299 行内余量核对随票做，超出则按 M5 先例拆 `harness/tools/kb.py` 新文件）；loop.py **零净增**（召回在入口、query_kb 走既有工具分派）。
4. **A6/C2 面**：embedding 与 Chroma 是新增三方依赖（`sentence-transformers` + `chromadb`）——走 M5 D-43 同款「C2 指零新依赖，新增须显式评审」路径（G3/G4）；无 subprocess 面，bandit 增量预期为零。

### 数据模型变更

| 变更项 | 类型 | 说明 | 迁移方式 |
|---|---|---|---|
| `kb_chunks` | **新增表（第九表，超架构 §4 八表清单，须评审 + 回写架构）** | 一个知识块一行：`id, incident_id(FK→incidents, 一对多), investigation_id(FK, nullable——复用出口可无新调查), section(opening_card/timeline/root_cause/remediation/suggestions), seq, text, source_meta_json(报告版本/生成时间/拼装来源表行 id 锚点——可回溯禁虚构), hit_count(命中计数，M7 反馈预留), created_at, superseded_at(nullable——覆盖淘汰标记)` | `create_all`（dev），Alembic 延至 MySQL |
| `investigations` | **无变更**（D-31 冻结） | 闭环报告读多表拼装，不落新列；报告版本锚 `kb_chunks.source_meta_json` | — |
| `remediation_proposals` / `evidence_steps` / `hypotheses` | **无变更**（D-25/D-46 冻结） | 报告与切块的**读**数据源，只消费不扩列 | — |
| Chroma collection | **新增外部索引（非 SQLite 表）** | 向量 + metadata（incident_id/section）；**权威在 `kb_chunks` 表**，Chroma 索引可由表全量重建（删库重建脚本随票提供，G7 定案后是纪律：以 SQL 为权威，向量库只是索引） | 重建式迁移 |

### API 变更

| API | 变更类型 | 请求/响应要点 | 影响调用方 |
|---|---|---|---|
| `GET /investigations/{incident_id}/report.md` | 升级（D-36 最小版 → 闭环报告版） | 响应仍 `text/markdown`；内容增时间线节/处置节/建议节；既有证据链节不删（M7 样本源兼容）；建议节在 LLM 未启用时落「改进建议：见 runbook / 待人工补充」占位 | M7 评测 / M8 报告页 |
| `GET /kb/incidents/{incident_id}` | 新增 | 知识条目（块列表 + 元数据 + hit_count）；404 = 未入库（未实证或从未闭环） | M7 召回质量评测 / M8 知识库页 |
| `POST /investigate` | 行为扩展（契约不破） | 命中缓存复用时返回既有报告 + `reused_from` 标注（200 语义不变）；未命中开局注入 kb 参考证据节点（报告 JSON 增可选 `kb_reference` 键，键集合扩展须走 D-35 同款契约测试修订） | M8 UI / 评测台 |

## 验收标准

> 可实测、可判定；实测后回填打勾，不得虚构。设计期 mock-only（MockEmbedder 确定性向量），真实召回 e2e 留 T7（环境门槛：本地 embedding 模型下载 + 活 demo 栈复现同故障两遍，开工前需用户确认）。

- [ ] **闭环报告五节齐全且禁虚构**（PRD §7-M6）：时间线节逐条可回溯 alert_events 行 id、根因节 = confirmed hypotheses + supporting steps、处置节 = remediation_proposals 全链、建议节门控输出——单测断言报告文本中每个数据点可经 `source_meta_json` 锚点回查库行（T 期实现票）
- [ ] **复现同一故障第二次，首步即引用历史案例**（PRD 验收硬口径，可录屏为亮点）：mock 侧——同剧本第二遍调查开局证据链含 `source=kb` 参考节点且文本来自第一遍入库报告（确定性断言）；真实侧——活栈同故障注入两遍，第二遍报告含 kb 引用（T7，环境门槛）
- [ ] **指纹精确命中走缓存复用不走重查**：同指纹（D-14 口径）+ 既有 mitigated 闭环 → 新告警不建新调查、出口 `reused_from` 关联既有报告；指纹未命中但向量相似 → 正常开调查只作参考（单测断言两条路径互斥，G9）
- [ ] **知识污染三道防线可机械断言**：①未实证（investigating/escalated）事件入库调用被拒（G8 门槛测试）；②kb 证据单独不能证实假设（Verifier 规则层单测：仅 kb 支撑的假设证实被拒并提示补本源证据）；③重复调查覆盖 investigations 旧行时 kb 旧块同步 superseded（覆盖语义测试）
- [ ] **D-23 冻结面不破**：六工具集合、`QueryKbInput{query, top_k}` 形状、`ToolResult` 形状、loop 主循环结构不变（import + 键集合断言，M5 `test_m5_frozen_face` 同款形制）；未注入检索函数时 query_kb 维持 unavailable stub（既有测试零回退）
- [ ] **全量门禁只增不减**：pytest（基线 **682 passed / 12 skipped** 只增不减）+ ruff 双检 + import-linter C3–C6 + bandit 全绿，coverage ≥80%（harness 单独 ≥85%）（设计票收尾复跑确认未污染）
- [ ] **真实召回 e2e 回填**（T7）：同故障两遍的召回命中/得分/耗时如实回填验收节与 issue Comments（禁虚构）；设计文档翻 `implemented`、D-49+ 落位核对、架构 §4 回写（第九表 + 时序图 M6 落点）核对

## 依赖

- **前置依赖**：M5 8/8 resolved ✅（处置留痕第八表 + 恢复验证 + incident 翻 mitigated）；D-08（RAG 定位）/ D-23（query_kb stub 接缝）/ D-28（终态语义）/ D-31（investigations 覆盖语义 = kb 覆盖触发源）/ D-35（报告 JSON 形状权威）/ D-36（report.md 最小版 = 本票升级基线）/ D-39–D-48（处置留痕读源）只消费不推翻
- **数据 / 环境依赖**：SQLite dev 库 `oncall.db`；Chroma persistent 目录（dev `./chroma_data/`，不入版本库，`gitignore` 随票）；**T7 需本地 embedding 模型**（G3 选型下载，无 key 门槛）+ 活 demo 栈同故障复现两遍
- **后续影响**：知识条目数据面是 M7（召回质量矩阵：命中率/引用正确率/污染防护有效列）、M8（报告页 + 知识库页）的直接输入；`hit_count` 与反馈接口是母本模块 E「命中率反馈淘汰」闭环的预留接缝

## 开放设计点（评审 grill）

> G1–G9 已于 2026-09-09 **评审定案**：用户逐条拍板全部采纳推荐默认解，登记 `decisions.md` **D-49–D-57**（每条 D 的「理由」栏为该 G 行核心理由的决策形态，ADR 三判据逐条评估——无全中项，均留快速索引层不升级 ADR）。外部标准来源见节末「评审依据」R1–R5（URL + 取用日期 2026-09-09）。

| # | 开放点 | 推荐默认解 | 理由 | 依据 |
|---|---|---|---|---|
| G1 | 闭环报告落库形态：报告整体落新表 vs 读库拼装不落表（出口即拼装） | **读库拼装不落表**：报告 = 出口处对 investigations/evidence_steps/hypotheses/remediation_proposals/kb_chunks 的确定性序列化；`kb_chunks.source_meta_json` 锚定拼装来源行 id 供回溯。不扩 investigations 冻结面（D-31），不建「报告表」 | 报告数据 100% 已在各表（M1–M5 落库形态完备），落报告表 = 引入可漂移的第二副本（M0 双权威教训）；D-35 已定「M3 build_report 现形状为权威、读库序列化不倒改」，本票延续同款哲学；改进建议节（LLM 生成）不落表随出口渲染，若 M7 需评测建议质量再议落点 | D-31/D-35/双权威教训 |
| G2 | 改进建议（PRD 报告四节之一）生成方式：确定性模板 vs LLM 生成；LLM 门槛形态 | **LLM 生成、双门槛门控**：建议节是语义综合任务（从证据链提炼防复发建议），模板无信息量；门控 = ①env 开关（缺省关，测试/CI 全走占位文案）②实现票口径照 M5「环境门槛 vs key 门槛」区分——建议生成是**key 门槛票**（真实 LLM 调用，唯一例外，须单独拍板）；设计期与 mock 测试一律占位 | 改进建议无确定性数据源，硬拼模板是伪内容（D-18 教训对偶：无源不虚构）；M2–M5 mock-only 纪律不破——mock 契约（输入证据链摘要 → 输出结构化建议）先冻结，真实调用是可后挂的注换实现；与「报告五节禁虚构」不冲突：建议节显式标注「AI 生成建议」非事实陈述 | M5 T8 门槛口径 + PRD §7-M6 + mock-only 纪律 |
| G3 | embedding 模型选型：本地中文小模型 vs 云 API vs BGE-M3 全量 | **本地 `BAAI/bge-small-zh-v1.5`**（sentence-transformers 加载，≈100MB，CPU 可跑，中文检索效果好）：新增 `sentence-transformers` 依赖须评审（C2 面）；云 API 违背零 key 调查主链路纪律；BGE-M3（≈2GB）质量更高但本机内存预算超配，留升级位 | 技术栈地图 L3 层点名 BGE-M3 为示例——small-zh 是同族轻量档，本地零 key、dev 机（16GB）无压力；场景是短文本（章节块）相似检索，small 档够用；模型封装在 `kb/embedder.py` 可注换，升级 M3 无契约代价。R2 官方卡：该模型中文检索任务表现同族前列 | C2 + 硬规 10 + R2 |
| G4 | 向量库选型：Chroma（persistent local）vs pgvector | **Chroma persistent local**：技术栈地图二选一中的本机档——嵌入式、零服务、Python 原生；pgvector 需 Postgres，与 SQLite dev 栈割裂（D-30 单库单事务理由在此反号：向量索引不参与调查事务，可重建，割裂无代价） | Chroma 官方定位即本地嵌入式原型库，与「SQLite 起步」同哲学（R1）；pgvector 叙事留「切 MySQL 时可平移」的一句迁移注记即可（D-04 接缝哲学）；新增 `chromadb` 依赖须评审 | D-30 哲学对偶 + R1 |
| G5 | 报告切块粒度：整报告一块 vs 章节切块 vs 固定 token 窗口滑切 | **章节切块**（opening_card 摘要 / 时间线 / 根因 / 处置 / 建议 五类 section，各 1–2 块）：报告结构即切分边界，零 NLP 依赖；召回用途对齐——Planner 需要的是「这个案例的根因/处置」不是全文 | 整报告一块 = 召回后塞进上下文的token浪费且含无关节；固定窗口滑切切断章节语义且依赖 tokenizer；章节块天然带结构化 metadata（section 类型），召回可按 section 过滤（如只召回根因节）——检索质量与上下文预算双赢（R3：结构化分块优于朴素滑窗） | 上下文预算（D-25 纪律）+ R3 |
| G6 | 召回触发时机：调查开局确定性自动召回 vs 纯 Planner 自主 query_kb vs 纯自动（kb 结果直接当事实） | **混合：开局确定性召回一次（不占步）+ query_kb 留 Planner 自主深查**：开局召回与 collect_context（D-16）同构——上下文组装是确定性前置不是 Agent 步；query_kb 保持模型自主（硬规 1）；kb 参考节点语义永远是「参考」非事实 | PRD 验收「首步即引用」要求召回不能赌 Planner 第一步恰好调 query_kb（LLM 行为不确定，验收会 flaky）；确定性前置是 mock 可断言、录屏可复现的系统行为；「纯自动当事实」违反 D-08（污染主线）；张力注记：硬规 1 说「步骤由模型决定」，本解的回答是**召回不是步**——与 M2 规则通道先行（确定性预筛不进模型）同一取舍方向 | D-08/D-16/硬规 1 + PRD 验收口径 + R4（Agentic RAG 检索时机可前置亦可 agent 决策，混合是主流） |
| G7 | 存储权威划分：SQLite `kb_chunks` 第九表为权威 + Chroma 可重建索引 vs Chroma 单一权威（文本向量化后只存向量库） | **SQLite 权威 + Chroma 只作向量索引**：块文本/元数据/命中计数落第九表（可 SQL 审计、可 join incidents 回溯），Chroma 只存向量 + 指回块 id；提供全量重建脚本，索引损坏零数据损失 | 「可回溯、禁虚构」要求文本权威在版本控制的 SQL 侧；Chroma 是嵌入式库非数据库（R1 官方口径：prototype→production 需外挂持久化纪律），拿它当唯一权威把审计面外包给索引组件是伪简洁；第九表 = 超架构 §4 八表规划的显式偏差，走 D-31/D-46 同款「评审 + 随票回写架构」通道 | D-31/D-46 先例 + R1 |
| G8 | 入库门槛与触发：什么事件值得入库；触发点（恢复验证通过即入 vs 出口手动 vs 定时批） | **门槛 = incident `mitigated`（恢复验证实证，D-40 链路自然落点）；触发 = confirm 同步链收尾后同步触发（failure 不阻塞处置出口，落日志重试）**：未实证（investigating/escalated/failed）永不入库——错误结论没有入库通道；escalated 案例即使人工后续解决，留 M7 人工标注通道再入，本票不自动入 | 母本模块 E「增量更新 + 过期淘汰」的前提是入库内容可信——知识污染第一道防线就是把关在入库口（R5：LLM 应用知识库投毒风险，入口治理优于出口过滤）；同步触发免去后台任务/队列复杂度，失败重试是日志级运维事项非设计分叉 | D-40 链路 + 母本模块 E + R5 |
| G9 | 缓存重复判定与命中语义：指纹精确命中复用的粒度（复用整报告 vs 复用根因节）；复用是否允许被新证据推翻 | **指纹精确命中（D-14 含桶口径）+ 源事件 mitigated → 复用整报告关联出口（`reused_from`），不重查**；复用报告中的 kb 块不更新 hit_count（复用 ≠ 召回，M7 评测口径分离）；**指纹命中但源事件非 mitigated → 不复用，正常开调查**（无可信结论可复用） | 缓存的意义是省一次调查（步数/成本/时延），整报告复用收益最大且不引入「部分复用」的拼装复杂度；0 漏报纪律：复用前提是既往结论已实证，否则宁可重查（保守方向，与 D-14 桶边界同向）；hit_count 只统计真实召回参考（向量通道），保 M7「召回质量」指标纯净 | D-14/D-20 指纹口径 + 0 漏报纪律 + M7 评测面 |

### 评审依据（官方标准来源）

| # | 来源 | 取用要点 | URL | 取用日期 |
|---|---|---|---|---|
| R1 | Chroma 官方文档 | persistent client 形态（`PersistentClient`）、collection/metadata 模型、官方对「嵌入式原型库，production 需外挂持久化与重建纪律」的定位 | https://docs.trychroma.com | 2026-09-09 |
| R2 | BAAI bge-small-zh-v1.5 官方模型卡（Hugging Face） | 中文检索/相似度任务表现、sentence-transformers 加载方式、模型规格（≈24M 参数 / 512 token 窗口——章节块长度适配） | https://huggingface.co/BAAI/bge-small-zh-v1.5 | 2026-09-09 |
| R3 | Anthropic 工程博客（contextual retrieval / RAG for agents 系） | 结构化分块优于朴素滑切；召回结果作为参考上下文注入而非事实判据；Agent 场景检索时机可前置亦可由 agent 决策（混合主流） | https://www.anthropic.com/news/contextual-retrieval | 2026-09-09 |
| R4 | LangGraph / Agentic RAG 官方教程 | Agentic RAG 与传统 RAG 的差异点（检索是工具、时机可自主可前置）——D-08 论述的外部印证 | https://langchain-ai.github.io/langgraph/tutorials/rag-agents/ | 2026-09-09 |
| R5 | OWASP Top 10 for LLM Applications（LLM08: Vector and Embedding Weaknesses） | 知识库投毒/向量污染防护：入口治理（只收可信来源）、检索结果语义隔离（不当事实）、覆盖与淘汰机制——本设计三道防线的外部标准印证 | https://genai.owasp.org/llm-top-10/ | 2026-09-09 |

## 开发计划（任务拆解）

> 预告形制：评审通过后才建 `.scratch/m6-report-kb/` tracker 并落 issue 文件；此处 T1–T7 是拆票预告非 issue。

| # | 任务 | 内容 | 依赖 |
|---|---|---|---|
| T1 | 第九表 + kb 模块骨架 | `kb_chunks` ORM + `oncall.knowledge`（C3 预留位 `oncall.knowledge`，pyproject 零改动） 包（C3 禁列随票增补 pyproject）+ frozen-face 契约测试（D-25/D-31/D-46 冻结列不动断言） | — |
| T2 | 闭环报告拼装器 + 出口升级 | `kb/report.py` 读库拼装 + `report.md` 升级 + `source_meta_json` 回溯锚点断言 + 建议节占位（G2 mock 契约冻结） | T1 |
| T3 | 切块 + 向量化 + 入库管线 | `chunking.py`（G5 章节切分）+ `embedder.py`（MockEmbedder/真实可注换）+ `store.py`（Chroma + 权威同步）+ `pipeline.py`（G8 门槛 + 覆盖淘汰） | T1 |
| T4 | query_kb 实装 + 开局召回 | `retriever.py` + registry handler 注入 + `recall.py`（指纹缓存复用 + 向量参考召回两通道，G6/G9） | T3 |
| T5 | 污染防线 + Verifier 纪律 | kb 证据单独不可证实假设的规则层约束 + 覆盖同步 superseded + 未实证拒入库测试（三道防线机械断言） | T4 |
| T6 | 门禁收口 + 文档 | 全量门禁复跑 + `docs/README.md`/CONTEXT.md 术语收尾 + 架构 §4/§5 回写（第九表 + 时序 M6 落点） | T5 |
| T7 | 真实召回 e2e（环境门槛票） | 本地 embedding 模型 + 活 demo 栈同故障两遍：首步引用断言 + 召回得分/耗时回填（开工前需用户确认环境就绪） | T6 |

## 风险清单（评审随附，交用户复核后才可派工实现票）

1. **sentence-transformers 依赖树重**（torch 传递依赖 ≈2GB 磁盘）——G3 评审时给「torch CPU 版指定源安装」备注；若否决，降级方案 = 纯 hash 词面相似（信息检索质量明显降，验收「相似召回」可能不可达，须重议 PRD 验收口径）。
2. **Chroma SQLite 冲突**：chromadb 内嵌 sqlite 版本与 SQLAlchemy 生态偶有 pysqlite3 冲突报告（社区 issue）——T3 先做依赖冒烟，冲突则隔离 chroma 到子进程或退 pgvector 重评审。
3. **开局召回上下文预算**：kb 参考节点随 opening 注入会挤 token 预算——块只注入 `opening_card` 摘要节 + 根因节（各 ≤500 字符），全文经 query_kb 或 `GET /kb/...` 按需取；预算断言复用 D-25 上下文纪律测试。
4. **PRD 验收「录屏亮点」依赖 T7 环境**：活栈两遍注入的时延（每遍 ≈2–3min）与 embedding 模型首载耗时需预留；若环境不可用，mock 侧断言已覆盖逻辑正确性，录屏亮点降级为「mock 演示 + 实测数据回填」如实注记（不虚构）。
5. **缓存复用与评测混淆**：M7 评测台跑同剧本时会命中缓存复用出口、跳过真实调查，污染指标矩阵——评测入口须带 `skip_cache` 旁路（T4 预留参数，M7 票面注记）。

## 评审后动作（2026-09-09 已执行完毕）

- [x] 登记定案决策至 `docs/design/decisions.md`（D-49–D-57，每条 G 对应一行，ADR 三判据评估见 decisions 表）
- [x] 新术语入 `CONTEXT.md`（Agent 架构节：**知识块 / KB Chunk**、**闭环报告 / Closed-Loop Report**、**开局召回 / Opening Recall**）
- [x] 更新 `docs/README.md` 索引行（随草案先行，见本次提交）
- [x] 建 `.scratch/m6-report-kb/` tracker（spec.md + issues/01–07，与 T1–T7 对应；T7 标环境门槛注记）
- [ ] 架构 §4 第九表 + §5 时序 M6 落点回写（随 T1 实现票）
