"""M4-01 LiveKit 房间 token：签发与验证（过期和权限边界可测试）。

- 角色 grants 显式固定（student = join + 发布/订阅/数据通道），管理类权限
  （room_admin/room_create/room_record/agent/recorder/hidden）一律不授予；
- TTL 显式传入 → JWT exp 可精确断言（验收「token 过期可测试」）；
- 验证用 livekit-api TokenVerifier：过期抛 jwt.ExpiredSignatureError、
  签名不符抛 jwt.InvalidSignatureError（验收「权限边界可测试」的服务端等价）。
- 签发是纯本地 JWT 操作，不依赖 LiveKit server 运行。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import jwt as pyjwt
from livekit import api as lk_api

ROLE_STUDENT = "student"
VOICE_ROLES = (ROLE_STUDENT,)
ROOM_PATTERN_MAX = 64
MIN_TTL_SECONDS = 30
MAX_TTL_SECONDS = 86400
DEFAULT_TTL_SECONDS = 3600


class VoiceTokenError(ValueError):
    """token 非法（过期/签名不符/格式错误）的统一领域异常。"""


@dataclass(frozen=True, slots=True)
class VoiceToken:
    token: str
    room: str
    identity: str
    expires_at: datetime  # aware UTC


def build_voice_token(
    room: str,
    identity: str,
    *,
    api_key: str,
    api_secret: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    role: str = ROLE_STUDENT,
) -> VoiceToken:
    """签发学生角色的房间 token（本地 JWT，不依赖 server）。"""
    if role not in VOICE_ROLES:
        raise VoiceTokenError(f"未知角色: {role}")
    if not identity.strip():
        raise VoiceTokenError("identity 不能为空")
    issued_at = datetime.now(UTC)
    token = (
        lk_api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_grants(_student_grants(room))
        .with_ttl(timedelta(seconds=ttl_seconds))
    ).to_jwt()
    return VoiceToken(
        token=token,
        room=room,
        identity=identity,
        expires_at=issued_at + timedelta(seconds=ttl_seconds),
    )


def verify_voice_token(token: str, *, api_key: str, api_secret: str) -> lk_api.access_token.Claims:
    """验证 token（签名 + 过期）；失败统一转 VoiceTokenError。"""
    try:
        return lk_api.TokenVerifier(api_key, api_secret).verify(token)
    except pyjwt.ExpiredSignatureError as cause:
        raise VoiceTokenError("token 已过期") from cause
    except pyjwt.InvalidSignatureError as cause:
        raise VoiceTokenError("token 签名不符") from cause
    except pyjwt.InvalidTokenError as cause:
        raise VoiceTokenError(f"token 非法: {cause}") from cause


def _student_grants(room: str) -> lk_api.VideoGrants:
    """学生角色 grants：加入 + 音视频发布/订阅 + 数据通道；管理权限一律为空。"""
    return lk_api.VideoGrants(
        room_join=True,
        room=room,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )


def decode_expiry(token: str) -> tuple[datetime | None, datetime | None]:
    """读取 JWT 的 exp/nbf（不验签，仅供测试与展示断言过期窗口）。"""
    claims = pyjwt.decode(token, options={"verify_signature": False})
    exp = claims.get("exp")
    nbf = claims.get("nbf")
    return (
        datetime.fromtimestamp(exp, tz=timezone.utc) if exp is not None else None,
        datetime.fromtimestamp(nbf, tz=timezone.utc) if nbf is not None else None,
    )


def new_identity(prefix: str = "student") -> str:
    """未指定身份时生成稳定格式的临时身份。"""
    return f"{prefix}-{uuid4().hex[:8]}"
