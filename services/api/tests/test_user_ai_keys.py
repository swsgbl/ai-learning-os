"""M15-014 个人 AI 密钥（BYOK）契约：门禁/归属隔离/不回显/审计/resolver 优先级。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.routes.ai_providers import resolve_provider_key
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


def test_requires_auth(client: TestClient):
    assert client.get("/api/v1/me/ai-keys").status_code == 401


def test_list_put_delete_flow_never_echoes(client: TestClient):
    alice = _register_login(client, "byok_alice")
    # 初始：三槽位全空
    body = client.get("/api/v1/me/ai-keys", headers=alice).json()
    assert {s["slot"] for s in body["keys"]} == {"llm", "asr_cloud", "tts_cloud"}
    assert all(s["configured"] is False for s in body["keys"])

    # 写入
    resp = client.put(
        "/api/v1/me/ai-keys/asr_cloud", json={"api_key": "sk-personal-9876"}, headers=alice
    )
    assert resp.status_code == 200
    assert resp.json()["last4"] == "9876"
    assert resp.json()["effect"] == "hot"
    assert "sk-personal-9876" not in resp.text

    # llm 槽位 effect=saved（判分器 app 级装配，诚实标注）
    resp = client.put("/api/v1/me/ai-keys/llm", json={"api_key": "sk-llm-12345678"}, headers=alice)
    assert resp.json()["effect"] == "saved"

    # 删除 → 空；再删 404
    assert client.delete("/api/v1/me/ai-keys/asr_cloud", headers=alice).status_code == 200
    assert client.delete("/api/v1/me/ai-keys/asr_cloud", headers=alice).status_code == 404
    # llm 仍在
    keys = {k["slot"]: k for k in client.get("/api/v1/me/ai-keys", headers=alice).json()["keys"]}
    assert keys["llm"]["configured"] is True and keys["llm"]["last4"] == "5678"


def test_slot_whitelist_and_key_validation(client: TestClient):
    alice = _register_login(client, "byok_gate")
    assert (
        client.put("/api/v1/me/ai-keys/bogus", json={"api_key": "sk-12345678"}, headers=alice).status_code == 422
    )
    assert (
        client.put("/api/v1/me/ai-keys/llm", json={"api_key": "short"}, headers=alice).status_code == 422
    )


def test_user_isolation_bob_cannot_touch_alice_keys(client: TestClient):
    alice = _register_login(client, "byok_iso_a")
    bob = _register_login(client, "byok_iso_b")
    client.put("/api/v1/me/ai-keys/tts_cloud", json={"api_key": "sk-alice-4321"}, headers=alice)
    # Bob 的列表看不到 Alice 的密钥
    bob_keys = {k["slot"]: k for k in client.get("/api/v1/me/ai-keys", headers=bob).json()["keys"]}
    assert bob_keys["tts_cloud"]["configured"] is False


def test_resolver_personal_overrides_global_and_env(client: TestClient):
    import asyncio

    alice = _register_login(client, "byok_res")
    uid = _uid(client, alice)
    app = client.app

    # env 未配、全局未配、个人未写 → None
    assert asyncio.run(resolve_provider_key(app, "tts_cloud", uid)) is None

    # 个人写入 → resolver 命中个人明文（优先于一切）
    client.put("/api/v1/me/ai-keys/tts_cloud", json={"api_key": "sk-mine-778899"}, headers=alice)
    assert asyncio.run(resolve_provider_key(app, "tts_cloud", uid)) == "sk-mine-778899"

    # 无 user 维度（全局视角）不受个人密钥影响
    assert asyncio.run(resolve_provider_key(app, "tts_cloud")) is None

    # 删除个人 → 回落全局/env（都未配 → None）
    client.delete("/api/v1/me/ai-keys/tts_cloud", headers=alice)
    assert asyncio.run(resolve_provider_key(app, "tts_cloud", uid)) is None


def test_audit_recorded_without_plaintext(client: TestClient):
    _register_login(client, "prov_admin2")
    client.portal.call(client.app.state.users.set_role, "prov_admin2", "admin")
    admin = _register_login(client, "prov_admin2")
    user = _register_login(client, "byok_audit")
    client.put("/api/v1/me/ai-keys/asr_cloud", json={"api_key": "sk-audit-555666"}, headers=user)
    entries = client.get("/api/v1/audit", headers=admin).json()
    entry = next(e for e in entries if e["action"] == "user_ai_key.set")
    assert entry["target_type"] == "user_ai_key"
    assert "sk-audit-555666" not in str(entry)
    assert entry["after"]["last4"] == "5666"
