"""M10-04 历史无归属试卷治理：只读分类报告 + 精确 ID 安全迁移。

覆盖：报告分类/引用统计/汇总、报告只读、CLI fail-closed 与输出路径安全、
dry-run 不落盘不修改、--yes 缺失拒绝执行、无效 ID 整体拒绝、行数不一致
事务回滚、assign-owner 成功与审计（含事务内目标用户复核失败回滚）、
keep-public 仅记录决策（含陈旧行计划失败不写审计）、export-delete
拒绝被引用卷、未引用卷导出后删除、完整归档校验（同 ID 内容损坏/malformed
paper.id 不抛异常、无效 UTF-8 归档返回 problems 不抛异常）、既有归档
拒绝覆盖（含 dangling symlink）、writer OSError 稳定失败不删库、同数量
换内容在删除事务被拒绝。
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.base import Base
from app.db.orm import AuditLogRow, ExamSessionRow, PaperRow, QuestionRow, UserRow
from app.db.session import create_engine, make_sessionmaker
from app.ops import cli as cli_module
from app.ops import legacy_papers as legacy
from app.ops.legacy_papers import (
    build_legacy_paper_report,
    format_report_summary,
    resolve_paper_ids,
    run_legacy_migrate,
    validate_export_file,
)

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def _paper(paper_id: str, *, source: str = "imported", owner_id: str | None = None):
    return PaperRow(
        id=paper_id,
        title=f"legacy {paper_id}",
        subtitle="",
        source=source,
        university=None,
        year=None,
        subject="imported",
        difficulty="unknown",
        duration_minutes=10,
        tags=[],
        origin_url=None,
        license="UNKNOWN",
        owner_id=owner_id,
    )


def _question(question_id: str, paper_id: str, *, score: float = 1.0, sort_order: int = 1):
    return QuestionRow(
        id=question_id,
        paper_id=paper_id,
        question_type="mcq",
        stem="1+1=?",
        options=[{"key": "A", "text": "1"}, {"key": "B", "text": "2"}],
        answer="B",
        explanation="arithmetic",
        angles={"concept": "", "method": "", "mistake": "", "variant": ""},
        knowledge=[],
        score=score,
        difficulty=3,
        sort_order=sort_order,
    )


def _exam(exam_id: str, paper_id: str, started_at: datetime):
    return ExamSessionRow(
        exam_id=exam_id,
        paper_id=paper_id,
        paper_title=f"legacy {paper_id}",
        mode="exam",
        status="submitted",
        started_at=started_at,
        end_at=started_at + timedelta(minutes=10),
        submitted_at=started_at + timedelta(minutes=9),
        owner_id=None,
    )


def _make_db(tmp_path) -> str:
    """SQLite 替身库：seed 卷 / 已归属卷 / 被引用历史卷 / 未引用卷 / 空卷。"""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'legacy-governance.db'}"

    async def run() -> None:
        engine = create_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = make_sessionmaker(engine)
        async with sessions() as session, session.begin():
            session.add(
                UserRow(
                    id="u_alice",
                    username="alice_gov",
                    password_hash="x" * 60,
                    role="learner",
                    created_at=NOW,
                )
            )
            session.add(
                UserRow(
                    id="u_bob",
                    username="bob_gov",
                    password_hash="x" * 60,
                    role="learner",
                    created_at=NOW,
                )
            )
            session.add(_paper("seed-paper", source="AI Learning OS seed"))
            session.add(_question("q_seed", "seed-paper"))
            session.add(_paper("pap_owned", owner_id="u_bob"))
            session.add(_question("q_owned", "pap_owned"))
            session.add(_paper("pap_ref"))
            session.add(_question("q_ref_1", "pap_ref", score=2.0, sort_order=1))
            session.add(_question("q_ref_2", "pap_ref", score=3.0, sort_order=2))
            session.add(_exam("exam_old", "pap_ref", NOW - timedelta(days=10)))
            session.add(_exam("exam_new", "pap_ref", NOW - timedelta(days=1)))
            session.add(_paper("pap_unref"))
            session.add(_question("q_unref", "pap_unref", score=1.5))
            session.add(_paper("pap_empty"))
        await engine.dispose()

    asyncio.run(run())
    return db_url


def _fetch(db_url: str, model, *conditions):
    # engine 必须在同一个 event loop 内 dispose：否则 aiosqlite worker 线程
    # 携已关闭 loop 的 future 存活，全量套件随机触发
    # PytestUnhandledThreadExceptionWarning（call_soon_threadsafe on closed loop）。
    async def run():
        engine = create_engine(db_url)
        try:
            sessions = make_sessionmaker(engine)
            async with sessions() as session:
                rows = (
                    await session.execute(select(model).where(*conditions))
                ).scalars().all()
                for row in rows:
                    session.expunge(row)
                return rows
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _report_args(db_url=None, *, as_json=False, output=None):
    return SimpleNamespace(db_url=db_url, as_json=as_json, output=output)


def _migrate_args(
    path,
    db_url=None,
    *,
    paper_id=None,
    ids_file=None,
    to=None,
    export=None,
    output=None,
    yes=False,
):
    return SimpleNamespace(
        path=path,
        db_url=db_url,
        paper_id=paper_id,
        ids_file=ids_file,
        to=to,
        export=export,
        output=output,
        yes=yes,
    )


# --- 只读分类报告 -----------------------------------------------------------


def test_report_classifies_papers_and_aggregates(tmp_path) -> None:
    report = asyncio.run(build_legacy_paper_report(_make_db(tmp_path)))
    entries = {entry["paper_id"]: entry for entry in report["papers"]}
    assert set(entries) == {"pap_ref", "pap_unref", "pap_empty"}

    ref = entries["pap_ref"]
    assert ref["referenced_by_exams"] is True
    assert ref["exam_count"] == 2
    assert ref["question_count"] == 2
    assert ref["total_score"] == pytest.approx(5.0)
    assert ref["suggested_path"] == "keep_public"
    assert ref["first_exam_started_at"] == (NOW - timedelta(days=10)).isoformat()
    assert ref["last_exam_started_at"] == (NOW - timedelta(days=1)).isoformat()
    # papers 表无时间戳列：如实输出 null，不编造时间。
    assert ref["created_at"] is None
    assert ref["updated_at"] is None

    unref = entries["pap_unref"]
    assert unref["referenced_by_exams"] is False
    assert unref["exam_count"] == 0
    assert unref["last_exam_started_at"] is None
    assert unref["suggested_path"] == "assign_owner"

    empty = entries["pap_empty"]
    assert empty["question_count"] == 0
    assert empty["total_score"] == 0.0
    assert empty["suggested_path"] == "export_review"

    summary = report["summary"]
    assert summary["total"] == 3
    assert summary["referenced"] == 1
    assert summary["unreferenced"] == 2
    assert summary["total_questions"] == 3
    assert summary["by_source"] == {"imported": 3}
    assert summary["by_suggested_path"] == {
        "keep_public": 1,
        "assign_owner": 1,
        "export_review": 1,
    }
    assert summary["by_question_count_bucket"]["0"] == 1
    assert summary["by_question_count_bucket"]["1-10"] == 2
    assert summary["by_last_reference_month"]["2026-08"] == 1
    assert summary["by_last_reference_month"]["unreferenced"] == 2
    assert report["scope"]["read_only"] is True


def test_report_is_read_only_and_summary_hides_production_ids(tmp_path) -> None:
    db_url = _make_db(tmp_path)
    report = asyncio.run(build_legacy_paper_report(db_url))
    summary_text = format_report_summary(report)
    assert "pap_ref" not in summary_text
    assert "总数: 3" in summary_text
    # 数据库零写入：行数不变、无审计。
    assert len(_fetch(db_url, PaperRow)) == 5
    assert len(_fetch(db_url, QuestionRow)) == 5
    assert len(_fetch(db_url, ExamSessionRow)) == 2
    assert _fetch(db_url, AuditLogRow) == []


def test_cli_report_summary_json_and_output_safety(tmp_path, capsys) -> None:
    db_url = _make_db(tmp_path)

    assert cli_module._run_legacy_paper_report(_report_args(db_url)) == 0
    out = capsys.readouterr().out
    assert "历史无归属试卷分类报告" in out
    assert "pap_ref" not in out

    assert (
        cli_module._run_legacy_paper_report(_report_args(db_url, as_json=True)) == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["papers"]) == 3

    unsafe = tmp_path / "report.json"
    assert (
        cli_module._run_legacy_paper_report(_report_args(db_url, output=str(unsafe)))
        == 2
    )
    assert not unsafe.exists()

    safe = tmp_path / "artifacts" / "report.json"
    assert (
        cli_module._run_legacy_paper_report(_report_args(db_url, output=str(safe)))
        == 0
    )
    written = json.loads(safe.read_text(encoding="utf-8"))
    assert {entry["paper_id"] for entry in written["papers"]} == {
        "pap_ref",
        "pap_unref",
        "pap_empty",
    }


def test_main_dispatches_legacy_paper_report(tmp_path, capsys, monkeypatch) -> None:
    db_url = _make_db(tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["cli", "legacy-paper-report", "--db-url", db_url]
    )
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
    assert "总数: 3" in capsys.readouterr().out


# --- 精确 ID 解析与 CLI 门禁 -------------------------------------------------


def test_resolve_paper_ids_validates_literals(tmp_path) -> None:
    assert resolve_paper_ids(["pap_a", "pap_a"], None) == ["pap_a"]
    with pytest.raises(ValueError):
        resolve_paper_ids(["pap_%"], None)
    with pytest.raises(ValueError):
        resolve_paper_ids(["*"], None)
    with pytest.raises(ValueError):
        resolve_paper_ids(["x" * 65], None)
    with pytest.raises(ValueError):
        resolve_paper_ids([], None)

    ids_file = tmp_path / "ids.txt"
    ids_file.write_text(
        "# 注释行\n\n  pap_unref  \npap_empty\n", encoding="utf-8"
    )
    assert resolve_paper_ids(None, ids_file) == ["pap_unref", "pap_empty"]

    assert (
        cli_module._run_legacy_paper_migrate(
            _migrate_args(
                "assign-owner",
                db_url="sqlite+aiosqlite:///:memory:",
                paper_id=["pap_%"],
                to="alice_gov",
            )
        )
        == 2
    )


def test_cli_fails_closed_without_db(tmp_path, capsys) -> None:
    assert cli_module._run_legacy_paper_report(_report_args()) == 2
    assert "fail-closed" in capsys.readouterr().out
    assert (
        cli_module._run_legacy_paper_migrate(
            _migrate_args("keep-public", paper_id=["pap_ref"])
        )
        == 2
    )


def test_cli_requires_to_and_export_arguments(tmp_path) -> None:
    db_url = _make_db(tmp_path)
    assert (
        cli_module._run_legacy_paper_migrate(
            _migrate_args("assign-owner", db_url=db_url, paper_id=["pap_unref"])
        )
        == 2
    )
    assert (
        cli_module._run_legacy_paper_migrate(
            _migrate_args("export-delete", db_url=db_url, paper_id=["pap_unref"])
        )
        == 2
    )
    unsafe_export = tmp_path / "export.jsonl"
    assert (
        cli_module._run_legacy_paper_migrate(
            _migrate_args(
                "export-delete",
                db_url=db_url,
                paper_id=["pap_unref"],
                export=str(unsafe_export),
            )
        )
        == 2
    )
    assert not unsafe_export.exists()


# --- 迁移：dry-run / 无效 ID / 事务一致性 -----------------------------------


def test_dry_run_never_writes(tmp_path) -> None:
    db_url = _make_db(tmp_path)
    export = tmp_path / "artifacts" / "export.jsonl"

    assign = asyncio.run(
        run_legacy_migrate(
            db_url, "assign-owner", ["pap_unref"], execute=False, owner_ref="alice_gov"
        )
    )
    assert assign["dry_run"] is True
    assert assign["executed"] is False
    assert assign["exit_code"] == 0

    delete = asyncio.run(
        run_legacy_migrate(
            db_url, "export-delete", ["pap_unref"], execute=False, export_path=export
        )
    )
    assert delete["dry_run"] is True
    assert not export.exists()

    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_unref")
    assert _fetch(db_url, AuditLogRow) == []


def test_cli_missing_yes_refuses_execution(tmp_path, capsys) -> None:
    db_url = _make_db(tmp_path)
    code = cli_module._run_legacy_paper_migrate(
        _migrate_args(
            "assign-owner", db_url=db_url, paper_id=["pap_unref"], to="alice_gov"
        )
    )
    assert code == 0
    assert "[dry-run]" in capsys.readouterr().out
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_unref")[0].owner_id is None
    assert _fetch(db_url, AuditLogRow) == []


def test_invalid_ids_abort_whole_operation(tmp_path) -> None:
    db_url = _make_db(tmp_path)
    report = asyncio.run(
        run_legacy_migrate(
            db_url,
            "assign-owner",
            ["pap_unref", "pap_ghost"],
            execute=True,
            owner_ref="alice_gov",
        )
    )
    assert report["executed"] is False
    assert report["exit_code"] == 1
    assert report["invalid_paper_ids"] == ["pap_ghost"]
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_unref")[0].owner_id is None
    assert _fetch(db_url, AuditLogRow) == []


def test_row_count_mismatch_rolls_back(tmp_path, monkeypatch) -> None:
    db_url = _make_db(tmp_path)

    original_load_states = legacy._load_states

    async def fake_load_states(session, paper_ids):
        states = await original_load_states(session, paper_ids)
        # 模拟陈旧计划：包含一条数据库中不存在的行。
        states["pap_ghost"] = {
            "title": "ghost",
            "source": "imported",
            "owner_id": None,
            "exam_count": 0,
        }
        return states

    monkeypatch.setattr(legacy, "_load_states", fake_load_states)
    with pytest.raises(RuntimeError, match="不一致"):
        asyncio.run(
            run_legacy_migrate(
                db_url,
                "assign-owner",
                ["pap_unref", "pap_ghost"],
                execute=True,
                owner_ref="alice_gov",
            )
        )
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_unref")[0].owner_id is None
    assert _fetch(db_url, AuditLogRow) == []


# --- assign-owner 与 keep-public --------------------------------------------


def test_assign_owner_updates_eligible_rows_and_audits(tmp_path) -> None:
    db_url = _make_db(tmp_path)
    report = asyncio.run(
        run_legacy_migrate(
            db_url,
            "assign-owner",
            ["pap_unref", "pap_owned", "seed-paper"],
            execute=True,
            owner_ref="alice_gov",
        )
    )
    assert report["executed"] is True
    assert report["exit_code"] == 0
    assert report["eligible_paper_ids"] == ["pap_unref"]
    assert report["papers_modified"] == 1
    skips = {item["paper_id"]: item["reason"] for item in report["skipped"]}
    assert "系统 seed" in skips["seed-paper"]
    assert "已有归属" in skips["pap_owned"]

    papers = {paper.id: paper for paper in _fetch(db_url, PaperRow)}
    assert papers["pap_unref"].owner_id == "u_alice"
    assert papers["pap_owned"].owner_id == "u_bob"
    assert papers["seed-paper"].owner_id is None

    audits = _fetch(db_url, AuditLogRow)
    assert len(audits) == 1
    assert audits[0].action == "ops.legacy_paper.assign_owner"
    assert audits[0].actor_username == "cli-operator"
    assert audits[0].request_id.startswith("cli-")
    assert audits[0].before == {"owner_id": None, "paper_ids": ["pap_unref"]}
    assert audits[0].after["owner_id"] == "u_alice"
    assert audits[0].after["username"] == "alice_gov"
    assert audits[0].after["updated"] == 1


def test_assign_owner_accepts_user_id_and_rejects_unknown_user(tmp_path) -> None:
    db_url = _make_db(tmp_path)
    by_id = asyncio.run(
        run_legacy_migrate(
            db_url, "assign-owner", ["pap_unref"], execute=True, owner_ref="u_bob"
        )
    )
    assert by_id["exit_code"] == 0
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_unref")[0].owner_id == "u_bob"

    unknown = asyncio.run(
        run_legacy_migrate(
            db_url, "assign-owner", ["pap_empty"], execute=True, owner_ref="ghost"
        )
    )
    assert unknown["executed"] is False
    assert unknown["exit_code"] == 1
    assert "目标用户不存在" in unknown["failure"]
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_empty")[0].owner_id is None
    assert len(_fetch(db_url, AuditLogRow)) == 1  # 仅第一次成功迁移写审计


def test_assign_owner_target_user_vanishing_in_transaction_rolls_back(
    tmp_path, monkeypatch
) -> None:
    """解析与写入事务之间的并发窗口：目标用户在事务复核时消失 → 回滚不审计。"""
    db_url = _make_db(tmp_path)
    original = legacy._resolve_target_user
    calls = {"count": 0}

    async def vanishing_user(session, reference, *, for_update=False):
        calls["count"] += 1
        if for_update:
            return None  # 事务内复核：目标用户已被并发删除
        return await original(session, reference)

    monkeypatch.setattr(legacy, "_resolve_target_user", vanishing_user)
    with pytest.raises(RuntimeError, match="事务复核"):
        asyncio.run(
            run_legacy_migrate(
                db_url,
                "assign-owner",
                ["pap_unref"],
                execute=True,
                owner_ref="alice_gov",
            )
        )
    assert calls["count"] == 2  # 事务外解析 + 事务内锁定复核
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_unref")[0].owner_id is None
    assert _fetch(db_url, AuditLogRow) == []


def test_keep_public_records_decision_without_touching_papers(tmp_path) -> None:
    db_url = _make_db(tmp_path)
    report = asyncio.run(
        run_legacy_migrate(db_url, "keep-public", ["pap_ref"], execute=True)
    )
    assert report["executed"] is True
    assert report["papers_modified"] == 0
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_ref")[0].owner_id is None
    assert len(_fetch(db_url, ExamSessionRow)) == 2

    audits = _fetch(db_url, AuditLogRow)
    assert len(audits) == 1
    assert audits[0].action == "ops.legacy_paper.keep_public"
    assert audits[0].after["decision"] == "keep_public"
    assert audits[0].after["papers_modified"] == 0


def test_keep_public_stale_plan_rolls_back_without_audit(tmp_path, monkeypatch) -> None:
    """陈旧行计划（行已消失/状态漂移）：审计不得与事实不符 → 回滚不写审计。"""
    db_url = _make_db(tmp_path)
    original = legacy._load_states

    async def stale_states(session, paper_ids):
        states = await original(session, paper_ids)
        states["pap_ghost"] = {
            "title": "ghost",
            "source": "imported",
            "owner_id": None,
            "exam_count": 0,
        }
        return states

    monkeypatch.setattr(legacy, "_load_states", stale_states)
    with pytest.raises(RuntimeError, match="不一致"):
        asyncio.run(
            run_legacy_migrate(
                db_url, "keep-public", ["pap_ref", "pap_ghost"], execute=True
            )
        )
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_ref")[0].owner_id is None
    assert _fetch(db_url, AuditLogRow) == []


# --- export-delete ----------------------------------------------------------


def test_export_delete_refers_referenced_paper_to_manual_handling(tmp_path) -> None:
    db_url = _make_db(tmp_path)
    export = tmp_path / "artifacts" / "export.jsonl"
    report = asyncio.run(
        run_legacy_migrate(
            db_url, "export-delete", ["pap_ref"], execute=True, export_path=export
        )
    )
    assert report["executed"] is False
    assert report["exit_code"] == 1
    assert report["blocked_paper_ids"] == ["pap_ref"]
    assert any("历史考试" in item["reason"] for item in report["skipped"])
    # 被引用卷与考试历史原样保留；无导出、无审计。
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_ref")
    assert len(_fetch(db_url, ExamSessionRow)) == 2
    assert not export.exists()
    assert _fetch(db_url, AuditLogRow) == []


def test_export_delete_exports_then_deletes_unreferenced(tmp_path) -> None:
    db_url = _make_db(tmp_path)
    export = tmp_path / "artifacts" / "export.jsonl"
    report = asyncio.run(
        run_legacy_migrate(
            db_url,
            "export-delete",
            ["pap_unref", "pap_empty", "seed-paper"],
            execute=True,
            export_path=export,
        )
    )
    assert report["exit_code"] == 0
    assert report["executed"] is True
    assert report["eligible_paper_ids"] == ["pap_unref", "pap_empty"]
    assert report["deleted_questions"] == 1

    records = [
        json.loads(line)
        for line in export.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {record["paper"]["id"] for record in records} == {
        "pap_unref",
        "pap_empty",
    }
    unref = next(r for r in records if r["paper"]["id"] == "pap_unref")
    assert len(unref["questions"]) == 1
    assert unref["questions"][0]["answer"] == "B"
    assert unref["paper"]["owner_id"] is None

    remaining = {paper.id for paper in _fetch(db_url, PaperRow)}
    assert remaining == {"seed-paper", "pap_owned", "pap_ref"}
    assert _fetch(db_url, QuestionRow, QuestionRow.paper_id == "pap_unref") == []
    assert len(_fetch(db_url, ExamSessionRow)) == 2

    audits = _fetch(db_url, AuditLogRow)
    assert len(audits) == 1
    assert audits[0].action == "ops.legacy_paper.export_delete"
    assert audits[0].after["deleted_papers"] == 2
    assert audits[0].after["deleted_questions"] == 1
    assert audits[0].after["export_path"] == str(export)


def test_export_validation_failure_keeps_database_untouched(
    tmp_path, monkeypatch
) -> None:
    db_url = _make_db(tmp_path)
    export = tmp_path / "artifacts" / "export.jsonl"

    def corrupt_writer(path, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-json\n", encoding="utf-8")

    monkeypatch.setattr(legacy, "_write_export_file", corrupt_writer)
    report = asyncio.run(
        run_legacy_migrate(
            db_url, "export-delete", ["pap_unref"], execute=True, export_path=export
        )
    )
    assert report["executed"] is False
    assert report["exit_code"] == 1
    assert report["export_validation_problems"]
    assert "导出校验失败" in report["failure"]
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_unref")
    assert _fetch(db_url, QuestionRow, QuestionRow.paper_id == "pap_unref")
    assert _fetch(db_url, AuditLogRow) == []


def test_export_delete_invalid_utf8_archive_keeps_database(
    tmp_path, monkeypatch, capsys
) -> None:
    """归档被写成无效 UTF-8：返回稳定失败计划（exit_code 1），DB 原样保留。

    CLI 只捕获 RuntimeError；validate_export_file 对无效 UTF-8 必须计入
    problems 而不是抛 UnicodeDecodeError（ValueError 子类），否则整条
    export-delete 链路 traceback 而非稳定失败。
    """
    db_url = _make_db(tmp_path)
    export = tmp_path / "artifacts" / "export.jsonl"

    def invalid_utf8_writer(path, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xfe\n")

    monkeypatch.setattr(legacy, "_write_export_file", invalid_utf8_writer)
    report = asyncio.run(
        run_legacy_migrate(
            db_url, "export-delete", ["pap_unref"], execute=True, export_path=export
        )
    )
    assert report["executed"] is False
    assert report["exit_code"] == 1
    assert any("无法按 UTF-8 读取" in p for p in report["export_validation_problems"])
    assert "导出校验失败" in report["failure"]
    assert export.read_bytes() == b"\xff\xfe\n"
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_unref")
    assert _fetch(db_url, QuestionRow, QuestionRow.paper_id == "pap_unref")
    assert _fetch(db_url, AuditLogRow) == []

    # 经 CLI 入口同样稳定：退出码 1、报告含导出校验问题，不 traceback。
    code = cli_module._run_legacy_paper_migrate(
        _migrate_args(
            "export-delete",
            db_url=db_url,
            paper_id=["pap_unref"],
            export=str(tmp_path / "artifacts" / "export-cli.jsonl"),
            yes=True,
        )
    )
    assert code == 1
    body = json.loads(capsys.readouterr().out.split("\n[失败]")[0])
    assert body["executed"] is False
    assert body["exit_code"] == 1
    assert any("无法按 UTF-8 读取" in p for p in body["export_validation_problems"])
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_unref")
    assert _fetch(db_url, QuestionRow, QuestionRow.paper_id == "pap_unref")
    assert _fetch(db_url, AuditLogRow) == []


def test_export_delete_tampered_content_same_ids_keeps_database(tmp_path, monkeypatch):
    """同 ID 集合但题干/答案被损坏：完整归档校验失败，DB 原样保留。"""
    db_url = _make_db(tmp_path)
    export = tmp_path / "artifacts" / "export.jsonl"

    def tampering_writer(path, records):
        tampered = json.loads(json.dumps(records, ensure_ascii=False))
        for record in tampered:
            if record["paper"]["id"] == "pap_unref":
                record["paper"]["title"] = "tampered title"
                record["questions"][0]["answer"] = "A"
                record["questions"][0]["explanation"] = "tampered"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in tampered),
            encoding="utf-8",
        )

    monkeypatch.setattr(legacy, "_write_export_file", tampering_writer)
    report = asyncio.run(
        run_legacy_migrate(
            db_url, "export-delete", ["pap_unref"], execute=True, export_path=export
        )
    )
    assert report["executed"] is False
    assert report["exit_code"] == 1
    assert any("内容与快照不一致" in p for p in report["export_validation_problems"])
    # ID 集合一致：不得误报缺少/多出/重复。
    assert not any(
        "缺少" in p or "多出" in p or "重复" in p
        for p in report["export_validation_problems"]
    )
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_unref")
    assert _fetch(db_url, QuestionRow, QuestionRow.paper_id == "pap_unref")
    assert _fetch(db_url, AuditLogRow) == []


def test_export_delete_refuses_to_overwrite_existing_archive(tmp_path) -> None:
    """既有归档是历史证据：拒绝覆盖、不改 DB、原文件字节不动。"""
    db_url = _make_db(tmp_path)
    export = tmp_path / "artifacts" / "export.jsonl"
    export.parent.mkdir(parents=True)
    export.write_text("既有历史证据\n", encoding="utf-8")

    report = asyncio.run(
        run_legacy_migrate(
            db_url, "export-delete", ["pap_unref"], execute=True, export_path=export
        )
    )
    assert report["executed"] is False
    assert report["exit_code"] == 1
    assert "拒绝覆盖" in report["failure"]
    assert export.read_text(encoding="utf-8") == "既有历史证据\n"
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_unref")
    assert _fetch(db_url, QuestionRow, QuestionRow.paper_id == "pap_unref")
    assert _fetch(db_url, AuditLogRow) == []


def test_export_delete_refuses_to_overwrite_dangling_symlink(tmp_path) -> None:
    """dangling symlink 也是“已存在”：拒绝且绝不透过链接写目标文件。"""
    db_url = _make_db(tmp_path)
    export = tmp_path / "artifacts" / "export.jsonl"
    export.parent.mkdir(parents=True)
    try:
        export.symlink_to(tmp_path / "nowhere.jsonl")
    except OSError as cause:
        pytest.skip(f"本环境无法创建 symlink: {cause}")

    report = asyncio.run(
        run_legacy_migrate(
            db_url, "export-delete", ["pap_unref"], execute=True, export_path=export
        )
    )
    assert report["executed"] is False
    assert report["exit_code"] == 1
    assert "拒绝覆盖" in report["failure"]
    assert export.is_symlink() and not export.exists()
    assert not (tmp_path / "nowhere.jsonl").exists()
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_unref")
    assert _fetch(db_url, AuditLogRow) == []


def test_export_writer_oserror_fails_closed_without_db_changes(
    tmp_path, monkeypatch
) -> None:
    """导出 IO 失败（如磁盘满）→ 稳定失败计划，不删任何行、不写成功审计。"""
    db_url = _make_db(tmp_path)
    export = tmp_path / "artifacts" / "export.jsonl"

    def no_space_writer(path, records):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(legacy, "_write_export_file", no_space_writer)
    report = asyncio.run(
        run_legacy_migrate(
            db_url, "export-delete", ["pap_unref"], execute=True, export_path=export
        )
    )
    assert report["executed"] is False
    assert report["exit_code"] == 1
    assert "写入失败" in report["failure"]
    assert not export.exists()
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_unref")
    assert _fetch(db_url, QuestionRow, QuestionRow.paper_id == "pap_unref")
    assert _fetch(db_url, AuditLogRow) == []


def test_export_writer_losing_create_race_fails_closed(tmp_path, monkeypatch) -> None:
    """排他创建竞态（对手抢先建档）→ FileExistsError 稳定失败，不删库。"""
    db_url = _make_db(tmp_path)
    export = tmp_path / "artifacts" / "export.jsonl"

    def racing_writer(path, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("对手已抢先建档\n", encoding="utf-8")
        raise FileExistsError(f"导出文件已存在: {path}")

    monkeypatch.setattr(legacy, "_write_export_file", racing_writer)
    report = asyncio.run(
        run_legacy_migrate(
            db_url, "export-delete", ["pap_unref"], execute=True, export_path=export
        )
    )
    assert report["executed"] is False
    assert report["exit_code"] == 1
    assert "拒绝覆盖" in report["failure"]
    assert export.read_text(encoding="utf-8") == "对手已抢先建档\n"
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_unref")
    assert _fetch(db_url, QuestionRow, QuestionRow.paper_id == "pap_unref")
    assert _fetch(db_url, AuditLogRow) == []


def test_export_delete_rejects_same_count_content_swap(tmp_path, monkeypatch) -> None:
    """导出校验通过后、删除事务前题目被同数量换内容 → 复查不一致整体回滚。"""
    db_url = _make_db(tmp_path)
    db_file = tmp_path / "legacy-governance.db"
    export = tmp_path / "artifacts" / "export.jsonl"
    original_validate = legacy.validate_export_file

    def swap_stem_during_validation(path, records):
        problems = original_validate(path, records)
        assert problems == []  # 归档本身完好，问题在库里
        # 模拟校验后、删除事务前的同数量内容替换（rowcount 巧合相等）。
        conn = sqlite3.connect(db_file)
        try:
            conn.execute(
                "UPDATE questions SET stem = ? WHERE id = ?",
                ("tampered-after-export", "q_unref"),
            )
            conn.commit()
        finally:
            conn.close()
        return problems

    monkeypatch.setattr(legacy, "validate_export_file", swap_stem_during_validation)
    # rowcount 巧合相等（1==1），拒绝必须来自内容复查而非行数校验。
    with pytest.raises(RuntimeError, match="已验证导出档案不一致"):
        asyncio.run(
            run_legacy_migrate(
                db_url,
                "export-delete",
                ["pap_unref"],
                execute=True,
                export_path=export,
            )
        )
    # 删除被拒绝：试卷与题目原样保留（外部改动如实保留），无审计。
    questions = _fetch(db_url, QuestionRow, QuestionRow.paper_id == "pap_unref")
    assert len(questions) == 1
    assert questions[0].stem == "tampered-after-export"
    assert _fetch(db_url, PaperRow, PaperRow.id == "pap_unref")
    assert _fetch(db_url, AuditLogRow) == []


def test_validate_export_file_detects_problems(tmp_path) -> None:
    record_a = {"paper": {"id": "pap_a"}, "questions": []}
    record_b = {"paper": {"id": "pap_b"}, "questions": []}
    good = tmp_path / "good.jsonl"
    good.write_text(
        json.dumps(record_a, ensure_ascii=False) + "\n"
        + json.dumps(record_b, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    assert validate_export_file(good, [record_a, record_b]) == []

    broken = tmp_path / "broken.jsonl"
    broken.write_text("oops\n", encoding="utf-8")
    assert validate_export_file(broken, [record_a])

    mismatch = tmp_path / "mismatch.jsonl"
    mismatch.write_text(
        json.dumps(record_b, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    problems = validate_export_file(mismatch, [record_a])
    assert any("缺少" in problem for problem in problems)
    assert any("多出" in problem for problem in problems)

    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text(
        json.dumps(record_a, ensure_ascii=False) + "\n"
        + json.dumps(record_a, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    assert any("重复" in problem for problem in validate_export_file(duplicate, [record_a]))


def test_validate_export_file_detects_tampered_content_same_ids(tmp_path) -> None:
    """同 paper ID 但题干/答案被损坏：完整归档校验必须失败（不只看 ID 集合）。"""
    expected = [
        {
            "exported_at": "2026-09-03T00:00:00+00:00",
            "paper": {"id": "pap_a", "title": "原卷", "owner_id": None},
            "questions": [
                {
                    "id": "q1",
                    "stem": "1+1=?",
                    "answer": "B",
                    "explanation": "arithmetic",
                    "options": [{"key": "A", "text": "1"}],
                    "score": 1.5,
                }
            ],
        }
    ]
    tampered_file = tmp_path / "tampered.jsonl"
    tampered = json.loads(json.dumps(expected, ensure_ascii=False))
    tampered[0]["paper"]["title"] = "损坏标题"
    tampered[0]["questions"][0]["answer"] = "A"
    tampered[0]["questions"][0]["explanation"] = "损坏解释"
    tampered_file.write_text(
        json.dumps(tampered[0], ensure_ascii=False) + "\n", encoding="utf-8"
    )
    problems = validate_export_file(tampered_file, expected)
    assert any("内容与快照不一致" in problem for problem in problems)
    detail = next(p for p in problems if "内容与快照不一致" in p)
    assert "paper.title" in detail
    assert "questions" in detail
    # ID 集合本身一致：不得误报缺少/多出。
    assert not any("缺少" in p or "多出" in p for p in problems)


def test_validate_export_file_malformed_paper_ids_never_raise(tmp_path) -> None:
    """malformed paper.id（非字符串/空值/不可哈希对象）→ problems，不抛 TypeError。"""
    expected = [{"paper": {"id": "pap_a"}, "questions": []}]
    malformed = tmp_path / "malformed.jsonl"
    malformed.write_text(
        "\n".join(
            [
                json.dumps({"paper": {"id": {"nested": "dict"}}}),
                json.dumps({"paper": {"id": None}}),
                json.dumps({"paper": {"id": ""}}),
                json.dumps({"paper": {"id": ["unhashable"]}}),
                json.dumps({"paper": []}),
                json.dumps(["not", "an", "object"]),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    problems = validate_export_file(malformed, expected)
    assert len([p for p in problems if "paper.id 非法" in p]) == 4
    assert any("缺少 paper 对象" in p for p in problems)
    assert any("不是试卷记录对象" in p for p in problems)
    assert any("缺少 1 张试卷" in p for p in problems)


def test_validate_export_file_invalid_utf8_returns_problem(tmp_path) -> None:
    """无效 UTF-8 归档：直接返回 problems（含明确文案），不抛 UnicodeDecodeError。"""
    expected = [{"paper": {"id": "pap_a"}, "questions": []}]
    invalid = tmp_path / "invalid-utf8.jsonl"
    invalid.write_bytes(b"\xff\xfe\n")
    problems = validate_export_file(invalid, expected)
    assert any("无法按 UTF-8 读取" in problem for problem in problems)
    # 文件级读取失败即整体不可信：不进入逐行解析，也不误报缺行。
    assert len(problems) == 1


def test_cli_export_delete_via_ids_file(tmp_path, capsys) -> None:
    db_url = _make_db(tmp_path)
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("# 选中的未引用卷\npap_unref\n\npap_empty\n", encoding="utf-8")
    export = tmp_path / "artifacts" / "export.jsonl"
    code = cli_module._run_legacy_paper_migrate(
        _migrate_args(
            "export-delete",
            db_url=db_url,
            ids_file=str(ids_file),
            export=str(export),
            yes=True,
        )
    )
    assert code == 0
    body = json.loads(capsys.readouterr().out.split("\n[dry-run]")[0])
    assert body["executed"] is True
    assert export.exists()
    assert {paper.id for paper in _fetch(db_url, PaperRow)} == {
        "seed-paper",
        "pap_owned",
        "pap_ref",
    }


# --- 测试资源生命周期 --------------------------------------------------------


def test_fetch_disposes_verification_engine(tmp_path, monkeypatch) -> None:
    """回归 M10-10：_fetch 自建 engine 必须在 finally dispose。

    未 dispose 的 aiosqlite 连接要等 GC 异步回收（时机不确定），其 worker
    线程可能在后续测试的 event loop 关闭后才调 call_soon_threadsafe →
    全量套件随机出现 PytestUnhandledThreadExceptionWarning。业务断言已由
    本文件全部经由 _fetch 读库覆盖，此处只锁资源生命周期。
    """
    db_url = _make_db(tmp_path)
    engines = []
    real_create_engine = create_engine

    def tracking_create_engine(url: str, **kwargs):
        engine = real_create_engine(url, **kwargs)
        engines.append(engine)
        return engine

    monkeypatch.setattr(sys.modules[__name__], "create_engine", tracking_create_engine)
    assert _fetch(db_url, PaperRow)
    # dispose 已在 _fetch 返回前完成：连接池为空（未 dispose 时为 1），
    # aiosqlite worker 线程随之退出，不会比 event loop 活得更久。
    assert len(engines) == 1
    assert "Connections in pool: 0" in engines[0].pool.status()


# --- M11-13 --output 批次报告落盘 ------------------------------------------------


def test_migrate_output_registered_in_argparse(monkeypatch, capsys, tmp_path) -> None:
    """--output 参数已注册：main 解析后进入 handler 护栏（越界路径 exit 2）。"""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "legacy-paper-migrate",
            "keep-public",
            "--db-url",
            "sqlite+aiosqlite:///:memory:",
            "--paper-id",
            "pap_ref",
            "--output",
            str(tmp_path / "batch.json"),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "拒绝写入" in captured.out
    assert not (tmp_path / "batch.json").exists()


def test_migrate_output_dry_run_persists_report(tmp_path, capsys) -> None:
    """dry-run 报告如实落盘；stdout 纯 JSON 可解析且与文件逐字一致，提示走 stderr。"""
    db_url = _make_db(tmp_path)
    out = tmp_path / "artifacts" / "batch.json"
    code = cli_module._run_legacy_paper_migrate(
        _migrate_args(
            "keep-public", db_url=db_url, paper_id=["pap_ref"], output=str(out)
        )
    )
    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["dry_run"] is True
    assert payload["paper_ids"] == ["pap_ref"]
    assert out.read_text(encoding="utf-8") == captured.out.rstrip("\n")
    assert "[dry-run]" in captured.err
    assert "报告已写入" in captured.err
    assert "[dry-run]" not in captured.out and "报告已写入" not in captured.out


@pytest.mark.parametrize("bad_kind", ["outside", "directory", "symlink", "symlink_dir"])
def test_migrate_output_rejects_unsafe_path_before_db(
    tmp_path, capsys, monkeypatch, bad_kind
) -> None:
    """非法输出形态（越界/目录/symlink/中间目录 symlink）在 DB runner 前拒绝。"""
    called = []
    monkeypatch.setattr(legacy, "run_legacy_migrate", lambda *a, **k: called.append(1))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    out = None
    if bad_kind == "outside":
        out = tmp_path / "batch.json"
    elif bad_kind == "directory":
        out = artifacts / "adir"
        out.mkdir()
    else:
        try:
            if bad_kind == "symlink":
                real = tmp_path / "real.json"
                real.write_text("{}", encoding="utf-8")
                out = artifacts / "batch.json"
                os.symlink(real, out)
            else:
                real_dir = tmp_path / "real-dir"
                real_dir.mkdir()
                link_dir = artifacts / "link-dir"
                os.symlink(real_dir, link_dir, target_is_directory=True)
                out = link_dir / "batch.json"
        except OSError:
            pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    assert (
        cli_module._run_legacy_paper_migrate(
            _migrate_args(
                "keep-public",
                db_url="sqlite+aiosqlite:///:memory:",
                paper_id=["pap_ref"],
                output=str(out),
            )
        )
        == 2
    )
    assert called == []
    assert "拒绝写入" in capsys.readouterr().out
    if bad_kind == "outside":
        assert not out.exists()
    if bad_kind == "symlink":
        assert out.is_symlink(), "symlink 本身不被改写"
    if bad_kind == "symlink_dir":
        assert (tmp_path / "real-dir").is_dir()


@pytest.mark.parametrize("variant", ["direct", "dotdot", "case"])
def test_migrate_output_conflict_with_ids_file_rejected(
    tmp_path, capsys, monkeypatch, variant
) -> None:
    """output == --ids-file（直接与等价路径书写）拒绝且 runner 零调用、字节不变。"""
    if variant == "case" and os.path.normcase("A") != os.path.normcase("a"):
        pytest.skip("此平台路径大小写敏感，大小写变体不是等价路径")
    called = []
    monkeypatch.setattr(legacy, "run_legacy_migrate", lambda *a, **k: called.append(1))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    ids_file = artifacts / "ids.txt"
    ids_file.write_text("pap_unref\n", encoding="utf-8")
    before = ids_file.read_bytes()
    conflict = {
        "direct": str(ids_file),
        "dotdot": os.path.join(str(artifacts), "..", artifacts.name, "ids.txt"),
        "case": os.path.join(str(artifacts), "IDS.TXT"),
    }[variant]
    assert (
        cli_module._run_legacy_paper_migrate(
            _migrate_args(
                "keep-public",
                db_url="sqlite+aiosqlite:///:memory:",
                ids_file=str(ids_file),
                output=conflict,
            )
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "同一文件" in captured.out
    assert called == []
    assert ids_file.read_bytes() == before


def test_migrate_output_conflict_with_export_rejected(
    tmp_path, capsys, monkeypatch
) -> None:
    """export-delete：output == --export 拒绝（防批次报告覆盖导出证据）。"""
    called = []
    monkeypatch.setattr(legacy, "run_legacy_migrate", lambda *a, **k: called.append(1))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    export = artifacts / "export.jsonl"
    assert (
        cli_module._run_legacy_paper_migrate(
            _migrate_args(
                "export-delete",
                db_url="sqlite+aiosqlite:///:memory:",
                paper_id=["pap_unref"],
                export=str(export),
                output=str(export),
            )
        )
        == 2
    )
    assert "同一文件" in capsys.readouterr().out
    assert called == []
    assert not export.exists()


def test_migrate_output_atomic_failure_keeps_old_bytes(
    tmp_path, capsys, monkeypatch
) -> None:
    """写入失败：exit 2、旧输出字节原样、无 .tmp 残留、如实说明 DB 状态。"""
    db_url = _make_db(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    out = artifacts / "batch.json"
    out.write_text('{"old": true}', encoding="utf-8")

    def no_space(path, text):
        raise OSError("模拟磁盘满")

    monkeypatch.setattr(cli_module, "_write_report_atomic", no_space)
    code = cli_module._run_legacy_paper_migrate(
        _migrate_args(
            "keep-public", db_url=db_url, paper_id=["pap_ref"], output=str(out)
        )
    )
    assert code == 2
    assert out.read_text(encoding="utf-8") == '{"old": true}'
    assert not list(artifacts.glob("*.tmp"))
    captured = capsys.readouterr()
    assert "落盘失败" in captured.err
    assert "数据库未修改" in captured.err
    assert "报告已写入" not in captured.err
    assert json.loads(captured.out)["dry_run"] is True


def test_migrate_output_runtime_error_keeps_old_bytes(
    tmp_path, capsys, monkeypatch
) -> None:
    """run_* 抛 RuntimeError（事务回滚、无 report）：不写新输出、旧输出不变。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    out = artifacts / "batch.json"
    out.write_text('{"old": true}', encoding="utf-8")

    async def boom(*a, **k):
        raise RuntimeError("行数不一致")

    monkeypatch.setattr(legacy, "run_legacy_migrate", boom)
    code = cli_module._run_legacy_paper_migrate(
        _migrate_args(
            "assign-owner",
            db_url="sqlite+aiosqlite:///:memory:",
            paper_id=["pap_unref"],
            to="alice_gov",
            output=str(out),
            yes=True,
        )
    )
    assert code == 1
    assert out.read_text(encoding="utf-8") == '{"old": true}'
    assert not list(artifacts.glob("*.tmp"))
    assert "事务已回滚" in capsys.readouterr().out


def test_migrate_output_failure_report_persisted_and_rejected(tmp_path, capsys) -> None:
    """failure report 如实落盘且保留退出码 1；governance-evidence 拒绝之。"""
    db_url = _make_db(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    out = artifacts / "batch.json"
    code = cli_module._run_legacy_paper_migrate(
        _migrate_args(
            "assign-owner",
            db_url=db_url,
            paper_id=["pap_unref", "pap_ghost"],
            to="alice_gov",
            output=str(out),
            yes=True,
        )
    )
    assert code == 1
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["executed"] is False
    assert written["invalid_paper_ids"] == ["pap_ghost"]
    assert written["exit_code"] == 1
    captured = capsys.readouterr()
    assert "[失败]" in captured.err
    assert json.loads(captured.out)["executed"] is False

    report_file = artifacts / "report.json"
    assert (
        cli_module._run_legacy_paper_report(
            _report_args(db_url, output=str(report_file))
        )
        == 0
    )
    evidence = artifacts / "legacy-papers.json"
    assert (
        cli_module._run_governance_evidence(
            SimpleNamespace(
                report=str(report_file),
                batch=[str(out)],
                output=str(evidence),
                as_json=False,
            )
        )
        == 2
    )
    assert not evidence.exists()


def test_migrate_output_batch_consumed_by_governance_evidence(tmp_path) -> None:
    """成功批次经 --output 落盘后可被 governance-evidence 直接消费推导。"""
    db_url = _make_db(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    report_file = artifacts / "report.json"
    assert (
        cli_module._run_legacy_paper_report(
            _report_args(db_url, output=str(report_file))
        )
        == 0
    )
    batch = artifacts / "batch.json"
    assert (
        cli_module._run_legacy_paper_migrate(
            _migrate_args(
                "keep-public",
                db_url=db_url,
                paper_id=["pap_ref"],
                output=str(batch),
                yes=True,
            )
        )
        == 0
    )
    persisted = json.loads(batch.read_text(encoding="utf-8"))
    assert persisted["executed"] is True
    assert persisted["audit_action"] == "ops.legacy_paper.keep_public"

    evidence = artifacts / "legacy-papers.json"
    assert (
        cli_module._run_governance_evidence(
            SimpleNamespace(
                report=str(report_file),
                batch=[str(batch)],
                output=str(evidence),
                as_json=False,
            )
        )
        == 1
    )
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["step"] == "legacy-papers"
    assert payload["pending_count"] == 2  # pap_unref / pap_empty 未覆盖
    assert payload["batches_executed"] == 1

    dry_batch = artifacts / "dry-batch.json"
    assert (
        cli_module._run_legacy_paper_migrate(
            _migrate_args(
                "keep-public",
                db_url=db_url,
                paper_id=["pap_unref"],
                output=str(dry_batch),
            )
        )
        == 0
    )
    assert (
        cli_module._run_governance_evidence(
            SimpleNamespace(
                report=str(report_file),
                batch=[str(dry_batch)],
                output=str(evidence),
                as_json=False,
            )
        )
        == 2
    )
