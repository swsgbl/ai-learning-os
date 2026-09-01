"""M5-07 课程生成草稿持久化：创建/查询/人工审核（终态不可逆，同 M5-05/06 队列模式）。"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import CourseGenerationDraftRow
from app.repositories.memory import utc_now

_TERMINAL = {"approved", "rejected"}


class CourseGenerationDraftRepository:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def create(self, draft: dict) -> dict:
        draft_id = f"cgd_{uuid.uuid4().hex}"
        now = self._clock()
        async with self._sessionmaker() as session, session.begin():
            session.add(CourseGenerationDraftRow(
                id=draft_id,
                goal=draft["goal"],
                status="pending_review",
                dag_version=draft["dag_version"],
                plan=draft["plan"],
                chapter_count=draft["chapter_count"],
                generation_note=draft["generation_note"],
                created_at=now,
            ))
        return await self.get(draft_id)

    async def get(self, draft_id: str) -> dict | None:
        async with self._sessionmaker() as session:
            row = await session.get(CourseGenerationDraftRow, draft_id)
            if row is None:
                return None
            return self._view(row)

    async def list_by_status(self, status: str | None = None) -> list[dict]:
        query = select(CourseGenerationDraftRow).order_by(CourseGenerationDraftRow.created_at)
        if status is not None:
            query = query.where(CourseGenerationDraftRow.status == status)
        async with self._sessionmaker() as session:
            rows = (await session.execute(query)).scalars().all()
            return [self._view(row) for row in rows]

    async def review(self, draft_id: str, decision: str, note: str | None) -> dict | None:
        """人工审核：pending_review -> approved/rejected；已终态返回哨兵供上层 409。"""
        if decision not in _TERMINAL:
            raise ValueError(f"非法审核决定: {decision}")
        now = self._clock()
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(CourseGenerationDraftRow, draft_id)
            if row is None:
                return "MISSING"
            if row.status in _TERMINAL:
                return "TERMINAL"
            row.status = decision
            row.review_note = note
            row.reviewed_at = now
        return await self.get(draft_id)

    def _view(self, row: CourseGenerationDraftRow) -> dict:
        return {
            "id": row.id,
            "goal": row.goal,
            "status": row.status,
            "dag_version": row.dag_version,
            "plan": dict(row.plan or {}),
            "chapter_count": row.chapter_count,
            "generation_note": row.generation_note,
            "review_note": row.review_note,
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
            "created_at": row.created_at.isoformat(),
        }
