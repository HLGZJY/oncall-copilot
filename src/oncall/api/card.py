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
from oncall.db import AlertEvent, Incident
from oncall.db.views import alert_body, incident_body  # 再导出，公开面兼容
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


def list_alerts(
    session: Session,
    *,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
    fingerprint: str | None = None,
    verdict: str | None = None,
) -> dict[str, Any]:
    """告警列表（分页 + 指纹/verdict 过滤）：只给本体，不给上下文（上下文只在单卡接口）。

    verdict 过滤走 SQLite JSON1 `json_extract`（D-19/R7：11 剧本规模不预优化，
    不建独立 verdict 列/索引，实测成为瓶颈再议）。
    """
    limit = max(1, min(limit, MAX_LIST_LIMIT))
    offset = max(0, offset)
    query = select(AlertEvent)
    count_query = select(func.count()).select_from(AlertEvent)
    if fingerprint is not None:
        query = query.where(AlertEvent.fingerprint == fingerprint)
        count_query = count_query.where(AlertEvent.fingerprint == fingerprint)
    if verdict is not None:
        verdict_filter = func.json_extract(AlertEvent.classification_json, "$.verdict") == verdict
        query = query.where(verdict_filter)
        count_query = count_query.where(verdict_filter)
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


def list_incidents(
    session: Session,
    *,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """事件列表（分页）：M3 调查入口的查询面，每条可经 alert_ids[0] 锚到 D-17 卡片。"""
    limit = max(1, min(limit, MAX_LIST_LIMIT))
    offset = max(0, offset)
    total = session.scalar(select(func.count()).select_from(Incident)) or 0
    rows = session.scalars(
        select(Incident)
        .order_by(Incident.created_at.desc(), Incident.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [incident_body(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
