"""FastAPI 入口：POST /ingest（Alertmanager receiver 的目标端点）+ 事件卡片查询。

本票只落 oncall 侧端点；deploy/ 的 dump receiver 与双写开关不动（切换在 issue 06）。

dev 运行（仓库根目录）：
    uvicorn oncall.ingest.app:create_app --factory --port 8000
数据库 URL 经环境变量 ONCALL_DATABASE_URL 配置，默认 sqlite:///./oncall.db。
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import TYPE_CHECKING

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from oncall.api.routes import create_router
from oncall.context.config import ContextConfig
from oncall.db import create_tables
from oncall.ingest.fingerprint import DEFAULT_DEDUP_WINDOW
from oncall.ingest.schemas import AlertmanagerWebhook
from oncall.ingest.service import ingest_webhook

if TYPE_CHECKING:
    from oncall.context.promql import PromClient

DATABASE_URL_ENV = "ONCALL_DATABASE_URL"
DEFAULT_DATABASE_URL = "sqlite:///./oncall.db"
DEDUP_WINDOW_SECONDS_ENV = "ONCALL_DEDUP_WINDOW_SECONDS"


def create_app(
    engine: Engine | None = None,
    dedup_window: timedelta | None = None,
    context_config: ContextConfig | None = None,
    context_client: PromClient | None = None,
) -> FastAPI:
    """应用工厂：测试注入内存库引擎与上下文替身；进程启动走环境变量配置。

    时间窗默认 10 分钟（G1），可用 `ONCALL_DEDUP_WINDOW_SECONDS` 覆盖；
    上下文配置默认 `ContextConfig.from_env()`（issue 04 口径）。
    """
    if engine is None:
        engine = create_engine(os.environ.get(DATABASE_URL_ENV, DEFAULT_DATABASE_URL))
    if dedup_window is None:
        dedup_window = timedelta(
            seconds=int(
                os.environ.get(DEDUP_WINDOW_SECONDS_ENV, int(DEFAULT_DEDUP_WINDOW.total_seconds()))
            )
        )
    if context_config is None:
        context_config = ContextConfig.from_env()
    create_tables(engine)  # dev 建表（D-13：Alembic 延至切 MySQL 时引入）

    app = FastAPI(title="oncall-copilot", version="0.1.0")

    @app.post("/ingest")
    def ingest(webhook: AlertmanagerWebhook) -> dict[str, int]:
        """接收 Alertmanager webhook v4 payload，归一化落 alert_events。

        成功 2xx + {received, deduped}；校验失败由 FastAPI 落 422（4xx 不吞错，
        让 AM 的重试语义可见异常）。
        """
        with Session(engine) as session:
            result = ingest_webhook(session, webhook, dedup_window)
        return {"received": result.received, "deduped": result.deduped}

    # 事件卡片查询路由（issue 05）：context_client 为 None 时 collect_context
    # 内部按 config 建生产客户端；测试注入替身避免真实网络
    app.include_router(
        create_router(engine, context_config=context_config, context_client=context_client)
    )

    return app
