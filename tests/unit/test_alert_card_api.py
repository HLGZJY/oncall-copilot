"""T5 验收测试：事件卡片 JSON 契约 + 查询路由（issue 05 / G3 定案）。

契约来源：CONTEXT.md「事件卡片 / Alert Card」（≠ 事件/Incident）、
docs/design/m1-alert-ingestion-design.md §API 变更表、decisions.md D-17（键名登记）。

覆盖：
- 固定键名断言：卡片顶层 {alert, context, generated_at}；alert 本体 13 键；
  context.sources = metrics/topology/changes 三源，每源 {source, status, items, meta}
- 本体字段与落库一致（归一化字段 = alert_events 行 + annotations 溯源）
- 不存在的 id → 404
- Prometheus 不可达 → 卡片仍 200，对应源显式 unavailable（0 漏收延伸，不 5xx）
- 合并告警 → dedup_count 体现进卡片（issue 05 验收：对 03 产出可读）
- GET /alerts 列表：分页 + 按指纹过滤

断网纪律（A1）：不 import httpx——上下文经 create_app 注入 FakePromClient；
TestClient 走进程内 ASGI（inproc_asgi 豁免断网 fixture）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session

from conftest import AM_FINGERPRINT, FIRING_STARTS, make_alert, make_webhook
from oncall.context.config import ContextConfig
from oncall.db import AlertEvent, create_tables
from oncall.infra.http import HTTPClientError
from oncall.ingest.app import create_app

# TestClient 走进程内 ASGI 传输，无真实网络 IO（pytest-socket 豁免，同 test_ingest_api）
pytestmark = pytest.mark.inproc_asgi

# ── D-17 契约键名（评审定案后不再改；测试即守卫） ──
CARD_KEYS = {"alert", "context", "generated_at"}
ALERT_KEYS = {
    "id",
    "fingerprint",
    "source",
    "status",
    "labels",
    "annotations",
    "am_fingerprint",
    "raw_alert",
    "webhook",
    "fired_at",
    "last_fired_at",
    "resolved_at",
    "dedup_count",
}
SOURCE_KEYS = {"source", "status", "items", "meta"}
SOURCE_NAMES = ("metrics", "topology", "changes")  # collect_context 固定顺序
LIST_KEYS = {"items", "total", "limit", "offset"}

PROM_URL = "http://prom.test:9090"


class FakePromClient:
    """PromClient 测试替身：error 非空时抛传输错误模拟 Prometheus 不可达。"""

    def __init__(
        self,
        series: list[dict] | None = None,
        target_list: list[dict] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.series = (
            series
            if series is not None
            else [
                {
                    "metric": {"__name__": "demo_latency_seconds", "job": "api-gw"},
                    "values": [[0, "0.5"]],
                }
            ]
        )
        self._targets = (
            target_list
            if target_list is not None
            else [{"labels": {"job": "api-gw", "instance": "api-gw-1"}, "health": "up"}]
        )
        self.error = error

    def query_range(self, query: str, *, start, end, step) -> list[dict]:
        if self.error is not None:
            raise self.error
        return list(self.series)

    def targets(self) -> list[dict]:
        if self.error is not None:
            raise self.error
        return list(self._targets)


def _make_app(client: FakePromClient | None = None):
    """内存库 + TestClient：上下文客户端注入替身（A1 断网合规）。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_tables(engine)
    app = create_app(
        engine,
        context_config=ContextConfig(prometheus_url=PROM_URL),
        context_client=client or FakePromClient(),
    )
    return TestClient(app), engine


def _seed_alert(client: TestClient) -> None:
    resp = client.post("/ingest", json=make_webhook())
    assert resp.status_code == 200


class TestCardContract:
    def test_card_top_level_and_source_keys_are_fixed(self):
        """契约键名一次到位：顶层/本体/三源键集合精确匹配（D-17）。"""
        client, _engine = _make_app()
        _seed_alert(client)

        resp = client.get("/alerts/1/context")

        assert resp.status_code == 200
        card = resp.json()
        assert set(card) == CARD_KEYS
        assert set(card["alert"]) == ALERT_KEYS
        sources = card["context"]["sources"]
        assert [s["source"] for s in sources] == list(SOURCE_NAMES)
        for source in sources:
            assert set(source) == SOURCE_KEYS
            assert source["status"] == "ok"

    def test_alert_body_matches_persisted_row(self):
        """本体 = alert_events 行 + annotations 溯源，字段值与落库一致。"""
        client, engine = _make_app()
        _seed_alert(client)

        card = client.get("/alerts/1/context").json()
        body = card["alert"]

        with Session(engine) as session:
            row = session.get(AlertEvent, 1)
        assert body["fingerprint"] == row.fingerprint
        assert body["labels"]["alertname"] == "DemoApiGwHighLatency"
        assert body["labels"]["severity"] == "warning"  # 非 canonical label 全量保留
        assert body["annotations"]["summary"].startswith("api-gw")
        assert body["am_fingerprint"] == AM_FINGERPRINT
        assert body["raw_alert"]["labels"]["alertname"] == "DemoApiGwHighLatency"
        assert body["dedup_count"] == 1
        assert body["status"] == "deduped"
        assert body["resolved_at"] is None
        assert body["fired_at"].startswith(FIRING_STARTS[:19])  # ISO8601 UTC
        assert card["context"]["sources"][0]["items"][0]["metric"]["job"] == "api-gw"

    def test_generated_at_is_present(self):
        client, _engine = _make_app()
        _seed_alert(client)
        card = client.get("/alerts/1/context").json()
        assert "T" in card["generated_at"]  # ISO8601 时间戳


class TestCardFailurePaths:
    def test_missing_alert_returns_404(self):
        client, _engine = _make_app()
        resp = client.get("/alerts/999/context")
        assert resp.status_code == 404

    def test_prometheus_down_card_still_200_with_unavailable_sources(self):
        """Prometheus 不可达：卡片照常返回，对应源显式 unavailable（不 5xx、不留 null）。"""
        client, _engine = _make_app(FakePromClient(error=HTTPClientError("connection refused")))
        _seed_alert(client)

        resp = client.get("/alerts/1/context")

        assert resp.status_code == 200
        sources = {s["source"]: s for s in resp.json()["context"]["sources"]}
        assert sources["metrics"]["status"] == "unavailable"
        assert "reason" in sources["metrics"]["meta"]
        assert sources["metrics"]["items"] == []
        assert sources["topology"]["status"] == "unavailable"
        # changes 是占位 adapter（G4），不触网，恒 ok + 空
        assert sources["changes"]["status"] == "ok"
        assert sources["changes"]["items"] == []


class TestMergedAlertCard:
    def test_merged_alert_exposes_dedup_count(self):
        """issue 05 验收：对 03 产出的合并告警，卡片字段齐全且 dedup_count 正确。"""
        client, _engine = _make_app()
        client.post("/ingest", json=make_webhook())
        client.post(
            "/ingest",
            json=make_webhook(alerts=[make_alert(startsAt="2026-09-06T06:30:00.000Z")]),
        )

        card = client.get("/alerts/1/context").json()

        assert card["alert"]["dedup_count"] == 2
        assert set(card) == CARD_KEYS


class TestAlertsList:
    def test_list_returns_alert_bodies_with_total(self):
        client, _engine = _make_app()
        client.post(
            "/ingest",
            json=make_webhook(
                alerts=[
                    make_alert(),
                    make_alert(
                        labels={"alertname": "DemoSqlSlow", "job": "mysql", "instance": "db-1"},
                        fingerprint="aa11bb22",
                    ),
                ]
            ),
        )

        resp = client.get("/alerts")

        assert resp.status_code == 200
        listing = resp.json()
        assert set(listing) == LIST_KEYS
        assert listing["total"] == 2
        assert len(listing["items"]) == 2
        for body in listing["items"]:
            assert set(body) == ALERT_KEYS
            assert "context" not in body  # 列表只给本体，上下文只在单卡接口

    def test_list_pagination_and_fingerprint_filter(self):
        client, engine = _make_app()
        client.post(
            "/ingest",
            json=make_webhook(
                alerts=[
                    make_alert(),
                    make_alert(
                        labels={"alertname": "DemoSqlSlow", "job": "mysql", "instance": "db-1"},
                        fingerprint="aa11bb22",
                    ),
                ]
            ),
        )
        with Session(engine) as session:
            fp = session.get(AlertEvent, 1).fingerprint

        page = client.get("/alerts?limit=1&offset=1").json()
        assert page["total"] == 2
        assert len(page["items"]) == 1
        assert page["limit"] == 1
        assert page["offset"] == 1

        filtered = client.get(f"/alerts?fingerprint={fp}").json()
        assert filtered["total"] == 1
        assert filtered["items"][0]["fingerprint"] == fp
