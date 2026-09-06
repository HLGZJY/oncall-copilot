Status: ready-for-agent

# 01 alert_events 表与模型

## 任务

按 `docs/design/m1-alert-ingestion-design.md` §数据模型 + D-13 建 SQLAlchemy 2.0 model 与 SQLite 建表（dev 用 `create_all`），落 `src/oncall/db/`。

字段 = 架构 §4 冻结列（`id / fingerprint / source / labels_json / fired_at / status(deduped/classified)`）+ M1 增列（`dedup_count / last_fired_at / resolved_at / annotations_json`）。`fingerprint` 加索引；`status` 默认 `deduped`（M2 写入 `classified`）。

## 要点

- 六表中**只建 `alert_events`**，其余（incidents/evidence_steps/hypotheses）属 M2–M4，不越界
- `annotations_json` 需能装下：原始告警 annotations + Alertmanager 自带 `fingerprint`（溯源用，见设计文档 G5）
- 不引 Alembic（延至切 MySQL，D-13）

## 验收（可机械判定，实测后回填）

- [ ] pytest 建 `alert_events` 全字段可插入/查询/更新，字段与 D-13 清单一一对应
- [ ] `fingerprint` 列唯一性/索引可验证（插入同 fingerprint 二次被约束或可按索引查）
- [ ] 全量 pytest 绿；coverage ≥80%（只算 `src/oncall`）

## Blocked by

（无，可与 M0-05/06 并行）
