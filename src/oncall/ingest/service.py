"""/ingest 落库服务：归一化告警 → alert_events（去重状态落 DB 行，D-13/G2，不引 Redis）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from oncall.db import AlertEvent
from oncall.ingest.normalize import NormalizedAlert, normalize_webhook
from oncall.ingest.schemas import AlertmanagerWebhook

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class IngestResult:
    """响应计数：received = 本次 payload 内 alert 条数，deduped = 其中判重跳过的条数。"""

    received: int
    deduped: int


def ingest_webhook(session: Session, webhook: AlertmanagerWebhook) -> IngestResult:
    """批量落库，返回 {received, deduped} 计数。

    判重锚点 = NormalizedAlert.fingerprint（本票为原始标识键，issue 03 换主指纹）。
    命中已有行 → 原样跳过：不新增行、dedup_count / last_fired_at 一概不动——
    D-13 幂等硬要求：同一 payload 原样重放不产生新记录、不重复计数
    （Alertmanager 对非 2xx 指数退避重试，重复投递是常态而非异常，G3）。
    """
    received = deduped = 0
    for normalized in normalize_webhook(webhook):
        received += 1
        existing = session.scalar(
            select(AlertEvent).where(AlertEvent.fingerprint == normalized.fingerprint)
        )
        if existing is not None:
            deduped += 1
            continue
        session.add(_to_row(normalized))
    session.commit()
    return IngestResult(received=received, deduped=deduped)


def _to_row(normalized: NormalizedAlert) -> AlertEvent:
    """NormalizedAlert → ORM 行（status/dedup_count/last_fired_at 走模型默认）。"""
    return AlertEvent(
        fingerprint=normalized.fingerprint,
        source=normalized.source,
        labels_json=dict(normalized.labels),
        annotations_json=dict(normalized.annotations_json),
        fired_at=normalized.fired_at,
        resolved_at=normalized.resolved_at,
    )
