"""M10-04 历史无归属试卷治理：只读分类报告 + 精确 ID 安全迁移。

覆盖：报告分类/引用统计/汇总、报告只读、CLI fail-closed 与输出路径安全、
dry-run 不落盘不修改、--yes 缺失拒绝执行、无效 ID 整体拒绝、行数不一致
事务回滚、assign-owner 成功与审计、keep-public 仅记录决策、export-delete
拒绝被引用卷、未引用卷导出后删除、导出校验失败不删库。
"""
from __future__ import annotations

import asyncio
import json
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
    async def run():
        sessions = make_sessionmaker(create_engine(db_url))
        async with sessions() as session:
            rows = (
                await session.execute(select(model).where(*conditions))
            ).scalars().all()
            for row in rows:
                session.expunge(row)
            return rows

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
    yes=False,
):
    return SimpleNamespace(
        path=path,
        db_url=db_url,
        paper_id=paper_id,
        ids_file=ids_file,
        to=to,
        export=export,
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


def test_validate_export_file_detects_problems(tmp_path) -> None:
    good = tmp_path / "good.jsonl"
    good.write_text(
        json.dumps({"paper": {"id": "pap_a"}, "questions": []})
        + "\n"
        + json.dumps({"paper": {"id": "pap_b"}, "questions": []})
        + "\n",
        encoding="utf-8",
    )
    assert validate_export_file(good, ["pap_a", "pap_b"]) == []

    broken = tmp_path / "broken.jsonl"
    broken.write_text("oops\n", encoding="utf-8")
    assert validate_export_file(broken, ["pap_a"])

    mismatch = tmp_path / "mismatch.jsonl"
    mismatch.write_text(
        json.dumps({"paper": {"id": "pap_b"}, "questions": []}) + "\n",
        encoding="utf-8",
    )
    problems = validate_export_file(mismatch, ["pap_a"])
    assert any("缺少" in problem for problem in problems)
    assert any("多出" in problem for problem in problems)

    duplicate = tmp_path / "duplicate.jsonl"
    duplicate.write_text(
        json.dumps({"paper": {"id": "pap_a"}, "questions": []})
        + "\n"
        + json.dumps({"paper": {"id": "pap_a"}, "questions": []})
        + "\n",
        encoding="utf-8",
    )
    assert any("重复" in problem for problem in validate_export_file(duplicate, ["pap_a"]))


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

