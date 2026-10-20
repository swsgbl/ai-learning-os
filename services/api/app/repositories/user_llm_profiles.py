"""M15-018 多厂商 LLM 密钥库仓储：user_llm_profiles 的持久化。

- 每用户上限 MAX_PROFILES（8）——超出 ValueError → 路由 409；
- activate 在同一事务内先清后立（每用户恰一条激活）；
- 密文/last4 由路由层（SecretBox）产生，仓储不理解加密语义。
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import UserLlmProfileRow

MAX_PROFILES = 8


class LlmProfileRecord(BaseModel):
    id: str
    user_id: str
    label: str
    ciphertext: str
    last4: str
    endpoint: str | None
    model: str | None
    is_active: bool
    created_at: datetime

    model_config = {"frozen": True}


def _to_record(row: UserLlmProfileRow) -> LlmProfileRecord:
    return LlmProfileRecord(
        id=row.id,
        user_id=row.user_id,
        label=row.label,
        ciphertext=row.ciphertext,
        last4=row.last4,
        endpoint=row.endpoint,
        model=row.model,
        is_active=row.is_active,
        created_at=row.created_at,
    )


class UserLlmProfilesRepository:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def list_for_user(self, user_id: str) -> list[LlmProfileRecord]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(UserLlmProfileRow)
                    .where(UserLlmProfileRow.user_id == user_id)
                    .order_by(UserLlmProfileRow.created_at)
                )
            ).scalars().all()
            return [_to_record(r) for r in rows]

    async def get(self, profile_id: str, user_id: str) -> LlmProfileRecord | None:
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(UserLlmProfileRow).where(
                        UserLlmProfileRow.id == profile_id,
                        UserLlmProfileRow.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            return _to_record(row) if row else None

    async def create(
        self,
        user_id: str,
        label: str,
        ciphertext: str,
        last4: str,
        *,
        endpoint: str | None = None,
        model: str | None = None,
        activate: bool = False,
    ) -> LlmProfileRecord:
        async with self._sessionmaker() as session:
            count = len(
                (
                    await session.execute(
                        select(UserLlmProfileRow.id).where(UserLlmProfileRow.user_id == user_id)
                    )
                ).all()
            )
            if count >= MAX_PROFILES:
                raise ValueError(f"最多保存 {MAX_PROFILES} 个厂商配置")
            if activate:
                await session.execute(
                    update(UserLlmProfileRow)
                    .where(UserLlmProfileRow.user_id == user_id, UserLlmProfileRow.is_active)
                    .values(is_active=False)
                )
            row = UserLlmProfileRow(
                id=f"ulp_{uuid.uuid4().hex[:24]}",
                user_id=user_id,
                label=label,
                ciphertext=ciphertext,
                last4=last4,
                endpoint=endpoint,
                model=model,
                is_active=activate or count == 0,  # 首条自动激活
                created_at=self._clock(),
            )
            session.add(row)
            await session.commit()
            return _to_record(row)

    async def update_fields(
        self,
        profile_id: str,
        user_id: str,
        *,
        label: str | None = None,
        ciphertext: str | None = None,
        last4: str | None = None,
        endpoint: str | None = None,
        model: str | None = None,
    ) -> LlmProfileRecord | None:
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(UserLlmProfileRow).where(
                        UserLlmProfileRow.id == profile_id,
                        UserLlmProfileRow.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            if label is not None:
                row.label = label
            if ciphertext is not None:
                row.ciphertext = ciphertext
                row.last4 = last4 or ""
            if endpoint is not None:
                row.endpoint = endpoint
            if model is not None:
                row.model = model
            await session.commit()
            return _to_record(row)

    async def activate(self, profile_id: str, user_id: str) -> LlmProfileRecord | None:
        async with self._sessionmaker() as session, session.begin():
            row = (
                await session.execute(
                    select(UserLlmProfileRow).where(
                        UserLlmProfileRow.id == profile_id,
                        UserLlmProfileRow.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            await session.execute(
                update(UserLlmProfileRow)
                .where(UserLlmProfileRow.user_id == user_id, UserLlmProfileRow.is_active)
                .values(is_active=False)
            )
            row.is_active = True
            return _to_record(row)

    async def delete(self, profile_id: str, user_id: str) -> bool:
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(UserLlmProfileRow).where(
                        UserLlmProfileRow.id == profile_id,
                        UserLlmProfileRow.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            was_active = row.is_active
            await session.execute(
                delete(UserLlmProfileRow).where(
                    UserLlmProfileRow.id == profile_id, UserLlmProfileRow.user_id == user_id
                )
            )
            # 删的是激活条 → 最早创建的一条顶上（保持「恰一条激活」不变量）
            if was_active:
                remaining = (
                    await session.execute(
                        select(UserLlmProfileRow)
                        .where(UserLlmProfileRow.user_id == user_id)
                        .order_by(UserLlmProfileRow.created_at)
                    )
                ).scalars().all()
                if remaining:
                    remaining[0].is_active = True
            await session.commit()
            return True
