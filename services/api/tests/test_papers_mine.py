"""M15-007 /papers mine 字段契约：自有卷（materialize 定制课程）true、公共卷 false。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
SECRET = "x" * 32

CALCULUS_DAG = {
    "nodes": [
        {"id": "c_limit", "canonical_name": "极限", "subject": "math", "difficulty": 2,
         "description": "极限描述函数或数列的趋近行为"},
        {"id": "c_deriv", "canonical_name": "导数", "aliases": ["微分"], "subject": "math",
         "difficulty": 3, "description": "导数刻画瞬时变化率"},
        {"id": "c_int", "canonical_name": "积分", "subject": "math", "difficulty": 4,
         "description": "积分是微分的逆运算"},
    ],
    "edges": [
        {"prerequisite_id": "c_limit", "concept_id": "c_deriv"},
        {"prerequisite_id": "c_deriv", "concept_id": "c_int"},
    ],
    "note": "微积分入门图谱",
}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    get_settings.cache_clear()
    with TestClient(create_app(SQLITE_URL)) as c:
        yield c
    get_settings.cache_clear()


def _register_login(client: TestClient, username: str) -> dict:
    client.post("/api/v1/auth/register", json={"username": username, "password": "pass-word-1234"})
    token = client.post("/api/v1/auth/login", json={"username": username, "password": "pass-word-1234"}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def test_papers_mine_flag(client):
    alice = _register_login(client, "mine_alice")
    bob = _register_login(client, "mine_bob")

    # DAG 发布需 admin（M9-04 治理门禁）——测试内经 lifespan portal 直升角色
    # （生产由运维 admin CLI 升角色，同 set_role 通道）
    client.portal.call(client.app.state.users.set_role, "mine_alice", "admin")

    # 公共种子卷 → mine=false
    body = client.get("/api/v1/papers", headers=alice).json()
    assert body, "种子公共卷应存在"
    assert all(p["mine"] is False for p in body)

    # Alice 发布 DAG（learner 可发布）→ 生成草稿 → materialize 自有卷
    dag = client.post("/api/v1/concept-dag/versions", json=CALCULUS_DAG, headers=alice)
    assert dag.status_code == 201, dag.text
    draft = client.post(
        "/api/v1/courses/generation-drafts", json={"goal": "我想学导数与积分"}, headers=alice
    )
    assert draft.status_code == 201, draft.text
    draft_id = draft.json()["id"]
    materialized = client.post(
        f"/api/v1/courses/generation-drafts/{draft_id}/materialize", headers=alice
    )
    assert materialized.status_code == 201, materialized.text

    # 对 Alice：自有卷 mine=true；对 Bob：不可见（可见性不变）且全 false
    alice_papers = client.get("/api/v1/papers", headers=alice).json()
    mine_papers = [p for p in alice_papers if p["mine"] is True]
    assert len(mine_papers) == 1
    assert mine_papers[0]["title"].startswith("定制课程")

    bob_papers = client.get("/api/v1/papers", headers=bob).json()
    assert all(p["mine"] is False for p in bob_papers)
    assert mine_papers[0]["id"] not in {p["id"] for p in bob_papers}
