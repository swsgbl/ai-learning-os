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

    @staticmethod
    def _owner_scope(owner_id: str | None):
        """M9-02 归属过滤：owner=None 表示无主模式（只看 NULL 行），否则严格匹配。"""
        column = ResourceRow.owner_id
        return column.is_(None) if owner_id is None else (column == owner_id)

    async def get_owned(self, resource_id: str, owner_id: str | None) -> ResourceRecord | None:
        """归属感知读取：auth on 时他人/无主资源一律查不到（404，不暴露存在性）。"""
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(ResourceRow).where(
                        ResourceRow.id == resource_id,
                        self._owner_scope(owner_id),
                    )
                )
            ).scalar_one_or_none()
            return self._record(row) if row else None

    async def get(self, resource_id: str) -> ResourceRecord | None:
        """无归属校验读取（license report 等部署级治理视图使用）。"""
        async with self._sessionmaker() as session:
            row = await session.get(ResourceRow, resource_id)
            return self._record(row) if row else None

    async def list_all(self, *, limit: int = 500) -> list[ResourceRecord]:
        # M7-04 license report 用：派生对象授权快照清单（上限保护）。
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(ResourceRow).order_by(ResourceRow.id).limit(limit)
                )
            ).scalars().all()
            return [self._record(row) for row in rows]

    async def get_by_hash(
        self, content_hash: str, owner_id: str | None = None, *, scoped: bool = False
    ) -> ResourceRecord | None:
        """dedup 查询；scoped=True 时限定归属作用域（M9-02：跨用户不共享对象）。"""
        conditions = [ResourceRow.content_hash == content_hash]
        if scoped:
            conditions.append(self._owner_scope(owner_id))
        async with self._sessionmaker() as session:
            row = (
                await session.execute(select(ResourceRow).where(*conditions))
            ).scalar_one_or_none()
            return self._record(row) if row else None

    async def create(
        self, record: ResourceRecord, owner_id: str | None = None
    ) -> tuple[ResourceRecord, bool]:
        """归属作用域内按 content_hash 幂等创建（M9-02）；已存在返回 deduplicated=True。"""
        async with self._sessionmaker() as session, session.begin():
            existing = (
                await session.execute(
                    select(ResourceRow).where(
                        ResourceRow.content_hash == record.content_hash,
                        self._owner_scope(owner_id),
                    )
                )
            ).scalar_one_or_none()
            if existing:
                if record.source_id and not existing.source_id:
                    # 同内容重传补绑来源，并快照上传时点的 license
                    existing.source_id = record.source_id
                    existing.access_state = record.access_state
                    existing.license_state = record.license_state.value
                return self._record(existing), True
            session.add(self._row(record, owner_id))
        return record, False

    async def set_parse_status(
        self,
        resource_id: str,
        status: str,
        *,
        parser_name: str | None = None,
        metrics: dict | None = None,
        error: str | None = None,
    ) -> ResourceRecord | None:
        """M1-04：记录解析阶段状态（prompt pack C 要求 status/error/metrics）。"""
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(ResourceRow, resource_id)
            if not row:
                return None
            row.parse_status = status
            if parser_name is not None:
                row.parser_name = parser_name
            row.parse_metrics = metrics
            row.parse_error = error
            return self._record(row)

    @staticmethod
    def _row(record: ResourceRecord, owner_id: str | None = None) -> ResourceRow:
        return ResourceRow(
            id=record.id,
            owner_id=owner_id,
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
            parser_name=record.parser_name,
            parse_metrics=record.parse_metrics,
            parse_error=record.parse_error,
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
            parser_name=row.parser_name,
            parse_metrics=row.parse_metrics,
            parse_error=row.parse_error,
            fetched_at=_from_db(row.fetched_at),
        )


def _to_db(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _from_db(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
