"""M1-07 parse job queue persistence: idempotent enqueue, atomic claim, retry."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import ParseJobRow
from app.repositories.memory import utc_now

MAX_ATTEMPTS = 3


class ParseJobRepository:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def enqueue(
        self, resource_id: str, parser_name: str | None = None
    ) -> tuple[dict, bool]:
        """幂等入队：同 (resource_id, parser_name) 已存在则返回既有任务。

        succeeded/failed 终态任务重复入队时重置为 pending（显式重跑语义）。
        """
        async with self._sessionmaker() as session, session.begin():
            existing = (
                await session.execute(
                    select(ParseJobRow).where(
                        ParseJobRow.resource_id == resource_id,
                        ParseJobRow.parser_name == parser_name,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                if existing.status in {"succeeded", "failed"}:
                    existing.status = "pending"
                    existing.attempts = 0
                    existing.last_error = None
                    existing.updated_at = _to_db(self._clock())
                    return self._dict(existing), True
                return self._dict(existing), False
            now = _to_db(self._clock())
            row = ParseJobRow(
                id=f"job_{uuid.uuid4().hex}",
                resource_id=resource_id,
                parser_name=parser_name,
                status="pending",
                attempts=0,
                max_attempts=MAX_ATTEMPTS,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            return self._dict(row), True

    async def claim_next(self) -> dict | None:
        """原子认领：pending 或过期 running -> running；供 worker 单条消费。"""
        async with self._sessionmaker() as session, session.begin():
            row = (
                await session.execute(
                    select(ParseJobRow)
                    .where(ParseJobRow.status == "pending")
                    .order_by(ParseJobRow.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if not row:
                return None
            row.status = "running"
            row.attempts += 1
            row.updated_at = _to_db(self._clock())
            return self._dict(row)

    async def recover_stale_running(self) -> int:
        """进程重启恢复：把 running 任务重置 pending（单进程部署假设）。"""
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(
                update(ParseJobRow)
                .where(ParseJobRow.status == "running")
                .values(status="pending", updated_at=_to_db(self._clock()))
            )
            return result.rowcount or 0

    async def mark_succeeded(self, job_id: str) -> None:
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(ParseJobRow, job_id)
            if row:
                row.status = "succeeded"
                row.last_error = None
                row.updated_at = _to_db(self._clock())

    async def mark_failed(self, job_id: str, error: str) -> str:
        """失败处理：attempts 未耗尽回 pending 重试，否则 failed 终态。返回终态。"""
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(ParseJobRow, job_id)
            if not row:
                return "missing"
            row.last_error = error[:1024]
            if row.attempts >= row.max_attempts:
                row.status = "failed"
            else:
                row.status = "pending"
            row.updated_at = _to_db(self._clock())
            return row.status

    async def list_for_resource(self, resource_id: str) -> list[dict]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(ParseJobRow)
                    .where(ParseJobRow.resource_id == resource_id)
                    .order_by(ParseJobRow.created_at)
                )
            ).scalars().all()
            return [self._dict(row) for row in rows]

    @staticmethod
    def _dict(row: ParseJobRow) -> dict:
        return {
            "id": row.id,
            "resource_id": row.resource_id,
            "parser_name": row.parser_name,
            "status": row.status,
            "attempts": row.attempts,
            "max_attempts": row.max_attempts,
            "last_error": row.last_error,
        }


def _to_db(value: datetime) -> datetime:
    return value.astimezone(UTC)
