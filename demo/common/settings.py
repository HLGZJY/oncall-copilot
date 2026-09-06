"""demo 业务系统共享配置（被观测目标，不属 oncall 包——见 docs/design/m0-environment-design.md）。"""

import os
from dataclasses import dataclass

_DEFAULT_DB = "mysql+pymysql://root:oncall@localhost:3306/demo"


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    broker_url: str


def load_settings() -> Settings:
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return Settings(
        database_url=os.environ.get("DATABASE_URL", _DEFAULT_DB),
        redis_url=redis_url,
        broker_url=os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/1"),
    )
