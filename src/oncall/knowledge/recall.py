"""开局召回（D-54/G6 + D-57/G9）：确定性前置组装，**不是 Agent 步**、不占 15 步预算。

两通道互斥（指纹精确命中优先）：
- **缓存复用**（kind=reuse）：新事件告警的 **canonical label 子集**（D-14 指纹
  输入去掉时间窗桶 = D-20 逻辑告警口径，全指纹含桶且唯一约束不可跨行重复）
  精确命中一个已有 `mitigated` 闭环 + 活跃知识块的历史事件 → 复用整报告，
  不重查、不建新调查；复用不更新 hit_count（D-57，M7 召回质量口径分离）；
- **向量参考**（kind=reference）：指纹未命中 → 用开局告警的 labels 文本做
  向量检索，活跃块命中 → 作为 `source=kb` 参考证据随 opening 注入（hit_count++
  真实召回计）；
- 都未命中 → kind=none。
0 漏报纪律：复用前提是既往结论已实证（mitigated），否则宁可重查——保守方向
与 D-14「桶边界保守不漏收」同向。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from oncall.db.models import AlertEvent, Investigation, KbChunk
from oncall.db.models import Incident as IncidentRow

if TYPE_CHECKING:
    from oncall.knowledge.retriever import KbRetriever

__all__ = ["RecallOutcome", "opening_recall"]


@dataclass(frozen=True)
class RecallOutcome:
    """开局召回产物（两通道互斥；注入 opening / POST /investigate 出口）。"""

    kind: str  # "reuse" | "reference" | "none"
    reused_from: int | None = None  # reuse 通道：既有事件 id（reused_from 出口标注）
    hits: list[dict[str, Any]] = field(default_factory=list)  # reference 通道：kb_hits

    @property
    def hit(self) -> bool:
        return self.kind != "none"


def opening_recall(
    session: Session,
    incident_id: int,
    retriever: KbRetriever,
    *,
    top_k: int = 3,
    reference_query: str | None = None,
) -> RecallOutcome:
    """新调查开局的确定性召回（调用方：api investigate 前置）。

    `reference_query` 缺省 = 开局锚告警 labels 摘要文本（alertname@instance，
    确定性拼装不经 LLM）；可注入测试文本。session 事务归调用方（与 api 层
    既有 Session 范围一致），hit_count 增量随调用方 commit。
    """
    incident = session.get(IncidentRow, incident_id)
    if incident is None:
        return RecallOutcome(kind="none")

    # ── 通道一：指纹精确命中 → 缓存复用（D-57）──
    reuse_incident_id = _fingerprint_cache_hit(session, incident)
    if reuse_incident_id is not None:
        return RecallOutcome(kind="reuse", reused_from=reuse_incident_id)

    # ── 通道二：向量相似 → 参考证据（真实召回，hit_count++）──
    query = reference_query or _labels_summary(session, incident)
    if not query:
        return RecallOutcome(kind="none")
    hits = retriever.search(query, top_k, session=session)
    if not hits:
        return RecallOutcome(kind="none")
    return RecallOutcome(kind="reference", hits=[h.as_dict() for h in hits])


def _fingerprint_cache_hit(session: Session, incident: IncidentRow) -> int | None:
    """该事件告警的 **canonical label 子集**（D-14 指纹输入去掉时间窗桶 = D-20
    逻辑告警口径）在其他 mitigated 事件中出现过 → 返回那个事件 id。

    注：D-14 全指纹含时间窗桶且带唯一约束——同桶同源合并不产生新行，跨事件
    的「精确命中」只能是桶外同源连发（canonical 子集相同），故缓存比对键 =
    canonical 子集而非全指纹；排除自身；同签名多事件取最近收尾者；
    必须存在活跃知识块（有实证闭环的入库面）才算可复用。
    """
    signatures = _canonical_signatures(session, incident)
    if not signatures:
        return None
    rows = session.execute(
        select(IncidentRow.id)
        .join(Investigation, Investigation.incident_id == IncidentRow.id)
        .join(KbChunk, KbChunk.incident_id == IncidentRow.id)
        .where(
            IncidentRow.id != incident.id,
            IncidentRow.status == "mitigated",
            KbChunk.superseded_at.is_(None),
        )
        .order_by(Investigation.finished_at.desc())
        .limit(20)  # 候选上界：12 剧本规模足够，应用层精查canonical交集
    ).all()
    for (candidate_id,) in rows:
        candidate = session.get(IncidentRow, candidate_id)
        if _canonical_signatures(session, candidate) & signatures:
            return candidate_id
    return None


_CANONICAL_KEYS = ("alertname", "instance", "job")  # D-14 canonical label 子集（存在者参与）


def _canonical_signatures(session: Session, incident: IncidentRow) -> set[tuple[str, ...]]:
    """事件告警集的 canonical 子集集合（`{alertname, instance, job}` 存在者按字典序）。"""
    if not incident.alert_ids:
        return set()
    alerts = list(
        session.scalars(select(AlertEvent).where(AlertEvent.id.in_(list(incident.alert_ids))))
    )
    out: set[tuple[str, ...]] = set()
    for alert in alerts:
        labels = alert.labels_json
        out.add(tuple(labels.get(k, "") for k in _CANONICAL_KEYS))
    return out


def _labels_summary(session: Session, incident: IncidentRow) -> str:
    """开局锚告警 labels 摘要（确定性文本，向量通道 query）。"""
    if not incident.alert_ids:
        return ""
    alert = session.get(AlertEvent, incident.alert_ids[0])
    if alert is None:
        return ""
    labels = alert.labels_json
    name = labels.get("alertname", "")
    return f"{name}@{labels.get('instance', '')} {labels.get('job', '')}".strip()
