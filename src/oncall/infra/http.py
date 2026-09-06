"""统一 HTTP 收口接缝（架构守卫 C4：httpx 全仓只许 import 在本文件）。

设计：业务层不直接碰 HTTP 库，注入 `Fetcher` 协议的实现——
- 生产：`HttpxFetcher`（httpx 同步实现）；
- 测试：任意实现 `get()` 的替身（tests/unit 禁 import httpx，A1 守卫）。

M1 纪律：不做缓存/重试花活；非 2xx 与坏 JSON 的语义处理交给调用方
（Prometheus 侧在 `oncall.context.promql` 解包并降级）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

DEFAULT_TIMEOUT_SECONDS = 5.0


class HTTPClientError(Exception):
    """传输层失败（连接拒绝 / 超时等）；非 2xx 与坏 JSON 由调用方按语义处理。"""


@dataclass(frozen=True)
class HttpResponse:
    """最小响应抽象：状态码 + 文本 body（JSON 解析按需、显式失败）。"""

    status_code: int
    body: str

    def json(self) -> Any:
        return json.loads(self.body)


class Fetcher(Protocol):
    """HTTP GET 的最小协议：业务层与测试替身共同遵守的接缝。"""

    def get(self, url: str, *, params: Mapping[str, str], timeout: float) -> HttpResponse: ...


class HttpxFetcher:
    """httpx 同步实现；传输层异常统一包装为 HTTPClientError。

    trust_env=False：本机系统代理会拦 127.0.0.1（已知踩坑），监控面是内网
    地址，不该走系统代理；若未来确需代理，显式注入而非信任环境。
    """

    def __init__(self, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._default_timeout = timeout

    def get(
        self, url: str, *, params: Mapping[str, str], timeout: float | None = None
    ) -> HttpResponse:
        try:
            with httpx.Client(trust_env=False) as client:
                response = client.get(
                    url, params=dict(params), timeout=timeout or self._default_timeout
                )
        except httpx.HTTPError as exc:
            raise HTTPClientError(f"HTTP 传输失败: {url} ({exc})") from exc
        return HttpResponse(status_code=response.status_code, body=response.text)
