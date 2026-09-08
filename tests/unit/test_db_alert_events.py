"""T1 验收测试：alert_events 表与模型（issue .scratch/m1-alert-ingestion/issues/01）。

契约来源：docs/architecture/architecture.md §4 冻结列 + D-13 增列
（docs/design/m1-alert-ingestion-design.md §数据模型变更 G5）
+ D-19 M2 增列与 incidents 建表（m2-denoise-classify-design.md G4/G5）。

覆盖：
- 字段清单与 D-13 + D-19 一一对应（11 列），六表中 M2 建到 alert_events + incidents
- 全字段可插入 / 查询 / 更新
- fingerprint 唯一约束 + 索引可验证
- status 默认 deduped、CHECK 冻结取值（M2 写 classified）
- 首次落库 last_fired_at == fired_at（去重合并语义，T3 复用）
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from oncall.db import AlertEvent, create_tables

FIRED = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)

D13_COLUMNS = {
    # 架构 §4 冻结列
    "id",
    "fingerprint",
    "source",
    "labels_json",
    "fired_at",
    "status",
    # D-13 M1 增列
    "dedup_count",
    "last_fired_at",
    "resolved_at",
    "annotations_json",
    # D-19 M2 增列（NULL = 未分类，verdict 查询走 JSON1 json_extract，R7）
    "classification_json",
}


@pytest.fixture()
def engine():
    engine = create_engine("sqlite://")
    create_tables(engine)
    return engine


def _make(**overrides):
    """一条最小可插入告警（labels 对齐 G1 canonical 字段示例）。"""
    fields: dict = {
        "fingerprint": "a" * 64,
        "source": "alertmanager",
        "labels_json": {
            "alertname": "DemoApiGwHighLatency",
            "job": "api-gw",
            "instance": "api-gw-1",
        },
        "annotations_json": {
            "am_fingerprint": "ff00aa11",
            "summary": "api-gw P95 latency above threshold",
        },
        "fired_at": FIRED,
    }
    fields.update(overrides)
    return AlertEvent(**fields)


def _aware(dt: datetime) -> datetime:
    """SQLite 取回的时间无 tzinfo，统一补 UTC 后比较（SQLite 方言不保留时区）。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class TestFieldContract:
    def test_columns_match_d13_d19_exactly(self, engine):
        cols = {c["name"] for c in inspect(engine).get_columns("alert_events")}
        assert cols == D13_COLUMNS

    def test_tables_match_m4_scope(self, engine):
        """M1 建 alert_events，M2（issue 04）按 D-19 增建 incidents，
        M4（m4 issue 01）按 D-31 增建 investigations/evidence_steps/hypotheses；
        scenarios/eval_runs 不越界（前沿断言随建表里程碑演进，M2 先例）。"""
        assert sorted(inspect(engine).get_table_names()) == [
            "alert_events",
            "evidence_steps",
            "hypotheses",
            "incidents",
            "investigations",
        ]

    def test_fingerprint_is_indexed_and_unique(self, engine):
        indexes = inspect(engine).get_indexes("alert_events")
        fp_indexes = [i for i in indexes if i["column_names"] == ["fingerprint"]]
        assert len(fp_indexes) == 1
        assert fp_indexes[0]["unique"]  # SQLite 方言返回 1（int），truthy 即唯一索引


class TestInsertQueryUpdate:
    def test_full_roundtrip(self, engine):
        with Session(engine) as session:
            session.add(_make())
            session.commit()

            row = session.scalars(select(AlertEvent)).one()
            assert row.id == 1
            assert row.fingerprint == "a" * 64
            assert row.source == "alertmanager"
            assert row.labels_json["alertname"] == "DemoApiGwHighLatency"
            assert row.annotations_json["am_fingerprint"] == "ff00aa11"
            assert _aware(row.fired_at) == FIRED

    def test_update_status_to_classified(self, engine):
        """M2 分类后写回 status=classified + resolved_at（去重合并语义见 T3）。"""
        with Session(engine) as session:
            session.add(_make())
            session.commit()

            row = session.scalars(select(AlertEvent)).one()
            row.status = "classified"
            row.resolved_at = FIRED.replace(hour=13)
            session.commit()

            reloaded = session.scalars(select(AlertEvent)).one()
            assert reloaded.status == "classified"
            assert _aware(reloaded.resolved_at) == FIRED.replace(hour=13)


class TestConstraints:
    def test_duplicate_fingerprint_rejected(self, engine):
        with Session(engine) as session:
            session.add(_make())
            session.add(_make(labels_json={"alertname": "Other", "job": "api-gw", "instance": "x"}))
            with pytest.raises(IntegrityError, match="fingerprint"):
                session.commit()

    def test_status_defaults_to_deduped(self, engine):
        """status 默认 deduped（M1 只去重，M2 才写 classified）。"""
        with Session(engine) as session:
            session.add(_make())
            session.commit()

            row = session.scalars(select(AlertEvent)).one()
            assert row.status == "deduped"

    def test_status_check_constraint(self, engine):
        """status 取值冻结为 deduped/classified，脏值直接被 DB 拒绝。"""
        with Session(engine) as session:
            session.add(_make(status="bogus"))
            with pytest.raises(IntegrityError):
                session.commit()

    def test_dedup_count_defaults_to_one(self, engine):
        """首次 firing 即计数 1（T3 合并时应用层 ++）。"""
        with Session(engine) as session:
            session.add(_make())
            session.commit()

            row = session.scalars(select(AlertEvent)).one()
            assert row.dedup_count == 1

    def test_last_fired_at_backfilled_from_fired_at(self, engine):
        """首次落库 last_fired_at == fired_at；显式传入则不覆盖。"""
        with Session(engine) as session:
            session.add(_make())
            session.add(_make(fingerprint="c" * 64, last_fired_at=FIRED.replace(minute=30)))
            session.commit()

            rows = session.scalars(select(AlertEvent).order_by(AlertEvent.id)).all()
            assert _aware(rows[0].last_fired_at) == FIRED
            assert _aware(rows[1].last_fired_at) == FIRED.replace(minute=30)
