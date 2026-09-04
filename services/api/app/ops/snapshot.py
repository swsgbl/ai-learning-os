"""M10-14 运行观测快照：admin-only 只读聚合，供运维判断队列/积压/会话/角色状态。

runbook（docs/delivery/12 第 7 节）的最小观测落地，**不是 Prometheus 替代品**：

- 只发 SELECT/COUNT/GROUP BY，绝不写任何表、不修改任何状态；
- 只输出聚合计数与状态分布，不输出 username、query 文本、title、正文、
  request_id、业务 ID、endpoint 或模型 key；
- database backend 只给 sqlite/postgresql 类别，不透出 URL/host/db name；
- 数据库异常统一为固定脱敏文案（不嵌异常链/连接串）——503 语义由 route 映射。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.orm import (
    AuditLogRow,
    CourseGenerationDraftRow,
    CourseImportDraftRow,
    PaperQuestionDraftRow,
    PaperRow,
    ParseJobRow,
    ResourceRow,
    SearchQueryRow,
    UserRow,
    VariantQuestionDraftRow,
    VoiceSessionRow,
)

#: 四类审核草稿的待审状态值（与各 draft repo 的状态机同口径）。
PENDING_REVIEW = "pending_review"


class OpsSnapshotError(RuntimeError):
    """快照构建失败——str 恒为固定脱敏文案，绝不携带连接串/异常细节。"""


class PendingReviewDraftsOut(BaseModel):
    """待审草稿按来源分类计数（治理积压面）。"""

    course_import: int
    course_generation: int
    paper_question: int
    variant_question: int


class OpsSnapshotOut(BaseModel):
    """运行观测快照（字段命名稳定；全部为聚合值/状态，无任何业务正文）。"""

    generated_at: str  # 服务器 UTC ISO-8601
    database_backend: Literal["sqlite", "postgresql"]  # 类别 only，无 URL/host/db name
    users_by_role: dict[str, int]
    papers_total: int
    resources_by_parse_status: dict[str, int]
    parse_jobs_by_status: dict[str, int]
    pending_review_drafts: PendingReviewDraftsOut
    voice_sessions_by_status: dict[str, int]
    search_queries_total: int
    audit_entries_total: int
    worker_running: bool


def backend_category(engine: AsyncEngine) -> Literal["sqlite", "postgresql"]:
    """方言归类：只暴露 sqlite/postgresql 类别，未知方言按不可快照处理。"""
    name = engine.dialect.name.lower()
    if name.startswith("sqlite"):
        return "sqlite"
    if name.startswith("postgresql"):
        return "postgresql"
    raise OpsSnapshotError("unsupported database backend")


async def _total(session: AsyncSession, entity: type) -> int:
    return (await session.execute(select(func.count()).select_from(entity))).scalar_one()


async def _counts_by(session: AsyncSession, column: ColumnElement) -> dict[str, int]:
    rows = (await session.execute(select(column, func.count()).group_by(column))).all()
    return {str(value): count for value, count in rows}


async def _pending_review_total(session: AsyncSession, entity: type) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(entity).where(entity.status == PENDING_REVIEW)
        )
    ).scalar_one()


async def build_ops_snapshot(
    sessionmaker: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    *,
    worker_running: bool,
) -> OpsSnapshotOut:
    """在同一会话内完成全部只读聚合（SQLAlchemyError -> 固定脱敏 OpsSnapshotError）。"""
    backend = backend_category(engine)
    try:
        async with sessionmaker() as session:
            snapshot = OpsSnapshotOut(
                generated_at=datetime.now(UTC).isoformat(),
                database_backend=backend,
                users_by_role=await _counts_by(session, UserRow.role),
                papers_total=await _total(session, PaperRow),
                resources_by_parse_status=await _counts_by(session, ResourceRow.parse_status),
                parse_jobs_by_status=await _counts_by(session, ParseJobRow.status),
                pending_review_drafts=PendingReviewDraftsOut(
                    course_import=await _pending_review_total(session, CourseImportDraftRow),
                    course_generation=await _pending_review_total(session, CourseGenerationDraftRow),
                    paper_question=await _pending_review_total(session, PaperQuestionDraftRow),
                    variant_question=await _pending_review_total(session, VariantQuestionDraftRow),
                ),
                voice_sessions_by_status=await _counts_by(session, VoiceSessionRow.status),
                search_queries_total=await _total(session, SearchQueryRow),
                audit_entries_total=await _total(session, AuditLogRow),
                worker_running=worker_running,
            )
    except SQLAlchemyError as cause:
        # 不把 cause 文本并入 message（连接串/驱动细节零泄漏）；链仅供服务端日志排查。
        raise OpsSnapshotError("ops snapshot query failed") from cause
    return snapshot
