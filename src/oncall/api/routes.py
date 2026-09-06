"""事件卡片查询路由（issue 05）：薄封装，组装逻辑在 card.py。

GET /alerts/{id}/context → 事件卡片 JSON（G3 定案载体，M8 UI 与 M3 取证输入）
GET /alerts → 告警列表（分页 / 按指纹过滤），无 UI。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from oncall.api.card import build_alert_card, list_alerts

if TYPE_CHECKING:
    from oncall.context.config import ContextConfig
    from oncall.context.promql import PromClient


def create_router(
    engine: Engine,
    *,
    context_config: ContextConfig,
    context_client: PromClient | None = None,
) -> APIRouter:
    """路由工厂：引擎与上下文配置由应用工厂注入（测试替身从此进来）。"""
    router = APIRouter()

    @router.get("/alerts")
    def list_alert_events(
        limit: int = 20, offset: int = 0, fingerprint: str | None = None
    ) -> dict[str, Any]:
        with Session(engine) as session:
            return list_alerts(session, limit=limit, offset=offset, fingerprint=fingerprint)

    @router.get("/alerts/{alert_id}/context")
    def alert_card(alert_id: int) -> dict[str, Any]:
        with Session(engine) as session:
            card = build_alert_card(session, alert_id, config=context_config, client=context_client)
        if card is None:
            raise HTTPException(status_code=404, detail=f"alert_events 不存在: id={alert_id}")
        return card

    return router
