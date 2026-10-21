"""M15-024 学习视图：材料卷章节结构/学习目标/逐章练习归属。"""
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


_DAG = {
    "nodes": [
        {"id": "sql:select", "canonical_name": "SELECT 基础查询", "subject": "sql", "description": "d", "difficulty": 1},
    ],
    "edges": [],
    "note": "study-test",
}


def _publish_dag(client, headers, username):
    client.portal.call(client.app.state.users.set_role, username, "admin")
    resp = client.post("/api/v1/concept-dag/versions", json=_DAG, headers=headers)
    assert resp.status_code == 201, resp.text


def _login(client, username):
    client.post("/api/v1/auth/register", json={"username": username, "password": "pass-word-1234"})
    token = client.post("/api/v1/auth/login", json={"username": username, "password": "pass-word-1234"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _materialize(client, headers, goal):
    draft = client.post("/api/v1/courses/generation-drafts", json={"goal": goal}, headers=headers)
    assert draft.status_code == 201, draft.text
    mat = client.post(f"/api/v1/courses/generation-drafts/{draft.json()['id']}/materialize", headers=headers)
    assert mat.status_code == 201, mat.text
    return mat.json()["paper_id"]


def test_study_view_chapters_and_questions(client):
    h = _login(client, "study_alice")
    _publish_dag(client, h, 'study_alice')
    paper_id = _materialize(client, h, "学 SQL")
    resp = client.get(f"/api/v1/papers/{paper_id}/study", headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["paper_title"].startswith("定制课程")
    assert body["chapter_count"] >= 1
    ch = body["chapters"][0]
    assert ch["title"]
    assert "objectives" in ch
    assert len(ch["questions"]) >= 1
    assert ch["questions"][0]["stem"]


def test_study_view_404_for_non_course_paper(client):
    h = _login(client, "study_bob")
    assert client.get("/api/v1/papers/nonexistent/study", headers=h).status_code == 404


def test_study_view_owner_isolation(client):
    a = _login(client, "study_carol")
    _publish_dag(client, a, 'study_carol')
    paper_id = _materialize(client, a, "学 SQL")
    b = _login(client, "study_dave")
    assert client.get(f"/api/v1/papers/{paper_id}/study", headers=b).status_code == 404
