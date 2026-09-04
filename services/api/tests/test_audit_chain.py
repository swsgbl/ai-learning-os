"""M10-04 治理审计哈希链：append_audit、篡改检测、CLI 与静态守卫。

覆盖矩阵：
1. canonical JSON / datetime 确定性；entry_hash 对 previous/sequence/全部
   audit 稳定字段敏感；非法 previous_hash 拒绝；
2. append_audit 两条成链（genesis -> entry1 -> entry2，state 同事务推进）、
   state 缺失安全初始化、事务回滚零残留（含 state 初始化）、缺字段与
   敏感键（password/token/secret 嵌套变体）fail-closed；
3. verify_audit_chain 篡改识别：audit 行 action/before 篡改、entry_hash/
   previous_hash 篡改、删除 entry、sequence gap、head/state 漂移、
   孤儿 entry、state 缺失、表缺失；合法空链（0 entry + genesis state）
   通过；problems 不泄漏 before/after 正文；
4. 静态守卫：生产代码（app/ 除 domain/audit_chain.py 自身）不得直接构造
   AuditLogRow 绕链、不得从 repositories.audit 引 audit_insert_values；
5. CLI：注册分发、exit 0/1/2、--json 结构；
6. 0027 迁移：存量 audit_log 按 id 升序建链 + downgrade 只删新表。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from alembic.config import Config
from sqlalchemy import delete, insert, select, update

from alembic import command
from app.db.base import Base
from app.db.orm import AuditChainEntryRow, AuditChainStateRow, AuditLogRow
from app.db.session import create_engine, make_sessionmaker
from app.domain.audit_chain import (
    AUDIT_CHAIN_ALGORITHM,
    GENESIS_PREVIOUS_HASH,
    append_audit,
    audit_hash_values,
    canonical_datetime,
    canonical_json_bytes,
    compute_entry_hash,
)
from app.ops import cli as cli_module
from app.ops.audit_chain_verify import (
    format_verify_summary,
    verify_audit_chain,
)

NOW = datetime(2026, 9, 4, 8, 0, 0, 123456, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)

_API_DIR = Path(__file__).resolve().parents[1]


def _payload(action: str = "user.promote", **overrides) -> dict:
    base = {
        "action": action,
        "target_type": "user",
        "target_id": "u1",
        "request_id": "req-1",
        "actor_id": "u_admin",
        "actor_username": "admin",
        "before": {"role": "learner"},
        "after": {"role": "admin"},
    }
    base.update(overrides)
    return base


def _fixed_clock(value: datetime = NOW):
    return lambda: value


def _make_db(tmp_path, *, rows: int = 0) -> str:
    """create_all 建库；rows>0 时追加 rows 条链化审计（sequence 1..rows）。"""
    db_url = f"sqlite+aiosqlite:///{(tmp_path / 'chain.db').as_posix()}"

    async def run() -> None:
        engine = create_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = make_sessionmaker(engine)
        for index in range(rows):
            async with sessions() as session, session.begin():
                await append_audit(
                    session,
                    _payload(
                        action="user.promote",
                        target_id=f"u{index + 1}",
                        request_id=f"req-{index + 1}",
                    ),
                    clock=_fixed_clock(NOW + timedelta(seconds=index)),
                )
        await engine.dispose()

    asyncio.run(run())
    return db_url


def _mutate(db_url: str, statement) -> None:
    """绕开 ORM 语义直接改库（模拟持库写权限的篡改者）。"""
    engine = create_engine(db_url)
    try:

        async def run() -> None:
            async with engine.begin() as conn:
                await conn.execute(statement)

        asyncio.run(run())
    finally:
        asyncio.run(engine.dispose())


def _fetch(db_url: str, model, *conditions) -> list:
    # engine 必须在同一个 event loop 内 dispose（M10-10，同 legacy 治理测试）。
    async def run():
        engine = create_engine(db_url)
        try:
            sessions = make_sessionmaker(engine)
            async with sessions() as session:
                rows = (
                    (await session.execute(select(model).where(*conditions)))
                    .scalars()
                    .all()
                )
                for row in rows:
                    session.expunge(row)
                return rows
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _problems_of(db_url: str) -> list[str]:
    return asyncio.run(verify_audit_chain(db_url))["problems"]


# --- 1. 哈希确定性与字段敏感性 -----------------------------------------------


def test_canonical_json_is_key_order_independent_and_utc_stable() -> None:
    left = {"b": 1, "a": {"y": [1, 2], "x": None}}
    right = {"a": {"x": None, "y": [1, 2]}, "b": 1}
    assert canonical_json_bytes(left) == canonical_json_bytes(right)

    # 同一时刻的 naive(UTC) 与 aware(UTC) 序列化一致：SQLite/PG 读回统一
    naive = NOW.replace(tzinfo=None)
    assert canonical_datetime(naive) == canonical_datetime(NOW)
    assert canonical_datetime(NOW) == "2026-09-04T08:00:00.123456+00:00"
    # 恒定微秒位：microsecond=0 不产生更短形态（往返哈希一致的前提）
    assert canonical_datetime(NOW.replace(microsecond=0)).endswith(".000000+00:00")
    # 非 UTC 时区归一到 UTC（同一时刻哈希不受读回 tz 影响）
    tokyo = NOW.astimezone(timezone(timedelta(hours=9)))
    assert canonical_datetime(tokyo) == canonical_datetime(NOW)


def test_compute_entry_hash_is_deterministic_and_field_sensitive() -> None:
    values = audit_hash_values(_hash_row())
    baseline = compute_entry_hash(GENESIS_PREVIOUS_HASH, 1, values)
    assert baseline == compute_entry_hash(GENESIS_PREVIOUS_HASH, 1, values)
    assert len(baseline) == 64

    # previous_hash / sequence / 每个稳定字段任一变化 -> hash 变化
    other_prev = "f" * 64
    assert compute_entry_hash(other_prev, 1, values) != baseline
    assert compute_entry_hash(GENESIS_PREVIOUS_HASH, 2, values) != baseline
    for field in (
        "id", "actor_id", "actor_username", "action", "target_type",
        "target_id", "before", "after", "request_id", "created_at",
    ):
        mutated = dict(values)
        mutated[field] = f"tampered-{field}" if not isinstance(
            mutated[field], dict
        ) else {"k": "v"}
        assert compute_entry_hash(
            GENESIS_PREVIOUS_HASH, 1, mutated
        ) != baseline, f"hash 对 {field} 不敏感"

    # canonical key 顺序无关：字段顺序不同的 dict 哈希相同
    reordered = {k: values[k] for k in reversed(list(values))}
    assert compute_entry_hash(GENESIS_PREVIOUS_HASH, 1, reordered) == baseline

    with pytest.raises(ValueError):
        compute_entry_hash("not-hex", 1, values)
    with pytest.raises(ValueError):
        compute_entry_hash("", 1, values)


def _hash_row():
    """哈希字段提取接受 ORM 对象与 Mapping 两种形态的共用样例。"""
    class _Row:
        id = 7
        actor_id = "u_admin"
        actor_username = "admin"
        action = "user.promote"
        target_type = "user"
        target_id = "u1"
        before: ClassVar[dict] = {"role": "learner"}
        after: ClassVar[dict] = {"role": "admin"}
        request_id = "req-1"
        created_at = NOW

    return _Row()


def test_audit_hash_values_accepts_mapping_and_object() -> None:
    class Row(_hash_row().__class__):
        pass

    obj_values = audit_hash_values(Row())
    mapping = {
        "id": 7, "actor_id": "u_admin", "actor_username": "admin",
        "action": "user.promote", "target_type": "user", "target_id": "u1",
        "before": {"role": "learner"}, "after": {"role": "admin"},
        "request_id": "req-1", "created_at": NOW,
    }
    assert obj_values == audit_hash_values(mapping)
    assert obj_values["created_at"] == canonical_datetime(NOW)


# --- 2. append_audit：成链 / 初始化 / 回滚 / 输入门禁 ------------------------


def test_append_audit_chains_entries_and_advances_state(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=2)

    entries = _fetch(db_url, AuditChainEntryRow)
    assert [e.sequence for e in entries] == [1, 2]
    assert entries[0].previous_hash == GENESIS_PREVIOUS_HASH
    assert entries[1].previous_hash == entries[0].entry_hash
    assert all(e.algorithm == AUDIT_CHAIN_ALGORITHM for e in entries)

    audits = {a.id: a for a in _fetch(db_url, AuditLogRow)}
    assert {e.audit_id for e in entries} == set(audits)

    state = _fetch(db_url, AuditChainStateRow)
    assert len(state) == 1
    assert state[0].last_sequence == 2
    assert state[0].last_hash == entries[1].entry_hash
    assert state[0].algorithm == AUDIT_CHAIN_ALGORITHM

    # entry_hash 可用 audit 行稳定字段独立重算（与写入时同一规则）
    for entry in entries:
        audit = audits[entry.audit_id]
        assert compute_entry_hash(
            entry.previous_hash, entry.sequence, audit_hash_values(audit)
        ) == entry.entry_hash

    assert asyncio.run(verify_audit_chain(db_url))["valid"] is True


def test_append_audit_rolls_back_atomically(tmp_path) -> None:
    """审计事务失败：audit 行、entry、state 初始化全部回滚，链不残留。"""
    db_url = f"sqlite+aiosqlite:///{(tmp_path / 'rollback.db').as_posix()}"

    async def run() -> None:
        engine = create_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = make_sessionmaker(engine)

        async with sessions() as session, session.begin():
            await append_audit(session, _payload(), clock=_fixed_clock())
            raise RuntimeError("business step failed after audit")
        await engine.dispose()

    with pytest.raises(RuntimeError, match="business step failed"):
        asyncio.run(run())

    assert _fetch(db_url, AuditLogRow) == []
    assert _fetch(db_url, AuditChainEntryRow) == []
    assert _fetch(db_url, AuditChainStateRow) == []

    # 回滚后全新事务：sequence 仍从 1 起、previous 仍是 genesis
    async def append_again() -> None:
        engine = create_engine(db_url)
        sessions = make_sessionmaker(engine)
        async with sessions() as session, session.begin():
            await append_audit(session, _payload(), clock=_fixed_clock())
        await engine.dispose()

    asyncio.run(append_again())
    entries = _fetch(db_url, AuditChainEntryRow)
    assert [e.sequence for e in entries] == [1]
    assert entries[0].previous_hash == GENESIS_PREVIOUS_HASH


def test_append_audit_validates_payload_and_rejects_secrets(tmp_path) -> None:
    db_url = _make_db(tmp_path)

    async def append(payload) -> AuditLogRow:
        engine = create_engine(db_url)
        sessions = make_sessionmaker(engine)
        try:
            async with sessions() as session, session.begin():
                row = await append_audit(session, payload, clock=_fixed_clock())
            return row
        finally:
            await engine.dispose()

    with pytest.raises(ValueError, match="缺字段"):
        asyncio.run(
            append({"action": "x", "target_type": "y", "target_id": "z"})
        )
    with pytest.raises(ValueError, match="敏感键"):
        asyncio.run(
            append(_payload(before={"password_hash": "h"}))
        )
    with pytest.raises(ValueError, match="敏感键"):
        asyncio.run(
            append(_payload(after={"nested": [{"api_key": "k"}]}))
        )
    with pytest.raises(ValueError, match="敏感键"):
        asyncio.run(
            append(_payload(after={"meta": {"access-token": "t"}}))
        )
    # 所有非法 payload 均被拒绝：库内零审计残留（fail-closed）
    assert len(_fetch(db_url, AuditLogRow)) == 0


# --- 3. verify：篡改识别矩阵 --------------------------------------------------


def test_verify_valid_chain_and_report_shape(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=3)
    report = asyncio.run(verify_audit_chain(db_url))
    assert report["valid"] is True
    assert report["entries"] == 3
    assert report["audit_rows"] == 3
    assert report["algorithm"] == AUDIT_CHAIN_ALGORITHM
    assert report["problems"] == []
    assert "VALID" in format_verify_summary(report)


def test_verify_detects_tampered_audit_action(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=2)
    _mutate(db_url, update(AuditLogRow).values(action="role.demote"))
    problems = _problems_of(db_url)
    assert any("entry_hash 与 audit 行内容不匹配" in p for p in problems)


def test_verify_detects_tampered_audit_before(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=2)
    _mutate(
        db_url,
        update(AuditLogRow).values(before={"role": "attacker-forged"}),
    )
    problems = _problems_of(db_url)
    assert any("entry_hash 与 audit 行内容不匹配" in p for p in problems)


def test_verify_detects_tampered_entry_hash(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=2)
    _mutate(
        db_url,
        update(AuditChainEntryRow)
        .where(AuditChainEntryRow.sequence == 2)
        .values(entry_hash="a" * 64),
    )
    problems = _problems_of(db_url)
    assert any("entry_hash 与 audit 行内容不匹配" in p for p in problems)
    assert any("state.last_hash" in p for p in problems)


def test_verify_detects_tampered_previous_hash(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=2)
    _mutate(
        db_url,
        update(AuditChainEntryRow)
        .where(AuditChainEntryRow.sequence == 2)
        .values(previous_hash="b" * 64),
    )
    problems = _problems_of(db_url)
    assert any("previous_hash 链接断裂" in p for p in problems)


def test_verify_detects_deleted_entry_and_missing_audit(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=3)
    _mutate(
        db_url,
        delete(AuditChainEntryRow).where(AuditChainEntryRow.sequence == 2),
    )
    problems = _problems_of(db_url)
    assert any("无链 entry" in p for p in problems)  # audit 行仍在，entry 消失
    assert any("sequence 断裂" in p for p in problems)
    assert any("state" in p for p in problems)  # head 与缩短的链不一致


def test_verify_detects_deleted_audit_row(tmp_path) -> None:
    """SQLite 默认不强制 FK：直删 audit 行模拟绕过 RESTRICT 的破坏者。"""
    db_url = _make_db(tmp_path, rows=2)
    _mutate(db_url, delete(AuditLogRow).where(AuditLogRow.id == 1))
    problems = _problems_of(db_url)
    assert any("无对应 audit_log 行" in p for p in problems)


def test_verify_detects_sequence_gap(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=3)
    _mutate(
        db_url,
        update(AuditChainEntryRow)
        .where(AuditChainEntryRow.sequence == 3)
        .values(sequence=5),
    )
    problems = _problems_of(db_url)
    assert any("sequence 断裂" in p for p in problems)


def test_verify_detects_head_drift(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=2)
    _mutate(
        db_url,
        update(AuditChainStateRow)
        .values(last_hash="c" * 64),
    )
    problems = _problems_of(db_url)
    assert any("state.last_hash 与链尾" in p for p in problems)


def test_verify_detects_state_last_sequence_drift(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=2)
    _mutate(
        db_url,
        update(AuditChainStateRow).values(last_sequence=9),
    )
    problems = _problems_of(db_url)
    assert any("state.last_sequence=9" in p for p in problems)


def test_verify_detects_algorithm_drift(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=2)
    _mutate(
        db_url,
        update(AuditChainEntryRow)
        .where(AuditChainEntryRow.sequence == 1)
        .values(algorithm="md5"),
    )
    problems = _problems_of(db_url)
    assert any("algorithm 非 sha256" in p for p in problems)


def test_verify_reports_missing_state_row(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=2)
    _mutate(db_url, delete(AuditChainStateRow))
    problems = _problems_of(db_url)
    assert any("audit_chain_state 无行" in p for p in problems)


def test_verify_reports_missing_tables(tmp_path) -> None:
    """未执行 0027 的库（无链表）：报告明确缺表，而非 traceback。"""
    db_url = f"sqlite+aiosqlite:///{(tmp_path / 'bare.db').as_posix()}"

    async def run() -> None:
        engine = create_engine(db_url)
        audit_table = Base.metadata.tables["audit_log"]
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(
                    sync_conn, tables=[audit_table]
                )
            )
        await engine.dispose()

    asyncio.run(run())
    report = asyncio.run(verify_audit_chain(db_url))
    assert report["valid"] is False
    assert any("表缺失" in p and "audit_chain" in p for p in report["problems"])


def test_verify_accepts_genesis_state_on_empty_log(tmp_path) -> None:
    """0027 对空库的结果：0 entry + state(0, genesis) 是合法链。"""
    db_url = f"sqlite+aiosqlite:///{(tmp_path / 'empty.db').as_posix()}"

    async def run() -> None:
        engine = create_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with engine.begin() as conn:
            await conn.execute(
                insert(AuditChainStateRow).values(
                    id=1,
                    last_sequence=0,
                    last_hash=GENESIS_PREVIOUS_HASH,
                    algorithm=AUDIT_CHAIN_ALGORITHM,
                    updated_at=NOW,
                )
            )
        await engine.dispose()

    asyncio.run(run())
    report = asyncio.run(verify_audit_chain(db_url))
    assert report["valid"] is True, report["problems"]
    assert report["entries"] == 0


def test_verify_problems_never_leak_payload_content(tmp_path) -> None:
    """problems 文本只含 id/sequence/原因，不携带 before/after 正文。"""
    marker = "TOP-SECRET-PAYLOAD-MARKER-9f3a"
    db_url = _make_db(tmp_path, rows=2)
    _mutate(db_url, update(AuditLogRow).values(after={"note": marker}))
    report = asyncio.run(verify_audit_chain(db_url))
    assert report["valid"] is False
    dumped = json.dumps(report, ensure_ascii=False, default=str)
    assert marker not in dumped
    assert marker not in format_verify_summary(report)


# --- 4. 静态守卫：生产代码不得绕链 -------------------------------------------


def test_no_production_code_constructs_audit_log_row_directly() -> None:
    """app/ 生产代码只能经 append_audit 构造 AuditLogRow（唯一写入入口）。"""
    allowed = {
        Path("app/domain/audit_chain.py"),  # append_audit 实现自身
        Path("app/db/orm.py"),  # ORM 定义（class AuditLogRow(Base)）
    }
    violations: list[str] = []
    for path in (_API_DIR / "app").rglob("*.py"):
        rel = path.relative_to(_API_DIR)
        if rel in allowed or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "AuditLogRow(" in text:
            violations.append(str(rel))
        if "from app.repositories.audit import audit_insert_values" in text:
            violations.append(f"{rel} (legacy audit_insert_values import)")
    assert violations == [], f"绕过哈希链的审计写入点: {violations}"


# --- 5. CLI：注册 / exit code / --json ---------------------------------------


def _verify_args(db_url=None, *, as_json=False):
    return SimpleNamespace(db_url=db_url, as_json=as_json)


def test_cli_audit_chain_verify_exit_codes(tmp_path, capsys) -> None:
    # 缺 db-url：参数错误 exit 2
    assert cli_module._run_audit_chain_verify(_verify_args()) == 2
    assert "fail-closed" in capsys.readouterr().out

    # 连接失败：exit 2
    assert (
        cli_module._run_audit_chain_verify(
            _verify_args("sqlite+aiosqlite:///C:/definitely-missing-dir/x.db")
        )
        == 2
    )

    # 合法链：exit 0
    ok_url = _make_db(tmp_path, rows=2)
    assert cli_module._run_audit_chain_verify(_verify_args(ok_url)) == 0
    assert "VALID" in capsys.readouterr().out

    # 篡改链：exit 1
    _mutate(ok_url, update(AuditLogRow).values(action="role.demote"))
    assert cli_module._run_audit_chain_verify(_verify_args(ok_url)) == 1
    assert "INVALID" in capsys.readouterr().out


def test_cli_audit_chain_verify_json_output(tmp_path, capsys) -> None:
    db_url = _make_db(tmp_path, rows=1)
    _mutate(db_url, update(AuditChainStateRow).values(last_sequence=7))
    assert (
        cli_module._run_audit_chain_verify(_verify_args(db_url, as_json=True))
        == 1
    )
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is False
    assert report["entries"] == 1
    assert any("state.last_sequence=7" in p for p in report["problems"])


def test_main_dispatches_audit_chain_verify(tmp_path, monkeypatch, capsys) -> None:
    db_url = _make_db(tmp_path, rows=1)
    monkeypatch.setattr(
        sys, "argv", ["cli", "audit-chain-verify", "--db-url", db_url]
    )
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
    assert "VALID" in capsys.readouterr().out


# --- 6. 0027 迁移：存量建链与 downgrade --------------------------------------


def _alembic_config(database_url: str) -> Config:
    config = Config(str(_API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(_API_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_migration_0027_backfills_and_downgrades(tmp_path) -> None:
    db_path = tmp_path / "migrate_chain.db"
    db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    config = _alembic_config(db_url)

    # 先停在 0026，手工放入既有审计（模拟生产存量，id 由库分配 1..3）
    command.upgrade(config, "0026_paper_owner")
    with sqlite3.connect(db_path) as conn:
        for index in range(3):
            conn.execute(
                "INSERT INTO audit_log (actor_id, actor_username, action,"
                " target_type, target_id, before, after, request_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "u_admin", "admin", "user.promote", "user", f"u{index}",
                    None, '{"role": "admin"}', f"req-{index}",
                    "2026-09-01 00:00:00.000000",
                ),
            )

    command.upgrade(config, "head")

    with sqlite3.connect(db_path) as conn:
        entries = conn.execute(
            "SELECT audit_id, sequence, previous_hash, entry_hash, algorithm"
            " FROM audit_chain_entries ORDER BY sequence"
        ).fetchall()
        state = conn.execute(
            "SELECT last_sequence, last_hash, algorithm FROM audit_chain_state"
        ).fetchall()

    assert [row[1] for row in entries] == [1, 2, 3]
    assert entries[0][2] == GENESIS_PREVIOUS_HASH
    assert entries[1][2] == entries[0][3]
    assert entries[2][2] == entries[1][3]
    assert state == [(3, entries[2][3], AUDIT_CHAIN_ALGORITHM)]

    # 迁移建链后：应用层校验器全量重算通过（两套规则同源的实证）
    assert asyncio.run(verify_audit_chain(db_url))["valid"] is True

    # downgrade 只删两张新表，audit_log 数据不动
    command.downgrade(config, "0026_paper_owner")
    with sqlite3.connect(db_path) as conn:
        tables = {
            name
            for (name,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        audit_count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert "audit_chain_entries" not in tables
    assert "audit_chain_state" not in tables
    assert audit_count == 3


def test_migration_0027_empty_database_leaves_genesis_state(tmp_path) -> None:
    db_path = tmp_path / "migrate_empty.db"
    db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    command.upgrade(_alembic_config(db_url), "head")
    report = asyncio.run(verify_audit_chain(db_url))
    assert report["valid"] is True, report["problems"]
    assert report["entries"] == 0
