"""上下文三源单测（issue 04，TDD 接缝）。

断网纪律（A1）：不 import httpx——HTTP 层用 FakeFetcher 测试替身注入
PromClient；真连联调在 tests/integration/test_prometheus_live.py（integration 标记）。

统一返回形状：{source, status: ok|unavailable, items, meta}——
Prometheus 不可达时降级为 unavailable，不抛错、不 5xx（0 漏收纪律：
上下文缺失 ≠ 告警缺失）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from oncall.context import (
    SOURCE_CHANGES,
    SOURCE_METRICS,
    SOURCE_TOPOLOGY,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    ContextConfig,
    PromClient,
    collect_context,
    recent_changes,
    recent_metrics,
    service_topology,
)
from oncall.infra.http import HTTPClientError, HttpResponse

FIXED_NOW = datetime(2026, 9, 7, 6, 0, 0, tzinfo=UTC)
QUERY_RANGE_URL = "http://prom.test:9090/api/v1/query_range"
TARGETS_URL = "http://prom.test:9090/api/v1/targets"

LABELS = {"alertname": "DemoApiGwHighLatency", "job": "api-gw", "instance": "api-gw-1"}


def prom_response(payload: dict[str, Any]) -> HttpResponse:
    return HttpResponse(status_code=200, body=json.dumps(payload))


def matrix_payload(result: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": "success", "data": {"resultType": "matrix", "result": result}}


def sample_series() -> list[dict[str, Any]]:
    return [
        {
            "metric": {"__name__": "demo_latency_seconds", "job": "api-gw", "instance": "api-gw-1"},
            "values": [[1788736800, "0.51"], [1788736830, "0.62"]],
        }
    ]


class FakeFetcher:
    """Fetcher 测试替身：按 URL 前缀路由预置响应；error 非空时抛传输错误。"""

    def __init__(
        self,
        routes: dict[str, HttpResponse] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.routes = routes or {}
        self.error = error
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, *, params: dict[str, str], timeout: float) -> HttpResponse:
        if self.error is not None:
            raise self.error
        self.calls.append((url, dict(params)))
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                return response
        raise AssertionError(f"FakeFetcher 未预置该 URL 的响应: {url}")


def make_client(fetcher: FakeFetcher, config: ContextConfig | None = None) -> PromClient:
    cfg = config or ContextConfig(prometheus_url="http://prom.test:9090")
    return PromClient(fetcher, base_url=cfg.prometheus_url, timeout=cfg.timeout)


# ───────────────────────── 近期指标源 ─────────────────────────


class TestRecentMetrics:
    def test_ok_returns_series_with_unified_shape(self):
        fetcher = FakeFetcher({QUERY_RANGE_URL: prom_response(matrix_payload(sample_series()))})
        result = recent_metrics(make_client(fetcher), LABELS, config=ContextConfig(), now=FIXED_NOW)
        assert result.source == SOURCE_METRICS
        assert result.status == STATUS_OK
        assert len(result.items) == 1
        assert result.items[0]["metric"]["__name__"] == "demo_latency_seconds"
        assert "query" in result.meta and "window_seconds" in result.meta

    def test_query_filters_by_instance_and_job(self):
        fetcher = FakeFetcher({QUERY_RANGE_URL: prom_response(matrix_payload([]))})
        recent_metrics(make_client(fetcher), LABELS, config=ContextConfig(), now=FIXED_NOW)
        url, params = fetcher.calls[0]
        assert url == QUERY_RANGE_URL
        assert 'job="api-gw"' in params["query"] and 'instance="api-gw-1"' in params["query"]
        # 默认窗口 30m：start = end - 1800s
        assert float(params["end"]) - float(params["start"]) == 1800.0

    def test_window_is_configurable_not_hardcoded(self):
        fetcher = FakeFetcher({QUERY_RANGE_URL: prom_response(matrix_payload([]))})
        config = ContextConfig(window=timedelta(minutes=15), step="15s")
        result = recent_metrics(make_client(fetcher), LABELS, config=config, now=FIXED_NOW)
        _, params = fetcher.calls[0]
        assert result.meta["window_seconds"] == 900
        assert params["step"] == "15s"
        assert float(params["end"]) - float(params["start"]) == 900.0

    def test_transport_error_degrades_to_unavailable(self):
        fetcher = FakeFetcher(error=HTTPClientError("connection refused"))
        result = recent_metrics(make_client(fetcher), LABELS, config=ContextConfig(), now=FIXED_NOW)
        assert result.status == STATUS_UNAVAILABLE
        assert result.items == ()
        assert "reason" in result.meta

    def test_prom_api_error_degrades_to_unavailable(self):
        bad = prom_response({"status": "error", "errorType": "bad_data", "error": "parse error"})
        fetcher = FakeFetcher({QUERY_RANGE_URL: bad})
        result = recent_metrics(make_client(fetcher), LABELS, config=ContextConfig(), now=FIXED_NOW)
        assert result.status == STATUS_UNAVAILABLE

    def test_malformed_json_degrades_to_unavailable(self):
        fetcher = FakeFetcher({QUERY_RANGE_URL: HttpResponse(status_code=200, body="not json{")})
        result = recent_metrics(make_client(fetcher), LABELS, config=ContextConfig(), now=FIXED_NOW)
        assert result.status == STATUS_UNAVAILABLE

    def test_empty_series_is_ok_with_empty_items(self):
        fetcher = FakeFetcher({QUERY_RANGE_URL: prom_response(matrix_payload([]))})
        result = recent_metrics(make_client(fetcher), LABELS, config=ContextConfig(), now=FIXED_NOW)
        assert result.status == STATUS_OK
        assert result.items == ()

    def test_selector_escapes_label_value_quotes(self):
        fetcher = FakeFetcher({QUERY_RANGE_URL: prom_response(matrix_payload([]))})
        labels = {"job": 'weird"job\\x', "instance": "i1"}
        recent_metrics(make_client(fetcher), labels, config=ContextConfig(), now=FIXED_NOW)
        _, params = fetcher.calls[0]
        assert 'job="weird\\"job\\\\x"' in params["query"]


# ───────────────────────── 服务拓扑源 ─────────────────────────


def targets_payload() -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "activeTargets": [
                {
                    "labels": {"job": "api-gw", "instance": "api-gw-1"},
                    "health": "up",
                    "lastError": "",
                    "scrapeUrl": "http://api-gw-1:8000/metrics",
                },
                {
                    "labels": {"job": "worker", "instance": "worker-1"},
                    "health": "down",
                    "lastError": "connection refused",
                    "scrapeUrl": "http://worker-1:8001/metrics",
                },
            ]
        },
    }


class TestServiceTopology:
    def test_ok_returns_targets_with_labels(self):
        fetcher = FakeFetcher({TARGETS_URL: prom_response(targets_payload())})
        result = service_topology(make_client(fetcher), config=ContextConfig())
        assert result.source == SOURCE_TOPOLOGY
        assert result.status == STATUS_OK
        assert len(result.items) == 2
        assert result.items[0]["labels"]["job"] == "api-gw"
        assert result.items[0]["health"] == "up"
        assert set(result.meta["services"]) == {"api-gw", "worker"}

    def test_transport_error_degrades_to_unavailable(self):
        fetcher = FakeFetcher(error=HTTPClientError("timeout"))
        result = service_topology(make_client(fetcher), config=ContextConfig())
        assert result.status == STATUS_UNAVAILABLE
        assert result.items == ()


# ───────────────────────── 近期变更源（占位） ─────────────────────────


class TestRecentChanges:
    def test_placeholder_returns_empty_list_without_error(self):
        result = recent_changes(config=ContextConfig())
        assert result.source == SOURCE_CHANGES
        assert result.status == STATUS_OK
        assert result.items == ()


# ───────────────────────── 三源编排与统一形状 ─────────────────────────


class TestCollectContext:
    def test_three_sources_present_with_unified_shape(self):
        fetcher = FakeFetcher(
            {
                QUERY_RANGE_URL: prom_response(matrix_payload(sample_series())),
                TARGETS_URL: prom_response(targets_payload()),
            }
        )
        config = ContextConfig(prometheus_url="http://prom.test:9090")
        collected = collect_context(
            LABELS, config=config, client=make_client(fetcher, config), now=FIXED_NOW
        )
        sources = collected["sources"]
        assert [s["source"] for s in sources] == [SOURCE_METRICS, SOURCE_TOPOLOGY, SOURCE_CHANGES]
        for entry in sources:
            assert set(entry) == {"source", "status", "items", "meta"}

    def test_prometheus_down_all_sources_degrade_without_raise(self):
        fetcher = FakeFetcher(error=HTTPClientError("WinError 10061"))
        config = ContextConfig(prometheus_url="http://prom.test:9090")
        collected = collect_context(
            LABELS, config=config, client=make_client(fetcher, config), now=FIXED_NOW
        )
        statuses = {s["source"]: s["status"] for s in collected["sources"]}
        assert statuses[SOURCE_METRICS] == STATUS_UNAVAILABLE
        assert statuses[SOURCE_TOPOLOGY] == STATUS_UNAVAILABLE
        assert statuses[SOURCE_CHANGES] == STATUS_OK  # 变更源是占位，不依赖 Prometheus


# ───────────────────────── 配置 ─────────────────────────


class TestContextConfig:
    def test_defaults(self):
        config = ContextConfig()
        assert config.window == timedelta(minutes=30)
        assert config.timeout > 0

    def test_from_env_overrides(self):
        config = ContextConfig.from_env(
            {
                "ONCALL_PROMETHEUS_URL": "http://prom:9090",
                "ONCALL_CONTEXT_WINDOW_SECONDS": "900",
                "ONCALL_PROM_TIMEOUT_SECONDS": "2.5",
            }
        )
        assert config.prometheus_url == "http://prom:9090"
        assert config.window == timedelta(minutes=15)
        assert config.timeout == 2.5

    def test_from_env_defaults_when_missing(self):
        config = ContextConfig.from_env({})
        assert config.window == timedelta(minutes=30)
        assert config.prometheus_url

    @pytest.mark.parametrize("bad", ["abc", "-1", ""])
    def test_from_env_invalid_window_falls_back_to_default(self, bad: str):
        config = ContextConfig.from_env({"ONCALL_CONTEXT_WINDOW_SECONDS": bad})
        assert config.window == timedelta(minutes=30)
