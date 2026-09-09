"""M4-02/M14-01 语音链路 provider 路由：在线、本地、混合可配置切换。

定版语义（12 号 runbook + M14-01 定版）：
- local：ASR/TTS 全本地——本地真实引擎（local-funasr/local-cosyvoice）endpoint
  已配置则用真实引擎；未配置降级零依赖替身（fake/tone）并透出 fallback——
  不虚报已接真实引擎；
- hybrid：ASR 本地（语音原文不出本机，真实引擎优先、同上降级语义）、TTS 按
  配置可云端（仅文本出站）；
- cloud：ASR/TTS 均云端。
显式 ASR_PROVIDER/TTS_PROVIDER 覆盖 VOICE_MODE 的默认选择；云端未配置
endpoint 时降级替身并在结果透出 fallback——不虚报实际 provider。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.voice.providers import (
    ASR_LOCAL_FUNASR,
    LOCAL_ASR,
    LOCAL_TTS,
    TTS_LOCAL_COSYVOICE,
)

MODE_LOCAL = "local"
MODE_HYBRID = "hybrid"
MODE_CLOUD = "cloud"
VOICE_MODES = (MODE_LOCAL, MODE_HYBRID, MODE_CLOUD)
CLOUD_ASR = "cloud-openai"
CLOUD_TTS = "cloud-openai-tts"
#: M14-01 本地真实引擎 provider 名（HTTP adapter，部署见 tools/voice/）
LOCAL_REAL_ASR = ASR_LOCAL_FUNASR
LOCAL_REAL_TTS = TTS_LOCAL_COSYVOICE


@dataclass(frozen=True, slots=True)
class ProviderChoice:
    provider: str
    fallback: bool  # True = 想用的 provider 未配置，降级零依赖替身


def resolve_asr(
    voice_mode: str,
    requested: str | None,
    cloud_ready: bool = False,
    local_ready: bool = False,
) -> ProviderChoice:
    return _resolve("asr", voice_mode, requested, cloud_ready, local_ready)


def resolve_tts(
    voice_mode: str,
    requested: str | None,
    cloud_ready: bool = False,
    local_ready: bool = False,
) -> ProviderChoice:
    return _resolve("tts", voice_mode, requested, cloud_ready, local_ready)


def _resolve(
    kind: str,
    voice_mode: str,
    requested: str | None,
    cloud_ready: bool,
    local_ready: bool,
) -> ProviderChoice:
    if voice_mode not in VOICE_MODES:
        raise ValueError(f"未知 voice_mode: {voice_mode}")
    wanted = requested if requested is not None else _default_for(kind, voice_mode)
    if wanted in (CLOUD_ASR, CLOUD_TTS) and not cloud_ready:
        return ProviderChoice(provider=local_name_for(kind), fallback=True)
    if wanted in (LOCAL_REAL_ASR, LOCAL_REAL_TTS) and not local_ready:
        # M14-01：本地真实引擎 endpoint 未配置 → 降级零依赖替身并透出 fallback
        return ProviderChoice(provider=local_name_for(kind), fallback=True)
    return ProviderChoice(provider=wanted, fallback=False)


def _default_for(kind: str, voice_mode: str) -> str:
    if voice_mode == MODE_CLOUD:
        return CLOUD_ASR if kind == "asr" else CLOUD_TTS
    if voice_mode == MODE_HYBRID:
        # 混合：ASR 本地真实引擎（未配置由 _resolve 降级替身），TTS 云端（仅文本出站）
        return LOCAL_REAL_ASR if kind == "asr" else CLOUD_TTS
    # local：默认想要真实本地引擎（endpoint 未配置由 _resolve 降级替身并透出）
    return LOCAL_REAL_ASR if kind == "asr" else LOCAL_REAL_TTS


def local_name_for(kind: str) -> str:
    """零依赖替身名（fake/tone）——降级目标，非真实引擎。"""
    return LOCAL_ASR if kind == "asr" else LOCAL_TTS
