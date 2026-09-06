"""/ingest 落库服务：归一化告警 → alert_events，窗内合并（D-13/G2，不引 Redis）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from oncall.db import AlertEvent
from oncall.ingest.fingerprint import DEFAULT_DEDUP_WINDOW, as_utc, candidate_fingerprints
from oncall.ingest.normalize import NormalizedAlert, normalize_webhook
from oncall.ingest.schemas import AlertmanagerWebhook

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class IngestResult:
    """响应计数：received = 本次 payload 内 alert 条数，deduped = 判重/合并跳过的条数。"""

    received: int
    deduped: int


def ingest_webhook(
    session: Session,
    webhook: AlertmanagerWebhook,
    dedup_window: timedelta = DEFAULT_DEDUP_WINDOW,
) -> IngestResult:
    """批量落库 + 窗内合并，返回 {received, deduped}。

    命中窗内同指纹行的行为（G2）：
    - 新 firing（fired_at 晚于 last_fired_at）→ `dedup_count++`、`last_fired_at` 刷新，
      已恢复的行重新打开（`resolved_at` 清空）；
    - 原样重放 / 旧投递（fired_at ≤ last_fired_at）→ 只跳过、不计数——D-13 幂等硬要求
      （Alertmanager 对非 2xx 指数退避重试，重复投递是常态而非异常，G3）；
    - resolved 通知 → 落 `resolved_at`，不计数；
    - 超窗 → 无合并目标 → 新行（指纹含时间窗桶，见 `fingerprint.py` 模块 docstring）。
    """
    received = deduped = 0
    for normalized in normalize_webhook(webhook, dedup_window):
        received += 1
        target = _find_merge_target(session, normalized, dedup_window)
        if target is None:
            session.add(_to_row(normalized))
            continue
        deduped += 1
        _merge(target, normalized)
    session.commit()
    return IngestResult(received=received, deduped=deduped)


def _find_merge_target(
    session: Session, normalized: NormalizedAlert, window: timedelta
) -> AlertEvent | None:
    """窗内最近一次 firing 所在行；找不到（超窗/首次）返回 None。"""
    candidates = candidate_fingerprints(normalized.labels, normalized.fired_at, window)
    rows = session.scalars(select(AlertEvent).where(AlertEvent.fingerprint.in_(candidates))).all()
    in_window = [
        row for row in rows if abs(normalized.fired_at - as_utc(row.last_fired_at)) <= window
    ]
    if not in_window:
        return None
    return max(in_window, key=lambda row: as_utc(row.last_fired_at))


def _merge(row: AlertEvent, normalized: NormalizedAlert) -> None:
    """行内合并：计数与恢复状态联动，原样重放不做任何改动。"""
    if normalized.fired_at > as_utc(row.last_fired_at):
        row.dedup_count += 1
        row.last_fired_at = normalized.fired_at
        row.resolved_at = None  # 窗内再触发 = 重新打开
    resolved = normalized.resolved_at
    if resolved is not None and (row.resolved_at is None or resolved > as_utc(row.resolved_at)):
        row.resolved_at = resolved


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
