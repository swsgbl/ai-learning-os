"""M9-04 治理审计仓储：append-only 记录 + admin 只读列表。"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import AuditLogRow


class AuditRepository:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def record(
        self,
        *,
        action: str,
        target_type: str,
        target_id: str,
        request_id: str,
        actor_id: str | None = None,
        actor_username: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        async with self._sessionmaker() as session, session.begin():
            session.add(
                AuditLogRow(
                    actor_id=actor_id,
                    actor_username=actor_username,
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    before=before,
                    after=after,
                    request_id=request_id,
                    created_at=self._clock(),
                )
            )

    async def list_recent(self, limit: int = 100) -> list[dict]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(AuditLogRow).order_by(AuditLogRow.id.desc()).limit(limit)
                )
            ).scalars().all()
            return [
                {
                    "id": row.id,
                    "actor_id": row.actor_id,
                    "actor_username": row.actor_username,
                    "action": row.action,
                    "target_type": row.target_type,
                    "target_id": row.target_id,
                    "before": row.before,
                    "after": row.after,
                    "request_id": row.request_id,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]
