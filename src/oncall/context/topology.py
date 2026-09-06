"""服务拓扑源（G4 必做）：/api/v1/targets + labels 还原服务关系。

M1 口径：还原「服务（job）→ 实例（instance）」清单与健康态——调用边（A→B）
需要 service map 或拓扑配置输入，等 M3 取证引入真实依赖数据再升级，不臆造边。
"""

from __future__ import annotations

from oncall.context.config import ContextConfig
from oncall.context.models import SOURCE_TOPOLOGY, STATUS_OK, STATUS_UNAVAILABLE, SourceResult
from oncall.context.promql import UNAVAILABLE_ERRORS, PromClient


def service_topology(client: PromClient, *, config: ContextConfig) -> SourceResult:
    """targets 全量快照；Prometheus 不可达 → unavailable 降级，不抛错。"""
    try:
        targets = client.targets()
    except UNAVAILABLE_ERRORS as exc:
        return SourceResult(SOURCE_TOPOLOGY, STATUS_UNAVAILABLE, (), {"reason": str(exc)})
    items = []
    for target in targets:
        labels = target.get("labels", {})
        items.append(
            {
                "job": labels.get("job", ""),
                "instance": labels.get("instance", ""),
                "health": target.get("health", "unknown"),
                "labels": labels,
                "last_error": target.get("lastError", ""),
            }
        )
    services = sorted({item["job"] for item in items if item["job"]})
    return SourceResult(
        SOURCE_TOPOLOGY,
        STATUS_OK,
        tuple(items),
        {"target_count": len(items), "services": services},
    )
