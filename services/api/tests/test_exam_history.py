"""M15-023 我的考试历史：归属过滤/摘要字段/未交卷无分。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
SECRET = "x" * 32


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    get_settings.cache_clear()
    with TestClient(create_app(SQLITE_URL)) as c:
        yield c
    get_settings.cache_clear()


def _login(client, username):
    client.post("/api/v1/auth/register", json={"username": username, "password": "pass-word-1234"})
    token = client.post("/api/v1/auth/login", json={"username": username, "password": "pass-word-1234"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_history_requires_auth_and_is_empty_initially(client):
    assert client.get("/api/v1/exams/history").status_code == 401
    h = _login(client, "hist_alice")
    body = client.get("/api/v1/exams/history", headers=h).json()
    assert body["items"] == []


def test_history_lists_submitted_exam_with_score(client, monkeypatch):
    h = _login(client, "hist_bob")
    # 用 seed 公共卷（memory 仓库 seed functions-basics）开考交卷
    start = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"}, headers=h)
    assert start.status_code == 201, start.text
    exam_id = start.json()["exam_id"]
    questions = start.json()["questions"]
    for i, q in enumerate(questions[:1], start=1):
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": i, "question_id": q["id"], "answer": "A"},
            headers=h,
        )
    client.post(f"/api/v1/exams/{exam_id}/submit", json={}, headers=h)
    items = client.get("/api/v1/exams/history", headers=h).json()["items"]
    assert len(items) >= 1
    mine = next(i for i in items if i["exam_id"] == exam_id)
    assert mine["has_report"] is True
    assert mine["score"] is not None
    assert mine["question_count"] == len(questions)
    other = _login(client, "hist_carol")
    assert all(i["exam_id"] != exam_id for i in client.get("/api/v1/exams/history", headers=other).json()["items"])
