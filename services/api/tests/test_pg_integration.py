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


def test_essay_rubric_flow_against_real_postgres() -> None:
    """M2-10：真实 PG 下 essay 走 rubric 管线（evidence 表为空时 keyword judge 不引用 evidence，可落分）。"""
    paper = {
        "title": "PG rubric 验证卷",
        "duration_seconds": 600,
        "questions": [
            {
                "question": {
                    "question_type": "essay",
                    "stem": "论述归并排序",
                    "answer": {"rubric_points": ["分治", "合并"]},
                    "explanation": "分治 + 合并",
                },
                "score": 1.0,
            },
        ],
    }
    with TestClient(create_app(PG_URL)) as client:  # type: ignore[arg-type]
        (paper_id,) = client.post("/api/v1/papers/import", json=[paper]).json()["imported"]
        started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
        assert started.status_code == 201
        exam_id = started.json()["exam_id"]
        (question,) = started.json()["questions"]
        assert (
            client.put(
                f"/api/v1/exams/{exam_id}/answers",
                json={"sequence": 1, "question_id": question["id"], "answer": "归并排序是分治算法，逐层合并"},
            ).status_code
            == 200
        )
        report = client.post(f"/api/v1/exams/{exam_id}/submit", json={}).json()
        (item,) = report["items"]
        assert item["correct"] is True
        assert item["rubric"]["rule_version"] == "rubric-v1"
        assert item["rubric"]["score_ratio"] == 1.0
        assert {c["point"] for c in item["rubric"]["criteria"]} == {"分治", "合并"}


def test_exam_report_flow_against_real_postgres() -> None:
    """M2-11：真实 PG 下报告聚合端到端——题分/概念分/错题/补救任务从落库数据构建。"""
    paper = {
        "title": "PG 报告验证卷",
        "duration_seconds": 600,
        "questions": [
            {
                "question": {
                    "question_type": "mcq",
                    "stem": "1+1=?",
                    "options": ["1", "2"],
                    "answer": {"option_index": 1},
                    "explanation": "加法",
                    "concept_ids": ["arithmetic"],
                },
                "score": 2.0,
            },
            {
                "question": {
                    "question_type": "numeric",
                    "stem": "圆周率保留两位",
                    "answer": {"value": 3.14, "tolerance": 0.01},
                    "explanation": "pi",
                    "concept_ids": ["geometry"],
                },
                "score": 3.0,
            },
        ],
    }
    with TestClient(create_app(PG_URL)) as client:  # type: ignore[arg-type]
        (paper_id,) = client.post("/api/v1/papers/import", json=[paper]).json()["imported"]
        started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
        exam_id = started.json()["exam_id"]
        questions = started.json()["questions"]
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": questions[0]["id"], "answer": "B"},
        )
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 2, "question_id": questions[1]["id"], "answer": "3.2"},
        )
        client.post(f"/api/v1/exams/{exam_id}/submit", json={})

        report = client.get(f"/api/v1/exams/{exam_id}/report").json()
        assert report["score_max"] == 5.0
        assert report["score_earned"] == 2.0
        concepts = {c["concept"]: c for c in report["concepts"]}
        assert concepts["arithmetic"]["ratio"] == 1.0
        assert concepts["geometry"]["ratio"] == 0.0
        (mistake,) = report["mistakes"]
        assert mistake["question_id"] == questions[1]["id"]
        (task_id,) = mistake["remediation_task_ids"]
        assert report["remediation_tasks"][task_id]["detail"] == "geometry"


def test_learning_events_flow_against_real_postgres() -> None:
    """M3-01：真实 PG 下标准化学习事件端到端——事件源重放、attempt、判分一致。"""
    paper = {
        "title": "PG 学习事件验证卷",
        "duration_seconds": 600,
        "questions": [
            {
                "question": {
                    "question_type": "mcq",
                    "stem": "1+1=?",
                    "options": ["1", "2"],
                    "answer": {"option_index": 1},
                    "explanation": "加法",
                    "concept_ids": ["arithmetic"],
                    "difficulty": 5,
                },
                "score": 1.0,
            },
        ],
    }
    with TestClient(create_app(PG_URL)) as client:  # type: ignore[arg-type]
        (paper_id,) = client.post("/api/v1/papers/import", json=[paper]).json()["imported"]
        started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
        exam_id = started.json()["exam_id"]
        (question,) = started.json()["questions"]
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": question["id"], "answer": "A"},
        )
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 2, "question_id": question["id"], "answer": "B"},
        )

        stream = client.get(f"/api/v1/exams/{exam_id}/learning-events").json()
        (first, second) = stream["events"]
        assert (first["attempt_number"], first["correctness"]) == (1, False)
        assert (second["attempt_number"], second["correctness"]) == (2, True)
        assert first["concept_ids"] == ["arithmetic"]
        assert first["difficulty"] == 5  # QuestionSpec.difficulty 落库并透传到学习事件
        assert first["event_type"] == "answer"
        assert first["hint_used"] is False
        assert second["latency_ms"] >= 0

        replay = client.get(f"/api/v1/exams/{exam_id}/learning-events").json()
        assert replay == stream


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
