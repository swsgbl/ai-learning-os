"""M10-04 generation/variant legacy draft ownership: report + exact-ID migrate.

Scope is course_generation_drafts / variant_question_drafts rows with
owner_id IS NULL. Unlike course_import / paper_question drafts these tables
have no direct resource foreign key anchor (0025_draft_ownership), so the
read-only report reconstructs references from the business JSON
(plan.lessons[].resources[].resource_id / variants[].evidence.resource_id)
and proposes an owner only when every referenced resource exists and is
owned by exactly the same non-NULL user; anything else (no references, NULL
owner, multiple owners, missing resources) is manual_review — never guess.

Migration paths (assign-owner / keep-unowned) are dry-run by default, accept
only exact draft IDs of the requested kind (unknown or cross-kind IDs abort
the whole operation), never touch status / business JSON / any other table,
and re-verify row state (owner still NULL, row count matches the plan)
inside the executing transaction before updating or auditing. Every
executed path appends the audit row in the same transaction; audit payloads
carry only governance necessities (kind, IDs, owner ID) — no keys, tokens,
or resource bodies.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import (
    AuditLogRow,
    CourseGenerationDraftRow,
    ResourceRow,
    UserRow,
    VariantQuestionDraftRow,
)
from app.db.session import create_engine
from app.repositories.audit import audit_insert_values

KIND_COURSE_GENERATION = "course-generation"
KIND_VARIANT_QUESTION = "variant-question"
DRAFT_KINDS: dict[str, type] = {
    KIND_COURSE_GENERATION: CourseGenerationDraftRow,
    KIND_VARIANT_QUESTION: VariantQuestionDraftRow,
}
MIGRATION_PATHS = ("assign-owner", "keep-unowned")

ASSIGN_OWNER = "assign_owner"
MANUAL_REVIEW = "manual_review"

_SKIP_OWNED = "已有归属（owner_id 非 NULL），不在治理范围"

EXTRACTION_NOTE = (
    "引用提取：course-generation 读 plan.lessons[].resources[].resource_id；"
    "variant-question 读 variants[].evidence.resource_id；均从业务 JSON 重构，"
    "非外键（0025 后两表无资源锚）"
)
SUGGESTION_NOTE = (
    "建议规则（确定性）：全部引用资源存在且同属唯一非 NULL owner 才建议 "
    "assign_owner；无引用/存在 NULL owner/多 owner/资源缺失一律 manual_review，"
    "不自动猜测用户"
)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None


# --- 引用提取（业务 JSON -> resource_id 列表） -------------------------------


def extract_course_generation_resource_ids(plan: Any) -> list[str]:
    """plan.lessons[].resources[].resource_id 去重升序；结构异常按无引用处理。"""
    found: set[str] = set()
    if not isinstance(plan, dict):
        return sorted(found)
    lessons = plan.get("lessons")
    if not isinstance(lessons, list):
        return sorted(found)
    for lesson in lessons:
        if not isinstance(lesson, dict):
            continue
        resources = lesson.get("resources")
        if not isinstance(resources, list):
            continue
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            resource_id = resource.get("resource_id")
            if isinstance(resource_id, str) and resource_id:
                found.add(resource_id)
    return sorted(found)


def extract_variant_question_resource_ids(variants: Any) -> list[str]:
    """variants[].evidence.resource_id 去重升序；结构异常按无引用处理。"""
    found: set[str] = set()
    if not isinstance(variants, list):
        return sorted(found)
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        evidence = variant.get("evidence")
        if not isinstance(evidence, dict):
            continue
        resource_id = evidence.get("resource_id")
        if isinstance(resource_id, str) and resource_id:
            found.add(resource_id)
    return sorted(found)


EXTRACTORS = {
    KIND_COURSE_GENERATION: extract_course_generation_resource_ids,
    KIND_VARIANT_QUESTION: extract_variant_question_resource_ids,
}


def _suggest(
    refs: Sequence[str], owner_by_resource: dict[str, str | None]
) -> tuple[str, str | None, str]:
    """确定性建议：唯一非 NULL owner 才 assign_owner，其余 manual_review。"""
    if not refs:
        return MANUAL_REVIEW, None, "草稿未引用任何资源（无归属锚），需人工判断"
    missing = [rid for rid in refs if rid not in owner_by_resource]
    if missing:
        return (
            MANUAL_REVIEW,
            None,
            f"{len(missing)} 个引用资源不存在或已删除，不能推断归属",
        )
    unowned = [rid for rid in refs if owner_by_resource[rid] is None]
    if unowned:
        return (
            MANUAL_REVIEW,
            None,
            f"{len(unowned)} 个引用资源无归属（公共/NULL owner），不能推断用户",
        )
    distinct = sorted({owner_by_resource[rid] for rid in refs})
    if len(distinct) != 1:
        return (
            MANUAL_REVIEW,
            None,
            f"引用资源归属 {len(distinct)} 个不同用户，不能自动选择",
        )
    return ASSIGN_OWNER, distinct[0], "全部引用资源存在且同属一个用户，可安全归属"


# --- 只读归属报告 -----------------------------------------------------------


async def build_draft_owner_report(
    db_url: str, kinds: Sequence[str] | None = None
) -> dict[str, Any]:
    """Classify NULL-owner generation/variant drafts. Read-only, never mutates."""
    selected = list(kinds) if kinds else list(DRAFT_KINDS)
    for kind in selected:
        if kind not in DRAFT_KINDS:
            raise ValueError(f"未知草稿类型: {kind}")
    engine = create_engine(db_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            entries: list[dict[str, Any]] = []
            summaries: dict[str, dict[str, Any]] = {}
            for kind in selected:
                model = DRAFT_KINDS[kind]
                rows = (
                    (
                        await session.execute(
                            select(model)
                            .where(model.owner_id.is_(None))
                            .order_by(model.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                refs_by_draft = {
                    row.id: EXTRACTORS[kind](_business_json(kind, row))
                    for row in rows
                }
                all_refs = sorted(
                    {rid for ids in refs_by_draft.values() for rid in ids}
                )
                owner_by_resource: dict[str, str | None] = {}
                if all_refs:
                    for rid, owner in await session.execute(
                        select(ResourceRow.id, ResourceRow.owner_id).where(
                            ResourceRow.id.in_(all_refs)
                        )
                    ):
                        owner_by_resource[rid] = owner
                kind_entries = [
                    _entry(kind, row, refs_by_draft[row.id], owner_by_resource)
                    for row in rows
                ]
                entries.extend(kind_entries)
                summaries[kind] = _kind_summary(kind_entries, owner_by_resource)
            return {
                "generated_at": datetime.now(UTC).isoformat(),
                "scope": {"owner_id": None, "kinds": selected, "read_only": True},
                "notes": [EXTRACTION_NOTE, SUGGESTION_NOTE],
                "drafts": entries,
                "summary": {"total": len(entries), "by_kind": summaries},
            }
    finally:
        await engine.dispose()


def _business_json(kind: str, row: Any) -> Any:
    return row.plan if kind == KIND_COURSE_GENERATION else row.variants


def _entry(
    kind: str,
    row: Any,
    refs: Sequence[str],
    owner_by_resource: dict[str, str | None],
) -> dict[str, Any]:
    suggested, owner, reason = _suggest(refs, owner_by_resource)
    owners = sorted(
        {
            owner_by_resource[rid]
            for rid in refs
            if rid in owner_by_resource and owner_by_resource[rid] is not None
        }
    )
    entry: dict[str, Any] = {
        "kind": kind,
        "draft_id": row.id,
        "status": row.status,
        "created_at": _iso(row.created_at),
        "reviewed_at": _iso(row.reviewed_at),
        "referenced_resource_ids": list(refs),
        "referenced_resource_count": len(refs),
        "resource_owner_ids": owners,
        "unowned_resource_count": sum(
            1
            for rid in refs
            if rid in owner_by_resource and owner_by_resource[rid] is None
        ),
        "missing_resource_ids": [
            rid for rid in refs if rid not in owner_by_resource
        ],
        "suggested_path": suggested,
        "suggested_owner_id": owner,
        "suggestion_reason": reason,
    }
    if kind == KIND_COURSE_GENERATION:
        lessons = row.plan.get("lessons") if isinstance(row.plan, dict) else None
        entry.update(
            {
                "goal": row.goal,
                "chapter_count": row.chapter_count,
                "dag_version": row.dag_version,
                "lesson_count": len(lessons) if isinstance(lessons, list) else 0,
            }
        )
    else:
        entry.update(
            {
                "generation_note": row.generation_note,
                "variant_count": row.variant_count,
            }
        )
    return entry


def _kind_summary(
    entries: Sequence[dict[str, Any]], owner_by_resource: dict[str, str | None]
) -> dict[str, Any]:
    """按 kind 聚合状态/建议路径/资源 owner 分布（多 owner 精确计入各自桶）。"""
    by_status: dict[str, int] = {}
    by_path = {ASSIGN_OWNER: 0, MANUAL_REVIEW: 0}
    by_reason: dict[str, int] = {}
    assignable_by_owner: dict[str, int] = {}
    referenced: set[str] = set()
    missing: set[str] = set()
    unowned: set[str] = set()
    resources_by_owner: dict[str, set[str]] = {}
    for entry in entries:
        by_status[entry["status"]] = by_status.get(entry["status"], 0) + 1
        by_path[entry["suggested_path"]] += 1
        by_reason[entry["suggestion_reason"]] = (
            by_reason.get(entry["suggestion_reason"], 0) + 1
        )
        if entry["suggested_path"] == ASSIGN_OWNER:
            owner = entry["suggested_owner_id"]
            assignable_by_owner[owner] = assignable_by_owner.get(owner, 0) + 1
        referenced.update(entry["referenced_resource_ids"])
        missing.update(entry["missing_resource_ids"])
        for rid in entry["referenced_resource_ids"]:
            if rid in missing:
                continue
            owner = owner_by_resource.get(rid)
            if owner is None:
                unowned.add(rid)
            else:
                resources_by_owner.setdefault(owner, set()).add(rid)
    return {
        "total": len(entries),
        "by_status": dict(sorted(by_status.items())),
        "by_suggested_path": by_path,
        "by_suggestion_reason": dict(sorted(by_reason.items())),
        "assignable_drafts_by_owner": dict(sorted(assignable_by_owner.items())),
        "distinct_referenced_resources": len(referenced),
        "missing_resources": len(missing),
        "unowned_referenced_resources": len(unowned),
        "referenced_resources_by_owner": {
            owner: len(ids) for owner, ids in sorted(resources_by_owner.items())
        },
    }


def format_draft_owner_report_summary(report: dict[str, Any]) -> str:
    """Human-readable aggregate summary; never prints production draft IDs."""
    lines = [
        "历史无归属生成/变式草稿报告（只读）",
        f"生成时间: {report['generated_at']}",
        (
            "范围: course_generation_drafts / variant_question_drafts 中 "
            "owner_id IS NULL 的行（不修改数据库）"
        ),
    ]
    for kind, stats in report["summary"]["by_kind"].items():
        status_text = "  ".join(
            f"{name}={count}" for name, count in stats["by_status"].items()
        )
        lines.append(f"[{kind}] 总数: {stats['total']}  {status_text}")
        lines.append(
            "  建议路径: "
            + "  ".join(
                f"{name}={count}"
                for name, count in stats["by_suggested_path"].items()
            )
        )
        lines.append(
            f"  引用资源: 去重 {stats['distinct_referenced_resources']}"
            f"  缺失 {stats['missing_resources']}"
            f"  无主 {stats['unowned_referenced_resources']}"
        )
        if stats["assignable_drafts_by_owner"]:
            lines.append(
                "  可自动归属: "
                + "  ".join(
                    f"{owner}={count}"
                    for owner, count in stats["assignable_drafts_by_owner"].items()
                )
            )
        if stats["referenced_resources_by_owner"]:
            lines.append(
                "  引用资源 owner 分布: "
                + "  ".join(
                    f"{owner}={count}"
                    for owner, count in stats[
                        "referenced_resources_by_owner"
                    ].items()
                )
            )
    lines.append(f"说明: {EXTRACTION_NOTE}")
    lines.append(f"说明: {SUGGESTION_NOTE}")
    lines.append(
        "明细（含 draft_id 与引用列表）请用 --json 或 --output 写入 gitignore 的 "
        "artifacts/temp 目录。"
    )
    return "\n".join(lines)


# --- 精确 ID 解析 -----------------------------------------------------------


def resolve_draft_ids(
    explicit: Sequence[str] | None, ids_file: str | Path | None
) -> list[str]:
    """Merge --draft-id and --ids-file into a deduplicated literal ID list."""
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
            raise ValueError("草稿 ID 必须是精确字面量，不得包含 * 或 %")
        if len(item) > 64:
            raise ValueError("草稿 ID 长度不得超过 64")
        if item not in ids:
            ids.append(item)
    if not ids:
        raise ValueError("至少提供一个精确草稿 ID（--draft-id 或 --ids-file）")
    return ids


# --- 迁移执行 ---------------------------------------------------------------


async def _load_draft_states(
    session: AsyncSession, kind: str, draft_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    model = DRAFT_KINDS[kind]
    rows = (
        (await session.execute(select(model).where(model.id.in_(list(draft_ids)))))
        .scalars()
        .all()
    )
    return {row.id: {"status": row.status, "owner_id": row.owner_id} for row in rows}


async def _resolve_target_user(
    session: AsyncSession, reference: str, *, for_update: bool = False
) -> dict[str, str] | None:
    """Exact match on user id or username; never fuzzy.

    for_update=True 在当前事务内锁定用户行（PG FOR UPDATE；SQLite 忽略由库级
    锁兜底），供写事务在更新草稿前复核目标用户仍然存在（与 legacy_papers
    同一事务模式，独立实现避免跨模块私有依赖）。
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


def _audit_base(action: str, kind: str, ids: Sequence[str]) -> dict[str, Any]:
    return {
        "action": action,
        "target_type": DRAFT_KINDS[kind].__tablename__,
        "target_id": ",".join(ids)[:128],
        "request_id": f"cli-{uuid.uuid4().hex[:12]}",
        "actor_id": "cli-operator",
        "actor_username": "cli-operator",
    }


async def _lock_and_verify(
    session: AsyncSession, kind: str, eligible: Sequence[str]
) -> list[Any]:
    """锁定并复核 eligible 行仍存在且 owner 仍为 NULL；不符抛错整体回滚。"""
    model = DRAFT_KINDS[kind]
    rows = (
        (
            await session.execute(
                select(model).where(model.id.in_(list(eligible))).with_for_update()
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
        if row.owner_id is not None:
            raise RuntimeError(
                f"行 {row.id} 状态与计划不一致（owner 已变化），事务回滚"
            )
    return rows


def _plan_skeleton(
    path: str, kind: str, ids: list[str], *, dry_run: bool
) -> dict[str, Any]:
    return {
        "path": path,
        "kind": kind,
        "dry_run": dry_run,
        "requested": len(ids),
        "draft_ids": ids,
        "invalid_draft_ids": [],
        "eligible_draft_ids": [],
        "skipped": [],
    }


async def run_draft_migrate(
    db_url: str,
    kind: str,
    path: str,
    draft_ids: Sequence[str],
    *,
    execute: bool,
    owner_ref: str | None = None,
) -> dict[str, Any]:
    """Plan or execute one ownership path over exact draft IDs of one kind."""
    if kind not in DRAFT_KINDS:
        raise ValueError(f"未知草稿类型: {kind}")
    if path not in MIGRATION_PATHS:
        raise ValueError(f"未知迁移路径: {path}")
    ids = list(draft_ids)
    engine = create_engine(db_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            states = await _load_draft_states(session, kind, ids)
            plan = _plan_skeleton(path, kind, ids, dry_run=not execute)
            invalid = [did for did in ids if did not in states]
            skipped: list[dict[str, str]] = []
            eligible: list[str] = []
            for did in ids:
                if did not in states:
                    continue
                if states[did]["owner_id"] is not None:
                    skipped.append({"draft_id": did, "reason": _SKIP_OWNED})
                else:
                    eligible.append(did)
            plan["invalid_draft_ids"] = invalid
            plan["eligible_draft_ids"] = eligible
            plan["skipped"] = skipped
            if invalid:
                plan.update(
                    {
                        "executed": False,
                        "failure": (
                            f"未知草稿 ID {len(invalid)} 个（不存在或属于另一 "
                            f"kind），拒绝执行（精确行契约）"
                        ),
                        "exit_code": 1,
                    }
                )
                return plan
            if not execute:
                plan.update({"executed": False, "exit_code": 0})
                return plan
            if not eligible:
                plan.update({"executed": False, "exit_code": 0})
                return plan

            # 执行阶段独占一个显式事务；进入前统一结束读事务。
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
                    await _lock_and_verify(session, kind, eligible)
                    result = await session.execute(
                        update(DRAFT_KINDS[kind])
                        .where(
                            DRAFT_KINDS[kind].id.in_(eligible),
                            DRAFT_KINDS[kind].owner_id.is_(None),
                        )
                        .values(owner_id=target["id"])
                    )
                    if result.rowcount != len(eligible):
                        raise RuntimeError(
                            f"更新行数 {result.rowcount} 与计划 {len(eligible)} "
                            "不一致，事务回滚"
                        )
                    session.add(
                        AuditLogRow(
                            **audit_insert_values(
                                {
                                    **_audit_base(
                                        "ops.draft_owner.assign_owner",
                                        kind,
                                        eligible,
                                    ),
                                    "before": {
                                        "kind": kind,
                                        "owner_id": None,
                                        "draft_ids": eligible,
                                    },
                                    "after": {
                                        "kind": kind,
                                        "owner_id": target["id"],
                                        "username": target["username"],
                                        "draft_ids": eligible,
                                        "drafts_modified": result.rowcount,
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
                        "drafts_modified": len(eligible),
                        "target_user": target,
                        "audit_action": "ops.draft_owner.assign_owner",
                        "exit_code": 0,
                    }
                )
                return plan

            # keep-unowned：不修改草稿，只写审计决策。
            await session.rollback()
            async with session.begin():
                # 审计必须与事实一致：先锁定并复核 eligible 行仍存在且
                # owner 仍为 NULL，陈旧计划整体回滚、不写审计。
                await _lock_and_verify(session, kind, eligible)
                session.add(
                    AuditLogRow(
                        **audit_insert_values(
                            {
                                **_audit_base(
                                    "ops.draft_owner.keep_unowned", kind, eligible
                                ),
                                "before": {
                                    "kind": kind,
                                    "owner_id": None,
                                    "draft_ids": eligible,
                                },
                                "after": {
                                    "kind": kind,
                                    "decision": "keep_unowned",
                                    "draft_ids": eligible,
                                    "drafts_modified": 0,
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
                    "drafts_modified": 0,
                    "audit_action": "ops.draft_owner.keep_unowned",
                    "exit_code": 0,
                }
            )
            return plan
    finally:
        await engine.dispose()
