"""归一化映射集（本票 TDD 接缝，纯函数）：AM webhook v4 → 内部统一告警结构。

CONTEXT.md「归一化 / Normalization」：把外部告警源（Alertmanager webhook 等）的
原始 payload 校验并映射为内部统一告警结构，M1 的确定性环节——只做映射转换，
不做分类/降噪（M2）、不算时间窗合并（issue 03）。

指纹接缝（D-13/G1）：主指纹 = canonical 稳定 label 子集（{alertname, job, instance}
排序 + 0xFF 分隔）sha256，算法属 issue 03；本票按「原始标识」判重——AM 自带
fingerprint + 原始 alert 全量 payload 的 sha256。03 合入后替换去重锚点即可，
归一化与服务层签名不变。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from oncall.ingest.schemas import AlertmanagerAlert, AlertmanagerWebhook

SOURCE_ALERTMANAGER = "alertmanager"
ZERO_TIME = datetime(1, 1, 1, tzinfo=UTC)  # AM endsAt 零值哨兵 = 未恢复


class NormalizedAlert(BaseModel):
    """内部统一告警结构：字段一一对应 alert_events 列（架构 §4 + D-13）。"""

    # 本票 = 原始标识键（原始 payload 级）；issue 03 落主指纹（G1 canonical sha256）后语义升级
    fingerprint: str
    source: str = SOURCE_ALERTMANAGER
    labels: dict[str, str]
    annotations_json: dict[str, Any]
    fired_at: datetime
    resolved_at: datetime | None = None


def raw_identity_fingerprint(alert: AlertmanagerAlert) -> str:
    """原始标识判重键（issue 03 主指纹合入前的临时去重锚点）。

    = sha256(AM 自带 fingerprint + 原始 alert 规范化 JSON)。
    原样重放 → 键相同 → 判重跳过；新一轮 firing（startsAt 变）/resolved 通知 →
    键不同 → 各自成行（同故障时间窗合并由 03 的主指纹 + dedup_window 接手）。
    """
    raw = alert.model_dump(mode="json", by_alias=True)
    canonical = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((alert.fingerprint + canonical).encode("utf-8")).hexdigest()


def normalize_alert(
    alert: AlertmanagerAlert, webhook: AlertmanagerWebhook | None = None
) -> NormalizedAlert:
    """单条 alert 的 canonical 字段映射（纯函数）。

    - labels 全量保留：severity/scenario 等非 canonical label 是 M2 分类的输入，
      归一化不丢信息（丢弃/筛选只发生在 03 的指纹哈希输入，不动存储）；
    - endsAt 缺省或零值哨兵 → resolved_at=None（未恢复）；
    - 溯源（G5/D-13）：AM 自带 fingerprint + annotations + 原始 alert 全量 +
      webhook 级上下文，全部落 annotations_json。
    """
    resolved_at: datetime | None = alert.ends_at
    if resolved_at is not None and resolved_at == ZERO_TIME:
        resolved_at = None
    return NormalizedAlert(
        fingerprint=raw_identity_fingerprint(alert),
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


def normalize_webhook(webhook: AlertmanagerWebhook) -> list[NormalizedAlert]:
    """批量归一化：alerts[] 逐条映射，顺序保持（G3：不假设单条，容忍 truncatedAlerts）。"""
    return [normalize_alert(alert, webhook) for alert in webhook.alerts]
