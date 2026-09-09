"""M6-T1 验收 · 第九表 kb_chunks + 冻结面不破（m6 issue 01 / D-49–D-57）。

照 M5 test_m5_frozen_face 先例：import + 键集合精确断言（不用存在性软断言）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from oncall.db.models import (
    AlertEvent,
    Base,
    EvidenceStep,
    Hypothesis,
    Incident,
    Investigation,
    KbChunk,
    RemediationProposal,
)
from oncall.db import create_tables

# ── 冻结面（只消费不推翻）──

SIX_TABLES = {
    "alert_events",
    "incidents",
    "investigations",
    "evidence_steps",
    "hypotheses",
    "remediation_proposals",
}
NINE_TABLES = SIX_TABLES | {"kb_chunks"}

# D-55 第九表冻结列（字段契约 = m6 设计文档 §数据模型变更）
KB_CHUNK_COLUMNS = {
    "id",
    "incident_id",
    "investigation_id",
    "section",
    "seq",
    "text",
    "source_meta_json",
    "hit_count",
    "created_at",
    "superseded_at",
}
KB_SECTIONS = {"opening_card", "timeline", "root_cause", "remediation", "suggestions"}


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    create_tables(engine)
    session = Session(engine)
    yield session
    session.close()


def test_ninth_table_registered_and_frozen_columns():
    """第九表注册进同一 Base 元数据；列集合精确钉死；既有六表不倒改。"""
    assert set(Base.metadata.tables) >= NINE_TABLES
    assert set(KbChunk.__table__.columns.keys()) == KB_CHUNK_COLUMNS


def test_kb_chunk_create_all_and_roundtrip(db: Session) -> None:
    """create_all 幂等建表 + 一行知识块 roundtrip（D-55 字段全落可读）。"""
    incident = Incident(alert_ids=[1], status="mitigated")
    db.add(incident)
    db.flush()
    chunk = KbChunk(
        incident_id=incident.id,
        investigation_id=None,
        section="root_cause",
        seq=0,
        text="根因：demo-slow 端连接池耗尽（证据步 3-5）",
        source_meta_json={"report_version": "m6", "anchor": {"hypotheses": [1]}},
    )
    db.add(chunk)
    db.flush()
    row = db.scalar(select(KbChunk).where(KbChunk.id == chunk.id))
    assert row is not None
    assert row.section == "root_cause"
    assert row.hit_count == 0
    assert row.superseded_at is None
    assert row.source_meta_json["anchor"] == {"hypotheses": [1]}


def test_kb_chunk_section_check_constraint(db: Session) -> None:
    """section 五值冻结：非法值 DB 层拒绝（脏块进不来）。"""
    incident = Incident(alert_ids=[1], status="mitigated")
    db.add(incident)
    db.flush()
    db.add(KbChunk(incident_id=incident.id, section="whole_report", seq=0, text="x"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_kb_chunk_superseded_semantics(db: Session) -> None:
    """覆盖淘汰：旧块打 superseded_at 后不再是活跃块（召回侧过滤的数据面）。"""
    incident = Incident(alert_ids=[1], status="mitigated")
    db.add(incident)
    db.flush()
    old = KbChunk(incident_id=incident.id, section="root_cause", text="旧结论")
    db.add(old)
    db.flush()
    old.superseded_at = datetime.now(UTC)
    db.flush()
    active = db.scalars(
        select(KbChunk).where(KbChunk.incident_id == incident.id, KbChunk.superseded_at.is_(None))
    ).all()
    assert active == []


def test_existing_tables_frozen():
    """既有表列集合回归（D-25/D-31/D-46 冻结面不破的 T1 侧守卫）。"""
    assert set(AlertEvent.__table__.columns.keys()) >= {
        "id", "fingerprint", "dedup_count", "classification_json",
    }
    assert set(Investigation.__table__.columns.keys()) >= {
        "id", "incident_id", "status", "failure_mode",
    }
    assert set(EvidenceStep.__table__.columns.keys()) >= {
        "id", "incident_id", "step_no", "output_json",
    }
    assert set(Hypothesis.__table__.columns.keys()) == {
        "id", "incident_id", "text", "status", "supporting_steps", "against_steps",
    }
    assert set(RemediationProposal.__table__.columns.keys()) >= {
        "id", "incident_id", "dry_run_json", "status",
    }
