"""M15-014 个人 AI 密钥仓储（BYOK）：user_ai_keys 表的持久化。

与 AiProviderSecretsRepository 同构（同款加密语义/双方言 upsert），
差异仅在归属维度 user_id。密文/last4 由路由层（SecretBox）产生。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import UserAiKeyRow


class UserAiKeyRecord(BaseModel):
    user_id: str
    slot: str
    ciphertext: str
    last4: str
    endpoint: str | None = None
    model: str | None = None
    updated_at: datetime

    model_config = {"frozen": True}


def _upsert_stmt(dialect: str, values: dict, set_: dict):
    if dialect == "sqlite":
        stmt = sqlite_insert(UserAiKeyRow).values(**values)
        return stmt.on_conflict_do_update(index_elements=["user_id", "slot"], set_=set_)
    if dialect == "postgresql":
        stmt = pg_insert(UserAiKeyRow).values(**values)
        return stmt.on_conflict_do_update(index_elements=["user_id", "slot"], set_=set_)
    return insert(UserAiKeyRow).values(**values)


class UserAiKeysRepository:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def get(self, user_id: str, slot: str) -> UserAiKeyRecord | None:
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(UserAiKeyRow).where(
                        UserAiKeyRow.user_id == user_id, UserAiKeyRow.slot == slot
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return UserAiKeyRecord(
                user_id=row.user_id,
                slot=row.slot,
                ciphertext=row.ciphertext,
                last4=row.last4,
                endpoint=row.endpoint,
                model=row.model,
                updated_at=row.updated_at,
            )

    async def list_for_user(self, user_id: str) -> list[UserAiKeyRecord]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(UserAiKeyRow)
                    .where(UserAiKeyRow.user_id == user_id)
                    .order_by(UserAiKeyRow.slot)
                )
            ).scalars().all()
            return [
                UserAiKeyRecord(
                    user_id=r.user_id,
                    slot=r.slot,
                    ciphertext=r.ciphertext,
                    last4=r.last4,
                    endpoint=r.endpoint,
                    model=r.model,
                    updated_at=r.updated_at,
                )
                for r in rows
            ]

    async def upsert(
        self,
        user_id: str,
        slot: str,
        ciphertext: str,
        last4: str,
        *,
        endpoint: str | None = None,
        model: str | None = None,
    ) -> UserAiKeyRecord:
        now = self._clock()
        values = {
            "user_id": user_id,
            "slot": slot,
            "ciphertext": ciphertext,
            "last4": last4,
            "endpoint": endpoint,
            "model": model,
            "updated_at": now,
        }
        set_ = {
            "ciphertext": ciphertext,
            "last4": last4,
            "endpoint": endpoint,
            "model": model,
            "updated_at": now,
        }
        async with self._sessionmaker() as session:
            dialect = session.bind.dialect.name if session.bind is not None else ""
            await session.execute(_upsert_stmt(dialect, values, set_))
            await session.commit()
        return UserAiKeyRecord(
            user_id=user_id,
            slot=slot,
            ciphertext=ciphertext,
            last4=last4,
            endpoint=endpoint,
            model=model,
            updated_at=now,
        )

    async def clear(self, user_id: str, slot: str) -> bool:
        async with self._sessionmaker() as session:
            result = await session.execute(
                delete(UserAiKeyRow).where(UserAiKeyRow.user_id == user_id, UserAiKeyRow.slot == slot)
            )
            await session.commit()
            return bool(result.rowcount)
