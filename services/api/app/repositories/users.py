"""M9-01 用户仓储（M9-04 增角色）：users 表的持久化（共享同一 async sessionmaker）。"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import UserRow


class UserRecord(BaseModel):
    id: str
    username: str
    role: str = "learner"
    created_at: datetime

    model_config = {"frozen": True}


class UserRepository:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def create(self, username: str, password_hash: str) -> UserRecord:
        """创建用户；用户名已存在（预检 + 唯一约束兜底）显式 ValueError → 路由 409。"""
        row = UserRow(
            id=uuid.uuid4().hex,
            username=username,
            password_hash=password_hash,
            role="learner",
            created_at=self._clock(),
        )
        async with self._sessionmaker() as session, session.begin():
            if await session.scalar(select(UserRow).where(UserRow.username == username)):
                raise ValueError("username already exists")
            session.add(row)
            try:
                await session.flush()
            except IntegrityError as cause:
                raise ValueError("username already exists") from cause
            return UserRecord(
                id=row.id, username=row.username, role=row.role, created_at=row.created_at
            )

    async def get(self, user_id: str) -> UserRecord | None:
        async with self._sessionmaker() as session:
            row = await session.get(UserRow, user_id)
            if row is None:
                return None
            return UserRecord(
                id=row.id, username=row.username, role=row.role, created_at=row.created_at
            )

    async def find_by_username(self, username: str) -> tuple[UserRecord, str] | None:
        """返回 (record, password_hash)；不存在返回 None（路由层统一 401 防枚举）。"""
        async with self._sessionmaker() as session:
            row = (
                await session.scalars(select(UserRow).where(UserRow.username == username))
            ).first()
            if row is None:
                return None
            record = UserRecord(
                id=row.id, username=row.username, role=row.role, created_at=row.created_at
            )
            return record, row.password_hash

    async def get_role(self, user_id: str) -> str | None:
        """实时读角色（M9-04：管理员撤销需即时生效，不缓存进 token）。"""
        async with self._sessionmaker() as session:
            row = await session.get(UserRow, user_id)
            return row.role if row else None

    async def set_role(self, username: str, role: str) -> UserRecord | None:
        """按用户名设角色（admin CLI 专用）；返回更新后记录，用户不存在返回 None。"""
        async with self._sessionmaker() as session, session.begin():
            row = (
                await session.scalars(select(UserRow).where(UserRow.username == username))
            ).first()
            if row is None:
                return None
            row.role = role
            return UserRecord(
                id=row.id, username=row.username, role=row.role, created_at=row.created_at
            )

    async def list_users(self) -> list[UserRecord]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(select(UserRow).order_by(UserRow.created_at))
            ).scalars().all()
            return [
                UserRecord(
                    id=row.id, username=row.username, role=row.role, created_at=row.created_at
                )
                for row in rows
            ]
