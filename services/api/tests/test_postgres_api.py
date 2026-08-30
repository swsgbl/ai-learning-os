"""API 集成测试：数据库驱动的 app（SQLite 替身）全链路。

验收（AGENTS.md + docs/delivery/04）：
- active 状态响应不含 answer/explanation/angles；
- 服务端剩余时间权威；
- 交卷后才能取到解析与答案；
- 重复提交幂等。
"""
from __future__ import annotations

from app.main import create_app
from fastapi.testclient import TestClient

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def test_db_backed_app_full_flow_hides_answers_until_submit() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        assert client.get("/health").json()["status"] == "ok"

        papers = client.get("/api/v1/papers").json()
        assert papers, "数据库 seed 试卷必须可见"

        started = client.post(
            "/api/v1/papers/functions-basics/exams", json={"mode": "exam"}
        )
        assert started.status_code == 201
        exam = started.json()
        exam_id = exam["exam_id"]

        # active 状态不得泄露答案、解析、angles
        raw = str(exam).lower()
        assert '"answer"' not in raw
        assert "explanation" not in raw
        assert '"angles"' not in raw
        assert exam["status"] == "active"
        assert exam["server_remaining_seconds"] > 0
        # 服务端权威：server_end_at 由 API 写入
        assert exam["server_end_at"] > exam["server_started_at"]

        questions = exam["questions"]
        for index, question in enumerate(questions, start=1):
            response = client.put(
                f"/api/v1/exams/{exam_id}/answers",
                json={"sequence": index, "question_id": question["id"], "answer": "A"},
            )
            assert response.status_code == 200

        first = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
        second = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()

        report = first.json()
        assert report["total_count"] == len(questions)
        assert report["questions"], "复盘必须带题目与解析"
        assert all(item["explanation"] for item in report["items"])

        # 复盘可重复读取且与提交响应一致
        fetched = client.get(f"/api/v1/exams/{exam_id}/submission")
        assert fetched.status_code == 200
        assert fetched.json()["score"] == report["score"]


def test_db_backed_app_rejects_unknown_exam_and_question() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        started = client.post("/api/v1/papers/algorithms-basics/exams", json={"mode": "exam"})
        exam_id = started.json()["exam_id"]
        question_id = started.json()["questions"][0]["id"]

        unknown_exam = client.put(
            "/api/v1/exams/exam_missing/answers",
            json={"sequence": 1, "question_id": question_id, "answer": "A"},
        )
        assert unknown_exam.status_code == 404

        unknown_question = client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": "no-such-question", "answer": "A"},
        )
        assert unknown_question.status_code == 404
