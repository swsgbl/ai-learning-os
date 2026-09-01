"""M5-05 课程导入草稿持久化：创建/查询/人工审核（状态迁移终态不可逆）。"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import CourseImportDraftRow
from app.repositories.memory import utc_now

_TERMINAL = {"approved", "rejected"}


class CourseImportDraftRepository:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def create(self, draft: dict) -> dict:
        """草稿落库，状态固定 pending_review；返回完整视图。"""
        draft_id = f"crsd_{uuid.uuid4().hex}"
        now = self._clock()
        async with self._sessionmaker() as session, session.begin():
            session.add(CourseImportDraftRow(
                id=draft_id,
                title=draft["title"],
                status="pending_review",
                source_resource_id=draft["source_resource_id"],
                source_license_state=draft["source_license_state"],
                reuse_admission=draft["reuse_admission"],
                concepts=draft["concepts"],
                resource_refs=draft["resource_refs"],
                extraction_note=draft["extraction_note"],
                created_at=now,
            ))
        return await self.get(draft_id)

    async def get(self, draft_id: str) -> dict | None:
        async with self._sessionmaker() as session:
            row = await session.get(CourseImportDraftRow, draft_id)
            if row is None:
                return None
            return self._view(row)

    async def list_by_status(self, status: str | None = None) -> list[dict]:
        """按状态查审核队列；缺省返回全部（含终态）。"""
        query = select(CourseImportDraftRow).order_by(CourseImportDraftRow.created_at)
        if status is not None:
            query = query.where(CourseImportDraftRow.status == status)
        async with self._sessionmaker() as session:
            rows = (await session.execute(query)).scalars().all()
            return [self._view(row) for row in rows]

    async def review(self, draft_id: str, decision: str, note: str | None) -> dict | None:
        """人工审核：pending_review -> approved/rejected；已终态返回哨兵供上层 409。"""
        if decision not in _TERMINAL:
            raise ValueError(f"非法审核决定: {decision}")
        now = self._clock()
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(CourseImportDraftRow, draft_id)
            if row is None:
                return "MISSING"
            if row.status in _TERMINAL:
                return "TERMINAL"
            row.status = decision
            row.review_note = note
            row.reviewed_at = now
        return await self.get(draft_id)

    def _view(self, row: CourseImportDraftRow) -> dict:
        return {
            "id": row.id,
            "title": row.title,
            "status": row.status,
            "source_resource_id": row.source_resource_id,
            "source_license_state": row.source_license_state,
            "reuse_admission": row.reuse_admission,
            "concepts": list(row.concepts or []),
            "resource_refs": list(row.resource_refs or []),
            "extraction_note": row.extraction_note,
            "review_note": row.review_note,
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
            "created_at": row.created_at.isoformat(),
        }
