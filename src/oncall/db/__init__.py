"""数据访问层：ORM 模型 + dev 建表入口（M1 起步，M2/M4 同源复用）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from oncall.db.models import AlertEvent, Base, Incident, KbChunk

if TYPE_CHECKING:
    from sqlalchemy import Engine

__all__ = ["AlertEvent", "Base", "Incident", "KbChunk", "create_tables"]


def create_tables(engine: Engine) -> None:
    """dev 建表：Base.metadata.create_all（D-13：Alembic 延至切 MySQL 时引入）。"""
    Base.metadata.create_all(engine)
