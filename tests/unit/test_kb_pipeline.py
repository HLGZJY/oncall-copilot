"""M6-T3 验收 · 切块 / embedding / 向量索引 / 入库管线（m6 issue 03 / D-51–D-53、D-56）。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from oncall.db import create_tables
from oncall.db.models import AlertEvent, Incident, Investigation, KbChunk
from oncall.knowledge.chunking import chunk_report
from oncall.knowledge.embedder import MockEmbedder
from oncall.knowledge.pipeline import KnowledgePipeline, NotEligibleForIngestion
from oncall.knowledge.store import ChromaVectorStore, InMemoryVectorStore, VectorStoreUnavailable

FIRED = datetime(2026, 9, 6, 6, 28, 21, tzinfo=UTC)


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


def _seed_mitigated(session: Session) -> int:
    alert = AlertEvent(
        fingerprint="a" * 64,
        source="alertmanager",
        labels_json={"alertname": "DemoApiGwHighLatency", "instance": "api-gw-1"},
        fired_at=FIRED,
        last_fired_at=FIRED,
        dedup_count=2,
        annotations_json={},
    )
    session.add(alert)
    session.flush()
    incident = Incident(alert_ids=[alert.id], status="mitigated")
    session.add(incident)
    session.flush()
    session.add(
        Investigation(
            incident_id=incident.id,
            status="concluded",
            conclusion="CPU 飙高",
            step_count=4,
            opening_card_json={"alert": {"labels": {"alertname": "DemoApiGwHighLatency"}}},
        )
    )
    session.commit()
    return incident.id


def _seed_unmitigated(session: Session, status: str) -> int:
    incident = Incident(alert_ids=[])
    session.add(incident)
    session.flush()
    session.add(Investigation(incident_id=incident.id, status="concluded", step_count=1))
    incident_row = session.get(Incident, incident.id)
    incident_row.status = status
    session.commit()
    return incident.id


def test_chunk_report_boundaries_and_metadata() -> None:
    """五节各 1 块；section 词表冻结；空文本节跳过；source 锚点透传。"""
    sections = {
        "opening_card": {"text": "告警 X@y", "source": {"investigations": [1]}},
        "timeline": {"text": "- [deduped] A@b (alert#1)", "source": {"alert_events": [1]}},
        "root_cause": {"text": "无已证实假设", "source": {"hypotheses": []}},
        "remediation": {"text": "", "source": {}},
        "suggestions": {"text": "占位", "source": {"generated_by": "placeholder"}},
    }
    chunks = chunk_report(sections, incident_id=9, investigation_id=None)
    assert [c["section"] for c in chunks] == [
        "opening_card",
        "timeline",
        "root_cause",
        "suggestions",
    ]
    assert [c["seq"] for c in chunks] == [0, 1, 2, 4]  # remediation 空节跳过，seq 保留位次
    assert all(c["incident_id"] == 9 and c["investigation_id"] is None for c in chunks)
    assert chunks[0]["source_meta_json"] == {"source": {"investigations": [1]}}


def test_mock_embedder_deterministic() -> None:
    """同文本恒同向量；异文本异向量；维度钉死。"""
    emb = MockEmbedder(dimension=32)
    v1 = emb.embed(["根因：连接池耗尽", "根因：连接池耗尽"])
    v2 = emb.embed(["根因：CPU 飙高"])
    assert v1[0] == v1[1]
    assert v1[0] != v2[0]
    assert all(len(v) == 32 for v in v1 + v2)


def test_inmemory_store_cosine_rank_and_incident_delete() -> None:
    """余弦排序 + delete_incident 精确清incident（覆盖淘汰索引面）。"""
    store = InMemoryVectorStore()
    store.upsert(
        ["1", "2"],
        [[1.0, 0.0], [0.0, 1.0]],
        [{"incident_id": 1, "section": "root_cause"}, {"incident_id": 2, "section": "root_cause"}],
    )
    hits = store.query([0.9, 0.1], top_k=2)
    assert hits[0]["id"] == "1"
    assert hits[0]["incident_id"] == 1
    store.delete_incident(1)
    assert [h["id"] for h in store.query([0.9, 0.1], top_k=2)] == ["2"]


def test_chroma_lazy_import_unavailable_semantics(tmp_path) -> None:
    """未安装 chromadb → VectorStoreUnavailable；已安装 → 可构造（依赖冒烟通过）。"""
    try:
        store = ChromaVectorStore(path=str(tmp_path / "chroma"))
    except VectorStoreUnavailable:
        return  # 未安装路径：unavailable 语义成立（D-16）
        return
    store.upsert(["1"], [[1.0, 0.0]], [{"incident_id": 1}])
    assert store.query([1.0, 0.0], top_k=1)[0]["id"] == "1"


def test_pipeline_ingestion_gate_and_supersede(db: Session) -> None:
    """D-56 门槛：非 mitigated 全拒；mitigated 入库 + 重入覆盖旧块（D-31 联动）。"""
    emb, store = MockEmbedder(), InMemoryVectorStore()
    pipeline = KnowledgePipeline(engine=None, embedder=emb, store=store)

    for status in ("investigating", "closed"):
        bad = _seed_unmitigated(db, status)
        with pytest.raises(NotEligibleForIngestion):
            pipeline.ingest_incident(bad, db)

    incident_id = _seed_mitigated(db)
    n = pipeline.ingest_incident(incident_id, db)
    assert n == 5  # 五节全非空（remediation 空表落「无处置提案」如实行）
    rows = list(db.scalars(select(KbChunk).where(KbChunk.incident_id == incident_id)))
    assert all(r.superseded_at is None for r in rows)

    # 重入：旧块全部 superseded + 索引清除，新块写入
    pipeline.ingest_incident(incident_id, db)
    rows = list(db.scalars(select(KbChunk).where(KbChunk.incident_id == incident_id)))
    superseded = [r for r in rows if r.superseded_at is not None]
    active = [r for r in rows if r.superseded_at is None]
    assert len(superseded) == 5 and len(active) == 5
