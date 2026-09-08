Status: ready-for-agent
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

- [ ] pytest 绿：`create_all` 重复执行幂等（二次建表不报错）
- [ ] pytest 绿：FK 约束生效（非法 incident_id 拒绝）、`investigations.incident_id` 唯一约束生效（二次插入冲突）
- [ ] pytest 绿：status CHECK 约束生效（非法枚举拒绝）
- [ ] pytest 绿：三表列集合与设计文档「数据模型变更」节逐字段一致断言
- [ ] 全量门禁不回退：479 passed / 7 skipped 基线只增不减 + ruff 双检
