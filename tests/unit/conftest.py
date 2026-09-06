"""unit 测试共用 fixture：Alertmanager webhook v4 payload 构造器。

字段结构对齐 deploy/alerts-dump.jsonl 的 M0-03 实测样本（issue 02 联调基准），
不做任何字段简化假象——severity/generatorURL 等易变字段一律保留。
"""

from __future__ import annotations

from typing import Any

FIRING_STARTS = "2026-09-06T06:28:21.474Z"
RESOLVED_ENDS = "2026-09-06T06:28:41.474Z"
ZERO_ENDS = "0001-01-01T00:00:00Z"  # Alertmanager 的 endsAt 零值哨兵（未恢复）
AM_FINGERPRINT = "4df3b6985b707f0b"  # AM 自带全 labels FNV-1a（易变，仅溯源，D-13）


def make_alert(**overrides: Any) -> dict[str, Any]:
    """单条 alert（v4 契约字段齐全，含易变字段）。"""
    alert: dict[str, Any] = {
        "status": "firing",
        "labels": {
            "alertname": "DemoApiGwHighLatency",
            "job": "api-gw",
            "instance": "api-gw-1",
            "severity": "warning",
            "scenario": "cpu-spike",
        },
        "annotations": {
            "summary": "api-gw 非 DB 路径 P95 延迟超过 0.5s",
            "description": "疑似 CPU 资源被占用导致事件循环饥饿",
        },
        "startsAt": FIRING_STARTS,
        "endsAt": ZERO_ENDS,
        "generatorURL": "http://demo-prometheus:9090/graph?g0.expr=demo_latency%3E0.25",
        "fingerprint": AM_FINGERPRINT,
    }
    alert.update(overrides)
    return alert


def make_webhook(
    alerts: list[dict[str, Any]] | None = None, truncated_alerts: int = 0, **overrides: Any
) -> dict[str, Any]:
    """整个 webhook v4 payload：alerts[] 批量数组 + truncatedAlerts 截断字段。"""
    if alerts is None:
        alerts = [make_alert()]
    webhook: dict[str, Any] = {
        "receiver": "dump",
        "status": "firing",
        "alerts": alerts,
        "groupLabels": {"alertname": "DemoApiGwHighLatency"},
        "commonLabels": {"alertname": "DemoApiGwHighLatency", "severity": "warning"},
        "commonAnnotations": {},
        "externalURL": "http://demo-alertmanager:9093",
        "version": "4",
        "groupKey": '{}:{alertname="DemoApiGwHighLatency"}',
        "truncatedAlerts": truncated_alerts,
    }
    webhook.update(overrides)
    return webhook
