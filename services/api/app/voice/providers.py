"""M4-02/M14-01 ASR/TTS adapter：provider 协议 + 本地替身/本地真实引擎/云端实现。

定版约束（12 号 runbook + M14-01 定版）：
- ASR、LLM、TTS 分别配置 provider，不绑定单一厂商 SDK（本模块即协议层）；
- VOICE_MODE=local/hybrid/cloud 控制语音链路（routing.resolve_*）；
- 每次调用记录 provider 与 latency（TranscriptionResult/SynthesisResult 携带）。

本地实现分两层（诚实命名，绝不冒充）：
- 零依赖替身（M4-02）：fake-asr 确定性转写替身（显式 [fake] 前缀）、tone-tts
  stdLib wave 合成可播放 WAV（时长∝文本长度，非智能语音）；
- 真实本地引擎 HTTP adapter（M14-01）：主 API 不嵌模型 SDK，只经 HTTP 调本机
  服务——local-funasr（funasr-server SenseVoiceSmall CPU，/v1/audio/transcriptions）
  与 local-cosyvoice（CosyVoice 官方仓库 + OpenAI 兼容 bridge，/v1/audio/speech，
  WAV）。部署脚本见 tools/voice/；endpoint 未配置时由 routing 降级替身并透出
  fallback，不虚报已接真实引擎。

cloud-openai / cloud-openai-tts：OpenAI 兼容 /audio/transcriptions 与 /audio/speech
（httpx）。本地与云端复用同一 OpenAI 兼容请求实现（_openai_compatible_*），但
provider 名与失败文案明确区分——本地引擎不冒充 cloud。
失败语义 fail-closed 固定脱敏文案（不嵌 endpoint/key/httpx 异常文本），ASR 响应
形状校验、TTS 音频非空且 RIFF/WAV（response_format=wav 时不冒充，M10-13）。
"""
from __future__ import annotations

import io
import math
import struct
import time
import wave
from dataclasses import dataclass
from typing import Protocol

ASR_FAKE = "fake"
ASR_CLOUD = "cloud-openai"
ASR_LOCAL_FUNASR = "local-funasr"
TTS_TONE = "tone"
TTS_LOCAL_COSYVOICE = "local-cosyvoice"
LOCAL_ASR = ASR_FAKE
LOCAL_TTS = TTS_TONE
SAMPLE_RATE = 8000
TONE_HZ = 440.0
TONE_SECONDS_PER_CHAR = 0.12
TONE_MIN_SECONDS = 0.4


class ProviderUnavailable(RuntimeError):
    """provider 无法使用（未配置 endpoint/引擎未部署）。"""


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    confidence: float
    provider: str
    latency_ms: int
    is_final: bool = True


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    audio: bytes
    audio_format: str  # "wav"
    provider: str
    latency_ms: int


class AsrProvider(Protocol):
    name: str

    async def transcribe(self, audio: bytes, *, content_type: str = "audio/wav") -> TranscriptionResult: ...


class TtsProvider(Protocol):
    name: str

    async def synthesize(self, text: str) -> SynthesisResult: ...


class FakeAsrProvider:
    """确定性转写替身：同输入恒同输出，显式 [fake] 标记。"""

    name = ASR_FAKE

    async def transcribe(self, audio: bytes, *, content_type: str = "audio/wav") -> TranscriptionResult:
        started = time.perf_counter()
        result = TranscriptionResult(
            text=f"[fake]({len(audio)}B)",
            confidence=0.99,
            provider=self.name,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return result


class ToneTtsProvider:
    """零依赖 WAV 合成：440Hz 正弦、8kHz/16bit/mono，时长随文本长度增长。"""

    name = TTS_TONE

    async def synthesize(self, text: str) -> SynthesisResult:
        started = time.perf_counter()
        seconds = max(TONE_MIN_SECONDS, TONE_SECONDS_PER_CHAR * max(len(text.strip()), 1))
        frames = _sine_wav(seconds)
        return SynthesisResult(
            audio=frames,
            audio_format="wav",
            provider=self.name,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


async def _openai_compatible_transcribe(
    *,
    endpoint: str,
    api_key: str,
    model: str,
    audio: bytes,
    content_type: str,
    provider_name: str,
    label: str,
    transport=None,
    timeout: float = 30.0,
) -> TranscriptionResult:
    """OpenAI 兼容 /audio/transcriptions 请求（云端与本地真实引擎共用实现）。

    fail-closed 与脱敏口径（M10-13，本地同款 M14-01）：网络/HTTP/JSON 失败一律
    ProviderUnavailable 固定文案——不嵌 httpx 异常文本（str(cause) 含请求
    URL/endpoint），不回显 endpoint、key 或鉴权头（HTTP 失败只透状态码）；响应
    JSON 形状校验——顶层必须是对象、text 字段必须是字符串、confidence 非数值
    即拒绝（不猜测转写结果）。api_key 为空时不发 Authorization 头（本地服务
    默认无鉴权；云端调用方恒传 key）。
    """
    import httpx

    started = time.perf_counter()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport, headers=headers) as client:
            response = await client.post(
                f"{endpoint.rstrip('/')}/audio/transcriptions",
                files={"file": ("audio", audio, content_type)},
                data={"model": model},
            )
    except httpx.HTTPError as cause:
        # str(cause) 含请求 URL（endpoint）——固定文案，不回显敏感值
        raise ProviderUnavailable(f"{label} 请求失败（网络错误或超时）") from cause
    if not httpx.codes.is_success(response.status_code):
        raise ProviderUnavailable(f"{label} 端点返回 HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as cause:
        raise ProviderUnavailable(f"{label} 响应不是合法 JSON") from cause
    if not isinstance(payload, dict):
        raise ProviderUnavailable(f"{label} 响应不是 JSON 对象")
    text = payload.get("text")
    if not isinstance(text, str):
        raise ProviderUnavailable(f"{label} 响应 text 字段缺失或不是字符串")
    try:
        confidence = float(payload.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError) as cause:
        raise ProviderUnavailable(f"{label} 响应 confidence 字段类型非法") from cause
    return TranscriptionResult(
        text=text,
        confidence=confidence,
        provider=provider_name,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


async def _openai_compatible_synthesize(
    *,
    endpoint: str,
    api_key: str,
    model: str,
    text: str,
    provider_name: str,
    label: str,
    transport=None,
    timeout: float = 60.0,
    voice: str | None = None,
) -> SynthesisResult:
    """OpenAI 兼容 /audio/speech 请求（云端与本地真实引擎共用实现）。

    请求固定 response_format=wav——响应音频必须非空且带 RIFF/WAV 头，否则
    ProviderUnavailable（不把非 WAV 字节冒充 wav 结果）；网络/HTTP 失败用固定
    脱敏文案（不嵌 endpoint）。api_key 为空时不发 Authorization 头（本地
    bridge 可选鉴权；云端调用方恒传 key）。

    M14-65 voice：可选音色名（如 BigModel glm-tts 的 tongtong）——非空才写入
    请求体 voice 字段；None/空串不带该键（本地真实引擎与既有端点的请求体
    保持逐字节不变，云端中性默认由调用方以空值表达）。
    """
    import httpx

    started = time.perf_counter()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    payload = {"model": model, "input": text, "response_format": "wav"}
    if voice:
        payload["voice"] = voice
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport, headers=headers) as client:
            response = await client.post(
                f"{endpoint.rstrip('/')}/audio/speech",
                json=payload,
            )
    except httpx.HTTPError as cause:
        # str(cause) 含请求 URL（endpoint）——固定文案，不回显敏感值
        raise ProviderUnavailable(f"{label} 请求失败（网络错误或超时）") from cause
    if not httpx.codes.is_success(response.status_code):
        raise ProviderUnavailable(f"{label} 端点返回 HTTP {response.status_code}")
    audio = response.content
    if not audio:
        raise ProviderUnavailable(f"{label} 响应音频为空")
    if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise ProviderUnavailable(f"{label} 响应不是 RIFF/WAV 音频（已请求 response_format=wav）")
    return SynthesisResult(
        audio=audio,
        audio_format="wav",
        provider=provider_name,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


class CloudOpenAiAsrProvider:
    """OpenAI 兼容转写端点（endpoint/key 由部署 secret 提供，不入库不入码）。

    失败语义见 _openai_compatible_transcribe（M10-13 口径，本地真实引擎同款）。
    """

    name = ASR_CLOUD

    def __init__(self, endpoint: str, api_key: str, model: str, transport=None) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._transport = transport  # 测试注入 httpx.MockTransport

    async def transcribe(self, audio: bytes, *, content_type: str = "audio/wav") -> TranscriptionResult:
        return await _openai_compatible_transcribe(
            endpoint=self._endpoint,
            api_key=self._api_key,
            model=self._model,
            audio=audio,
            content_type=content_type,
            provider_name=self.name,
            label="cloud ASR",
            transport=self._transport,
            timeout=30.0,
        )


class CloudOpenAiTtsProvider:
    """OpenAI 兼容语音合成端点（/audio/speech，返回音频字节，M10-13 口径）。

    M14-65 voice：可选音色名透传到请求体 voice 字段（如 BigModel glm-tts 的
    tongtong）；None/空串不带该键（端点侧默认音色）。默认值只在 Settings
    （tts_cloud_voice=tongtong）——provider 是纯透传，不内置云端默认。
    """

    name = "cloud-openai-tts"

    def __init__(self, endpoint: str, api_key: str, model: str, voice: str | None = None, transport=None) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._voice = (voice or "").strip()  # 空 = 请求不带 voice 字段
        self._transport = transport
        self.name = "cloud-openai-tts"

    async def synthesize(self, text: str) -> SynthesisResult:
        return await _openai_compatible_synthesize(
            endpoint=self._endpoint,
            api_key=self._api_key,
            model=self._model,
            text=text,
            provider_name=self.name,
            label="cloud TTS",
            transport=self._transport,
            timeout=60.0,
            voice=self._voice,
        )


class LocalFunAsrAsrProvider:
    """本地 FunASR 转写端点（funasr-server，SenseVoiceSmall，OpenAI 兼容）。

    M14-01：主 API 不嵌 FunASR SDK，只经 HTTP 调本机服务（tools/voice/
    bootstrap_funasr_wsl.sh 部署，127.0.0.1:8010，CPU，默认无鉴权——api_key
    可选留空，留空不发 Authorization 头）。失败语义与云端同口径 fail-closed
    固定脱敏文案（不嵌 endpoint/key/httpx 异常文本），响应形状校验同云端。
    """

    name = ASR_LOCAL_FUNASR

    def __init__(
        self,
        endpoint: str,
        api_key: str = "",
        model: str = "sensevoice",
        timeout: float = 60.0,
        transport=None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout  # CPU 转写首轮可慢于云端，默认放宽
        self._transport = transport  # 测试注入 httpx.MockTransport

    async def transcribe(self, audio: bytes, *, content_type: str = "audio/wav") -> TranscriptionResult:
        return await _openai_compatible_transcribe(
            endpoint=self._endpoint,
            api_key=self._api_key,
            model=self._model,
            audio=audio,
            content_type=content_type,
            provider_name=self.name,
            label="本地 ASR",
            transport=self._transport,
            timeout=self._timeout,
        )


class LocalCosyVoiceTtsProvider:
    """本地 CosyVoice 合成端点（tools/voice/cosyvoice_openai_bridge.py，OpenAI 兼容）。

    M14-01：主 API 不嵌 CosyVoice SDK，只经 HTTP 调本机 bridge（tools/voice/
    bootstrap_cosyvoice_wsl.sh 部署，127.0.0.1:8011，/v1/audio/speech 返回
    WAV；bridge 可选鉴权——api_key 留空不发 Authorization 头）。TTS 音频非空
    且 RIFF/WAV 校验与云端同款（response_format=wav 时不冒充）；失败固定
    脱敏文案。
    """

    name = TTS_LOCAL_COSYVOICE

    def __init__(
        self,
        endpoint: str,
        api_key: str = "",
        model: str = "Fun-CosyVoice3-0.5B-2512",
        timeout: float = 120.0,
        transport=None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout  # 首次合成含加载后推理，默认放宽
        self._transport = transport  # 测试注入 httpx.MockTransport

    async def synthesize(self, text: str) -> SynthesisResult:
        return await _openai_compatible_synthesize(
            endpoint=self._endpoint,
            api_key=self._api_key,
            model=self._model,
            text=text,
            provider_name=self.name,
            label="本地 TTS",
            transport=self._transport,
            timeout=self._timeout,
        )


def _sine_wav(seconds: float) -> bytes:
    """生成 440Hz 正弦 WAV（stdlib wave，确定性字节输出）。"""
    total = int(SAMPLE_RATE * seconds)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for index in range(total):
            value = int(12000 * math.sin(2 * math.pi * TONE_HZ * index / SAMPLE_RATE))
            frames += struct.pack("<h", value)
        writer.writeframes(bytes(frames))
    return buffer.getvalue()
