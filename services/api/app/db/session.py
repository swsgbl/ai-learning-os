"""Async engine factory and database preparation helpers."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

logger = logging.getLogger(__name__)

_API_DIR = Path(__file__).resolve().parents[2]


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    if ":memory:" in database_url:
        # 内存库必须共享单一连接，否则每个连接各自一个空库。
        return create_async_engine(
            database_url,
            echo=echo,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    return create_async_engine(database_url, echo=echo, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


def is_sqlite(database_url: str) -> bool:
    return urlsplit(database_url).scheme.startswith("sqlite")


async def run_migrations(database_url: str) -> None:
    """Bring the schema to head via Alembic (programmatically, no CLI needed)."""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config(str(_API_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(_API_DIR / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", database_url)
    # Alembic 的 command 层是同步的；放到 worker 线程，避免阻塞事件循环。
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
    logger.info("database migrated to head")


async def prepare_database(engine: AsyncEngine, database_url: str) -> None:
    """SQLite 测试替身直接建表；PostgreSQL 走 Alembic migration。"""
    if is_sqlite(database_url):
        from app.db.base import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return
    await run_migrations(database_url)
