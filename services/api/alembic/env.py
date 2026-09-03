from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# 保证 `alembic` 可从仓库任意目录运行。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.db import orm  # noqa: F401  确保模型注册进 metadata
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    # M10-03: API lifespan 内跑迁移时 uvicorn logger 已创建；fileConfig 默认
    # disable_existing_loggers=True 会把它们静默禁用——迁移后 access log、
    # "Application startup complete" 与 5xx traceback 全部不再输出（容器日志
    # 假性干净）。显式保留既有 logger。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    settings = get_settings()
    if settings.database_url:
        return settings.database_url
    raise RuntimeError(
        "数据库 URL 未配置：请设置 DATABASE_URL 或通过程序化调用传入 sqlalchemy.url"
    )


def run_migrations_offline() -> None:
    """dry-run：生成 SQL 但不连接数据库。"""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": _database_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_run_sync)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
