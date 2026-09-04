"""M4-02 ASR/TTS adapter：provider 协议 + 本地实现 + 云端槽位。

定版约束（12 号 runbook）：
- ASR、LLM、TTS 分别配置 provider，不绑定单一厂商 SDK（本模块即协议层）；
- VOICE_MODE=local/hybrid/cloud 控制语音链路（resolve_providers）；
- 每次云端调用记录 provider 与 latency（TranscriptionResult/SynthesisResult 携带）。

本地实现是零依赖真实代码（可运行、可断言），诚实命名：
- fake-asr：确定性转写替身（显式 [fake] 前缀，绝不冒充真实 ASR）；
- tone-tts：stdLib wave 合成可播放 WAV（时长∝文本长度，非智能语音）。
funasr/cosyvoice 等部署级引擎按同一协议在部署环境注册（同 M1-04 Docling 先例）。
cloud-openai：OpenAI 兼容 /audio/transcriptions 与 /audio/speech（httpx）；失败语义
fail-closed 固定脱敏文案（不嵌 endpoint/key/httpx 异常文本），ASR 响应形状校验、
TTS 音频非空且 RIFF/WAV（response_format=wav 时不冒充，M10-13）。
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
TTS_TONE = "tone"
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


class CloudOpenAiAsrProvider:
    """OpenAI 兼容转写端点（endpoint/key 由部署 secret 提供，不入库不入码）。

    fail-closed 与脱敏口径与 CloudWebProvider（M10-12）一致：网络/HTTP/JSON 失败
    一律 ProviderUnavailable 固定文案——不嵌 httpx 异常文本（str(cause) 含请求
    URL/endpoint），不回显 endpoint、key 或鉴权头；响应 JSON 形状校验——顶层必须
    是对象、text 字段必须是字符串、confidence 非数值即拒绝（不猜测转写结果）。
    """

    name = ASR_CLOUD

    def __init__(self, endpoint: str, api_key: str, model: str, transport=None) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._transport = transport  # 测试注入 httpx.MockTransport

    async def transcribe(self, audio: bytes, *, content_type: str = "audio/wav") -> TranscriptionResult:
        import httpx

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=30.0,
                transport=self._transport,
                headers={"Authorization": f"Bearer {self._api_key}"},
            ) as client:
                response = await client.post(
                    f"{self._endpoint}/audio/transcriptions",
                    files={"file": ("audio", audio, content_type)},
                    data={"model": self._model},
                )
        except httpx.HTTPError as cause:
            # str(cause) 含请求 URL（endpoint）——固定文案，不回显敏感值
            raise ProviderUnavailable("cloud ASR 请求失败（网络错误或超时）") from cause
        if not httpx.codes.is_success(response.status_code):
            raise ProviderUnavailable(f"cloud ASR 端点返回 HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as cause:
            raise ProviderUnavailable("cloud ASR 响应不是合法 JSON") from cause
        if not isinstance(payload, dict):
            raise ProviderUnavailable("cloud ASR 响应不是 JSON 对象")
        text = payload.get("text")
        if not isinstance(text, str):
            raise ProviderUnavailable("cloud ASR 响应 text 字段缺失或不是字符串")
        try:
            confidence = float(payload.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError) as cause:
            raise ProviderUnavailable("cloud ASR 响应 confidence 字段类型非法") from cause
        return TranscriptionResult(
            text=text,
            confidence=confidence,
            provider=self.name,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


class CloudOpenAiTtsProvider:
    """OpenAI 兼容语音合成端点（/audio/speech，返回音频字节）。

    请求固定 response_format=wav——响应音频必须非空且带 RIFF/WAV 头，否则
    ProviderUnavailable（不把非 WAV 字节冒充 wav 结果）；网络/HTTP 失败用固定
    脱敏文案（同 CloudOpenAiAsrProvider/CloudWebProvider 口径，不嵌 endpoint）。
    """

    name = "cloud-openai-tts"

    def __init__(self, endpoint: str, api_key: str, model: str, transport=None) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._transport = transport
        self.name = "cloud-openai-tts"

    async def synthesize(self, text: str) -> SynthesisResult:
        import httpx

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=60.0,
                transport=self._transport,
                headers={"Authorization": f"Bearer {self._api_key}"},
            ) as client:
                response = await client.post(
                    f"{self._endpoint}/audio/speech",
                    json={"model": self._model, "input": text, "response_format": "wav"},
                )
        except httpx.HTTPError as cause:
            # str(cause) 含请求 URL（endpoint）——固定文案，不回显敏感值
            raise ProviderUnavailable("cloud TTS 请求失败（网络错误或超时）") from cause
        if not httpx.codes.is_success(response.status_code):
            raise ProviderUnavailable(f"cloud TTS 端点返回 HTTP {response.status_code}")
        audio = response.content
        if not audio:
            raise ProviderUnavailable("cloud TTS 响应音频为空")
        if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise ProviderUnavailable("cloud TTS 响应不是 RIFF/WAV 音频（已请求 response_format=wav）")
        return SynthesisResult(
            audio=audio,
            audio_format="wav",
            provider=self.name,
            latency_ms=int((time.perf_counter() - started) * 1000),
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
