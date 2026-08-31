"""M4-01 Voice 房间 token API：为浏览器加入 LiveKit 房间签发 JWT。

POST /api/v1/voice/token：503（LiveKit 未配置）/ 422（非法 room/ttl）；
签发不依赖 LiveKit server 运行（纯本地 JWT）；TTL 与 grants 显式，
过期和权限边界可测试（domain 层 verify_voice_token）。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.domain.voice_tokens import (
    DEFAULT_TTL_SECONDS,
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    ROLE_STUDENT,
    VOICE_ROLES,
    VoiceTokenError,
    build_voice_token,
    new_identity,
)

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])

_ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")


class VoiceTokenRequest(BaseModel):
    room: str
    identity: str | None = None
    ttl_seconds: int = Field(default=DEFAULT_TTL_SECONDS, ge=MIN_TTL_SECONDS, le=MAX_TTL_SECONDS)
    role: str = ROLE_STUDENT


class VoiceTokenOut(BaseModel):
    token: str
    room: str
    identity: str
    role: str
    expires_at: str
    ws_url: str


@router.post("/token", response_model=VoiceTokenOut)
async def issue_voice_token(payload: VoiceTokenRequest, request: Request) -> VoiceTokenOut:
    settings = get_settings()
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise HTTPException(status_code=503, detail="Voice requires LiveKit configuration")
    if not _ROOM_RE.fullmatch(payload.room):
        raise HTTPException(status_code=422, detail="room 需为 3-64 位字母数字/-/_")
    if payload.role not in VOICE_ROLES:
        raise HTTPException(status_code=422, detail=f"未知角色: {payload.role}")
    identity = payload.identity or new_identity()
    try:
        token = build_voice_token(
            payload.room,
            identity,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
            ttl_seconds=payload.ttl_seconds,
            role=payload.role,
        )
    except VoiceTokenError as cause:
        raise HTTPException(status_code=422, detail=str(cause)) from cause
    return VoiceTokenOut(
        token=token.token,
        room=token.room,
        identity=token.identity,
        role=payload.role,
        expires_at=token.expires_at.isoformat(),
        ws_url=settings.livekit_url or "ws://127.0.0.1:7880",
    )
