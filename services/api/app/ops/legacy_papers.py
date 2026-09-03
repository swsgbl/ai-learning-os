"""M10-04 legacy paper governance: read-only report + exact-ID migration.

Scope is papers with owner_id IS NULL and source != seed. The report never
writes to the database and never widens visibility. Migration paths
(keep-public / assign-owner / export-delete) are dry-run by default, accept
only exact paper IDs supplied by the operator (unknown IDs abort the whole
operation so a stale list cannot silently shrink or widen the blast radius),
re-verify row state inside the executing transaction, and refuse to delete
papers referenced by historical exams. Every executed path appends an audit
row in the same transaction (append-only semantics preserved).

export-delete is additionally evidence-first: the JSONL archive is created
exclusively (never overwrites an existing file, symlinks included), validated
as a full archive against the in-memory pre-export snapshot (deterministic
JSON equality of paper + questions fields, exact ID set, no missing/duplicate/
tampered lines), and the delete transaction re-verifies that the question rows
it is about to delete still match the verified archive byte-for-byte.
"""
from __future__ import annotations

import json
import os
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
    session: AsyncSession, reference: str, *, for_update: bool = False
) -> dict[str, str] | None:
    """Exact match on user id or username; never fuzzy.

    for_update=True 在当前事务内锁定用户行（PG FOR UPDATE；SQLite 忽略由库级
    锁兜底），供写事务在更新 papers 前复核目标用户仍然存在，消除“解析成功后
    目标被并发删除”的窗口。
    """

    def _stmt(where: Any) -> Any:
        statement = select(UserRow).where(where)
        return statement.with_for_update() if for_update else statement

    row = (await session.scalars(_stmt(UserRow.id == reference))).first()
    if row is None:
        row = (await session.scalars(_stmt(UserRow.username == reference))).first()
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


def _paper_payload(paper: PaperRow, questions: Sequence[QuestionRow]) -> dict[str, Any]:
    """导出记录的确定性内容部分（不含 exported_at 时间戳）。

    导出快照与删除事务内的复查共用此函数，保证“档案内容”与“即将删除的
    内容”按同一形态比较。
    """
    return {
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
            for question in questions
        ],
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
                # 末位追加 id，保证同 sort_order 行的顺序也确定。
                .order_by(QuestionRow.paper_id, QuestionRow.sort_order, QuestionRow.id)
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
                **_paper_payload(paper, grouped[paper.id]),
            }
        )
    return lines, counts


def _canonical(value: Any) -> str:
    """确定性 JSON 形态：键排序 + 紧凑分隔，供全等比较。"""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _expected_payload_canon(records: Sequence[dict[str, Any]]) -> dict[str, str]:
    """导出记录（含 exported_at）→ 每张卷归档内容的 canonical 形态。"""
    canon: dict[str, str] = {}
    for record in records:
        paper_id = record.get("paper", {}).get("id")
        if isinstance(paper_id, str):
            canon[paper_id] = _canonical(
                {"paper": record.get("paper"), "questions": record.get("questions")}
            )
    return canon


def _write_export_file(path: Path, records: Sequence[dict[str, Any]]) -> None:
    """排他创建导出档案；目标已存在（含 symlink / dangling symlink）一律拒绝。

    - O_CREAT|O_EXCL 保证绝不覆盖既有文件；POSIX 另加 O_NOFOLLOW 防 symlink
      竞态。Windows CRT 的 O_EXCL 会跟随 symlink 建到目标路径，故创建后再
      复核路径本身不是符号链接（是则视为已存在，fail-closed 拒绝写入）。
    - 写入后 fsync：删除事务提交前归档证据必须已落盘。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path):
        raise FileExistsError(f"导出文件已存在，拒绝覆盖历史证据: {path}")
    payload = "".join(
        json.dumps(record, ensure_ascii=False) + "\n" for record in records
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    handle = os.fdopen(os.open(path, flags), "w", encoding="utf-8", newline="\n")
    try:
        if os.path.islink(path):
            raise FileExistsError(
                f"导出路径在创建时是符号链接，拒绝写入（Windows 竞态兜底）: {path}"
            )
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()


def _record_diff(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """比较两份导出记录的差异字段（exported_at 时间戳除外全等）。"""
    diffs: list[str] = []
    exp_paper = expected.get("paper")
    act_paper = actual.get("paper")
    if not isinstance(exp_paper, dict) or not isinstance(act_paper, dict):
        return ["paper"]
    for field in sorted(set(exp_paper) | set(act_paper)):
        if _canonical(exp_paper.get(field)) != _canonical(act_paper.get(field)):
            diffs.append(f"paper.{field}")
    if _canonical(expected.get("questions")) != _canonical(actual.get("questions")):
        diffs.append("questions")
    return diffs


def validate_export_file(
    path: Path, expected_records: Sequence[dict[str, Any]]
) -> list[str]:
    """回读导出 JSONL 做「完整归档校验」；返回 problems（空列表 = 可信）。

    以内存 snapshot 为期望值：逐行解析后与期望记录做确定性全等比较（paper
    全字段 + questions 全字段，JSON canonical 比较），并继续校验 paper ID 精确
    集合与缺行/重复/错行。malformed paper.id（非字符串、空值、不可哈希对象等）
    一律计入 problems，绝不抛未捕获 TypeError。
    """
    problems: list[str] = []
    seen: list[str] = []
    parsed: dict[str, dict[str, Any]] = {}
    expected: dict[str, dict[str, Any]] = {}
    for record in expected_records:
        paper = record.get("paper")
        if isinstance(paper, dict) and isinstance(paper.get("id"), str):
            expected[paper["id"]] = record
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as cause:
        return [f"无法读取导出文件: {cause}"]
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as cause:
            problems.append(f"第 {number} 行无法解析为 JSON: {cause}")
            continue
        if not isinstance(record, dict):
            problems.append(f"第 {number} 行不是试卷记录对象")
            continue
        paper = record.get("paper")
        if not isinstance(paper, dict):
            problems.append(f"第 {number} 行缺少 paper 对象")
            continue
        paper_id = paper.get("id")
        if not isinstance(paper_id, str) or not paper_id:
            problems.append(
                f"第 {number} 行 paper.id 非法（必须是非空字符串）: {paper_id!r}"
            )
            continue
        seen.append(paper_id)
        parsed.setdefault(paper_id, record)
    if len(seen) != len(set(seen)):
        problems.append("导出文件存在重复 paper 记录")
    expected_ids = set(expected)
    actual_ids = set(seen)
    missing = sorted(expected_ids - actual_ids)
    extra = sorted(actual_ids - expected_ids)
    if missing:
        problems.append(f"导出缺少 {len(missing)} 张试卷: {missing[:5]}")
    if extra:
        problems.append(f"导出多出 {len(extra)} 张试卷: {extra[:5]}")
    for paper_id in sorted(expected_ids & actual_ids):
        diffs = _record_diff(expected[paper_id], parsed[paper_id])
        if diffs:
            problems.append(
                f"试卷 {paper_id} 导出内容与快照不一致: {', '.join(diffs[:5])}"
            )
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
                    # 审计必须与事实一致：先锁定并复核 eligible 行仍存在且
                    # owner/source 与计划一致，陈旧计划整体回滚、不写审计。
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
                    # 同一事务内锁定并复核目标用户仍存在，消除“解析成功后
                    # 目标被并发删除”的窗口；目标消失整体回滚、不写审计。
                    target = await _resolve_target_user(
                        session, owner_ref or "", for_update=True
                    )
                    if target is None:
                        raise RuntimeError(
                            f"目标用户 {owner_ref} 在事务复核时不存在，事务回滚"
                        )
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

            # export-delete：先排他导出并做完整归档校验，校验通过才进入删除
            # 事务；删除事务内再次证明“即将删除的题目 == 已验证档案”。
            assert export_path is not None
            if os.path.lexists(export_path):
                plan.update(
                    {
                        "executed": False,
                        "export_path": str(export_path),
                        "failure": (
                            f"导出文件已存在，拒绝覆盖历史证据（未删除任何数据库行）: "
                            f"{export_path}"
                        ),
                        "exit_code": 1,
                    }
                )
                return plan
            records, question_counts = await _snapshot_for_export(session, eligible)
            expected_payloads = _expected_payload_canon(records)
            # 结束快照读事务：写文件/校验期间不持有任何库级锁。
            await session.rollback()
            try:
                _write_export_file(export_path, records)
            except FileExistsError as cause:
                plan.update(
                    {
                        "executed": False,
                        "export_path": str(export_path),
                        "failure": f"导出文件已存在，拒绝覆盖（未删除任何数据库行）: {cause}",
                        "exit_code": 1,
                    }
                )
                return plan
            except OSError as cause:
                plan.update(
                    {
                        "executed": False,
                        "export_path": str(export_path),
                        "failure": f"导出文件写入失败，未删除任何数据库行: {cause}",
                        "exit_code": 1,
                    }
                )
                return plan
            problems = validate_export_file(export_path, records)
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
                # 复查当前题目快照与已验证导出档案一致：精确 ID 集合 + 完整记录
                # 全等。同数量换内容（行数巧合相等）也必须被拒绝。
                current_questions = (
                    (
                        await session.execute(
                            select(QuestionRow)
                            .where(QuestionRow.paper_id.in_(eligible))
                            .order_by(
                                QuestionRow.paper_id,
                                QuestionRow.sort_order,
                                QuestionRow.id,
                            )
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
                grouped: dict[str, list[QuestionRow]] = {pid: [] for pid in eligible}
                for question in current_questions:
                    grouped.setdefault(question.paper_id, []).append(question)
                current_payloads = {
                    row.id: _canonical(_paper_payload(row, grouped.get(row.id, [])))
                    for row in rows
                }
                if set(current_payloads) != set(eligible):
                    raise RuntimeError(
                        "当前试卷/题目集合与导出档案不一致，事务回滚（拒绝删除）"
                    )
                current_question_ids = {question.id for question in current_questions}
                expected_question_ids = {
                    question["id"]
                    for record in records
                    for question in record.get("questions") or []
                }
                if current_question_ids != expected_question_ids:
                    raise RuntimeError(
                        "当前题目 ID 集合与导出档案不一致，事务回滚（拒绝删除）"
                    )
                for paper_id in eligible:
                    if current_payloads[paper_id] != expected_payloads[paper_id]:
                        raise RuntimeError(
                            f"试卷 {paper_id} 当前内容与已验证导出档案不一致，"
                            "事务回滚（拒绝删除）"
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
