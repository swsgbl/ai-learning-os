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


def audit_insert_values(payload: dict, *, clock: Callable[[], datetime]) -> dict:
    """把审计 payload 标准化为 AuditLogRow insert 值（M9-05 同事务审计共用）。

    必需键：action/target_type/target_id/request_id；可选：actor_id/actor_username/
    before/after。缺 request_id 视为构造错误（审计必须可关联请求）。
    """
    missing = {"action", "target_type", "target_id", "request_id"} - payload.keys()
    if missing:
        raise ValueError(f"审计 payload 缺字段: {sorted(missing)}")
    return {
        "actor_id": payload.get("actor_id"),
        "actor_username": payload.get("actor_username"),
        "action": payload["action"],
        "target_type": payload["target_type"],
        "target_id": payload["target_id"],
        "before": payload.get("before"),
        "after": payload.get("after"),
        "request_id": payload["request_id"],
        "created_at": clock(),
    }
