"""M5-01 搜索执行记录 repository：append-only，一次执行一行。

查询计划（providers_requested）、结果摘要（results）与弃用原因
（providers_skipped，含 per-provider 原因）一并落盘可回查。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import SearchQueryRow
from app.repositories.memory import utc_now


def _from_db(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SearchQueryRepository:
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
        query: str,
        providers_requested: list[str],
        providers_skipped: list[dict],
        result_count: int,
        duration_ms: int,
        results: list[dict],
    ) -> dict:
        row = SearchQueryRow(
            query=query,
            providers_requested=providers_requested,
            providers_skipped=providers_skipped,
            result_count=result_count,
            duration_ms=duration_ms,
            results=results,
            created_at=self._clock(),
        )
        async with self._sessionmaker() as session, session.begin():
            session.add(row)
            await session.flush()
            return self._to_view(row)

    async def get(self, query_id: int) -> dict | None:
        async with self._sessionmaker() as session:
            row = await session.get(SearchQueryRow, query_id)
            return self._to_view(row) if row is not None else None

    @staticmethod
    def _to_view(row: SearchQueryRow) -> dict:
        return {
            "id": row.id,
            "query": row.query,
            "providers_requested": list(row.providers_requested or []),
            "providers_skipped": list(row.providers_skipped or []),
            "result_count": row.result_count,
            "duration_ms": row.duration_ms,
            "results": list(row.results or []),
            "created_at": _from_db(row.created_at).isoformat(),
        }
