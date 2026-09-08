"""search_logs 工具（G2② / D-23）：Loki query_range 封装 + 四态降级。

- HTTP 走与 query_metrics 同一 `Fetcher` 接缝（C4 收口，不新开 HTTP 通道，R4）
- limit 默认 100 且 ≤100、direction 默认 backward（schema 层已冻结缺省值，R4）
- `end` 缺省锚定 `incident.alert.last_fired_at`（D-17 时间锚），由工厂注入 anchor
- 四态语义与 query_metrics 一致：ok / empty / error（非 2xx、坏 JSON、
  status!=success、括号不配平）/ unavailable（传输层失败，D-16 永不抛错）
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from oncall.harness.tools.schemas import SearchLogsInput, ToolResult, ToolStatus
from oncall.harness.tools.sources import balanced, effective_timeout
from oncall.infra.http import HTTPClientError, HttpResponse

if TYPE_CHECKING:
    from oncall.harness.tools.registry import ToolHandler
    from oncall.infra.http import Fetcher

HTTP_OK = 200
NANOSECONDS = 1_000_000_000  # Loki query_range 时间参数为纳秒时间戳（R4）

__all__ = ["build_search_logs_handler"]


def _nanoseconds(moment: datetime) -> str:
    return str(int(moment.timestamp() * NANOSECONDS))


def _error(reason: str) -> ToolResult:
    return ToolResult(
        tool="search_logs", status=ToolStatus.ERROR, data=None, meta={"reason": reason}
    )


def build_search_logs_handler(
    fetcher: Fetcher, *, loki_url: str, timeout: float, anchor: datetime
) -> ToolHandler:
    """工厂：注入 Fetcher / Loki 地址 / 超时 / 时间锚（last_fired_at，D-17）。"""
    endpoint = loki_url.rstrip("/") + "/loki/api/v1/query_range"

    def handler(args: SearchLogsInput, *, timeout_seconds: float) -> ToolResult:
        if not balanced(args.selector):
            return _error("LogQL 选择器括号/花括号不配平，请修正后重试")
        end = args.end or anchor
        params: dict[str, str] = {
            "query": args.selector,
            "limit": str(args.limit),
            "direction": args.direction,
            "start": _nanoseconds(args.start),
            "end": _nanoseconds(end),
        }
        try:
            response = fetcher.get(
                endpoint, params=params, timeout=effective_timeout(timeout_seconds, timeout)
            )
        except HTTPClientError as exc:
            return ToolResult(
                tool="search_logs",
                status=ToolStatus.UNAVAILABLE,
                data=None,
                meta={"reason": f"Loki 不可达: {exc}"},
            )
        streams = _parse(response)
        if isinstance(streams, ToolResult):
            return streams
        if not streams:
            return ToolResult(tool="search_logs", status=ToolStatus.EMPTY, data=None, meta={})
        return ToolResult(
            tool="search_logs",
            status=ToolStatus.OK,
            data={"streams": streams},
            meta={"limit": args.limit, "direction": args.direction},
        )

    return handler


def _parse(response: HttpResponse) -> Any:
    """解包 Loki 响应；失败路径返回 ToolResult（error 四态），成功返回 streams 列表。"""
    if response.status_code != HTTP_OK:
        return _error(f"Loki query_range 返回 {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        return _error(f"Loki 响应非 JSON: {exc}")
    if not isinstance(payload, dict) or payload.get("status") != "success":
        detail = payload.get("error") if isinstance(payload, dict) else payload
        return _error(f"Loki status 非 success: {detail}")
    data = payload.get("data")
    result = data.get("result") if isinstance(data, dict) else None
    return result if isinstance(result, list) else []
