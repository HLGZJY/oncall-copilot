"""真连 Prometheus 联调（issue 04 验收：Prometheus 在线时实测回填）。

默认跳过：ONCALL_RUN_INTEGRATION=1 时才跑（CI 不进默认门禁，单独 schedule）。
本文件豁免 A1 断网与 SDK 守卫（tests/integration/ 目录约定）。
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from oncall.context import ContextConfig, PromClient, collect_context, recent_metrics
from oncall.infra.http import HttpxFetcher

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ONCALL_RUN_INTEGRATION") != "1",
        reason="真连联调须显式 ONCALL_RUN_INTEGRATION=1（依赖本机 M0 栈）",
    ),
]

PROM_URL = os.environ.get("ONCALL_PROMETHEUS_URL", "http://127.0.0.1:9090")


def live_client() -> PromClient:
    config = ContextConfig(prometheus_url=PROM_URL)
    fetcher = HttpxFetcher(timeout=config.timeout)
    return PromClient(fetcher, base_url=config.prometheus_url, timeout=config.timeout)


def test_targets_live():
    result = live_client().targets()  # 不经 adapter，直接验客户端接缝
    assert len(result) > 0
    assert "labels" in result[0]


def test_query_range_live():
    client = live_client()
    now = datetime.now(UTC)
    series = client.query_range("up", start=now - timedelta(minutes=5), end=now, step="15s")
    assert len(series) > 0
    assert "values" in series[0]


def test_collect_context_live_with_real_alert_labels():
    """从 alert_events 取一条真实告警的 labels 跑通三源（验收第 1 条）。"""

    db_path = os.environ.get("ONCALL_SQLITE_PATH", "./oncall.db")
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT labels_json FROM alert_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "alert_events 为空：先打一轮告警再跑联调"
    raw_labels = row[0]
    labels = json.loads(raw_labels) if isinstance(raw_labels, str) else raw_labels

    config = ContextConfig(prometheus_url=PROM_URL)
    collected = collect_context(labels, config=config, client=live_client())
    sources = {s["source"]: s for s in collected["sources"]}
    assert sources["metrics"]["status"] == "ok"
    assert sources["topology"]["status"] == "ok"
    assert sources["changes"]["items"] == []


def test_recent_metrics_live_by_instance():
    client = live_client()
    result = recent_metrics(
        client,
        {"job": "demo-api-gw", "instance": "api-gw:8000"},
        config=ContextConfig(prometheus_url=PROM_URL),
    )
    assert result.status == "ok"
