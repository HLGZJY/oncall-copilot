"""T2 验收测试：POST /ingest 端点 + 落库 + 幂等（issue 02）。

契约来源：docs/design/m1-alert-ingestion-design.md G3 定案（/ingest 幂等硬要求）、
D-13（去重状态落 DB 行）；验收标准见 issue 文件。

覆盖：
- 真实形态 payload POST → 2xx + {received, deduped} + 落库字段齐全
- 幂等硬要求：同一 payload 原样重放 3 次 → 仍 1 条记录、dedup_count 不变（D-13）
- 批量：单次 payload 含 ≥2 条 alerts[] → 逐条落库；payload 内自重复亦判重
- 校验失败 → 4xx，不吞错、服务不崩（后续请求仍正常）
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine, select
from sqlalchemy.orm import Session

from conftest import RESOLVED_ENDS, make_alert, make_webhook
from oncall.db import AlertEvent, create_tables
from oncall.ingest.app import create_app

# TestClient 走进程内 ASGI 传输，无真实网络 IO（pytest-socket 对事件循环自管道误伤，豁免）
pytestmark = pytest.mark.inproc_asgi


def _make_client():
    """内存库 + TestClient（StaticPool 跨线程共享同一内存连接）。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_tables(engine)
    return TestClient(create_app(engine)), engine


class TestIngestHappyPath:
    def test_firing_payload_returns_2xx_and_persists_full_fields(self):
        client, engine = _make_client()
        resp = client.post("/ingest", json=make_webhook())

        assert resp.status_code == 200
        assert resp.json() == {"received": 1, "deduped": 0}

        with Session(engine) as session:
            row = session.scalars(select(AlertEvent)).one()
            assert row.source == "alertmanager"
            assert row.status == "deduped"
            assert row.dedup_count == 1
            assert row.labels_json["alertname"] == "DemoApiGwHighLatency"
            assert row.annotations_json["am_fingerprint"] == "4df3b6985b707f0b"
            assert row.fired_at is not None
            assert row.resolved_at is None

    def test_resolved_payload_sets_resolved_at(self):
        client, engine = _make_client()
        resp = client.post(
            "/ingest",
            json=make_webhook(
                status="resolved", alerts=[make_alert(status="resolved", endsAt=RESOLVED_ENDS)]
            ),
        )
        assert resp.status_code == 200
        with Session(engine) as session:
            row = session.scalars(select(AlertEvent)).one()
            assert row.resolved_at is not None

    def test_batch_payload_persists_each_alert(self):
        """单次 payload 含 ≥2 条 alerts[] → 逐条落库（不假设单条）。"""
        client, engine = _make_client()
        batch = make_webhook(
            alerts=[
                make_alert(),
                make_alert(
                    labels={"alertname": "DemoSqlSlow", "job": "mysql", "instance": "db-1"},
                    fingerprint="aa11bb22",
                ),
            ]
        )
        resp = client.post("/ingest", json=batch)

        assert resp.status_code == 200
        assert resp.json() == {"received": 2, "deduped": 0}
        with Session(engine) as session:
            rows = session.scalars(select(AlertEvent)).all()
            assert len(rows) == 2

    def test_duplicate_alert_within_one_payload_deduped(self):
        """同一 payload 内两条一模一样的 alert → 第二条判重，只落 1 行。"""
        client, _engine = _make_client()
        resp = client.post("/ingest", json=make_webhook(alerts=[make_alert(), make_alert()]))
        assert resp.status_code == 200
        assert resp.json() == {"received": 2, "deduped": 1}


class TestIdempotency:
    def test_same_payload_replayed_3_times_yields_single_row(self):
        """D-13 幂等硬要求：原样重放 3 次 → 仍 1 条记录，dedup_count 不变。"""
        client, engine = _make_client()
        payload: dict[str, Any] = make_webhook()

        first = client.post("/ingest", json=payload)
        replays = [client.post("/ingest", json=payload) for _ in range(2)]

        assert first.json() == {"received": 1, "deduped": 0}
        for resp in replays:
            assert resp.status_code == 200
            assert resp.json() == {"received": 1, "deduped": 1}

        with Session(engine) as session:
            rows = session.scalars(select(AlertEvent)).all()
            assert len(rows) == 1
            assert rows[0].dedup_count == 1  # 原样重放不重复计数（03 主指纹接手窗口合并）

    def test_new_firing_of_same_alert_lands_new_row(self):
        """新一轮 firing（startsAt 变）→ 原始标识不同 → 各自成行（窗口合并属 issue 03）。"""
        client, engine = _make_client()
        client.post("/ingest", json=make_webhook())
        client.post(
            "/ingest",
            json=make_webhook(alerts=[make_alert(startsAt="2026-09-06T07:00:00.000Z")]),
        )

        with Session(engine) as session:
            rows = session.scalars(select(AlertEvent)).all()
            assert len(rows) == 2


class TestValidationFailure:
    def test_invalid_payload_returns_4xx_and_service_survives(self):
        """非法 payload → 4xx（AM 重试语义可见异常）；不吞错、后续请求正常。"""
        client, _engine = _make_client()

        bad_bodies = [
            {},  # 空对象
            {"status": "firing"},  # 缺 alerts
            {"status": "firing", "alerts": "not-a-list"},  # alerts 非数组
            {"status": "firing", "alerts": [{"labels": {}}]},  # alert 缺 startsAt/fingerprint
        ]
        for body in bad_bodies:
            resp = client.post("/ingest", json=body)
            assert 400 <= resp.status_code < 500, f"期望 4xx，实际 {resp.status_code}: {body}"

        # 服务未崩：合法请求照常落库
        resp = client.post("/ingest", json=make_webhook())
        assert resp.status_code == 200
        assert resp.json() == {"received": 1, "deduped": 0}
