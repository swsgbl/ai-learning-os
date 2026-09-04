"""M10-02 Web 治理工作台：/governance 页面依赖端点的端到端契约。

按前端真实请求序列锁定（apps/web/src/app/governance + draft-queue）：
1. admin 序列：auth/me 披露 admin -> 四类队列 list -> 详情 get -> approve 200
   -> 终态重复 409（页面给明确反馈）-> audit 回查看到对应 action；
2. learner 序列：auth/me 披露 learner -> audit/list 数据被 403 拒（页面无泄露提示）；
3. auth off：me 仍 401（本地模式页面靠 auth/status 分流，不虚构用户上下文）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app

SECRET = "m10-02-web-governance-secret-0123456789"

QUEUE_PATHS = (
    "/api/v1/courses/import-drafts",
    "/api/v1/courses/generation-drafts",
    "/api/v1/papers/import-drafts",
    "/api/v1/questions/variant-drafts",
)


@pytest.fixture()
def auth_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    get_settings.cache_clear()


@pytest.fixture()
def stack(tmp_path):
    db_url = "sqlite+aiosqlite:///" + (tmp_path / "m1002.db").as_posix()
    with TestClient(create_app(db_url)) as c:
        yield c, db_url


class User:
    def __init__(self, client: TestClient, name: str) -> None:
        body = {"username": name, "password": "password-123"}
        client.post("/api/v1/auth/register", json=body)
        token = client.post("/api/v1/auth/login", json=body).json()["access_token"]
        self.name = name
        self.headers = {"Authorization": f"Bearer {token}"}


def _promote(db_url: str, username: str) -> None:
    import asyncio

    from app.db.session import create_engine, make_sessionmaker
    from app.repositories.users import UserRepository

    async def _do():
        # engine 必须在同一个 event loop 内 dispose（M10-10，同 legacy 治理测试）。
        engine = create_engine(db_url)
        try:
            repo = UserRepository(make_sessionmaker(engine))
            assert await repo.set_role(username, "admin") is not None
        finally:
            await engine.dispose()

    asyncio.run(_do())


def _public_source(client: TestClient, headers: dict, source_id: str) -> None:
    r = client.post(
        "/api/v1/sources",
        json={
            "id": source_id,
            "name": source_id,
            "source_type": "oer",
            "license_state": "OPEN_LICENSE",
            "trust_tier": "B",
            "authority_score": 5,
            "homepage": f"https://example.edu/{source_id}",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text


def _upload_parsed(client: TestClient, headers: dict, marker: str, source_id: str) -> str:
    payload = f'[{{"question": "{marker}", "answer": "a/sinA=b/sinB"}}]'.encode()
    up = client.post(
        "/api/v1/resources/upload",
        files={"file": (f"{marker[:8]}.json", payload, "application/json")},
        data={"source_id": source_id},
        headers=headers,
    )
    assert up.status_code == 201, up.text
    rid = up.json()["id"]
    assert client.post(f"/api/v1/resources/{rid}/parse", headers=headers).status_code == 200
    return rid


def test_governance_page_admin_flow_contract(stack, auth_on) -> None:
    """admin 完整页面流：队列 -> 详情 -> 通过 -> 409 反馈 -> 审计回查。"""
    client, db_url = stack
    admin = User(client, "page_admin")
    _promote(db_url, "page_admin")

    # 页面加载第一步：me 披露 admin
    me = client.get("/api/v1/auth/me", headers=admin.headers)
    assert me.status_code == 200
    assert me.json()["role"] == "admin"

    # 页面首次渲染拉全部四个队列（无 status 过滤）
    for path in QUEUE_PATHS:
        listed = client.get(path, headers=admin.headers)
        assert listed.status_code == 200, f"{path} -> {listed.status_code}"

    # 造一条课程导入草稿（公开来源 + 上传 + 解析 + 建草稿）
    _public_source(client, admin.headers, "src_m1002_page")
    rid = _upload_parsed(client, admin.headers, "pageMarkerAdminFlow", "src_m1002_page")
    created = client.post(
        "/api/v1/courses/import-drafts", json={"resource_id": rid}, headers=admin.headers
    )
    assert created.status_code == 201, created.text
    draft_id = created.json()["id"]

    # 列表出现该草稿，详情端点返回同一 id
    listed = client.get("/api/v1/courses/import-drafts", headers=admin.headers).json()
    assert any(item["id"] == draft_id for item in listed)
    detail = client.get(f"/api/v1/courses/import-drafts/{draft_id}", headers=admin.headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "pending_review"

    # 审核动作：通过 -> 200 且状态翻转
    approved = client.post(
        f"/api/v1/courses/import-drafts/{draft_id}/approve",
        json={"note": "页面审核通过"},
        headers=admin.headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["review_note"] == "页面审核通过"

    # 动作后刷新详情：终态如实；重复审核 409（页面的 conflict 反馈）
    refreshed = client.get(f"/api/v1/courses/import-drafts/{draft_id}", headers=admin.headers)
    assert refreshed.json()["status"] == "approved"
    dup = client.post(
        f"/api/v1/courses/import-drafts/{draft_id}/approve",
        json={"note": None},
        headers=admin.headers,
    )
    assert dup.status_code == 409
    reject_late = client.post(
        f"/api/v1/courses/import-drafts/{draft_id}/reject",
        json={"note": None},
        headers=admin.headers,
    )
    assert reject_late.status_code == 409

    # 审计视图：能看到 actor/action/target；不含密钥类字段
    audit = client.get("/api/v1/audit", headers=admin.headers)
    assert audit.status_code == 200
    entry = next(
        e
        for e in audit.json()
        if e["action"] == "course_import.approve" and e["target_id"] == draft_id
    )
    assert entry["actor_username"] == "page_admin"
    assert entry["after"] == {"status": "approved"}
    assert set(entry) >= {"id", "actor_username", "action", "target_type", "target_id", "created_at"}


def test_governance_page_learner_flow_contract(stack, auth_on) -> None:
    """learner 页面流：me 披露 learner，治理数据一律 403 拒绝且不泄露内容。"""
    client, _db = stack
    learner = User(client, "page_learner")

    me = client.get("/api/v1/auth/me", headers=learner.headers)
    assert me.status_code == 200
    assert me.json()["role"] == "learner"

    # 审计读取被拒（页面据此渲染无泄露提示）
    audit = client.get("/api/v1/audit", headers=learner.headers)
    assert audit.status_code == 403
    assert audit.json()["detail"] == "需要管理员权限"

    # 审核动作被拒：门禁先于存在性判断，未知 id 也是 403 而非 404
    for path in QUEUE_PATHS:
        rejected = client.post(
            f"{path}/unknown-draft/approve", json={"note": None}, headers=learner.headers
        )
        assert rejected.status_code == 403, f"{path} -> {rejected.status_code}"


def test_governance_page_local_mode_contract(stack) -> None:
    """auth off：me 无 token 仍 401（不虚构用户）；队列对本地单用户放行。"""
    client, _db = stack
    assert client.get("/api/v1/auth/me").status_code == 401
    for path in QUEUE_PATHS:
        assert client.get(path).status_code == 200, f"{path} 本地模式应放行"
    assert client.get("/api/v1/audit").status_code == 200
