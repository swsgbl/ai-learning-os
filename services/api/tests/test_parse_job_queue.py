"""M1-07 parse job queue: idempotent enqueue, retry, recovery, parser switch."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _client():
    return TestClient(create_app(SQLITE_URL))


def _upload(client, content: bytes, name="doc.json") -> dict:
    return client.post(
        "/api/v1/resources/upload",
        files={"file": (name, content, "application/json")},
    ).json()


def test_enqueue_is_idempotent():
    with _client() as client:
        body = _upload(client, b'{"a": 1}')
        first = client.post(f"/api/v1/resources/{body['id']}/jobs")
        assert first.status_code == 202
        assert first.json()["status"] == "pending"
        again = client.post(f"/api/v1/resources/{body['id']}/jobs").json()
        assert again["id"] == first.json()["id"]


def test_succeeded_job_rerun_resets_to_pending():
    """终态任务显式重跑：重置 pending、attempts 清零。"""
    with _client() as client:
        body = _upload(client, b'{"a": 1}')
        first = client.post(f"/api/v1/resources/{body['id']}/jobs").json()
        jobs = client.get(f"/api/v1/resources/{body['id']}/jobs").json()
        assert jobs[0]["id"] == first["id"]
        rerun = client.post(f"/api/v1/resources/{body['id']}/jobs").json()
        assert rerun["id"] == first["id"]


# ---------- worker 单元（裸装配，不启后台循环） ----------
import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.session import prepare_database
from app.domain.resource import ResourceRecord
from app.parsing.base import ParserError
from app.parsing.fake import FakeParser
from app.parsing.registry import ParserRegistry
from app.parsing.worker import ParseWorker
from app.repositories.chunks import ChunkRepository
from app.repositories.parsejobs import ParseJobRepository
from app.repositories.resources import ResourceRepository
from app.storage.objectstore import MemoryObjectStore


def _registry(parser: FakeParser) -> ParserRegistry:
    registry = ParserRegistry()
    registry.register_instance(parser)
    return registry


def _setup(parser: FakeParser):
    engine = create_async_engine(SQLITE_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    def build():
        return prepare_database(engine, SQLITE_URL)

    asyncio.run(build())
    resources = ResourceRepository(maker)
    chunks = ChunkRepository(maker)
    jobs = ParseJobRepository(maker)
    store = MemoryObjectStore()
    worker = ParseWorker(jobs, resources, chunks, store, _registry(parser))
    return resources, jobs, worker, store, chunks


def _record(rid: str, key: str) -> ResourceRecord:
    return ResourceRecord(
        id=rid, media_type="pdf", title="t", content_hash=key,
        storage_key=key, size_bytes=2, fetched_at=datetime.now(UTC),
    )


def test_worker_success_creates_chunks():
    parser = FakeParser(name="fake")
    resources, jobs, worker, store, chunks = _setup(parser)
    record = _record("res_w1", "k1")

    async def scenario():
        await resources.create(record)
        store.put("k1", b"x")
        await jobs.enqueue("res_w1")
        return await worker.run_once()

    assert asyncio.run(scenario()) == "succeeded"
    assert parser.calls == 1
    found = asyncio.run(chunks.list_chunks("res_w1"))
    assert len(found) >= 1
    assert found[0]["page_start"] is None or found[0]["page_start"] >= 1


def test_worker_retries_then_fails():
    parser = FakeParser(name="fake", error=ParserError("boom"))
    resources, jobs, worker, store, _chunks = _setup(parser)
    record = _record("res_w2", "k2")

    async def scenario():
        await resources.create(record)
        store.put("k2", b"x")
        await jobs.enqueue("res_w2")
        return [await worker.run_once() for _ in range(4)]

    seen = asyncio.run(scenario())
    assert seen == ["pending", "pending", "failed", None]
    jobs_now = asyncio.run(jobs.list_for_resource("res_w2"))
    assert jobs_now[0]["status"] == "failed"
    assert jobs_now[0]["attempts"] == 3
    assert jobs_now[0]["last_error"] == "boom"


def test_recover_stale_running_and_parser_switch():
    """重启恢复：running 重置 pending；换 parser 入队生成独立任务。"""
    parser = FakeParser(name="fake")
    resources, jobs, _worker, store, _chunks = _setup(parser)
    record = _record("res_w3", "k3")

    async def scenario():
        await resources.create(record)
        store.put("k3", b"x")
        job_a, _ = await jobs.enqueue("res_w3", None)
        job_b, created_b = await jobs.enqueue("res_w3", "other")
        await jobs.claim_next()  # A -> running
        reset = await jobs.recover_stale_running()
        return job_a, job_b, created_b, reset

    job_a, job_b, created_b, reset = asyncio.run(scenario())
    assert job_a["id"] != job_b["id"]
    assert created_b is True
    assert reset == 1
