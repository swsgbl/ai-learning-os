"""M10-04 generation/variant 历史草稿归属治理：只读报告 + 精确 ID 安全迁移。

覆盖：两类报告字段/引用提取/owner 推断（多 owner、NULL owner、缺失资源、
无引用均 manual_review 不猜）、按 kind 过滤与汇总聚合、报告零写入、
CLI fail-closed 与输出路径护栏、dry-run 不落库不写审计、--yes 缺失拒绝
执行、无效/跨 kind ID 整体拒绝、行状态漂移事务回滚（含 keep-unowned）、
assign-owner 成功 + owner 更新 + 同事务审计（用户名/ID 解析、未知用户
拒绝、事务内目标用户复核失败回滚）、审计写入失败业务更新回滚、
keep-unowned 仅写审计不改草稿、status/业务 JSON 不被触碰。
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
from app.db.orm import (
    AuditLogRow,
    CourseGenerationDraftRow,
    ResourceRow,
    VariantQuestionDraftRow,
)
from app.db.session import create_engine, make_sessionmaker
from app.ops import cli as cli_module
from app.ops import draft_ownership as ownership
from app.ops.draft_ownership import (
    build_draft_owner_report,
    extract_course_generation_resource_ids,
    extract_variant_question_resource_ids,
    format_draft_owner_report_summary,
    resolve_draft_ids,
    run_draft_migrate,
)

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
REVIEWED = NOW + timedelta(hours=1)


def _user(user_id: str, username: str):
    from app.db.orm import UserRow

    return UserRow(
        id=user_id,
        username=username,
        password_hash="x" * 60,
        role="learner",
        created_at=NOW,
    )


def _resource(resource_id: str, *, owner_id: str | None):
    return ResourceRow(
        id=resource_id,
        owner_id=owner_id,
        media_type="application/pdf",
        title=f"resource {resource_id}",
        content_hash=f"sha256-{resource_id}",
        storage_key=f"objects/{resource_id}",
        fetched_at=NOW,
    )


def _cg_draft(
    draft_id: str,
    *,
    owner_id: str | None = None,
    status: str = "pending_review",
    plan: dict,
):
    return CourseGenerationDraftRow(
        id=draft_id,
        owner_id=owner_id,
        goal=f"学会 {draft_id}",
        status=status,
        dag_version=3,
        plan=plan,
        chapter_count=len(plan.get("lessons", [])),
        generation_note="生成 N 章",
        review_note=None,
        reviewed_at=REVIEWED if status in ("approved", "rejected") else None,
        created_at=NOW,
    )


def _vq_draft(
    draft_id: str,
    *,
    owner_id: str | None = None,
    status: str = "pending_review",
    variants: list,
):
    return VariantQuestionDraftRow(
        id=draft_id,
        owner_id=owner_id,
        status=status,
        variants=variants,
        variant_count=len(variants),
        generation_note=f"生成 {len(variants)} 个变式",
        review_note=None,
        reviewed_at=REVIEWED if status in ("approved", "rejected") else None,
        created_at=NOW,
    )


def _cg_plan(*resource_ids: str) -> dict:
    """lessons[].resources[].resource_id 结构（与 M5-07 计划一致）。"""
    return {
        "lessons": [
            {
                "chapter_no": 1,
                "objectives": "objective",
                "resources": [
                    {"resource_id": rid, "snippet": f"snippet {rid}"}
                    for rid in resource_ids
                ],
            }
        ]
    }


def _vq_variants(*resource_ids: str) -> list:
    """variants[].evidence.resource_id 结构（与 M5-08 生成器一致）。"""
    return [
        {
            "variant_no": index + 1,
            "transform": "context_swap",
            "stem": f"变式 {index + 1}",
            "evidence": {
                "source_question_no": index + 1,
                "resource_id": rid,
                "source_stem_excerpt": "原题摘录",
            },
        }
        for index, rid in enumerate(resource_ids)
    ]


def _make_db(tmp_path) -> str:
    """SQLite 替身库：两类草稿各覆盖 assign/manual 的全部建议分支。"""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'draft-ownership.db'}"

    async def run() -> None:
        engine = create_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = make_sessionmaker(engine)
        async with sessions() as session, session.begin():
            session.add(_user("u_alice", "alice_draft"))
            session.add(_user("u_bob", "bob_draft"))
            session.add(_resource("res_alice", owner_id="u_alice"))
            session.add(_resource("res_bob", owner_id="u_bob"))
            session.add(_resource("res_public", owner_id=None))
            session.add(
                _cg_draft("cgd_owned", owner_id="u_bob", plan=_cg_plan("res_bob"))
            )
            session.add(_cg_draft("cgd_single", plan=_cg_plan("res_alice")))
            session.add(
                _cg_draft("cgd_multi", plan=_cg_plan("res_alice", "res_bob"))
            )
            session.add(
                _cg_draft("cgd_null_owner", status="approved", plan=_cg_plan("res_public"))
            )
            session.add(_cg_draft("cgd_missing", plan=_cg_plan("res_ghost")))
            session.add(
                _cg_draft("cgd_norefs", status="rejected", plan=_cg_plan())
            )
            session.add(
                _vq_draft("vqd_owned", owner_id="u_alice", variants=_vq_variants("res_alice"))
            )
            session.add(
                _vq_draft("vqd_single", variants=_vq_variants("res_alice", "res_alice"))
            )
            session.add(
                _vq_draft("vqd_mixed", variants=_vq_variants("res_bob", "res_ghost"))
            )
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


def _report_args(db_url=None, *, kind=None, as_json=False, output=None):
    return SimpleNamespace(db_url=db_url, kind=kind, as_json=as_json, output=output)


def _migrate_args(
    path,
    kind="course-generation",
    db_url=None,
    *,
    draft_id=None,
    ids_file=None,
    to=None,
    yes=False,
):
    return SimpleNamespace(
        path=path,
        kind=kind,
        db_url=db_url,
        draft_id=draft_id,
        ids_file=ids_file,
        to=to,
        yes=yes,
    )


# --- 引用提取 ---------------------------------------------------------------


def test_extraction_walks_business_json_defensively() -> None:
    assert extract_course_generation_resource_ids(
        _cg_plan("res_b", "res_a", "res_b")
    ) == ["res_a", "res_b"]
    assert extract_course_generation_resource_ids({}) == []
    assert extract_course_generation_resource_ids(None) == []
    assert extract_course_generation_resource_ids({"lessons": "corrupt"}) == []
    assert extract_course_generation_resource_ids(
        {"lessons": [{"resources": [{"resource_id": 42}]}, "corrupt"]}
    ) == []

    assert extract_variant_question_resource_ids(
        _vq_variants("res_b", "res_a", "res_b")
    ) == ["res_a", "res_b"]
    assert extract_variant_question_resource_ids([]) == []
    assert extract_variant_question_resource_ids(None) == []
    assert extract_variant_question_resource_ids(["corrupt"]) == []
    assert extract_variant_question_resource_ids(
        [{"evidence": {"resource_id": None}}, {"evidence": "corrupt"}]
    ) == []


# --- 只读归属报告 -----------------------------------------------------------


def test_report_classifies_drafts_and_aggregates(tmp_path) -> None:
    report = asyncio.run(build_draft_owner_report(_make_db(tmp_path)))
    entries = {entry["draft_id"]: entry for entry in report["drafts"]}
    assert set(entries) == {
        "cgd_single",
        "cgd_multi",
        "cgd_null_owner",
        "cgd_missing",
        "cgd_norefs",
        "vqd_single",
        "vqd_mixed",
    }
    assert report["scope"] == {
        "owner_id": None,
        "kinds": ["course-generation", "variant-question"],
        "read_only": True,
    }

    single = entries["cgd_single"]
    assert single["kind"] == "course-generation"
    assert single["status"] == "pending_review"
    assert single["created_at"] == NOW.isoformat()
    assert single["reviewed_at"] is None
    assert single["referenced_resource_ids"] == ["res_alice"]
    assert single["resource_owner_ids"] == ["u_alice"]
    assert single["missing_resource_ids"] == []
    assert single["suggested_path"] == "assign_owner"
    assert single["suggested_owner_id"] == "u_alice"
    assert single["chapter_count"] == 1
    assert single["lesson_count"] == 1
    assert single["goal"] == "学会 cgd_single"
    assert single["dag_version"] == 3

    # approved 终态带审核时间；已归属行（cgd_owned）不在范围。
    assert entries["cgd_null_owner"]["reviewed_at"] == REVIEWED.isoformat()

    multi = entries["cgd_multi"]
    assert multi["suggested_path"] == "manual_review"
    assert multi["suggested_owner_id"] is None
    assert multi["resource_owner_ids"] == ["u_alice", "u_bob"]
    assert "不同用户" in multi["suggestion_reason"]

    null_owner = entries["cgd_null_owner"]
    assert null_owner["suggested_path"] == "manual_review"
    assert null_owner["unowned_resource_count"] == 1
    assert "无归属" in null_owner["suggestion_reason"]

    missing = entries["cgd_missing"]
    assert missing["suggested_path"] == "manual_review"
    assert missing["missing_resource_ids"] == ["res_ghost"]
    assert "不存在或已删除" in missing["suggestion_reason"]

    norefs = entries["cgd_norefs"]
    assert norefs["suggested_path"] == "manual_review"
    assert norefs["referenced_resource_ids"] == []
    assert "无归属锚" in norefs["suggestion_reason"]

    vqd = entries["vqd_single"]
    assert vqd["kind"] == "variant-question"
    # 同一资源被两个变式引用：去重后只算一个。
    assert vqd["referenced_resource_ids"] == ["res_alice"]
    assert vqd["referenced_resource_count"] == 1
    assert vqd["variant_count"] == 2
    assert vqd["suggested_path"] == "assign_owner"
    assert vqd["suggested_owner_id"] == "u_alice"

    vqd_mixed = entries["vqd_mixed"]
    assert vqd_mixed["suggested_path"] == "manual_review"
    assert vqd_mixed["missing_resource_ids"] == ["res_ghost"]

    by_kind = report["summary"]["by_kind"]
    cg = by_kind["course-generation"]
    assert cg["total"] == 5
    assert cg["by_status"] == {
        "approved": 1,
        "pending_review": 3,
        "rejected": 1,
    }
    assert cg["by_suggested_path"] == {"assign_owner": 1, "manual_review": 4}
    assert cg["distinct_referenced_resources"] == 4
    assert cg["missing_resources"] == 1
    assert cg["unowned_referenced_resources"] == 1
    assert cg["referenced_resources_by_owner"] == {"u_alice": 1, "u_bob": 1}
    assert cg["assignable_drafts_by_owner"] == {"u_alice": 1}

    vq = by_kind["variant-question"]
    assert vq["total"] == 2
    assert vq["by_suggested_path"] == {"assign_owner": 1, "manual_review": 1}
    assert vq["distinct_referenced_resources"] == 3
    assert vq["missing_resources"] == 1
    assert vq["unowned_referenced_resources"] == 0
    assert vq["assignable_drafts_by_owner"] == {"u_alice": 1}

    assert report["summary"]["total"] == 7
    assert len(report["notes"]) == 2


def test_report_kind_filter_reports_only_requested_kind(tmp_path) -> None:
    db_url = _make_db(tmp_path)
    report = asyncio.run(build_draft_owner_report(db_url, ["variant-question"]))
    kinds = {entry["kind"] for entry in report["drafts"]}
    assert kinds == {"variant-question"}
    assert report["scope"]["kinds"] == ["variant-question"]
    assert set(report["summary"]["by_kind"]) == {"variant-question"}

    with pytest.raises(ValueError, match="未知草稿类型"):
        asyncio.run(build_draft_owner_report(db_url, ["paper-question"]))


def test_report_is_read_only_and_summary_hides_production_ids(tmp_path) -> None:
    db_url = _make_db(tmp_path)
    report = asyncio.run(build_draft_owner_report(db_url))
    summary_text = format_draft_owner_report_summary(report)
    for draft_id in ("cgd_single", "vqd_single"):
        assert draft_id not in summary_text
    assert "[course-generation] 总数: 5" in summary_text
    assert "[variant-question] 总数: 2" in summary_text
    assert "不自动猜测用户" in summary_text
    # 数据库零写入：行数不变、无审计、资源归属不动。
    assert len(_fetch(db_url, CourseGenerationDraftRow)) == 6
    assert len(_fetch(db_url, VariantQuestionDraftRow)) == 3
    assert _fetch(db_url, AuditLogRow) == []
    resources = {row.id: row for row in _fetch(db_url, ResourceRow)}
    assert resources["res_public"].owner_id is None


def test_cli_report_summary_json_and_output_safety(tmp_path, capsys) -> None:
    db_url = _make_db(tmp_path)

    assert cli_module._run_draft_owner_report(_report_args(db_url)) == 0
    out = capsys.readouterr().out
    assert "历史无归属生成/变式草稿报告" in out
    assert "cgd_single" not in out

    assert (
        cli_module._run_draft_owner_report(
            _report_args(db_url, kind="variant-question", as_json=True)
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert {entry["kind"] for entry in payload["drafts"]} == {"variant-question"}

    unsafe = tmp_path / "report.json"
    assert (
        cli_module._run_draft_owner_report(_report_args(db_url, output=str(unsafe)))
        == 2
    )
    assert not unsafe.exists()

    safe = tmp_path / "artifacts" / "report.json"
    assert (
        cli_module._run_draft_owner_report(_report_args(db_url, output=str(safe)))
        == 0
    )
    written = json.loads(safe.read_text(encoding="utf-8"))
    assert {entry["draft_id"] for entry in written["drafts"]} == {
        "cgd_single",
        "cgd_multi",
        "cgd_null_owner",
        "cgd_missing",
        "cgd_norefs",
        "vqd_single",
        "vqd_mixed",
    }


def test_main_dispatches_draft_owner_report(tmp_path, capsys, monkeypatch) -> None:
    db_url = _make_db(tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["cli", "draft-owner-report", "--db-url", db_url]
    )
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
    assert "[course-generation] 总数: 5" in capsys.readouterr().out


# --- 精确 ID 解析与 CLI 门禁 -------------------------------------------------


def test_resolve_draft_ids_validates_literals(tmp_path) -> None:
    assert resolve_draft_ids(["cgd_a", "cgd_a"], None) == ["cgd_a"]
    with pytest.raises(ValueError):
        resolve_draft_ids(["cgd_%"], None)
    with pytest.raises(ValueError):
        resolve_draft_ids(["*"], None)
    with pytest.raises(ValueError):
        resolve_draft_ids(["x" * 65], None)
    with pytest.raises(ValueError):
        resolve_draft_ids([], None)

    ids_file = tmp_path / "ids.txt"
    ids_file.write_text(
        "# 注释行\n\n  cgd_single  \ncgd_multi\n", encoding="utf-8"
    )
    assert resolve_draft_ids(None, ids_file) == ["cgd_single", "cgd_multi"]

    # 通配符经 CLI 入口稳定 exit 2，不触碰数据库。
    assert (
        cli_module._run_draft_owner_migrate(
            _migrate_args(
                "assign-owner",
                db_url="sqlite+aiosqlite:///:memory:",
                draft_id=["cgd_%"],
                to="alice_draft",
            )
        )
        == 2
    )


def test_cli_fails_closed_without_db(tmp_path, capsys) -> None:
    assert cli_module._run_draft_owner_report(_report_args()) == 2
    assert "fail-closed" in capsys.readouterr().out
    assert (
        cli_module._run_draft_owner_migrate(
            _migrate_args("keep-unowned", draft_id=["cgd_single"])
        )
        == 2
    )
    assert "fail-closed" in capsys.readouterr().out


def test_cli_requires_to_for_assign_owner(tmp_path, capsys) -> None:
    db_url = _make_db(tmp_path)
    assert (
        cli_module._run_draft_owner_migrate(
            _migrate_args("assign-owner", db_url=db_url, draft_id=["cgd_single"])
        )
        == 2
    )
    assert "--to" in capsys.readouterr().out


def test_cli_rejects_unknown_kind(tmp_path, monkeypatch, capsys) -> None:
    db_url = _make_db(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "draft-owner-migrate",
            "assign-owner",
            "--kind",
            "paper-question",
            "--db-url",
            db_url,
            "--draft-id",
            "cgd_single",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 2  # argparse choices 拒绝未知 kind


# --- 迁移：dry-run / 无效 ID -------------------------------------------------


def test_dry_run_never_writes(tmp_path) -> None:
    db_url = _make_db(tmp_path)
    for path in ("assign-owner", "keep-unowned"):
        plan = asyncio.run(
            run_draft_migrate(
                db_url,
                "course-generation",
                path,
                ["cgd_single"],
                execute=False,
                owner_ref="alice_draft",
            )
        )
        assert plan["dry_run"] is True
        assert plan["executed"] is False
        assert plan["exit_code"] == 0
        assert plan["eligible_draft_ids"] == ["cgd_single"]
    assert _fetch(db_url, CourseGenerationDraftRow, CourseGenerationDraftRow.id == "cgd_single")[
        0
    ].owner_id is None
    assert _fetch(db_url, AuditLogRow) == []


def test_cli_missing_yes_refuses_execution(tmp_path, capsys) -> None:
    db_url = _make_db(tmp_path)
    code = cli_module._run_draft_owner_migrate(
        _migrate_args(
            "assign-owner",
            db_url=db_url,
            draft_id=["cgd_single"],
            to="alice_draft",
        )
    )
    assert code == 0
    assert "[dry-run]" in capsys.readouterr().out
    assert (
        _fetch(db_url, CourseGenerationDraftRow, CourseGenerationDraftRow.id == "cgd_single")[
            0
        ].owner_id
        is None
    )
    assert _fetch(db_url, AuditLogRow) == []


def test_unknown_and_cross_kind_ids_abort_whole_operation(tmp_path) -> None:
    db_url = _make_db(tmp_path)
    # 跨 kind：variant 草稿 ID 在 course-generation 表中不存在 → 整体拒绝。
    cross = asyncio.run(
        run_draft_migrate(
            db_url,
            "course-generation",
            "assign-owner",
            ["cgd_single", "vqd_single"],
            execute=True,
            owner_ref="alice_draft",
        )
    )
    assert cross["executed"] is False
    assert cross["exit_code"] == 1
    assert cross["invalid_draft_ids"] == ["vqd_single"]
    assert "另一 kind" in cross["failure"]
    assert (
        _fetch(db_url, CourseGenerationDraftRow, CourseGenerationDraftRow.id == "cgd_single")[
            0
        ].owner_id
        is None
    )
    assert _fetch(db_url, AuditLogRow) == []

    ghost = asyncio.run(
        run_draft_migrate(
            db_url,
            "variant-question",
            "keep-unowned",
            ["vqd_single", "vqd_ghost"],
            execute=True,
        )
    )
    assert ghost["executed"] is False
    assert ghost["exit_code"] == 1
    assert ghost["invalid_draft_ids"] == ["vqd_ghost"]
    assert _fetch(db_url, AuditLogRow) == []


# --- assign-owner ------------------------------------------------------------


def test_assign_owner_updates_rows_and_audits(tmp_path) -> None:
    db_url = _make_db(tmp_path)
    before = {
        row.id: row
        for row in _fetch(db_url, CourseGenerationDraftRow)
    }
    report = asyncio.run(
        run_draft_migrate(
            db_url,
            "course-generation",
            "assign-owner",
            ["cgd_single", "cgd_owned"],
            execute=True,
            owner_ref="alice_draft",
        )
    )
    assert report["executed"] is True
    assert report["exit_code"] == 0
    assert report["eligible_draft_ids"] == ["cgd_single"]
    assert report["drafts_modified"] == 1
    assert {item["draft_id"]: item["reason"] for item in report["skipped"]} == {
        "cgd_owned": "已有归属（owner_id 非 NULL），不在治理范围"
    }

    rows = {row.id: row for row in _fetch(db_url, CourseGenerationDraftRow)}
    assert rows["cgd_single"].owner_id == "u_alice"
    assert rows["cgd_owned"].owner_id == "u_bob"  # 已归属行不被改写
    # 只改 owner_id：status / 业务 JSON / 审核痕迹原样保留。
    assert rows["cgd_single"].status == before["cgd_single"].status
    assert rows["cgd_single"].plan == before["cgd_single"].plan
    assert rows["cgd_single"].goal == before["cgd_single"].goal
    assert rows["cgd_single"].reviewed_at == before["cgd_single"].reviewed_at

    audits = _fetch(db_url, AuditLogRow)
    assert len(audits) == 1
    audit = audits[0]
    assert audit.action == "ops.draft_owner.assign_owner"
    assert audit.target_type == "course_generation_drafts"
    assert audit.target_id == "cgd_single"
    assert audit.actor_username == "cli-operator"
    assert audit.request_id.startswith("cli-")
    assert audit.before == {
        "kind": "course-generation",
        "owner_id": None,
        "draft_ids": ["cgd_single"],
    }
    assert audit.after["kind"] == "course-generation"
    assert audit.after["owner_id"] == "u_alice"
    assert audit.after["username"] == "alice_draft"
    assert audit.after["draft_ids"] == ["cgd_single"]
    assert audit.after["drafts_modified"] == 1


def test_assign_owner_accepts_user_id_and_rejects_unknown_user(tmp_path) -> None:
    db_url = _make_db(tmp_path)
    by_id = asyncio.run(
        run_draft_migrate(
            db_url,
            "variant-question",
            "assign-owner",
            ["vqd_single"],
            execute=True,
            owner_ref="u_bob",
        )
    )
    assert by_id["exit_code"] == 0
    assert (
        _fetch(db_url, VariantQuestionDraftRow, VariantQuestionDraftRow.id == "vqd_single")[
            0
        ].owner_id
        == "u_bob"
    )

    unknown = asyncio.run(
        run_draft_migrate(
            db_url,
            "variant-question",
            "assign-owner",
            ["vqd_mixed"],
            execute=True,
            owner_ref="ghost_user",
        )
    )
    assert unknown["executed"] is False
    assert unknown["exit_code"] == 1
    assert "目标用户不存在" in unknown["failure"]
    assert (
        _fetch(db_url, VariantQuestionDraftRow, VariantQuestionDraftRow.id == "vqd_mixed")[
            0
        ].owner_id
        is None
    )
    assert len(_fetch(db_url, AuditLogRow)) == 1  # 仅第一次成功迁移写审计


def test_assign_owner_target_user_vanishing_in_transaction_rolls_back(
    tmp_path, monkeypatch
) -> None:
    """解析与写入事务之间的并发窗口：目标用户在事务复核时消失 → 回滚不审计。"""
    db_url = _make_db(tmp_path)
    original = ownership._resolve_target_user
    calls = {"count": 0}

    async def vanishing_user(session, reference, *, for_update=False):
        calls["count"] += 1
        if for_update:
            return None  # 事务内复核：目标用户已被并发删除
        return await original(session, reference)

    monkeypatch.setattr(ownership, "_resolve_target_user", vanishing_user)
    with pytest.raises(RuntimeError, match="事务复核"):
        asyncio.run(
            run_draft_migrate(
                db_url,
                "course-generation",
                "assign-owner",
                ["cgd_single"],
                execute=True,
                owner_ref="alice_draft",
            )
        )
    assert calls["count"] == 2  # 事务外解析 + 事务内锁定复核
    assert (
        _fetch(db_url, CourseGenerationDraftRow, CourseGenerationDraftRow.id == "cgd_single")[
            0
        ].owner_id
        is None
    )
    assert _fetch(db_url, AuditLogRow) == []


def test_audit_failure_rolls_back_business_update(tmp_path, monkeypatch) -> None:
    """审计写入失败 → 同事务业务更新一并回滚（fail-closed）。"""
    db_url = _make_db(tmp_path)

    async def broken_audit(session, payload, *, clock):
        raise RuntimeError("audit sink down")

    monkeypatch.setattr(ownership, "append_audit", broken_audit)
    with pytest.raises(RuntimeError, match="audit sink down"):
        asyncio.run(
            run_draft_migrate(
                db_url,
                "course-generation",
                "assign-owner",
                ["cgd_single"],
                execute=True,
                owner_ref="alice_draft",
            )
        )
    assert (
        _fetch(db_url, CourseGenerationDraftRow, CourseGenerationDraftRow.id == "cgd_single")[
            0
        ].owner_id
        is None
    )
    assert _fetch(db_url, AuditLogRow) == []


# --- 行状态漂移：事务复核回滚 -------------------------------------------------


def test_row_count_mismatch_rolls_back(tmp_path, monkeypatch) -> None:
    db_url = _make_db(tmp_path)
    original = ownership._load_draft_states

    async def stale_states(session, kind, draft_ids):
        states = await original(session, kind, draft_ids)
        # 模拟陈旧计划：包含一条数据库中不存在的行。
        states["cgd_ghost"] = {"status": "pending_review", "owner_id": None}
        return states

    monkeypatch.setattr(ownership, "_load_draft_states", stale_states)
    with pytest.raises(RuntimeError, match="不一致"):
        asyncio.run(
            run_draft_migrate(
                db_url,
                "course-generation",
                "assign-owner",
                ["cgd_single", "cgd_ghost"],
                execute=True,
                owner_ref="alice_draft",
            )
        )
    assert (
        _fetch(db_url, CourseGenerationDraftRow, CourseGenerationDraftRow.id == "cgd_single")[
            0
        ].owner_id
        is None
    )
    assert _fetch(db_url, AuditLogRow) == []


def test_owner_drift_in_transaction_rolls_back(tmp_path, monkeypatch) -> None:
    """计划认为 owner 为 NULL、执行时行已被并发归属 → 复核失败整体回滚。"""
    db_url = _make_db(tmp_path)
    original = ownership._load_draft_states

    async def drifted_states(session, kind, draft_ids):
        states = await original(session, kind, draft_ids)
        states["cgd_owned"]["owner_id"] = None  # 谎报：库里实际是 u_bob
        return states

    monkeypatch.setattr(ownership, "_load_draft_states", drifted_states)
    with pytest.raises(RuntimeError, match="owner 已变化"):
        asyncio.run(
            run_draft_migrate(
                db_url,
                "course-generation",
                "assign-owner",
                ["cgd_owned"],
                execute=True,
                owner_ref="alice_draft",
            )
        )
    assert (
        _fetch(db_url, CourseGenerationDraftRow, CourseGenerationDraftRow.id == "cgd_owned")[
            0
        ].owner_id
        == "u_bob"
    )
    assert _fetch(db_url, AuditLogRow) == []


def test_keep_unowned_stale_plan_rolls_back_without_audit(
    tmp_path, monkeypatch
) -> None:
    db_url = _make_db(tmp_path)
    original = ownership._load_draft_states

    async def stale_states(session, kind, draft_ids):
        states = await original(session, kind, draft_ids)
        states["cgd_ghost"] = {"status": "pending_review", "owner_id": None}
        return states

    monkeypatch.setattr(ownership, "_load_draft_states", stale_states)
    with pytest.raises(RuntimeError, match="不一致"):
        asyncio.run(
            run_draft_migrate(
                db_url,
                "course-generation",
                "keep-unowned",
                ["cgd_single", "cgd_ghost"],
                execute=True,
            )
        )
    assert (
        _fetch(db_url, CourseGenerationDraftRow, CourseGenerationDraftRow.id == "cgd_single")[
            0
        ].owner_id
        is None
    )
    assert _fetch(db_url, AuditLogRow) == []


# --- keep-unowned ------------------------------------------------------------


def test_keep_unowned_records_decision_without_touching_drafts(tmp_path) -> None:
    db_url = _make_db(tmp_path)
    before = {
        row.id: row for row in _fetch(db_url, VariantQuestionDraftRow)
    }
    report = asyncio.run(
        run_draft_migrate(
            db_url,
            "variant-question",
            "keep-unowned",
            ["vqd_single", "vqd_mixed"],
            execute=True,
        )
    )
    assert report["executed"] is True
    assert report["drafts_modified"] == 0
    assert report["audit_action"] == "ops.draft_owner.keep_unowned"

    rows = {row.id: row for row in _fetch(db_url, VariantQuestionDraftRow)}
    for draft_id in ("vqd_single", "vqd_mixed"):
        assert rows[draft_id].owner_id is None
        assert rows[draft_id].status == before[draft_id].status
        assert rows[draft_id].variants == before[draft_id].variants

    audits = _fetch(db_url, AuditLogRow)
    assert len(audits) == 1
    audit = audits[0]
    assert audit.action == "ops.draft_owner.keep_unowned"
    assert audit.target_type == "variant_question_drafts"
    assert audit.before == {
        "kind": "variant-question",
        "owner_id": None,
        "draft_ids": ["vqd_single", "vqd_mixed"],
    }
    assert audit.after["kind"] == "variant-question"
    assert audit.after["decision"] == "keep_unowned"
    assert audit.after["drafts_modified"] == 0


def test_cli_assign_owner_via_ids_file(tmp_path, capsys) -> None:
    db_url = _make_db(tmp_path)
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text(
        "# 选中治理的草稿\ncgd_single\n\n", encoding="utf-8"
    )
    code = cli_module._run_draft_owner_migrate(
        _migrate_args(
            "assign-owner",
            db_url=db_url,
            ids_file=str(ids_file),
            to="alice_draft",
            yes=True,
        )
    )
    assert code == 0
    body = json.loads(capsys.readouterr().out.split("\n[dry-run]")[0])
    assert body["executed"] is True
    assert (
        _fetch(db_url, CourseGenerationDraftRow, CourseGenerationDraftRow.id == "cgd_single")[
            0
        ].owner_id
        == "u_alice"
    )
    assert len(_fetch(db_url, AuditLogRow)) == 1
