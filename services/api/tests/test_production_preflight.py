"""M10-07 production cutover preflight：只读汇总预检的检查语义与安全边界。

覆盖矩阵：
1. CLI 注册与参数：缺 --db-url exit 2、--phase 必选（缺省 argparse exit 2）、
   **不存在 --yes 参数**（argparse 拒绝 exit 2——命令没有任何执行形态）、
   main 分发、--json / 人类摘要、--output artifacts/temp 路径护栏；
1b. --output 写入失败（返工）：artifacts 父级被普通文件占用（mkdir 失败）、
   write_text 抛 OSError（磁盘满）——均稳定 exit 2、简明错误、无 traceback、
   不打印检查结论（不把半途报告伪装成完整结论）；
2. phase 语义：pre-migration 链表缺失=pending（pending_migration）、
   current 落后 head=pending；post-migration 链表缺失/链 invalid/current !=
   head/未知 revision 均 fail（exit 1）；pre 与 post 全绿（exit 0）；
2b. 链表缺失形态（返工）：pre-migration 只有「entries+state 同时缺失且
   audit_log 存在」算 pending；仅缺其一、audit_log 缺失而链表在、三表
   全缺（库早于 0024）一律 fail（exit 1）——不对应任何迁移可达形态；
3. anchor：post 未提供/文件不存在=not_configured（exit 0，runbook 人工完成）、
   verify-only up-to-date=pass / head 超前=pending、verify-only **零写入**
   （锚文件字节前后不变）、锚文件有锚但 DB 链表缺失=fail、
   路径问题（父目录缺失/目录）exit 2；
3b. 同一快照（返工）：anchor 交叉核对消费主流程事务快照——anchor 模块
   load_verified_snapshot/create_engine 零调用、主流程恰好一个引擎；
4. 历史治理聚合：计数精确（seed/已归属不计入）、pending 语义、
   生产 paper/draft ID 零泄漏（marker 断言 JSON 与人类摘要）；
5. 安全边界：连接失败不回显凭据（marker 密码）、库名输出不含 URL、
   非法 phase exit 2；
5b. Alembic 脚本目录解析失败（返工）：AlembicError / SyntaxError 稳定
   exit 2、简明脱敏错误（不回显凭据 marker）、无 traceback、无检查结论；
6. 只读性：全部表行数、表集合、alembic_version、SQLite 库文件字节、
   锚文件字节在 pre/post 两 phase 各跑一轮后完全不变。
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy import update

from app.db.base import Base
from app.db.orm import (
    AuditLogRow,
    CourseGenerationDraftRow,
    PaperRow,
    VariantQuestionDraftRow,
)
from app.db.session import create_engine, make_sessionmaker
from app.domain.audit_chain import append_audit
from app.ops import cli as cli_module
from app.ops.production_preflight import (
    _alembic_script_state,
    format_preflight_summary,
    run_preflight,
)

NOW = datetime(2026, 9, 4, 9, 0, 0, 654321, tzinfo=UTC)
#: 生产 ID marker：任何输出（JSON/人类摘要）都不得包含
PAPER_ID_MARKER = "PROD-PAPER-ID-77c3"
GEN_DRAFT_ID_MARKER = "PROD-GEN-DRAFT-77c4"
VAR_DRAFT_ID_MARKER = "PROD-VAR-DRAFT-77c5"
#: 连接错误信息里的密码 marker：不得回显
PASSWORD_MARKER = "TOP-SECRET-PW-77c6"

CHECK_IDS = {"db-connect", "alembic", "audit-chain", "audit-anchor", "legacy-governance"}


def _head_revision() -> str:
    heads, _ = _alembic_script_state()
    assert len(heads) == 1
    return heads[0]


def _db_url(tmp_path, name: str = "pf.db") -> str:
    return f"sqlite+aiosqlite:///{(tmp_path / name).as_posix()}"


def _payload(target: str) -> dict:
    return {
        "action": "user.promote",
        "target_type": "user",
        "target_id": target,
        "request_id": f"req-{target}",
        "actor_id": "u_admin",
        "actor_username": "admin",
        "before": {"role": "learner"},
        "after": {"role": "admin"},
    }


def _init_db(
    db_url: str,
    *,
    audit_rows: int = 0,
    chain_tables: bool = True,
    alembic_rev: str | None = None,
) -> None:
    """create_all 建库；可选拆掉 0027 链表 / 写入 alembic_version（只读对账输入）。"""

    async def run() -> None:
        engine = create_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            if not chain_tables:
                # 模拟 0027 未执行的生产库形态（链表缺失）
                await conn.execute(sa.text("DROP TABLE audit_chain_entries"))
                await conn.execute(sa.text("DROP TABLE audit_chain_state"))
            if alembic_rev:
                await conn.execute(
                    sa.text(
                        "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
                    )
                )
                await conn.execute(
                    sa.text("INSERT INTO alembic_version (version_num) VALUES (:rev)"),
                    {"rev": alembic_rev},
                )
        sessions = make_sessionmaker(engine)
        for index in range(audit_rows):
            async with sessions() as session, session.begin():
                await append_audit(
                    session,
                    _payload(f"u{index + 1}"),
                    clock=lambda i=index: NOW + timedelta(seconds=i),
                )
        await engine.dispose()

    asyncio.run(run())


def _insert_legacy_data(db_url: str) -> None:
    """2 张无归属非 seed 卷（marker ID）+ seed 卷 + 已归属卷；
    generation/variant 各 1 条 NULL owner 草稿（marker ID）+ 1 条已归属。"""

    async def run() -> None:
        engine = create_engine(db_url)
        sessions = make_sessionmaker(engine)
        async with sessions() as session, session.begin():
            session.add_all(
                [
                    PaperRow(
                        id=PAPER_ID_MARKER + "-a",
                        title="legacy-a",
                        subtitle="",
                        source="imported",
                        subject="math",
                        difficulty="medium",
                        duration_minutes=60,
                        license="cc-by-4.0",
                        tags=[],
                        owner_id=None,
                    ),
                    PaperRow(
                        id=PAPER_ID_MARKER + "-b",
                        title="legacy-b",
                        subtitle="",
                        source="imported",
                        subject="math",
                        difficulty="medium",
                        duration_minutes=60,
                        license="cc-by-4.0",
                        tags=[],
                        owner_id=None,
                    ),
                    # seed 卷：owner NULL 但 source=seed，不计入治理计数
                    PaperRow(
                        id="seed-paper-1",
                        title="seed",
                        subtitle="",
                        source="AI Learning OS seed",
                        subject="math",
                        difficulty="easy",
                        duration_minutes=30,
                        license="cc0-1.0",
                        tags=[],
                        owner_id=None,
                    ),
                    # 已归属卷：不计入
                    PaperRow(
                        id="owned-paper-1",
                        title="owned",
                        subtitle="",
                        source="imported",
                        subject="math",
                        difficulty="hard",
                        duration_minutes=90,
                        license="cc-by-4.0",
                        tags=[],
                        owner_id="user-1",
                    ),
                    CourseGenerationDraftRow(
                        id=GEN_DRAFT_ID_MARKER,
                        owner_id=None,
                        goal="learn",
                        status="pending_review",
                        dag_version=1,
                        plan={},
                        chapter_count=1,
                        generation_note="note",
                        created_at=NOW,
                    ),
                    CourseGenerationDraftRow(
                        id="owned-gen-draft",
                        owner_id="user-1",
                        goal="learn",
                        status="pending_review",
                        dag_version=1,
                        plan={},
                        chapter_count=1,
                        generation_note="note",
                        created_at=NOW,
                    ),
                    VariantQuestionDraftRow(
                        id=VAR_DRAFT_ID_MARKER,
                        owner_id=None,
                        status="pending_review",
                        variants={},
                        variant_count=1,
                        generation_note="note",
                        created_at=NOW,
                    ),
                ]
            )
        await engine.dispose()

    asyncio.run(run())


def _tamper(db_url: str, statement) -> None:

    async def run() -> None:
        engine = create_engine(db_url)
        async with engine.begin() as conn:
            await conn.execute(statement)
        await engine.dispose()

    asyncio.run(run())


def _append_audits(db_url: str, count: int, *, base: int = 100) -> None:

    async def run() -> None:
        engine = create_engine(db_url)
        sessions = make_sessionmaker(engine)
        for index in range(count):
            async with sessions() as session, session.begin():
                await append_audit(
                    session,
                    _payload(f"x{base + index}"),
                    clock=lambda i=index: NOW + timedelta(seconds=base + i),
                )
        await engine.dispose()

    asyncio.run(run())


def _table_fingerprint(db_url: str) -> dict[str, tuple[int, ...]]:
    """全部表名 -> 行数 + alembic_version 值（只读性对照指纹）。"""

    async def run() -> dict[str, tuple[int, ...]]:
        engine = create_engine(db_url)
        try:
            async with engine.connect() as conn:
                names = sorted(
                    await conn.run_sync(lambda c: sa.inspect(c).get_table_names())
                )
                fingerprint: dict[str, tuple[int, ...]] = {}
                for name in names:
                    count = await conn.scalar(sa.text(f"SELECT COUNT(*) FROM {name}"))
                    if name == "alembic_version":
                        revs = [
                            row[0]
                            for row in await conn.execute(
                                sa.text("SELECT version_num FROM alembic_version")
                            )
                        ]
                        fingerprint[name] = (int(count or 0), *revs)
                    else:
                        fingerprint[name] = (int(count or 0),)
                return fingerprint
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _preflight(
    db_url,
    phase,
    *,
    anchor_file=None,
    as_json=False,
    output=None,
) -> int:
    return cli_module._run_production_preflight(
        SimpleNamespace(
            db_url=db_url,
            phase=phase,
            anchor_file=str(anchor_file) if anchor_file else None,
            as_json=as_json,
            output=str(output) if output else None,
        )
    )


def _run_json(db_url, phase, *, anchor_file=None) -> dict:
    """直接调 run_preflight 拿报告 dict（不经 CLI 打印），并守卫检查项集合不漂移。"""
    report = asyncio.run(run_preflight(db_url, phase, anchor_file=anchor_file))
    assert {c["id"] for c in report["checks"]} == CHECK_IDS
    return report


def _check(report: dict, check_id: str) -> dict:
    return next(c for c in report["checks"] if c["id"] == check_id)


# --- 1. CLI：注册 / 参数 / 输出护栏 -------------------------------------------


def test_cli_missing_db_url_exit_2(capsys) -> None:
    assert _preflight(None, "pre-migration") == 2
    assert "fail-closed" in capsys.readouterr().out


def test_cli_phase_required_and_no_yes_flag(tmp_path, monkeypatch, capsys) -> None:
    """--phase 必选；命令不存在 --yes（无任何执行形态），argparse 一律 exit 2。"""
    db_url = _db_url(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "production-preflight", "--db-url", db_url],
    )
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 2

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "production-preflight",
            "--db-url",
            db_url,
            "--phase",
            "pre-migration",
            "--yes",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 2
    capsys.readouterr()


def test_cli_invalid_phase_exit_2(tmp_path, capsys) -> None:
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev=_head_revision())
    assert _preflight(db_url, "not-a-phase") == 2


def test_cli_output_path_guard(tmp_path, capsys) -> None:
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev=_head_revision())
    # 仓库外普通路径：拒绝（exit 2，报告不落盘）
    assert _preflight(db_url, "pre-migration", output=tmp_path / "report.json") == 2
    assert "拒绝写入" in capsys.readouterr().out
    assert not (tmp_path / "report.json").exists()
    # artifacts/ 目录：允许并写入
    safe = tmp_path / "artifacts" / "report.json"
    assert _preflight(db_url, "pre-migration", output=safe) == 0
    out = capsys.readouterr().out
    assert "报告已写入" in out
    report = json.loads(safe.read_text(encoding="utf-8"))
    assert report["phase"] == "pre-migration"


def test_output_write_failure_when_parent_is_file(tmp_path, capsys) -> None:
    """artifacts 路径父级被普通文件占用（mkdir 失败）：稳定 exit 2、简明错误、
    无 traceback、不打印检查结论（不得把未落盘的报告伪装成完整结论）。"""
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev=_head_revision())
    (tmp_path / "artifacts").write_text("occupied", encoding="utf-8")

    assert (
        _preflight(db_url, "pre-migration", output=tmp_path / "artifacts" / "r.json")
        == 2
    )
    captured = capsys.readouterr()
    assert "报告写入失败" in captured.out
    assert "报告已写入" not in captured.out
    assert "RESULT:" not in captured.out  # 检查结论摘要不落屏
    assert captured.err == ""  # 无 traceback
    assert not (tmp_path / "artifacts" / "r.json").exists()


def test_output_write_failure_disk_full(tmp_path, monkeypatch, capsys) -> None:
    """write_text 抛 OSError（模拟磁盘满）：exit 2、报告不落盘、无检查结论。"""

    def boom(self, *args, **kwargs):
        raise OSError(28, "No space left on device")

    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev=_head_revision())
    target = tmp_path / "artifacts" / "r.json"
    monkeypatch.setattr(Path, "write_text", boom)

    assert _preflight(db_url, "pre-migration", output=target) == 2
    captured = capsys.readouterr()
    assert "报告写入失败" in captured.out
    assert "No space left" in captured.out
    assert "报告已写入" not in captured.out
    assert "RESULT:" not in captured.out
    assert captured.err == ""
    assert not target.exists()


def test_main_dispatches_production_preflight(tmp_path, monkeypatch, capsys) -> None:
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev=_head_revision())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "production-preflight",
            "--db-url",
            db_url,
            "--phase",
            "post-migration",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "生产切换只读 preflight" in out
    assert "不执行迁移" in out


# --- 2. phase 语义：pending / fail / pass 与退出码 ---------------------------


def test_pre_migration_pending_when_chain_tables_missing(tmp_path, capsys) -> None:
    """pre-migration 允许 0027 表缺失：audit-chain=pending_migration、
    alembic 落后=pending、anchor 未配置=pending、治理计数=pending；无 fail。"""
    db_url = _db_url(tmp_path)
    _init_db(
        db_url,
        chain_tables=False,
        alembic_rev="0026_paper_owner",
    )
    _insert_legacy_data(db_url)

    assert _preflight(db_url, "pre-migration") == 0
    report = _run_json(db_url, "pre-migration")
    chain = _check(report, "audit-chain")
    assert chain["status"] == "pending"
    assert "pending_migration" in chain["detail"]
    assert chain["data"]["missing_tables"] == [
        "audit_chain_entries",
        "audit_chain_state",
    ]
    assert _check(report, "alembic")["status"] == "pending"
    assert _check(report, "alembic")["data"] == {
        "current": "0026_paper_owner",
        "head": _head_revision(),
    }
    assert _check(report, "audit-anchor")["status"] == "pending"
    assert _check(report, "legacy-governance")["status"] == "pending"
    assert report["summary"]["fail"] == 0
    assert report["exit_code"] == 0
    summary = format_preflight_summary(report)
    assert "仍需按 runbook 人工" in summary


def test_pre_and_post_migration_all_pass(tmp_path) -> None:
    """迁移已就位（current==head、链 valid、锚 up-to-date、无待归属）：两 phase 全绿。"""
    db_url = _db_url(tmp_path)
    head = _head_revision()
    _init_db(db_url, audit_rows=2, alembic_rev=head)
    anchor_path = tmp_path / "anchor.jsonl"
    from app.ops.audit_chain_anchor import run_anchor

    assert asyncio.run(run_anchor(db_url, anchor_path, execute=True))["status"] == "anchored"

    for phase in ("pre-migration", "post-migration"):
        report = _run_json(db_url, phase, anchor_file=anchor_path)
        assert {c["status"] for c in report["checks"]} == {"pass"}, phase
        assert report["exit_code"] == 0
        assert _check(report, "alembic")["data"]["current"] == head
        assert _check(report, "audit-anchor")["data"]["anchor_status"] == "up-to-date"


def test_post_migration_chain_invalid_exit_1(tmp_path, capsys) -> None:
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=2, alembic_rev=_head_revision())
    _tamper(db_url, update(AuditLogRow).values(action="role.demote"))

    assert _preflight(db_url, "post-migration", as_json=True) == 1
    report = json.loads(capsys.readouterr().out)
    chain = _check(report, "audit-chain")
    assert chain["status"] == "fail"
    assert "invalid" in chain["detail"]
    assert report["summary"]["fail"] == 1
    assert report["exit_code"] == 1


def test_post_migration_chain_tables_missing_exit_1(tmp_path) -> None:
    db_url = _db_url(tmp_path)
    _init_db(db_url, chain_tables=False, alembic_rev=_head_revision())
    report = _run_json(db_url, "post-migration")
    assert _check(report, "audit-chain")["status"] == "fail"
    assert report["exit_code"] == 1


def test_pre_migration_partial_chain_table_missing_fails(tmp_path) -> None:
    """仅缺 entries 或仅缺 state 都不是迁移可达形态（0027 原子建两表）：
    pre-migration 也 fail（exit 1），schema 部分损坏/连错库必须停下排查。"""
    for missing in ("audit_chain_entries", "audit_chain_state"):
        db_url = _db_url(tmp_path, f"pf-{missing}.db")
        _init_db(db_url, audit_rows=1, alembic_rev=_head_revision())
        _tamper(db_url, sa.text(f"DROP TABLE {missing}"))

        assert _preflight(db_url, "pre-migration") == 1, missing
        report = _run_json(db_url, "pre-migration")
        chain = _check(report, "audit-chain")
        assert chain["status"] == "fail", missing
        assert chain["data"]["missing_tables"] == [missing]
        assert missing in chain["detail"]
        assert "不对应任何迁移可达形态" in chain["detail"]
        assert report["exit_code"] == 1, missing


def test_pre_migration_audit_log_missing_fails(tmp_path) -> None:
    """audit_log 缺失而链表存在：0024 先建 audit_log、0027 后建链表，该
    形态不可达（链表在说明 0027 执行过、audit_log 必在）——fail。"""
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev=_head_revision())
    _tamper(db_url, sa.text("DROP TABLE audit_log"))
    report = _run_json(db_url, "pre-migration")
    chain = _check(report, "audit-chain")
    assert chain["status"] == "fail"
    assert chain["data"]["missing_tables"] == ["audit_log"]
    assert "不对应任何迁移可达形态" in chain["detail"]
    assert report["exit_code"] == 1


def test_pre_migration_all_three_chain_tables_missing_fails(tmp_path) -> None:
    """三表全缺=库早于 0024（audit_log 建立之前）：不是本 runbook 的
    preflight 起点（runbook 预期 current=0026），fail 而非 pending。"""
    db_url = _db_url(tmp_path, "pf-pre0024.db")
    _init_db(db_url, alembic_rev="0023_ownership")
    for table in ("audit_log", "audit_chain_entries", "audit_chain_state"):
        _tamper(db_url, sa.text(f"DROP TABLE {table}"))
    report = _run_json(db_url, "pre-migration")
    chain = _check(report, "audit-chain")
    assert chain["status"] == "fail"
    assert set(chain["data"]["missing_tables"]) == {
        "audit_log",
        "audit_chain_entries",
        "audit_chain_state",
    }
    assert "不对应任何迁移可达形态" in chain["detail"]
    assert report["exit_code"] == 1


def test_post_migration_partial_chain_table_missing_fails(tmp_path) -> None:
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev=_head_revision())
    _tamper(db_url, sa.text("DROP TABLE audit_chain_state"))
    report = _run_json(db_url, "post-migration")
    assert _check(report, "audit-chain")["status"] == "fail"
    assert report["exit_code"] == 1


def test_post_migration_alembic_behind_head_exit_1(tmp_path) -> None:
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev="0026_paper_owner")
    report = _run_json(db_url, "post-migration")
    alembic = _check(report, "alembic")
    assert alembic["status"] == "fail"
    assert "post-migration 要求 current == head" in alembic["detail"]
    assert report["exit_code"] == 1


def test_pre_migration_unknown_revision_fails(tmp_path) -> None:
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev="0099_not_in_repo")
    report = _run_json(db_url, "pre-migration")
    assert _check(report, "alembic")["status"] == "fail"
    assert "不在迁移脚本目录" in _check(report, "alembic")["detail"]
    assert report["exit_code"] == 1


def test_alembic_version_missing_fails(tmp_path) -> None:
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1)  # 无 alembic_version 表
    for phase in ("pre-migration", "post-migration"):
        report = _run_json(db_url, phase)
        assert _check(report, "alembic")["status"] == "fail", phase


# --- 3. anchor：verify-only / not_configured / 路径护栏 -----------------------


def test_anchor_verify_only_never_writes(tmp_path) -> None:
    """锚定检查只读：head 超前（valid/pending）与 up-to-date（pass）两种形态下
    锚文件字节都不变。"""
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=3, alembic_rev=_head_revision())
    anchor_path = tmp_path / "anchor.jsonl"
    from app.ops.audit_chain_anchor import run_anchor

    asyncio.run(run_anchor(db_url, anchor_path, execute=True))
    _append_audits(db_url, 2)  # head 前进 -> verify-only 应报可锚定（pending）

    before = anchor_path.read_bytes()
    report = _run_json(db_url, "post-migration", anchor_file=anchor_path)
    anchor = _check(report, "audit-anchor")
    assert anchor["status"] == "pending"
    assert anchor["data"]["anchor_status"] == "valid"
    assert "audit-chain-anchor --yes" in anchor["detail"]
    assert anchor_path.read_bytes() == before

    report = _run_json(db_url, "pre-migration", anchor_file=anchor_path)
    assert _check(report, "audit-anchor")["status"] == "pending"
    assert anchor_path.read_bytes() == before


def test_anchor_not_configured_post_migration(tmp_path, capsys) -> None:
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev=_head_revision())

    # 未提供 --anchor-file：not_configured，不构成 fail
    report = _run_json(db_url, "post-migration")
    anchor = _check(report, "audit-anchor")
    assert anchor["status"] == "not_configured"
    assert "本工具不创建锚文件" in anchor["detail"]
    assert report["exit_code"] == 0

    # 提供了路径但文件不存在（父目录存在）：同样 not_configured（人工创建）
    assert _preflight(db_url, "post-migration", anchor_file=tmp_path / "no-anchor.jsonl") == 0
    out = capsys.readouterr().out
    assert "NOT_CONFIGURED" in out
    assert not (tmp_path / "no-anchor.jsonl").exists()  # 绝不自动创建


def test_anchor_exists_but_db_chain_missing_fails(tmp_path) -> None:
    """pre-migration 下锚文件已有锚点而 DB 链表缺失（疑似回退/错库）：fail。"""
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=2, alembic_rev=_head_revision())
    anchor_path = tmp_path / "anchor.jsonl"
    from app.ops.audit_chain_anchor import run_anchor

    asyncio.run(run_anchor(db_url, anchor_path, execute=True))
    # 拆链表模拟「DB 无链却有锚点」
    _tamper(db_url, sa.text("DROP TABLE audit_chain_entries"))
    _tamper(db_url, sa.text("DROP TABLE audit_chain_state"))

    report = _run_json(db_url, "pre-migration", anchor_file=anchor_path)
    assert _check(report, "audit-anchor")["status"] == "fail"
    assert report["exit_code"] == 1


def test_anchor_bad_path_exit_2(tmp_path, capsys) -> None:
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev=_head_revision())
    # 父目录缺失（不自动创建）
    assert (
        _preflight(db_url, "pre-migration", anchor_file=tmp_path / "no-dir" / "a.jsonl")
        == 2
    )
    assert "锚文件输入无效" in capsys.readouterr().out
    # 路径是目录
    assert _preflight(db_url, "pre-migration", anchor_file=tmp_path) == 2


# --- 3b. 同一快照：anchor 交叉核对不开第二个数据库连接 ----------------------


def test_anchor_check_consumes_main_snapshot(tmp_path, monkeypatch) -> None:
    """锚定交叉核对消费主流程事务快照：anchor 模块 load_verified_snapshot
    与 create_engine 零调用（修复前 run_anchor 会自建第二个连接），主流程
    全程恰好一个引擎。"""
    from app.ops import audit_chain_anchor as anchor_module
    from app.ops import production_preflight as preflight_module
    from app.ops.audit_chain_anchor import run_anchor

    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=2, alembic_rev=_head_revision())
    anchor_path = tmp_path / "anchor.jsonl"
    assert asyncio.run(run_anchor(db_url, anchor_path, execute=True))["status"] == (
        "anchored"
    )

    snapshot_calls: list[str] = []
    real_snapshot = anchor_module.load_verified_snapshot

    async def spy_load_snapshot(url):
        snapshot_calls.append(url)
        return await real_snapshot(url)

    anchor_engines: list[str] = []
    real_anchor_engine = anchor_module.create_engine

    def spy_anchor_engine(url, **kwargs):
        anchor_engines.append(url)
        return real_anchor_engine(url, **kwargs)

    pf_engines: list[str] = []
    real_pf_engine = preflight_module.create_engine

    def spy_pf_engine(url, **kwargs):
        pf_engines.append(url)
        return real_pf_engine(url, **kwargs)

    monkeypatch.setattr(anchor_module, "load_verified_snapshot", spy_load_snapshot)
    monkeypatch.setattr(anchor_module, "create_engine", spy_anchor_engine)
    monkeypatch.setattr(preflight_module, "create_engine", spy_pf_engine)

    report = _run_json(db_url, "post-migration", anchor_file=anchor_path)
    assert snapshot_calls == []  # anchor 校验未另取快照
    assert anchor_engines == []  # anchor 路径零新引擎
    assert pf_engines == [db_url]  # 主流程恰好一个引擎
    anchor = _check(report, "audit-anchor")
    assert anchor["status"] == "pass"
    assert anchor["data"]["anchor_status"] == "up-to-date"


# --- 4. 历史治理聚合：计数精确 + 生产 ID 零泄漏 ------------------------------


def test_governance_counts_and_no_id_leak(tmp_path, capsys) -> None:
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev=_head_revision())
    _insert_legacy_data(db_url)

    assert _preflight(db_url, "pre-migration") == 0
    human = capsys.readouterr().out
    assert _preflight(db_url, "pre-migration", as_json=True) == 0
    report = json.loads(capsys.readouterr().out)

    governance = _check(report, "legacy-governance")
    assert governance["status"] == "pending"
    assert governance["data"] == {
        "unowned_non_seed_papers": 2,
        "course_generation_null_owner_drafts": 1,
        "variant_question_null_owner_drafts": 1,
    }
    assert "legacy-paper-migrate" in governance["detail"]

    for marker in (PAPER_ID_MARKER, GEN_DRAFT_ID_MARKER, VAR_DRAFT_ID_MARKER):
        assert marker not in human, marker
        assert marker not in json.dumps(report), marker


def test_governance_zero_counts_pass(tmp_path) -> None:
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev=_head_revision())
    report = _run_json(db_url, "post-migration")
    assert _check(report, "legacy-governance")["status"] == "pass"


def test_governance_tables_missing_fails(tmp_path) -> None:
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev=_head_revision())
    _tamper(db_url, sa.text("DROP TABLE papers"))
    report = _run_json(db_url, "pre-migration")
    assert _check(report, "legacy-governance")["status"] == "fail"
    assert "不是本应用 Schema" in _check(report, "legacy-governance")["detail"]


# --- 5. 安全边界：凭据不回显 / 库名不含 URL ----------------------------------


def test_connection_failure_redacts_credentials(capsys) -> None:
    url = f"postgresql+asyncpg://preflight_user:{PASSWORD_MARKER}@127.0.0.1:1/nodb"
    assert _preflight(url, "pre-migration") == 2
    out = capsys.readouterr().out
    assert PASSWORD_MARKER not in out
    assert "预检执行失败" in out

    # SQLite 连接失败（目录不存在）同样是 exit 2
    assert (
        _preflight("sqlite+aiosqlite:///C:/definitely-missing-dir/pf.db", "pre-migration")
        == 2
    )


def test_database_label_without_url_leak(tmp_path) -> None:
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev=_head_revision())
    report = _run_json(db_url, "pre-migration")
    connect = _check(report, "db-connect")
    assert connect["status"] == "pass"
    assert connect["data"]["dialect"] == "sqlite"
    # 库名是文件路径（非凭据），但绝不含完整 URL scheme
    assert connect["data"]["database"].endswith("pf.db")
    assert "sqlite+aiosqlite://" not in json.dumps(report)


def test_redact_secrets_unit() -> None:
    from app.ops.production_preflight import redact_secrets

    text = (
        "connect failed: postgresql+asyncpg://aios:s3cret@127.0.0.1:5433/db "
        "and sqlite+aiosqlite:///C:/x.db (no creds)"
    )
    redacted = redact_secrets(text)
    assert "s3cret" not in redacted
    assert "://aios:***@127.0.0.1:5433/db" in redacted
    assert "sqlite+aiosqlite:///C:/x.db" in redacted  # 无凭据段原样保留


# --- 5b. Alembic 脚本目录解析失败：稳定 exit 2 --------------------------------


def test_alembic_script_parse_failure_exit_2(tmp_path, monkeypatch, capsys) -> None:
    """脚本目录解析抛 alembic CommandError / SyntaxError（脚本损坏/语法
    错误/目录异常）：exit 2、简明脱敏错误、无 traceback、不产生检查结论。"""
    from alembic.util.exc import CommandError

    from app.ops import production_preflight as preflight_module

    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev=_head_revision())

    def raise_alembic():
        raise CommandError(
            "can't locate revision from postgresql://u:" + PASSWORD_MARKER + "@h/db"
        )

    monkeypatch.setattr(preflight_module, "_alembic_script_state", raise_alembic)
    assert _preflight(db_url, "pre-migration") == 2
    captured = capsys.readouterr()
    assert "Alembic 脚本目录解析失败" in captured.out
    assert PASSWORD_MARKER not in captured.out  # 错误信息先抹凭据再输出
    assert "RESULT:" not in captured.out  # 未产生检查结论
    assert captured.err == ""  # 无 traceback

    def raise_syntax():
        raise SyntaxError("invalid syntax (0027_audit_chain.py, line 40)")

    monkeypatch.setattr(preflight_module, "_alembic_script_state", raise_syntax)
    assert _preflight(db_url, "pre-migration", as_json=True) == 2
    captured = capsys.readouterr()
    assert "Alembic 脚本目录解析失败" in captured.out
    assert '"checks"' not in captured.out  # as_json 也不输出半份报告
    assert captured.err == ""


# --- 6. 只读性：行数 / 表集合 / 库文件字节 / 锚文件字节不变 -------------------


def test_preflight_is_read_only(tmp_path) -> None:
    db_url = _db_url(tmp_path)
    head = _head_revision()
    _init_db(db_url, audit_rows=2, alembic_rev=head)
    _insert_legacy_data(db_url)
    anchor_path = tmp_path / "anchor.jsonl"
    from app.ops.audit_chain_anchor import run_anchor

    asyncio.run(run_anchor(db_url, anchor_path, execute=True))

    db_file = tmp_path / "pf.db"
    before = {
        "tables": _table_fingerprint(db_url),
        "db_bytes": db_file.read_bytes(),
        "anchor_bytes": anchor_path.read_bytes(),
    }

    # pre（无锚参数 / 带锚参数）与 post（带锚参数）各跑一轮
    assert _preflight(db_url, "pre-migration") == 0
    assert _preflight(db_url, "pre-migration", anchor_file=anchor_path) == 0
    assert _preflight(db_url, "post-migration", anchor_file=anchor_path) == 0

    assert _table_fingerprint(db_url) == before["tables"]
    assert db_file.read_bytes() == before["db_bytes"]
    assert anchor_path.read_bytes() == before["anchor_bytes"]


def test_summary_pending_never_disguised_as_pass(tmp_path, capsys) -> None:
    """pending 项存在时人类摘要必须写明仍需人工动作，且退出码仍是 0（非 fail）。"""
    db_url = _db_url(tmp_path)
    _init_db(db_url, audit_rows=1, alembic_rev=_head_revision())
    assert _preflight(db_url, "pre-migration") == 0
    out = capsys.readouterr().out
    assert "pending=1" in out  # anchor expected_pending
    assert "仍需按 runbook 人工" in out
    report = _run_json(db_url, "pre-migration")
    assert report["summary"]["pending"] == 1
    assert report["exit_code"] == 0
