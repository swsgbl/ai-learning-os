"""M14-266：显式语音应用接线（适配 + 包装，fake-only 验证）。

M14-265 之后的窄应用接线切片：把**已加载的显式设置对象**适配为
``VoiceAppFactoryConfig``，复用 M14-265
``build_voice_app_subscriber_factory`` 构造受控订阅者工厂，再以
keyword 参数**显式**传入既有 ``create_app``——语音接线只能经由
``create_app_with_voice_subscriber`` 显式发生。

边界（非声明）：
- **零隐式接线**：不改 ``app.main``——模块级默认
  ``app = create_app()`` 原样（仍无订阅者工厂）；``create_app``
  签名/lifespan 实现零改动；``create_app(None)`` 与
  ``voice_subscriber_factory=None`` 语义不变（M14-262 契约）。
- **零配置/环境读取**：设置对象必须显式传入；本模块绝不调用
  全局设置读取入口、绝不读 env 文件/环境变量/secret 文件/
  全局态。
- **服务端 URL 语义**：适配器只读 ``livekit_url``（服务端
  URL）——**故意不读** ``public_livekit_url``（面向浏览器公开
  地址，非服务端订阅者可达面）；TTL 无既有类型化设置——按
  ``voice_tokens.DEFAULT_TTL_SECONDS``（本切片不新增设置项）。
- **复用而非重复**：凭据/URL/runtime 形状/token/净化逻辑全部
  复用 M14-265/264/263——本模块只做适配与显式传递；工厂失败
  （含设置值非法）发生在任何 FastAPI app 返回之前。
- **畸形设置访问净化**：属性缺失/属性读取抛错统一转通用净化
  域错误（守卫后 ``raise ... from None``——``__cause__``/
  ``__context__``/traceback 均不含设置值/属性名/URL/凭据/
  协作者异常文本）。
- **注册 ≠ 启动**：显式注入只在 lifespan 期间安装
  ``app.state.voice_subscriber_factory``；绝不创建/启动订阅者、
  不签发 token、不调用 LiveKit SDK；管理器所有权/关停顺序
  （M14-258/259 契约）零变化。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from fastapi import FastAPI

from app.domain.voice_app_factory import (
    VoiceAppFactoryConfig,
    build_voice_app_subscriber_factory,
)
from app.domain.voice_tokens import DEFAULT_TTL_SECONDS
from app.main import create_app

#: 通用净化消息白名单（绝不包含设置值/属性名/URL/凭据/异常文本）。
_MESSAGE_SETTINGS_INVALID = "设置对象非法：无法读取语音装配所需字段"


class VoiceAppWiringError(RuntimeError):
    """接线错误（消息为通用白名单文案，绝不携带设置值/异常文本）。"""


@runtime_checkable
class VoiceAppSettingsLike(Protocol):
    """语音应用接线所需的最小设置面（结构满足即可，不要求配置类）。

    恰三个必读属性——服务端 URL/API key/API secret；
    ``public_livekit_url`` 故意不在面内（公开地址不用于服务端
    订阅者装配）。
    """

    @property
    def livekit_url(self) -> str: ...

    @property
    def livekit_api_key(self) -> str: ...

    @property
    def livekit_api_secret(self) -> str: ...


def voice_app_factory_config(settings: VoiceAppSettingsLike) -> VoiceAppFactoryConfig:
    """已加载设置对象 → ``VoiceAppFactoryConfig``（只读恰三字段）。

    属性访问全部在守卫内完成：缺失/抛错统一转通用净化域错误
    （守卫后 ``raise ... from None``——异常链彻底抑制）。TTL 用
    ``DEFAULT_TTL_SECONDS``（无既有类型化设置，本切片不新增）；
    值级校验（凭据/URL）不在本层重复——由 M14-265 装配先行
    完成（失败在任何签发/runtime/create_app 之前）。
    """

    def _read(name: str) -> object:
        return getattr(settings, name)

    values: dict[str, object] = {}
    read_failed = False
    try:
        for name in ("livekit_url", "livekit_api_key", "livekit_api_secret"):
            values[name] = _read(name)
    except Exception:  # noqa: BLE001 —— 任何读取失败统一转净化错误
        read_failed = True
    if read_failed or not isinstance(settings, VoiceAppSettingsLike):
        # 在 except 块之外 raise：__context__ 亦为 None（异常链彻底抑制）
        raise VoiceAppWiringError(_MESSAGE_SETTINGS_INVALID) from None
    return VoiceAppFactoryConfig(
        livekit_url=values["livekit_url"],  # type: ignore[arg-type]
        api_key=values["livekit_api_key"],  # type: ignore[arg-type]
        api_secret=values["livekit_api_secret"],  # type: ignore[arg-type]
        ttl_seconds=DEFAULT_TTL_SECONDS,
    )


def create_app_with_voice_subscriber(
    database_url: str | None = None,
    *,
    settings: VoiceAppSettingsLike,
) -> FastAPI:
    """显式语音接线包装：设置对象 → 工厂 → 既有 ``create_app``。

    顺序契约：适配设置 → 复用 M14-265 构造受控工厂（**先于**
    ``create_app``——工厂失败发生在任何 FastAPI app 返回之前，
    绝不半装配）→ 以 keyword 参数把**同一实例**显式传入既有
    ``create_app``（其校验/lifespan 安装/关停语义零变化）。
    设置对象必须显式提供——本包装绝不读取任何全局配置态。
    """
    config = voice_app_factory_config(settings)
    factory = build_voice_app_subscriber_factory(config)
    return create_app(database_url, voice_subscriber_factory=factory)


__all__ = [
    "VoiceAppSettingsLike",
    "VoiceAppWiringError",
    "create_app_with_voice_subscriber",
    "voice_app_factory_config",
]
