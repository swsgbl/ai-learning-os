"""M10-03 production data inventory and narrowly scoped acceptance cleanup.

The inventory is read-only. Acceptance cleanup deliberately has no whole-table
mode: it resolves learner accounts by explicit username prefixes, collects the
exact row ids owned by those accounts, and deletes only those ids in one
database transaction. Admin accounts, normal users, system seed papers, and the
append-only audit log are retained.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import (
    AnswerEventRow,
    AuditLogRow,
    ChunkRow,
    CourseGenerationDraftRow,
    CourseImportDraftRow,
    EvalRunRow,
    EvidenceRow,
    ExamSessionRow,
    MisconceptionCandidateRow,
    PaperQuestionDraftRow,
    PaperRow,
    ParseJobRow,
    QuestionRow,
    ResourceRow,
    SearchQueryRow,
    StudentConceptStateRow,
    SubmissionRow,
    UserRow,
    VariantQuestionDraftRow,
    VoiceAnswerEventRow,
    VoiceSessionRow,
    VoiceTraceSpanRow,
    VoiceTranscriptRow,
)
from app.db.session import create_engine
from app.domain.audit_chain import append_audit

DEFAULT_ACCEPTANCE_MARKERS = ("smoke_", "voice_smoke_")
_DRAFT_MODELS = {
    "course_import_drafts": CourseImportDraftRow,
    "course_generation_drafts": CourseGenerationDraftRow,
    "paper_question_drafts": PaperQuestionDraftRow,
    "variant_question_drafts": VariantQuestionDraftRow,
}


def validate_markers(markers: Sequence[str]) -> tuple[str, ...]:
    """Validate literal prefixes; SQL wildcard characters are never expanded."""
    if not markers:
        raise ValueError("至少提供一个验收标记前缀")
    unique: list[str] = []
    for marker in markers:
        value = marker.strip()
        if not value or "*" in value or "%" in value:
            raise ValueError("验收标记必须是非空字面量，且不得包含 * 或 %")
        if len(value) > 64:
            raise ValueError("验收标记长度不得超过 64")
        if value not in unique:
            unique.append(value)
    return tuple(unique)


async def _count(session: AsyncSession, model, *conditions) -> int:
    query = select(func.count()).select_from(model)
    if conditions:
        query = query.where(*conditions)
    value = await session.scalar(query)
    return int(value or 0)


async def _ids(session: AsyncSession, column, *conditions) -> list[Any]:
    if conditions and all(condition is None for condition in conditions):
        return []
    values = (await session.scalars(select(column).where(*conditions))).all()
    return sorted(set(values), key=repr)


async def build_data_inventory(db_url: str) -> dict[str, Any]:
    """Return a read-only production data/risk inventory."""
    engine = create_engine(db_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            report: dict[str, Any] = {
                "users": {
                    "total": await _count(session, UserRow),
                    "admins": await _count(session, UserRow, UserRow.role == "admin"),
                    "learners": await _count(session, UserRow, UserRow.role == "learner"),
                    "acceptance_candidates": await _count(
                        session,
                        UserRow,
                        UserRow.role == "learner",
                        or_(
                            *(
                                UserRow.username.startswith(marker, autoescape=True)
                                for marker in DEFAULT_ACCEPTANCE_MARKERS
                            )
                        ),
                    ),
                },
                "papers": {
                    "total": await _count(session, PaperRow),
                    "system_seed": await _count(
                        session,
                        PaperRow,
                        PaperRow.owner_id.is_(None),
                        PaperRow.source == "AI Learning OS seed",
                    ),
                    "public_null_owner": await _count(
                        session, PaperRow, PaperRow.owner_id.is_(None)
                    ),
                    "private_owned": await _count(
                        session, PaperRow, PaperRow.owner_id.is_not(None)
                    ),
                    "non_seed_null_owner": await _count(
                        session,
                        PaperRow,
                        PaperRow.owner_id.is_(None),
                        PaperRow.source != "AI Learning OS seed",
                    ),
                    "questions": await _count(session, QuestionRow),
                },
                "resources": {
                    "total": await _count(session, ResourceRow),
                    "owned": await _count(
                        session, ResourceRow, ResourceRow.owner_id.is_not(None)
                    ),
                    "null_owner": await _count(
                        session, ResourceRow, ResourceRow.owner_id.is_(None)
                    ),
                    "chunks": await _count(session, ChunkRow),
                    "evidence": await _count(session, EvidenceRow),
                    "parse_jobs": await _count(session, ParseJobRow),
                    "failed_parse_jobs": await _count(
                        session, ParseJobRow, ParseJobRow.status == "failed"
                    ),
                },
                "exams": {
                    "sessions": await _count(session, ExamSessionRow),
                    "answer_events": await _count(session, AnswerEventRow),
                    "submissions": await _count(session, SubmissionRow),
                },
                "voice": {
                    "transcripts": await _count(session, VoiceTranscriptRow),
                    "stored_audio_objects": await _count(
                        session,
                        VoiceTranscriptRow,
                        VoiceTranscriptRow.audio_object_key.is_not(None),
                    ),
                    "sessions": await _count(session, VoiceSessionRow),
                    "answer_events": await _count(session, VoiceAnswerEventRow),
                    "trace_spans": await _count(session, VoiceTraceSpanRow),
                },
                "drafts": {
                    name: {
                        "total": await _count(session, model),
                        "pending_review": await _count(
                            session, model, model.status == "pending_review"
                        ),
                        "approved": await _count(
                            session, model, model.status == "approved"
                        ),
                        "rejected": await _count(
                            session, model, model.status == "rejected"
                        ),
                    }
                    for name, model in _DRAFT_MODELS.items()
                },
                "governance": {
                    "audit_log": await _count(session, AuditLogRow),
                    "search_queries": await _count(session, SearchQueryRow),
                },
                "learning": {
                    "student_concept_states": await _count(session, StudentConceptStateRow),
                    "misconception_candidates": await _count(
                        session, MisconceptionCandidateRow
                    ),
                    "eval_runs": await _count(session, EvalRunRow),
                },
            }
            report["risk_summary"] = {
                "admin_accounts": report["users"]["admins"],
                "acceptance_candidate_users": report["users"][
                    "acceptance_candidates"
                ],
                "non_seed_public_papers": report["papers"]["non_seed_null_owner"],
                "private_papers": report["papers"]["private_owned"],
                "null_owner_resources": report["resources"]["null_owner"],
                "stored_voice_audio_objects": report["voice"][
                    "stored_audio_objects"
                ],
                "failed_parse_jobs": report["resources"]["failed_parse_jobs"],
                "pending_review_drafts": sum(
                    item["pending_review"] for item in report["drafts"].values()
                ),
            }
            return report
    finally:
        await engine.dispose()


async def _build_cleanup_plan(
    session: AsyncSession, markers: Sequence[str]
) -> dict[str, Any]:
    prefix = or_(
        *(UserRow.username.startswith(marker, autoescape=True) for marker in markers)
    )
    users = (
        await session.execute(
            select(UserRow.id, UserRow.username, UserRow.role).where(
                UserRow.role == "learner", prefix
            )
        )
    ).all()
    skipped_admins = (
        await session.execute(
            select(UserRow.username).where(UserRow.role == "admin", prefix)
        )
    ).scalars().all()
    user_ids = [row.id for row in users]

    exam_ids: list[str] = []
    resource_ids: list[str] = []
    paper_ids: list[str] = []
    if user_ids:
        exam_ids = await _ids(
            session, ExamSessionRow.exam_id, ExamSessionRow.owner_id.in_(user_ids)
        )
        resource_ids = await _ids(
            session, ResourceRow.id, ResourceRow.owner_id.in_(user_ids)
        )
        paper_ids = await _ids(session, PaperRow.id, PaperRow.owner_id.in_(user_ids))

    voice_session_ids = (
        await _ids(
            session, VoiceSessionRow.id, VoiceSessionRow.exam_id.in_(exam_ids)
        )
        if exam_ids
        else []
    )
    draft_ids = {
        "course_import_drafts": await _ids(
            session,
            CourseImportDraftRow.id,
            or_(
                CourseImportDraftRow.owner_id.in_(user_ids),
                CourseImportDraftRow.source_resource_id.in_(resource_ids),
            ),
        )
        if user_ids or resource_ids
        else [],
        "course_generation_drafts": await _ids(
            session,
            CourseGenerationDraftRow.id,
            CourseGenerationDraftRow.owner_id.in_(user_ids),
        )
        if user_ids
        else [],
        "paper_question_drafts": await _ids(
            session,
            PaperQuestionDraftRow.id,
            or_(
                PaperQuestionDraftRow.owner_id.in_(user_ids),
                PaperQuestionDraftRow.resource_id.in_(resource_ids),
            ),
        )
        if user_ids or resource_ids
        else [],
        "variant_question_drafts": await _ids(
            session,
            VariantQuestionDraftRow.id,
            VariantQuestionDraftRow.owner_id.in_(user_ids),
        )
        if user_ids
        else [],
    }
    search_ids = (
        await _ids(session, SearchQueryRow.id, SearchQueryRow.owner_id.in_(user_ids))
        if user_ids
        else []
    )
    transcript_ids = (
        await _ids(
            session,
            VoiceTranscriptRow.id,
            or_(
                VoiceTranscriptRow.owner_id.in_(user_ids),
                VoiceTranscriptRow.exam_id.in_(exam_ids),
            ),
        )
        if user_ids or exam_ids
        else []
    )
    voice_answer_ids = (
        await _ids(
            session,
            VoiceAnswerEventRow.event_id,
            or_(
                VoiceAnswerEventRow.exam_id.in_(exam_ids),
                VoiceAnswerEventRow.session_id.in_(voice_session_ids),
            ),
        )
        if exam_ids or voice_session_ids
        else []
    )
    trace_ids = (
        await _ids(
            session,
            VoiceTraceSpanRow.id,
            or_(
                VoiceTraceSpanRow.exam_id.in_(exam_ids),
                VoiceTraceSpanRow.session_id.in_(voice_session_ids),
            ),
        )
        if exam_ids or voice_session_ids
        else []
    )
    submission_ids = (
        await _ids(
            session, SubmissionRow.id, SubmissionRow.exam_session_id.in_(exam_ids)
        )
        if exam_ids
        else []
    )
    answer_event_ids = (
        await _ids(
            session,
            AnswerEventRow.id,
            AnswerEventRow.exam_session_id.in_(exam_ids),
        )
        if exam_ids
        else []
    )
    question_ids = (
        await _ids(session, QuestionRow.id, QuestionRow.paper_id.in_(paper_ids))
        if paper_ids
        else []
    )
    parse_job_ids = (
        await _ids(session, ParseJobRow.id, ParseJobRow.resource_id.in_(resource_ids))
        if resource_ids
        else []
    )
    evidence_ids = (
        await _ids(session, EvidenceRow.id, EvidenceRow.resource_id.in_(resource_ids))
        if resource_ids
        else []
    )
    chunk_ids = (
        await _ids(session, ChunkRow.id, ChunkRow.resource_id.in_(resource_ids))
        if resource_ids
        else []
    )

    internal_counts = {
        "voice_trace_spans": len(trace_ids),
        "voice_answer_events": len(voice_answer_ids),
        "voice_sessions": len(voice_session_ids),
        "voice_transcripts": len(transcript_ids),
        "search_queries": len(search_ids),
        **{name: len(ids) for name, ids in draft_ids.items()},
        "submissions": len(submission_ids),
        "answer_events": len(answer_event_ids),
        "exam_sessions": len(exam_ids),
        "questions": len(question_ids),
        "papers": len(paper_ids),
        "parse_jobs": len(parse_job_ids),
        "evidence": len(evidence_ids),
        "chunks": len(chunk_ids),
        "resources": len(resource_ids),
        "users": len(user_ids),
    }
    object_keys = await _object_keys_owned_only_by(
        session, resource_ids=resource_ids, transcript_ids=transcript_ids
    )
    return {
        "markers": list(markers),
        "matched_users": [{"id": row.id, "username": row.username} for row in users],
        "skipped_admin_usernames": list(skipped_admins),
        "counts": internal_counts,
        "object_keys": object_keys,
        "_ids": {
            "users": user_ids,
            "papers": paper_ids,
            "questions": question_ids,
            "exam_sessions": exam_ids,
            "answer_events": answer_event_ids,
            "submissions": submission_ids,
            "voice_sessions": voice_session_ids,
            "voice_answer_events": voice_answer_ids,
            "voice_trace_spans": trace_ids,
            "voice_transcripts": transcript_ids,
            "search_queries": search_ids,
            "course_import_drafts": draft_ids["course_import_drafts"],
            "course_generation_drafts": draft_ids["course_generation_drafts"],
            "paper_question_drafts": draft_ids["paper_question_drafts"],
            "variant_question_drafts": draft_ids["variant_question_drafts"],
            "parse_jobs": parse_job_ids,
            "evidence": evidence_ids,
            "chunks": chunk_ids,
            "resources": resource_ids,
        },
    }


async def _object_keys_owned_only_by(
    session: AsyncSession, *, resource_ids: Sequence[str], transcript_ids: Sequence[int]
) -> list[str]:
    """Find object keys referenced only by the rows selected for deletion."""
    candidates: set[str] = set()
    if resource_ids:
        candidates.update(
            await session.scalars(
                select(ResourceRow.storage_key).where(ResourceRow.id.in_(resource_ids))
            )
        )
    if transcript_ids:
        candidates.update(
            await session.scalars(
                select(VoiceTranscriptRow.audio_object_key).where(
                    VoiceTranscriptRow.id.in_(transcript_ids),
                    VoiceTranscriptRow.audio_object_key.is_not(None),
                )
            )
        )
    keys: list[str] = []
    for key in sorted(candidates):
        resource_refs = await _count(
            session, ResourceRow, ResourceRow.storage_key == key
        )
        audio_refs = await _count(
            session,
            VoiceTranscriptRow,
            VoiceTranscriptRow.audio_object_key == key,
        )
        selected_resource_refs = (
            await _count(
                session,
                ResourceRow,
                ResourceRow.storage_key == key,
                ResourceRow.id.in_(resource_ids),
            )
            if resource_ids
            else 0
        )
        selected_audio_refs = (
            await _count(
                session,
                VoiceTranscriptRow,
                VoiceTranscriptRow.audio_object_key == key,
                VoiceTranscriptRow.id.in_(transcript_ids),
            )
            if transcript_ids
            else 0
        )
        if resource_refs + audio_refs == selected_resource_refs + selected_audio_refs:
            keys.append(key)
    return keys


async def _delete_exact(
    session: AsyncSession, model, column, values: Sequence[Any], label: str
) -> int:
    if not values:
        return 0
    result = await session.execute(delete(model).where(column.in_(values)))
    if result.rowcount != len(values):
        raise RuntimeError(f"{label} 删除数量与计划不一致，事务已回滚")
    return result.rowcount


async def run_acceptance_clean(
    db_url: str, markers: Sequence[str], *, execute: bool
) -> dict[str, Any]:
    """Plan or execute narrowly scoped acceptance cleanup."""
    safe_markers = validate_markers(markers)
    engine = create_engine(db_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            plan = await _build_cleanup_plan(session, safe_markers)
            if not execute:
                return _public_report(plan, dry_run=True)
            # The planning selects implicitly begin a read transaction. End it so
            # the destructive phase owns one explicit transaction.
            await session.rollback()

            ids = plan["_ids"]
            async with session.begin():
                deleted: dict[str, int] = {}
                deleted["voice_trace_spans"] = await _delete_exact(
                    session, VoiceTraceSpanRow, VoiceTraceSpanRow.id,
                    ids["voice_trace_spans"], "voice_trace_spans"
                )
                deleted["voice_answer_events"] = await _delete_exact(
                    session, VoiceAnswerEventRow, VoiceAnswerEventRow.event_id,
                    ids["voice_answer_events"], "voice_answer_events"
                )
                deleted["voice_sessions"] = await _delete_exact(
                    session, VoiceSessionRow, VoiceSessionRow.id,
                    ids["voice_sessions"], "voice_sessions"
                )
                deleted["voice_transcripts"] = await _delete_exact(
                    session, VoiceTranscriptRow, VoiceTranscriptRow.id,
                    ids["voice_transcripts"], "voice_transcripts"
                )
                deleted["search_queries"] = await _delete_exact(
                    session, SearchQueryRow, SearchQueryRow.id,
                    ids["search_queries"], "search_queries"
                )
                for name, model in (
                    ("course_import_drafts", CourseImportDraftRow),
                    ("course_generation_drafts", CourseGenerationDraftRow),
                    ("paper_question_drafts", PaperQuestionDraftRow),
                    ("variant_question_drafts", VariantQuestionDraftRow),
                ):
                    deleted[name] = await _delete_exact(
                        session, model, model.id, ids[name], name
                    )
                deleted["submissions"] = await _delete_exact(
                    session, SubmissionRow, SubmissionRow.id,
                    ids["submissions"], "submissions"
                )
                deleted["answer_events"] = await _delete_exact(
                    session, AnswerEventRow, AnswerEventRow.id,
                    ids["answer_events"], "answer_events"
                )
                deleted["exam_sessions"] = await _delete_exact(
                    session, ExamSessionRow, ExamSessionRow.exam_id,
                    ids["exam_sessions"], "exam_sessions"
                )
                deleted["questions"] = await _delete_exact(
                    session, QuestionRow, QuestionRow.id, ids["questions"], "questions"
                )
                deleted["papers"] = await _delete_exact(
                    session, PaperRow, PaperRow.id, ids["papers"], "papers"
                )
                deleted["parse_jobs"] = await _delete_exact(
                    session, ParseJobRow, ParseJobRow.id,
                    ids["parse_jobs"], "parse_jobs"
                )
                deleted["evidence"] = await _delete_exact(
                    session, EvidenceRow, EvidenceRow.id, ids["evidence"], "evidence"
                )
                deleted["chunks"] = await _delete_exact(
                    session, ChunkRow, ChunkRow.id, ids["chunks"], "chunks"
                )
                deleted["resources"] = await _delete_exact(
                    session, ResourceRow, ResourceRow.id,
                    ids["resources"], "resources"
                )
                deleted["users"] = await _delete_exact(
                    session, UserRow, UserRow.id, ids["users"], "users"
                )
                audit = {
                    "action": "ops.acceptance_clean",
                    "target_type": "database",
                    "target_id": ",".join(safe_markers)[:128],
                    "request_id": f"cli-{uuid.uuid4().hex[:12]}",
                    "actor_id": "cli-operator",
                    "actor_username": "cli-operator",
                    "before": {"planned": plan["counts"]},
                    "after": {"deleted": deleted},
                }
                await append_audit(
                    session, audit, clock=lambda: datetime.now(UTC)
                )

            object_report = await _delete_objects(plan["object_keys"])
            report = _public_report(plan, dry_run=False)
            report["deleted"] = deleted
            report["object_store"] = object_report
            report["audit_policy"] = "append-only retained"
            return report
    finally:
        await engine.dispose()


async def _delete_objects(keys: Sequence[str]) -> dict[str, Any]:
    if not keys:
        return {"attempted": 0, "deleted": 0, "errors": []}
    from app.core.config import get_settings
    from app.storage.objectstore import make_object_store

    settings = get_settings()
    persistent = bool(
        settings.s3_endpoint
        and settings.s3_bucket
        and settings.s3_access_key
        and settings.s3_secret_key
    )
    if not persistent:
        return {
            "attempted": 0,
            "deleted": 0,
            "errors": [],
            "note": "未配置持久对象存储；数据库引用已清理",
        }
    store = make_object_store(settings)
    from botocore.exceptions import BotoCoreError, ClientError

    deleted = 0
    errors: list[str] = []
    for key in keys:
        try:
            store.delete(key)
            deleted += 1
        except (OSError, ValueError, BotoCoreError, ClientError) as cause:
            errors.append(f"{key}: {cause}")
    return {"attempted": len(keys), "deleted": deleted, "errors": errors}


def _public_report(plan: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    return {
        "dry_run": dry_run,
        "markers": plan["markers"],
        "matched_users": plan["matched_users"],
        "skipped_admin_usernames": plan["skipped_admin_usernames"],
        "planned_deletions": plan["counts"],
        "unique_object_keys_eligible_for_delete": len(plan["object_keys"]),
    }
