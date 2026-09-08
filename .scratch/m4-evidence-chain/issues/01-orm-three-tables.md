Status: resolved
Blocked by:

# 01 ORM 三表（T1 / G1–G3；D-30/D-31/D-32）

## 任务

- **`investigations` 表**（D-31）：`id, incident_id(FK→incidents.id), status(running/concluded/escalated/aborted), stop_reason, conclusion, failure_mode, step_count, total_tokens, total_cost_cny, started_at, finished_at`；`incident_id` **唯一约束**（重复调查覆盖旧行，对齐现进程内注册表语义）；status CHECK 约束照 M2 先例落 DB 层
- **`evidence_steps` 表**（架构 §4 冻结列照抄）：`id, incident_id(FK), step_no, thought, tool, input_json, output_json, output_summary, tokens, cost, latency_ms, ts`；`(incident_id, step_no)` 组合唯一
- **`hypotheses` 表**（架构 §4 冻结列照抄）：`id, incident_id(FK), text, status(confirmed/rejected/active), supporting_steps[], against_steps[]`
- 建表走现有 `Base`（`src/oncall/db/models.py`，现 89 行扩后 ≈210 < C6）+ `create_all` 幂等（D-30：现库 `oncall.db`，Alembic 延至切 MySQL）
- input 侧口径（D-32）：只落 `input_json` 全文，**不设 `input_summary` 列**；output 侧双存照冻结

## 要点

- 冻结列字段与架构 §4 逐字段一致——文档是权威，字段名不得擅改（D-25）
- 第七表超出架构 §4 六表规划是已评审偏差（D-31），架构回写归后续票，本票不改架构文档
- 全程零 src 逻辑变更，只扩 models.py；`tests/test_architecture_guards.py` 必须保持全绿

## 验收（可机械判定）

- [x] pytest 绿：`create_all` 重复执行幂等（二次建表不报错）——`TestCreateAllIdempotent::test_second_create_all_is_noop`
- [x] pytest 绿：FK 约束生效（非法 incident_id 拒绝）、`investigations.incident_id` 唯一约束生效（二次插入冲突）——`TestConstraints::test_foreign_key_rejects_unknown_incident` / `test_investigation_incident_id_unique_conflict`
- [x] pytest 绿：status CHECK 约束生效（非法枚举拒绝）——investigations 四态 + hypotheses 三态各一测（`ck_investigations_status` / `ck_hypotheses_status`）
- [x] pytest 绿：三表列集合与设计文档「数据模型变更」节逐字段一致断言——三表列集合精确相等断言（含 D-32 反向断言 `input_summary` 不存在）
- [x] 全量门禁不回退：479 passed / 7 skipped 基线只增不减 + ruff 双检——**494 passed / 7 skipped**（+15）+ ruff check / ruff format --check 全绿；架构守卫 7 项全绿；coverage 98%（不回退）

## 回填注记（2026-09-08，T1 完成）

- 落位：`src/oncall/db/models.py`（89 → 196 行 < C6 300；新增 `Investigation` / `EvidenceStep` / `Hypothesis` 三个 ORM 类）+ 新增 `tests/unit/test_db_evidence_models.py`（15 测试）
- 列对齐说明：`evidence_steps`/`hypotheses` 与架构 §4 冻结列逐字段一致（类型自定不越权——§4 只冻结字段名）；`investigations` 按 D-31 列清单落位，默认值对齐 M3 内存契约初始态（status=running / 计数 0 / started_at 落库即取）；步级列名 `cost` 照 §4 冻结（内存契约叫 `cost_cny`，DB 列名以文档为权威）
- 边界外一处**测试前沿断言更新**：`tests/unit/test_db_alert_events.py::test_tables_match_m2_scope` → `test_tables_match_m4_scope`（全库表清单断言 2 表 → 5 表）——建表里程碑演进使旧前沿断言自然过期，照 M2 更新 M1 同名断言先例处理；schema 断言语义不变（不越界守卫）
- SQLite FK 测试先例缺位（test_db_alert_events 未测 FK），本票在测试 engine fixture 落 `PRAGMA foreign_keys=ON` 事件监听，02 票 FK 相关测试可复用此模式
- 零 src 逻辑变更（仅 models.py 扩展）、零 LLM 调用、零 HTTP；holdout/ 未触碰；CONTEXT.md 无新术语（调查记录/证据仓库/证据步均已评审时入表）
