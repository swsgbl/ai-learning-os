"""Repository 契约测试：内存实现与 PostgreSQL(SQLite 替身)实现必须行为一致。

覆盖 docs/delivery/04 + AGENT_PROMPTS M0-06 验收：
- sequence 唯一/递增/同载荷幂等/异载荷冲突；
- 重复提交幂等返回同一 submission；
- 考试过期后拒绝答案、服务端时钟权威（客户端无法延长考试）；
- answer_events/submissions 的唯一约束由数据库兜底。
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.db.orm import AnswerEventRow
from app.db.session import create_engine, make_sessionmaker, prepare_database
from app.domain.models import ExamStatus
from app.repositories.memory import MemoryRepository
from app.repositories.postgres import PostgresRepository

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


class FakeClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 31, 9, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current = self.current + timedelta(seconds=seconds)


async def make_sqlite_repo(clock: FakeClock) -> PostgresRepository:
    engine = create_engine(SQLITE_URL)
    await prepare_database(engine, SQLITE_URL)
    repo = PostgresRepository(make_sessionmaker(engine), clock=clock)
    await repo.seed_papers_if_empty()
    return repo


async def run_contract(repo, clock: FakeClock, paper_id: str) -> None:
    papers = await repo.list_papers()
    assert papers, "seed 试卷必须存在"
    paper = await repo.get_paper(paper_id)
    assert paper

    # 服务端时钟权威：end_at 只由 repository 写入
    exam = await repo.create_exam(paper, "exam")
    assert exam.status == ExamStatus.ACTIVE
    assert exam.end_at - exam.started_at == timedelta(minutes=paper.duration_minutes)
    assert 0 < (exam.end_at - clock.current).total_seconds() <= paper.duration_minutes * 60

    first = paper.questions[0]
    await repo.save_answer(exam.exam_id, 1, first.id, "A")
    # 同序号同载荷幂等
    await repo.save_answer(exam.exam_id, 1, first.id, "A")
    # 同序号异载荷冲突
    with pytest.raises(ValueError):
        await repo.save_answer(exam.exam_id, 1, first.id, "B")
    # 乱序（跳号或回退）拒绝
    with pytest.raises(ValueError):
        await repo.save_answer(exam.exam_id, 3, first.id, "C")

    second = paper.questions[1]
    await repo.save_answer(exam.exam_id, 2, second.id, "B")

    # 重复提交幂等
    submission = await repo.submit(exam.exam_id)
    assert await repo.submit(exam.exam_id) == submission
    assert submission.total_count == len(paper.questions)

    # 交卷后答案事件被拒绝
    with pytest.raises(PermissionError):
        await repo.save_answer(exam.exam_id, 3, first.id, "C")

    # 过期：服务端时钟越过 end_at 后禁止作答，get_exam 翻转 EXPIRED
    expired = await repo.create_exam(paper, "exam")
    clock.advance(paper.duration_minutes * 60 + 1)
    record = await repo.get_exam(expired.exam_id)
    assert record is not None and record.status == ExamStatus.EXPIRED
    with pytest.raises(PermissionError):
        await repo.save_answer(expired.exam_id, 1, first.id, "A")

    # 超时提交以 min(now, end_at) 计算用时，客户端无法延长考试
    late = await repo.submit(expired.exam_id)
    assert late.duration_seconds <= paper.duration_minutes * 60


def test_memory_repository_contract() -> None:
    clock = FakeClock()

    async def body() -> None:
        await run_contract(MemoryRepository(clock=clock), clock, "algorithms-basics")

    asyncio.run(body())


def test_postgres_repository_contract_on_sqlite() -> None:
    clock = FakeClock()

    async def body() -> None:
        repo = await make_sqlite_repo(clock)
        await run_contract(repo, clock, "algorithms-basics")

    asyncio.run(body())


def test_sqlite_unique_constraints_backstop() -> None:
    """即使应用层校验被绕过，数据库唯一约束也必须兜底。"""

    async def body() -> None:
        clock = FakeClock()
        engine = create_engine(SQLITE_URL)
        await prepare_database(engine, SQLITE_URL)
        repo = PostgresRepository(make_sessionmaker(engine), clock=clock)
        await repo.seed_papers_if_empty()
        paper = await repo.get_paper("algorithms-basics")
        assert paper
        exam = await repo.create_exam(paper, "exam")
        await repo.save_answer(exam.exam_id, 1, paper.questions[0].id, "A")

        from sqlalchemy import insert

        async with engine.begin() as conn:
            with pytest.raises(Exception):  # noqa: B017 - 底层 IntegrityError
                await conn.execute(
                    insert(AnswerEventRow).values(
                        exam_session_id=exam.exam_id,
                        sequence=1,
                        question_id=paper.questions[0].id,
                        answer="B",
                        occurred_at=datetime(2026, 8, 31, 9, 0, 1, tzinfo=UTC),
                    )
                )

    asyncio.run(body())
