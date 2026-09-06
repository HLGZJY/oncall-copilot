"""T2 验收测试（TDD 接缝）：AM webhook v4 校验 + 归一化映射集（issue 02）。

契约来源：docs/design/m1-alert-ingestion-design.md §技术方案 + G1/G3/G5 定案、
docs/design/decisions.md D-13、CONTEXT.md「归一化 / Normalization」。

覆盖：
- v4 契约解析：alerts[] 批量数组、truncatedAlerts 容忍、未知字段忽略、缺关键字段拒绝
- 归一化映射（纯函数）：canonical 字段 → alert_events 列语义
  （source/labels_json/fired_at/resolved_at/annotations_json）
- 溯源（D-13/G5）：AM 自带 fingerprint + 原始 payload 全量进 annotations_json
- 原始标识判重键（issue 03 主指纹的预留接缝）：确定性、区分度、64 位 hex
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from conftest import AM_FINGERPRINT, RESOLVED_ENDS, make_alert, make_webhook
from oncall.ingest.normalize import (
    normalize_alert,
    normalize_webhook,
    raw_identity_fingerprint,
)
from oncall.ingest.schemas import AlertmanagerAlert, AlertmanagerWebhook

FIRED_EXPECTED = datetime(2026, 9, 6, 6, 28, 21, 474000, tzinfo=UTC)
RESOLVED_EXPECTED = datetime(2026, 9, 6, 6, 28, 41, 474000, tzinfo=UTC)


def _parse(webhook: dict) -> AlertmanagerWebhook:
    return AlertmanagerWebhook.model_validate(webhook)


class TestWebhookContract:
    def test_v4_payload_parses_with_batch_alerts_and_truncation(self):
        """批量 alerts[] + truncatedAlerts 均容忍（G3/R1：不假设单条）。"""
        webhook = _parse(make_webhook(truncated_alerts=3))
        assert webhook.version == "4"
        assert webhook.truncated_alerts == 3
        assert len(webhook.alerts) == 1

    def test_unknown_fields_are_ignored(self):
        """未来版本新增字段不炸端点（前向兼容）。"""
        payload = make_webhook()
        payload["someFutureField"] = {"x": 1}
        assert _parse(payload).status == "firing"

    def test_missing_alerts_rejected(self):
        payload = make_webhook()
        del payload["alerts"]
        with pytest.raises(ValidationError):
            _parse(payload)

    def test_missing_status_rejected(self):
        payload = make_webhook()
        del payload["status"]
        with pytest.raises(ValidationError):
            _parse(payload)

    def test_alert_starts_at_parses_to_aware_datetime(self):
        alert = AlertmanagerAlert.model_validate(make_alert())
        assert alert.starts_at == FIRED_EXPECTED
        assert alert.starts_at.tzinfo is not None


class TestNormalizeMapping:
    def test_firing_alert_maps_canonical_fields(self):
        """canonical 映射：source/labels/fired_at/resolved_at 逐列对齐 alert_events 语义。"""
        normalized = normalize_alert(AlertmanagerAlert.model_validate(make_alert()))
        assert normalized.source == "alertmanager"
        assert normalized.labels["alertname"] == "DemoApiGwHighLatency"
        assert normalized.labels["job"] == "api-gw"
        assert normalized.labels["instance"] == "api-gw-1"
        assert normalized.fired_at == FIRED_EXPECTED
        assert normalized.resolved_at is None  # endsAt 零值哨兵 → 未恢复

    def test_resolved_alert_sets_resolved_at(self):
        """resolved 通知：endsAt 真实时间 → resolved_at（联动正确落列）。"""
        resolved = make_alert(status="resolved", endsAt=RESOLVED_ENDS)
        normalized = normalize_alert(AlertmanagerAlert.model_validate(resolved))
        assert normalized.resolved_at == RESOLVED_EXPECTED

    def test_provenance_stored_in_annotations_json(self):
        """溯源（G5/D-13）：AM 自带 fingerprint + 原始 alert 全量 + webhook 上下文全存。"""
        alert = make_alert()
        webhook = make_webhook(alerts=[alert])
        normalized = normalize_webhook(_parse(webhook))[0]

        ann = normalized.annotations_json
        assert ann["am_fingerprint"] == AM_FINGERPRINT
        assert ann["annotations"] == alert["annotations"]
        # 全量原始 payload（按 v4 字段名；时间经 Pydantic 规范化为 ISO 微秒格式）
        assert ann["raw_alert"] == AlertmanagerAlert.model_validate(alert).model_dump(
            mode="json", by_alias=True
        )
        assert set(ann["raw_alert"]) == set(alert)  # v4 字段一个不少
        assert ann["webhook"]["receiver"] == "dump"
        assert ann["webhook"]["status"] == "firing"
        assert ann["webhook"]["truncated_alerts"] == 0

    def test_batch_alerts_normalize_in_order(self):
        """单次 payload 含 ≥2 条 alerts[] → 逐条归一化，顺序保持。"""
        first = make_alert()
        second = make_alert(
            status="resolved",
            endsAt=RESOLVED_ENDS,
            labels={"alertname": "DemoSqlSlow", "job": "mysql", "instance": "db-1"},
            fingerprint="aa11bb22",
        )
        normalized = normalize_webhook(_parse(make_webhook(alerts=[first, second])))
        assert len(normalized) == 2
        assert normalized[0].labels["alertname"] == "DemoApiGwHighLatency"
        assert normalized[1].labels["alertname"] == "DemoSqlSlow"
        assert normalized[1].resolved_at == RESOLVED_EXPECTED

    def test_volatile_labels_preserved_not_dropped(self):
        """severity/scenario 等非 canonical label 原样保留（M2 分类要用，归一化不丢信息）。"""
        normalized = normalize_alert(AlertmanagerAlert.model_validate(make_alert()))
        assert normalized.labels["severity"] == "warning"
        assert normalized.labels["scenario"] == "cpu-spike"


class TestRawIdentityFingerprint:
    def test_deterministic_across_calls(self):
        alert = AlertmanagerAlert.model_validate(make_alert())
        assert raw_identity_fingerprint(alert) == raw_identity_fingerprint(
            AlertmanagerAlert.model_validate(make_alert())
        )

    def test_is_64_hex_chars(self):
        digest = raw_identity_fingerprint(AlertmanagerAlert.model_validate(make_alert()))
        assert len(digest) == 64
        int(digest, 16)  # hex 可解析

    def test_differs_from_am_builtin_fingerprint(self):
        """AM 自带 fingerprint 是 16 位 FNV-1a；原始标识键必须与之可区分（防混用）。"""
        digest = raw_identity_fingerprint(AlertmanagerAlert.model_validate(make_alert()))
        assert digest != AM_FINGERPRINT

    def test_sensitive_to_starts_at_change(self):
        """同 alert 新一轮 firing（startsAt 变）→ 原始标识不同 → 各自成行（窗口合并留给 03）。"""
        old = AlertmanagerAlert.model_validate(make_alert())
        new = AlertmanagerAlert.model_validate(make_alert(startsAt="2026-09-06T07:00:00.000Z"))
        assert raw_identity_fingerprint(old) != raw_identity_fingerprint(new)

    def test_sensitive_to_status_change(self):
        """firing 与 resolved 的原始 payload 不同 → 标识不同。"""
        firing = AlertmanagerAlert.model_validate(make_alert())
        resolved = AlertmanagerAlert.model_validate(
            make_alert(status="resolved", endsAt=RESOLVED_ENDS)
        )
        assert raw_identity_fingerprint(firing) != raw_identity_fingerprint(resolved)
