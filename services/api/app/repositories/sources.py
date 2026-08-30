"""M1-01 Source Registry repository."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import SourceRow
from app.domain.license import LicenseState, TrustTier
from app.domain.source import SourceRecord
from app.repositories.memory import utc_now
from app.repositories.seed_sources import seed_sources

MIN_AUTHORITY_SCORE = 0
MAX_AUTHORITY_SCORE = 100


class SourceRepository:
    """Persistence for source registry. Shares the async sessionmaker with the exam repo."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def seed_if_empty(self) -> int:
        async with self._sessionmaker() as session, session.begin():
            count = await session.scalar(select(func.count()).select_from(SourceRow))
            if count:
                return 0
            for record in seed_sources():
                session.add(self._row(record))
            return 6

    async def create(self, record: SourceRecord) -> SourceRecord:
        async with self._sessionmaker() as session, session.begin():
            if await session.get(SourceRow, record.id):
                raise ValueError("source id already exists")
            session.add(self._row(record))
        return record

    async def get(self, source_id: str) -> SourceRecord | None:
        async with self._sessionmaker() as session:
            row = await session.get(SourceRow, source_id)
            return self._record(row) if row else None

    async def list(self) -> list[SourceRecord]:
        async with self._sessionmaker() as session:
            rows = (await session.execute(select(SourceRow).order_by(SourceRow.id))).scalars().all()
            return [self._record(row) for row in rows]

    async def mark_verified(self, source_id: str) -> SourceRecord | None:
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(SourceRow, source_id)
            if not row:
                return None
            row.last_verified_at = _to_db(self._clock())
            return self._record(row)

    @staticmethod
    def _row(record: SourceRecord) -> SourceRow:
        return SourceRow(
            id=record.id,
            name=record.name,
            source_type=record.source_type,
            authority_score=record.authority_score,
            homepage=record.homepage,
            terms_url=record.terms_url,
            robots_policy_snapshot=record.robots_policy_snapshot,
            rate_limit=record.rate_limit,
            trust_tier=record.trust_tier.value,
            license_state=record.license_state.value,
            last_verified_at=_to_db(record.last_verified_at),
            notes=record.notes,
        )

    @staticmethod
    def _record(row: SourceRow) -> SourceRecord:
        return SourceRecord(
            id=row.id,
            name=row.name,
            source_type=row.source_type,
            homepage=row.homepage,
            authority_score=row.authority_score,
            terms_url=row.terms_url,
            robots_policy_snapshot=row.robots_policy_snapshot,
            rate_limit=row.rate_limit,
            trust_tier=TrustTier(row.trust_tier),
            license_state=LicenseState(row.license_state),
            last_verified_at=_from_db(row.last_verified_at),
            notes=row.notes,
        )


def _to_db(value: datetime | None):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _from_db(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
