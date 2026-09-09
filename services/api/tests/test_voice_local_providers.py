"""M14-01 本地真实语音引擎 adapter：local-funasr / local-cosyvoice HTTP provider 测试。

- 路由：local/hybrid 默认真实本地引擎；endpoint 未配置降级替身并透出 fallback；
  cloud 模式不受 local_ready 影响；显式 provider 覆盖与未知 provider 422；
- provider 单元：httpx.MockTransport 伪造本地服务（无网络依赖、无真实 key）——
  成功字段保持、multipart/JSON 请求形状、可选鉴权头（无 key 不发）、
  失败语义 fail-closed 固定脱敏文案（网络/HTTP/非法 JSON/形状）与
  TTS 空/非 RIFF 拒绝（与云端 M10-13 同口径，但文案为「本地 …」不冒充 cloud）；
- API 路由：providers 视图透出真实引擎与降级、transcribe/synthesize 响应头
  X-Voice-Provider/X-Voice-Fallback、ProviderUnavailable → 502 且不回显 endpoint。
"""
from __future__ import annotations

import asyncio
import io
import json
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app
from app.voice.providers import (
    LocalCosyVoiceTtsProvider,
    LocalFunAsrAsrProvider,
    ProviderUnavailable,
    TranscriptionResult,
    _sine_wav,
)
from app.voice.routing import resolve_asr, resolve_tts

SQLITE_URL = "sqlite+aiosqlite:///:memory:"

LOCAL_ASR_ENDPOINT = "http://127.0.0.1:8010/v1"
LOCAL_TTS_ENDPOINT = "http://127.0.0.1:8011/v1"

_VOICE_ENV_KEYS = (
    "VOICE_MODE",
    "ASR_PROVIDER",
    "TTS_PROVIDER",
    "ASR_CLOUD_ENDPOINT",
    "ASR_CLOUD_API_KEY",
    "TTS_CLOUD_ENDPOINT",
    "TTS_CLOUD_API_KEY",
    "ASR_LOCAL_ENDPOINT",
    "ASR_LOCAL_MODEL",
    "ASR_LOCAL_API_KEY",
    "TTS_LOCAL_ENDPOINT",
    "TTS_LOCAL_MODEL",
    "TTS_LOCAL_API_KEY",
)

#: 脱敏断言基准：异常文案不得出现 endpoint/key/鉴权头/httpx 异常文本/响应正文
_SENSITIVE_MARKERS = (
    "127.0.0.1",
    "8010",
    "8011",
    "local-test-key",
    "Authorization",
    "Bearer",
    "ConnectError",
    "ConnectTimeout",
    "upstream",
)


def _configure(monkeypatch, *, mode: str, asr_local: bool = False, tts_local: bool = False,
               asr_provider: str | None = None, tts_provider: str | None = None) -> None:
    for key in _VOICE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("VOICE_MODE", mode)
    if asr_local:
        monkeypatch.setenv("ASR_LOCAL_ENDPOINT", LOCAL_ASR_ENDPOINT)
    if tts_local:
        monkeypatch.setenv("TTS_LOCAL_ENDPOINT", LOCAL_TTS_ENDPOINT)
    if asr_provider:
        monkeypatch.setenv("ASR_PROVIDER", asr_provider)
    if tts_provider:
        monkeypatch.setenv("TTS_PROVIDER", tts_provider)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _assert_sanitized(exc: pytest.ExceptionInfo[ProviderUnavailable], *extra: str) -> None:
    message = str(exc.value)
    for marker in (*_SENSITIVE_MARKERS, *extra):
        assert marker not in message, marker


# ---------- 路由：真实本地引擎选择与降级 ----------


def test_local_mode_defaults_to_real_engines_when_configured() -> None:
    asr = resolve_asr("local", None, cloud_ready=False, local_ready=True)
    tts = resolve_tts("local", None, cloud_ready=False, local_ready=True)
    assert (asr.provider, asr.fallback) == ("local-funasr", False)
    assert (tts.provider, tts.fallback) == ("local-cosyvoice", False)


def test_local_mode_unconfigured_falls_back_to_placeholders() -> None:
    """endpoint 未配置 → 降级替身并透出 fallback（不虚报已接真实引擎）。"""
    asr = resolve_asr("local", None, local_ready=False)
    tts = resolve_tts("local", None, local_ready=False)
    assert (asr.provider, asr.fallback) == ("fake", True)
    assert (tts.provider, tts.fallback) == ("tone", True)


def test_cloud_mode_ignores_local_readiness() -> None:
    """cloud 语义不受本地引擎配置影响（云端优先，未配置云端才降级替身）。"""
    asr = resolve_asr("cloud", None, cloud_ready=True, local_ready=True)
    tts = resolve_tts("cloud", None, cloud_ready=True, local_ready=True)
    assert (asr.provider, asr.fallback) == ("cloud-openai", False)
    assert (tts.provider, tts.fallback) == ("cloud-openai-tts", False)
    down = resolve_asr("cloud", None, cloud_ready=False, local_ready=True)
    assert (down.provider, down.fallback) == ("fake", True)


def test_hybrid_asr_local_real_engine_semantics() -> None:
    """hybrid：ASR 本地真实引擎优先，未配置降级替身；TTS 仍按云端语义。"""
    asr = resolve_asr("hybrid", None, cloud_ready=True, local_ready=True)
    assert (asr.provider, asr.fallback) == ("local-funasr", False)
    asr_down = resolve_asr("hybrid", None, cloud_ready=True, local_ready=False)
    assert (asr_down.provider, asr_down.fallback) == ("fake", True)
    tts = resolve_tts("hybrid", None, cloud_ready=True, local_ready=False)
    assert (tts.provider, tts.fallback) == ("cloud-openai-tts", False)


def test_explicit_real_local_provider_respected_and_falls_back() -> None:
    """显式 local-funasr/local-cosyvoice：已配置直用；未配置降级替身并透出。"""
    asr = resolve_asr("cloud", "local-funasr", cloud_ready=True, local_ready=True)
    assert (asr.provider, asr.fallback) == ("local-funasr", False)
    asr_down = resolve_asr("cloud", "local-funasr", cloud_ready=True, local_ready=False)
    assert (asr_down.provider, asr_down.fallback) == ("fake", True)
    tts_down = resolve_tts("cloud", "local-cosyvoice", cloud_ready=True, local_ready=False)
    assert (tts_down.provider, tts_down.fallback) == ("tone", True)


def test_explicit_placeholder_is_not_a_fallback() -> None:
    """显式 fake/tone 是明确选择，不算降级。"""
    asr = resolve_asr("local", "fake", local_ready=True)
    tts = resolve_tts("local", "tone", local_ready=True)
    assert (asr.provider, asr.fallback) == ("fake", False)
    assert (tts.provider, tts.fallback) == ("tone", False)


def test_settings_local_slot_defaults() -> None:
    """本地槽位默认：endpoint/key 空（降级替身）、模型名与超时有明确默认。"""
    settings = Settings(_env_file=None)
    assert settings.asr_local_endpoint is None
    assert settings.asr_local_api_key is None
    assert settings.asr_local_model == "sensevoice"
    assert settings.tts_local_endpoint is None
    assert settings.tts_local_api_key is None
    assert settings.tts_local_model == "Fun-CosyVoice3-0.5B-2512"
    assert settings.asr_local_timeout_seconds > 0
    assert settings.tts_local_timeout_seconds > 0


# ---------- LocalFunAsrAsrProvider：MockTransport ----------


def test_local_funasr_success_parses_openai_compatible_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/transcriptions"
        assert "Authorization" not in request.headers  # 无 key 不发鉴权头（本地服务默认无鉴权）
        assert b"sensevoice" in request.content  # multipart 携带 model 字段
        assert b"audio-bytes" in request.content
        return httpx.Response(200, json={"text": "本地识别文本", "confidence": 0.88})

    async def body() -> None:
        provider = LocalFunAsrAsrProvider(
            LOCAL_ASR_ENDPOINT, api_key="", model="sensevoice",
            transport=httpx.MockTransport(handler),
        )
        result = await provider.transcribe(b"audio-bytes")
        assert result.text == "本地识别文本"
        assert result.confidence == pytest.approx(0.88)
        assert result.provider == "local-funasr"
        assert result.latency_ms >= 0

    asyncio.run(body())


def test_local_funasr_sends_bearer_when_key_configured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer local-test-key"
        return httpx.Response(200, json={"text": "文本"})

    async def body() -> None:
        provider = LocalFunAsrAsrProvider(
            LOCAL_ASR_ENDPOINT, api_key="local-test-key",
            transport=httpx.MockTransport(handler),
        )
        result = await provider.transcribe(b"audio-bytes")
        assert result.provider == "local-funasr"

    asyncio.run(body())


def test_local_funasr_network_failure_is_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection to http://127.0.0.1:8010/v1/audio/transcriptions refused")

    async def body() -> None:
        provider = LocalFunAsrAsrProvider(
            LOCAL_ASR_ENDPOINT, transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ProviderUnavailable, match="本地 ASR 请求失败") as exc:
            await provider.transcribe(b"audio-bytes")
        _assert_sanitized(exc, "refused")

    asyncio.run(body())


def test_local_funasr_http_failure_is_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream local asr detail")

    async def body() -> None:
        provider = LocalFunAsrAsrProvider(
            LOCAL_ASR_ENDPOINT, transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ProviderUnavailable, match=r"本地 ASR 端点返回 HTTP 500") as exc:
            await provider.transcribe(b"audio-bytes")
        _assert_sanitized(exc)

    asyncio.run(body())


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),  # 非合法 JSON
        httpx.Response(200, json=["a", "b"]),  # 顶层非对象
        httpx.Response(200, json={"confidence": 0.9}),  # text 缺失
        httpx.Response(200, json={"text": 123}),  # text 非字符串
        httpx.Response(200, json={"text": "文本", "confidence": "high"}),  # confidence 非数值
    ],
    ids=["not-json", "not-object", "text-missing", "text-not-str", "confidence-not-numeric"],
)
def test_local_funasr_rejects_invalid_payload_shapes(response: httpx.Response) -> None:
    """ASR JSON 形状校验：非法载荷 fail-closed（不猜测转写结果）且文案脱敏。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return response

    async def body() -> None:
        provider = LocalFunAsrAsrProvider(
            LOCAL_ASR_ENDPOINT, transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ProviderUnavailable, match="本地 ASR 响应") as exc:
            await provider.transcribe(b"audio-bytes")
        _assert_sanitized(exc)

    asyncio.run(body())


# ---------- LocalCosyVoiceTtsProvider：MockTransport ----------


def test_local_cosyvoice_success_preserves_wav_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/v1/audio/speech"
        assert payload["model"] == "Fun-CosyVoice3-0.5B-2512"
        assert payload["input"] == "本地合成文本"
        assert payload["response_format"] == "wav"
        assert "Authorization" not in request.headers  # 无 key 不发鉴权头
        return httpx.Response(200, content=_sine_wav(0.4), headers={"Content-Type": "audio/wav"})

    async def body() -> None:
        provider = LocalCosyVoiceTtsProvider(
            LOCAL_TTS_ENDPOINT, api_key="", transport=httpx.MockTransport(handler),
        )
        result = await provider.synthesize("本地合成文本")
        assert result.audio_format == "wav"
        assert result.provider == "local-cosyvoice"
        assert result.audio[:4] == b"RIFF" and result.audio[8:12] == b"WAVE"
        assert result.latency_ms >= 0

    asyncio.run(body())


def test_local_cosyvoice_network_and_http_failures_are_sanitized() -> None:
    def network_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out connecting to http://127.0.0.1:8011")

    def http_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream cosyvoice detail")

    async def body() -> None:
        network = LocalCosyVoiceTtsProvider(
            LOCAL_TTS_ENDPOINT, transport=httpx.MockTransport(network_handler),
        )
        with pytest.raises(ProviderUnavailable, match="本地 TTS 请求失败") as exc:
            await network.synthesize("文本")
        _assert_sanitized(exc, "timed out")

        http_failure = LocalCosyVoiceTtsProvider(
            LOCAL_TTS_ENDPOINT, transport=httpx.MockTransport(http_handler),
        )
        with pytest.raises(ProviderUnavailable, match=r"本地 TTS 端点返回 HTTP 503") as exc:
            await http_failure.synthesize("文本")
        _assert_sanitized(exc)

    asyncio.run(body())


@pytest.mark.parametrize(
    "content",
    [b"", b"\xff\xfb\x90\x00mp3-frame-bytes", b"RIFFxxxxBUNK"],
    ids=["empty", "non-riff", "riff-but-not-wave"],
)
def test_local_cosyvoice_rejects_invalid_audio(content: bytes) -> None:
    """TTS 音频校验：空/非 RIFF/非 WAVE（请求了 response_format=wav）一律拒绝。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, headers={"Content-Type": "audio/wav"})

    async def body() -> None:
        provider = LocalCosyVoiceTtsProvider(
            LOCAL_TTS_ENDPOINT, transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ProviderUnavailable, match="本地 TTS 响应") as exc:
            await provider.synthesize("文本")
        _assert_sanitized(exc)

    asyncio.run(body())


def test_local_cosyvoice_invalid_json_response_is_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json", headers={"Content-Type": "audio/wav"})

    async def body() -> None:
        provider = LocalCosyVoiceTtsProvider(
            LOCAL_TTS_ENDPOINT, transport=httpx.MockTransport(handler),
        )
        with pytest.raises(ProviderUnavailable, match="本地 TTS 响应不是 RIFF/WAV") as exc:
            await provider.synthesize("文本")
        _assert_sanitized(exc)

    asyncio.run(body())


# ---------- API 路由：providers 视图 + 响应头 + 502 脱敏 + 422 ----------


class _StubLocalAsr:
    """替身本地 ASR（monkeypatch 注入路由模块）：成功返回或抛固定脱敏错误。"""

    name = "local-funasr"
    error: str | None = None

    def __init__(self, *args, **kwargs) -> None: ...

    async def transcribe(self, audio: bytes, *, content_type: str = "audio/wav") -> TranscriptionResult:
        if _StubLocalAsr.error:
            raise ProviderUnavailable(_StubLocalAsr.error)
        return TranscriptionResult(text="本地识别文本", confidence=0.9, provider=self.name, latency_ms=5)


class _StubLocalTts:
    """替身本地 TTS（monkeypatch 注入路由模块）：成功返回 WAV 或抛固定脱敏错误。"""

    name = "local-cosyvoice"
    error: str | None = None

    def __init__(self, *args, **kwargs) -> None: ...

    async def synthesize(self, text: str):
        from app.voice.providers import SynthesisResult

        if _StubLocalTts.error:
            raise ProviderUnavailable(_StubLocalTts.error)
        return SynthesisResult(audio=_sine_wav(0.4), audio_format="wav", provider=self.name, latency_ms=7)


@pytest.fixture()
def _stub_local_providers(monkeypatch):
    _StubLocalAsr.error = None
    _StubLocalTts.error = None
    monkeypatch.setattr("app.api.routes.voice.LocalFunAsrAsrProvider", _StubLocalAsr)
    monkeypatch.setattr("app.api.routes.voice.LocalCosyVoiceTtsProvider", _StubLocalTts)
    yield
    _StubLocalAsr.error = None
    _StubLocalTts.error = None


def test_providers_view_reflects_real_local_engines(monkeypatch) -> None:
    """local 且 endpoint 配好 → 视图透出 local-funasr/local-cosyvoice 且无 fallback。"""
    _configure(monkeypatch, mode="local", asr_local=True, tts_local=True)
    with TestClient(create_app(None)) as client:
        body = client.get("/api/v1/voice/providers").json()
        assert body["voice_mode"] == "local"
        assert (body["asr"]["provider"], body["asr"]["fallback"]) == ("local-funasr", False)
        assert (body["tts"]["provider"], body["tts"]["fallback"]) == ("local-cosyvoice", False)


def test_providers_view_hybrid_local_asr_real(monkeypatch) -> None:
    """hybrid：ASR 本地真实引擎 + TTS 云端语义（未配置云端 → tone fallback）。"""
    _configure(monkeypatch, mode="hybrid", asr_local=True)
    with TestClient(create_app(None)) as client:
        body = client.get("/api/v1/voice/providers").json()
        assert (body["asr"]["provider"], body["asr"]["fallback"]) == ("local-funasr", False)
        assert (body["tts"]["provider"], body["tts"]["fallback"]) == ("tone", True)


def test_transcribe_with_local_engine_headers_and_persistence(monkeypatch, _stub_local_providers) -> None:
    """endpoint 配好 → 走真实本地 ASR：响应头 provider=local-funasr/fallback=0，transcript 恒落盘。"""
    _configure(monkeypatch, mode="local", asr_local=True)
    with TestClient(create_app(SQLITE_URL)) as client:
        response = client.post(
            "/api/v1/voice/transcribe",
            files={"audio": ("clip.wav", _sine_wav(0.4), "audio/wav")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "local-funasr"
        assert body["text"] == "本地识别文本"
        assert response.headers["x-voice-provider"] == "local-funasr"
        assert response.headers["x-voice-fallback"] == "0"
        recent = client.get("/api/v1/voice/transcripts").json()
        assert any(item["id"] == body["id"] for item in recent["items"])


def test_transcribe_fallback_headers_when_local_unconfigured(monkeypatch) -> None:
    """endpoint 未配置 → 降级 fake：响应头透出 fallback=1（不虚报真实引擎）。"""
    _configure(monkeypatch, mode="local")
    with TestClient(create_app(SQLITE_URL)) as client:
        response = client.post(
            "/api/v1/voice/transcribe",
            files={"audio": ("clip.wav", _sine_wav(0.4), "audio/wav")},
        )
        assert response.status_code == 200
        assert response.headers["x-voice-provider"] == "fake"
        assert response.headers["x-voice-fallback"] == "1"


def test_transcribe_local_failure_maps_to_sanitized_502(monkeypatch, _stub_local_providers) -> None:
    """真实引擎失败 → 502 固定脱敏文案（不回显 endpoint/key/异常文本）。"""
    _StubLocalAsr.error = "本地 ASR 请求失败（网络错误或超时）"
    _configure(monkeypatch, mode="local", asr_local=True)
    with TestClient(create_app(SQLITE_URL)) as client:
        response = client.post(
            "/api/v1/voice/transcribe",
            files={"audio": ("clip.wav", _sine_wav(0.4), "audio/wav")},
        )
        assert response.status_code == 502
        assert response.json()["detail"] == "本地 ASR 请求失败（网络错误或超时）"
        for marker in ("8010", "127.0.0.1", "local-test-key"):
            assert marker not in response.text


def test_synthesize_with_local_engine_headers(monkeypatch, _stub_local_providers) -> None:
    """endpoint 配好 → 走真实本地 TTS：WAV 正文 + provider/fallback 响应头。"""
    _configure(monkeypatch, mode="local", tts_local=True)
    with TestClient(create_app(None)) as client:
        response = client.post("/api/v1/voice/synthesize", json={"text": "本地合成文本"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert response.headers["x-voice-provider"] == "local-cosyvoice"
        assert response.headers["x-voice-fallback"] == "0"
        with wave.open(io.BytesIO(response.content), "rb") as reader:
            assert reader.getnframes() > 0


def test_synthesize_local_failure_maps_to_sanitized_502(monkeypatch, _stub_local_providers) -> None:
    _StubLocalTts.error = "本地 TTS 端点返回 HTTP 503"
    _configure(monkeypatch, mode="local", tts_local=True)
    with TestClient(create_app(None)) as client:
        response = client.post("/api/v1/voice/synthesize", json={"text": "本地合成文本"})
        assert response.status_code == 502
        assert response.json()["detail"] == "本地 TTS 端点返回 HTTP 503"
        for marker in ("8011", "127.0.0.1", "local-test-key"):
            assert marker not in response.text


def test_unknown_provider_rejected_with_422(monkeypatch) -> None:
    """未知 provider 名：422 点名（ASR/TTS 各自校验），不静默降级。"""
    _configure(monkeypatch, mode="local", asr_provider="nonexistent-asr")
    with TestClient(create_app(SQLITE_URL)) as client:
        response = client.post(
            "/api/v1/voice/transcribe",
            files={"audio": ("clip.wav", _sine_wav(0.4), "audio/wav")},
        )
        assert response.status_code == 422
        assert "未知 ASR provider" in response.json()["detail"]

    _configure(monkeypatch, mode="local", tts_provider="nonexistent-tts")
    with TestClient(create_app(None)) as client:
        response = client.post("/api/v1/voice/synthesize", json={"text": "文本"})
        assert response.status_code == 422
        assert "未知 TTS provider" in response.json()["detail"]
