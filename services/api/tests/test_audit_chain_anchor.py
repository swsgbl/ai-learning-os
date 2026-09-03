"""M10-06 审计链库外锚定：锚文件成链、DB 交叉核对、文件安全与 CLI。

覆盖矩阵：
1. 锚行构造：anchor_hash canonical 重算确定；字段集固定无敏感字段；
2. 主流程：valid initial anchor（含空库 genesis 锚）、追加 audit 后第二锚
   （锚链链接）、same head up-to-date 不重复追加、dry-run / 缺 --yes
   不落盘、--verify-only 三链校验；
3. 拒绝矩阵：DB 链 invalid、锚文件 tamper（字段篡改/anchor_hash 重算
   伪造/previous 链断/重复锚点/字段集增删/schema/algorithm 变体）、
   partial line、无效 UTF-8、空行、非 JSON、DB 整链重算（verify valid
   但锚点 hash 全变）、DB sequence 回退；
4. 文件安全：symlink（含 dangling）/目录/父目录缺失拒绝 exit 2、写入
   中途失败不留半行（回截恢复）、新建 0600（POSIX）；
4b. 打开/创建语义与持久化（返工）：既有文件不带 O_CREAT 打开、不存在
   才 O_CREAT|O_EXCL 排他新建——并发窗口被抢先创建则 FileExistsError
   失败且不覆盖对方内容；POSIX O_NOFOLLOW 可用时 symlink 在 open 处
   拒绝（无该旗标的平台用注入常量 + mock os.open 测能力分支并注明）；
   排他新建后 symlink 复核（Windows CRT O_EXCL 跟随链接）；新建成功
   后 fsync 父目录（仅新建调用；EINVAL=文件系统能力跳过、其余 OSError
   上抛、无目录 fd 能力平台跳过）；失败场景一律不污染锚文件；
4c. 失败回截保护区（Codex 审核返工）：写入/文件 fsync/新建后的父目录
   fsync 任一失败都回截——新建文件父目录 fsync EIO 后锚行不在盘上
   （重跑不误判 up-to-date）、既有文件 fsync EIO 回原字节、回截本身
   失败仍上抛原始错误不虚构成功（均注入模拟故障，不依赖真实磁盘错误）；
5. 并发边界：verify 与交叉核对共用单连接单事务快照（引擎计数=1）；
   外部快照参数（production-preflight 复用；Codex 二轮返工收敛）：不建
   引擎、verify 结论**始终从 snapshot 纯重算**（verify_report 参数已移除
   ——杜绝「报告与快照不一致」的数据来源歧义）、与 --yes 互斥、与
   db_url 互斥（同时提供=来源二义拒绝）、无 db_url 且无快照拒绝，全部
   拒绝形态零锚文件副作用；
6. CLI：exit 0/1/2、--json 结构、人类摘要、main 分发、--yes 与
   --verify-only 互斥、无 secret 泄漏（marker 字符串）。
"""
from __future__ import annotations

import asyncio
import errno
import json
import os
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, update

from app.db.base import Base
from app.db.orm import AuditChainEntryRow, AuditChainStateRow, AuditLogRow
from app.db.session import create_engine, make_sessionmaker
from app.domain.audit_chain import (
    GENESIS_PREVIOUS_HASH,
    append_audit,
)
from app.ops import audit_chain_anchor as anchor_module
from app.ops import cli as cli_module
from app.ops.audit_chain_anchor import (
    ANCHOR_ALGORITHM,
    ANCHOR_SCHEMA_VERSION,
    AnchorInputError,
    append_anchor_line,
    build_anchor,
    compute_anchor_hash,
    format_anchor_summary,
    run_anchor,
)
from app.ops.audit_chain_verify import (
    load_chain_snapshot,
    verify_audit_chain,
    verify_chain_snapshot,
)

NOW = datetime(2026, 9, 4, 8, 0, 0, 123456, tzinfo=UTC)
LATER = NOW + timedelta(minutes=10)


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


def _make_db(tmp_path, *, rows: int = 0, name: str = "anchor.db") -> str:
    """create_all 建库；rows>0 时追加 rows 条链化审计（sequence 1..rows）。"""
    db_url = f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}"

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
                        target_id=f"u{index + 1}",
                        request_id=f"req-{index + 1}",
                    ),
                    clock=_fixed_clock(NOW + timedelta(seconds=index)),
                )
        await engine.dispose()

    asyncio.run(run())
    return db_url


def _make_genesis_db(tmp_path, *, name: str = "genesis.db") -> str:
    """空库 + 手工 genesis state（0027 对空库的结果形态）。"""
    from sqlalchemy import insert

    db_url = f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}"

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
                    algorithm="sha256",
                    updated_at=NOW,
                )
            )
        await engine.dispose()

    asyncio.run(run())
    return db_url


def _append_rows(db_url: str, count: int, *, base_second: int = 100) -> None:
    """追加 count 条链化审计（不同 created_at -> 不同 entry_hash）。"""

    async def run() -> None:
        engine = create_engine(db_url)
        sessions = make_sessionmaker(engine)
        for index in range(count):
            async with sessions() as session, session.begin():
                await append_audit(
                    session,
                    _payload(
                        target_id=f"x{base_second + index}",
                        request_id=f"req-x{base_second + index}",
                    ),
                    clock=_fixed_clock(NOW + timedelta(seconds=base_second + index)),
                )
        await engine.dispose()

    asyncio.run(run())


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


def _db_state(db_url: str) -> tuple[int, str]:
    async def run():
        engine = create_engine(db_url)
        try:
            async with engine.connect() as conn:
                state = (
                    await conn.execute(select(AuditChainStateRow))
                ).mappings().one()
                return state["last_sequence"], state["last_hash"]
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _anchor(db_url: str, path, **kwargs) -> dict:
    kwargs.setdefault("clock", _fixed_clock(LATER))
    return asyncio.run(run_anchor(db_url, path, **kwargs))


def _snapshot_of(db_url: str) -> tuple[dict, dict]:
    """独立取一份链快照与 verify 报告（模拟只读消费方已加载的快照）。"""

    async def run():
        engine = create_engine(db_url)
        try:
            async with engine.connect() as conn:
                snapshot = await load_chain_snapshot(conn)
        finally:
            await engine.dispose()
        return snapshot, verify_chain_snapshot(snapshot)

    return asyncio.run(run())


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_anchors(path: Path, anchors: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(a, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n"
            for a in anchors
        ),
        encoding="utf-8",
    )


def _cli(db_url, anchor_file, *, yes=False, verify_only=False, as_json=False) -> int:
    return cli_module._run_audit_chain_anchor(
        SimpleNamespace(
            db_url=db_url,
            anchor_file=str(anchor_file),
            yes=yes,
            verify_only=verify_only,
            as_json=as_json,
        )
    )


# --- 1. 锚行构造 ------------------------------------------------------------


def test_anchor_hash_is_deterministic_and_field_sensitive() -> None:
    anchor = build_anchor(
        sequence=3,
        head_hash="ab" * 32,
        previous_anchor_hash=GENESIS_PREVIOUS_HASH,
        clock=_fixed_clock(),
    )
    assert sorted(anchor) == [
        "algorithm",
        "anchor_hash",
        "anchored_at",
        "head_hash",
        "previous_anchor_hash",
        "schema_version",
        "sequence",
    ]
    assert anchor["schema_version"] == ANCHOR_SCHEMA_VERSION
    assert anchor["algorithm"] == ANCHOR_ALGORITHM
    assert anchor["anchored_at"] == "2026-09-04T08:00:00.123456+00:00"
    assert anchor["anchor_hash"] == compute_anchor_hash(anchor)
    assert len(anchor["anchor_hash"]) == 64

    # 任一参与字段变化 -> anchor_hash 变化
    for field, value in (
        ("sequence", 4),
        ("head_hash", "cd" * 32),
        ("previous_anchor_hash", "ef" * 32),
        ("anchored_at", "2026-09-04T09:00:00.000000+00:00"),
    ):
        mutated = dict(anchor)
        mutated[field] = value
        assert compute_anchor_hash(mutated) != anchor["anchor_hash"], field

    with pytest.raises(ValueError):
        build_anchor(
            sequence=-1,
            head_hash="ab" * 32,
            previous_anchor_hash=GENESIS_PREVIOUS_HASH,
            clock=_fixed_clock(),
        )
    with pytest.raises(ValueError):
        build_anchor(
            sequence=1,
            head_hash="not-hex",
            previous_anchor_hash=GENESIS_PREVIOUS_HASH,
            clock=_fixed_clock(),
        )


# --- 2. 主流程：initial anchor / 第二锚 / up-to-date / dry-run / verify-only


def test_initial_anchor_writes_one_valid_line(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=3)
    head_sequence, head_hash = _db_state(db_url)
    anchor_path = tmp_path / "anchor.jsonl"

    # dry-run：只报计划，不落盘
    dry = _anchor(db_url, anchor_path)
    assert dry["status"] == "dry-run"
    assert dry["valid"] is True
    assert dry["written"] is False
    assert not anchor_path.exists()
    assert dry["proposed_anchor"]["sequence"] == head_sequence
    assert dry["db"]["head_hash"] == head_hash

    # --yes 落盘一行：canonical JSON + 换行，anchor_hash 可独立重算
    done = _anchor(db_url, anchor_path, execute=True)
    assert done["status"] == "anchored"
    assert done["written"] is True
    raw = anchor_path.read_bytes()
    assert raw.endswith(b"\n")
    (line,) = _lines(anchor_path)
    assert line["sequence"] == head_sequence == 3
    assert line["head_hash"] == head_hash
    assert line["previous_anchor_hash"] == GENESIS_PREVIOUS_HASH
    assert compute_anchor_hash(line) == line["anchor_hash"]
    assert line == done["proposed_anchor"]

    # 锚行不含敏感字段：只有七个固定字段
    assert set(line) == {
        "algorithm", "anchor_hash", "anchored_at", "head_hash",
        "previous_anchor_hash", "schema_version", "sequence",
    }


def test_second_anchor_chains_after_new_audits(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=3)
    anchor_path = tmp_path / "anchor.jsonl"
    assert _anchor(db_url, anchor_path, execute=True)["status"] == "anchored"

    _append_rows(db_url, 2)  # sequence 4、5
    head_sequence, head_hash = _db_state(db_url)

    report = _anchor(db_url, anchor_path, execute=True)
    assert report["status"] == "anchored"
    lines = _lines(anchor_path)
    assert len(lines) == 2
    assert lines[1]["sequence"] == head_sequence == 5
    assert lines[1]["head_hash"] == head_hash
    assert lines[1]["previous_anchor_hash"] == lines[0]["anchor_hash"]
    assert compute_anchor_hash(lines[1]) == lines[1]["anchor_hash"]
    assert report["anchor_file"]["anchors"] == 2


def test_same_head_is_up_to_date_and_appends_nothing(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=2)
    anchor_path = tmp_path / "anchor.jsonl"
    _anchor(db_url, anchor_path, execute=True)
    before = anchor_path.read_bytes()

    for execute in (False, True):
        report = _anchor(db_url, anchor_path, execute=execute)
        assert report["status"] == "up-to-date"
        assert report["written"] is False
        assert report["proposed_anchor"] is None
        assert anchor_path.read_bytes() == before  # 不重复追加


def test_genesis_anchor_for_empty_database(tmp_path) -> None:
    """0027 后立即锚定空库：sequence=0 + genesis head 也是合法初始锚。"""
    db_url = _make_genesis_db(tmp_path)
    anchor_path = tmp_path / "anchor.jsonl"

    report = _anchor(db_url, anchor_path, execute=True)
    assert report["status"] == "anchored"
    (line,) = _lines(anchor_path)
    assert line["sequence"] == 0
    assert line["head_hash"] == GENESIS_PREVIOUS_HASH

    again = _anchor(db_url, anchor_path, execute=True)
    assert again["status"] == "up-to-date"


def test_verify_only_validates_all_three_chains(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=3)
    anchor_path = tmp_path / "anchor.jsonl"

    # 锚文件尚不存在、head 前进：三链自洽 -> valid（提示可锚定），不落盘
    report = _anchor(db_url, anchor_path, verify_only=True)
    assert report["status"] == "valid"
    assert report["written"] is False
    assert not anchor_path.exists()
    assert "VALID" in format_anchor_summary(report)
    assert "可执行锚定" in format_anchor_summary(report)

    # 已锚定 + 无新审计：verify-only up-to-date
    _anchor(db_url, anchor_path, execute=True)
    report = _anchor(db_url, anchor_path, verify_only=True)
    assert report["status"] == "up-to-date"
    assert len(_lines(anchor_path)) == 1


def test_verify_only_and_yes_are_mutually_exclusive(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=1)
    with pytest.raises(AnchorInputError):
        _anchor(db_url, tmp_path / "a.jsonl", execute=True, verify_only=True)


# --- 3. 拒绝矩阵 -------------------------------------------------------------


def test_rejects_when_db_chain_invalid(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=2)
    _mutate(db_url, update(AuditLogRow).values(action="role.demote"))
    anchor_path = tmp_path / "anchor.jsonl"

    report = _anchor(db_url, anchor_path, execute=True)
    assert report["status"] == "invalid"
    assert any("entry_hash 与 audit 行内容不匹配" in p for p in report["problems"])
    assert not anchor_path.exists()  # 拒绝时不新建锚文件


def test_rejects_whole_chain_recompute_even_though_db_verify_valid(tmp_path) -> None:
    """核心价值测试：整链重算后 DB 自洽（verify valid），但锚点对不上 -> 拒绝。"""
    db_url = _make_db(tmp_path, rows=3)
    anchor_path = tmp_path / "anchor.jsonl"
    _anchor(db_url, anchor_path, execute=True)

    # 持库写权限者删光审计与链数据，用不同时间整链重写（自洽但 hash 全变）
    _mutate(db_url, delete(AuditChainEntryRow))
    _mutate(db_url, delete(AuditLogRow))
    _mutate(db_url, delete(AuditChainStateRow))
    _append_rows(db_url, 3, base_second=900)

    assert asyncio.run(verify_audit_chain(db_url))["valid"] is True
    before = anchor_path.read_bytes()
    report = _anchor(db_url, anchor_path, execute=True)
    assert report["status"] == "invalid"
    assert any(
        "head_hash 与当前 DB entry_hash 不匹配" in p for p in report["problems"]
    )
    assert anchor_path.read_bytes() == before  # 拒绝不追加


def test_rejects_db_sequence_rollback(tmp_path) -> None:
    """DB 回退到 sequence 2（自洽）：锚点 sequence=3 不在链中 -> 拒绝。"""
    db_url = _make_db(tmp_path, rows=3)
    anchor_path = tmp_path / "anchor.jsonl"
    _anchor(db_url, anchor_path, execute=True)

    async def rollback() -> None:
        engine = create_engine(db_url)
        try:
            async with engine.begin() as conn:
                entry3 = (
                    await conn.execute(
                        select(AuditChainEntryRow).where(
                            AuditChainEntryRow.sequence == 3
                        )
                    )
                ).mappings().one()
                hash2 = (
                    await conn.execute(
                        select(AuditChainEntryRow.entry_hash).where(
                            AuditChainEntryRow.sequence == 2
                        )
                    )
                ).scalar_one()
                await conn.execute(
                    delete(AuditChainEntryRow).where(AuditChainEntryRow.sequence == 3)
                )
                await conn.execute(
                    delete(AuditLogRow).where(AuditLogRow.id == entry3["audit_id"])
                )
                await conn.execute(
                    update(AuditChainStateRow).values(last_sequence=2, last_hash=hash2)
                )
        finally:
            await engine.dispose()

    asyncio.run(rollback())
    assert asyncio.run(verify_audit_chain(db_url))["valid"] is True

    report = _anchor(db_url, anchor_path, execute=True)
    assert report["status"] == "invalid"
    assert any("在当前 DB 链中不存在" in p for p in report["problems"])


def _forged_anchor(sequence: int, head_hash: str, previous: str) -> dict:
    """内部 hash 自洽的伪造锚行（能过锚链自校验，靠交叉核对拦截）。"""
    return build_anchor(
        sequence=sequence,
        head_hash=head_hash,
        previous_anchor_hash=previous,
        clock=_fixed_clock(),
    )


def test_rejects_forged_anchor_with_valid_internal_hash(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=2)
    anchor_path = tmp_path / "anchor.jsonl"
    _write_anchors(
        anchor_path,
        [_forged_anchor(2, "ee" * 32, GENESIS_PREVIOUS_HASH)],
    )
    report = _anchor(db_url, anchor_path, execute=True)
    assert report["status"] == "invalid"
    assert any(
        "head_hash 与当前 DB entry_hash 不匹配" in p for p in report["problems"]
    )


def test_rejects_tampered_anchor_fields(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=2)
    _, head_hash = _db_state(db_url)
    base = build_anchor(
        sequence=2,
        head_hash=head_hash,
        previous_anchor_hash=GENESIS_PREVIOUS_HASH,
        clock=_fixed_clock(),
    )

    def check(mutator, expected_problem: str) -> None:
        anchor_path = tmp_path / "anchor.jsonl"
        _write_anchors(anchor_path, [mutator(dict(base))])
        report = _anchor(db_url, anchor_path)
        assert report["status"] == "invalid", report
        assert any(expected_problem in p for p in report["problems"])

    # 篡改 head_hash 但不重算 anchor_hash
    check(lambda a: a | {"head_hash": "ff" * 32}, "anchor_hash 与字段重算不匹配")
    # 篡改 anchor_hash 本身
    check(lambda a: a | {"anchor_hash": "0" * 64}, "anchor_hash 与字段重算不匹配")
    # 多字段
    check(lambda a: a | {"note": "x"}, "字段集不符")
    # 少字段
    check(lambda a: {k: v for k, v in a.items() if k != "anchored_at"}, "字段集不符")
    # schema/algorithm 变体（anchor_hash 随之重算，仍拒绝）
    check(
        lambda a: _recompute(a | {"schema_version": 2}), "schema_version 非 1"
    )
    check(lambda a: _recompute(a | {"algorithm": "md5"}), "algorithm 非 sha256")
    # anchored_at 非法
    check(lambda a: _recompute(a | {"anchored_at": "not-a-time"}), "anchored_at")


def _recompute(anchor: dict) -> dict:
    anchor = dict(anchor)
    anchor["anchor_hash"] = compute_anchor_hash(anchor)
    return anchor


def test_rejects_broken_anchor_chain_link_and_duplicate(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=3)
    _, head3 = _db_state(db_url)
    clock = _fixed_clock()
    first = build_anchor(
        sequence=2,
        head_hash=head3,
        previous_anchor_hash=GENESIS_PREVIOUS_HASH,
        clock=clock,
    )
    second = build_anchor(
        sequence=3,
        head_hash=head3,
        previous_anchor_hash=first["anchor_hash"],
        clock=clock,
    )

    # 第二行 previous_anchor_hash 断链
    path = tmp_path / "a1.jsonl"
    _write_anchors(
        path,
        [first, _recompute(second | {"previous_anchor_hash": "99" * 32})],
    )
    report = _anchor(db_url, path)
    assert any("不链接" in p for p in report["problems"])

    # 重复锚点（sequence 不严格递增）
    path = tmp_path / "a2.jsonl"
    _write_anchors(path, [first, dict(first)])
    report = _anchor(db_url, path)
    assert any("未严格递增" in p for p in report["problems"])

    # sequence=0 锚点 head 非 genesis 常量
    path = tmp_path / "a3.jsonl"
    _write_anchors(
        path, [_recompute(first | {"sequence": 0, "head_hash": "77" * 32})]
    )
    report = _anchor(db_url, path)
    assert any("sequence=0 的 head_hash 非 genesis" in p for p in report["problems"])


def test_rejects_malformed_anchor_file_bytes(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=1)
    _, head = _db_state(db_url)
    good = build_anchor(
        sequence=1,
        head_hash=head,
        previous_anchor_hash=GENESIS_PREVIOUS_HASH,
        clock=_fixed_clock(),
    )
    good_line = json.dumps(good, sort_keys=True, separators=(",", ":")) + "\n"

    cases = {
        "partial-line": good_line.encode()[:-8],  # 截尾（无换行结尾）
        "no-trailing-newline": good_line.strip().encode(),
        "invalid-utf8": b"\xff\xfe" + good_line.encode(),
        "empty-line": (good_line + "\n").encode(),
        "not-json": (good_line + "{oops}\n").encode(),
        "not-object": (good_line + "[1,2]\n").encode(),
    }
    for name, data in cases.items():
        path = tmp_path / f"{name}.jsonl"
        path.write_bytes(data)
        report = _anchor(db_url, path)
        assert report["status"] == "invalid", name
        assert report["problems"], name

    # 空文件是合法初始状态（尚未锚定）
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    assert _anchor(db_url, empty)["status"] == "dry-run"


def test_truncated_whole_last_line_semantics(tmp_path) -> None:
    """锚文件被截掉整行（自洽剩余部分）：接受并允许重新锚定当前 head。

    如实边界：整行截断从纯锚文件角度自洽，无法与「从未锚定过」区分——
    权威见证是 WORM/离线副本，本机文件只是操作见证（docs 同步说明）。
    DB 侧重算/回退仍被交叉核对拦截（见上方两个拒绝测试）。
    """
    db_url = _make_db(tmp_path, rows=3)
    _, head3 = _db_state(db_url)
    clock = _fixed_clock()
    # 用真实 DB sequence=2 hash 构造合法第一行与第二行，随后截掉整第二行
    hash2 = asyncio.run(_entry_hash_of(db_url, 2))
    first = build_anchor(
        sequence=2,
        head_hash=hash2,
        previous_anchor_hash=GENESIS_PREVIOUS_HASH,
        clock=clock,
    )
    second = build_anchor(
        sequence=3,
        head_hash=head3,
        previous_anchor_hash=first["anchor_hash"],
        clock=clock,
    )
    path = tmp_path / "trunc.jsonl"
    _write_anchors(path, [first, second])
    # 截掉最后一整行
    _write_anchors(path, [first])
    assert len(_lines(path)) == 1

    report = _anchor(db_url, path, execute=True)
    assert report["status"] == "anchored", report["problems"]
    assert [line["sequence"] for line in _lines(path)] == [2, 3]
    assert _lines(path)[1]["previous_anchor_hash"] == first["anchor_hash"]


async def _entry_hash_of(db_url: str, sequence: int) -> str:
    engine = create_engine(db_url)
    try:
        async with engine.connect() as conn:
            return (
                await conn.execute(
                    select(AuditChainEntryRow.entry_hash).where(
                        AuditChainEntryRow.sequence == sequence
                    )
                )
            ).scalar_one()
    finally:
        await engine.dispose()


# --- 4. 文件安全 -------------------------------------------------------------


def test_rejects_symlink_directory_and_missing_parent(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=1)

    # 目录
    with pytest.raises(AnchorInputError):
        _anchor(db_url, tmp_path)

    # 父目录不存在（不自动创建）
    with pytest.raises(AnchorInputError):
        _anchor(db_url, tmp_path / "no-such-dir" / "anchor.jsonl")

    # symlink（指向存在文件与 dangling 两种）：环境不支持创建则 skip
    real = tmp_path / "real.jsonl"
    real.write_text("", encoding="utf-8")
    for name, target in (
        ("link-to-file", real),
        ("dangling-link", tmp_path / "never-exists.jsonl"),
    ):
        link = tmp_path / name
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
        with pytest.raises(AnchorInputError):
            _anchor(db_url, link)


def test_append_failure_leaves_no_partial_line(tmp_path, monkeypatch) -> None:
    """写入中途失败：ftruncate 回截到原大小，锚文件不留半行。"""
    db_url = _make_db(tmp_path, rows=2)
    anchor_path = tmp_path / "anchor.jsonl"
    assert _anchor(db_url, anchor_path, execute=True)["status"] == "anchored"
    original = anchor_path.read_bytes()

    _append_rows(db_url, 1)  # head 前进，使下一次锚定确实要写行
    real_write = os.write
    calls: list[int] = []

    def flaky_write(fd, data):
        calls.append(fd)
        if len(calls) == 1:
            view = memoryview(data)
            half = len(data) // 2
            real_write(fd, view[:half])
            return half  # 先写半行
        raise OSError("simulated disk full")

    monkeypatch.setattr(anchor_module.os, "write", flaky_write)
    with pytest.raises(OSError):
        _anchor(db_url, anchor_path, execute=True)
    assert anchor_path.read_bytes() == original  # 半行被回截，文件不变


@pytest.mark.skipif(
    sys.platform.startswith("win"), reason="POSIX 权限位语义；Windows 无 0600"
)
def test_anchor_file_created_with_0600(tmp_path) -> None:
    db_url = _make_db(tmp_path, rows=1)
    anchor_path = tmp_path / "anchor.jsonl"
    _anchor(db_url, anchor_path, execute=True)
    assert stat.S_IMODE(os.stat(anchor_path).st_mode) == 0o600


# --- 4b. 打开/创建语义与持久化（返工） --------------------------------------


def test_concurrent_creation_fails_closed_without_overwriting(
    tmp_path, monkeypatch
) -> None:
    """排他创建竞态：既有文件打开报不存在后、O_EXCL 创建前，路径被另一
    操作员抢先创建 -> FileExistsError 失败退出，不覆盖、不追加对方文件。
    """
    anchor_path = tmp_path / "anchor.jsonl"
    other = b'{"taken":"by-other-operator"}\n'
    real_open = os.open

    def racing_open(p, flags, *args, **kwargs):
        if Path(p) == anchor_path and not flags & os.O_CREAT:
            # 「打开既有文件」窗口：另一操作员抢先创建了锚文件
            fd = real_open(
                anchor_path,
                os.O_WRONLY | os.O_CREAT | getattr(os, "O_BINARY", 0),
                0o600,
            )
            os.write(fd, other)
            os.close(fd)
            raise FileNotFoundError(errno.ENOENT, "模拟：检查时文件不存在", str(p))
        return real_open(p, flags, *args, **kwargs)

    monkeypatch.setattr(anchor_module.os, "open", racing_open)
    with pytest.raises(FileExistsError):
        append_anchor_line(anchor_path, b'{"we":"lost-the-race"}\n')
    # 不猜测、不覆盖：锚文件保持对方写入的内容
    assert anchor_path.read_bytes() == other


def test_existing_file_opened_without_create_flag(tmp_path, monkeypatch) -> None:
    """续写既有文件必须不带 O_CREAT：盲开 O_CREAT 会掩盖路径被替换。"""
    anchor_path = tmp_path / "anchor.jsonl"
    anchor_path.write_bytes(b"")
    seen_flags: list[int] = []
    real_open = os.open

    def spy_open(p, flags, *args, **kwargs):
        seen_flags.append(flags)
        return real_open(p, flags, *args, **kwargs)

    monkeypatch.setattr(anchor_module.os, "open", spy_open)
    append_anchor_line(anchor_path, b'{"a":1}\n')
    assert seen_flags == [anchor_module._APPEND_OPEN_FLAGS | anchor_module._O_NOFOLLOW]
    assert not seen_flags[0] & os.O_CREAT


@pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW") or sys.platform.startswith("win"),
    reason="需要 POSIX O_NOFOLLOW（内核对 symlink 返回 ELOOP）；Windows 无该旗标，"
    "另见下方注入常量 + mock 的能力分支测试",
)
def test_symlink_open_rejected_at_open_via_nofollow(tmp_path) -> None:
    """POSIX：O_NOFOLLOW 使 symlink 在 open 处被内核拒绝（ELOOP ->
    AnchorInputError），消除前置检查与打开之间的 swap TOCTOU 窗口。
    """
    real = tmp_path / "real.jsonl"
    real.write_bytes(b"unchanged\n")
    link = tmp_path / "link.jsonl"
    os.symlink(real, link)
    with pytest.raises(AnchorInputError):
        append_anchor_line(link, b'{"x":1}\n')
    assert real.read_bytes() == b"unchanged\n"  # 失败不污染目标文件


@pytest.mark.skipif(
    hasattr(os, "O_NOFOLLOW"),
    reason="平台原生支持 O_NOFOLLOW，symlink 拒绝由上方真实测试覆盖",
)
def test_nofollow_capability_branch_mocked_without_native_support(
    tmp_path, monkeypatch
) -> None:
    """无 O_NOFOLLOW 旗标的平台（Windows）无法真实触发 ELOOP：注入能力
    常量并 mock os.open 模拟 POSIX 内核对 symlink 的 ELOOP，验证旗标
    确实传给 open 且被转成 AnchorInputError、不写既有文件。真实内核
    行为由上方 POSIX 测试覆盖（mock 测试，如实注明）。
    """
    fake_nofollow = 0o400000  # 任意位值，只验证旗标传递与错误转换
    monkeypatch.setattr(anchor_module, "_O_NOFOLLOW", fake_nofollow)
    anchor_path = tmp_path / "anchor.jsonl"
    anchor_path.write_bytes(b"unchanged\n")
    seen_flags: list[int] = []

    def fake_open(p, flags, *args, **kwargs):
        seen_flags.append(flags)
        if flags & fake_nofollow:
            raise OSError(errno.ELOOP, "too many levels of symbolic links", str(p))
        raise AssertionError("打开既有文件必须携带 O_NOFOLLOW 旗标")

    monkeypatch.setattr(anchor_module.os, "open", fake_open)
    with pytest.raises(AnchorInputError):
        append_anchor_line(anchor_path, b'{"x":1}\n')
    assert seen_flags and seen_flags[0] & fake_nofollow
    assert anchor_path.read_bytes() == b"unchanged\n"


def test_create_rejects_symlink_path_via_post_check(tmp_path) -> None:
    """排他新建遇 symlink 路径必须 fail-closed：POSIX O_EXCL 检查链接
    本身直接 EEXIST；Windows CRT 的 O_CREAT|O_EXCL 会跟随 dangling
    symlink 在目标处创建，靠新建后的 islink 复核拒绝（与 M10-04 导出
    档案同模式）。两种形态都不得把锚行透过链接写出。环境无法创建
    symlink 则 skip。
    """
    victim_dir = tmp_path / "victim"
    victim_dir.mkdir()
    link = tmp_path / "anchor.jsonl"
    try:
        os.symlink(victim_dir / "target.jsonl", link)
    except (OSError, NotImplementedError):
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    with pytest.raises((AnchorInputError, FileExistsError)):
        append_anchor_line(link, b'{"x":1}\n')
    target = victim_dir / "target.jsonl"
    # POSIX：目标处根本没创建；Windows：至多留下 0 字节目标文件——
    # 锚行绝不出现（不清理目标：不猜测链接指向的文件归属）
    assert not target.exists() or target.read_bytes() == b""


def test_parent_directory_fsync_only_for_new_file(tmp_path, monkeypatch) -> None:
    """新建锚文件写入 fsync 成功后同步父目录；续写既有文件不重复同步
    （目录项未变化）。spy 内调用真实实现：POSIX 真实 fsync 目录，
    Windows 按能力跳过。
    """
    calls: list[Path] = []
    real = anchor_module._fsync_parent_directory

    def spy(p):
        calls.append(Path(p))
        real(p)

    monkeypatch.setattr(anchor_module, "_fsync_parent_directory", spy)
    anchor_path = tmp_path / "anchor.jsonl"
    append_anchor_line(anchor_path, b'{"a":1}\n')
    assert calls == [anchor_path]
    append_anchor_line(anchor_path, b'{"b":2}\n')
    assert calls == [anchor_path]  # 续写不触发目录同步
    assert anchor_path.read_bytes() == b'{"a":1}\n{"b":2}\n'


def test_parent_directory_fsync_capability_boundaries(tmp_path, monkeypatch) -> None:
    """目录 fsync 错误语义：无能力平台不打开目录；EINVAL（文件系统不
    支持）按能力跳过；其余 OSError 原样上抛（锚行已 fsync 也不虚报
    目录项持久）；fd 每次都被关闭。
    """
    # 无目录 fd 能力（Windows 形态）：直接跳过，不触碰 os.open
    monkeypatch.setattr(anchor_module, "_CAN_FSYNC_DIR", False)

    def fail_open(p, *args, **kwargs):
        raise AssertionError("无能力时不应打开目录")

    monkeypatch.setattr(anchor_module.os, "open", fail_open)
    anchor_module._fsync_parent_directory(tmp_path / "a.jsonl")  # 不抛

    # 有能力（POSIX 形态）：EINVAL=文件系统能力边界跳过；其余上抛
    monkeypatch.setattr(anchor_module, "_CAN_FSYNC_DIR", True)
    opened: list[object] = []
    closed: list[int] = []

    def fake_open(p, flags, *args, **kwargs):
        opened.append((p, flags))
        return 7

    monkeypatch.setattr(anchor_module.os, "open", fake_open)
    monkeypatch.setattr(
        anchor_module.os, "close", lambda fd: closed.append(fd)
    )

    def raise_einval(fd):
        raise OSError(errno.EINVAL, "fs does not support directory fsync")

    monkeypatch.setattr(anchor_module.os, "fsync", raise_einval)
    anchor_module._fsync_parent_directory(tmp_path / "a.jsonl")
    assert closed == [7]

    def raise_eio(fd):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(anchor_module.os, "fsync", raise_eio)
    with pytest.raises(OSError):
        anchor_module._fsync_parent_directory(tmp_path / "a.jsonl")
    assert closed == [7, 7]  # 抛错路径同样关闭 fd
    assert opened and opened[0][0] == tmp_path


# --- 4c. 失败回截保护区（返工：目录 fsync 失败也在保护区内） -----------------


def test_new_file_parent_dir_fsync_eio_truncates_anchor_line(
    tmp_path, monkeypatch
) -> None:
    """回归（Codex 审核实证）：新建锚文件后父目录 fsync EIO——锚行已
    写入并 fsync，但持久化承诺未达成而上抛；此前目录 fsync 在回截
    保护区之外，锚行留在盘上，CLI exit 2 后重跑误判 up-to-date（状态
    二义）。现在必须回截：锚行不在盘上（新建文件回 0 字节空文件=合法
    初始状态，不 unlink——删除目录项又需目录 fsync，而它正在失败）。
    """

    def eio_dir_fsync(path):
        raise OSError(errno.EIO, "simulated directory fsync I/O error")

    monkeypatch.setattr(anchor_module, "_fsync_parent_directory", eio_dir_fsync)
    anchor_path = tmp_path / "anchor.jsonl"
    with pytest.raises(OSError) as exc_info:
        append_anchor_line(anchor_path, b'{"x":1}\n')
    assert exc_info.value.errno == errno.EIO
    assert anchor_path.read_bytes() == b""  # 锚行不存在

    # 环境恢复后重跑：正常追加一行，不会因残留锚行误判 up-to-date
    monkeypatch.undo()
    append_anchor_line(anchor_path, b'{"x":1}\n')
    assert anchor_path.read_bytes() == b'{"x":1}\n'


def test_existing_file_fsync_eio_rolls_back_to_original_bytes(
    tmp_path, monkeypatch
) -> None:
    """既有文件：写入成功但文件 fsync EIO（磁盘 IO 故障形态）-> 回截到
    原字节，不留新增行。同一 mock 使回截后的 fsync 也失败——原始 EIO
    仍上抛，不虚构成功。
    """
    anchor_path = tmp_path / "anchor.jsonl"
    original = b'{"kept":true}\n'
    anchor_path.write_bytes(original)

    def eio_fsync(fd):
        raise OSError(errno.EIO, "simulated file fsync I/O error")

    monkeypatch.setattr(anchor_module.os, "fsync", eio_fsync)
    with pytest.raises(OSError) as exc_info:
        append_anchor_line(anchor_path, b'{"new":1}\n')
    assert exc_info.value.errno == errno.EIO
    assert anchor_path.read_bytes() == original  # 新增行被回截


def test_rollback_failure_does_not_mask_original_error(
    tmp_path, monkeypatch
) -> None:
    """写入失败（ENOSPC）且回截 ftruncate 也失败（EIO）：上抛的必须是
    **原始** ENOSPC，绝不用次生错误或虚构成功替代。半行残留属如实声明
    的残余灾难路径，由下次完整校验按 partial line fail-closed。
    """
    anchor_path = tmp_path / "anchor.jsonl"
    anchor_path.write_bytes(b'{"kept":true}\n')
    real_write = os.write
    calls: list[int] = []

    def flaky_write(fd, data):
        calls.append(fd)
        if len(calls) == 1:
            view = memoryview(data)
            half = len(data) // 2
            real_write(fd, view[:half])
            return half  # 先写半行
        raise OSError(errno.ENOSPC, "simulated disk full")

    monkeypatch.setattr(anchor_module.os, "write", flaky_write)

    def failing_ftruncate(fd, length):
        raise OSError(errno.EIO, "simulated ftruncate failure")

    monkeypatch.setattr(anchor_module.os, "ftruncate", failing_ftruncate)
    with pytest.raises(OSError) as exc_info:
        append_anchor_line(anchor_path, b'{"x":1}\n')
    assert exc_info.value.errno == errno.ENOSPC  # 原始错误，非次生 EIO


# --- 5. 并发边界：单连接单事务快照 -------------------------------------------


def test_verify_and_cross_check_share_single_engine_and_snapshot(
    tmp_path, monkeypatch
) -> None:
    """锚定全程只建一次引擎一个连接：verify 与交叉核对同一快照，无竞态。"""
    db_url = _make_db(tmp_path, rows=2)
    calls: list[str] = []
    real_create_engine = anchor_module.create_engine

    def counting_create_engine(url, **kwargs):
        calls.append(url)
        return real_create_engine(url, **kwargs)

    monkeypatch.setattr(anchor_module, "create_engine", counting_create_engine)
    report = _anchor(db_url, tmp_path / "anchor.jsonl", execute=True)
    assert report["status"] == "anchored"
    assert calls == [db_url]  # 恰好一个引擎：读取与校验共用同一快照


def test_run_anchor_consumes_external_snapshot_without_new_engine(
    tmp_path, monkeypatch
) -> None:
    """外部快照路径（production-preflight 复用）：不建引擎、不另取快照，
    verify-only 结论与自建快照路径一致；verify 结论从 snapshot 纯重算
    （verify_report 参数已移除——外部报告与快照不一致的歧义无从产生）。"""
    db_url = _make_db(tmp_path, rows=2)
    anchor_path = tmp_path / "anchor.jsonl"
    assert _anchor(db_url, anchor_path, execute=True)["status"] == "anchored"
    snapshot, _ = _snapshot_of(db_url)

    def no_engine(url, **kwargs):
        raise AssertionError("外部快照路径不得新建数据库引擎")

    monkeypatch.setattr(anchor_module, "create_engine", no_engine)

    verify_calls: list[dict] = []

    def spy_verify(snap):
        verify_calls.append(snap)
        return verify_chain_snapshot(snap)

    monkeypatch.setattr(anchor_module, "verify_chain_snapshot", spy_verify)

    report = asyncio.run(
        run_anchor(None, anchor_path, verify_only=True, snapshot=snapshot)
    )
    assert report["status"] == "up-to-date"
    assert report["anchor_file"]["anchors"] == 1
    assert verify_calls == [snapshot]  # 纯重算恰好一次（无 IO、无新连接）


def test_run_anchor_external_snapshot_guardrails(tmp_path) -> None:
    """外部快照只服务只读路径，参数组合 fail-closed 防歧义（Codex 二轮
    返工）：与 --yes 互斥（落盘必须现场快照）、与 db_url 互斥（同时提供=
    数据来源二义，此前会静默用 snapshot 忽略 db_url）；无 db_url 且未提供
    快照时拒绝。全部拒绝零锚文件副作用。"""
    db_url = _make_db(tmp_path, rows=1)
    anchor_path = tmp_path / "anchor.jsonl"
    snapshot, _ = _snapshot_of(db_url)

    with pytest.raises(AnchorInputError, match="--yes 互斥"):
        asyncio.run(run_anchor(None, anchor_path, execute=True, snapshot=snapshot))
    with pytest.raises(AnchorInputError, match="--db-url"):
        asyncio.run(run_anchor(None, anchor_path, verify_only=True))
    with pytest.raises(AnchorInputError, match="--db-url 互斥"):
        asyncio.run(
            run_anchor(db_url, anchor_path, verify_only=True, snapshot=snapshot)
        )
    assert not anchor_path.exists()  # 三种拒绝都不产生锚文件


# --- 6. CLI：exit code / json / 摘要 / 分发 / 无泄漏 -------------------------


def test_cli_exit_codes_and_outputs(tmp_path, capsys) -> None:
    # 缺 --db-url：exit 2
    assert _cli(None, tmp_path / "a.jsonl") == 2
    assert "fail-closed" in capsys.readouterr().out

    # 连接失败：exit 2
    assert (
        _cli("sqlite+aiosqlite:///C:/definitely-missing-dir/x.db", tmp_path / "a.jsonl")
        == 2
    )

    # 目录锚文件：exit 2
    db_url = _make_db(tmp_path, rows=2)
    assert _cli(db_url, tmp_path) == 2

    # dry-run：exit 0，不落盘
    anchor_path = tmp_path / "anchor.jsonl"
    assert _cli(db_url, anchor_path) == 0
    assert "DRY-RUN" in capsys.readouterr().out
    assert not anchor_path.exists()

    # --yes：exit 0，落盘
    assert _cli(db_url, anchor_path, yes=True) == 0
    assert "ANCHORED" in capsys.readouterr().out
    assert len(_lines(anchor_path)) == 1

    # up-to-date：exit 0 不追加
    assert _cli(db_url, anchor_path, yes=True) == 0
    assert "UP-TO-DATE" in capsys.readouterr().out
    assert len(_lines(anchor_path)) == 1

    # DB 链被篡改：exit 1，不再追加
    _mutate(db_url, update(AuditLogRow).values(action="role.demote"))
    assert _cli(db_url, anchor_path, yes=True) == 1
    out = capsys.readouterr().out
    assert "INVALID" in out
    assert len(_lines(anchor_path)) == 1

    # verify-only：exit 1（DB invalid）
    assert _cli(db_url, anchor_path, verify_only=True) == 1

    # --yes 与 --verify-only 同时给：exit 2
    assert _cli(db_url, anchor_path, yes=True, verify_only=True) == 2


def test_cli_json_report_shape(tmp_path, capsys) -> None:
    db_url = _make_db(tmp_path, rows=2)
    anchor_path = tmp_path / "anchor.jsonl"
    assert _cli(db_url, anchor_path, as_json=True) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "dry-run"
    assert report["valid"] is True
    assert report["written"] is False
    assert report["db"]["entries"] == 2
    assert report["db"]["head_sequence"] == 2
    assert report["anchor_file"]["path"] == str(anchor_path)
    assert report["anchor_file"]["anchors"] == 0
    assert set(report["proposed_anchor"]) == {
        "algorithm", "anchor_hash", "anchored_at", "head_hash",
        "previous_anchor_hash", "schema_version", "sequence",
    }


def test_cli_main_dispatches_audit_chain_anchor(tmp_path, monkeypatch, capsys) -> None:
    db_url = _make_db(tmp_path, rows=1)
    anchor_path = tmp_path / "anchor.jsonl"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "audit-chain-anchor",
            "--db-url",
            db_url,
            "--anchor-file",
            str(anchor_path),
            "--yes",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
    assert "ANCHORED" in capsys.readouterr().out
    assert len(_lines(anchor_path)) == 1


def test_anchor_reports_never_leak_secret_payload(tmp_path, capsys) -> None:
    """锚定报告/摘要只含 sequence/hash/原因，不携带 before/after 正文。"""
    marker = "TOP-SECRET-PAYLOAD-MARKER-71c9"
    db_url = _make_db(tmp_path, rows=2)
    anchor_path = tmp_path / "anchor.jsonl"
    assert _cli(db_url, anchor_path, yes=True) == 0
    capsys.readouterr()

    _mutate(db_url, update(AuditLogRow).values(after={"note": marker}))
    assert _cli(db_url, anchor_path, yes=True, as_json=True) == 1
    out = capsys.readouterr().out
    assert marker not in out
    assert marker not in format_anchor_summary(json.loads(out))

    # 锚文件本身也不含 marker（只有固定七个无敏感字段）
    assert marker not in anchor_path.read_text(encoding="utf-8")
