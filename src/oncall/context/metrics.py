"""近期指标源（G4 必做）：按 alert labels（instance/job）查近 N 分钟序列。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from oncall.context.config import ContextConfig
from oncall.context.models import SOURCE_METRICS, STATUS_OK, STATUS_UNAVAILABLE, SourceResult
from oncall.context.promql import UNAVAILABLE_ERRORS, PromClient

FILTER_LABELS: tuple[str, ...] = ("job", "instance")  # issue 04 口径：instance/job 过滤


def escape_label_value(value: str) -> str:
    """PromQL 字符串字面量转义（反斜杠 → 引号 → 换行，顺序不可换）。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def build_selector(labels: Mapping[str, str]) -> str:
    """alert 相关 label → PromQL selector。

    instance/job 存在者参与；两者皆缺时退回 alertname（至少能圈定告警相关
    序列）；再缺则空 selector（调用方 items 自然为空，不臆造过滤面）。
    """
    names: tuple[str, ...] = FILTER_LABELS
    if not any(labels.get(name) for name in names):
        names = ("alertname",)
    matchers = [
        f'{name}="{escape_label_value(labels[name])}"' for name in names if labels.get(name)
    ]
    return "{" + ", ".join(matchers) + "}"


def recent_metrics(
    client: PromClient,
    labels: Mapping[str, str],
    *,
    config: ContextConfig,
    now: datetime | None = None,
) -> SourceResult:
    """近 config.window 的序列；Prometheus 不可达 → unavailable 降级，不抛错。"""
    moment = now or datetime.now(UTC)
    query = build_selector(labels)
    try:
        result = client.query_range(
            query,
            start=moment - config.window,
            end=moment,
            step=config.step,
        )
    except UNAVAILABLE_ERRORS as exc:
        return SourceResult(SOURCE_METRICS, STATUS_UNAVAILABLE, (), {"reason": str(exc)})
    return SourceResult(
        SOURCE_METRICS,
        STATUS_OK,
        tuple(result),
        {
            "query": query,
            "window_seconds": int(config.window.total_seconds()),
            "step": config.step,
            "start": (moment - config.window).isoformat(),
            "end": moment.isoformat(),
            "series_count": len(result),
        },
    )
