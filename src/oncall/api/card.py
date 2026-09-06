"""事件卡片 / Alert Card 组装（issue 05，G3 定案）。

CONTEXT.md：事件卡片 = 归一化告警本体 + 三源上下文的 JSON 契约，无 UI 阶段的
验收载体——**不是**「事件/Incident」（M2 判定真实后才建档）。契约键名一次到位
（M8 UI 与 M3 取证的输入），登记 `decisions.md` D-17，评审后不再改。

降级纪律：上下文直接复用 `collect_context`（issue 04）——Prometheus 不可达时
对应源显式 `unavailable`，卡片照常 200 返回（上下文缺失 ≠ 告警缺失，
0 漏收纪律对上下文侧的延伸）；不存在的 id 由路由层转 404。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from oncall.context import collect_context
from oncall.db import AlertEvent
from oncall.ingest.fingerprint import as_utc

if TYPE_CHECKING:
    from oncall.context.config import ContextConfig
    from oncall.context.promql import PromClient

DEFAULT_LIST_LIMIT = 20
MAX_LIST_LIMIT = 100


def build_alert_card(
    session: Session,
    alert_id: int,
    *,
    config: ContextConfig,
    client: PromClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """按 id 组装事件卡片；alert_events 无此行时返回 None（路由转 404）。

    三源上下文的时间锚 = 该告警的 `last_fired_at`：卡片描述的是「这次告警
    发生时」的近期指标/拓扑，而非「现在」，回放历史 dump 时上下文才有意义。
    """
    row = session.get(AlertEvent, alert_id)
    if row is None:
        return None
    return {
        "alert": alert_body(row),
        "context": collect_context(
            row.labels_json,
            config=config,
            client=client,
            now=as_utc(row.last_fired_at),
        ),
        "generated_at": (now or datetime.now(UTC)).isoformat(),
    }


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


def list_alerts(
    session: Session,
    *,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
    fingerprint: str | None = None,
) -> dict[str, Any]:
    """告警列表（分页 + 按指纹过滤）：只给本体，不给上下文（上下文只在单卡接口）。"""
    limit = max(1, min(limit, MAX_LIST_LIMIT))
    offset = max(0, offset)
    query = select(AlertEvent)
    count_query = select(func.count()).select_from(AlertEvent)
    if fingerprint is not None:
        query = query.where(AlertEvent.fingerprint == fingerprint)
        count_query = count_query.where(AlertEvent.fingerprint == fingerprint)
    total = session.scalar(count_query) or 0
    rows = session.scalars(
        query.order_by(AlertEvent.last_fired_at.desc(), AlertEvent.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [alert_body(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _iso(moment: datetime) -> str:
    return as_utc(moment).isoformat()
