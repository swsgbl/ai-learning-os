"""M9-02 数据归属拆分：三域隔离语义（auth on）与本地调试模式零破坏（auth off）。

语义矩阵（ADR 66）：
- auth off：写入 owner=NULL、读取不过滤——现状行为，存量测试零破坏；
- auth on：写入 owner=me；读取严格 ==me；他人/无主资源一律 404（不暴露存在性）；
- resources dedup 作用域 = (owner, content_hash)——跨用户同内容各自保留，
  杜绝「B 通过 dedup 读到 A 的对象」；
- voice 域经 exam 锚定归属（session -> exam -> owner），无独立列。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
SECRET = "m9-02-isolation-secret-0123456789abcdef"


@pytest.fixture()
def auth_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    get_settings.cache_clear()


class User:
    def __init__(self, client: TestClient, name: str) -> None:
        self.headers = self._login(client, name)


    @staticmethod
    def _login(client: TestClient, name: str) -> dict[str, str]:
        body = {"username": name, "password": "password-123"}
        client.post("/api/v1/auth/register", json=body)
        token = client.post("/api/v1/auth/login", json=body).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}


PAPER = {
    "title": "Ownership Paper",
    "duration_seconds": 1800,
    "questions": [
        {
            "question": {
                "question_type": "mcq",
                "stem": "2+2=?",
                "options": ["3", "4"],
                "answer": {"option_index": 1},
                "explanation": "加法",
                "concept_ids": ["arith"],
                "difficulty": 2,
            },
            "score": 1.0,
        }
    ],
}


def _upload(client: TestClient, headers: dict, name="a.pdf", content=b"pdf-own-v1"):
    return client.post(
        "/api/v1/resources/upload",
        files={"file": (name, content, "application/pdf")},
        headers=headers,
    )


# --- resources ---


def test_resources_are_invisible_to_other_users(auth_on) -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        alice = User(client, "alice_res")
        bob = User(client, "bob_res")

        rid = _upload(client, alice.headers).json()["id"]

        assert client.get(f"/api/v1/resources/{rid}", headers=alice.headers).status_code == 200
        assert client.get(f"/api/v1/resources/{rid}", headers=bob.headers).status_code == 404
        # 浏览器 cookie 已按设计认证；清空后才是真正的未携带凭据 401。
        client.cookies.clear()
        assert client.get(f"/api/v1/resources/{rid}").status_code == 401

        # 下游读取面同样隔离：parse / chunks / jobs
        assert client.post(f"/api/v1/resources/{rid}/parse", headers=bob.headers).status_code == 404
        assert client.get(f"/api/v1/resources/{rid}/chunks", headers=bob.headers).status_code == 404
        assert client.post(f"/api/v1/resources/{rid}/jobs", headers=bob.headers).status_code == 404


def test_resource_dedup_never_crosses_users(auth_on) -> None:
    """同内容跨用户各自保留——dedup 泄漏即对象泄漏。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        alice = User(client, "alice_dedup")
        bob = User(client, "bob_dedup")

        first = _upload(client, alice.headers).json()
        assert first["deduplicated"] is False
        again = _upload(client, alice.headers).json()
        assert again["deduplicated"] is True and again["id"] == first["id"]

        bob_upload = _upload(client, bob.headers).json()
        assert bob_upload["deduplicated"] is False, "跨用户同内容不得共享资源"
        assert bob_upload["id"] != first["id"]

        bob_again = _upload(client, bob.headers).json()
        assert bob_again["deduplicated"] is True and bob_again["id"] == bob_upload["id"]


def test_auth_off_keeps_global_dedup_and_visibility(monkeypatch: pytest.MonkeyPatch) -> None:
    """auth off = 本地调试模式：dedup 全局、无归属（现状语义零破坏）。"""
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app(SQLITE_URL)) as client:
        first = _upload(client, {}).json()
        again = _upload(client, {}).json()
        assert again["deduplicated"] is True and again["id"] == first["id"]
        assert client.get(f"/api/v1/resources/{first['id']}").status_code == 200


# --- exams ---


def _start_exam(client: TestClient, headers: dict) -> str:
    # papers 公共题库：首个用户导入后其余用户复用（重复导入失败无妨）
    client.post("/api/v1/papers/import", json=[PAPER], headers=headers)
    papers = client.get("/api/v1/papers", headers=headers).json()
    paper_id = next(p["id"] for p in papers if p["title"] == "Ownership Paper")
    started = client.post(
        f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"}, headers=headers
    )
    assert started.status_code == 201
    return started.json()["exam_id"]


def test_exams_are_invisible_to_other_users(auth_on) -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        alice = User(client, "alice_exam")
        bob = User(client, "bob_exam")
        exam_id = _start_exam(client, alice.headers)

        # A 全链路可用；B 的 get/answers/submit/report 一律 404
        assert client.get(f"/api/v1/exams/{exam_id}", headers=alice.headers).status_code == 200
        answer = {"sequence": 1, "question_id": "qx", "answer": "B"}
        for method, path, payload in (
            ("get", f"/api/v1/exams/{exam_id}", None),
            ("put", f"/api/v1/exams/{exam_id}/answers", answer),
            ("post", f"/api/v1/exams/{exam_id}/submit", {}),
            ("get", f"/api/v1/exams/{exam_id}/submission", None),
            ("get", f"/api/v1/exams/{exam_id}/report", None),
            ("get", f"/api/v1/exams/{exam_id}/learning-events", None),
        ):
            kwargs = {"headers": bob.headers}
            if payload is not None:
                kwargs["json"] = payload
            r = getattr(client, method)(path, **kwargs)
            assert r.status_code == 404, f"{method} {path} -> {r.status_code}"


def test_exam_owner_can_complete_own_exam(auth_on) -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        alice = User(client, "alice_flow")
        exam_id = _start_exam(client, alice.headers)
        exam = client.get(f"/api/v1/exams/{exam_id}", headers=alice.headers).json()
        question_id = exam["questions"][0]["id"]
        put = client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": question_id, "answer": "B"},
            headers=alice.headers,
        )
        assert put.status_code == 200
        submitted = client.post(f"/api/v1/exams/{exam_id}/submit", json={}, headers=alice.headers)
        assert submitted.status_code == 200
        report = client.get(f"/api/v1/exams/{exam_id}/report", headers=alice.headers)
        assert report.status_code == 200


# --- voice（经 exam 锚定）---


def test_voice_session_cannot_anchor_to_foreign_exam(auth_on) -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        alice = User(client, "alice_voice")
        bob = User(client, "bob_voice")
        exam_id = _start_exam(client, alice.headers)

        assert client.post(
            "/api/v1/voice/sessions", json={"exam_id": exam_id}, headers=bob.headers
        ).status_code == 404
        ok = client.post(
            "/api/v1/voice/sessions", json={"exam_id": exam_id}, headers=alice.headers
        )
        assert ok.status_code == 201
        session_id = ok.json()["session_id"]

        # B 不能读/操作 A 的语音会话；A 可以
        assert client.get(f"/api/v1/voice/sessions/{session_id}", headers=bob.headers).status_code == 404
        assert client.get(f"/api/v1/voice/sessions/{session_id}", headers=alice.headers).status_code == 200
        # B 列 A 的考试的会话 -> 考试本身 404（与 create 语义一致）
        assert client.get(
            "/api/v1/voice/sessions", params={"exam_id": exam_id}, headers=bob.headers
        ).status_code == 404


# --- student 聚合收缩 ---


def test_student_recompute_scopes_to_owner(auth_on) -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        alice = User(client, "alice_agg")
        bob = User(client, "bob_agg")
        exam_id = _start_exam(client, alice.headers)
        exam = client.get(f"/api/v1/exams/{exam_id}", headers=alice.headers).json()
        question_id = exam["questions"][0]["id"]
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": question_id, "answer": "A"},  # 错题 -> 学习事件
            headers=alice.headers,
        )
        client.post(f"/api/v1/exams/{exam_id}/submit", json={}, headers=alice.headers)

        alice_events = client.get(
            f"/api/v1/exams/{exam_id}/learning-events", headers=alice.headers
        )
        bob_events = client.get(f"/api/v1/exams/{exam_id}/learning-events", headers=bob.headers)
        assert alice_events.status_code == 200
        assert bob_events.status_code == 404
