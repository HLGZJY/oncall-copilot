"""归一化映射集（TDD 接缝，纯函数）：AM webhook v4 → 内部统一告警结构。

CONTEXT.md「归一化 / Normalization」：把外部告警源（Alertmanager webhook 等）的
原始 payload 校验并映射为内部统一告警结构，M1 的确定性环节——只做映射转换，
不做分类/降噪（M2）；去重合并在 service 层（时间窗，G2）。

指纹（G1/D-13）：`NormalizedAlert.fingerprint` = 主指纹
（canonical 稳定 label 子集 + 时间窗桶 → sha256），算法见 `fingerprint.py`。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from oncall.ingest.fingerprint import DEFAULT_DEDUP_WINDOW, canonical_fingerprint
from oncall.ingest.schemas import AlertmanagerAlert, AlertmanagerWebhook

SOURCE_ALERTMANAGER = "alertmanager"
ZERO_TIME = datetime(1, 1, 1, tzinfo=UTC)  # AM endsAt 零值哨兵 = 未恢复


class NormalizedAlert(BaseModel):
    """内部统一告警结构：字段一一对应 alert_events 列（架构 §4 + D-13）。"""

    fingerprint: str  # 主指纹（G1 canonical + 时间窗桶）
    source: str = SOURCE_ALERTMANAGER
    labels: dict[str, str]
    annotations_json: dict[str, Any]
    fired_at: datetime
    resolved_at: datetime | None = None


def normalize_alert(
    alert: AlertmanagerAlert,
    webhook: AlertmanagerWebhook | None = None,
    dedup_window: timedelta = DEFAULT_DEDUP_WINDOW,
) -> NormalizedAlert:
    """单条 alert 的 canonical 字段映射（纯函数）。

    - labels 全量保留：severity/scenario 等非 canonical label 是 M2 分类的输入，
      归一化不丢信息（筛选只发生在指纹哈希输入，不动存储）；
    - endsAt 缺省或零值哨兵 → resolved_at=None（未恢复）；
    - 溯源（G5/D-13）：AM 自带 fingerprint + annotations + 原始 alert 全量 +
      webhook 级上下文，全部落 annotations_json。
    """
    resolved_at: datetime | None = alert.ends_at
    if resolved_at is not None and resolved_at == ZERO_TIME:
        resolved_at = None
    return NormalizedAlert(
        fingerprint=canonical_fingerprint(alert.labels, alert.starts_at, dedup_window),
        source=SOURCE_ALERTMANAGER,
        labels=dict(alert.labels),
        annotations_json={
            "am_fingerprint": alert.fingerprint,
            "annotations": dict(alert.annotations),
            "raw_alert": alert.model_dump(mode="json", by_alias=True),
            "webhook": {} if webhook is None else webhook.context(),
        },
        fired_at=alert.starts_at,
        resolved_at=resolved_at,
    )


def normalize_webhook(
    webhook: AlertmanagerWebhook, dedup_window: timedelta = DEFAULT_DEDUP_WINDOW
) -> list[NormalizedAlert]:
    """批量归一化：alerts[] 逐条映射，顺序保持（G3：不假设单条，容忍 truncatedAlerts）。"""
    return [normalize_alert(alert, webhook, dedup_window) for alert in webhook.alerts]
