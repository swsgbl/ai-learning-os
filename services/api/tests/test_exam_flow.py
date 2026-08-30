from app.main import create_app
from fastapi.testclient import TestClient


def test_health() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_exam_hides_answers_and_uses_server_clock() -> None:
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "active"
        assert body["server_remaining_seconds"] > 0
        assert "answer" not in str(body["questions"])


def test_answers_submit_and_idempotent_review() -> None:
    with TestClient(create_app()) as client:
        started = client.post("/api/v1/papers/algorithms-basics/exams", json={"mode": "voice"}).json()
        exam_id = started["exam_id"]
        questions = started["questions"]

        assert client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": questions[0]["id"], "answer": "B"},
        ).status_code == 200
        assert client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": questions[0]["id"], "answer": "B"},
        ).status_code == 200

        first = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
        second = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert first.json()["correct_count"] == 1
        assert first.json()["score"] == 33
