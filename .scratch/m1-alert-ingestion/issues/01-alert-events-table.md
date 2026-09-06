Status: resolved

# 01 alert_events 表与模型

## 任务

按 `docs/design/m1-alert-ingestion-design.md` §数据模型 + D-13 建 SQLAlchemy 2.0 model 与 SQLite 建表（dev 用 `create_all`），落 `src/oncall/db/`。

字段 = 架构 §4 冻结列（`id / fingerprint / source / labels_json / fired_at / status(deduped/classified)`）+ M1 增列（`dedup_count / last_fired_at / resolved_at / annotations_json`）。`fingerprint` 加索引；`status` 默认 `deduped`（M2 写入 `classified`）。

## 要点

- 六表中**只建 `alert_events`**，其余（incidents/evidence_steps/hypotheses）属 M2–M4，不越界
- `annotations_json` 需能装下：原始告警 annotations + Alertmanager 自带 `fingerprint`（溯源用，见设计文档 G5）
- 不引 Alembic（延至切 MySQL，D-13）

## 验收（可机械判定，实测后回填）

- [x] pytest 建 `alert_events` 全字段可插入/查询/更新，字段与 D-13 清单一一对应
  → 实测（2026-09-06，`tests/unit/test_db_alert_events.py`）：10 列与 D-13 清单集合相等（`test_columns_match_d13_exactly`）；全字段插入/查询/更新（`test_full_roundtrip`、`test_update_status_to_classified`）绿；六表只建 `alert_events`（`test_only_alert_events_table`）绿
- [x] `fingerprint` 列唯一性/索引可验证（插入同 fingerprint 二次被约束或可按索引查）
  → 实测：唯一索引 `ix_alert_events_fingerprint`（unique）经 inspector 断言（`test_fingerprint_is_indexed_and_unique`）；同 fingerprint 二次插入 IntegrityError（`test_duplicate_fingerprint_rejected`）绿。附带：status 默认 deduped + CHECK 冻结取值、dedup_count 默认 1、last_fired_at 由 fired_at 回填（显式值不覆盖）均有专测
- [x] 全量 pytest 绿；coverage ≥80%（只算 `src/oncall`）
  → 实测：58 passed，coverage 97.67%（fail_under=80 达标）；ruff check + format 绿

落地文件：`src/oncall/db/__init__.py`（Base + create_tables，dev SQLite create_all，Alembic 延至切 MySQL）+ `src/oncall/db/models.py`（AlertEvent，架构 §4 冻结列 + D-13 增列；fingerprint String(64) unique+index 即 sha256 hex 定位）。

## Blocked by

（无，可与 M0-05/06 并行）
