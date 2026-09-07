"""ORM 行 → JSON 契约序列化器（db 层中性出口，api 与 classify 共用）。

为什么放 db 层而不是 api：alert_body 是 D-17 事件卡片本体的序列化实现，
api/card.py（查询面）与 classify/service.py（LLM 通道输入卡片）都要用——
若留在 api 包，classify → api 的 import 会与 api.routes → classify.service
形成循环（api 包 __init__ 级联拉起 routes）。下沉后依赖方向统一为
api → classify → db，M1 公开面不受影响（card.py 原样再导出）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from oncall.db import AlertEvent, Incident
from oncall.ingest.fingerprint import as_utc

__all__ = ["alert_body", "incident_body"]


def alert_body(row: AlertEvent) -> dict[str, Any]:
    """归一化告警本体（D-17 键名）：alert_events 行 + annotations 溯源展开。"""
    provenance = row.annotations_json or {}
    return {
        "id": row.id,
        "fingerprint": row.fingerprint,
        "source": row.source,
        "status": row.status,
        "labels": dict(row.labels_json),
        "annotations": dict(provenance.get("annotations", {})),
        # 溯源（G5/D-13）：AM 自带指纹仅交叉溯源；raw_alert/webhook 是 M3 取证输入
        "am_fingerprint": provenance.get("am_fingerprint", ""),
        "raw_alert": provenance.get("raw_alert", {}),
        "webhook": provenance.get("webhook", {}),
        "fired_at": _iso(row.fired_at),
        "last_fired_at": _iso(row.last_fired_at),
        "resolved_at": None if row.resolved_at is None else _iso(row.resolved_at),
        "dedup_count": row.dedup_count,
    }


def incident_body(row: Incident) -> dict[str, Any]:
    """incidents 最小集五字段（G5 定案）；alert_ids[0] 即 M3 调查的 primary anchor。"""
    return {
        "id": row.id,
        "alert_ids": list(row.alert_ids),
        "severity": row.severity,
        "status": row.status,
        "created_at": _iso(row.created_at),
    }


def _iso(moment: datetime) -> str:
    return as_utc(moment).isoformat()
