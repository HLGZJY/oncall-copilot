# m4-evidence-chain · Spec

M4 证据链与过程存储（W4 前半，PRD §7-M4，不可砍三支柱之一）。**权威设计**：`docs/design/m4-evidence-chain-design.md`（status: **reviewed**，2026-09-08 G1–G9 评审定案；评审依据见该文档 R1–R5）。

> **派工解冻**：G1–G9 已于 2026-09-08 全部评审定案（用户逐条拍板采纳推荐解，登记 `decisions.md` **D-30–D-38**）；全部 8 票就绪态终审完成，按编号顺序派工。**T8 真实实测重跑开工前需用户确认 key**（照 M3 issue 08 流程）。

## 目标

把 M3 内存契约落库为 ORM 三表（`investigations` + `evidence_steps` + `hypotheses`）：每步 `{step, thought, tool, input, output, ts, cost}` **步进即写 100% 落库**（可审计、任意一步可回溯原始工具输出）；事故-证据一对多模型（外键锚 `incidents.id`）；导出 JSON/Markdown 报告；同票承接 issue 08（M3）两个结构缺口修复（opening 视图 + 工具 schema 可见）并重跑真实实测。M3/M4 边界（D-25）：内存契约不倒改，内存对象替换为行 id，指针接口不变。

## 关键契约（2026-09-08 评审定案，D-30–D-38；执行时不得擅改）

- **冻结输入只消费不推翻**：D-17（卡片 13 键）/ D-19（incidents 五字段不扩列）/ D-22（Planner 协议）/ D-23（六工具面）/ D-25（M3/M4 边界：契约不倒改、行 id 替换指针）/ D-28（终止语义与 escalate 落点）
- **存储（D-30/D-31）**：现库 `oncall.db` 加表，SQLAlchemy `Base` 复用 + `create_all` 幂等，Alembic 延至切 MySQL；新增第七表 `investigations`（incident_id 1:1 唯一约束、重复调查覆盖旧行；**超架构 §4 六表规划的显式偏差已评审，随票回写架构 §4**）
- **落库口径（D-32/D-33/D-34）**：input 只落 `input_json` 全文（不设 `input_summary` 列）；output 侧 `output_json`/`output_summary` 双存照架构 §4 冻结；**步进即写**每步一个事务，写失败冒泡归类 `tool_error` 不静默；成本会话级汇总（Planner `usage_log` + Verifier 裁决 usage），步级维持工具埋点现语义
- **导出（D-35/D-36）**：JSON 以 M3 `build_report` 现形状为准（键集合契约测试精确守卫），**修订 agent-loop-design 示例对齐冻结契约**（消除双权威，回写随 T3 提交交用户复核）；Markdown 本票最小版（str 模板零新依赖，`GET /investigations/{incident_id}/report.md`）
- **缺口①（D-37）**：view 增 `opening` = D-17 卡片精简投影（`{alertname, instance, job, severity, source, status, fired_at, last_fired_at}` + 三源 `context.status` 摘要，不含 items 全文）；组装抽 `context_manager.build_decision_view`，loop.py（297 行贴 C6）瘦身
- **缺口②（D-38）**：system prompt 附六工具入参 schema 摘要（静态模板 A2 落位，每工具 2–3 行，预算 +≈250 tokens 且 ≤1500 断言钉死）；不补第 7 个 tool_help 工具（不改 D-23 面）
- **架构守卫**：C3（harness 禁 import classify/ingest/api——`oncall.db` 不在禁列，落库接缝落 `db/evidence_repo.py` 依赖方向合法）/ C4/C5 / C6 ≤300 行 / A2（prompt 模板落位，禁业务代码内联）
- **数据纪律**：mock 3 剧本 = cpu-spike / slow-sql / queue-backlog（D-29 沿用），只碰 `datasets/golden/dev/`（holdout/ 禁读含 raw 切片）；实测数字只许引用 M3 issue 08 实测值（真实轮 ≈¥0.008/6 次、步数 2–5），禁虚构
- **TDD + mock-only**：设计期零真实 LLM 调用；三表 ORM 契约、步进即写接缝、报告键集合、opening 投影键集是约定接缝；门禁基线 **479 passed / 7 skipped / 98.05%** 只增不减

## 任务序列

| Issue | 任务 | 对应设计文档 | Blocked by | 就绪态 |
|---|---|---|---|---|
| 01 | ORM 三表（investigations/evidence_steps/hypotheses + create_all + CHECK） | T1 / G1–G3 | — | resolved（2026-09-08） |
| 02 | 证据仓库写入接缝（步进即写/行 id 回填/写失败熔断） | T2 / G4–G5 | 01 | resolved（2026-09-08） |
| 03 | 报告读库与 JSON 形状定案（注册表退役 + agent-loop-design 回写） | T3 / G6 | 02 | resolved（2026-09-08） |
| 04 | Markdown 最小版导出（report.md 端点 + str 模板） | T4 / G7 | 03 | ✅ resolved |
| 05 | 缺口① opening 视图（build_decision_view + loop 瘦身） | T5 / G8 | — | resolved（2026-09-08） |
| 06 | 缺口② 工具 schema 摘要进 system prompt | T6 / G9 | — | ✅ resolved（2026-09-08） |
| 07 | 验收断言与门禁（验收标准节逐条转机械断言） | T7 | 01–06 | resolved |
| 08 | 真实实测重跑与收尾（**开工前需用户确认 key**） | T8 | 05, 06, 07 | ready-for-agent（key 门槛） |

## M4/M5/M6/M7 边界（spec 级写清）

- **M5**：execute_action 实装 + 四道闸门——M4 不碰
- **M6**：报告内容生成（时间线美化/处置记录/改进建议）+ 历史报告向量化——M4 Markdown 只是数据直出的输入
- **M7**：`eval_runs` 评测行、Top-1/Top-3 矩阵、LLM-as-judge、人工抽检——M4 只保证 `investigations` 会话级字段（failure_mode/步数/成本）可取；多轮评测历史落 eval_runs 另表，不混 investigations
- **M8**：Vue3 时间线/证据链 UI——`GET /investigations` JSON 即无 UI 阶段载体
