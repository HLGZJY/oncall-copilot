"""M3-03 取证四工具测试（G2①②⑤ / G3 / D-16 / D-23 / D-24）。

- Fetcher 替身注入（C4 收口），零真实网络，断网单测全绿
- 每工具四态（ok/empty/error/unavailable）+ 边界行为单测
- 时间锚缺省：query_metrics 走 `default_time_window` helper（schema 冻结 start/end
  必填，缺省填充发生在参数组装侧，issue 06/07 调用）；search_logs 的 end 缺省在
  handler 内锚定 `last_fired_at`（D-17 时间锚）
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from oncall.context.config import ContextConfig
from oncall.harness.tools.anomaly import detect_anomaly_handler
from oncall.harness.tools.logs import build_search_logs_handler
from oncall.harness.tools.metrics import MAX_SERIES, build_query_metrics_handler
from oncall.harness.tools.registry import (
    TOOL_NAMES,
    ToolParamError,
    ToolRegistry,
    ToolStatus,
    register_six_tools,
)
from oncall.harness.tools.schemas import (
    DetectAnomalyInput,
    GetTopologyInput,
    QueryMetricsInput,
    SearchLogsInput,
)
from oncall.harness.tools.sources import default_time_window
from oncall.harness.tools.topology import build_get_topology_handler
from oncall.infra.http import HTTPClientError, HttpResponse

FIXED_NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
ANCHOR = FIXED_NOW  # incident.alert.last_fired_at（D-17）
LOKI_URL = "http://loki:3100"
CONFIG = ContextConfig()


class FakeFetcher:
    """Fetcher 替身：按序回放 HttpResponse / 异常，记录调用供断言。"""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, *, params: Any, timeout: float) -> HttpResponse:
        self.calls.append({"url": url, "params": dict(params), "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def prom_body(series: list[dict[str, Any]]) -> str:
    return json.dumps({"status": "success", "data": {"result": series}})


def loki_body(streams: list[dict[str, Any]]) -> str:
    return json.dumps({"status": "success", "data": {"resultType": "streams", "result": streams}})


def make_handlers(fetcher: FakeFetcher) -> dict[str, Any]:
    return {
        "query_metrics": build_query_metrics_handler(fetcher, config=CONFIG),
        "search_logs": build_search_logs_handler(
            fetcher, loki_url=LOKI_URL, timeout=5.0, anchor=ANCHOR
        ),
        "detect_anomaly": detect_anomaly_handler,
        "get_topology": build_get_topology_handler(fetcher, config=CONFIG),
    }


class TestDefaultTimeWindow:
    def test_anchor_minus_window_to_anchor(self) -> None:
        start, end = default_time_window(ANCHOR, CONFIG.window)
        assert start == ANCHOR - timedelta(minutes=30)
        assert end == ANCHOR

    def test_custom_window(self) -> None:
        start, end = default_time_window(ANCHOR, timedelta(minutes=5))
        assert (end - start) == timedelta(minutes=5)


class TestQueryMetrics:
    def make(self, responses: list[Any]) -> tuple[Any, Any]:
        fetcher = FakeFetcher(responses)
        return fetcher, build_query_metrics_handler(fetcher, config=CONFIG)

    def args(self, promql: str = "up") -> dict[str, Any]:
        return {"promql": promql, "start": ANCHOR.isoformat(), "end": ANCHOR.isoformat()}

    def test_ok_single_series(self) -> None:
        fetcher, handler = self.make(
            [HttpResponse(200, prom_body([{"metric": {"__name__": "up"}, "values": [[1, "1"]]}]))]
        )
        result = handler(self._validated(), timeout_seconds=30.0)
        assert result.tool == "query_metrics"
        assert result.status is ToolStatus.OK
        assert result.data is not None and len(result.data["series"]) == 1
        assert result.meta["truncated"] is False
        assert fetcher.calls[0]["url"].endswith("/api/v1/query_range")

    def _validated(self) -> Any:
        return QueryMetricsInput(
            promql="up",
            start=ANCHOR - timedelta(minutes=30),
            end=ANCHOR,
        )

    def test_empty_result(self) -> None:
        _, handler = self.make([HttpResponse(200, prom_body([]))])
        result = handler(self._validated(), timeout_seconds=30.0)
        assert result.status is ToolStatus.EMPTY

    def test_error_non_2xx(self) -> None:
        _, handler = self.make([HttpResponse(503, "timeout")])
        result = handler(self._validated(), timeout_seconds=30.0)
        assert result.status is ToolStatus.ERROR
        assert "reason" in result.meta

    def test_error_bad_json(self) -> None:
        _, handler = self.make([HttpResponse(200, "<html>not json</html>")])
        result = handler(self._validated(), timeout_seconds=30.0)
        assert result.status is ToolStatus.ERROR

    def test_error_status_not_success(self) -> None:
        _, handler = self.make([HttpResponse(200, json.dumps({"status": "error", "error": "x"}))])
        result = handler(self._validated(), timeout_seconds=30.0)
        assert result.status is ToolStatus.ERROR

    def test_unavailable_transport(self) -> None:
        _, handler = self.make([HTTPClientError("connect refused")])
        result = handler(self._validated(), timeout_seconds=30.0)
        assert result.status is ToolStatus.UNAVAILABLE
        assert "reason" in result.meta

    def test_series_truncated_to_20(self) -> None:
        many = [{"metric": {"i": str(i)}, "values": [[1, "1"]]} for i in range(MAX_SERIES + 5)]
        _, handler = self.make([HttpResponse(200, prom_body(many))])
        result = handler(self._validated(), timeout_seconds=30.0)
        assert result.status is ToolStatus.OK
        assert result.data is not None and len(result.data["series"]) == MAX_SERIES
        assert result.meta["truncated"] is True
        assert result.meta["series_total"] == MAX_SERIES + 5

    def test_unbalanced_promql_is_error_without_http(self) -> None:
        fetcher, handler = self.make([])
        args = QueryMetricsInput(promql="up{", start=ANCHOR, end=ANCHOR)
        result = handler(args, timeout_seconds=30.0)
        assert result.status is ToolStatus.ERROR
        assert fetcher.calls == []

    def test_timeout_passed_down_to_fetcher(self) -> None:
        fetcher, handler = self.make(
            [HttpResponse(200, prom_body([{"metric": {}, "values": [[1, "1"]]}]))]
        )
        handler(self._validated(), timeout_seconds=2.0)
        assert fetcher.calls[0]["timeout"] == 2.0  # min(config 5.0, 传入 2.0)

    def test_via_registry_end_to_end(self) -> None:
        fetcher = FakeFetcher([HttpResponse(200, prom_body([]))])
        registry = ToolRegistry(now=lambda: FIXED_NOW)
        register_six_tools(registry, make_handlers(fetcher))
        execution = registry.execute("query_metrics", self.args())
        assert execution.result.status is ToolStatus.EMPTY


class TestSearchLogs:
    def make(self, responses: list[Any]) -> tuple[FakeFetcher, Any]:
        fetcher = FakeFetcher(responses)
        return fetcher, build_search_logs_handler(
            fetcher, loki_url=LOKI_URL, timeout=5.0, anchor=ANCHOR
        )

    def _validated(self, **overrides: Any) -> Any:
        raw: dict[str, Any] = {
            "selector": '{job="api-gw"}',
            "start": ANCHOR - timedelta(minutes=30),
            "end": None,
            "limit": 100,
            "direction": "backward",
        }
        raw.update(overrides)
        return SearchLogsInput(**raw)

    def test_ok_stream(self) -> None:
        _fetcher, handler = self.make(
            [HttpResponse(200, loki_body([{"stream": {"job": "api-gw"}, "values": [["1", "x"]]}]))]
        )
        result = handler(self._validated(), timeout_seconds=30.0)
        assert result.status is ToolStatus.OK
        assert result.data is not None and len(result.data["streams"]) == 1

    def test_empty(self) -> None:
        _, handler = self.make([HttpResponse(200, loki_body([]))])
        result = handler(self._validated(), timeout_seconds=30.0)
        assert result.status is ToolStatus.EMPTY

    def test_error_non_2xx_and_bad_json(self) -> None:
        for response in (HttpResponse(500, "oops"), HttpResponse(200, "not-json")):
            _, handler = self.make([response])
            result = handler(self._validated(), timeout_seconds=30.0)
            assert result.status is ToolStatus.ERROR

    def test_unavailable_transport(self) -> None:
        _, handler = self.make([HTTPClientError("loki down")])
        result = handler(self._validated(), timeout_seconds=30.0)
        assert result.status is ToolStatus.UNAVAILABLE

    def test_end_defaults_to_anchor(self) -> None:
        fetcher, handler = self.make([HttpResponse(200, loki_body([]))])
        handler(self._validated(end=None), timeout_seconds=30.0)
        params = fetcher.calls[0]["params"]
        expected_ns = str(int(ANCHOR.timestamp() * 1_000_000_000))
        assert params["end"] == expected_ns
        assert params["direction"] == "backward"
        assert params["limit"] == "100"

    def test_limit_and_direction_forward(self) -> None:
        fetcher, handler = self.make([HttpResponse(200, loki_body([]))])
        handler(self._validated(limit=50, direction="forward"), timeout_seconds=30.0)
        params = fetcher.calls[0]["params"]
        assert params["limit"] == "50"
        assert params["direction"] == "forward"

    def test_unbalanced_selector_is_error_without_http(self) -> None:
        fetcher, handler = self.make([])
        result = handler(self._validated(selector="{job="), timeout_seconds=30.0)
        assert result.status is ToolStatus.ERROR
        assert fetcher.calls == []


class TestDetectAnomaly:
    def run(self, values: list[float]) -> Any:
        timestamps = [ANCHOR + timedelta(seconds=i) for i in range(len(values))]
        return detect_anomaly_handler(
            DetectAnomalyInput(values=values, timestamps=timestamps), timeout_seconds=30.0
        )

    def test_normal_flat_is_empty(self) -> None:
        result = self.run([10.0] * 12)
        assert result.status is ToolStatus.EMPTY

    def test_spike_detected(self) -> None:
        values = [10.0] * 8 + [100.0] + [10.0] * 3
        result = self.run(values)
        assert result.status is ToolStatus.OK
        anomalies = result.data["anomalies"]
        assert 8 in [a["index"] for a in anomalies]
        assert "stat-v1" in result.meta["method"]

    def test_level_drift_detected(self) -> None:
        values = [10.0, 11.0, 9.0, 10.0, 12.0, 10.0, 50.0, 52.0, 48.0, 50.0, 55.0, 50.0]
        result = self.run(values)
        assert result.status is ToolStatus.OK
        flagged = [a["index"] for a in result.data["anomalies"]]
        assert any(i >= 6 for i in flagged)

    def test_too_short_is_error(self) -> None:
        result = self.run([1.0, 2.0])
        assert result.status is ToolStatus.ERROR

    def test_empty_values_via_schema(self) -> None:
        registry = ToolRegistry(now=lambda: FIXED_NOW)
        register_six_tools(registry, make_handlers(FakeFetcher([])))
        with pytest.raises(ToolParamError):
            registry.execute("detect_anomaly", {"values": [], "timestamps": []})


class TestGetTopology:
    def make(self, responses: list[Any]) -> tuple[FakeFetcher, Any]:
        fetcher = FakeFetcher(responses)
        return fetcher, build_get_topology_handler(fetcher, config=CONFIG)

    def targets_body(self, targets: list[dict[str, Any]]) -> str:
        return json.dumps({"status": "success", "data": {"activeTargets": targets}})

    def _validated(self, service: str | None = None) -> Any:
        return GetTopologyInput(service=service)

    def test_ok_d16_shape(self) -> None:
        body = self.targets_body(
            [{"labels": {"job": "api-gw", "instance": "api-gw-1"}, "health": "up"}]
        )
        _, handler = self.make([HttpResponse(200, body)])
        result = handler(self._validated(), timeout_seconds=30.0)
        assert result.status is ToolStatus.OK
        assert result.data is not None
        assert result.data["source"] == "topology"
        assert result.data["status"] == "ok"
        assert isinstance(result.data["items"], list)

    def test_unavailable_degraded(self) -> None:
        _, handler = self.make([HTTPClientError("prom down")])
        result = handler(self._validated(), timeout_seconds=30.0)
        assert result.status is ToolStatus.UNAVAILABLE
        assert "reason" in result.meta

    def test_empty_targets(self) -> None:
        _, handler = self.make([HttpResponse(200, self.targets_body([]))])
        result = handler(self._validated(), timeout_seconds=30.0)
        assert result.status is ToolStatus.EMPTY

    def test_service_filter_no_match_is_empty(self) -> None:
        body = self.targets_body([{"labels": {"job": "api-gw"}, "health": "up"}])
        _, handler = self.make([HttpResponse(200, body)])
        result = handler(self._validated(service="db"), timeout_seconds=30.0)
        assert result.status is ToolStatus.EMPTY


class TestRegisterForensicTools:
    def test_four_handlers_complete_six_registration(self) -> None:
        registry = ToolRegistry(now=lambda: FIXED_NOW)
        register_six_tools(registry, make_handlers(FakeFetcher([])))
        assert registry.names() == set(TOOL_NAMES)

    def test_missing_handler_rejected(self) -> None:
        registry = ToolRegistry(now=lambda: FIXED_NOW)
        handlers = make_handlers(FakeFetcher([]))
        del handlers["detect_anomaly"]
        with pytest.raises(ValueError, match="需注入执行函数"):
            register_six_tools(registry, handlers)
