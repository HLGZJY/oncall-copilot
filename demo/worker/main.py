"""worker：Celery 消费端，把 tasks 表 pending 行置为 done（被观测目标）。

消费延迟刻意保留 time.sleep(0.2)：模拟业务耗时，供 T2 的延迟/队列深度指标观测。
"""

import logging
import time

from celery import Celery
from sqlalchemy.exc import SQLAlchemyError

from common.db import SessionLocal, Task
from common.settings import load_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("worker")
settings = load_settings()

celery_app = Celery("demo", broker=settings.broker_url)


def _mark_done(task_id: int, attempts: int = 3, delay: float = 2.0) -> None:
    for attempt in range(1, attempts + 1):
        try:
            with SessionLocal() as session:
                task = session.get(Task, task_id)
                if task is None:
                    logger.error("task %d not found", task_id)
                    return
                task.status = "done"
                session.commit()
            logger.info("task %d done", task_id)
            return
        except SQLAlchemyError:
            logger.warning(
                "db write failed for task %d (attempt %d/%d)", task_id, attempt, attempts
            )
            time.sleep(delay)
    msg = f"failed to mark task {task_id} done after {attempts} attempts"
    raise RuntimeError(msg)


@celery_app.task(name="demo.process_task")
def process_task(task_id: int) -> None:
    time.sleep(0.2)
    _mark_done(task_id)
