"""M4-02 语音转写 repository：transcript 追加落盘（append-only）。

策略（12 号 runbook）：transcript/意图/事件恒存；原始音频仅
PRIVACY_STORE_AUDIO=true 时写对象存储（key 存 audio_object_key），
否则显式丢弃、audio_stored=false 留痕。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import VoiceTranscriptRow
from app.repositories.memory import utc_now


def _from_db(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class VoiceTranscriptRepository:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def save(
        self,
        *,
        provider: str,
        text: str,
        confidence: float,
        latency_ms: int,
        audio_bytes: int,
        audio_object_key: str | None,
        audio_stored: bool,
        exam_id: str | None = None,
        question_id: str | None = None,
        owner_id: str | None = None,
    ) -> dict:
        """追加一行转写；返回存储视图（含 id/created_at）。"""
        row = VoiceTranscriptRow(
            owner_id=owner_id,
            provider=provider,
            text=text,
            confidence=confidence,
            latency_ms=latency_ms,
            audio_bytes=audio_bytes,
            audio_object_key=audio_object_key,
            audio_stored=audio_stored,
            exam_id=exam_id,
            question_id=question_id,
            created_at=self._clock(),
        )
        async with self._sessionmaker() as session, session.begin():
            session.add(row)
            await session.flush()
            return self._to_view(row)

    async def list_for_owner(self, owner_id: str, limit: int = 50) -> list[dict]:
        """M9-02: auth on 时只回当前用户的转写。"""
        async with self._sessionmaker() as session:
            rows = await session.execute(
                select(VoiceTranscriptRow)
                .where(VoiceTranscriptRow.owner_id == owner_id)
                .order_by(VoiceTranscriptRow.id.desc())
                .limit(limit)
            )
            return [self._to_view(row) for row in rows.scalars().all()]

    async def list_recent(self, limit: int = 50) -> list[dict]:
        """最近转写（auth off 本地调试模式与部署级视图使用）。"""
        async with self._sessionmaker() as session:
            rows = await session.execute(
                select(VoiceTranscriptRow)
                .order_by(VoiceTranscriptRow.id.desc())
                .limit(limit)
            )
            return [self._to_view(row) for row in rows.scalars().all()]

    @staticmethod
    def _to_view(row: VoiceTranscriptRow) -> dict:
        return {
            "id": row.id,
            "provider": row.provider,
            "text": row.text,
            "confidence": row.confidence,
            "latency_ms": row.latency_ms,
            "audio_bytes": row.audio_bytes,
            "audio_object_key": row.audio_object_key,
            "audio_stored": row.audio_stored,
            "exam_id": row.exam_id,
            "question_id": row.question_id,
            "created_at": _from_db(row.created_at).isoformat(),
        }
