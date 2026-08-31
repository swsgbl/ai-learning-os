"""M4-02 ASR/TTS adapter：在线、本地、混合 provider 可配置切换；语音原文按隐私策略落盘。

- 路由：VOICE_MODE 三模式 + 显式 provider 覆盖 + 云端未配置降级（fallback 透出）；
- 转写落盘：transcript 恒存 DB、可回查；原始音频仅 PRIVACY_STORE_AUDIO=true 时写对象存储；
- 云端 provider 用 httpx.MockTransport 伪造端点（无真实 key 依赖）。
"""
from __future__ import annotations

import asyncio
import io
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.voice.providers import (
    LOCAL_ASR,
    LOCAL_TTS,
    CloudOpenAiAsrProvider,
    CloudOpenAiTtsProvider,
    FakeAsrProvider,
    ProviderUnavailable,
    ToneTtsProvider,
    _sine_wav,
)
from app.voice.routing import resolve_asr, resolve_tts

SQLITE_URL = "sqlite+aiosqlite:///:memory:"

_CLOUD_ENV_KEYS = (
    "ASR_CLOUD_ENDPOINT",
    "ASR_CLOUD_API_KEY",
    "TTS_CLOUD_ENDPOINT",
    "TTS_CLOUD_API_KEY",
)


def _configure_voice(monkeypatch, *, mode: str, asr_cloud: bool = False, tts_cloud: bool = False,
                     store_audio: bool = False) -> None:
    monkeypatch.setenv("VOICE_MODE", mode)
    monkeypatch.setenv("PRIVACY_STORE_AUDIO", "true" if store_audio else "false")
    for key in _CLOUD_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    if asr_cloud:
        monkeypatch.setenv("ASR_CLOUD_ENDPOINT", "https://cloud.example.invalid/v1")
        monkeypatch.setenv("ASR_CLOUD_API_KEY", "test-key")
    if tts_cloud:
        monkeypatch.setenv("TTS_CLOUD_ENDPOINT", "https://cloud.example.invalid/v1")
        monkeypatch.setenv("TTS_CLOUD_API_KEY", "test-key")
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------- 路由：三模式切换 ----------


def test_local_mode_selects_local_providers() -> None:
    asr = resolve_asr("local", None, cloud_ready=True)  # 云端已配置也不该用（local 模式）
    tts = resolve_tts("local", None, cloud_ready=True)
    assert (asr.provider, asr.fallback) == (LOCAL_ASR, False)
    assert (tts.provider, tts.fallback) == (LOCAL_TTS, False)


def test_cloud_mode_selects_cloud_and_falls_back_when_unconfigured() -> None:
    ready = resolve_asr("cloud", None, cloud_ready=True)
    assert (ready.provider, ready.fallback) == ("cloud-openai", False)
    down = resolve_asr("cloud", None, cloud_ready=False)
    assert (down.provider, down.fallback) == (LOCAL_ASR, True)  # 降级并透出


def test_hybrid_mode_keeps_asr_local_but_tts_cloud() -> None:
    """混合语义（runbook）：ASR 本地（语音不出本机），TTS 可云端（仅文本出站）。"""
    asr = resolve_asr("hybrid", None, cloud_ready=True)
    tts = resolve_tts("hybrid", None, cloud_ready=True)
    assert asr.provider == LOCAL_ASR
    assert tts.provider == "cloud-openai-tts"
    tts_unconfigured = resolve_tts("hybrid", None, cloud_ready=False)
    assert (tts_unconfigured.provider, tts_unconfigured.fallback) == (LOCAL_TTS, True)


def test_explicit_provider_overrides_mode() -> None:
    """显式 ASR_PROVIDER/TTS_PROVIDER 覆盖 VOICE_MODE 默认。"""
    asr = resolve_asr("local", "cloud-openai", cloud_ready=True)
    assert asr.provider == "cloud-openai"
    tts = resolve_tts("cloud", "tone", cloud_ready=False)
    assert (tts.provider, tts.fallback) == (LOCAL_TTS, False)  # 显式指定本地不算降级


def test_unknown_mode_rejected() -> None:
    with pytest.raises(ValueError, match="voice_mode"):
        resolve_asr("edge", None, cloud_ready=False)


# ---------- 本地实现：确定性 ----------


def test_fake_asr_is_deterministic_and_honest() -> None:
    async def body() -> None:
        provider = FakeAsrProvider()
        first = await provider.transcribe(b"abc")
        second = await provider.transcribe(b"abc")
        assert first == second
        assert first.text.startswith("[fake]")  # 显式替身标记，不冒充真实 ASR
        assert first.provider == LOCAL_ASR

    asyncio.run(body())


def test_tone_tts_produces_playable_wav_scaled_by_text() -> None:
    async def body() -> None:
        provider = ToneTtsProvider()
        short = await provider.synthesize("好")
        long = await provider.synthesize("这一句明显长很多所以音频也应该更长")
        for result in (short, long):
            with wave.open(io.BytesIO(result.audio), "rb") as reader:
                assert reader.getframerate() == 8000
                assert reader.getnchannels() == 1
            assert result.audio_format == "wav"
        assert len(long.audio) > len(short.audio)  # 时长∝文本长度

    asyncio.run(body())


def test_sine_wav_is_deterministic() -> None:
    assert _sine_wav(0.4) == _sine_wav(0.4)


# ---------- 云端 provider：MockTransport ----------


def test_cloud_asr_parses_openai_compatible_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/transcriptions"
        assert request.headers["Authorization"] == "Bearer test-key"
        assert b"audio-bytes" in request.content
        return httpx.Response(200, json={"text": "识别文本", "confidence": 0.9})

    async def body() -> None:
        provider = CloudOpenAiAsrProvider(
            "https://cloud.example.invalid/v1",
            "test-key",
            "whisper-1",
            transport=httpx.MockTransport(handler),
        )
        result = await provider.transcribe(b"audio-bytes")
        assert result.text == "识别文本"
        assert result.provider == "cloud-openai"

    asyncio.run(body())


def test_cloud_tts_failure_raises_provider_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async def body() -> None:
        provider = CloudOpenAiTtsProvider(
            "https://cloud.example.invalid/v1",
            "test-key",
            "tts-1",
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ProviderUnavailable, match="cloud TTS"):
            await provider.synthesize("文本")

    asyncio.run(body())


# ---------- API：配置视图 + 转写落盘 + 合成 ----------


def test_providers_endpoint_reflects_modes(monkeypatch) -> None:
    """三模式切换：local→双本地；cloud→双云端；hybrid→ASR 本地+TTS 云端降级透出。"""
    _configure_voice(monkeypatch, mode="local")
    with TestClient(create_app(None)) as client:
        body = client.get("/api/v1/voice/providers").json()
        assert body["voice_mode"] == "local"
        assert (body["asr"]["provider"], body["asr"]["fallback"]) == ("fake", False)
        assert (body["tts"]["provider"], body["tts"]["fallback"]) == ("tone", False)
        assert body["privacy_store_audio"] is False

        _configure_voice(monkeypatch, mode="cloud", asr_cloud=True, tts_cloud=True)
        body = client.get("/api/v1/voice/providers").json()
        assert body["voice_mode"] == "cloud"
        assert body["asr"]["provider"] == "cloud-openai"
        assert body["tts"]["provider"] == "cloud-openai-tts"

        _configure_voice(monkeypatch, mode="hybrid", asr_cloud=True)  # TTS 云端未配置
        body = client.get("/api/v1/voice/providers").json()
        assert body["asr"]["provider"] == "fake"
        assert (body["tts"]["provider"], body["tts"]["fallback"]) == ("tone", True)


def test_transcribe_persists_transcript_and_drops_audio_by_default(monkeypatch) -> None:
    """默认隐私策略（PRIVACY_STORE_AUDIO=false）：transcript 恒存可回查、原始音频不落盘。"""
    _configure_voice(monkeypatch, mode="local")
    with TestClient(create_app(SQLITE_URL)) as client:
        response = client.post(
            "/api/v1/voice/transcribe",
            files={"audio": ("clip.wav", _sine_wav(0.4), "audio/wav")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["text"].startswith("[fake]")
        assert body["provider"] == "fake"
        assert body["audio_bytes"] > 0
        assert body["audio_stored"] is False
        assert body["audio_object_key"] is None

        recent = client.get("/api/v1/voice/transcripts").json()
        assert recent["item_count"] >= 1
        assert any(item["id"] == body["id"] for item in recent["items"])


def test_transcribe_stores_audio_when_privacy_allows(monkeypatch) -> None:
    """PRIVACY_STORE_AUDIO=true：原始音频写对象存储并留 key。"""
    _configure_voice(monkeypatch, mode="local", store_audio=True)
    with TestClient(create_app(SQLITE_URL)) as client:
        audio = _sine_wav(0.4)
        body = client.post(
            "/api/v1/voice/transcribe",
            files={"audio": ("clip.wav", audio, "audio/wav")},
        ).json()
        assert body["audio_stored"] is True
        assert body["audio_object_key"]
        stored = client.app.state.objects.get(body["audio_object_key"])  # type: ignore[attr-defined]
        assert stored == audio


def test_transcribe_requires_database(monkeypatch) -> None:
    _configure_voice(monkeypatch, mode="local")
    with TestClient(create_app(None)) as client:
        response = client.post(
            "/api/v1/voice/transcribe",
            files={"audio": ("clip.wav", _sine_wav(0.4), "audio/wav")},
        )
        assert response.status_code == 503


def test_transcribe_rejects_empty_audio(monkeypatch) -> None:
    _configure_voice(monkeypatch, mode="local")
    with TestClient(create_app(SQLITE_URL)) as client:
        response = client.post(
            "/api/v1/voice/transcribe",
            files={"audio": ("clip.wav", b"", "audio/wav")},
        )
        assert response.status_code == 422


def test_synthesize_returns_wav_with_provider_header(monkeypatch) -> None:
    _configure_voice(monkeypatch, mode="local")
    with TestClient(create_app(None)) as client:
        response = client.post("/api/v1/voice/synthesize", json={"text": "你好世界"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert response.headers["x-voice-provider"] == "tone"
        assert response.headers["x-voice-fallback"] == "0"
        with wave.open(io.BytesIO(response.content), "rb") as reader:
            assert reader.getnframes() > 0


def test_synthesize_fallback_header_when_cloud_unconfigured(monkeypatch) -> None:
    """hybrid 且 TTS 云端未配置 → 合成走本地并在响应头透出 fallback。"""
    _configure_voice(monkeypatch, mode="hybrid")
    with TestClient(create_app(None)) as client:
        response = client.post("/api/v1/voice/synthesize", json={"text": "混合模式"})
        assert response.headers["x-voice-provider"] == "tone"
        assert response.headers["x-voice-fallback"] == "1"
