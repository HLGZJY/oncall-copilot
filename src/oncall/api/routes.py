"""查询与分类路由：事件卡片（issue 05）+ 双通道分类与事件查询（issue 04）。

GET  /alerts                → 告警列表（分页 / 指纹 / verdict 过滤）
GET  /alerts/{id}/context   → 事件卡片 JSON（G3 定案载体，M8 UI 与 M3 取证输入）
POST /classify              → 双通道编排分类（G4：独立入口，不串联 /ingest）
GET  /incidents             → 事件列表（G5：M3 调查入口查询面）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from oncall.api.card import build_alert_card, list_alerts, list_incidents
from oncall.classify.models import Verdict
from oncall.classify.service import classify_alerts
from oncall.db import AlertEvent

if TYPE_CHECKING:
    from oncall.classify.service import ClassifyRuntime
    from oncall.context.config import ContextConfig
    from oncall.context.promql import PromClient

STATUS_DEDUPED = "deduped"  # 架构 §4 冻结枚举：仅未分类行需要分类


class ClassifyRequest(BaseModel):
    """POST /classify 入参契约（G4）：`{alert_id}` 与 `{batch}` 必须二选一。"""

    model_config = ConfigDict(extra="forbid")

    alert_id: int | None = None
    batch: Literal["all", "pending"] | None = None


def create_router(
    engine: Engine,
    *,
    context_config: ContextConfig,
    context_client: PromClient | None = None,
    classify_runtime: ClassifyRuntime | None = None,
) -> APIRouter:
    """路由工厂：引擎、上下文配置与分类运行时由应用工厂注入（测试替身从此进来）。

    `classify_runtime` 缺省 None 时 /classify 落 503——独立入口不静默降级到 mock，
    LLM 依赖必须显式注入（R6：ingest 主链路可用性与本通道无关）。
    """
    llm_channel = classify_runtime.llm_channel if classify_runtime is not None else None
    classify_options = classify_runtime.options if classify_runtime is not None else None
    router = APIRouter()

    @router.get("/alerts")
    def list_alert_events(
        limit: int = 20,
        offset: int = 0,
        fingerprint: str | None = None,
        verdict: Verdict | None = None,
    ) -> dict[str, Any]:
        with Session(engine) as session:
            return list_alerts(
                session,
                limit=limit,
                offset=offset,
                fingerprint=fingerprint,
                verdict=None if verdict is None else verdict.value,
            )

    @router.get("/alerts/{alert_id}/context")
    def alert_card(alert_id: int) -> dict[str, Any]:
        with Session(engine) as session:
            card = build_alert_card(session, alert_id, config=context_config, client=context_client)
        if card is None:
            raise HTTPException(status_code=404, detail=f"alert_events 不存在: id={alert_id}")
        return card

    @router.post("/classify")
    def classify(req: ClassifyRequest) -> dict[str, int]:
        """双通道编排分类：规则先行 → LLM 通道 → 同事务落库（G4 编排语义在 service.py）。"""
        if (req.alert_id is None) == (req.batch is None):
            raise HTTPException(status_code=422, detail="alert_id 与 batch 必须二选一，且只给其一")
        if llm_channel is None:
            raise HTTPException(
                status_code=503, detail="LLM 通道未配置：create_app(llm_channel=...) 注入后可用"
            )
        with Session(engine) as session:
            if req.alert_id is not None:
                if session.get(AlertEvent, req.alert_id) is None:
                    raise HTTPException(
                        status_code=404, detail=f"alert_events 不存在: id={req.alert_id}"
                    )
                alert_ids = [req.alert_id]
            elif req.batch == "pending":
                alert_ids = list(
                    session.scalars(
                        select(AlertEvent.id)
                        .where(AlertEvent.status == STATUS_DEDUPED)
                        .order_by(AlertEvent.id)
                    ).all()
                )
            else:  # batch == "all"
                alert_ids = list(
                    session.scalars(select(AlertEvent.id).order_by(AlertEvent.id)).all()
                )
            summary = classify_alerts(
                session, alert_ids, llm_channel=llm_channel, options=classify_options
            )
        return {
            "classified": summary.classified,
            "false_positive": summary.false_positive,
            "risk": summary.risk,
            "incident": summary.incident,
            "llm_calls": summary.llm_calls,
        }

    @router.get("/incidents")
    def incidents(limit: int = 20, offset: int = 0) -> dict[str, Any]:
        with Session(engine) as session:
            return list_incidents(session, limit=limit, offset=offset)

    return router
