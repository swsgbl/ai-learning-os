"""M10-04 legacy paper governance: read-only report + exact-ID migration.

Scope is papers with owner_id IS NULL and source != seed. The report never
writes to the database and never widens visibility. Migration paths
(keep-public / assign-owner / export-delete) are dry-run by default, accept
only exact paper IDs supplied by the operator (unknown IDs abort the whole
operation so a stale list cannot silently shrink or widen the blast radius),
re-verify row state inside the executing transaction, and refuse to delete
papers referenced by historical exams. Every executed path appends an audit
row in the same transaction (append-only semantics preserved).
"""
from __future__ import annotations

import json
import subprocess
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import AuditLogRow, ExamSessionRow, PaperRow, QuestionRow, UserRow
from app.db.session import create_engine
from app.repositories.audit import audit_insert_values

SEED_SOURCE = "AI Learning OS seed"
MIGRATION_PATHS = ("keep-public", "assign-owner", "export-delete")
KEEP_PUBLIC = "keep_public"
ASSIGN_OWNER = "assign_owner"
EXPORT_REVIEW = "export_review"

_SKIP_SEED = "系统 seed 卷，不在治理范围（保持公共）"
_SKIP_OWNED = "已有归属（owner_id 非 NULL），不在治理范围"
_BLOCK_REFERENCED = "被历史考试引用，拒绝删除（需人工处理；不得级联删考试）"

TIMESTAMPS_NOTE = (
    "papers 表无 created_at/updated_at 列（schema 未引入时间戳），报告输出 null；"
    "时间证据以 exam_sessions.started_at 派生的首次/最近引用时间为准"
)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None


def _month_key(value: datetime | None) -> str:
    moment = _as_utc(value)
    return f"{moment.year:04d}-{moment.month:02d}" if moment else "unreferenced"


def _question_bucket(count: int) -> str:
    if count <= 0:
        return "0"
    if count <= 10:
        return "1-10"
    if count <= 30:
        return "11-30"
    if count <= 60:
        return "31-60"
    return "60+"


def _suggest(exam_count: int, question_count: int) -> tuple[str, str]:
    if exam_count > 0:
        return KEEP_PUBLIC, "被历史考试引用，保留公共以维持考试历史可读"
    if question_count > 0:
        return ASSIGN_OWNER, "无考试引用且有内容，建议归属维护者后转为私有"
    return EXPORT_REVIEW, "无考试引用且无题目，建议导出审查后删除"


# --- 只读分类报告 -----------------------------------------------------------


async def build_legacy_paper_report(db_url: str) -> dict[str, Any]:
    """Classify non-seed NULL-owner papers. Read-only, never mutates the DB."""
    engine = create_engine(db_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            papers = (
                (
                    await session.execute(
                        select(PaperRow)
                        .where(PaperRow.owner_id.is_(None), PaperRow.source != SEED_SOURCE)
                        .order_by(PaperRow.id)
                    )
                )
                .scalars()
                .all()
            )
            paper_ids = [paper.id for paper in papers]
            question_stats: dict[str, tuple[int, float]] = {}
            exam_stats: dict[str, tuple[int, datetime | None, datetime | None]] = {}
            if paper_ids:
                for row in (
                    await session.execute(
                        select(
                            QuestionRow.paper_id,
                            func.count(QuestionRow.id),
                            func.coalesce(func.sum(QuestionRow.score), 0.0),
                        )
                        .where(QuestionRow.paper_id.in_(paper_ids))
                        .group_by(QuestionRow.paper_id)
                    )
                ).all():
                    question_stats[row[0]] = (int(row[1]), float(row[2]))
                for row in (
                    await session.execute(
                        select(
                            ExamSessionRow.paper_id,
                            func.count(ExamSessionRow.exam_id),
                            func.min(ExamSessionRow.started_at),
                            func.max(ExamSessionRow.started_at),
                        )
                        .where(ExamSessionRow.paper_id.in_(paper_ids))
                        .group_by(ExamSessionRow.paper_id)
                    )
                ).all():
                    exam_stats[row[0]] = (int(row[1]), _as_utc(row[2]), _as_utc(row[3]))

            entries: list[dict[str, Any]] = []
            for paper in papers:
                question_count, total_score = question_stats.get(paper.id, (0, 0.0))
                exam_count, first_seen, last_seen = exam_stats.get(paper.id, (0, None, None))
                suggested, reason = _suggest(exam_count, question_count)
                entries.append(
                    {
                        "paper_id": paper.id,
                        "title": paper.title,
                        "source": paper.source,
                        "created_at": None,
                        "updated_at": None,
                        "subject": paper.subject,
                        "year": paper.year,
                        "license": paper.license,
                        "origin_url": paper.origin_url,
                        "question_count": question_count,
                        "total_score": total_score,
                        "referenced_by_exams": exam_count > 0,
                        "exam_count": exam_count,
                        "first_exam_started_at": _iso(first_seen),
                        "last_exam_started_at": _iso(last_seen),
                        "suggested_path": suggested,
                        "suggestion_reason": reason,
                    }
                )
            return _assemble_report(entries)
    finally:
        await engine.dispose()


def _assemble_report(entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_source: dict[str, int] = {}
    by_path: dict[str, int] = {KEEP_PUBLIC: 0, ASSIGN_OWNER: 0, EXPORT_REVIEW: 0}
    by_bucket: dict[str, int] = {"0": 0, "1-10": 0, "11-30": 0, "31-60": 0, "60+": 0}
    by_month: dict[str, int] = {}
    referenced = 0
    total_questions = 0
    for entry in entries:
        by_source[entry["source"]] = by_source.get(entry["source"], 0) + 1
        by_path[entry["suggested_path"]] += 1
        by_bucket[_question_bucket(entry["question_count"])] += 1
        month = (
            _month_key(datetime.fromisoformat(entry["last_exam_started_at"]))
            if entry["last_exam_started_at"]
            else "unreferenced"
        )
        by_month[month] = by_month.get(month, 0) + 1
        if entry["referenced_by_exams"]:
            referenced += 1
        total_questions += entry["question_count"]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "owner_id": None,
            "exclude_source": SEED_SOURCE,
            "read_only": True,
        },
        "notes": [TIMESTAMPS_NOTE],
        "papers": entries,
        "summary": {
            "total": len(entries),
            "referenced": referenced,
            "unreferenced": len(entries) - referenced,
            "total_questions": total_questions,
            "by_suggested_path": by_path,
            "by_source": by_source,
            "by_question_count_bucket": by_bucket,
            "by_last_reference_month": dict(sorted(by_month.items())),
        },
    }


def format_report_summary(report: dict[str, Any]) -> str:
    """Human-readable aggregate summary; never prints production paper IDs."""
    summary = report["summary"]
    lines = [
        "历史无归属试卷分类报告（只读）",
        f"生成时间: {report['generated_at']}",
        (
            "范围: papers.owner_id IS NULL 且 source != "
            f"'{report['scope']['exclude_source']}'（不修改数据库）"
        ),
        (
            f"总数: {summary['total']}  被引用: {summary['referenced']}  "
            f"未引用: {summary['unreferenced']}  题目总数: {summary['total_questions']}"
        ),
    ]
    lines.append(
        "建议路径: "
        + "  ".join(
            f"{name}={count}" for name, count in summary["by_suggested_path"].items()
        )
    )
    lines.append(
        "来源分布: "
        + "  ".join(
            f"{name}={count}" for name, count in sorted(summary["by_source"].items())
        )
    )
    lines.append(
        "题量分布: "
        + "  ".join(
            f"{name}={count}"
            for name, count in summary["by_question_count_bucket"].items()
        )
    )
    lines.append(
        "最近引用月份分布: "
        + "  ".join(
            f"{name}={count}"
            for name, count in summary["by_last_reference_month"].items()
        )
    )
    lines.append(f"说明: {TIMESTAMPS_NOTE}")
    lines.append(
        "明细（含 paper_id）请用 --json 或 --output 写入 gitignore 的 artifacts/temp 目录。"
    )
    return "\n".join(lines)


# --- 精确 ID 解析与安全路径 -------------------------------------------------


def resolve_paper_ids(
    explicit: Sequence[str] | None, ids_file: str | Path | None
) -> list[str]:
    """Merge --paper-id and --ids-file into a deduplicated literal ID list."""
    raw = [value for value in (explicit or [])]
    if ids_file is not None:
        raw.extend(
            line.strip()
            for line in Path(ids_file).read_text(encoding="utf-8").splitlines()
        )
    ids: list[str] = []
    for value in raw:
        item = value.strip()
        if not item or item.startswith("#"):
            continue
        if "*" in item or "%" in item:
            raise ValueError("试卷 ID 必须是精确字面量，不得包含 * 或 %")
        if len(item) > 64:
            raise ValueError("试卷 ID 长度不得超过 64")
        if item not in ids:
            ids.append(item)
    if not ids:
        raise ValueError("至少提供一个精确试卷 ID（--paper-id 或 --ids-file）")
    return ids


def is_safe_artifact_path(path: str | Path) -> bool:
    """Report/export files hold production IDs: only gitignored locations.

    判定规则（fail-closed）：
    1. 直接父目录名是 artifacts/ 或 temp/（操作者显式选择隔离目录）；或
    2. git check-ignore 判定该路径被忽略（覆盖仓库内 artifacts/temp 子目录
       及其他 .gitignore 规则；仓库外路径 git 无法判定即拒绝）。
    注意只看直接父目录——Windows 的 %TEMP% 是任意路径的祖先，不能据此放行。
    """
    resolved = Path(path).resolve()
    if resolved.parent.name.lower() in ("artifacts", "temp"):
        return True
    repo_root = Path(__file__).resolve().parents[4]
    try:
        git = subprocess.run(
            ["git", "check-ignore", "--quiet", str(resolved)],
            cwd=str(repo_root),
            capture_output=True,
            check=False,
        )
    except OSError:
        return False
    return git.returncode == 0


# --- 迁移执行 ---------------------------------------------------------------


async def _load_states(
    session: AsyncSession, paper_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(PaperRow).where(PaperRow.id.in_(list(paper_ids)))
            )
        )
        .scalars()
        .all()
    )
    exam_counts: dict[str, int] = {}
    if rows:
        for row in (
            await session.execute(
                select(ExamSessionRow.paper_id, func.count(ExamSessionRow.exam_id))
                .where(ExamSessionRow.paper_id.in_([row.id for row in rows]))
                .group_by(ExamSessionRow.paper_id)
            )
        ).all():
            exam_counts[row[0]] = int(row[1])
    return {
        row.id: {
            "title": row.title,
            "source": row.source,
            "owner_id": row.owner_id,
            "exam_count": exam_counts.get(row.id, 0),
        }
        for row in rows
    }


async def _resolve_target_user(
    session: AsyncSession, reference: str
) -> dict[str, str] | None:
    """Exact match on user id or username; never fuzzy."""
    row = (
        await session.scalars(select(UserRow).where(UserRow.id == reference))
    ).first()
    if row is None:
        row = (
            await session.scalars(
                select(UserRow).where(UserRow.username == reference)
            )
        ).first()
    if row is None:
        return None
    return {"id": row.id, "username": row.username, "role": row.role}


def _audit_base(action: str, ids: Sequence[str]) -> dict[str, Any]:
    return {
        "action": action,
        "target_type": "papers",
        "target_id": ",".join(ids)[:128],
        "request_id": f"cli-{uuid.uuid4().hex[:12]}",
        "actor_id": "cli-operator",
        "actor_username": "cli-operator",
    }


async def _snapshot_for_export(
    session: AsyncSession, paper_ids: Sequence[str]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    papers = (
        (
            await session.execute(
                select(PaperRow).where(PaperRow.id.in_(list(paper_ids)))
            )
        )
        .scalars()
        .all()
    )
    questions = (
        (
            await session.execute(
                select(QuestionRow)
                .where(QuestionRow.paper_id.in_(list(paper_ids)))
                .order_by(QuestionRow.paper_id, QuestionRow.sort_order)
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[str, list[QuestionRow]] = {paper.id: [] for paper in papers}
    for question in questions:
        grouped.setdefault(question.paper_id, []).append(question)
    lines: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for paper in sorted(papers, key=lambda item: item.id):
        counts[paper.id] = len(grouped[paper.id])
        lines.append(
            {
                "exported_at": datetime.now(UTC).isoformat(),
                "paper": {
                    "id": paper.id,
                    "title": paper.title,
                    "subtitle": paper.subtitle,
                    "source": paper.source,
                    "university": paper.university,
                    "year": paper.year,
                    "subject": paper.subject,
                    "difficulty": paper.difficulty,
                    "duration_minutes": paper.duration_minutes,
                    "tags": paper.tags,
                    "origin_url": paper.origin_url,
                    "license": paper.license,
                    "owner_id": paper.owner_id,
                },
                "questions": [
                    {
                        "id": question.id,
                        "paper_id": question.paper_id,
                        "question_type": question.question_type,
                        "stem": question.stem,
                        "options": question.options,
                        "answer": question.answer,
                        "explanation": question.explanation,
                        "angles": question.angles,
                        "knowledge": question.knowledge,
                        "score": question.score,
                        "difficulty": question.difficulty,
                        "sort_order": question.sort_order,
                    }
                    for question in grouped[paper.id]
                ],
            }
        )
    return lines, counts


def _write_export_file(path: Path, records: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in records
    )
    path.write_text(payload, encoding="utf-8")


def validate_export_file(path: Path, expected_ids: Sequence[str]) -> list[str]:
    """Re-read the JSONL export; return problems (empty list = trustworthy)."""
    problems: list[str] = []
    seen: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as cause:
        return [f"无法读取导出文件: {cause}"]
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            seen.append(record["paper"]["id"])
        except (json.JSONDecodeError, KeyError, TypeError) as cause:
            problems.append(f"第 {number} 行无法解析为试卷记录: {cause}")
    expected = set(expected_ids)
    actual = set(seen)
    if len(seen) != len(actual):
        problems.append("导出文件存在重复 paper 记录")
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        problems.append(f"导出缺少 {len(missing)} 张试卷: {missing[:5]}")
    if extra:
        problems.append(f"导出多出 {len(extra)} 张试卷: {extra[:5]}")
    return problems


def _plan_skeleton(
    path: str, ids: list[str], *, dry_run: bool
) -> dict[str, Any]:
    return {
        "path": path,
        "dry_run": dry_run,
        "requested": len(ids),
        "paper_ids": ids,
        "invalid_paper_ids": [],
        "eligible_paper_ids": [],
        "skipped": [],
        "blocked_paper_ids": [],
    }


async def run_legacy_migrate(
    db_url: str,
    path: str,
    paper_ids: Sequence[str],
    *,
    execute: bool,
    owner_ref: str | None = None,
    export_path: Path | None = None,
) -> dict[str, Any]:
    """Plan or execute one migration path over exact paper IDs."""
    if path not in MIGRATION_PATHS:
        raise ValueError(f"未知迁移路径: {path}")
    ids = list(paper_ids)
    engine = create_engine(db_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            states = await _load_states(session, ids)
            plan = _plan_skeleton(path, ids, dry_run=not execute)
            invalid = [pid for pid in ids if pid not in states]
            skipped: list[dict[str, str]] = []
            eligible: list[str] = []
            for pid in ids:
                if pid not in states:
                    continue
                state = states[pid]
                if state["source"] == SEED_SOURCE:
                    skipped.append({"paper_id": pid, "reason": _SKIP_SEED})
                elif state["owner_id"] is not None:
                    skipped.append({"paper_id": pid, "reason": _SKIP_OWNED})
                else:
                    eligible.append(pid)
            blocked: list[dict[str, str]] = []
            if path == "export-delete":
                for pid in list(eligible):
                    if states[pid]["exam_count"] > 0:
                        eligible.remove(pid)
                        blocked.append({"paper_id": pid, "reason": _BLOCK_REFERENCED})
                skipped.extend(blocked)
            plan["invalid_paper_ids"] = invalid
            plan["eligible_paper_ids"] = eligible
            plan["skipped"] = skipped
            plan["blocked_paper_ids"] = [item["paper_id"] for item in blocked]
            if invalid:
                plan.update(
                    {
                        "executed": False,
                        "failure": f"未知试卷 ID {len(invalid)} 个，拒绝执行（精确行契约）",
                        "exit_code": 1,
                    }
                )
                return plan
            if not execute:
                plan.update({"executed": False, "exit_code": 0})
                return plan
            if not eligible:
                plan.update(
                    {
                        "executed": False,
                        "failure": (
                            "选中试卷全部被历史考试引用，拒绝删除（需人工处理）"
                            if blocked
                            else None
                        ),
                        "exit_code": 1 if blocked else 0,
                    }
                )
                return plan

            # 执行阶段独占一个显式事务；各分支在 begin 前统一结束读事务。
            if path == "keep-public":
                await session.rollback()
                async with session.begin():
                    session.add(
                        AuditLogRow(
                            **audit_insert_values(
                                {
                                    **_audit_base(
                                        "ops.legacy_paper.keep_public", eligible
                                    ),
                                    "before": {
                                        "owner_id": None,
                                        "paper_ids": eligible,
                                    },
                                    "after": {
                                        "decision": "keep_public",
                                        "paper_ids": eligible,
                                        "papers_modified": 0,
                                    },
                                },
                                clock=lambda: datetime.now(UTC),
                            )
                        )
                    )
                plan.update(
                    {
                        "executed": True,
                        "executed_count": 0,
                        "papers_modified": 0,
                        "audit_action": "ops.legacy_paper.keep_public",
                        "exit_code": 0,
                    }
                )
                return plan

            if path == "assign-owner":
                target = await _resolve_target_user(session, owner_ref or "")
                if target is None:
                    plan.update(
                        {
                            "executed": False,
                            "failure": f"目标用户不存在: {owner_ref}",
                            "exit_code": 1,
                        }
                    )
                    return plan
                await session.rollback()
                async with session.begin():
                    rows = (
                        (
                            await session.execute(
                                select(PaperRow)
                                .where(PaperRow.id.in_(eligible))
                                .with_for_update()
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if len(rows) != len(eligible):
                        raise RuntimeError(
                            f"锁定行数 {len(rows)} 与计划 {len(eligible)} 不一致，事务回滚"
                        )
                    for row in rows:
                        if row.owner_id is not None or row.source == SEED_SOURCE:
                            raise RuntimeError(
                                f"行 {row.id} 状态与计划不一致（owner/source 已变化），事务回滚"
                            )
                    result = await session.execute(
                        update(PaperRow)
                        .where(
                            PaperRow.id.in_(eligible), PaperRow.owner_id.is_(None)
                        )
                        .values(owner_id=target["id"])
                    )
                    if result.rowcount != len(eligible):
                        raise RuntimeError(
                            f"更新行数 {result.rowcount} 与计划 {len(eligible)} 不一致，事务回滚"
                        )
                    session.add(
                        AuditLogRow(
                            **audit_insert_values(
                                {
                                    **_audit_base(
                                        "ops.legacy_paper.assign_owner", eligible
                                    ),
                                    "before": {"owner_id": None, "paper_ids": eligible},
                                    "after": {
                                        "owner_id": target["id"],
                                        "username": target["username"],
                                        "paper_ids": eligible,
                                        "updated": result.rowcount,
                                    },
                                },
                                clock=lambda: datetime.now(UTC),
                            )
                        )
                    )
                plan.update(
                    {
                        "executed": True,
                        "executed_count": len(eligible),
                        "papers_modified": len(eligible),
                        "target_user": target,
                        "audit_action": "ops.legacy_paper.assign_owner",
                        "exit_code": 0,
                    }
                )
                return plan

            # export-delete：先导出并校验，校验通过才进入删除事务。
            assert export_path is not None
            records, question_counts = await _snapshot_for_export(session, eligible)
            _write_export_file(export_path, records)
            problems = validate_export_file(export_path, eligible)
            if problems:
                plan.update(
                    {
                        "executed": False,
                        "export_path": str(export_path),
                        "export_validation_problems": problems,
                        "failure": "导出校验失败，未删除任何数据库行",
                        "exit_code": 1,
                    }
                )
                return plan
            planned_questions = sum(question_counts.values())
            await session.rollback()
            async with session.begin():
                rows = (
                    (
                        await session.execute(
                            select(PaperRow)
                            .where(PaperRow.id.in_(eligible))
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
                if len(rows) != len(eligible):
                    raise RuntimeError(
                        f"锁定行数 {len(rows)} 与计划 {len(eligible)} 不一致，事务回滚"
                    )
                for row in rows:
                    if row.owner_id is not None or row.source == SEED_SOURCE:
                        raise RuntimeError(
                            f"行 {row.id} 状态与计划不一致（owner/source 已变化），事务回滚"
                        )
                references = await session.scalar(
                    select(func.count())
                    .select_from(ExamSessionRow)
                    .where(ExamSessionRow.paper_id.in_(eligible))
                )
                if int(references or 0) > 0:
                    raise RuntimeError(
                        f"仍有 {references} 条考试引用选中试卷，拒绝删除（不得级联删考试）"
                    )
                deleted_questions = await session.execute(
                    delete(QuestionRow).where(QuestionRow.paper_id.in_(eligible))
                )
                if deleted_questions.rowcount != planned_questions:
                    raise RuntimeError(
                        f"题目删除行数 {deleted_questions.rowcount} 与计划 "
                        f"{planned_questions} 不一致，事务回滚"
                    )
                deleted_papers = await session.execute(
                    delete(PaperRow).where(
                        PaperRow.id.in_(eligible), PaperRow.owner_id.is_(None)
                    )
                )
                if deleted_papers.rowcount != len(eligible):
                    raise RuntimeError(
                        f"试卷删除行数 {deleted_papers.rowcount} 与计划 "
                        f"{len(eligible)} 不一致，事务回滚"
                    )
                session.add(
                    AuditLogRow(
                        **audit_insert_values(
                            {
                                **_audit_base(
                                    "ops.legacy_paper.export_delete", eligible
                                ),
                                "before": {
                                    "paper_ids": eligible,
                                    "question_counts": question_counts,
                                },
                                "after": {
                                    "deleted_papers": deleted_papers.rowcount,
                                    "deleted_questions": deleted_questions.rowcount,
                                    "export_path": str(export_path),
                                },
                            },
                            clock=lambda: datetime.now(UTC),
                        )
                    )
                )
            plan.update(
                {
                    "executed": True,
                    "executed_count": len(eligible),
                    "papers_modified": len(eligible),
                    "deleted_questions": planned_questions,
                    "export_path": str(export_path),
                    "audit_action": "ops.legacy_paper.export_delete",
                    "exit_code": 0,
                }
            )
            return plan
    finally:
        await engine.dispose()

