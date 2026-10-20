"""M15-018 多厂商 LLM 密钥库契约：多 profile/激活切换/上限/隔离/判分 resolver。"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api.routes.me_ai_keys import resolve_user_llm_config
from app.core.config import get_settings
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
SECRET = "x" * 32


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    for var in ("LLM_API_KEY", "ASR_CLOUD_API_KEY", "TTS_CLOUD_API_KEY"):
        monkeypatch.delenv(var, raising=False)
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


def _uid(client: TestClient, headers: dict) -> str:
    return client.get("/api/v1/auth/me", headers=headers).json()["id"]


def _create(client, headers, label, key, endpoint=None, model=None, activate=False):
    return client.post(
        "/api/v1/me/ai-keys/llm/profiles",
        json={"label": label, "api_key": key, "endpoint": endpoint, "model": model, "activate": activate},
        headers=headers,
    )


def test_multi_profile_create_and_activate_switch(client: TestClient):
    alice = _register_login(client, "multi_alice")
    # 首条自动激活
    r1 = _create(client, alice, "DeepSeek", "sk-ds-12345678", "https://api.deepseek.com/v1", "deepseek-chat")
    assert r1.status_code == 201
    assert r1.json()["active"] is True
    # 第二条不抢激活
    r2 = _create(client, alice, "OpenAI", "sk-oa-87654321", "https://api.openai.com/v1", "gpt-4o-mini")
    assert r2.status_code == 201
    assert r2.json()["active"] is False
    # 列表两条，DeepSeek 激活
    listing = client.get("/api/v1/me/ai-keys", headers=alice).json()
    assert len(listing["profiles"]) == 2
    assert [p["label"] for p in listing["profiles"]] == ["DeepSeek", "OpenAI"]
    assert next(p for p in listing["profiles"] if p["label"] == "DeepSeek")["active"] is True
    # 切换到 OpenAI
    act = client.post(f"/api/v1/me/ai-keys/llm/profiles/{r2.json()['id']}/activate", headers=alice)
    assert act.status_code == 200 and act.json()["active"] is True
    listing = client.get("/api/v1/me/ai-keys", headers=alice).json()
    assert next(p for p in listing["profiles"] if p["label"] == "OpenAI")["active"] is True
    assert next(p for p in listing["profiles"] if p["label"] == "DeepSeek")["active"] is False
    # 恰一条激活
    assert sum(1 for p in listing["profiles"] if p["active"]) == 1


def test_resolver_follows_active_profile(client: TestClient):
    alice = _register_login(client, "multi_res")
    uid = _uid(client, alice)
    _create(client, alice, "DeepSeek", "sk-ds-11112222", "https://api.deepseek.com/v1", "deepseek-chat")
    r2 = _create(client, alice, "Moonshot", "sk-moon-33334444", "https://api.moonshot.cn/v1", "kimi-latest")
    assert asyncio.run(resolve_user_llm_config(client.app, uid))["api_key"] == "sk-ds-11112222"
    client.post(f"/api/v1/me/ai-keys/llm/profiles/{r2.json()['id']}/activate", headers=alice)
    config = asyncio.run(resolve_user_llm_config(client.app, uid))
    assert config["api_key"] == "sk-moon-33334444"
    assert config["model"] == "kimi-latest"


def test_profile_cap_and_isolation(client: TestClient):
    alice = _register_login(client, "multi_cap")
    for i in range(8):
        assert _create(client, alice, f"厂商{i}", f"sk-cap-1234567{i}").status_code == 201
    assert _create(client, alice, "超出", "sk-cap-99999999").status_code == 409
    # Bob 看不到 Alice 的任何配置
    bob = _register_login(client, "multi_bob")
    assert client.get("/api/v1/me/ai-keys", headers=bob).json()["profiles"] == []
    # Bob 切换/删除 Alice 的配置 → 404
    listing = client.get("/api/v1/me/ai-keys", headers=alice).json()
    target = listing["profiles"][0]["id"]
    assert client.post(f"/api/v1/me/ai-keys/llm/profiles/{target}/activate", headers=bob).status_code == 404
    assert client.delete(f"/api/v1/me/ai-keys/llm/profiles/{target}", headers=bob).status_code == 404


def test_delete_active_falls_to_earliest(client: TestClient):
    alice = _register_login(client, "multi_del")
    r1 = _create(client, alice, "A", "sk-del-11111111")
    _create(client, alice, "B", "sk-del-22222222")
    # 删除激活的 A → B（最早的剩余条）顶上
    assert client.delete(f"/api/v1/me/ai-keys/llm/profiles/{r1.json()['id']}", headers=alice).status_code == 200
    listing = client.get("/api/v1/me/ai-keys", headers=alice).json()
    assert len(listing["profiles"]) == 1
    assert listing["profiles"][0]["label"] == "B" and listing["profiles"][0]["active"] is True


def test_profile_endpoint_ssrf_guard(client: TestClient):
    alice = _register_login(client, "multi_ssrf")
    resp = _create(client, alice, "内网", "sk-ssrf-12345678", "http://169.254.169.254/v1")
    assert resp.status_code == 422
    resp = _create(client, alice, "合法", "sk-ssrf-12345678", "https://api.deepseek.com/v1")
    assert resp.status_code == 201


def test_legacy_put_wraps_active_profile(client: TestClient):
    """旧单槽 PUT 兼容：覆盖激活条（无则新建「默认」）。"""
    alice = _register_login(client, "multi_legacy")
    resp = client.put(
        "/api/v1/me/ai-keys/llm",
        json={"api_key": "sk-legacy-123456", "endpoint": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
        headers=alice,
    )
    assert resp.status_code == 200
    listing = client.get("/api/v1/me/ai-keys", headers=alice).json()
    assert len(listing["profiles"]) == 1
    assert listing["profiles"][0]["label"] == "默认" and listing["profiles"][0]["active"] is True
