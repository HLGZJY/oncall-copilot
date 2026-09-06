"""PromQL HTTP API 客户端——M3 取证要复用的接缝（issue 04 要点）。

只封装协议（query_range / targets 的端点、参数、响应解包），不做缓存/重试
花活；base_url 与超时由调用方注入。API 层失败（非 2xx / status!=success /
坏 JSON）统一抛 `PromAPIError`；传输层失败透传 `HTTPClientError`——
降级判定在 adapter 层做，捕获 `UNAVAILABLE_ERRORS` 即可。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from oncall.infra.http import HTTPClientError

if TYPE_CHECKING:
    from oncall.infra.http import Fetcher


class PromAPIError(Exception):
    """Prometheus API 层失败（响应可达但内容不可用）。"""


UNAVAILABLE_ERRORS = (HTTPClientError, PromAPIError)
HTTP_OK = 200  # Prometheus API v1 正常响应码


class PromClient:
    """Prometheus HTTP API v1 客户端（同步）。

    M3 取证复用时直接注入同一实例/同一 Fetcher，无需改接口。
    """

    def __init__(self, fetcher: Fetcher, *, base_url: str, timeout: float) -> None:
        self._fetcher = fetcher
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def query_range(
        self, query: str, *, start: datetime, end: datetime, step: str
    ) -> list[dict[str, Any]]:
        """区间查询 → matrix result（series 列表，含 metric + values）。"""
        payload = self._get(
            "/api/v1/query_range",
            {
                "query": query,
                "start": f"{start.timestamp():.3f}",
                "end": f"{end.timestamp():.3f}",
                "step": step,
            },
        )
        return payload["data"]["result"]

    def targets(self) -> list[dict[str, Any]]:
        """活跃抓取目标（含 labels / health / lastError）。"""
        payload = self._get("/api/v1/targets", {})
        return payload["data"]["activeTargets"]

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        response = self._fetcher.get(self._base_url + path, params=params, timeout=self._timeout)
        if response.status_code != HTTP_OK:
            raise PromAPIError(f"Prometheus {path} 返回 {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise PromAPIError(f"Prometheus {path} 响应非 JSON: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("status") != "success":
            detail = payload.get("error") if isinstance(payload, dict) else payload
            raise PromAPIError(f"Prometheus {path} status 非 success: {detail}")
        return payload
