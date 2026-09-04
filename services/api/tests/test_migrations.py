"""Alembic migration 基线测试（backlog M0-05 验收：up / down / dry-run）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic.config import Config

from alembic import command

_API_DIR = Path(__file__).resolve().parents[1]


def _config(database_url: str) -> Config:
    config = Config(str(_API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_API_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {name for (name,) in rows}


def test_migration_up_down_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate.db"
    database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    config = _config(database_url)

    command.upgrade(config, "head")
    tables = _tables(db_path)
    assert {
        "papers",
        "questions",
        "exam_sessions",
        "answer_events",
        "submissions",
        "sources",
        "resources",
    } <= tables
    assert "alembic_version" in tables

    with sqlite3.connect(db_path) as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    from alembic.script import ScriptDirectory as _SD

    head = _SD.from_config(_config("sqlite://")).get_heads()[0]
    assert version == (head,)

    command.downgrade(config, "base")
    leftover = _tables(db_path)
    assert not ({"papers", "questions", "exam_sessions", "answer_events", "submissions", "sources", "resources"} & leftover)


def test_migration_upgrade_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate2.db"
    config = _config(f"sqlite+aiosqlite:///{db_path.as_posix()}")

    command.upgrade(config, "head")
    command.upgrade(config, "head")  # 重复 upgrade 不报错
    with sqlite3.connect(db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='submissions'"
        ).fetchone()[0]
    assert count == 1


def test_migration_offline_dry_run(tmp_path: Path, capsys) -> None:
    config = _config(f"sqlite+aiosqlite:///{(tmp_path / 'never_created.db').as_posix()}")
    command.upgrade(config, "head", sql=True)

    output = capsys.readouterr().out
    assert "CREATE TABLE papers" in output
    assert "CREATE TABLE answer_events" in output
    assert not (tmp_path / "never_created.db").exists()


def test_migration_preserves_existing_loggers(tmp_path: Path) -> None:
    """M10-03: lifespan 内跑迁移不得禁用 uvicorn logger。

    生产时序：uvicorn 启动即创建 uvicorn.error/uvicorn.access logger →
    lifespan 里 command.upgrade 加载本 env.py → fileConfig 若按默认
    disable_existing_loggers=True 会把它们静默，迁移后 access log、
    "Application startup complete" 与 5xx traceback 全部消失
    （容器日志假性干净）。回归：fileConfig 必须显式保留既有 logger。
    """
    import logging

    # 模拟 uvicorn 启动时序：迁移执行前 logger 已实例化（fileConfig 只
    # 禁用“已存在”的 logger，预先 getLogger 即可复现生产条件）
    access_logger = logging.getLogger("uvicorn.access")
    error_logger = logging.getLogger("uvicorn.error")
    access_logger.info("pre-migration access line")
    error_logger.info("pre-migration error line")

    config = _config(f"sqlite+aiosqlite:///{(tmp_path / 'logging.db').as_posix()}")
    command.upgrade(config, "head")

    assert not access_logger.disabled, "迁移禁用了 uvicorn.access：access log 将静默"
    assert not error_logger.disabled, "迁移禁用了 uvicorn.error：启动完成与 5xx traceback 将静默"
