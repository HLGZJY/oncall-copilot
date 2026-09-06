"""上下文三源（M1 issue 04）：近期指标 / 服务拓扑 / 近期变更（占位）。

对外出口：`collect_context`（三源编排）与各单源 adapter；
PromQL 客户端 `PromClient` 是 M3 取证要复用的接缝。
"""

from oncall.context.changes import recent_changes
from oncall.context.config import ContextConfig
from oncall.context.metrics import build_selector, recent_metrics
from oncall.context.models import (
    SOURCE_CHANGES,
    SOURCE_METRICS,
    SOURCE_TOPOLOGY,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    SourceResult,
)
from oncall.context.promql import PromAPIError, PromClient
from oncall.context.service import collect_context
from oncall.context.topology import service_topology

__all__ = [
    "SOURCE_CHANGES",
    "SOURCE_METRICS",
    "SOURCE_TOPOLOGY",
    "STATUS_OK",
    "STATUS_UNAVAILABLE",
    "ContextConfig",
    "PromAPIError",
    "PromClient",
    "SourceResult",
    "build_selector",
    "collect_context",
    "recent_changes",
    "recent_metrics",
    "service_topology",
]
