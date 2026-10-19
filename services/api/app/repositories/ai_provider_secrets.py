"""M15-008 provider 密钥仓储：ai_provider_secrets 表的持久化（PG）与内存替身。

- 槽位白名单在路由层校验；仓储只认 slot 字符串原样存取；
- 密文/last4 由路由层（SecretBox）产生，仓储不理解加密语义；
- 内存替身供无数据库模式（治理端点在无库时本就 503，替身仅保测试形状）。
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import AiProviderSecretRow


class ProviderSecretRecord(BaseModel):
    slot: str
    ciphertext: str
    last4: str
    updated_at: datetime

    model_config = {"frozen": True}


class AiProviderSecretsRepository:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def get(self, slot: str) -> ProviderSecretRecord | None:
        async with self._sessionmaker() as session:
            row = (await session.execute(select(AiProviderSecretRow).where(AiProviderSecretRow.slot == slot))).scalar_one_or_none()
            if row is None:
                return None
            return ProviderSecretRecord(
                slot=row.slot, ciphertext=row.ciphertext, last4=row.last4, updated_at=row.updated_at
            )

    async def list_all(self) -> list[ProviderSecretRecord]:
        async with self._sessionmaker() as session:
            rows = (await session.execute(select(AiProviderSecretRow).order_by(AiProviderSecretRow.slot))).scalars().all()
            return [
                ProviderSecretRecord(slot=r.slot, ciphertext=r.ciphertext, last4=r.last4, updated_at=r.updated_at)
                for r in rows
            ]

    async def upsert(self, slot: str, ciphertext: str, last4: str) -> ProviderSecretRecord:
        """写入（覆盖旧值）。last4 仅供状态页脱敏展示。

        双方言 upsert（audit_chain 同款先例）：PG/SQLite 走 ON CONFLICT
        DO UPDATE；未知方言退化为普通 insert（冲突 IntegrityError，
        fail-closed 不静默分叉）。
        """
        now = self._clock()
        values = {"slot": slot, "ciphertext": ciphertext, "last4": last4, "updated_at": now}
        async with self._sessionmaker() as session:
            dialect = session.bind.dialect.name if session.bind is not None else ""
            if dialect == "sqlite":
                stmt = sqlite_insert(AiProviderSecretRow).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[AiProviderSecretRow.slot],
                    set_={"ciphertext": ciphertext, "last4": last4, "updated_at": now},
                )
            elif dialect == "postgresql":
                stmt = pg_insert(AiProviderSecretRow).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[AiProviderSecretRow.slot],
                    set_={"ciphertext": ciphertext, "last4": last4, "updated_at": now},
                )
            else:
                stmt = insert(AiProviderSecretRow).values(**values)
            await session.execute(stmt)
            await session.commit()
        return ProviderSecretRecord(slot=slot, ciphertext=ciphertext, last4=last4, updated_at=now)

    async def clear(self, slot: str) -> bool:
        """删除槽位（回退 env 由调用方语义决定）。返回是否有删除。"""
        async with self._sessionmaker() as session:
            result = await session.execute(delete(AiProviderSecretRow).where(AiProviderSecretRow.slot == slot))
            await session.commit()
            return bool(result.rowcount)


class MemoryAiProviderSecretsRepository:
    """内存替身：与 PG 版同接口（测试/无库模式形状对齐）。"""

    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._rows: dict[str, ProviderSecretRecord] = {}
        self._clock = clock

    async def get(self, slot: str) -> ProviderSecretRecord | None:
        return self._rows.get(slot)

    async def list_all(self) -> list[ProviderSecretRecord]:
        return [self._rows[k] for k in sorted(self._rows)]

    async def upsert(self, slot: str, ciphertext: str, last4: str) -> ProviderSecretRecord:
        record = ProviderSecretRecord(slot=slot, ciphertext=ciphertext, last4=last4, updated_at=self._clock())
        self._rows[slot] = record
        return record

    async def clear(self, slot: str) -> bool:
        return self._rows.pop(slot, None) is not None
