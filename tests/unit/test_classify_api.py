"""分类 API 契约测试（issue 04 / G4 定案）。

契约来源：docs/design/m2-denoise-classify-design.md G4 +
decisions.md D-19（独立入口不串联 /ingest、幂等跳过已分类行）。

覆盖：
- POST /classify：单条 alert_id / batch pending（只处理 deduped 行）/ batch all /
  重复 classify 幂等；响应恰好五键 {classified, false_positive, risk, incident, llm_calls}
- 入参校验：alert_id 与 batch 二选一（422）、未知 alert_id（404）、
  LLM 通道未注入（503，禁止静默 mock）
- GET /alerts?verdict=：SQLite JSON1 json_extract 过滤（R7）
- GET /incidents：M3 调查入口查询面
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine, select
from sqlalchemy.orm import Session

from oncall.classify import (
    ClassifyRuntime,
    LLMChannel,
    LLMChannelOptions,
    MockLLMClassifier,
)
from oncall.db import AlertEvent, Incident, create_tables
from oncall.ingest.app import create_app

pytestmark = pytest.mark.inproc_asgi

NOW = datetime(2026, 9, 7, 7, 0, 0, tzinfo=UTC)
FIRED_AT = datetime(2026, 9, 6, 6, 28, 21, tzinfo=UTC)
MODEL = "deepseek-chat"
CLASSIFIED_RESPONSE_KEYS = {"classified", "false_positive", "risk", "incident", "llm_calls"}


def _make_client(mock: MockLLMClassifier | None = None, *, with_channel: bool = True):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_tables(engine)
    runtime = None
    if with_channel:
        channel = LLMChannel(
            mock or MockLLMClassifier(),
            model=MODEL,
            samples=[],
            options=LLMChannelOptions(classified_at=NOW),
        )
        runtime = ClassifyRuntime(llm_channel=channel)
    return TestClient(create_app(engine, classify_runtime=runtime)), engine


def _seed(engine, rows: list[dict[str, Any]]) -> list[int]:
    """批量造行：status="classified" 的行须自带 classification_json。"""
    ids = []
    with Session(engine) as session:
        for spec in rows:
            row = AlertEvent(
                fingerprint=spec["fingerprint"],
                source="alertmanager",
                labels_json=spec.get("labels")
                or {"alertname": "DemoApiGwHighLatency", "job": "api-gw", "severity": "warning"},
                annotations_json=spec.get("annotations") or {},
                fired_at=FIRED_AT,
                status=spec.get("status", "deduped"),
                classification_json=spec.get("classification_json"),
            )
            session.add(row)
            session.flush()
            ids.append(row.id)
        session.commit()
    return ids


def _classified_payload(verdict: str) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "confidence": 0.9,
        "reason": "seeded",
        "channel": "llm",
        "model": MODEL,
        "tokens": 10,
        "cost_cny": 0.0001,
        "classified_at": NOW.isoformat(),
    }


class TestClassifySingle:
    def test_single_alert_id_classifies_and_files_incident(self):
        client, engine = _make_client()
        (alert_id,) = _seed(engine, [{"fingerprint": "fp-a"}])

        resp = client.post("/classify", json={"alert_id": alert_id})

        assert resp.status_code == 200
        assert set(resp.json()) == CLASSIFIED_RESPONSE_KEYS
        assert resp.json() == {
            "classified": 1,
            "false_positive": 0,
            "risk": 0,
            "incident": 1,
            "llm_calls": 1,
        }
        with Session(engine) as session:
            row = session.get(AlertEvent, alert_id)
            assert row.status == "classified"
            assert row.classification_json["verdict"] == "incident"

    def test_unknown_alert_id_returns_404(self):
        client, _engine = _make_client()
        resp = client.post("/classify", json={"alert_id": 999})
        assert resp.status_code == 404

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"alert_id": 1, "batch": "all"},
            {"alert_id": 1, "batch": "pending"},
            {"batch": "nope"},
            {"alert_id": "x"},
            {"other": 1},
        ],
    )
    def test_invalid_request_bodies_return_422(self, body):
        client, _engine = _make_client()
        resp = client.post("/classify", json=body)
        assert resp.status_code == 422, f"body={body}"

    def test_channel_not_injected_returns_503(self):
        """LLM 通道未注入 → 503（独立入口不静默降级到 mock）。"""
        client, engine = _make_client(with_channel=False)
        _seed(engine, [{"fingerprint": "fp-a"}])
        resp = client.post("/classify", json={"batch": "pending"})
        assert resp.status_code == 503


class TestClassifyBatch:
    def _mixed_state_engine(self):
        """1 条规则可判行 + 1 条待 LLM 行 + 1 条已分类行。"""
        client, engine = _make_client()
        ids = _seed(
            engine,
            [
                {"fingerprint": "fp-rule", "annotations": {"raw_alert": {"status": "resolved"}}},
                {"fingerprint": "fp-llm"},
                {
                    "fingerprint": "fp-done",
                    "status": "classified",
                    "classification_json": _classified_payload("risk"),
                },
            ],
        )
        return client, engine, ids

    def test_batch_pending_only_processes_deduped_rows(self):
        client, engine, (rule_id, llm_id, done_id) = self._mixed_state_engine()

        resp = client.post("/classify", json={"batch": "pending"})

        assert resp.status_code == 200
        assert resp.json() == {
            "classified": 2,
            "false_positive": 1,
            "risk": 0,
            "incident": 1,
            "llm_calls": 1,  # 规则命中行不耗 LLM，仅待决行 1 次
        }
        with Session(engine) as session:
            assert session.get(AlertEvent, rule_id).classification_json["channel"] == "rule"
            assert session.get(AlertEvent, llm_id).classification_json["verdict"] == "incident"
            seeded = session.get(AlertEvent, done_id)
            assert seeded.classification_json == _classified_payload("risk")  # 已分类行原样

    def test_batch_all_processes_every_row_but_skips_classified(self):
        client, _engine, _ids = self._mixed_state_engine()

        resp = client.post("/classify", json={"batch": "all"})

        assert resp.status_code == 200
        assert resp.json()["classified"] == 2  # 幂等：已 classified 行跳过
        assert resp.json()["llm_calls"] == 1

    def test_replayed_batch_is_idempotent(self):
        client, engine, _ids = self._mixed_state_engine()
        first = client.post("/classify", json={"batch": "all"})
        second = client.post("/classify", json={"batch": "all"})

        assert first.json()["classified"] == 2
        assert second.json() == {
            "classified": 0,
            "false_positive": 0,
            "risk": 0,
            "incident": 0,
            "llm_calls": 0,
        }
        with Session(engine) as session:
            assert len(session.scalars(select(AlertEvent)).all()) == 3  # 不新增行
            assert len(session.scalars(select(Incident)).all()) == 1  # 不重复建档


class TestAlertVerdictFilter:
    def test_filter_by_each_verdict(self):
        client, engine = _make_client()
        (rule_id, incident_id, risk_id, _pending_id) = _seed(
            engine,
            [
                # 规则可判行 → 单条 classify 后 false_positive
                {"fingerprint": "fp-rule", "annotations": {"raw_alert": {"status": "resolved"}}},
                # LLM 默认 mock（incident, 0.8）→ 单条 classify 后 incident
                {"fingerprint": "fp-inc"},
                # 预置已分类 risk 行（不进本次 classify）
                {
                    "fingerprint": "fp-risk",
                    "status": "classified",
                    "classification_json": _classified_payload("risk"),
                },
                # 永不定档行：保持 NULL + deduped，不得被任何 verdict 过滤命中
                {"fingerprint": "fp-pending"},
            ],
        )
        assert client.post("/classify", json={"alert_id": rule_id}).json()["false_positive"] == 1
        assert client.post("/classify", json={"alert_id": incident_id}).json()["incident"] == 1

        for verdict, expected_id in [
            ("false_positive", rule_id),
            ("incident", incident_id),
            ("risk", risk_id),
        ]:
            resp = client.get("/alerts", params={"verdict": verdict})
            assert resp.status_code == 200
            assert [item["id"] for item in resp.json()["items"]] == [expected_id], verdict

        # NULL 行不被任何 verdict 命中（json_extract(NULL) 不参与匹配）
        for verdict in ("false_positive", "incident", "risk"):
            resp = client.get("/alerts", params={"verdict": verdict, "fingerprint": "fp-pending"})
            assert resp.json()["items"] == [], verdict

    def test_invalid_verdict_value_returns_422(self):
        client, _engine = _make_client()
        resp = client.get("/alerts", params={"verdict": "banana"})
        assert resp.status_code == 422

    def test_filter_combines_with_fingerprint(self):
        client, engine = _make_client()
        _ids = _seed(
            engine,
            [
                {"fingerprint": "fp-rule", "annotations": {"raw_alert": {"status": "resolved"}}},
                {"fingerprint": "fp-inc"},
                {"fingerprint": "fp-other"},
            ],
        )
        # 只分类两行，fp-other 保持未分类
        client.post("/classify", json={"alert_id": _ids[0]})
        client.post("/classify", json={"alert_id": _ids[1]})

        # verdict + fingerprint 双过滤：只命中该指纹下的 incident 行
        resp = client.get("/alerts", params={"verdict": "incident", "fingerprint": "fp-inc"})
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["fingerprint"] == "fp-inc"

        # 同 verdict 换指纹（该行未分类）→ 空
        resp = client.get("/alerts", params={"verdict": "incident", "fingerprint": "fp-other"})
        assert resp.json()["items"] == []


class TestIncidentsEndpoint:
    def test_list_incidents_returns_filed_rows_with_five_fields(self):
        client, engine = _make_client()
        _seed(
            engine,
            [
                {"fingerprint": "fp-rule", "annotations": {"raw_alert": {"status": "resolved"}}},
                {"fingerprint": "fp-inc", "labels": {"alertname": "A", "severity": "critical"}},
            ],
        )
        client.post("/classify", json={"batch": "pending"})

        resp = client.get("/incidents")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        (item,) = body["items"]
        assert set(item) == {"id", "alert_ids", "severity", "status", "created_at"}
        assert item["severity"] == "critical"
        assert item["status"] == "investigating"
        assert len(item["alert_ids"]) == 1

    def test_empty_incidents_list(self):
        client, _engine = _make_client()
        resp = client.get("/incidents")
        assert resp.status_code == 200
        assert resp.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}
