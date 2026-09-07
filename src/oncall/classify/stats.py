"""降噪统计口径（issue 05 / G6 定案，decisions.md D-20）。

为什么统计层归并而不改 M1 落库：D-14 主指纹含时间窗桶，桶边界附近（如 9:59
与 10:01）的同源连发必然开 2 行——这是「保守不漏收」的特性而非 bug（M1 issue 06
实录已证）。统计层把跨桶分行还原为 1 个逻辑告警（CONTEXT.md「逻辑告警」），
否则「3 连发合并 1 条」类口径验收 flaky。M1 落库语义零改动。

口径（D-20 定案）：
- 归并：canonical label 子集 `{alertname, job, instance}`（同 D-14 指纹输入，
  不含桶）相同 + 相邻两行 `前.last_fired_at` 与 `后.fired_at` 间隔 ≤
  `dedup_window` 的行传递归并为 1 个逻辑告警；
- R = Σ `dedup_count`（D-15 口径：重放不计数，只有新 firing ++）——不是行数，
  否则 M1 行内合并的贡献不计入降噪分子，80% 口径失真；
- 降噪率 = (R − I) / R，I = 判为 incident 的逻辑告警数；R=0 不除零
  （返回 None + denoise_rate_defined=False，报告侧据此标注「无有效投递」）；
- 漏报 = golden 标注 incident 的逻辑告警被判 false_positive 的数量；risk 不算
  漏报（D-07 中间态），单列 risk_observed 观察；
- golden 标注（GoldenAnnotation，评测基准）与分类结果（行内 verdict，被测
  输出）显式分形——标注错 = 评测全错（D-18 教训），二者只在比对函数里相遇。

纯函数为主（输入行序列 → 输出指标 dict），M7 评测台直接复用；DB 只读快照
`snapshot_alert_rows` 在 session 块内物化普通值，规避 detached 实例访问
（issue 04 实录）。
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from oncall.classify.models import Verdict
from oncall.db import AlertEvent
from oncall.ingest.fingerprint import CANONICAL_LABELS, DEFAULT_DEDUP_WINDOW, as_utc

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.orm import Session

__all__ = [
    "UNCLASSIFIED_VERDICT",
    "AlertStatsRow",
    "GoldenAnnotation",
    "LogicalAlert",
    "compute_denoise_metrics",
    "merge_logical_alerts",
    "snapshot_alert_rows",
]

UNCLASSIFIED_VERDICT = "unclassified"  # 存在未分类行且无 incident/risk 时的防御出口


@dataclass(frozen=True)
class AlertStatsRow:
    """统计输入行：落库形态的普通值快照，与 ORM 生命周期解耦（detached 安全）。"""

    labels: Mapping[str, str]
    fired_at: datetime
    last_fired_at: datetime
    dedup_count: int
    verdict: str | None  # classification_json.verdict；None = 未分类


@dataclass(frozen=True)
class LogicalAlert:
    """跨桶分行同源连发在统计层归并出的单一计数单位（D-20）。

    `effective_verdict` = 归并行 verdict 的收敛判定，优先级
    incident > risk > 未分类 > false_positive：
    - incident 优先 = 同源行只要有行被判真即算识别（0 漏报口径）；
    - risk 优先于 false_positive = D-20「risk 不算漏报」，宁单列不臆断漏报；
    - 存在未分类行且无 incident/risk 时落 unclassified，统计不臆断
      （评测运行全量分类后不会出现，属防御分支）。
    """

    canonical_key: tuple[str, ...]
    rows: tuple[AlertStatsRow, ...]

    @property
    def dedup_count(self) -> int:
        return sum(row.dedup_count for row in self.rows)

    @property
    def fired_at(self) -> datetime:
        return min(as_utc(row.fired_at) for row in self.rows)

    @property
    def last_fired_at(self) -> datetime:
        return max(as_utc(row.last_fired_at) for row in self.rows)

    @property
    def effective_verdict(self) -> str:
        verdicts = {row.verdict for row in self.rows}
        if Verdict.INCIDENT.value in verdicts:
            return Verdict.INCIDENT.value
        if Verdict.RISK.value in verdicts:
            return Verdict.RISK.value
        if None in verdicts:
            return UNCLASSIFIED_VERDICT
        return Verdict.FALSE_POSITIVE.value


@dataclass(frozen=True)
class GoldenAnnotation:
    """golden 标注（评测基准，D-18：标注错 = 评测全错——与分类结果显式分形）。"""

    labels: Mapping[str, str]
    classification: str = Verdict.INCIDENT.value  # D-21：可选字段缺省 incident


def canonical_label_key(labels: Mapping[str, str]) -> tuple[str, ...]:
    """D-14 指纹输入（不含桶）：canonical label 子集按固定序取值（falsy 跳出）。"""
    return tuple(labels.get(name, "") for name in CANONICAL_LABELS if labels.get(name))


def merge_logical_alerts(
    rows: Sequence[AlertStatsRow],
    *,
    dedup_window: timedelta = DEFAULT_DEDUP_WINDOW,
) -> list[LogicalAlert]:
    """逻辑告警归并（D-20）：canonical 子集相同 + 相邻行间隔 ≤ 窗口，传递归并。

    组内按 `fired_at` 排序后逐对比较 `后.fired_at − 前.last_fired_at`：
    ≤ `dedup_window` 连链（传递性：A→B→C 逐对 ≤ 窗口则三行归并为 1 个），
    > 窗口断链开新逻辑告警。只发生在统计层，M1 落库行为不动。
    """
    grouped: dict[tuple[str, ...], list[AlertStatsRow]] = defaultdict(list)
    for row in rows:
        grouped[canonical_label_key(row.labels)].append(row)

    logical: list[LogicalAlert] = []
    for key in sorted(grouped):
        ordered = sorted(grouped[key], key=lambda row: as_utc(row.fired_at))
        chain: list[AlertStatsRow] = [ordered[0]]
        for prev, cur in itertools.pairwise(ordered):
            if as_utc(cur.fired_at) - as_utc(prev.last_fired_at) <= dedup_window:
                chain.append(cur)
            else:
                logical.append(LogicalAlert(canonical_key=key, rows=tuple(chain)))
                chain = [cur]
        logical.append(LogicalAlert(canonical_key=key, rows=tuple(chain)))
    return logical


def compute_denoise_metrics(
    rows: Sequence[AlertStatsRow],
    golden_annotations: Sequence[GoldenAnnotation] = (),
    *,
    dedup_window: timedelta = DEFAULT_DEDUP_WINDOW,
) -> dict[str, Any]:
    """降噪指标核算（D-20 口径）：输入评测运行数据 → 输出指标字典（M7 直接复用）。

    golden 比对按 canonical label 子集匹配（同 key 多条时首条生效）；无 golden
    覆盖的逻辑告警不计入漏报/风险口径，单列 unmatched——评测运行中每个告警
    都应有 golden 条目，unmatched > 0 即数据底座缺口（issue 07 据此拦截）。
    """
    logical_alerts = merge_logical_alerts(rows, dedup_window=dedup_window)
    total_firings = sum(row.dedup_count for row in rows)
    incident_count = sum(
        1 for la in logical_alerts if la.effective_verdict == Verdict.INCIDENT.value
    )
    false_positive_count = sum(
        1 for la in logical_alerts if la.effective_verdict == Verdict.FALSE_POSITIVE.value
    )
    risk_count = sum(1 for la in logical_alerts if la.effective_verdict == Verdict.RISK.value)

    # R=0 不除零：降噪率未定义，以 denoise_rate_defined=False 标注
    denoise_rate: float | None = None
    if total_firings > 0:
        denoise_rate = (total_firings - incident_count) / total_firings

    metrics: dict[str, Any] = {
        "alert_rows": len(rows),
        "logical_alerts": len(logical_alerts),
        "total_firings": total_firings,
        "incident_count": incident_count,
        "false_positive_count": false_positive_count,
        "risk_count": risk_count,
        "unclassified_count": sum(
            1 for la in logical_alerts if la.effective_verdict == UNCLASSIFIED_VERDICT
        ),
        "denoise_rate": denoise_rate,
        "denoise_rate_defined": total_firings > 0,
        "golden": None,
    }

    if not golden_annotations:
        return metrics

    golden_map: dict[tuple[str, ...], str] = {}
    for annotation in golden_annotations:
        golden_map.setdefault(canonical_label_key(annotation.labels), annotation.classification)

    missed = risk_observed = false_alarms = unmatched = 0
    for la in logical_alerts:
        expected = golden_map.get(la.canonical_key)
        if expected is None:
            unmatched += 1
        elif expected == Verdict.INCIDENT.value:
            if la.effective_verdict == Verdict.FALSE_POSITIVE.value:
                missed += 1
            elif la.effective_verdict == Verdict.RISK.value:
                risk_observed += 1
        elif (
            expected == Verdict.FALSE_POSITIVE.value
            and la.effective_verdict == Verdict.INCIDENT.value
        ):
            false_alarms += 1

    metrics["golden"] = {
        "missed": missed,
        "risk_observed": risk_observed,
        "false_alarms": false_alarms,
        "unmatched": unmatched,
    }
    return metrics


def snapshot_alert_rows(session: Session) -> list[AlertStatsRow]:
    """DB 只读快照：session 块内物化普通值，调用方无需持有 ORM 行（detached 安全）。"""
    stmt = select(AlertEvent).order_by(AlertEvent.fired_at, AlertEvent.id)
    return [
        AlertStatsRow(
            labels=dict(row.labels_json),
            fired_at=as_utc(row.fired_at),
            last_fired_at=as_utc(row.last_fired_at),
            dedup_count=row.dedup_count,
            verdict=(row.classification_json or {}).get("verdict"),
        )
        for row in session.execute(stmt).scalars()
    ]
