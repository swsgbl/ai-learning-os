"""M14-272c：语音管线部署接线（lifespan 安装 + 同步 FunASR transcriber）。

M14-272b 之后的最终接线切片：把 M14-272a 组装器
（``ServerVoicePipeline``）接进既有应用链——

1. **设置面**：``VoicePipelineSettingsLike`` 恰五属性（服务端
   ``livekit_url``/``livekit_api_key``/``livekit_api_secret`` +
   ``asr_local_endpoint``/``asr_local_model``）；
2. **同步 transcriber**：``sync_funasr_transcriber``——本地
   FunASR OpenAI 兼容端点的同步 HTTP 调用件（multipart 表单 →
   ``/audio/transcriptions``；api_key 空不发 Authorization 头；
   loopback ``trust_env=False``——与 M14-106 同口径，系统代理
   不得劫持本机请求）；失败统一通用净化文案（不嵌端点/响应
   体/HTTP 状态文本）；transport 可注入（测试 MockTransport）；
3. **应用包装**：``create_app_with_voice_pipeline`` 复用
   M14-265/266 受控工厂构造 + ``voice_pipeline_factory`` 注入
   （main.py lifespan 在 manager 建立后安装
   ``app.state.voice_pipeline``——与 M14-262 工厂注入同构：
   显式注入、缺省零变化、**注册 ≠ 启动**；关停顺序：先关管线
   会话再关 manager）。

边界：不做 HTTP 路由（三端 API 契约对齐后另切片）、不切换
部署入口（voice_entrypoint 仍为 M14-267 语义）、真实端到端
验收属 M14-272 验收切片；本模块零 LiveKit SDK 直连（经
M14-263 受控工厂链）。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

import httpx
from fastapi import FastAPI

from app.domain.voice_app_factory import (
    build_room_aware_token_provider_factory,
    build_voice_app_subscriber_factory,
)
from app.domain.voice_funasr_adapter import FunAsrBatchAdapter
from app.domain.voice_pipeline import ServerVoicePipeline
from app.domain.voice_runtime_factory import (
    build_room_aware_controlled_voice_subscriber_factory,
)
from app.domain.voice_runtime_manager import VoiceRuntimeManager
from app.domain.voice_subscriber_factory import ControlledVoiceSubscriberFactory
from app.main import create_app
from app.voice_app import voice_app_factory_config

#: 同步转写 HTTP 超时（CPU 转写可慢——与 LocalFunAsrAsrProvider 默认同宽）。
DEFAULT_ASR_TIMEOUT_SECONDS = 60.0

#: 通用净化消息白名单（绝不拼接端点/响应体/HTTP 文本）。
_MESSAGE_ASR_ENDPOINT_INVALID = "asr_local_endpoint 非法：非空字符串（本地 FunASR 端点）"
_MESSAGE_TRANSCRIBE_FAILED = "本地 ASR 转写失败（通用净化消息）"
_MESSAGE_TRANSCRIBE_BAD_PAYLOAD = "本地 ASR 响应非法（通用净化消息）"


class VoicePipelineWiringError(RuntimeError):
    """接线错误（消息为通用白名单文案）。"""


@runtime_checkable
class VoicePipelineSettingsLike(Protocol):
    """管线部署设置面：恰五属性（服务端 LiveKit 三件 + ASR 两件）。

    本协议的 ``livekit_url`` 是 M14-266 服务端口径（部署入口
    M14-267 由 ``livekit_server_url`` 映射）；浏览器面 URL 不在
    此面内。
    """

    @property
    def livekit_url(self) -> str: ...

    @property
    def livekit_api_key(self) -> str: ...

    @property
    def livekit_api_secret(self) -> str: ...

    @property
    def asr_local_endpoint(self) -> str | None: ...

    @property
    def asr_local_model(self) -> str | None: ...


class _TranscriptionText:
    """transcriber 产物（仅 text——FunAsrBatchAdapter 恰需此面）。"""

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


def _wrap_wav(pcm: bytes, sample_rate: int, channels: int) -> bytes:
    """裸 PCM → WAV 容器（RIFF 头 + data；16-bit LE）。

    服务端帧通道交付裸 PCM（LiveKit AudioFrame.data）——转写端点
    按 wav 解析，发送前包装容器头。
    """
    import struct

    bits = 16
    data_size = len(pcm)
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    header = b"RIFF" + struct.pack(
        "<I4s4sIHHIIHH4sI",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
        b"data",
        data_size,
    )
    return header + pcm


def sync_funasr_transcriber(
    endpoint: str,
    *,
    model: str = "sensevoice",
    api_key: str = "",
    timeout: float = DEFAULT_ASR_TIMEOUT_SECONDS,
    sample_rate: int = 48000,
    channels: int = 1,
    transport: httpx.BaseTransport | None = None,
) -> Callable[[bytes], _TranscriptionText]:
    """本地 FunASR 同步转写调用件（OpenAI 兼容 multipart 表单）。

    loopback ``trust_env=False``（M14-106：系统/环境代理不得劫持
    本机请求）；api_key 空不发 Authorization 头；失败统一净化
    文案（链彻底抑制——端点/响应体/状态文本绝不外泄）。
    transport 注入面仅供测试（MockTransport）。
    """
    canonical_endpoint = endpoint.rstrip("/")

    def transcribe(audio: bytes) -> _TranscriptionText:
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        client_kwargs: dict[str, object] = {
            "timeout": timeout,
            "trust_env": False,  # loopback 直连（M14-106）
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        failed = False
        bad_payload = False
        text: str | None = None
        try:
            with httpx.Client(**client_kwargs) as client:  # type: ignore[arg-type]
                response = client.post(
                    f"{canonical_endpoint}/audio/transcriptions",
                    files={
                        "file": (
                            "audio.wav",
                            _wrap_wav(audio, sample_rate, channels),
                            "audio/wav",
                        )
                    },
                    data={"model": model},
                    headers=headers,
                )
            if response.status_code != 200:
                failed = True
            else:
                payload = response.json()
                candidate = payload.get("text") if isinstance(payload, dict) else None
                if isinstance(candidate, str):
                    text = candidate
                else:
                    bad_payload = True
        except Exception:  # noqa: BLE001 —— 网络/解码失败统一净化
            failed = True
        if failed:
            raise VoicePipelineWiringError(_MESSAGE_TRANSCRIBE_FAILED) from None
        if bad_payload or text is None:
            raise VoicePipelineWiringError(_MESSAGE_TRANSCRIBE_BAD_PAYLOAD) from None
        return _TranscriptionText(text)

    return transcribe


def build_voice_pipeline_factory(
    settings: VoicePipelineSettingsLike,
    *,
    transcriber: Callable[[bytes], object] | None = None,
) -> Callable[[VoiceRuntimeManager], ServerVoicePipeline]:
    """设置 → 管线工厂（lifespan 在 manager 建立后调用安装）。

    凭据/URL 校验完全复用 M14-266/265 路径（``voice_app_factory_config``
    先行——非法值在任何管线/app 构造之前失败）；每会话经
    ``event_sink_factory``（M14-272a/272c 注入面）绑定该会话
    split；ASR adapter 用 FunAsrBatchAdapter（transcriber 缺省从
    ``asr_local_endpoint``/``asr_local_model`` 构造同步调用件；
    显式注入优先——测试/可替换面）。ASR endpoint 缺失/空白
    fail-closed（服务端订阅转写链必须有 ASR——绝不静默空转）。
    """
    endpoint = settings.asr_local_endpoint
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise VoicePipelineWiringError(_MESSAGE_ASR_ENDPOINT_INVALID)
    model = settings.asr_local_model or "sensevoice"
    resolved_transcriber = transcriber or sync_funasr_transcriber(
        endpoint, model=model
    )
    # M14-266 校验先行：非法凭据/URL 在任何后续构造之前失败
    config = voice_app_factory_config(settings)
    token_provider_factory = build_room_aware_token_provider_factory(
        api_key=config.api_key,
        api_secret=config.api_secret,
        ttl_seconds=config.ttl_seconds,
    )

    def factory_builder(split: object) -> ControlledVoiceSubscriberFactory:
        # URL 校验 + runtime 工厂 + event_sink 注入透传（M14-263/272c）
        return build_room_aware_controlled_voice_subscriber_factory(
            config.livekit_url,
            token_provider_factory,
            event_sink_factory=lambda: split,
        )

    def pipeline_factory(manager: VoiceRuntimeManager) -> ServerVoicePipeline:
        return ServerVoicePipeline(
            manager=manager,
            factory_builder=factory_builder,  # type: ignore[arg-type]
            asr_adapter_factory=lambda: FunAsrBatchAdapter(
                transcriber=resolved_transcriber
            ),
        )

    return pipeline_factory


def create_app_with_voice_pipeline(
    database_url: str | None = None,
    *,
    settings: VoicePipelineSettingsLike,
    transcriber: Callable[[bytes], object] | None = None,
) -> FastAPI:
    """部署入口：受控工厂注册 + 管线 lifespan 安装（显式注入）。

    M14-265/266 校验路径先行（非法设置先于任何 app 创建失败——
    绝不半装配）；``voice_pipeline_factory`` 显式注入
    main.create_app（缺省路径零变化——不传则不安装管线属性）。
    """
    pipeline_factory = build_voice_pipeline_factory(settings, transcriber=transcriber)
    factory = build_voice_app_subscriber_factory(voice_app_factory_config(settings))
    return create_app(
        database_url,
        voice_subscriber_factory=factory,
        voice_pipeline_factory=pipeline_factory,
    )


__all__ = [
    "DEFAULT_ASR_TIMEOUT_SECONDS",
    "VoicePipelineSettingsLike",
    "VoicePipelineWiringError",
    "build_voice_pipeline_factory",
    "create_app_with_voice_pipeline",
    "sync_funasr_transcriber",
]
