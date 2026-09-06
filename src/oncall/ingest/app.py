"""FastAPI 入口：POST /ingest（Alertmanager receiver 的目标端点）。

本票只落 oncall 侧端点；deploy/ 的 dump receiver 与双写开关不动（切换在 issue 06）。

dev 运行（仓库根目录）：
    uvicorn oncall.ingest.app:create_app --factory --port 8000
数据库 URL 经环境变量 ONCALL_DATABASE_URL 配置，默认 sqlite:///./oncall.db。
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from oncall.db import create_tables
from oncall.ingest.schemas import AlertmanagerWebhook
from oncall.ingest.service import ingest_webhook

DATABASE_URL_ENV = "ONCALL_DATABASE_URL"
DEFAULT_DATABASE_URL = "sqlite:///./oncall.db"


def create_app(engine: Engine | None = None) -> FastAPI:
    """应用工厂：测试注入内存库引擎；进程启动走环境变量配置的 URL。"""
    if engine is None:
        engine = create_engine(os.environ.get(DATABASE_URL_ENV, DEFAULT_DATABASE_URL))
    create_tables(engine)  # dev 建表（D-13：Alembic 延至切 MySQL 时引入）

    app = FastAPI(title="oncall-copilot", version="0.1.0")

    @app.post("/ingest")
    def ingest(webhook: AlertmanagerWebhook) -> dict[str, int]:
        """接收 Alertmanager webhook v4 payload，归一化落 alert_events。

        成功 2xx + {received, deduped}；校验失败由 FastAPI 落 422（4xx 不吞错，
        让 AM 的重试语义可见异常）。
        """
        with Session(engine) as session:
            result = ingest_webhook(session, webhook)
        return {"received": result.received, "deduped": result.deduped}

    return app
