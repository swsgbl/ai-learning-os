"""M14-263：真实 LiveKit 工厂的受控装配函数（纯域层，fake-only 验证）。

M14-262 之后的下一个窄后端基础件：一个纯域装配函数
``build_controlled_voice_subscriber_factory(url, token_provider)``——
先用确定性规则校验 LiveKit URL 与 token-provider callable，再恰
一次调用 ``voice_runtime.livekit_runtime_factories(url)``（返回
闭包对——调用本身零 SDK 构造、零网络），把返回的 room 工厂与
audio-stream 工厂连同 token-provider callable 一起装进 M14-261
的 ``ControlledVoiceSubscriberFactory``。M14-264 增补 room-aware
变体 ``build_room_aware_controlled_voice_subscriber_factory(url,
token_provider_factory)``：provider 是 ``Callable[[str, str],
str]``，仅在订阅者启动时收到恰确的已校验 room/identity（杜绝
为错误房间签发 token）。**这不是运行时接线**：
不改 ``app.main.create_app``/FastAPI lifespan/app.state、不读
settings/环境变量/凭据/token 材料、不启动订阅者/房间/音频流/
LiveKit 连接（装配即返回，绝不触发任何 ``create``/启动路径）、
不加 HTTP 路由/readiness/公开暴露。真实调用留给后续受控切片。

安全契约：
- **URL 先行校验**（在任何工厂或 token-provider 调用之前）：
  非空普通字符串；仅 ``ws``/``wss`` scheme；主机名必须存在且
  非空；userinfo（用户名/密码）必须缺席；query 与 fragment
  必须缺席；端口必须合法（可解析为 1–65535 的整数，urlsplit
  解析失败或越界均确定性拒绝）。校验失败抛通用净化错误——
  **绝不把 URL 原文拼进错误消息**（URL 可能携带凭据形态）。
- **token provider 只验 callable**：绝不调用、绝不存储或检视
  其返回值（token 只经 token-provider callable 与订阅者启动
  局部流动——M14-255/261 契约保持）。
- **异常链彻底抑制**：runtime 工厂装配失败统一通用净化消息且
  在 except 块之外 ``raise ... from None``——``__cause__``/
  ``__context__``/序列化 traceback 均不含协作者异常文本。
- **零副作用**：除恰一次调用注入/被替换的
  ``livekit_runtime_factories`` 外，无网络/磁盘/进程/定时器/
  线程/事件循环/settings/环境/凭据/LiveKit SDK 副作用。
"""
from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from app.domain.voice_runtime import livekit_runtime_factories
from app.domain.voice_subscriber_factory import ControlledVoiceSubscriberFactory

#: 允许的 scheme 白名单（LiveKit realtime 信号面仅 ws/wss）。
_ALLOWED_SCHEMES = ("ws", "wss")

#: 合法端口范围（1–65535；保留端口 0 拒绝）。
_MAX_PORT = 65535

#: 通用净化消息白名单（绝不拼接 URL 原文/协作者异常文本）。
_MESSAGE_URL_INVALID = (
    "LiveKit URL 非法：非空字符串、仅 ws/wss、须有主机名、"
    "无 userinfo/query/fragment、端口合法（1–65535）"
)
_MESSAGE_TOKEN_PROVIDER_INVALID = "token provider 非法：必须为可调用对象"
_MESSAGE_RUNTIME_FACTORIES_FAILED = "语音运行时工厂装配失败（通用净化消息）"


class VoiceRuntimeFactoryError(RuntimeError):
    """装配错误（消息为通用白名单文案，绝不携带 URL/凭据/异常文本）。"""


def validate_livekit_url(url: object) -> str:
    """LiveKit URL 确定性校验，通过即原样返回（不规范化、不改写）。

    规则：非空普通字符串；仅 ws/wss scheme；主机名存在且非空；
    userinfo 缺席；query 与 fragment 缺席；端口可解析为 1–65535
    的整数。任何违规抛 ``VoiceRuntimeFactoryError``（通用净化
    消息——URL 原文绝不进错误文本）。
    """
    if not isinstance(url, str) or not url.strip():
        raise VoiceRuntimeFactoryError(_MESSAGE_URL_INVALID)
    try:
        parsed = urlsplit(url)
        port = parsed.port  # 非数字/越界在此抛 ValueError
    except ValueError:
        raise VoiceRuntimeFactoryError(_MESSAGE_URL_INVALID) from None
    if (
        parsed.scheme not in _ALLOWED_SCHEMES
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= _MAX_PORT)
    ):
        raise VoiceRuntimeFactoryError(_MESSAGE_URL_INVALID)
    return url


def _runtime_factory_pair(
    canonical_url: str,
) -> tuple[Callable[[], object], Callable[[object], object]]:
    """调用 ``livekit_runtime_factories`` 恰一次，验证并解包工厂对。

    **形状验证在守卫路径内完成**（Codex R1 修正）：返回值恰为
    二元可迭代且两元素均 callable 才通过；抛错/返回 None/长度
    错误（3 元组、1 元组）/非可迭代/元素非 callable——任何畸形
    形状统一转通用净化错误且在 except 块之外 raise——
    ``__cause__``/``__context__`` 均为 None（异常链彻底抑制，
    协作者异常文本/返回值内容绝不经 ValueError/TypeError 逃逸）。
    """
    room_factory: Callable[[], object] | None = None
    audio_stream_factory: Callable[[object], object] | None = None
    runtime_failed = False
    try:
        returned = livekit_runtime_factories(canonical_url)
        candidate_room, candidate_stream = returned  # 解包在守卫内
        if callable(candidate_room) and callable(candidate_stream):
            room_factory = candidate_room
            audio_stream_factory = candidate_stream
    except Exception:  # noqa: BLE001 —— 抛错/解包失败统一转净化错误
        runtime_failed = True
    if runtime_failed or room_factory is None or audio_stream_factory is None:
        # 在 except 块之外 raise：__context__ 亦为 None（异常链彻底抑制）
        raise VoiceRuntimeFactoryError(_MESSAGE_RUNTIME_FACTORIES_FAILED) from None
    return room_factory, audio_stream_factory


def build_controlled_voice_subscriber_factory(
    url: str,
    token_provider: Callable[[], str],
) -> ControlledVoiceSubscriberFactory:
    """装配受控订阅者工厂（URL 先行校验 → runtime 工厂恰一次 → 组装）。

    顺序契约：URL 校验 → token-provider callable 校验（均先于
    runtime 工厂调用）；``livekit_runtime_factories(validated_url)``
    恰调用一次；返回的 room/audio-stream 工厂与 token-provider
    callable 以引用透传 ``ControlledVoiceSubscriberFactory``（装配
    期间零调用）；装配即返回——绝不触发订阅者创建/启动路径。
    runtime 工厂失败转通用净化错误（异常链彻底抑制）。
    """
    canonical_url = validate_livekit_url(url)
    if not callable(token_provider):
        raise VoiceRuntimeFactoryError(_MESSAGE_TOKEN_PROVIDER_INVALID)
    room_factory, audio_stream_factory = _runtime_factory_pair(canonical_url)
    return ControlledVoiceSubscriberFactory(
        token_provider=token_provider,
        room_factory=room_factory,
        audio_stream_factory=audio_stream_factory,
    )


def build_room_aware_controlled_voice_subscriber_factory(
    url: str,
    token_provider_factory: Callable[[str, str], str],
) -> ControlledVoiceSubscriberFactory:
    """以 room-aware provider 装配受控订阅者工厂（M14-264）。

    与零参版本同一顺序契约：URL 校验（M14-263 语义原样复用）→
    token-provider **factory** callable 校验（先于 runtime 工厂
    调用；绝不调用它）→ ``livekit_runtime_factories(validated_url)``
    恰一次 → room/audio-stream 工厂与 provider factory 以引用透传
    ``ControlledVoiceSubscriberFactory``（room-aware 路径：provider
    factory 仅在订阅者启动时收到恰确的已校验 room/identity——
    杜绝为错误房间签发 token）。装配即返回——绝不触发订阅者
    创建/启动；runtime 工厂失败转通用净化错误（异常链彻底抑制）。
    """
    canonical_url = validate_livekit_url(url)
    if not callable(token_provider_factory):
        raise VoiceRuntimeFactoryError(_MESSAGE_TOKEN_PROVIDER_INVALID)
    room_factory, audio_stream_factory = _runtime_factory_pair(canonical_url)
    return ControlledVoiceSubscriberFactory(
        token_provider_factory=token_provider_factory,
        room_factory=room_factory,
        audio_stream_factory=audio_stream_factory,
    )


__all__ = [
    "VoiceRuntimeFactoryError",
    "build_controlled_voice_subscriber_factory",
    "build_room_aware_controlled_voice_subscriber_factory",
    "validate_livekit_url",
]
