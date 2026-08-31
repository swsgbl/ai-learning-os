"""M3-04 Misconception repository：误解候选的物化存储。

与 M3-03 同款：事件源 append-only，状态 = 全量 replace 重算，重复执行行集合一致。
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import MisconceptionCandidateRow
from app.domain.misconceptions import MisconceptionCandidate
from app.repositories.memory import utc_now


def _to_db(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _from_db(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class MisconceptionRepository:
    """Persistence for materialized misconception candidates. Shares the sessionmaker."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def recompute(self, candidates: Iterable[MisconceptionCandidate]) -> list[MisconceptionCandidate]:
        ordered = sorted(candidates, key=lambda item: (item.concept_id, item.pattern))
        async with self._sessionmaker() as session, session.begin():
            await session.execute(delete(MisconceptionCandidateRow))
            session.add_all(
                MisconceptionCandidateRow(
                    concept_id=item.concept_id,
                    pattern=item.pattern,
                    status=item.status,
                    confidence=item.confidence,
                    independent_count=item.independent_count,
                    occurrence_count=item.occurrence_count,
                    question_ids=list(item.question_ids),
                    first_seen_at=_to_db(item.first_seen_at),
                    last_seen_at=_to_db(item.last_seen_at),
                    updated_at=_to_db(item.updated_at),
                )
                for item in ordered
            )
        return await self.list_candidates()

    async def list_candidates(self) -> list[MisconceptionCandidate]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(MisconceptionCandidateRow).order_by(
                        MisconceptionCandidateRow.confidence.desc(),
                        MisconceptionCandidateRow.concept_id,
                        MisconceptionCandidateRow.pattern,
                    )
                )
            ).scalars().all()
            return [self._candidate_from_row(row) for row in rows]

    async def list_by_concept(self, concept_id: str) -> list[MisconceptionCandidate]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(MisconceptionCandidateRow)
                    .where(MisconceptionCandidateRow.concept_id == concept_id)
                    .order_by(MisconceptionCandidateRow.confidence.desc())
                )
            ).scalars().all()
            return [self._candidate_from_row(row) for row in rows]

    @staticmethod
    def _candidate_from_row(row: MisconceptionCandidateRow) -> MisconceptionCandidate:
        return MisconceptionCandidate(
            concept_id=row.concept_id,
            pattern=row.pattern,
            status=row.status,
            confidence=row.confidence,
            independent_count=row.independent_count,
            occurrence_count=row.occurrence_count,
            question_ids=tuple(row.question_ids or ()),
            first_seen_at=_from_db(row.first_seen_at),
            last_seen_at=_from_db(row.last_seen_at),
            updated_at=_from_db(row.updated_at),
        )
