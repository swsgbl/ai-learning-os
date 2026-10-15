"""M14-261：受控语音订阅者工厂（纯域装配层，fake-only 验证）。

M14-260 之后的下一个窄后端基础件：一个纯域、受控的工厂——每次
``create(room, identity)`` 从注入的 fake 兼容协作者（token provider /
room factory / audio-stream factory）装配**一个** ``ServerVoiceSubscriber``
与**一个独立** ``BoundedVoiceFrameSink``，并返回结构满足
``ManagedSubscriberLike`` 的生命周期句柄。**这不是运行时接线**：
不导入/不调用真实 LiveKit SDK、不调用 ``livekit_runtime_factories``
（那是后续受控切片的职责）、不进 FastAPI lifespan/app.state、不加
HTTP 路由、不改 readiness、不做流式 ASR/VAD/barge-in/pause-resume。

设计契约：
- **构造零副作用**：工厂构造只保存注入的 callable 与正数配置
  （max_events）——不调用任何协作者、无网络/磁盘/进程/定时器/
  线程/事件循环操作；协作者仅在订阅者 ``start()`` 内部被调用
  （token 只经 token-provider callable 进入订阅者启动局部变量，
  绝不流经工厂/句柄状态——与订阅域安全契约同口径）。
- **参数先行校验**：``create()`` 先校验 room 与 identity 再做任何
  装配——无效参数在任何协作者调用之前确定性失败（订阅者根本
  未构造，token provider/room factory/audio-stream factory 零调用）。
- **room 边界与既有公开口径一致**：``^[A-Za-z0-9_-]{3,64}$``
  （与 API 路由层房间正则同口径、同约束——非字符串/空白/越界
  长度/非法字符集一律确定性拒绝）。
- **identity 保守既有风格边界**：与 room 采用同一保守正则
  ``^[A-Za-z0-9_-]{3,64}$``——不放宽 room 语义，且与
  ``voice_tokens.new_identity()`` 生成的 ``student-xxxxxxxx``
  形态及既有服务端 ``server-sub-*`` 命名天然兼容。
- **每次装配彼此独立**：每次 ``create()`` 新建一个独立 sink 与
  一个独立订阅者（无共享可变订阅者/汇状态；帧与事件只进本订阅者
  自己的汇）。
- **token 协作者二选一（M14-264）**：既有零参
  ``token_provider: Callable[[], str]``（源/语义完全兼容）或新
  room-aware ``token_provider_factory: Callable[[str, str], str]``
  ——两者都缺/都给确定性拒绝。room-aware 路径以捕获**已校验**
  canonical room/identity 的零参闭包适配订阅者：provider factory
  仅在订阅者启动时被调用、收到恰确的该句柄 room/identity（杜绝
  为错误房间签发 token）；其返回 token 仍只进订阅者启动局部，
  绝不存于工厂/句柄/投影。
- **句柄只暴露净化投影**：``start()/stop()/state()`` 委托订阅者；
  ``sink_snapshot()`` 委托汇的 ``snapshot()``（有界净化、全新拷贝）。
  句柄绝不暴露原始 sink 对象、订阅者内部、token 或音频字节；
  ``state()`` 对订阅者投影再做一层 JWT 形态递归净化（纵深防御
  ——即使外部音轨 sid 携带 token 形态也进不了公开投影）。
- **异常链抑制**：需要包装协作者失败的错误一律通用净化消息 +
  ``from None`` 且在 except 块之外 raise——``__cause__``/
  ``__context__``/序列化 traceback 均不含原始异常文本（token/
  room 工厂异常文本绝不泄漏）；token provider 失败由订阅者转
  failed 终态（通用净化事件），句柄据此抛通用启动失败错误。
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.domain.voice_frame_sink import BoundedVoiceFrameSink
from app.domain.voice_subscription import (
    DEFAULT_MAX_EVENTS,
    STATUS_FAILED,
    ServerVoiceSubscriber,
)

if TYPE_CHECKING:
    from app.domain.voice_subscription import AudioStreamLike, RoomLike

#: 房间名与身份标识共用保守边界（与 API 路由层公开房间正则同口径）。
ROOM_IDENTITY_PATTERN = r"^[A-Za-z0-9_-]{3,64}$"

_ROOM_IDENTITY_RE = re.compile(ROOM_IDENTITY_PATTERN)

#: JWT 三段形态（与运行时管理器/汇同口径）：句柄投影的纵深脱敏。
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")

_REDACTED = "[REDACTED-JWT]"

#: 通用净化消息白名单（绝不拼接协作者/订阅者异常文本）。
_MESSAGE_PROVIDER_CONFIG = "token provider 配置非法：token_provider 与 token_provider_factory 必须恰提供一个"
_MESSAGE_INVALID_COLLABORATOR = "注入协作者非法：token provider（或 room-aware provider factory）/room factory/audio-stream factory 必须为可调用对象"
_MESSAGE_INVALID_EVENT_SINK_FACTORY = "event_sink_factory 非法：必须为可调用对象"
_MESSAGE_INVALID_EVENT_SINK = "注入 event sink 非法：需要 on_event/on_frame 结构面"
_MESSAGE_SINK_SNAPSHOT_UNAVAILABLE = "注入 sink 无快照面：sink_snapshot 不可用（通用净化消息）"
_MESSAGE_INVALID_MAX_EVENTS = "max_events 必须为正"
_MESSAGE_ROOM_INVALID = "房间名非法：仅 [A-Za-z0-9_-]，长度 3–64"
_MESSAGE_IDENTITY_INVALID = "身份标识非法：仅 [A-Za-z0-9_-]，长度 3–64"
_MESSAGE_ASSEMBLY_FAILED = "订阅者装配失败（通用净化消息）"
_MESSAGE_START_FAILED = "订阅者启动失败（通用净化消息）"
_MESSAGE_STOP_FAILED = "订阅者停止失败（通用净化消息）"


class VoiceSubscriberFactoryError(RuntimeError):
    """工厂/句柄错误（消息为通用白名单文案，绝不携带 token/异常文本）。"""


@runtime_checkable
class _InjectedEventSinkLike(Protocol):
    """M14-272a：注入 event sink 的最小结构面（on_event/on_frame）。"""

    def on_event(self, event: object) -> None: ...

    def on_frame(self, frame: object) -> None: ...


def validate_room(room: object) -> str:
    """房间名校验：与既有公开房间边界 ``^[A-Za-z0-9_-]{3,64}$`` 同口径。

    非字符串（含 bytes/bool）、空白、长度越界（<3 或 >64）、非法
    字符集一律确定性拒绝；通过即原样返回（不做 trim——房间名是
    精确标识符，空白本身即非法）。
    """
    if not isinstance(room, str) or _ROOM_IDENTITY_RE.fullmatch(room) is None:
        raise VoiceSubscriberFactoryError(_MESSAGE_ROOM_INVALID)
    return room


def validate_identity(identity: object) -> str:
    """身份标识校验：保守既有风格边界——与 room 同一正则。

    不放宽 room 语义；与 ``voice_tokens.new_identity()`` 的
    ``student-xxxxxxxx`` 形态及既有 ``server-sub-*`` 服务端命名
    天然兼容。非字符串/空白/越界/非法字符集确定性拒绝。
    """
    if not isinstance(identity, str) or _ROOM_IDENTITY_RE.fullmatch(identity) is None:
        raise VoiceSubscriberFactoryError(_MESSAGE_IDENTITY_INVALID)
    return identity


def _sanitize_text(text: str) -> str:
    return _JWT_RE.sub(_REDACTED, text)


def _sanitize_value(value: object) -> object:
    """递归净化：字符串过 JWT 正则；列表/字典逐层复制（不改写原值）。"""
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_value(item) for key, item in value.items()}
    return value


class ControlledVoiceSubscriberHandle:
    """``create()`` 返回的生命周期句柄（结构满足 ``ManagedSubscriberLike``）。

    生命周期委托订阅者（``start``/``stop``/``state``）；另暴露
    ``sink_snapshot()`` 有界净化汇快照。任何订阅者异常统一转通用
    净化错误且异常链被彻底抑制（在 except 块之外 raise——
    ``__cause__``/``__context__`` 均为 None）；启动完成后订阅者已
    进入 failed 终态（如 token provider 失败被订阅者吞转为事件）
    同样转通用启动失败错误——fail-closed，调用方绝不误判成功。
    """

    __slots__ = ("_sink", "_subscriber")

    def __init__(
        self,
        subscriber: ServerVoiceSubscriber,
        sink: BoundedVoiceFrameSink,
    ) -> None:
        self._subscriber = subscriber
        self._sink = sink

    async def start(self) -> None:
        """委托订阅者启动；任何失败（异常或 failed 终态）转通用净化错误。"""
        start_failed = False
        try:
            await self._subscriber.start()
        except Exception:  # noqa: BLE001 —— 任何失败统一转净化错误
            start_failed = True
        if start_failed or str(self._subscriber.state().get("status", "")) == STATUS_FAILED:
            # 在 except 块之外 raise：__context__ 亦为 None（异常链彻底抑制）
            raise VoiceSubscriberFactoryError(_MESSAGE_START_FAILED) from None

    async def stop(self) -> None:
        """委托订阅者停止（订阅者自身保证确定清理顺序）；异常转净化错误。"""
        stop_failed = False
        try:
            await self._subscriber.stop()
        except Exception:  # noqa: BLE001 —— 任何失败统一转净化错误
            stop_failed = True
        if stop_failed:
            raise VoiceSubscriberFactoryError(_MESSAGE_STOP_FAILED) from None

    def state(self) -> dict[str, object]:
        """订阅者状态投影的净化拷贝（JWT 形态递归脱敏，纵深防御）。"""
        return {
            str(key): _sanitize_value(item)
            for key, item in self._subscriber.state().items()
        }

    def sink_snapshot(self) -> dict[str, object]:
        """有界净化汇快照（全新拷贝；不暴露原始 sink 对象）。

        M14-272a：注入 sink 无 ``snapshot`` 面时确定性拒绝
        （通用净化文案——注入面自带快照能力才可查询）。
        """
        snapshot = getattr(self._sink, "snapshot", None)
        if not callable(snapshot):
            raise VoiceSubscriberFactoryError(_MESSAGE_SINK_SNAPSHOT_UNAVAILABLE)
        return snapshot()

    def __repr__(self) -> str:
        return "<ControlledVoiceSubscriberHandle>"


class ControlledVoiceSubscriberFactory:
    """受控订阅者装配工厂（纯域、显式构造、零构造副作用）。

    注入 fake 兼容协作者三件套；``create(room, identity)`` 先校验
    参数、再装配一对彼此独立的 sink + 订阅者并返回生命周期句柄。
    工厂自身无状态保留（不注册、不计数、不持有装配产物）——
    注册/容量/排空是 ``VoiceRuntimeManager`` 的职责（本工厂的
    ``create`` 可直接作为管理器 ``start(key, factory)`` 的零参
    工厂闭包体：``lambda: controlled_factory.create(room, ident)``）。
    """

    def __init__(
        self,
        *,
        token_provider: Callable[[], str] | None = None,
        token_provider_factory: Callable[[str, str], str] | None = None,
        room_factory: Callable[[], RoomLike],
        audio_stream_factory: Callable[[object], AudioStreamLike],
        max_events: int = DEFAULT_MAX_EVENTS,
        event_sink_factory: Callable[[], object] | None = None,
    ) -> None:
        # M14-264：token 协作者二选一——零参 token_provider 或
        # room-aware token_provider_factory；两者都缺/都给确定性
        # 拒绝（在构造期即失败，绝不静默偏选）。
        if (token_provider is None) == (token_provider_factory is None):
            raise VoiceSubscriberFactoryError(_MESSAGE_PROVIDER_CONFIG)
        for collaborator in (
            token_provider,
            token_provider_factory,
            room_factory,
            audio_stream_factory,
        ):
            if collaborator is not None and not callable(collaborator):
                raise VoiceSubscriberFactoryError(_MESSAGE_INVALID_COLLABORATOR)
        # M14-272a：可选 event_sink_factory——缺省保持既有内部
        # BoundedVoiceFrameSink 装配（零回归）；给定时 create 用其
        # 产物作订阅者 event_sink（结构面在 create 期校验）。
        if event_sink_factory is not None and not callable(event_sink_factory):
            raise VoiceSubscriberFactoryError(_MESSAGE_INVALID_EVENT_SINK_FACTORY)
        if not isinstance(max_events, int) or isinstance(max_events, bool) or max_events <= 0:
            raise VoiceSubscriberFactoryError(_MESSAGE_INVALID_MAX_EVENTS)
        self._token_provider = token_provider
        self._token_provider_factory = token_provider_factory
        self._room_factory = room_factory
        self._audio_stream_factory = audio_stream_factory
        self._max_events = max_events
        self._event_sink_factory = event_sink_factory

    def create(self, room: object, identity: object) -> ControlledVoiceSubscriberHandle:
        """装配一个订阅者 + 一个独立汇（参数先行校验；零协作者预调用）。

        无效 room/identity 在任何协作者调用之前确定性失败；装配
        失败统一转通用净化错误（异常链彻底抑制）。token 协作者以
        callable 引用透传订阅者——本方法绝不调用它（token 只在
        订阅者启动局部变量中短暂存在，用后即不可达）。M14-264
        room-aware 路径：以捕获**已校验** canonical_room/
        canonical_identity 的零参闭包适配订阅者——provider factory
        仅在订阅者启动时被调用，且收到恰确的该句柄 room/identity
        （杜绝为错误房间签发 token）；其返回 token 仍只进订阅者
        启动局部，绝不存于工厂/句柄。
        """
        canonical_room = validate_room(room)
        canonical_identity = validate_identity(identity)
        provider_factory = self._token_provider_factory
        if provider_factory is not None:

            def subscriber_token_provider() -> str:
                return provider_factory(canonical_room, canonical_identity)

        else:
            subscriber_token_provider = self._token_provider
        sink: object | None = None
        subscriber: ServerVoiceSubscriber | None = None
        assembly_failed = False
        try:
            if self._event_sink_factory is not None:
                candidate = self._event_sink_factory()
                # 注入产物结构面校验（缺 on_event/on_face 确定性拒绝）
                if not isinstance(candidate, _InjectedEventSinkLike):
                    raise VoiceSubscriberFactoryError(_MESSAGE_INVALID_EVENT_SINK)
                sink = candidate
            else:
                sink = BoundedVoiceFrameSink(max_events=self._max_events)
            subscriber = ServerVoiceSubscriber(
                room=canonical_room,
                identity=canonical_identity,
                token_provider=subscriber_token_provider,
                room_factory=self._room_factory,
                audio_stream_factory=self._audio_stream_factory,
                event_sink=sink,
                max_events=self._max_events,
            )
        except Exception:  # noqa: BLE001 —— 装配失败统一转净化错误
            assembly_failed = True
        if assembly_failed or sink is None or subscriber is None:
            # 在 except 块之外 raise：__context__ 亦为 None（异常链彻底抑制）
            raise VoiceSubscriberFactoryError(_MESSAGE_ASSEMBLY_FAILED) from None
        return ControlledVoiceSubscriberHandle(subscriber, sink)


__all__ = [
    "ROOM_IDENTITY_PATTERN",
    "ControlledVoiceSubscriberFactory",
    "ControlledVoiceSubscriberHandle",
    "VoiceSubscriberFactoryError",
    "validate_identity",
    "validate_room",
]
