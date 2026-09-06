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

# /health 专用独立小池：健康检查不得依赖主连接池——主池被打满（如慢 SQL 剧本）
# 时 /health 仍须如实反映"服务可达"，否则健康检查与业务故障共沉浮，告警正交性丧失
health_engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=2,
    max_overflow=0,
    pool_timeout=30,  # 排队等待而非报错，避免故障时 /health 503 风暴污染错误率告警
)

# /metrics 滞留任务统计专用引擎：必须与被观测故障正交——主池被打满（死锁/慢 SQL
# 剧本）时 /metrics 仍须在抓取超时内返回。read_timeout=2s 保证 tasks 表被锁死时
# 查询快速失败并跳过本次刷新，而不是拖垮整个指标端点
metrics_engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=1,
    max_overflow=0,
    pool_timeout=1,
    connect_args={"read_timeout": 2},
)


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


def check_db_direct() -> bool:
    """走独立小池的 DB 探活（/health 专用，见 health_engine 注释）。"""
    try:
        with health_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.warning("database health check failed", exc_info=True)
        return False
