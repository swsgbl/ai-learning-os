"""M4-02 语音链路 provider 路由：在线、本地、混合可配置切换。

定版语义（12 号 runbook）：
- local：ASR/TTS 全本地；
- hybrid：ASR 本地（语音原文不出本机）、TTS 按配置可云端（仅文本出站）；
- cloud：ASR/TTS 均云端。
显式 ASR_PROVIDER/TTS_PROVIDER 覆盖 VOICE_MODE 的默认选择；
云端未配置 endpoint 时降级本地并在结果透出 fallback——不虚报实际 provider。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.voice.providers import LOCAL_ASR, LOCAL_TTS

MODE_LOCAL = "local"
MODE_HYBRID = "hybrid"
MODE_CLOUD = "cloud"
VOICE_MODES = (MODE_LOCAL, MODE_HYBRID, MODE_CLOUD)
CLOUD_ASR = "cloud-openai"
CLOUD_TTS = "cloud-openai-tts"


@dataclass(frozen=True, slots=True)
class ProviderChoice:
    provider: str
    fallback: bool  # True = 想用云端但未配置，降级本地


def resolve_asr(voice_mode: str, requested: str | None, cloud_ready: bool) -> ProviderChoice:
    return _resolve("asr", voice_mode, requested, cloud_ready, LOCAL_ASR, CLOUD_ASR)


def resolve_tts(voice_mode: str, requested: str | None, cloud_ready: bool) -> ProviderChoice:
    return _resolve("tts", voice_mode, requested, cloud_ready, LOCAL_TTS, CLOUD_TTS)


def _resolve(
    kind: str,
    voice_mode: str,
    requested: str | None,
    cloud_ready: bool,
    local_name: str,
    cloud_name: str,
) -> ProviderChoice:
    if voice_mode not in VOICE_MODES:
        raise ValueError(f"未知 voice_mode: {voice_mode}")
    wanted = requested if requested is not None else _default_for(kind, voice_mode)
    if wanted in (CLOUD_ASR, CLOUD_TTS) and not cloud_ready:
        return ProviderChoice(provider=local_name, fallback=True)
    return ProviderChoice(provider=wanted, fallback=False)


def _default_for(kind: str, voice_mode: str) -> str:
    if voice_mode == MODE_CLOUD:
        return CLOUD_ASR if kind == "asr" else CLOUD_TTS
    if voice_mode == MODE_HYBRID:
        # 混合：ASR 本地（原始音频不出本机），TTS 云端（仅文本出站）
        return LOCAL_ASR if kind == "asr" else CLOUD_TTS
    return local_name_for(kind)


def local_name_for(kind: str) -> str:
    return LOCAL_ASR if kind == "asr" else LOCAL_TTS
