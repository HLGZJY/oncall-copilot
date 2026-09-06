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
from sqlalchemy import text

from api_gw.metrics import (
    DB_POOL_SIZE,
    DB_POOL_USED,
    QUEUE_DEPTH,
    REQUEST_DURATION_SECONDS,
    REQUESTS_TOTAL,
    STALE_PENDING,
    TASK_CACHE_OPERATIONS,
)
from common.db import SessionLocal, Task, check_db_direct, engine, init_db, metrics_engine
from common.logsetup import configure_logging
from common.settings import load_settings

configure_logging("api-gw")
logger = structlog.get_logger("api-gw")
settings = load_settings()

# 任务消息协议版本：随消息一起发给 worker。当前系统统一 v1；
# 剧本 11 通过 redis 门禁 chaos:min_protocol 模拟"旧消息遇新协议要求"的版本偏斜
TASK_PROTOCOL_VERSION = "v1"
# GET /tasks/{id} 的 redis 缓存 TTL（秒）——剧本 10 缓存雪崩的失效域
TASK_CACHE_TTL_SECONDS = 60

redis_client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
# 队列深度必须读 broker 所在的 redis db（broker_url 与 redis_url 的 db 可以不同——
# 实测 broker 在 /1 而 redis_url 指向 /0，读错 db 会让 demo_queue_depth 恒为 0）
broker_client = redis_lib.Redis.from_url(settings.broker_url, decode_responses=True)
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
        return int(broker_client.llen("celery"))
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
    _refresh_stale_pending()


def _refresh_stale_pending() -> None:
    """统计滞留 pending 超 60s 的任务数（业务语义层观测，剧本 11）。

    走专用短超时引擎（metrics_engine）：tasks 表被锁死（死锁/慢 SQL 剧本）时
    查询 2s 内失败并跳过刷新，/metrics 绝不因被观测故障而失联——
    指标端点与故障共沉浮会让所有告警一起失明（实测教训，见 issue 04）。
    """
    try:
        with metrics_engine.connect() as conn:
            (count,) = conn.execute(
                text(
                    "SELECT COUNT(*) FROM tasks "
                    "WHERE status = 'pending' "
                    "AND created_at < UTC_TIMESTAMP() - INTERVAL 60 SECOND"
                )
            ).fetchone()
        STALE_PENDING.set(count)
    except Exception:
        logger.warning("stale pending gauge refresh failed")


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
    celery_app.send_task("demo.process_task", args=[task_id, TASK_PROTOCOL_VERSION])
    logger.info("task created", task_id=task_id, status=task_status)
    return {"id": task_id, "status": task_status}


@app.get("/tasks/{task_id}")
def get_task(task_id: int) -> dict[str, Any]:
    # 读缓存（剧本 10 缓存雪崩的失效域）：TTL 60s，键 task:{id}
    # 注意：worker 置 done 后缓存里仍是旧状态，最长 60s 不一致——demo 可接受
    cache_key = f"task:{task_id}"
    try:
        cached = redis_client.get(cache_key)
    except Exception:
        cached = None
    if cached is not None:
        TASK_CACHE_OPERATIONS.labels(result="hit").inc()
        return json.loads(cached)
    TASK_CACHE_OPERATIONS.labels(result="miss").inc()
    with SessionLocal() as session:
        task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    data = {"id": task.id, "status": task.status, "payload": task.payload}
    try:
        redis_client.setex(cache_key, TASK_CACHE_TTL_SECONDS, json.dumps(data))
    except Exception:
        logger.warning("task cache write failed", task_id=task_id)
    return data
