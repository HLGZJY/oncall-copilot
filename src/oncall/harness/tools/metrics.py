"""query_metrics 工具（G2① / D-23）：Prometheus query_range 封装 + 四态降级。

- HTTP 一律经注入的 `Fetcher`（C4 收口）；协议层复用 `context.promql.PromClient`
  （M1 三源先例零重建，不在 C3 禁列）
- matrix 结果 series 上限 20 截断（D-23）；输出 ≤2000 tokens 预算由 Registry
  `_summarize` 统一收口，工具内不自造第二套截断（M3-02 语义对齐）
- 四态：ok（有 series）/ empty（空）/ error（非 2xx、坏 JSON、PromQL 语义错——
  最小校验只做括号配平）/ unavailable（传输层失败，D-16 纪律：永不向上抛）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from oncall.context.config import ContextConfig
from oncall.context.promql import PromAPIError, PromClient
from oncall.harness.tools.schemas import QueryMetricsInput, ToolResult, ToolStatus
from oncall.harness.tools.sources import balanced, effective_timeout
from oncall.infra.http import HTTPClientError

if TYPE_CHECKING:
    from oncall.harness.tools.registry import ToolHandler
    from oncall.infra.http import Fetcher

MAX_SERIES = 20  # D-23：matrix series 上限

__all__ = ["MAX_SERIES", "build_query_metrics_handler"]


def build_query_metrics_handler(fetcher: Fetcher, *, config: ContextConfig) -> ToolHandler:
    """工厂：注入 Fetcher 与三源配置，返回 ToolHandler 签名的执行函数。"""
    base_url = config.prometheus_url

    def handler(args: QueryMetricsInput, *, timeout_seconds: float) -> ToolResult:
        if not balanced(args.promql):
            return ToolResult(
                tool="query_metrics",
                status=ToolStatus.ERROR,
                data=None,
                meta={"reason": "PromQL 括号/花括号不配平，请修正后重试"},
            )
        client = PromClient(
            fetcher,
            base_url=base_url,
            timeout=effective_timeout(timeout_seconds, config.timeout),
        )
        step = args.step or config.step
        try:
            series = client.query_range(args.promql, start=args.start, end=args.end, step=step)
        except HTTPClientError as exc:
            return ToolResult(
                tool="query_metrics",
                status=ToolStatus.UNAVAILABLE,
                data=None,
                meta={"reason": f"Prometheus 不可达: {exc}"},
            )
        except PromAPIError as exc:
            return ToolResult(
                tool="query_metrics",
                status=ToolStatus.ERROR,
                data=None,
                meta={"reason": str(exc)},
            )
        if not series:
            return ToolResult(tool="query_metrics", status=ToolStatus.EMPTY, data=None, meta={})
        return ToolResult(
            tool="query_metrics",
            status=ToolStatus.OK,
            data={"series": series[:MAX_SERIES]},
            meta={"step": step, "series_total": len(series), "truncated": len(series) > MAX_SERIES},
        )

    return handler
