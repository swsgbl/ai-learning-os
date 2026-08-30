"""M1-07 parse worker：消费队列执行完整解析管线（normalize + chunk + evidence）。"""
from __future__ import annotations

import asyncio
import logging

from app.parsing.base import ParserError, ParserUnavailable
from app.parsing.chunking import chunk_blocks
from app.parsing.registry import ParserRegistry
from app.repositories.chunks import ChunkRepository
from app.repositories.parsejobs import ParseJobRepository
from app.repositories.resources import ResourceRepository
from app.storage.objectstore import ObjectStore

logger = logging.getLogger(__name__)


class ParseWorker:
    def __init__(
        self,
        jobs: ParseJobRepository,
        resources: ResourceRepository,
        chunks: ChunkRepository,
        store: ObjectStore,
        registry: ParserRegistry,
        poll_interval: float = 0.5,
    ) -> None:
        self._jobs = jobs
        self._resources = resources
        self._chunks = chunks
        self._store = store
        self._registry = registry
        self._poll_interval = poll_interval
        self._task: asyncio.Task | None = None

    async def run_once(self) -> str | None:
        """消费一条任务；返回终态（succeeded/failed/pending=待重试）或 None 无任务。"""
        job = await self._jobs.claim_next()
        if not job:
            return None
        resource = await self._resources.get(job["resource_id"])
        if not resource:
            await self._jobs.mark_failed(job["id"], "资源不存在")
            return "failed"
        try:
            data = self._store.get(resource.storage_key)
            parser = self._registry.select(resource.media_type, prefer=job["parser_name"])
            doc = parser.parse(data, resource.media_type)
            from app.parsing.normalize import normalize_blocks

            doc.blocks = normalize_blocks(doc.blocks)
            await self._chunks.replace_chunks(
                resource.id,
                chunk_blocks(doc.blocks),
                parser_name=doc.parser_name,
                license_state=resource.license_state.value,
                source_id=resource.source_id,
                url=resource.url,
            )
            await self._resources.set_parse_status(
                resource.id,
                "parsed",
                parser_name=doc.parser_name,
                metrics={"block_count": len(doc.blocks), "chunked": True},
            )
            await self._jobs.mark_succeeded(job["id"])
            return "succeeded"
        except (ParserError, ParserUnavailable) as cause:
            final = await self._jobs.mark_failed(job["id"], str(cause))
            await self._resources.set_parse_status(resource.id, "failed", error=str(cause))
            logger.warning("parse job %s -> %s: %s", job["id"], final, cause)
            return final

    async def _loop(self) -> None:
        while True:
            result = await self.run_once()
            if result is None:
                await asyncio.sleep(self._poll_interval)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
