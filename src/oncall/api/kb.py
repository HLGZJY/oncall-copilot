"""知识库 API（M6-T4 / D-49–D-57）：入库触发 + 知识条目查询（M7/M8 数据面）。

- `POST /kb/ingest/{incident_id}`：手动/程序化触发闭环报告入库——D-56 门槛
  （incident mitigated）在 pipeline 内强制，未实证 → 409；无调查/无事件 → 404；
- `GET /kb/incidents/{incident_id}`：该事件活跃知识块列表（superseded 不出），
  404 = 未入库（未实证或从未闭环）。

C3 合法方向：api → knowledge → db 单向（与 api→remediation→db 同构）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from oncall.db.models import Incident
from oncall.db.models import KbChunk as KbChunkRow
from oncall.knowledge.pipeline import NotEligibleForIngestion

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from oncall.knowledge.pipeline import KnowledgePipeline

__all__ = ["create_kb_router"]


def _serialize_chunk(row: KbChunkRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "incident_id": row.incident_id,
        "investigation_id": row.investigation_id,
        "section": row.section,
        "seq": row.seq,
        "text": row.text,
        "source_meta_json": row.source_meta_json,
        "hit_count": row.hit_count,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def create_kb_router(engine: Engine, pipeline: KnowledgePipeline | None) -> APIRouter:
    """知识库路由工厂：pipeline 缺省 None → ingest 落 503（能力未装配不静默降级）。"""
    router = APIRouter()

    @router.post("/kb/ingest/{incident_id}")
    def ingest(incident_id: int) -> dict[str, Any]:
        if pipeline is None:
            raise HTTPException(
                status_code=503,
                detail="知识入库能力未配置（KnowledgePipeline 未注入 / ONCALL_KB_ENABLED 未开启）",
            )
        with Session(engine) as session:
            if session.get(Incident, incident_id) is None:
                raise HTTPException(
                    status_code=404, detail=f"incidents 不存在: id={incident_id}"
                )
            try:
                count = pipeline.ingest_incident(incident_id, session)
                session.commit()
            except NotEligibleForIngestion as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"incident_id": incident_id, "chunks": count}

    @router.get("/kb/incidents/{incident_id}")
    def kb_incident(incident_id: int) -> dict[str, Any]:
        """事件知识条目：活跃块（superseded 不出）；未入库 → 404。"""
        with Session(engine) as session:
            if session.get(Incident, incident_id) is None:
                raise HTTPException(
                    status_code=404, detail=f"incidents 不存在: id={incident_id}"
                )
            rows = list(
                session.scalars(
                    select(KbChunkRow)
                    .where(
                        KbChunkRow.incident_id == incident_id,
                        KbChunkRow.superseded_at.is_(None),
                    )
                    .order_by(KbChunkRow.section, KbChunkRow.seq)
                )
            )
        if not rows:
            raise HTTPException(
                status_code=404, detail=f"事件未入库（未实证或从未闭环）: id={incident_id}"
            )
        return {"incident_id": incident_id, "count": len(rows), "chunks": [_serialize_chunk(r) for r in rows]}

    return router
