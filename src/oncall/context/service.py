"""三源编排（issue 04）：按 alert labels 组装近期指标 / 服务拓扑 / 近期变更。

降级纪律：任一源失败只落该源的 unavailable 标记，不向上抛——
collect_context 永不因 Prometheus 挂了而失败（0 漏收：上下文缺失 ≠ 告警缺失）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from oncall.context.changes import recent_changes
from oncall.context.config import ContextConfig
from oncall.context.metrics import recent_metrics
from oncall.context.models import SourceResult
from oncall.context.promql import PromClient
from oncall.context.topology import service_topology
from oncall.infra.http import HttpxFetcher


def collect_context(
    labels: Mapping[str, str],
    *,
    config: ContextConfig | None = None,
    client: PromClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """三源快照：{"sources": [{source, status, items, meta} × 3]}。

    client 缺省时按 config 建 HttpxFetcher 生产客户端；测试注入替身。
    """
    config = config or ContextConfig()
    if client is None:
        client = PromClient(HttpxFetcher(), base_url=config.prometheus_url, timeout=config.timeout)
    sources: list[SourceResult] = [
        recent_metrics(client, labels, config=config, now=now),
        service_topology(client, config=config),
        recent_changes(config=config),
    ]
    return {"sources": [result.to_dict() for result in sources]}
