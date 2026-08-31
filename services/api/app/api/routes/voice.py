"""M4-01/M4-02 Voice API：房间 token 签发 + ASR/TTS adapter 端点。

- POST /token：M4-01 房间 JWT（过期和权限边界可测试）。
- GET  /providers：M4-02 provider 配置视图（VOICE_MODE 三模式切换结果，无 DB 依赖）。
- POST /transcribe：语音→文本；transcript 恒落盘，原始音频按
  PRIVACY_STORE_AUDIO 策略落对象存储或显式丢弃（runbook §3）。
- POST /synthesize：文本→WAV 音频（tone 本地合成或云端 provider）。
"""
from __future__ import annotations

import hashlib
import re

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile
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
from app.voice.providers import (
    LOCAL_ASR,
    LOCAL_TTS,
    CloudOpenAiAsrProvider,
    CloudOpenAiTtsProvider,
    FakeAsrProvider,
    ProviderUnavailable,
    ToneTtsProvider,
)
from app.voice.routing import (
    VOICE_MODES,
    resolve_asr,
    resolve_tts,
)

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])

_ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")

MAX_AUDIO_BYTES = 20 * 1024 * 1024  # 20MB：单次转写输入上限


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


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ProviderView(BaseModel):
    """单个语音环节的 provider 解析结果（不虚报：fallback=True 表示云端未配置已降级本地）。"""

    requested: str | None
    provider: str
    fallback: bool


class ProvidersOut(BaseModel):
    voice_mode: str
    asr: ProviderView
    tts: ProviderView
    privacy_store_audio: bool
    privacy_send_context_to_cloud: bool


class TranscriptionOut(BaseModel):
    id: int
    text: str
    confidence: float
    provider: str
    latency_ms: int
    audio_bytes: int
    audio_stored: bool
    audio_object_key: str | None


class TranscriptView(BaseModel):
    id: int
    provider: str
    text: str
    confidence: float
    latency_ms: int
    audio_bytes: int
    audio_stored: bool
    audio_object_key: str | None
    exam_id: str | None
    question_id: str | None
    created_at: str


class TranscriptsOut(BaseModel):
    item_count: int
    items: list[TranscriptView]


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


def _voice_mode(settings) -> str:
    mode = settings.voice_mode
    if mode not in VOICE_MODES:
        raise HTTPException(status_code=422, detail=f"非法 VOICE_MODE: {mode}")
    return mode


@router.get("/providers", response_model=ProvidersOut)
async def list_providers() -> ProvidersOut:
    """provider 配置视图：当前 VOICE_MODE 下 ASR/TTS 的实际选择。"""
    settings = get_settings()
    mode = _voice_mode(settings)
    asr_ready = bool(settings.asr_cloud_endpoint and settings.asr_cloud_api_key)
    tts_ready = bool(settings.tts_cloud_endpoint and settings.tts_cloud_api_key)
    asr = resolve_asr(mode, settings.asr_provider, asr_ready)
    tts = resolve_tts(mode, settings.tts_provider, tts_ready)
    return ProvidersOut(
        voice_mode=mode,
        asr=ProviderView(requested=settings.asr_provider, provider=asr.provider, fallback=asr.fallback),
        tts=ProviderView(requested=settings.tts_provider, provider=tts.provider, fallback=tts.fallback),
        privacy_store_audio=settings.privacy_store_audio,
        privacy_send_context_to_cloud=settings.privacy_send_context_to_cloud,
    )


def _build_asr(settings, choice_provider: str):
    if choice_provider == LOCAL_ASR:
        return FakeAsrProvider()
    if choice_provider == "cloud-openai":
        return CloudOpenAiAsrProvider(
            settings.asr_cloud_endpoint or "",
            settings.asr_cloud_api_key or "",
            settings.asr_cloud_model,
        )
    raise HTTPException(status_code=422, detail=f"未知 ASR provider: {choice_provider}")


def _build_tts(settings, choice_provider: str):
    if choice_provider == LOCAL_TTS:
        return ToneTtsProvider()
    if choice_provider == "cloud-openai-tts":
        return CloudOpenAiTtsProvider(
            settings.tts_cloud_endpoint or "",
            settings.tts_cloud_api_key or "",
            settings.tts_cloud_model,
        )
    raise HTTPException(status_code=422, detail=f"未知 TTS provider: {choice_provider}")


@router.post("/transcribe", response_model=TranscriptionOut)
async def transcribe(request: Request, audio: UploadFile) -> TranscriptionOut:
    """语音→文本：transcript 恒落盘；原始音频按 PRIVACY_STORE_AUDIO 策略处理。"""
    settings = get_settings()
    mode = _voice_mode(settings)
    repo = request.app.state.voice_transcripts
    if repo is None:
        raise HTTPException(status_code=503, detail="Transcription requires a database")
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=422, detail="音频内容为空")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="音频超过 20MB 上限")

    choice = resolve_asr(mode, settings.asr_provider, bool(settings.asr_cloud_endpoint and settings.asr_cloud_api_key))
    provider = _build_asr(settings, choice.provider)
    try:
        result = await provider.transcribe(data, content_type=audio.content_type or "audio/wav")
    except ProviderUnavailable as cause:
        raise HTTPException(status_code=502, detail=str(cause)) from cause

    audio_object_key: str | None = None
    audio_stored = False
    if settings.privacy_store_audio:
        digest = hashlib.sha256(data).hexdigest()
        audio_object_key = f"voice/{digest[:2]}/{digest}"
        request.app.state.objects.put(audio_object_key, data, audio.content_type or "audio/wav")
        audio_stored = True
    view = await repo.save(
        provider=result.provider,
        text=result.text,
        confidence=result.confidence,
        latency_ms=result.latency_ms,
        audio_bytes=len(data),
        audio_object_key=audio_object_key,
        audio_stored=audio_stored,
    )
    return TranscriptionOut(
        id=view["id"],
        text=view["text"],
        confidence=view["confidence"],
        provider=view["provider"],
        latency_ms=view["latency_ms"],
        audio_bytes=view["audio_bytes"],
        audio_stored=view["audio_stored"],
        audio_object_key=view["audio_object_key"],
    )


@router.get("/transcripts", response_model=TranscriptsOut)
async def list_transcripts(request: Request, limit: int = 50) -> TranscriptsOut:
    """最近转写记录（transcript 恒存的回查面；不含音频内容本身）。"""
    repo = request.app.state.voice_transcripts
    if repo is None:
        raise HTTPException(status_code=503, detail="Transcripts require a database")
    limit = max(1, min(limit, 200))
    rows = await repo.list_recent(limit=limit)
    return TranscriptsOut(
        item_count=len(rows),
        items=[TranscriptView(**row) for row in rows],
    )


@router.post("/synthesize")
async def synthesize(payload: SynthesizeRequest) -> Response:
    """文本→WAV。合成不落库（派生音频无留存需求），provider 透出响应头。"""
    settings = get_settings()
    mode = _voice_mode(settings)
    choice = resolve_tts(mode, settings.tts_provider, bool(settings.tts_cloud_endpoint and settings.tts_cloud_api_key))
    provider = _build_tts(settings, choice.provider)
    try:
        result = await provider.synthesize(payload.text)
    except ProviderUnavailable as cause:
        raise HTTPException(status_code=502, detail=str(cause)) from cause
    return Response(
        content=result.audio,
        media_type="audio/wav",
        headers={
            "X-Voice-Provider": result.provider,
            "X-Voice-Fallback": "1" if choice.fallback else "0",
        },
    )
