"""worker：Celery 消费端，把 tasks 表 pending 行置为 done（被观测目标）。

消费延迟刻意保留 time.sleep(0.2)：模拟业务耗时，供延迟/队列深度指标观测。
日志经 structlog 渲染 JSON 到 stdout，loki driver 直推 Loki。
"""

import time

import redis as redis_lib
import structlog
from celery import Celery
from sqlalchemy.exc import SQLAlchemyError

from common.db import SessionLocal, Task
from common.logsetup import configure_logging
from common.settings import load_settings

configure_logging("worker")
logger = structlog.get_logger("worker")
settings = load_settings()

redis_client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)

celery_app = Celery("demo", broker=settings.broker_url)
# structlog 直接写 stdout（loki driver 采集）——禁掉 celery 的日志劫持与 stdout 重定向，
# 否则 JSON 行会被包上 "WARNING/ForkPoolWorker" 前缀，破坏结构化格式
celery_app.conf.update(
    worker_hijack_root_logger=False,
    worker_redirect_stdouts=False,
)


def _mark_failed(task_id: int) -> None:
    """重试耗尽后的终态：置 failed 并记 error 级日志，保证失败可观测。"""
    try:
        with SessionLocal() as session:
            task = session.get(Task, task_id)
            if task is not None:
                task.status = "failed"
                session.commit()
        logger.error("task failed", task_id=task_id, reason="db write retries exhausted")
    except SQLAlchemyError:
        # 连终态都写不进去（DB 彻底不可用）——只能靠日志暴露
        logger.error("task failed and terminal state unwritable", task_id=task_id)


def _mark_done(task_id: int, attempts: int = 3, delay: float = 2.0) -> None:
    for attempt in range(1, attempts + 1):
        try:
            with SessionLocal() as session:
                task = session.get(Task, task_id)
                if task is None:
                    logger.error("task not found", task_id=task_id)
                    return
                task.status = "done"
                session.commit()
            logger.info("task done", task_id=task_id)
            return
        except SQLAlchemyError:
            logger.warning("db write failed", task_id=task_id, attempt=attempt, attempts=attempts)
            time.sleep(delay)
    _mark_failed(task_id)


def _protocol_supported(protocol: str) -> bool:
    """剧本 11 的版本偏斜门禁：redis 键 chaos:min_protocol 存在时，低于该版本的
    任务消息视为不兼容（模拟"旧消息遇新协议要求"）。键不存在 = 全兼容。"""
    try:
        min_protocol = redis_client.get("chaos:min_protocol")
    except Exception:
        return True
    if not min_protocol:
        return True
    return protocol >= min_protocol


@celery_app.task(name="demo.process_task")
def process_task(task_id: int, protocol: str = "v1") -> None:
    # 语义层故障形态：消息被正常消费、指标日志全正常，但业务侧任务永远 pending。
    # 旧消息被跳过后**不会**重新入队——真实版本偏斜事故的典型数据丢失形态
    if not _protocol_supported(protocol):
        logger.error(
            "task protocol incompatible, task left pending",
            task_id=task_id,
            protocol=protocol,
            min_protocol=redis_client.get("chaos:min_protocol"),
        )
        return
    time.sleep(0.2)
    _mark_done(task_id)
