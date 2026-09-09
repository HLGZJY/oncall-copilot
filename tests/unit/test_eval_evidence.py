"""M7-T7 验收 · 真实档 golden 证据面下沉 src（D-18 同源纪律）。

`oncall.eval.evidence.make_golden_handlers` 是真实档矩阵装配的取证四工具
Fetcher 替身：返回值全部派生自 golden 实测切片（runs 时间窗 + 告警时间线），
**`root_cause` / `investigation_path` / `remediation` 三字段绝不进证据面**
（被评对象不能看答案）。tests/golden_support 的 e2e 版委托本实现（单源）。
"""

from __future__ import annotations

import json
from typing import Any

from oncall.eval.evidence import make_golden_handlers
from oncall.eval.golden import GoldenScenario


def _golden() -> GoldenScenario:
    return GoldenScenario.model_validate(
        {
            "scenario": "cpu-spike",
            "root_cause": "stress 进程把 cpu 打满导致事件循环饥饿",
            "remediation": "停探针并恢复 cpuset",
            "investigation_path": ["查 CPU 指标", "查日志确认 stress"],
            "runs": [
                {
                    "started_at": "2026-09-06T06:28:00+08:00",
                    "recovered_at": "2026-09-06T06:40:00+08:00",
                    "alert_timeline": [
                        {
                            "alert_name": "DemoApiGwHighLatency",
                            "labels": {"job": "api-gw", "instance": "api-gw-1"},
                            "fired_at": "2026-09-06T06:28:21+08:00",
                            "resolved_at": "2026-09-06T06:30:00+08:00",
                        }
                    ],
                }
            ],
        }
    )


def _tool_result(handlers: dict[str, Any], tool: str, args: dict[str, Any] | None = None) -> Any:
    handler = handlers[tool]
    return handler(type("Args", (), {"__dict__": args or {}})(), timeout_seconds=5.0)


def test_handlers_registered_for_four_evidence_tools():
    handlers = make_golden_handlers(_golden())
    assert set(handlers) == {"query_metrics", "search_logs", "detect_anomaly", "get_topology"}


def test_metrics_payload_window_hits_derived_from_timeline():
    handlers = make_golden_handlers(_golden())
    result = _tool_result(
        handlers,
        "query_metrics",
        {"promql": "up", "start": "2026-09-06T06:28:30+08:00", "end": "2026-09-06T06:28:40+08:00"},
    )
    data = result.data
    assert result.status.value == "ok"
    assert data["metrics"]["direction"] == "up"  # 窗口压到 firing 区间
    assert data["metrics"]["alerts_in_window"][0]["alert_name"] == "DemoApiGwHighLatency"


def test_topology_and_logs_derived_from_timeline():
    handlers = make_golden_handlers(_golden())
    topo = _tool_result(handlers, "get_topology").data["topology"]
    assert topo["services"] == ["api-gw"]
    logs = _tool_result(handlers, "search_logs", {"selector": '{job="api-gw}'}).data["logs"]
    assert "DemoApiGwHighLatency" in json.dumps(logs, ensure_ascii=False)


def test_evidence_face_never_leaks_annotations():
    """防泄漏守卫：root_cause / investigation_path / remediation 不出现在任何工具输出。"""
    handlers = make_golden_handlers(_golden())
    for tool in handlers:
        result = _tool_result(handlers, tool, {"promql": "up"})
        blob = json.dumps(result.data, ensure_ascii=False, default=str)
        assert "打满" not in blob and "stress" not in blob, f"{tool} 泄漏了标注语义"
        assert "investigation_path" not in blob and "remediation" not in blob
        assert result.meta["source"] == "golden-dev-timeline"


def test_result_shape_matches_e2e_precedent():
    """返回值形状与 tests/golden_support e2e 先例逐字对齐（委托单源后不漂移）。"""
    handlers = make_golden_handlers(_golden())
    result = _tool_result(handlers, "query_metrics")
    assert result.tool == "query_metrics"
    assert set(result.data) == {"scenario", "timeline", "metrics"}
    timeline = result.data["timeline"]
    assert set(timeline) == {"runs", "alerts"}
    assert timeline["alerts"][0]["labels"] == {"job": "api-gw", "instance": "api-gw-1"}
