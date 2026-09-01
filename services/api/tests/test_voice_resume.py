"""M4-07 断线恢复：重连后从当前题、当前选项和服务端状态继续。

- GET /voice/sessions?exam_id=：断线后凭 exam_id 找回会话；
- GET /voice/sessions/{id}/resume：服务端状态 + 当前题公开内容（不含
  answer/explanation——考试模式不泄露）+ 已提交答案（exam 权威值）；
  只读不迁移——同状态多次恢复恒同输出（幂等）。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _start_exam(client: TestClient) -> str:
    response = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
    assert response.status_code == 201
    return response.json()["exam_id"]


def _commands(client: TestClient, session_id: str, *types: str) -> None:
    for command_type in types:
        response = client.post(
            f"/api/v1/voice/sessions/{session_id}/commands", json={"type": command_type}
        )
        assert response.status_code == 200, response.text


def _session(client: TestClient, exam_id: str) -> str:
    return client.post("/api/v1/voice/sessions", json={"exam_id": exam_id}).json()["session_id"]


def _first_question_id(client: TestClient, exam_id: str) -> str:
    return client.get(f"/api/v1/exams/{exam_id}").json()["questions"][0]["id"]


def test_list_sessions_by_exam() -> None:
    """断线后凭 exam_id 找回会话（含状态与 revision）。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_a = _session(client, exam_id)
        session_b = _session(client, exam_id)
        _commands(client, session_b, "start_reading")

        items = client.get("/api/v1/voice/sessions", params={"exam_id": exam_id}).json()["items"]
        assert [item["session_id"] for item in items] == [session_a, session_b]
        assert items[1]["status"] == "READING_QUESTION"

        other = client.get(
            "/api/v1/voice/sessions", params={"exam_id": "exam_missing"}
        ).json()["items"]
        assert other == []


def test_resume_returns_question_and_committed_answer() -> None:
    """等待期已提交 B 后断线：resume 返回当前题 + 服务端权威答案，不含正确答案。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = _session(client, exam_id)
        _commands(client, session_id, "start_reading", "question_read", "options_read")
        question_id = _first_question_id(client, exam_id)
        assert client.post(
            f"/api/v1/voice/sessions/{session_id}/commands",
            json={"type": "answer_proposed", "question_id": question_id, "answer": "B"},
        ).status_code == 200

        body = client.get(f"/api/v1/voice/sessions/{session_id}/resume").json()
        assert body["session"]["status"] == "ANSWER_COMMITTED"
        assert body["session"]["question_index"] == 0
        assert body["question"]["id"] == question_id
        assert body["question"]["stem"]
        assert [option["key"] for option in body["question"]["options"]] == ["A", "B", "C", "D"]
        assert "answer" not in body["question"]  # 不泄露正确答案
        assert "explanation" not in body["question"]
        assert body["committed_answer"] == "B"  # 服务端权威值，断线前提交不丢失
        assert body["question_total"] >= 1


def test_resume_during_announcement_returns_current_question() -> None:
    """读题播报期断线：resume 返回当前题供客户端重播，committed 为空。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = _session(client, exam_id)
        _commands(client, session_id, "start_reading")

        body = client.get(f"/api/v1/voice/sessions/{session_id}/resume").json()
        assert body["session"]["status"] == "READING_QUESTION"
        assert body["question"]["id"] == _first_question_id(client, exam_id)
        assert body["committed_answer"] is None

        # 重连后客户端凭返回的当前题重播；提交前先 barge_in 打断进倾听（播报期
        # 提交保持 409 关闭——打断不提交半成品，正确流程=先打断再提交）
        assert client.post(
            f"/api/v1/voice/sessions/{session_id}/commands",
            json={"type": "answer_proposed", "question_id": body["question"]["id"], "answer": "C"},
        ).status_code == 409
        assert client.post(
            f"/api/v1/voice/sessions/{session_id}/commands", json={"type": "barge_in"}
        ).status_code == 200
        assert client.post(
            f"/api/v1/voice/sessions/{session_id}/commands",
            json={"type": "answer_proposed", "question_id": body["question"]["id"], "answer": "C"},
        ).status_code == 200
        exam_view = client.get(f"/api/v1/exams/{exam_id}").json()
        assert exam_view["answers"].get(body["question"]["id"]) == "C"


def test_resume_is_idempotent_and_stateless() -> None:
    """只读不迁移：多次恢复恒同输出，revision 不变。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = _session(client, exam_id)
        _commands(client, session_id, "start_reading", "question_read", "options_read")

        first = client.get(f"/api/v1/voice/sessions/{session_id}/resume").json()
        second = client.get(f"/api/v1/voice/sessions/{session_id}/resume").json()
        assert first == second
        assert first["session"]["revision"] == second["session"]["revision"]


def test_resume_after_reconnect_overwrites() -> None:
    """断线前提交 B，重连恢复后改口 C：覆盖语义保持（追加覆盖事件）。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = _session(client, exam_id)
        _commands(client, session_id, "start_reading", "question_read", "options_read")
        question_id = _first_question_id(client, exam_id)
        url = f"/api/v1/voice/sessions/{session_id}/commands"
        assert client.post(
            url, json={"type": "answer_proposed", "question_id": question_id, "answer": "B"}
        ).status_code == 200

        assert client.get(f"/api/v1/voice/sessions/{session_id}/resume").json()["committed_answer"] == "B"
        assert client.post(
            url, json={"type": "answer_proposed", "question_id": question_id, "answer": "C"}
        ).status_code == 200
        exam_view = client.get(f"/api/v1/exams/{exam_id}").json()
        assert exam_view["answers"].get(question_id) == "C"
        assert exam_view["next_sequence"] >= 3


def test_resume_terminal_409_and_missing_404() -> None:
    """终态无可恢复（409）；未知会话（404）；无 DB（503）。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id = _start_exam(client)
        session_id = _session(client, exam_id)
        _commands(client, session_id, "end")
        assert client.get(f"/api/v1/voice/sessions/{session_id}/resume").status_code == 409
        assert client.get("/api/v1/voice/sessions/vs_missing/resume").status_code == 404

    with TestClient(create_app(None)) as client:
        assert client.get("/api/v1/voice/sessions/vs_x/resume").status_code == 503
        assert client.get("/api/v1/voice/sessions", params={"exam_id": "e"}).status_code == 503
