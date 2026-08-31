"""真实 PostgreSQL 集成测试。

设置 AIOS_PG_TEST_URL（如 docker compose 里的 postgres）才运行，否则跳过：
    AIOS_PG_TEST_URL=postgresql+asyncpg://aios:aios@127.0.0.1:5432/ai_learning_os \
        .venv/Scripts/python -m pytest services/api/tests/test_pg_integration.py
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

PG_URL = os.environ.get("AIOS_PG_TEST_URL")

pytestmark = pytest.mark.skipif(not PG_URL, reason="需要 AIOS_PG_TEST_URL 指向真实 PostgreSQL")


def test_exam_flow_against_real_postgres() -> None:
    with TestClient(create_app(PG_URL)) as client:  # type: ignore[arg-type]
        papers = client.get("/api/v1/papers").json()
        assert papers

        started = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
        assert started.status_code == 201
        exam = started.json()
        raw = str(exam).lower()
        assert '"answer"' not in raw and "explanation" not in raw

        exam_id = exam["exam_id"]
        for index, question in enumerate(exam["questions"], start=1):
            assert (
                client.put(
                    f"/api/v1/exams/{exam_id}/answers",
                    json={"sequence": index, "question_id": question["id"], "answer": "B"},
                ).status_code
                == 200
            )

        first = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
        second = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
        assert first.json() == second.json()
        assert client.get(f"/api/v1/exams/{exam_id}/submission").status_code == 200


def test_concurrent_answer_writers_get_explicit_outcome() -> None:
    """M2-05 并发同 sequence 写入：一个成功一个明确拒绝，绝无未处理 IntegrityError。"""
    import asyncio

    from app.db.session import create_engine, make_sessionmaker, prepare_database
    from app.repositories.postgres import PostgresRepository as Repo
    from app.repositories.seed import seed_papers

    async def _flow() -> None:
        engine = create_engine(PG_URL)  # type: ignore[arg-type]
        await prepare_database(engine, PG_URL)  # type: ignore[arg-type]
        repo = Repo(make_sessionmaker(engine))
        paper = next(p for p in seed_papers() if p.id == "functions-basics")
        record = await repo.create_exam(paper, "exam")
        question = paper.questions[0]

        results = await asyncio.gather(
            repo.save_answer(record.exam_id, 1, question.id, "A"),
            repo.save_answer(record.exam_id, 1, question.id, "B"),
            return_exceptions=True,
        )
        outcomes = sorted(type(result).__name__ for result in results)
        assert outcomes == ["ExamSessionRecord", "ValueError"], (
            f"并发结果必须是一个成功一个明确拒绝，实际: {outcomes}"
        )
        await engine.dispose()

    asyncio.run(_flow())
