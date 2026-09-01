"""M4-05 语音答案事件 repository：event_id 幂等记录。

- record：event_id 冲突（客户端重放）→ 返回既有行（幂等，绝不重复落库）；
- get / list_for_session：幂等重放读取与回查。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import VoiceAnswerEventRow
from app.repositories.memory import utc_now

_EVENT_ID_CONFLICT = "event_id 已被其他会话使用"


def _from_db(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class VoiceAnswerEventRepository:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def record(
        self,
        *,
        event_id: str,
        session_id: str,
        exam_id: str,
        question_id: str,
        normalized_answer: str | None,
        intent: str,
        transcript: str,
        confidence: float,
        accepted: bool,
    ) -> dict:
        """记录规范化答案事件；event_id 已存在则幂等返回既有行。"""
        async with self._sessionmaker() as session, session.begin():
            existing = await session.get(VoiceAnswerEventRow, event_id)
            if existing is not None:
                if existing.session_id != session_id:
                    raise ValueError(_EVENT_ID_CONFLICT)  # 跨会话复用 event_id → 拒绝，不读他人事件
                return self._to_view(existing)
            row = VoiceAnswerEventRow(
                event_id=event_id,
                session_id=session_id,
                exam_id=exam_id,
                question_id=question_id,
                normalized_answer=normalized_answer,
                intent=intent,
                transcript=transcript,
                confidence=confidence,
                accepted=accepted,
                created_at=self._clock(),
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError:
                # 并发重放撞主键：回读既有行，幂等返回（跨会话冲突 → 拒绝）。
                existing = await session.get(VoiceAnswerEventRow, event_id)
                if existing is None:
                    raise
                if existing.session_id != session_id:
                    raise ValueError(_EVENT_ID_CONFLICT) from None
                return self._to_view(existing)
            return self._to_view(row)

    async def get(self, event_id: str) -> dict | None:
        async with self._sessionmaker() as session:
            row = await session.get(VoiceAnswerEventRow, event_id)
            return self._to_view(row) if row else None

    async def list_for_session(self, session_id: str) -> list[dict]:
        async with self._sessionmaker() as session:
            rows = await session.execute(
                select(VoiceAnswerEventRow)
                .where(VoiceAnswerEventRow.session_id == session_id)
                .order_by(VoiceAnswerEventRow.created_at.asc())
            )
            return [self._to_view(row) for row in rows.scalars().all()]

    @staticmethod
    def _to_view(row: VoiceAnswerEventRow) -> dict:
        return {
            "event_id": row.event_id,
            "session_id": row.session_id,
            "exam_id": row.exam_id,
            "question_id": row.question_id,
            "normalized_answer": row.normalized_answer,
            "intent": row.intent,
            "transcript": row.transcript,
            "confidence": row.confidence,
            "accepted": row.accepted,
            "created_at": _from_db(row.created_at).isoformat(),
        }
