"""知识入库编排（D-56/G8 门槛 + D-31 覆盖联动）：拼装 → 切块 → 向量化 → 落库。

入库门槛 = incident `mitigated`（恢复验证实证）——错误结论没有入库通道
（知识污染第一道防线）；触发方（处置链收尾/调用方）传入 incident_id，本模块
拒绝未实证事件并抛 `NotEligibleForIngestion`。覆盖语义：incident 重复调查
覆盖 `investigations` 旧行（D-31）时，本模块先 `supersede_incident` 打掉旧块
再写新块（第三道防线：淘汰）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from oncall.db.models import Incident
from oncall.db.models import KbChunk as KbChunkRow
from oncall.knowledge.chunking import chunk_report
from oncall.knowledge.report import build_closed_loop_report

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from oncall.knowledge.embedder import Embedder
    from oncall.knowledge.store import VectorStore

__all__ = ["NotEligibleForIngestion", "KnowledgePipeline"]


class NotEligibleForIngestion(RuntimeError):
    """事件未达入库门槛（非 mitigated 实证）——拒绝入库（D-56）。"""


class KnowledgePipeline:
    """入库管线：构造注入 embedder + store（测试 Mock，真实模型可注换）。"""

    def __init__(self, engine: "Engine", embedder: "Embedder", store: "VectorStore") -> None:
        self._engine = engine
        self._embedder = embedder
        self._store = store

    def ingest_incident(self, incident_id: int, session: Any) -> int:
        """拼装五节 → 切块 → 向量化 → 落权威表 + 向量索引；返回写入块数。

        `session` 为调用方事务（步进即写先例 D-33 同款：落库写失败不静默吞）。
        调查行取最近一次（D-31 1:1）；investigation_id 缺省可空（缓存复用路径）。
        """
        from sqlalchemy.orm import Session as OrmSession

        assert isinstance(session, OrmSession)
        incident_row = session.get(Incident, incident_id)
        if incident_row is None:
            raise KeyError(f"incidents 不存在: id={incident_id}")
        if incident_row.status != "mitigated":
            raise NotEligibleForIngestion(
                f"入库门槛未达（D-56）：incident_id={incident_id} status={incident_row.status}，"
                "只有 mitigated（恢复验证实证）的闭环报告才入库"
            )
        sections = build_closed_loop_report(session, incident_id)
        from oncall.db.models import Investigation

        inv = session.scalar(
            select(Investigation).where(Investigation.incident_id == incident_id)
        )
        chunks = chunk_report(
            sections,
            incident_id=incident_id,
            investigation_id=inv.id if inv is not None else None,
        )
        if not chunks:
            return 0
        # 覆盖淘汰：先打掉旧块（D-31 覆盖联动），再写新块
        self.supersede_incident(incident_id, session)
        vectors = self._embedder.embed([c["text"] for c in chunks])
        rows = [KbChunkRow(**c) for c in chunks]
        session.add_all(rows)
        session.flush()
        self._store.upsert(
            ids=[str(r.id) for r in rows],
            vectors=vectors,
            metadatas=[
                {"incident_id": c["incident_id"], "section": c["section"]} for c in chunks
            ],
        )
        return len(rows)

    def supersede_incident(self, incident_id: int, session: Any) -> None:
        """覆盖淘汰（D-31 联动）：该 incident 旧块全部打 superseded_at + 索引清除。"""
        from sqlalchemy.orm import Session as OrmSession

        assert isinstance(session, OrmSession)
        old_rows = list(
            session.scalars(
                select(KbChunkRow).where(
                    KbChunkRow.incident_id == incident_id,
                    KbChunkRow.superseded_at.is_(None),
                )
            )
        )
        if old_rows:
            now = datetime.now(UTC)
            for row in old_rows:
                row.superseded_at = now
            session.flush()
        self._store.delete_incident(incident_id)
