"""api-gw：demo 业务系统的接入层（被观测目标，不属 oncall 包）。

观测契约：/health 返回 db/redis/queue_depth；POST /tasks 建任务并投递 Celery；
/metrics 挂 prometheus_client 默认注册表（T2 在此扩业务指标）。
"""

import logging
from contextlib import asynccontextmanager
from typing import Any

import redis as redis_lib
from celery import Celery
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from pydantic import BaseModel

from common.db import SessionLocal, Task, check_db, init_db
from common.settings import load_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("api-gw")
settings = load_settings()

redis_client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
celery_app = Celery(broker=settings.broker_url)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="oncall-demo-api-gw", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


class TaskIn(BaseModel):
    payload: str = ""


def _queue_depth() -> int:
    try:
        return int(redis_client.llen("celery"))
    except Exception:
        logger.warning("redis queue depth query failed", exc_info=True)
        return -1


@app.get("/health")
def health() -> JSONResponse:
    db_ok = check_db()
    depth = _queue_depth()
    redis_ok = depth >= 0
    ok = db_ok and redis_ok
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": "ok" if ok else "degraded",
            "db": "up" if db_ok else "down",
            "redis": "up" if redis_ok else "down",
            "queue_depth": depth,
        },
    )


@app.post("/tasks", status_code=201)
def create_task(body: TaskIn) -> dict[str, Any]:
    with SessionLocal() as session:
        task = Task(payload=body.payload[:255])
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id, task_status = task.id, task.status
    celery_app.send_task("demo.process_task", args=[task_id])
    logger.info("task %d created", task_id)
    return {"id": task_id, "status": task_status}


@app.get("/tasks/{task_id}")
def get_task(task_id: int) -> dict[str, Any]:
    with SessionLocal() as session:
        task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return {"id": task.id, "status": task.status, "payload": task.payload}
