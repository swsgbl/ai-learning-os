"""M2-04 服务端权威计时：start/end 服务器写入；改时间/刷新/重连不能延长考试。"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.db.session import create_engine, make_sessionmaker, prepare_database
from app.domain.models import ExamStatus
from app.main import create_app
from app.repositories.memory import MemoryRepository, remaining_seconds
from app.repositories.postgres import PostgresRepository
from app.repositories.seed import seed_papers

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def test_start_exam_times_are_server_written_and_ignore_client_fields() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        papers = {p["id"]: p for p in client.get("/api/v1/papers").json()}
        duration = papers["functions-basics"]["duration_minutes"] * 60
        # 客户端伪造时间字段必须被忽略
        started = client.post(
            "/api/v1/papers/functions-basics/exams",
            json={"mode": "exam", "started_at": "2000-01-01T00:00:00Z", "extra_minutes": 9999},
        )
        assert started.status_code == 201
        exam = started.json()
        begin = datetime.fromisoformat(exam["server_started_at"])
        end = datetime.fromisoformat(exam["server_end_at"])
        # 起止差 == 试卷时长，与客户端提供的任何值无关
        assert (end - begin).total_seconds() == duration
        assert begin.year >= 2026, "start 必须来自服务器时钟"


def test_refresh_and_reconnect_cannot_extend_server_clock() -> None:
    """多次 get_exam（刷新/重连）end_at 不变；服务端时钟推进只会缩短剩余时间。"""
    start = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    state = {"now": start}

    def clock() -> datetime:
        return state["now"]

    async def _flow() -> None:
        engine = create_engine(SQLITE_URL)
        await prepare_database(engine, SQLITE_URL)
        repo = PostgresRepository(make_sessionmaker(engine), clock=clock)
        paper = next(p for p in seed_papers() if p.id == "functions-basics")
        record = await repo.create_exam(paper, "exam")

        seen_ends: list[datetime] = []
        for _ in range(3):  # 模拟三次刷新/重连
            current = await repo.get_exam(record.exam_id)
            assert current is not None
            seen_ends.append(current.end_at)
            state["now"] += timedelta(minutes=2)
        assert len(set(seen_ends)) == 1, "end_at 固定在服务端，刷新不能延长"

        after = await repo.get_exam(record.exam_id)
        assert after is not None
        assert remaining_seconds(after, state["now"]) < remaining_seconds(after, start)
        await engine.dispose()

    asyncio.run(_flow())


def test_timeout_settles_duration_at_server_end_not_wall_clock() -> None:
    """超时翻转 EXPIRED 后：禁止作答；交卷用时按 min(now, end_at) 结算。"""
    start = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    state = {"now": start}

    def clock() -> datetime:
        return state["now"]

    repo = MemoryRepository(clock=clock)
    paper = next(p for p in seed_papers() if p.id == "functions-basics")
    record = asyncio.run(repo.create_exam(paper, "exam"))

    state["now"] = start + timedelta(minutes=45)  # 越过试卷时限
    expired = asyncio.run(repo.get_exam(record.exam_id))
    assert expired is not None and expired.status == ExamStatus.EXPIRED

    import pytest

    with pytest.raises(PermissionError):
        asyncio.run(repo.save_answer(record.exam_id, 1, paper.questions[0].id, "A"))

    report = asyncio.run(repo.submit(record.exam_id))
    assert report.duration_seconds == paper.duration_minutes * 60, (
        "用时按 min(now, end_at) 结算，不随挂钟延长"
    )


def test_client_submitted_time_fields_never_affect_grading_clock() -> None:
    """答案/提交请求携带伪造时间字段不影响服务端结算。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        started = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
        exam_id = started.json()["exam_id"]
        question = started.json()["questions"][0]
        saved = client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={
                "sequence": 1,
                "question_id": question["id"],
                "answer": "A",
                "answered_at": "1999-01-01T00:00:00Z",
            },
        )
        assert saved.status_code == 200
        report = client.post(
            f"/api/v1/exams/{exam_id}/submit",
            json={"submitted_at": "1999-01-01T00:00:00Z"},
        )
        assert report.status_code == 200
        assert 0 <= report.json()["duration_seconds"] <= 6 * 60
