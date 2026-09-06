"""api-gw：demo 业务系统的接入层（被观测目标，不属 oncall 包）。

观测契约（issue 02）：
- /health 返回 db/redis/queue_depth
- /metrics 输出四类指标：QPS（demo_requests_total）/ 延迟（demo_request_duration_seconds）/
  队列深度（demo_queue_depth）/ DB 连接池（demo_db_pool_used/size）
- 日志经 structlog 渲染 JSON 到 stdout，loki driver 直推 Loki
"""

import json
import time
from contextlib import asynccontextmanager
from typing import Any

import redis as redis_lib
import structlog
from celery import Celery
from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from api_gw.metrics import (
    DB_POOL_SIZE,
    DB_POOL_USED,
    QUEUE_DEPTH,
    REQUEST_DURATION_SECONDS,
    REQUESTS_TOTAL,
)
from common.db import SessionLocal, Task, check_db_direct, engine, init_db
from common.logsetup import configure_logging
from common.settings import load_settings

configure_logging("api-gw")
logger = structlog.get_logger("api-gw")
settings = load_settings()

redis_client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
celery_app = Celery(broker=settings.broker_url)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="oncall-demo-api-gw", lifespan=lifespan)


class TaskIn(BaseModel):
    payload: str = ""


def _queue_depth() -> int:
    try:
        return int(redis_client.llen("celery"))
    except Exception:
        logger.warning("redis queue depth query failed")
        return -1


def _refresh_gauges() -> None:
    QUEUE_DEPTH.set(max(_queue_depth(), 0))
    try:
        DB_POOL_USED.set(engine.pool.checkedout())
        DB_POOL_SIZE.set(engine.pool.size())
    except Exception:
        logger.warning("db pool gauge refresh failed")


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # 未处理异常由 ServerErrorMiddleware 兜底回 500，但不会流经下方统计——
        # 必须在此补记，否则 5xx 指标对崩溃型故障失明（慢 SQL 剧本的池超时即此形态）
        REQUESTS_TOTAL.labels(request.method, _endpoint(request), "500").inc()
        REQUEST_DURATION_SECONDS.labels(_endpoint(request)).observe(time.perf_counter() - start)
        raise
    REQUESTS_TOTAL.labels(request.method, _endpoint(request), str(response.status_code)).inc()
    REQUEST_DURATION_SECONDS.labels(_endpoint(request)).observe(time.perf_counter() - start)
    return response


def _endpoint(request: Request) -> str:
    # 未匹配路由一律记 "unmatched"：若回退到原始 path，404 扫描会让
    # endpoint label 基数随 URL 无限膨胀（prometheus 高基数事故源）。
    # 注意必须在路由匹配后读取（call_next 返回/抛出时 scope["route"] 才已写入）
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


@app.get("/health")
def health() -> Response:
    db_ok = check_db_direct()
    depth = _queue_depth()
    redis_ok = depth >= 0
    ok = db_ok and redis_ok
    return Response(
        status_code=200 if ok else 503,
        content=json.dumps(
            {
                "status": "ok" if ok else "degraded",
                "db": "up" if db_ok else "down",
                "redis": "up" if redis_ok else "down",
                "queue_depth": depth,
            }
        ),
        media_type="application/json",
    )


@app.get("/metrics")
def metrics() -> Response:
    _refresh_gauges()
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/tasks", status_code=201)
def create_task(body: TaskIn) -> dict[str, Any]:
    with SessionLocal() as session:
        task = Task(payload=body.payload[:255])
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id, task_status = task.id, task.status
    celery_app.send_task("demo.process_task", args=[task_id])
    logger.info("task created", task_id=task_id, status=task_status)
    return {"id": task_id, "status": task_status}


@app.get("/tasks/{task_id}")
def get_task(task_id: int) -> dict[str, Any]:
    with SessionLocal() as session:
        task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return {"id": task.id, "status": task.status, "payload": task.payload}
