"""M1-06 chunk + evidence persistence; replace-on-reparse idempotent."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import ChunkRow, EvidenceRow
from app.parsing.chunking import Chunk
from app.repositories.memory import utc_now


class ChunkRepository:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def replace_chunks(
        self,
        resource_id: str,
        chunks: list[Chunk],
        *,
        parser_name: str,
        license_state: str,
        source_id: str | None = None,
        url: str | None = None,
    ) -> int:
        """重解析语义：整资源替换（delete + insert 同事务），幂等且不残留旧块。"""
        async with self._sessionmaker() as session, session.begin():
            await session.execute(delete(EvidenceRow).where(EvidenceRow.resource_id == resource_id))
            await session.execute(delete(ChunkRow).where(ChunkRow.resource_id == resource_id))
            for index, chunk in enumerate(chunks):
                chunk_id = f"chk_{uuid.uuid4().hex}"
                session.add(ChunkRow(
                    id=chunk_id,
                    resource_id=resource_id,
                    chunk_index=index,
                    text=chunk.text,
                    chunk_hash=chunk.chunk_hash,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    slide=chunk.slide,
                    block_types=chunk.block_types,
                ))
                session.add(EvidenceRow(
                    id=f"ev_{uuid.uuid4().hex}",
                    chunk_id=chunk_id,
                    resource_id=resource_id,
                    source_id=source_id,
                    url=url,
                    parser_name=parser_name,
                    locator={
                        "page": chunk.page_start,
                        "page_end": chunk.page_end,
                        "slide": chunk.slide,
                    },
                    snippet_hash=chunk.chunk_hash,
                    license_state=license_state,
                    retrieved_at=_to_db(self._clock()),
                ))
            return len(chunks)

    async def list_chunks(self, resource_id: str) -> list[dict]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(ChunkRow)
                    .where(ChunkRow.resource_id == resource_id)
                    .order_by(ChunkRow.chunk_index)
                )
            ).scalars().all()
            return [
                {
                    "id": row.id,
                    "chunk_index": row.chunk_index,
                    "text": row.text,
                    "chunk_hash": row.chunk_hash,
                    "page_start": row.page_start,
                    "page_end": row.page_end,
                    "slide": row.slide,
                    "block_types": row.block_types,
                }
                for row in rows
            ]

    async def list_evidence(self, resource_id: str) -> list[dict]:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(
                    select(EvidenceRow).where(EvidenceRow.resource_id == resource_id)
                )
            ).scalars().all()
            return [
                {
                    "id": row.id,
                    "chunk_id": row.chunk_id,
                    "parser_name": row.parser_name,
                    "locator": row.locator,
                    "snippet_hash": row.snippet_hash,
                    "license_state": row.license_state,
                    "source_id": row.source_id,
                    "url": row.url,
                }
                for row in rows
            ]


def _to_db(value: datetime) -> datetime:
    return value.astimezone(UTC)
