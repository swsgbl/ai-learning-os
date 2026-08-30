"""Alembic migration 基线测试（backlog M0-05 验收：up / down / dry-run）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

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
    assert {"papers", "questions", "exam_sessions", "answer_events", "submissions"} <= tables
    assert "alembic_version" in tables

    with sqlite3.connect(db_path) as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert version == ("0001_initial",)

    command.downgrade(config, "base")
    assert not ({"papers", "questions", "exam_sessions", "answer_events", "submissions"} & _tables(db_path))


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
