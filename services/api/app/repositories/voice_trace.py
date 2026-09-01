"""M4-09 语音耗时观测 repository：span 追加落盘（append-only，观测不判定）。

服务端自动埋点（asr/tts/intent/fsm）与客户端上报（vad/llm/first_audio）
写同一张表，source 字段区分来源；聚合视图由 summarize_spans 纯函数计算。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import VoiceTraceSpanRow
from app.repositories.memory import utc_now


def _from_db(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class VoiceTraceRepository:
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
        stage: str,
        duration_ms: int,
        source: str,
        session_id: str | None = None,
        exam_id: str | None = None,
        question_id: str | None = None,
    ) -> dict:
        """追加一行耗时 span；返回存储视图（含 id/created_at）。"""
        row = VoiceTraceSpanRow(
            stage=stage,
            duration_ms=duration_ms,
            source=source,
            session_id=session_id,
            exam_id=exam_id,
            question_id=question_id,
            created_at=self._clock(),
        )
        async with self._sessionmaker() as session, session.begin():
            session.add(row)
            await session.flush()
            return self._to_view(row)

    async def list_recent(self, limit: int = 500) -> list[dict]:
        async with self._sessionmaker() as session:
            rows = await session.execute(
                select(VoiceTraceSpanRow)
                .order_by(VoiceTraceSpanRow.id.desc())
                .limit(limit)
            )
            return [self._to_view(row) for row in rows.scalars().all()]

    @staticmethod
    def _to_view(row: VoiceTraceSpanRow) -> dict:
        return {
            "id": row.id,
            "stage": row.stage,
            "duration_ms": row.duration_ms,
            "source": row.source,
            "session_id": row.session_id,
            "exam_id": row.exam_id,
            "question_id": row.question_id,
            "created_at": _from_db(row.created_at).isoformat(),
        }
