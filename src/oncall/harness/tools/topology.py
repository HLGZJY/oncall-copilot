"""get_topology 工具（G2⑤ / D-23）：复用 oncall.context 三源，返回 D-16 统一形状。

- 复用 `context.topology.service_topology`（不在 C3 禁列，三源实现零重建）；
  `PromClient` 经注入的 Fetcher 组装（依赖注入照 context 先例）
- D-16 形状：`data = {source, status, items, meta}`；source 级 unavailable →
  ToolResult unavailable（meta.reason 透传），永不向上抛（D-16 纪律延伸）
- 可选 `service` 过滤（按 job 聚焦单服务）；过滤后无条目 → empty
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from oncall.context.config import ContextConfig
from oncall.context.models import STATUS_UNAVAILABLE
from oncall.context.promql import PromClient
from oncall.context.topology import service_topology
from oncall.harness.tools.schemas import GetTopologyInput, ToolResult, ToolStatus
from oncall.harness.tools.sources import effective_timeout

if TYPE_CHECKING:
    from oncall.harness.tools.registry import ToolHandler
    from oncall.infra.http import Fetcher

__all__ = ["build_get_topology_handler"]


def build_get_topology_handler(fetcher: Fetcher, *, config: ContextConfig) -> ToolHandler:
    """工厂：注入 Fetcher 与三源配置，返回 ToolHandler 签名的执行函数。"""
    base_url = config.prometheus_url

    def handler(args: GetTopologyInput, *, timeout_seconds: float) -> ToolResult:
        client = PromClient(
            fetcher,
            base_url=base_url,
            timeout=effective_timeout(timeout_seconds, config.timeout),
        )
        result = service_topology(client, config=config)
        if result.status == STATUS_UNAVAILABLE:
            return ToolResult(
                tool="get_topology",
                status=ToolStatus.UNAVAILABLE,
                data=None,
                meta={"reason": str(result.meta.get("reason", "Prometheus 不可达"))},
            )
        items = list(result.items)
        if args.service is not None:
            items = [item for item in items if item.get("job") == args.service]
        if not items:
            return ToolResult(tool="get_topology", status=ToolStatus.EMPTY, data=None, meta={})
        meta = dict(result.meta)
        if args.service is not None:
            meta["service"] = args.service
        return ToolResult(
            tool="get_topology",
            status=ToolStatus.OK,
            data={"source": result.source, "status": result.status, "items": items, "meta": meta},
        )

    return handler
