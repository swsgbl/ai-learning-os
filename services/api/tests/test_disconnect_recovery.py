"""M2-06 自动保存与断线恢复：刷新/断网后返回服务端答案与 server_remaining_seconds。

- GET /exams/{id} 恢复完整作答状态（answers + 剩余时间 + next_sequence）；
- next_sequence 为服务端权威序号：恢复后客户端由此继续追加答案，不再自行计数；
- 断线重传同序号同内容幂等（M2-05 已锁），此处锁定恢复后继续作答链路。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def test_refresh_restores_server_answers_and_remaining() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        started = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
        exam_id = started.json()["exam_id"]
        questions = started.json()["questions"]
        for sequence, question in enumerate(questions[:2], start=1):
            client.put(
                f"/api/v1/exams/{exam_id}/answers",
                json={"sequence": sequence, "question_id": question["id"], "answer": "A"},
            )

        # 模拟刷新/重连：全新 GET 返回服务端权威状态
        restored = client.get(f"/api/v1/exams/{exam_id}").json()
        assert list(restored["answers"].values()) == ["A", "A"]
        assert restored["server_remaining_seconds"] > 0
        assert restored["status"] == "active"
        # 服务端权威序号：已消费 2 个事件，下一个是 3
        assert restored["next_sequence"] == 3


def test_client_resumes_saving_from_server_next_sequence() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        started = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
        exam_id = started.json()["exam_id"]
        questions = started.json()["questions"]

        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": questions[0]["id"], "answer": "A"},
        )
        # 断线恢复：客户端丢弃本地计数，采用服务端 next_sequence 继续
        restored = client.get(f"/api/v1/exams/{exam_id}").json()
        resumed = client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={
                "sequence": restored["next_sequence"],
                "question_id": questions[1]["id"],
                "answer": "B",
            },
        )
        assert resumed.status_code == 200
        assert resumed.json()["next_sequence"] == 3

        final = client.get(f"/api/v1/exams/{exam_id}").json()
        assert len(final["answers"]) == 2


def test_recovery_of_unknown_exam_returns_404() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        response = client.get("/api/v1/exams/exam_missing")
        assert response.status_code == 404
