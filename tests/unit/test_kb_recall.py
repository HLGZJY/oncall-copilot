"""M6-T4 验收 · query_kb 实装 + 开局召回两通道（m6 issue 04 / D-23 冻结面、D-54、D-57）。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from oncall.db import create_tables
from oncall.db.models import AlertEvent, Incident, Investigation, KbChunk
from oncall.harness.tools.registry import ToolRegistry
from oncall.harness.tools.schemas import QueryKbInput, ToolStatus
from oncall.knowledge.embedder import MockEmbedder
from oncall.knowledge.pipeline import KnowledgePipeline
from oncall.knowledge.recall import opening_recall
from oncall.knowledge.retriever import build_query_kb_handler
from oncall.knowledge.store import InMemoryVectorStore

FIRED = datetime(2026, 9, 6, 6, 28, 21, tzinfo=UTC)


@pytest.fixture()
def kb_engine():
    """共享内存库（StaticPool：跨连接同库——retriever 内部独立 Session 可见种子数据）。"""
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    create_tables(engine)
    return engine


@pytest.fixture()
def db(kb_engine):
    session = Session(kb_engine)
    yield session
    session.close()


def _seed_closed(session: Session, *, fingerprint: str, status: str = "mitigated") -> int:
    """告警 + 事件 + 收尾调查（返回 incident id；status 可控）。"""
    alert = AlertEvent(
        fingerprint=fingerprint,
        source="alertmanager",
        labels_json={"alertname": "DemoApiGwHighLatency", "instance": "api-gw-1"},
        fired_at=FIRED,
        last_fired_at=FIRED,
        dedup_count=1,
        annotations_json={},
    )
    session.add(alert)
    session.flush()
    incident = Incident(alert_ids=[alert.id], status=status)
    session.add(incident)
    session.flush()
    session.add(
        Investigation(
            incident_id=incident.id,
            status="concluded",
            conclusion="CPU 飙高",
            step_count=2,
            finished_at=datetime.now(UTC),
            opening_card_json={"alert": {"labels": {"alertname": "DemoApiGwHighLatency"}}},
        )
    )
    session.commit()
    return incident.id


def _make_pipeline(kb_engine) -> KnowledgePipeline:
    return KnowledgePipeline(engine=kb_engine, embedder=MockEmbedder(), store=InMemoryVectorStore())


def test_query_kb_handler_injected_returns_kb_hits(db: Session, kb_engine) -> None:
    """注入检索器后 query_kb 返回 ok + kb_hits（source=kb 语义标记）。"""
    pipeline = _make_pipeline(kb_engine)
    incident_id = _seed_closed(db, fingerprint="a" * 64)
    pipeline.ingest_incident(incident_id, db)
    handler = build_query_kb_handler(pipeline.retriever())
    assert handler is not None
    result = handler(QueryKbInput(query="DemoApiGwHighLatency@api-gw-1", top_k=3), timeout_seconds=5.0)
    assert result.status is ToolStatus.OK
    assert result.meta["source"] == "kb"
    assert result.data["kb_hits"][0]["source"] == "kb"
    assert result.data["kb_hits"][0]["section"] in {"opening_card", "timeline", "root_cause", "remediation", "suggestions"}


def test_query_kb_handler_empty_and_not_injected() -> None:
    """无命中 → empty；未注入检索器 → None（registry 落 stub，零回退）。"""
    handler = build_query_kb_handler(_make_pipeline(kb_engine).retriever())
    assert handler is not None
    result = handler(QueryKbInput(query="完全无关的查询文本 xyz", top_k=3), timeout_seconds=5.0)
    assert result.status is ToolStatus.EMPTY
    assert build_query_kb_handler(None) is None


def test_query_kb_via_registry_frozen_face(db: Session, kb_engine) -> None:
    """经 registry 执行：D-23 冻结面（入参 schema / ToolResult 形状 / 六工具集合）不破。"""
    from oncall.harness.permission import PermissionLevel
    from oncall.harness.tools.registry import TOOL_NAMES, ToolSpec
    from oncall.harness.tools.schemas import ToolResult as TR

    assert set(TOOL_NAMES) == {
        "query_metrics", "search_logs", "detect_anomaly", "get_topology", "query_kb", "execute_action",
    }
    assert set(QueryKbInput.model_fields) == {"query", "top_k"}
    assert set(TR.model_fields) == {"tool", "status", "data", "meta"}

    pipeline = _make_pipeline(kb_engine)
    incident_id = _seed_closed(db, fingerprint="a" * 64)
    pipeline.ingest_incident(incident_id, db)
    registry = ToolRegistry()
    spec = ToolSpec("query_kb", QueryKbInput, PermissionLevel.L0, "历史事故检索（RAG）")
    registry.register(spec, build_query_kb_handler(pipeline.retriever()))  # type: ignore[arg-type]
    execution = registry.execute("query_kb", {"query": "DemoApiGwHighLatency@api-gw-1", "top_k": 2})
    assert execution.result.status is ToolStatus.OK


def test_opening_recall_fingerprint_cache_reuse(db: Session, kb_engine) -> None:
    """D-57：canonical 子集命中 + 源 mitigated → reuse，不更新 hit_count（口径分离）。

    注：全指纹含时间窗桶且唯一约束（D-14），跨事件命中 = canonical 子集相同
    （桶外同源连发，D-20 逻辑告警口径）——第二遍告警用不同指纹行、同 labels。
    """
    pipeline = _make_pipeline(kb_engine)
    src = _seed_closed(db, fingerprint="a" * 64)
    pipeline.ingest_incident(src, db)
    before = db.get(KbChunk, 1).hit_count

    # 新事件：不同指纹（不同时间窗桶），同 canonical labels（同源故障再来）
    alert2 = AlertEvent(
        fingerprint="c" * 64,
        source="alertmanager",
        labels_json={"alertname": "DemoApiGwHighLatency", "instance": "api-gw-1"},
        fired_at=FIRED,
        last_fired_at=FIRED,
        dedup_count=1,
        annotations_json={},
    )
    db.add(alert2)
    db.flush()
    new_incident = Incident(alert_ids=[alert2.id], status="investigating")
    db.add(new_incident)
    db.commit()

    outcome = opening_recall(db, new_incident.id, pipeline.retriever())
    assert outcome.kind == "reuse"
    assert outcome.reused_from == src
    assert db.get(KbChunk, 1).hit_count == before  # 复用不计召回


def test_opening_recall_vector_reference(db: Session, kb_engine) -> None:
    """canonical 未命中 → 向量相似 → reference 通道，hit_count++（真实召回计）。

    注入 reference_query = 源 labels 文本（同向量 → score=1.0，确定性断言）。
    """
    pipeline = _make_pipeline(kb_engine)
    src = _seed_closed(db, fingerprint="a" * 64)
    pipeline.ingest_incident(src, db)

    alert2 = AlertEvent(
        fingerprint="b" * 64,  # 指纹不同、labels 也不同（异源事件）
        source="alertmanager",
        labels_json={"alertname": "OtherQueueBacklog", "instance": "queue-1"},
        fired_at=FIRED,
        last_fired_at=FIRED,
        dedup_count=1,
        annotations_json={},
    )
    db.add(alert2)
    db.flush()
    new_incident = Incident(alert_ids=[alert2.id], status="investigating")
    db.add(new_incident)
    db.commit()

    outcome = opening_recall(
        db, new_incident.id, pipeline.retriever(), reference_query="DemoApiGwHighLatency@api-gw-1"
    )
    assert outcome.kind == "reference"
    assert outcome.hits and outcome.hits[0]["source"] == "kb"
    hit_counts = list(db.scalars(db.query(KbChunk.hit_count)))
    assert sum(hit_counts) > 0


def test_opening_recall_none_when_no_kb(db: Session, kb_engine) -> None:
    """知识库空 → none（不虚构不降级）。"""
    pipeline = _make_pipeline(kb_engine)
    incident_id = _seed_closed(db, fingerprint="a" * 64, status="investigating")
    outcome = opening_recall(db, incident_id, pipeline.retriever())
    assert outcome.kind == "none"
