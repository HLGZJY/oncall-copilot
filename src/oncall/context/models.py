"""上下文三源统一返回形状（issue 04 / G4）：{source, status, items, meta}。

status 只有 ok | unavailable 两态——Prometheus 不可达时降级为 unavailable
而非抛错：上下文缺失 ≠ 告警缺失（0 漏收纪律），接口不许 5xx。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

SOURCE_METRICS = "metrics"
SOURCE_TOPOLOGY = "topology"
SOURCE_CHANGES = "changes"
STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SourceResult:
    """单源结果：items 为条目序列（指标 series / targets / 变更记录），meta 放查询口径。"""

    source: str
    status: str
    items: tuple[Any, ...] = ()
    meta: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status,
            "items": list(self.items),
            "meta": dict(self.meta),
        }
