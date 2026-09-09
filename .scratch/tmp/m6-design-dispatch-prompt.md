# M6 设计文档派工 prompt（报告与知识库：设计期，零写码）

执行 oncall-copilot M6 设计文档：**只写设计、零写码、零建表、零真实调用**，产出 `docs/design/m6-report-kb-design.md`（status: `draft`）+ G 开放点清单供用户评审；评审定案前不拆票、不动 `src/`。

## 环境与基线
- 仓库：`F:\Git repository\oncall-copilot`（main）。Python/工具全用绝对路径（venv `C:/Users/heguo/.workbuddy/binaries/python/envs/default/Scripts/`）
- 门禁基线：pytest **682 passed / 12 skipped** 只增不减 + ruff 双检 + bandit 全绿（设计票不改码，收尾仍跑一遍确认未污染）
- 严格遵循 `AGENTS.md` 12 条硬规；术语一律用 `CONTEXT.md` 词汇，新术语当场入表；提交规范中文 + type 前缀 + issue/文档路径引用

## 必读（顺序，先读再写）
1. **总纲**：`AGENTS.md`、`CONTEXT.md`
2. **需求口径**：`docs/prd.md` §7-M6（报告生成：时间线/根因/处置/改进建议；历史报告切块向量化入库；新告警调查时自动召回相似历史作参考；**缓存重复问题**——PRD 面试追问点：「RAG 用在问答之外的 Agent 场景有什么不同？知识污染怎么防？」）
3. **路线图**：`docs/plans/roadmap-m0-m9.md` M6（P1，W5 后半）与裁剪顺序
4. **架构**：`docs/architecture/architecture.md`——闭环报告 → 向量化入库(M6) 的主循环落点、§4 表规划（M6 可能要新增表，凡超八表规划的偏差须显式注记并预告回写）
5. **决策（权威）**：`docs/design/decisions.md`——**D-08（RAG 定位：召回历史事故用 RAG，查当前状态用工具调用，RAG 是工具不是架构）**、**D-23（六工具中 `query_kb = unavailable stub，M6 接入真实 RAG`——本票即兑现该 stub 承诺，不加第 7 工具）**、D-31（investigations 第七表，报告与调查行的衔接点）、D-39–D-48（M5 定案，注意与处置留痕的衔接）
6. **设计形制模板**：`docs/design/m5-remediation-gates-design.md`（frontmatter source 链、status 演进、目标/Non-goals、模块表、G 开放点表、R 评审依据表、验收标准节、拆票节——**照此形制**）与 `docs/design/feature-design-template.md`
7. **母本回溯（只读不引用）**：`docs/reference/_sources/项目信息.md` 3.3 模块 E（知识沉淀六步流程）、4.4 冲突一、6.4 证据链结构
8. **现状接缝**：`src/oncall/` 中 `query_kb` stub、M4 evidence chain 三表 + investigations 第七表、M5 处置留痕（`remediation_proposals` 等）、M1–M4 的落库形态（report 入库须与既有表外键衔接）

## 设计要覆盖的核心问题（每处给候选方案 + 推荐默认解，交用户 G 评审拍板）
1. **报告生成**：结构化事故报告（时间线/根因/处置/建议）的数据来源 = investigations + evidence chain + remediation 留痕（**禁虚构，全部读库拼装**）；报告生成是否走 LLM 润色（注意：设计期 mock-only 纪律，真实 LLM 调用票的门槛如何设——照 M5「环境门槛 vs key 门槛」的口径区分）；报告落库形态（新表 or investigations 扩展 → 涉架构 §4 偏差须显式注记）
2. **向量化入库**：切块粒度（整报告 vs 章节）；embedding 选型（技术栈地图列了 Chroma/pgvector 二选一，本地中文 embedding 模型选型给推荐）；增量更新 + 过期淘汰（母本模块 E 要求）
3. **RAG 召回 = query_kb 工具实装**：兑现 D-23 stub；召回结果作为**参考证据**进入新调查（证据链节点标注来源 kb，非事实本身——知识污染防护的第一道）；召回触发时机（调查开始自动召回 or Planner 决策调用——硬规 1 模型自主 vs 确定性前置的取舍）
4. **知识污染防护**（PRD 追问点，必须正面回答）：历史案例只作假设参考不作判据；报告只在与 incident 强关联（mitigated/recovered 实证）后才入库；错误报告的淘汰机制
5. **缓存重复问题**：PRD §7-M6 列的「缓存重复问题」——相同指纹/相似事件的调查结果缓存与 RAG 召回的边界，给清晣的判定规则
6. **测试与评测面**：报告结构断言（确定性，mock 即可测）；RAG 召回质量评测预留 M7 接口；真实 e2e 票的门槛（需真实 embedding/LLM？活 demo 栘？）

## 流程与产出
1. 新建 `docs/design/m6-report-kb-design.md`（status `draft`），形制照 M5：frontmatter source 全链 + 目标/Non-goals + 模块表 + 技术方案 + **G1–Gn 开放点表（每条给候选与推荐默认解）** + **R1–Rn 评审依据表（官方文档 URL + 取用日期，AI 检索提供，用户保留推翻权）** + 验收标准 + 拆票预告（T1–Tn 与未来 `.scratch/m6-report-kb/issues/` 对应，评审通过后才建 tracker）
2. 更新 `docs/README.md` 索引行；若产生新术语先入 `CONTEXT.md`
3. 不写 `decisions.md`（D-49+ 待用户评审定案后登记）；不建 `.scratch/m6-*` tracker
4. 提交：`docs(M6-报告与知识库): 设计草案 ...`，body 写清核心取舍

## 边界与纪律
- 零写码、零建表、零真实 API 调用；不改既有模块行为
- 涉及推翻既有决策（D-08/D-23/D-31 等）的方案必须显式标注「推翻面」，默认全部在既有决策框架内设计
- 母本只在 docs/ 提炼版语焉不详时回溯，正文引用一律指向 docs/ 提炼版
- 估算的验收口径须标注「实测后回填，不得虚构」

## 验收（可机械判定）
- [ ] `docs/design/m6-report-kb-design.md` 存在且 status `draft`，形制与 M5 对齐（G 开放点 + R 评审依据 + 验收节齐全）
- [ ] 六大核心问题每条有候选方案 + 推荐默认解
- [ ] D-23 query_kb stub 兑现路径、D-08 RAG 定位、D-31 衔接均有显式章节
- [ ] 知识污染防护有正面回答（PRD 追问点）
- [ ] `docs/README.md` 索引更新；新术语已入 CONTEXT.md（若有）
- [ ] 全量门禁复跑绿（pytest 682/12 只增不减 + ruff 双检）
