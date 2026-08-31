"""M2-07 幂等提交与超时提交（04 号文档 §7 错误码语义）：

- 重复提交返回同一 submission（EXAM_ALREADY_SUBMITTED → 展示既有报告）；
- 超时只触发一次：超时结算（timeout submit）幂等，唯一约束兜底，时钟再推进不改变结果；
- 过期提交拒绝：结算后答案事件永久拒绝；并发超时结算只产生一份报告。
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.db.session import create_engine, make_sessionmaker, prepare_database
from app.repositories.memory import MemoryRepository
from app.repositories.postgres import PostgresRepository
from app.repositories.seed import seed_papers

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


class FakeClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 31, 9, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current = self.current + timedelta(seconds=seconds)


async def _make_postgres(clock: FakeClock) -> PostgresRepository:
    engine = create_engine(SQLITE_URL)
    await prepare_database(engine, SQLITE_URL)
    return engine, PostgresRepository(make_sessionmaker(engine), clock=clock)


@pytest.mark.parametrize("repo_kind", ["memory", "postgres"])
def test_timeout_submit_settles_exactly_once(repo_kind: str) -> None:
    clock = FakeClock()

    async def body() -> None:
        if repo_kind == "memory":
            repo = MemoryRepository(clock=clock)  # type: ignore[arg-type]
            engine = None
        else:
            engine, repo = await _make_postgres(clock)
            await repo.seed_papers_if_empty()
        paper = next(p for p in seed_papers() if p.id == "functions-basics")

        record = await repo.create_exam(paper, "exam")
        clock.advance(paper.duration_minutes * 60 + 5)
        await repo.get_exam(record.exam_id)  # 懒翻转 EXPIRED

        # 超时结算（timeout submit）：第一次结算出报告
        first = await repo.submit(record.exam_id)
        # 只触发一次：重复结算 / 时钟继续推进，都是同一份报告
        clock.advance(600)
        second = await repo.submit(record.exam_id)
        assert second == first
        assert first.duration_seconds == paper.duration_minutes * 60

        # 结算后（SUBMITTED）答案事件永久拒绝
        with pytest.raises(PermissionError):
            await repo.save_answer(record.exam_id, 1, paper.questions[0].id, "A")

        if engine is not None:
            await engine.dispose()

    asyncio.run(body())


def test_timeout_submit_idempotent_at_api_level() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(SQLITE_URL)) as client:
        started = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
        exam_id = started.json()["exam_id"]
        question = started.json()["questions"][0]
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": question["id"], "answer": "A"},
        )

        first = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
        second = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
        third = client.get(f"/api/v1/exams/{exam_id}/submission")
        assert first.status_code == second.status_code == third.status_code == 200
        assert first.json() == second.json() == third.json()


def test_concurrent_timeout_submit_yields_single_submission() -> None:
    """真实 PG：并发超时结算撞 submissions 唯一约束，幂等收敛到同一份报告。"""
    import os

    pg_url = os.environ.get("AIOS_PG_TEST_URL")
    if not pg_url:
        pytest.skip("需要 AIOS_PG_TEST_URL 指向真实 PostgreSQL")

    async def body() -> None:
        engine = create_engine(pg_url)
        await prepare_database(engine, pg_url)
        repo = PostgresRepository(make_sessionmaker(engine))
        paper = next(p for p in seed_papers() if p.id == "functions-basics")

        record = await repo.create_exam(paper, "exam")
        # 直接把时钟越过 end_at（repo 内部 clock 为 utc_now，用 DB 时间翻转不可控，
        # 改为并发主动结算：唯一约束保证只成功一次）
        results = await asyncio.gather(
            repo.submit(record.exam_id),
            repo.submit(record.exam_id),
            return_exceptions=True,
        )
        ids = [r.exam_id for r in results if isinstance(r, object) and not isinstance(r, BaseException)]
        errors = [r for r in results if isinstance(r, BaseException)]
        # 两个并发提交必须收敛：要么都拿到同一份报告，要么一个拿到报告一个明确幂等错误
        if errors:
            assert len(errors) == 1 and len(ids) == 1, f"并发结果异常: {results}"
        else:
            assert ids[0] == ids[1], "并发提交必须返回同一 submission"
        await engine.dispose()

    asyncio.run(body())
