"""M4-01 LiveKit 房间 token：过期和权限边界可测试。

签发为纯本地 JWT（不依赖 server）；验证语义 = livekit TokenVerifier
（过期 → ExpiredSignatureError、签名不符 → InvalidSignatureError）。
"""
from __future__ import annotations

from datetime import timedelta

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domain.voice_tokens import (
    ROLE_STUDENT,
    VoiceTokenError,
    build_voice_token,
    decode_expiry,
    verify_voice_token,
)
from app.main import create_app

API_KEY = "devkey"
API_SECRET = "test-secret-32-bytes-long-enough!"
ROOM = "exam-room"


@pytest.fixture
def livekit_env(monkeypatch):
    """注入 LiveKit dev 配置并清 settings 缓存；teardown 恢复环境后再清一次。"""
    monkeypatch.setenv("LIVEKIT_API_KEY", API_KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", API_SECRET)
    monkeypatch.setenv("LIVEKIT_URL", "ws://127.0.0.1:7880")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _build(ttl_seconds: int = 3600, secret: str = API_SECRET, identity: str = "student-1"):
    return build_voice_token(
        ROOM,
        identity,
        api_key=API_KEY,
        api_secret=secret,
        ttl_seconds=ttl_seconds,
    )


def test_token_expiry_window_matches_ttl() -> None:
    """验收「token 过期可测试」：JWT exp - nbf 精确等于 TTL。"""
    token = _build(ttl_seconds=600)
    exp, nbf = decode_expiry(token.token)
    assert exp is not None and nbf is not None
    assert exp - nbf == timedelta(seconds=600)
    assert token.expires_at > nbf


def test_student_grants_boundary_is_explicit() -> None:
    """验收「权限边界可测试」：student 恰好四项权限，管理类全部为空。"""
    token = _build()
    claims = verify_voice_token(token.token, api_key=API_KEY, api_secret=API_SECRET)
    video = claims.video
    assert claims.identity == "student-1"
    assert video.room_join is True and video.room == ROOM
    assert video.can_publish is True
    assert video.can_subscribe is True
    assert video.can_publish_data is True
    # 管理类权限一律不授予
    for forbidden in (
        video.room_admin,
        video.room_create,
        video.room_record,
        video.hidden,
        video.recorder,
        video.agent,
        video.ingress_admin,
    ):
        assert not forbidden


def test_expired_token_is_rejected() -> None:
    token = _build(ttl_seconds=-3600)  # 远超 verify leeway（60s）
    with pytest.raises(VoiceTokenError, match="已过期"):
        verify_voice_token(token.token, api_key=API_KEY, api_secret=API_SECRET)


def test_token_signed_with_wrong_secret_is_rejected() -> None:
    forged = _build(secret="another-secret-32-bytes-long-enough!")
    with pytest.raises(VoiceTokenError, match="签名不符"):
        verify_voice_token(forged.token, api_key=API_KEY, api_secret=API_SECRET)


def test_garbage_token_is_rejected() -> None:
    with pytest.raises(VoiceTokenError):
        verify_voice_token("not-a-jwt", api_key=API_KEY, api_secret=API_SECRET)


def test_unknown_role_rejected_at_issue_time() -> None:
    with pytest.raises(VoiceTokenError, match="未知角色"):
        build_voice_token(
            ROOM,
            "student-1",
            api_key=API_KEY,
            api_secret=API_SECRET,
            role="admin",  # type: ignore[arg-type]
        )


def test_issue_token_endpoint_roundtrip(livekit_env) -> None:
    """端点签发 → domain 验证往返；identity 缺省自动生成。"""
    with TestClient(create_app(None)) as client:
        body = client.post(
            "/api/v1/voice/token",
            json={"room": ROOM, "ttl_seconds": 1800},
        )
        assert body.status_code == 200
        payload = body.json()
        assert payload["room"] == ROOM
        assert payload["role"] == ROLE_STUDENT
        assert payload["identity"].startswith("student-")
        assert payload["ws_url"] == "ws://127.0.0.1:7880"
        claims = verify_voice_token(
            payload["token"], api_key=API_KEY, api_secret=API_SECRET
        )
        assert claims.identity == payload["identity"]
        exp, _ = decode_expiry(payload["token"])
        assert exp is not None


def test_issue_token_endpoint_validation(livekit_env) -> None:
    with TestClient(create_app(None)) as client:
        assert client.post("/api/v1/voice/token", json={"room": "ab"}).status_code == 422
        assert client.post("/api/v1/voice/token", json={"room": "bad room!"}).status_code == 422
        assert (
            client.post(
                "/api/v1/voice/token", json={"room": ROOM, "ttl_seconds": 5}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/voice/token", json={"room": ROOM, "ttl_seconds": 999999}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/voice/token", json={"room": ROOM, "role": "admin"}
            ).status_code
            == 422
        )


def test_issue_token_without_configuration_is_503(monkeypatch) -> None:
    """LiveKit 未配置 → 503（不是崩溃）。"""
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app(None)) as client:
        response = client.post("/api/v1/voice/token", json={"room": ROOM})
        assert response.status_code == 503
    get_settings.cache_clear()


def test_exp_field_required_for_verification() -> None:
    """无 exp 的手工 token 被拒（验证器强制 require exp——过期语义不可绕过）。"""
    forged = pyjwt.encode(
        {"sub": "ghost", "name": "", "video": {"roomJoin": True, "room": ROOM}},
        API_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(VoiceTokenError):
        verify_voice_token(forged, api_key=API_KEY, api_secret=API_SECRET)
