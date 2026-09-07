"""统计口径测试（issue 05 / G6 定案，decisions.md D-20）。

钉死的行为：
- 逻辑告警归并：跨桶 2 行同源连发归并 1 个（D-14 桶边界的对偶还原）；
  传递归并（A→B→C 逐对间隔 ≤window → 1 个）；断链（>window → 2 个）；
  输入顺序不影响归并结果；naive datetime 按 UTC 处理
- R = Σ dedup_count（D-15 口径）而非行数；降噪率 = (R − I) / R；
  R=0 不除零（返回 None + denoise_rate_defined=False）
- 漏报：golden 标注 incident 被判 false_positive 计 1；被判 risk 不计漏报、
  单列 risk_observed；golden 缺省 classification=incident（D-21）
- golden 标注（评测基准）与分类结果（被测输出）显式分形（D-18 教训）
- DB 只读快照在 session 块内物化普通值，session 关闭后仍可用（坑⑥）
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import StaticPool, create_engine
from sqlalchemy.orm import Session

from oncall.classify import (
    AlertStatsRow,
    GoldenAnnotation,
    Verdict,
    compute_denoise_metrics,
    merge_logical_alerts,
    snapshot_alert_rows,
)
from oncall.db import AlertEvent, create_tables
from oncall.ingest.fingerprint import window_bucket

NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)
LABELS: dict[str, str] = {
    "alertname": "DemoApiGwHighLatency",
    "job": "api-gw",
    "instance": "demo-api-gw:8000",
}


def at(hour: int, minute: int) -> datetime:
    """2026-09-07 当天的 UTC 时间构造器（10m 桶：[10:00,10:10) / [10:10,10:20) …）。"""
    return datetime(2026, 9, 7, hour, minute, tzinfo=UTC)


def make_row(
    fired_at: datetime,
    last_fired_at: datetime,
    *,
    dedup_count: int = 1,
    verdict: str | None = None,
    labels: dict[str, Any] | None = None,
) -> AlertStatsRow:
    return AlertStatsRow(
        labels=labels if labels is not None else dict(LABELS),
        fired_at=fired_at,
        last_fired_at=last_fired_at,
        dedup_count=dedup_count,
        verdict=verdict,
    )


class TestMergeLogicalAlerts:
    def test_cross_bucket_two_rows_merge_into_one_logical_alert(self):
        """跨两个 10m 桶的同源连发（D-14 桶边界实录）→ 统计层还原为 1 个逻辑告警。"""
        first = make_row(at(10, 5), at(10, 9), dedup_count=2)  # 桶 [10:00,10:10)
        second = make_row(at(10, 11), at(10, 14), dedup_count=3)  # 桶 [10:10,10:20)
        assert window_bucket(first.fired_at) != window_bucket(second.fired_at)  # 跨桶前提

        logical = merge_logical_alerts([first, second])

        assert len(logical) == 1
        assert len(logical[0].rows) == 2
        assert logical[0].dedup_count == 5  # Σ dedup_count，跨桶归并不丢计数
        assert logical[0].fired_at == at(10, 5)
        assert logical[0].last_fired_at == at(10, 14)
        assert logical[0].canonical_key == ("DemoApiGwHighLatency", "demo-api-gw:8000", "api-gw")

    def test_transitive_chain_three_rows_merge_into_one(self):
        """A(10:01)→B(10:08)→C(10:15) 逐对间隔 ≤10m，传递归并为 1 个（票面实录例）。"""
        a = make_row(at(10, 1), at(10, 3))
        b = make_row(at(10, 8), at(10, 10))
        c = make_row(at(10, 15), at(10, 17))

        logical = merge_logical_alerts([a, b, c])

        assert len(logical) == 1
        assert len(logical[0].rows) == 3

    def test_gap_over_window_breaks_chain_into_two(self):
        """间隔 >window 断链开新逻辑告警。"""
        a = make_row(at(10, 1), at(10, 3))
        b = make_row(at(10, 20), at(10, 22))  # 与 a.last_fired_at 间隔 17m > 10m

        logical = merge_logical_alerts([a, b])

        assert len(logical) == 2

    def test_gap_equal_to_window_merges(self):
        """间隔恰等于 window（≤ 语义）归并。"""
        a = make_row(at(10, 1), at(10, 3))
        b = make_row(at(10, 13), at(10, 15))  # 间隔恰 10m

        assert len(merge_logical_alerts([a, b])) == 1

    def test_different_canonical_labels_never_merge(self):
        other = dict(LABELS, alertname="DemoSlowSql")
        rows = [
            make_row(at(10, 5), at(10, 6)),
            make_row(at(10, 7), at(10, 8), labels=other),
        ]

        assert len(merge_logical_alerts(rows)) == 2

    def test_input_order_does_not_affect_merge_result(self):
        first = make_row(at(10, 5), at(10, 9))
        second = make_row(at(10, 11), at(10, 14))

        assert len(merge_logical_alerts([second, first])) == 1

    def test_naive_datetimes_treated_as_utc(self):
        """SQLite 取回无 tzinfo（坑⑧）：naive 一律按 UTC 归并比较。"""
        first = AlertStatsRow(
            labels=dict(LABELS),
            fired_at=at(10, 5).replace(tzinfo=None),
            last_fired_at=at(10, 9).replace(tzinfo=None),
            dedup_count=1,
            verdict=None,
        )
        second = AlertStatsRow(
            labels=dict(LABELS),
            fired_at=at(10, 11).replace(tzinfo=None),
            last_fired_at=at(10, 14).replace(tzinfo=None),
            dedup_count=1,
            verdict=None,
        )

        assert len(merge_logical_alerts([first, second])) == 1

    def test_empty_input_yields_no_logical_alerts(self):
        assert merge_logical_alerts([]) == []


class TestEffectiveVerdict:
    """归并行 verdict 收敛判定：incident > risk > 未分类 > false_positive（D-20 对偶）。"""

    def test_incident_takes_priority_over_risk(self):
        rows = [
            make_row(at(10, 1), at(10, 2), verdict=Verdict.INCIDENT),
            make_row(at(10, 4), at(10, 5), verdict=Verdict.RISK),
        ]

        assert merge_logical_alerts(rows)[0].effective_verdict == Verdict.INCIDENT.value

    def test_risk_takes_priority_over_false_positive(self):
        """risk 优先于 false_positive：D-20「risk 不算漏报」，不臆断漏报。"""
        rows = [
            make_row(at(10, 1), at(10, 2), verdict=Verdict.RISK),
            make_row(at(10, 4), at(10, 5), verdict=Verdict.FALSE_POSITIVE),
        ]

        assert merge_logical_alerts(rows)[0].effective_verdict == Verdict.RISK.value

    def test_all_false_positive_is_false_positive(self):
        rows = [make_row(at(10, 1), at(10, 2), verdict=Verdict.FALSE_POSITIVE)]

        assert merge_logical_alerts(rows)[0].effective_verdict == Verdict.FALSE_POSITIVE.value

    def test_unclassified_row_blocks_false_positive_assertion(self):
        """存在未分类行且无 incident/risk 时不臆断（评测全量分类后不出现，防御分支）。"""
        rows = [
            make_row(at(10, 1), at(10, 2), verdict=Verdict.FALSE_POSITIVE),
            make_row(at(10, 4), at(10, 5), verdict=None),
        ]

        assert merge_logical_alerts(rows)[0].effective_verdict == "unclassified"


class TestDenoiseMetrics:
    def test_denoise_rate_formula_with_known_counts(self):
        """构造已知 R/I：A(fp, 2 行归并 dedup 3+2=5) + B(incident, 1) + C(risk, 1)
        → R=7、I=1、降噪率 = 6/7；R 用 Σ dedup_count（7）而非行数（4）。"""
        a1 = make_row(at(10, 1), at(10, 3), dedup_count=3, verdict=Verdict.FALSE_POSITIVE)
        a2 = make_row(at(10, 6), at(10, 8), dedup_count=2, verdict=Verdict.FALSE_POSITIVE)
        b = make_row(at(11, 0), at(11, 1), dedup_count=1, verdict=Verdict.INCIDENT)
        c = make_row(at(12, 0), at(12, 1), dedup_count=1, verdict=Verdict.RISK)

        metrics = compute_denoise_metrics([a1, a2, b, c])

        assert metrics["alert_rows"] == 4
        assert metrics["logical_alerts"] == 3
        assert metrics["total_firings"] == 7  # R = Σ dedup_count，不是行数
        assert metrics["incident_count"] == 1
        assert metrics["false_positive_count"] == 1
        assert metrics["risk_count"] == 1
        assert metrics["denoise_rate"] == pytest.approx(6 / 7)
        assert metrics["denoise_rate_defined"] is True
        assert metrics["golden"] is None  # 未提供 golden 时比对段为 None

    def test_r_uses_dedup_sum_not_row_count(self):
        """3 行归并 1 个逻辑告警、每行 dedup_count=2 → R=6（行数口径会失真为 3）。"""
        rows = [
            make_row(at(10, 1), at(10, 3), dedup_count=2, verdict=Verdict.FALSE_POSITIVE),
            make_row(at(10, 6), at(10, 8), dedup_count=2, verdict=Verdict.FALSE_POSITIVE),
            make_row(at(10, 11), at(10, 13), dedup_count=2, verdict=Verdict.FALSE_POSITIVE),
        ]

        metrics = compute_denoise_metrics(rows)

        assert metrics["logical_alerts"] == 1
        assert metrics["total_firings"] == 6
        assert metrics["denoise_rate"] == pytest.approx(1.0)

    def test_r_zero_returns_none_without_zero_division(self):
        """R=0 不除零：降噪率返回 None 并以 defined=False 标注（报告侧注明无有效投递）。"""
        metrics = compute_denoise_metrics([])

        assert metrics["alert_rows"] == 0
        assert metrics["logical_alerts"] == 0
        assert metrics["total_firings"] == 0
        assert metrics["denoise_rate"] is None
        assert metrics["denoise_rate_defined"] is False


class TestGoldenComparison:
    def test_missed_counts_golden_incident_judged_false_positive(self):
        """golden 标注 incident 被判 false_positive → 计 1 漏报。"""
        rows = [make_row(at(10, 1), at(10, 2), verdict=Verdict.FALSE_POSITIVE)]
        golden = [GoldenAnnotation(labels=dict(LABELS))]  # 缺省 classification=incident（D-21）

        metrics = compute_denoise_metrics(rows, golden)

        assert metrics["golden"]["missed"] == 1
        assert metrics["golden"]["risk_observed"] == 0

    def test_risk_does_not_count_as_missed_but_listed_separately(self):
        """golden incident 被判 risk → 不计漏报，单列 risk_observed（D-20）。"""
        rows = [make_row(at(10, 1), at(10, 2), verdict=Verdict.RISK)]
        golden = [GoldenAnnotation(labels=dict(LABELS))]

        metrics = compute_denoise_metrics(rows, golden)

        assert metrics["golden"]["missed"] == 0
        assert metrics["golden"]["risk_observed"] == 1

    def test_incident_matching_golden_incident_is_not_missed(self):
        rows = [make_row(at(10, 1), at(10, 2), verdict=Verdict.INCIDENT)]
        golden = [GoldenAnnotation(labels=dict(LABELS))]

        metrics = compute_denoise_metrics(rows, golden)

        assert metrics["golden"]["missed"] == 0
        assert metrics["golden"]["risk_observed"] == 0
        assert metrics["golden"]["false_alarms"] == 0

    def test_false_alarm_counts_golden_false_positive_judged_incident(self):
        """golden false_positive 被判 incident → false_alarms 单列（冤枉真告警的另一半）。"""
        rows = [make_row(at(10, 1), at(10, 2), verdict=Verdict.INCIDENT)]
        golden = [
            GoldenAnnotation(labels=dict(LABELS), classification=Verdict.FALSE_POSITIVE.value)
        ]

        metrics = compute_denoise_metrics(rows, golden)

        assert metrics["golden"]["false_alarms"] == 1
        assert metrics["golden"]["missed"] == 0

    def test_unmatched_logical_alerts_excluded_from_judgement_and_counted(self):
        """无 golden 覆盖的逻辑告警不计入漏报口径，单列 unmatched 可见（数据底座缺口）。"""
        other = dict(LABELS, alertname="DemoSlowSql")
        rows = [make_row(at(10, 1), at(10, 2), verdict=Verdict.FALSE_POSITIVE)]
        golden = [GoldenAnnotation(labels=other)]

        metrics = compute_denoise_metrics(rows, golden)

        assert metrics["golden"]["unmatched"] == 1
        assert metrics["golden"]["missed"] == 0

    def test_merged_rows_share_one_golden_judgement(self):
        """归并后的逻辑告警整体与 golden 比对一次，不按行重复计漏报。"""
        rows = [
            make_row(at(10, 1), at(10, 3), verdict=Verdict.FALSE_POSITIVE),
            make_row(at(10, 6), at(10, 8), verdict=Verdict.FALSE_POSITIVE),
        ]
        golden = [GoldenAnnotation(labels=dict(LABELS))]

        metrics = compute_denoise_metrics(rows, golden)

        assert metrics["logical_alerts"] == 1
        assert metrics["golden"]["missed"] == 1


class TestSnapshotAlertRows:
    def test_snapshot_materializes_plain_values_usable_after_session_close(self):
        """session 块内物化普通值，关闭后照常核算（detached 访问不炸，坑⑥）。"""
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        create_tables(engine)
        with Session(engine) as session:
            classification = {
                "verdict": Verdict.FALSE_POSITIVE.value,
                "confidence": 0.9,
                "reason": "[resolved_only_ghost]",
                "channel": "rule",
                "model": "mock",
                "tokens": 0,
                "cost_cny": 0.0,
                "classified_at": NOW.isoformat(),
            }
            session.add(
                AlertEvent(
                    fingerprint="fp-stats-1",
                    source="alertmanager",
                    labels_json=dict(LABELS),
                    annotations_json={},
                    fired_at=at(10, 5),
                    classification_json=dict(classification),
                )
            )
            session.add(
                AlertEvent(
                    fingerprint="fp-stats-2",
                    source="alertmanager",
                    labels_json=dict(LABELS),
                    annotations_json={},
                    fired_at=at(10, 11),
                    classification_json=dict(classification),
                )
            )
            session.commit()

            snapshot = snapshot_alert_rows(session)

        # session 已关闭：纯值快照继续参与统计
        metrics = compute_denoise_metrics(snapshot)
        assert metrics["alert_rows"] == 2
        assert metrics["logical_alerts"] == 1  # 6m 间隔跨桶同源 → 归并
        assert metrics["total_firings"] == 2
        assert metrics["false_positive_count"] == 1
        assert metrics["unclassified_count"] == 0
