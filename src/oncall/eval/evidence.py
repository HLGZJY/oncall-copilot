"""真实档 golden 证据面（m7 issue 07 / D-18 同源纪律，src 侧单源）。

取证四工具 Fetcher 替身：返回值全部派生自 golden `runs` 时间窗 + 告警时间线
实测切片——「查到的证据」与剧本同源；**`root_cause` / `investigation_path` /
`remediation` 三字段绝不进证据面**（真实 Planner 是被评对象，不能把答案喂进
prompt）。`tests/golden_support` 的 e2e 版委托本实现（单源不漂移）。

与 M3-08 先例的形状逐字对齐：data = {scenario, timeline, <工具载荷>}，
meta.source = "golden-dev-timeline"；真实 Planner 调查视图缺事件锚点的缺口
以「任意一次成功调用即可见全量 golden 上下文」补偿（评价公平）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from oncall.eval.golden import GoldenScenario
from oncall.harness.tools.schemas import ToolResult, ToolStatus

__all__ = ["make_golden_handlers"]


def _timeline_entries(golden: GoldenScenario) -> list[dict[str, Any]]:
    """展平全部 runs 的告警时间线（stub 证据的唯一数据源，D-18）。"""
    entries: list[dict[str, Any]] = []
    for i, run in enumerate(golden.runs):
        entries.extend({"run": i, **entry} for entry in run["alert_timeline"])  # type: ignore[union-attr]
    return entries


def _as_dt(value: Any) -> Any:
    """窗口参数兼容 datetime 与 ISO 字符串（planner JSON args 为字符串）。"""
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _window_overlaps(entry: dict[str, Any], start: Any, end: Any) -> bool:
    fired = datetime.fromisoformat(str(entry["fired_at"]))
    resolved = datetime.fromisoformat(str(entry["resolved_at"]))
    return fired <= end and resolved >= start  # type: ignore[operator]


def _time_anchor(golden: GoldenScenario) -> datetime:
    """时间锚（D-17 口径）：首个 run 的首条告警 fired_at。"""
    return datetime.fromisoformat(str(golden.runs[0]["alert_timeline"][0]["fired_at"]))  # type: ignore[index]


def _direction_in_window(golden: GoldenScenario, start: Any, end: Any) -> str:
    """异常方向由 golden 时间窗推导：窗口压到任一 firing 区间 → up，否则 flat。"""
    hit = any(_window_overlaps(entry, start, end) for entry in _timeline_entries(golden))
    return "up" if hit else "flat"


def make_golden_handlers(golden: GoldenScenario) -> dict[str, Any]:
    """取证四工具 Fetcher 替身（D-18 同源，零标注泄漏）。"""

    def timeline_evidence() -> dict[str, Any]:
        return {
            "runs": [
                {
                    "started_at": run.get("started_at"),
                    "recovered_at": run.get("recovered_at"),
                }
                for run in golden.runs
            ],
            "alerts": [
                {
                    "alert_name": entry["alert_name"],
                    "labels": entry["labels"],
                    "fired_at": entry["fired_at"],
                    "resolved_at": entry["resolved_at"],
                }
                for entry in _timeline_entries(golden)
            ],
        }

    def make(tool: str, payload_key: str, build: Any) -> Any:
        def handler(args: BaseModel, *, timeout_seconds: float) -> ToolResult:
            del timeout_seconds  # golden 替身即时返回，无真实 IO
            return ToolResult(
                tool=tool,
                status=ToolStatus.OK,
                data={
                    "scenario": golden.scenario,
                    "timeline": timeline_evidence(),
                    payload_key: build(args),
                },
                meta={"source": "golden-dev-timeline"},
            )

        return handler

    def metrics_payload(args: Any) -> dict[str, Any]:
        start = _as_dt(getattr(args, "start", None) or _time_anchor(golden))
        end = _as_dt(getattr(args, "end", None) or start)
        window_hits = [
            {"alert_name": e["alert_name"], "labels": e["labels"], "fired_at": e["fired_at"]}
            for e in _timeline_entries(golden)
            if _window_overlaps(e, start, end)
        ]
        return {
            "promql": getattr(args, "promql", "n/a"),
            "direction": _direction_in_window(golden, start, end),
            "alerts_in_window": window_hits,
        }

    def logs_payload(args: Any) -> dict[str, Any]:
        return {
            "selector": getattr(args, "selector", "n/a"),
            "lines": [
                f"{e['fired_at']} {e['alert_name']} fired labels={json.dumps(e['labels'])}"
                for e in _timeline_entries(golden)
            ],
        }

    def anomaly_payload(args: Any) -> dict[str, Any]:
        anchor = _time_anchor(golden)
        values = getattr(args, "values", [])
        return {
            "direction": _direction_in_window(golden, anchor, anchor) if values else "flat",
            "anomaly_windows": [
                {"alert_name": e["alert_name"], "fired_at": e["fired_at"], "run": e["run"]}
                for e in _timeline_entries(golden)
            ],
        }

    def topology_payload(args: Any) -> dict[str, Any]:
        entries = _timeline_entries(golden)
        return {
            "services": sorted({e["labels"].get("job", "n/a") for e in entries}),
            "instances": sorted({e["labels"].get("instance", "n/a") for e in entries}),
            "alert_names": sorted({e["alert_name"] for e in entries}),
        }

    return {
        "query_metrics": make("query_metrics", "metrics", metrics_payload),
        "search_logs": make("search_logs", "logs", logs_payload),
        "detect_anomaly": make("detect_anomaly", "anomaly", anomaly_payload),
        "get_topology": make("get_topology", "topology", topology_payload),
    }
