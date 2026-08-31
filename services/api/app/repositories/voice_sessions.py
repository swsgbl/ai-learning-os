"""M4-03 语音会话 repository：FSM 状态持久化 + 乐观并发。

- create/get：会话生命周期管理（一个 exam 可有多个语音会话，重连即新会话）；
- apply：事件应用（status 迁移已在 domain 层校验，此处只落库 + revision+1）。
状态本身不在此重复校验——领域规则收敛在 app/domain/voice_session_fsm.py。
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import VoiceSessionRow
from app.domain import voice_session_fsm as fsm
from app.repositories.memory import utc_now


def _from_db(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class VoiceSessionRepository:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def create(self, exam_id: str) -> dict:
        """创建语音会话（SESSION_READY，question_index=0，revision=1）。"""
        session_id = f"vs_{uuid.uuid4().hex[:20]}"
        now = self._clock()
        row = VoiceSessionRow(
            id=session_id,
            exam_id=exam_id,
            status=fsm.SESSION_READY,
            question_index=0,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        async with self._sessionmaker() as session, session.begin():
            session.add(row)
            await session.flush()
            return self._to_view(row)

    async def get(self, session_id: str) -> dict | None:
        async with self._sessionmaker() as session:
            row = await session.get(VoiceSessionRow, session_id)
            return self._to_view(row) if row else None

    async def apply(
        self,
        session_id: str,
        *,
        new_status: str,
        question_index: int | None = None,
        expected_revision: int | None = None,
    ) -> dict:
        """应用已校验的迁移：更新 status（可选 question_index）并 revision+1。

        expected_revision 提供时做乐观并发校验（不匹配 → 并发冲突错误）。
        """
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(VoiceSessionRow, session_id)
            if row is None:
                raise KeyError("语音会话不存在")
            if expected_revision is not None and row.revision != expected_revision:
                raise ValueError("语音会话已被并发修改，请刷新后重试")
            row.status = new_status
            if question_index is not None:
                row.question_index = question_index
            row.revision += 1
            row.updated_at = self._clock()
            await session.flush()
            return self._to_view(row)

    async def list_for_exam(self, exam_id: str) -> list[dict]:
        async with self._sessionmaker() as session:
            rows = await session.execute(
                select(VoiceSessionRow)
                .where(VoiceSessionRow.exam_id == exam_id)
                .order_by(VoiceSessionRow.created_at.asc())
            )
            return [self._to_view(row) for row in rows.scalars().all()]

    @staticmethod
    def _to_view(row: VoiceSessionRow) -> dict:
        return {
            "session_id": row.id,
            "exam_id": row.exam_id,
            "status": row.status,
            "question_index": row.question_index,
            "revision": row.revision,
            "created_at": _from_db(row.created_at).isoformat(),
            "updated_at": _from_db(row.updated_at).isoformat(),
        }
