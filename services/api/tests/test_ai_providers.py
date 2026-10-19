"""M15-008 AI 密钥治理契约：门禁 / 脱敏 / 槽位白名单 / resolver 优先级 / 加密往返。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.secrets import SecretBox, SecretBoxError, derive_key
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
SECRET = "x" * 32


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    # 三槽位 env 清零（防本机 .env 泄漏进状态断言）
    # pydantic 直读容器内变量名（无 AIOS_ 前缀；前缀是 compose/env 文件层约定）
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


# ---------- SecretBox 纯单元 ----------


def test_secret_box_roundtrip():
    box = SecretBox(SECRET)
    stored = box.encrypt_to_b64("sk-very-secret-123456")
    assert "sk-very-secret" not in stored
    assert box.decrypt_from_b64(stored) == "sk-very-secret-123456"


def test_secret_box_rejects_empty_secret():
    with pytest.raises(SecretBoxError):
        SecretBox("")


def test_secret_box_derive_is_domain_separated():
    # HKDF info 域分离：同 secret 派生稳定，但与裸 sha256 不同
    assert derive_key(SECRET) == derive_key(SECRET)
    assert derive_key(SECRET) != derive_key("y" * 32)


def test_secret_box_corrupt_ciphertext_fails_closed():
    box = SecretBox(SECRET)
    with pytest.raises(SecretBoxError):
        box.decrypt_from_b64("not-base64-$$$")
    with pytest.raises(SecretBoxError):
        box.decrypt_from_b64(box.encrypt_to_b64("k")[:16])  # 截断密文


# ---------- 治理端点契约 ----------


def test_get_requires_admin(client: TestClient):
    learner = _register_login(client, "prov_learner")
    resp = client.get("/api/v1/admin/ai-providers", headers=learner)
    assert resp.status_code == 403


def test_get_slots_shape(client: TestClient):
    _register_login(client, "prov_admin")
    client.portal.call(client.app.state.users.set_role, "prov_admin", "admin")
    admin = _register_login(client, "prov_admin")
    resp = client.get("/api/v1/admin/ai-providers", headers=admin)
    assert resp.status_code == 200
    slots = {s["slot"]: s for s in resp.json()["slots"]}
    assert set(slots) == {"llm", "asr_cloud", "tts_cloud"}
    # env 未配置 → 未配置态；无密钥泄漏字段
    for s in slots.values():
        assert s["configured"] is False
        assert s["source"] is None
        assert s["last4"] is None
        assert s["env_var"].startswith("AIOS_")
        assert s["effect"] in ("hot", "restart")


def test_put_and_delete_flow_never_echoes_secret(client: TestClient):
    _register_login(client, "prov_root")
    client.portal.call(client.app.state.users.set_role, "prov_root", "admin")
    admin = _register_login(client, "prov_root")

    # 写入
    resp = client.put(
        "/api/v1/admin/ai-providers/asr_cloud", json={"api_key": "sk-test-abcd1234"}, headers=admin
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "db"
    assert body["last4"] == "1234"
    assert body["effect"] == "hot"
    assert "sk-test-abcd1234" not in resp.text  # 明文永不回显

    # 状态视图：db 优先
    state = client.get("/api/v1/admin/ai-providers", headers=admin).json()
    asr = next(s for s in state["slots"] if s["slot"] == "asr_cloud")
    assert asr["configured"] is True and asr["source"] == "db" and asr["last4"] == "1234"

    # 删除 → 回退 env（未配置 env → 未配置态）
    resp = client.delete("/api/v1/admin/ai-providers/asr_cloud", headers=admin)
    assert resp.status_code == 200
    assert resp.json()["source"] is None
    # 再删 → 404
    assert client.delete("/api/v1/admin/ai-providers/asr_cloud", headers=admin).status_code == 404


def test_put_slot_whitelist_and_key_validation(client: TestClient):
    _register_login(client, "prov_gate")
    client.portal.call(client.app.state.users.set_role, "prov_gate", "admin")
    admin = _register_login(client, "prov_gate")
    assert (
        client.put("/api/v1/admin/ai-providers/unknown", json={"api_key": "sk-12345678"}, headers=admin).status_code
        == 422
    )
    assert (
        client.put("/api/v1/admin/ai-providers/llm", json={"api_key": "short"}, headers=admin).status_code == 422
    )


def test_put_requires_admin(client: TestClient):
    learner = _register_login(client, "prov_writer")
    resp = client.put(
        "/api/v1/admin/ai-providers/llm", json={"api_key": "sk-1234567890"}, headers=learner
    )
    assert resp.status_code == 403


def test_audit_recorded_on_write(client: TestClient):
    _register_login(client, "prov_audit")
    client.portal.call(client.app.state.users.set_role, "prov_audit", "admin")
    admin = _register_login(client, "prov_audit")
    client.put("/api/v1/admin/ai-providers/llm", json={"api_key": "sk-audit-987654"}, headers=admin)
    entries = client.get("/api/v1/audit", headers=admin).json()
    entry = next(e for e in entries if e["action"] == "ai_provider_key.set")
    assert entry["target_id"] == "llm"
    assert "sk-audit-987654" not in str(entry)  # 审计无明文
    assert entry["after"]["last4"] == "7654"


# ---------- resolver 优先级 ----------


def test_resolver_db_overrides_env_and_falls_back(client: TestClient, monkeypatch):
    from app.api.routes.ai_providers import resolve_provider_key

    _register_login(client, "prov_res")
    client.portal.call(client.app.state.users.set_role, "prov_res", "admin")
    admin = _register_login(client, "prov_res")

    # env 未配置且 DB 未写入 → None
    import asyncio

    assert asyncio.run(resolve_provider_key(client.app, "tts_cloud")) is None

    # DB 写入 → resolver 返回明文
    client.put("/api/v1/admin/ai-providers/tts_cloud", json={"api_key": "sk-res-654321"}, headers=admin)
    assert asyncio.run(resolve_provider_key(client.app, "tts_cloud")) == "sk-res-654321"

    # 删除 → 回退 env（未配置 → None）
    client.delete("/api/v1/admin/ai-providers/tts_cloud", headers=admin)
    assert asyncio.run(resolve_provider_key(client.app, "tts_cloud")) is None
