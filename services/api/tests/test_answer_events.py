"""M2-05 答案 append-only event：sequence 唯一；重复/乱序/并发冲突按明确规则处理。

规则（04 号文档 + 本文件锁定）：
- sequence 从 1 严格递增（乱序拒绝 409）；
- 同 sequence 同内容重复提交幂等返回原状态；
- 同 sequence 不同答案拒绝 409，且原事件不可被篡改（append-only）；
- 考试结束（EXPIRED/SUBMITTED）后禁止追加事件；
- 并发写入撞唯一约束转换为明确 409（uq_answer_events_exam_sequence）。
"""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.main import create_app
from app.repositories.postgres import PostgresRepository

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _start(client: TestClient) -> tuple[str, list[dict]]:
    started = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
    assert started.status_code == 201
    return started.json()["exam_id"], started.json()["questions"]


def test_out_of_order_sequence_rejected_with_409() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id, questions = _start(client)
        response = client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 3, "question_id": questions[0]["id"], "answer": "A"},
        )
        assert response.status_code == 409
        assert "递增" in response.json()["detail"]


def test_duplicate_same_content_is_idempotent() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id, questions = _start(client)
        payload = {"sequence": 1, "question_id": questions[0]["id"], "answer": "A"}
        first = client.put(f"/api/v1/exams/{exam_id}/answers", json=payload)
        second = client.put(f"/api/v1/exams/{exam_id}/answers", json=payload)
        assert first.status_code == second.status_code == 200
        assert first.json()["answers"] == second.json()["answers"]


def test_conflicting_answer_on_same_sequence_rejected_and_original_intact() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id, questions = _start(client)
        ok = client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": questions[0]["id"], "answer": "A"},
        )
        conflict = client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": questions[0]["id"], "answer": "B"},
        )
        assert ok.status_code == 200
        assert conflict.status_code == 409
        # append-only：原事件不被篡改（API answers + 事件流仍为原值）
        after = client.get(f"/api/v1/exams/{exam_id}").json()
        assert after["answers"][questions[0]["id"]] == "A"

        async def _events() -> list[tuple[int, str]]:
            repo = PostgresRepository(client.app.state.sessionmaker)
            record = await repo.get_exam(exam_id)
            return [(event.sequence, event.answer) for event in (record.events if record else [])]

        assert asyncio.run(_events()) == [(1, "A")]


def test_events_are_append_only_after_rejection() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id, questions = _start(client)
        for index, question in enumerate(questions[:3], start=1):
            client.put(
                f"/api/v1/exams/{exam_id}/answers",
                json={"sequence": index, "question_id": question["id"], "answer": "A"},
            )
        # 一次乱序写入被拒后，事件流保持原样
        rejected = client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 9, "question_id": questions[0]["id"], "answer": "C"},
        )
        assert rejected.status_code == 409

        async def _sequences() -> list[int]:
            repo = PostgresRepository(client.app.state.sessionmaker)
            record = await repo.get_exam(exam_id)
            return [event.sequence for event in (record.events if record else [])]

        assert asyncio.run(_sequences()) == [1, 2, 3]


def test_no_events_after_exam_ended() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id, questions = _start(client)
        submitted = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
        assert submitted.status_code == 200
        late = client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": questions[0]["id"], "answer": "A"},
        )
        assert late.status_code == 409
