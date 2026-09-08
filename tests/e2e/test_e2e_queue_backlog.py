"""M3-08 端到端验收——queue-backlog 剧本（issue 08 / T8-A；D-18 同源 + D-29 规则判分）。

golden timeline → Fetcher 替身（D-18），MockPlanner 剧本驱动经
`POST /investigate` 走至 conclusion；结论 vs golden `root_cause` 规则匹配级
比对（关键实体 + 动作词）。共享夹具与判分器在 tests/golden_support.py。
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session

import golden_support
from oncall.api.investigation import InvestigationDeps
from oncall.db import AlertEvent, Incident, create_tables
from oncall.ingest.app import create_app

pytestmark = pytest.mark.inproc_asgi

SCENARIO = "queue-backlog"
_REPORT_DIR = Path(__file__).resolve().parents[2] / ".scratch" / "tmp"
REPORT_PATH = _REPORT_DIR / "m3-08-mock-e2e-report.json"
EXPECTED_TOOLS = ["query_metrics", "query_metrics", "search_logs"]


def _make_engine() -> object:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_tables(engine)
    return engine


def _seed_incident(engine: object) -> int:
    """建档：golden 首条告警落 alert_events + 1 条 incident（开局锚点数据源）。"""
    entry = golden_support.load_golden(SCENARIO)["runs"][0]["alert_timeline"][0]
    with Session(engine) as session:  # type: ignore[arg-type]
        row = AlertEvent(
            fingerprint=f"fp-{SCENARIO}",
            source="alertmanager",
            labels_json=entry["labels"],
            annotations_json={},
            fired_at=datetime.fromisoformat(entry["fired_at"]),
            status="deduped",
        )
        session.add(row)
        session.flush()
        incident = Incident(alert_ids=[row.id], severity="warning", status="investigating")
        session.add(incident)
        session.commit()
        return incident.id


def test_mock_e2e_concludes_with_golden_root_cause():
    """3 步证据步 → conclusion；结论与 golden root_cause 规则匹配级命中（D-29）。"""
    doc = golden_support.load_golden(SCENARIO)
    engine = _make_engine()
    incident_id = _seed_incident(engine)
    app = create_app(
        engine,
        investigation=InvestigationDeps(
            components=golden_support.make_e2e_components(
                golden_support.make_mock_planner(doc), doc
            )
        ),
    )
    client = TestClient(app)

    started = time.perf_counter()
    resp = client.post("/investigate", json={"incident_id": incident_id})
    duration_seconds = round(time.perf_counter() - started, 3)

    assert resp.status_code == 200
    body = resp.json()
    assert body["termination"] == "concluded"
    assert body["conclusion"] == doc["root_cause"]  # mock 收束 = golden 逐字（同源自证）
    assert body["step_count"] == 3  # ≤15（架构 §3.4 步数硬安全阀）
    assert duration_seconds < 300  # ≤5min 实测
    assert body["failure_mode"] is None  # 末步假设已证实，非 premature_stop
    assert body["confidence"] == pytest.approx(1 / 3, abs=1e-4)
    assert body["opening_card"] is not None
    assert [step["tool"] for step in body["steps"]] == EXPECTED_TOOLS  # 剧本驱动顺序
    # D-18：证据面与剧本同源——每步证据打 golden 溯源标记
    assert all(
        step["output_json"]["meta"]["source"] == "golden-dev-timeline" for step in body["steps"]
    )
    # 假设流转：前两步证伪、末步证实（排查收敛形状）
    statuses = [h["status"] for h in body["hypotheses"]]
    assert statuses == ["rejected", "rejected", "confirmed"]

    verdict = golden_support.match_root_cause(body["conclusion"], SCENARIO)
    assert verdict["hit"], verdict  # 规则匹配级命中（D-29）
    golden_support.append_report(
        REPORT_PATH,
        {
            "segment": "mock",
            "scenario": SCENARIO,
            "termination": body["termination"],
            "step_count": body["step_count"],
            "duration_seconds": duration_seconds,
            "failure_mode": body["failure_mode"],
            "confidence": body["confidence"],
            "match": verdict,
            "conclusion": body["conclusion"],
        },
    )
