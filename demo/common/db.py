"""demo 业务系统的 MySQL 连接与 tasks 表（被观测目标，不属 oncall 包）。"""

import logging
import time
from datetime import UTC, datetime

from sqlalchemy import String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from common.settings import load_settings

logger = logging.getLogger(__name__)
settings = load_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=5,
    max_overflow=5,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Task(Base):
    """异步任务：api-gw 落库 pending → worker 消费后置 done。字段名是后续剧本的观测契约，勿改名。"""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    payload: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


def init_db(retries: int = 15, delay: float = 2.0) -> None:
    """建库建表；MySQL 未就绪时重试（compose healthcheck 之外的兜底）。"""
    for attempt in range(1, retries + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            Base.metadata.create_all(engine)
            logger.info("database ready, tasks table ensured")
            return
        except Exception:
            logger.warning("database not ready (attempt %d/%d)", attempt, retries)
            time.sleep(delay)
    msg = f"database unreachable after {retries} retries"
    raise RuntimeError(msg)


def check_db() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.warning("database health check failed", exc_info=True)
        return False
