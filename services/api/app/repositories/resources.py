"""M1-03 resource repository: content-hash dedup persistence."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import ResourceRow
from app.domain.resource import ResourceRecord
from app.repositories.memory import utc_now


class ResourceRepository:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def get(self, resource_id: str) -> ResourceRecord | None:
        async with self._sessionmaker() as session:
            row = await session.get(ResourceRow, resource_id)
            return self._record(row) if row else None

    async def get_by_hash(self, content_hash: str) -> ResourceRecord | None:
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(ResourceRow).where(ResourceRow.content_hash == content_hash)
                )
            ).scalar_one_or_none()
            return self._record(row) if row else None

    async def create(self, record: ResourceRecord) -> tuple[ResourceRecord, bool]:
        """按 content_hash 幂等创建；已存在时返回既有记录与 deduplicated=True。"""
        async with self._sessionmaker() as session, session.begin():
            existing = (
                await session.execute(
                    select(ResourceRow).where(ResourceRow.content_hash == record.content_hash)
                )
            ).scalar_one_or_none()
            if existing:
                return self._record(existing), True
            session.add(self._row(record))
        return record, False

    @staticmethod
    def _row(record: ResourceRecord) -> ResourceRow:
        return ResourceRow(
            id=record.id,
            source_id=record.source_id,
            url=record.url,
            media_type=record.media_type,
            title=record.title,
            language=record.language,
            access_state=record.access_state,
            license_state=record.license_state.value,
            content_hash=record.content_hash,
            storage_key=record.storage_key,
            size_bytes=record.size_bytes,
            content_type=record.content_type,
            parse_status=record.parse_status,
            fetched_at=_to_db(record.fetched_at),
        )

    @staticmethod
    def _record(row: ResourceRow) -> ResourceRecord:
        from app.domain.license import LicenseState

        return ResourceRecord(
            id=row.id,
            source_id=row.source_id,
            url=row.url,
            media_type=row.media_type,
            title=row.title,
            language=row.language,
            access_state=row.access_state,
            license_state=LicenseState(row.license_state),
            content_hash=row.content_hash,
            storage_key=row.storage_key,
            size_bytes=row.size_bytes,
            content_type=row.content_type,
            parse_status=row.parse_status,
            fetched_at=_from_db(row.fetched_at),
        )


def _to_db(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _from_db(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
