"""M14-255：服务端 LiveKit 订阅基础（域层纯逻辑，零 FastAPI 接线/零运行时）。

目标：为「服务端订阅学习者已发布音轨」提供第一个最小后端基础件——
一个只消费、不发布的房间订阅控制器。**这不是流式 ASR，也不是运行时
集成**：本模块不进 FastAPI startup/lifespan、不新增/改动 HTTP 端点、
不调用 ASR/TTS/VAD/barge-in/pause-resume；真实运行时接线（livekit
rtc Room/AudioStream 的 async 适配）是后续切片的职责。

可测性契约：Room 工厂、AudioStream 工厂、token provider、事件汇全部
注入——全部行为可用 fake 零网络、确定性地验证（协议面见各 Protocol）。

安全契约：
- token 只存在于内存局部变量并直接传给 room.connect——绝不进入
  state()/事件/日志/错误消息（失败消息为白名单通用文案，不拼接
  cause 文本，杜绝 JWT/凭据经异常字符串泄漏）；
- 音频帧原样转发给注入的 sink，控制器不缓存、不保留任何原始音频
  字节——state()/事件序列化后不含帧数据。

生命周期契约：
- 一个实例恰拥有一段房间连接：start 幂等（active 期重复调用
  no-op）；stop 幂等（重复调用 no-op）并按确定顺序清理——先全部
  音频流、后房间断开；实例终止（stopped/failed）后重启是 API 误用
  （抛 VoiceSubscriptionError，不静默新建连接）；
- 任何失败（token/connect/audio_stream/轨退订 close 失败）不留活资源：先尽力关闭已
  建流、再断开房间，然后进入 failed 状态并发出带安全类别
  （token/connect/audio_stream/cleanup）与通用净化消息的事件。
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

#: 状态机：idle →（start）→ active →（stop/远端断开）→ stopped；
#: 任一步失败 → failed（终态；与 stopped 同为不可重启）。
STATUS_IDLE = "idle"
STATUS_ACTIVE = "active"
STATUS_STOPPED = "stopped"
STATUS_FAILED = "failed"
_TERMINAL = (STATUS_STOPPED, STATUS_FAILED)

#: 事件环形缓冲上限（状态投影有界；超过即滚动淘汰最旧事件）。
DEFAULT_MAX_EVENTS = 64

#: 失败类别白名单（安全类别——不含任何 cause 细节）。
CATEGORY_TOKEN = "token"
CATEGORY_CONNECT = "connect"
CATEGORY_AUDIO_STREAM = "audio_stream"
CATEGORY_CLEANUP = "cleanup"

#: 通用净化消息白名单（绝不拼接异常文本）。
_MESSAGE_TOKEN = "语音房间凭据获取失败"
_MESSAGE_CONNECT = "LiveKit 房间连接失败"
_MESSAGE_AUDIO_STREAM = "音频流创建失败"
_MESSAGE_CLEANUP = "订阅资源清理失败"
_MESSAGE_STOPPED_TERMINAL = "订阅控制器已终止，不可重启"


class VoiceSubscriptionError(RuntimeError):
    """订阅控制器 API 误用（消息为通用文案，绝不携带 token/cause 细节）。"""


@runtime_checkable
class EventSinkLike(Protocol):
    """事件/帧汇：事件与音频帧的唯一消费者（帧绝不回流控制器）。"""

    def on_event(self, event: SubscriptionEvent) -> None: ...

    def on_frame(self, frame: AudioFrameLike) -> None: ...


@runtime_checkable
class RoomLike(Protocol):
    """Room 最小协议（真实 SDK 的 async 适配由未来运行时接线提供）。"""

    def on(self, event: str, handler: Callable[..., None]) -> None: ...

    def connect(self, token: str) -> object: ...

    def disconnect(self) -> object: ...


@runtime_checkable
class AudioStreamLike(Protocol):
    """音频流最小协议：注册帧回调 + 关闭。"""

    def set_frame_handler(self, handler: Callable[[AudioFrameLike], None]) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class AudioFrameLike(Protocol):
    """音频帧最小协议：只读数据视图（转发后控制器不再持有）。"""

    @property
    def data(self) -> bytes: ...


@dataclass(frozen=True)
class SubscriptionEvent:
    """有界、净化的订阅事件（type/room/identity + 可选安全类别与短标签）。"""

    type: str
    room: str
    identity: str
    category: str | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.type,
            "room": self.room,
            "identity": self.identity,
            "category": self.category,
            "detail": self.detail,
        }


@dataclass
class _EventLog:
    """有界事件日志（环形淘汰 + 全量计数）。"""

    max_entries: int
    entries: deque[SubscriptionEvent] = field(default_factory=deque)
    total: int = 0

    def append(self, event: SubscriptionEvent) -> None:
        if len(self.entries) == self.max_entries:
            self.entries.popleft()
        self.entries.append(event)
        self.total += 1


def _track_sid(track: object, fallback_index: int) -> str:
    """音轨公开标识（LiveKit sid 非敏感）；缺失时给稳定占位。"""
    sid = getattr(track, "sid", None)
    return sid if isinstance(sid, str) and sid else f"track-{fallback_index}"


def _track_kind(track: object) -> str:
    kind = getattr(track, "kind", None)
    return kind if isinstance(kind, str) and kind else "unknown"


class ServerVoiceSubscriber:
    """单房间连接的服务端订阅控制器（subscribe-only 消费者）。

    生命周期：start() → room.connect（token 只进内存局部变量）→
    track_subscribed（仅 audio 建 AudioStream 并转发帧；非 audio 忽略）
    → stop()/远端断开 → 先关全部流、后断房间（确定顺序）。
    """

    def __init__(
        self,
        *,
        room: str,
        identity: str,
        token_provider: Callable[[], str],
        room_factory: Callable[[], RoomLike],
        audio_stream_factory: Callable[[object], AudioStreamLike],
        event_sink: EventSinkLike,
        max_events: int = DEFAULT_MAX_EVENTS,
    ) -> None:
        if not room.strip():
            raise VoiceSubscriptionError("room 不能为空")
        if not identity.strip():
            raise VoiceSubscriptionError("identity 不能为空")
        if max_events <= 0:
            raise VoiceSubscriptionError("max_events 必须为正")
        self._room_name = room
        self._identity = identity
        self._token_provider = token_provider
        self._room_factory = room_factory
        self._audio_stream_factory = audio_stream_factory
        self._event_sink = event_sink
        self._status = STATUS_IDLE
        self._room_conn: RoomLike | None = None
        self._streams: dict[str, AudioStreamLike] = {}
        self._events = _EventLog(max_entries=max_events)

    # --- 事件与状态（有界 + 净化投影） ---

    def _emit(self, event_type: str, *, category: str | None = None, detail: str = "") -> None:
        event = SubscriptionEvent(
            type=event_type,
            room=self._room_name,
            identity=self._identity,
            category=category,
            detail=detail,
        )
        self._events.append(event)
        self._event_sink.on_event(event)

    def state(self) -> dict[str, object]:
        """有界净化状态投影（无 token、无音频字节、无凭据面字段）。"""
        return {
            "status": self._status,
            "room": self._room_name,
            "identity": self._identity,
            "audio_track_count": len(self._streams),
            "audio_track_ids": sorted(self._streams),
            "event_count": self._events.total,
            "events": [event.to_dict() for event in self._events.entries],
        }

    # --- 生命周期 ---

    def start(self) -> None:
        """幂等启动：active 期重复调用 no-op；终态后重启是 API 误用。"""
        if self._status == STATUS_ACTIVE:
            return
        if self._status in _TERMINAL:
            raise VoiceSubscriptionError(_MESSAGE_STOPPED_TERMINAL)
        self._status = STATUS_ACTIVE  # 先占位：事件处理器可能同步回调
        self._emit("connecting")
        try:
            token = self._token_provider()  # 局部变量：用后即不可达
        except Exception:  # noqa: BLE001 —— 任何失败统一转净化事件（不泄 cause）
            self._fail(CATEGORY_TOKEN, _MESSAGE_TOKEN)
            return
        room = self._room_factory()
        room.on("track_subscribed", self._on_track_subscribed)
        room.on("track_unsubscribed", self._on_track_unsubscribed)
        room.on("disconnected", self._on_room_disconnected)
        try:
            room.connect(token)
        except Exception:  # noqa: BLE001
            self._room_conn = room  # 交由 _fail 统一尽力断开
            self._fail(CATEGORY_CONNECT, _MESSAGE_CONNECT)
            return
        self._room_conn = room
        self._emit("connected")

    def stop(self) -> None:
        """幂等停止：确定顺序清理——先全部音频流、后房间断开。"""
        if self._status in (STATUS_STOPPED, STATUS_IDLE):
            # idle stop：无资源可清，仅封存生命周期；stopped stop：no-op
            self._status = STATUS_STOPPED
            return
        if self._status == STATUS_FAILED:
            self._status = STATUS_STOPPED  # failed 已清理资源；封存即可
            return
        self._status = STATUS_STOPPED
        self._close_streams_ordered()
        self._disconnect_room_ordered()
        self._emit("stopped")

    # --- 音轨回调（由注入 Room 在订阅事件时调用） ---

    def _on_track_subscribed(self, track: object, *_args: object) -> None:
        if self._status != STATUS_ACTIVE:
            return  # 已停止/失败的连接不再消费新轨
        sid = _track_sid(track, len(self._streams) + 1)
        kind = _track_kind(track)
        if kind != "audio":
            # 只消费音频：视频/数据轨安全忽略（事件只记 kind 标签）
            self._emit("track_ignored", detail=f"kind={kind}")
            return
        try:
            stream = self._audio_stream_factory(track)
        except Exception:  # noqa: BLE001
            self._fail(CATEGORY_AUDIO_STREAM, _MESSAGE_AUDIO_STREAM)
            return
        stream.set_frame_handler(self._forward_frame)
        self._streams[sid] = stream
        self._emit("track_subscribed", detail=sid)

    def _on_track_unsubscribed(self, track: object, *_args: object) -> None:
        sid = _track_sid(track, -1)
        stream = self._streams.pop(sid, None)
        if stream is not None:
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                # close 失败 = 可能仍活的资源——违反「失败零活资源」契约，
                # fail-closed：清剩余流 + 断房间 + failed 终态（supervisor
                # 修正 2；通用净化消息，不泄 cause）
                self._fail(CATEGORY_CLEANUP, _MESSAGE_CLEANUP)
                return
        self._emit("track_finished", detail=sid)

    def _on_room_disconnected(self, *_args: object) -> None:
        """远端断开：流随之失效——静默清理（不重复 failed/stopped 事件，
        状态封存 stopped；房间已死，不保留其引用；后续 stop() 幂等
        no-op）。"""
        if self._status in _TERMINAL:
            return
        self._close_streams_ordered(quiet=True)
        self._room_conn = None  # 远端已断：不再持有死房间对象（supervisor 修正 3）
        self._status = STATUS_STOPPED
        self._emit("stopped", detail="room-disconnected")

    # --- 帧转发（不缓存） ---

    def _forward_frame(self, frame: AudioFrameLike) -> None:
        if self._status != STATUS_ACTIVE:
            return  # 已停连接不再转发（防御：迟到的帧直接丢弃）
        self._event_sink.on_frame(frame)  # 直转——控制器不持有帧

    # --- 清理（确定顺序） ---

    def _close_streams_ordered(self, *, quiet: bool = False) -> None:
        for sid in sorted(self._streams):
            stream = self._streams.pop(sid)
            try:
                stream.close()
            except Exception:  # noqa: BLE001 —— 清理失败继续处理其余资源
                if not quiet:
                    self._emit(
                        "failed", category=CATEGORY_CLEANUP, detail="stream close 失败"
                    )

    def _disconnect_room_ordered(self) -> None:
        room = self._room_conn
        self._room_conn = None
        if room is None:
            return
        try:
            room.disconnect()
        except Exception:  # noqa: BLE001 —— 清理失败尽力而为，不抛出
            self._emit("failed", category=CATEGORY_CLEANUP, detail="room disconnect 失败")

    def _fail(self, category: str, message: str) -> None:
        """失败路径：先清全部已获资源（流→房间），再置 failed + 净化事件。"""
        self._close_streams_ordered(quiet=True)
        self._disconnect_room_ordered()
        self._status = STATUS_FAILED
        self._emit("failed", category=category, detail=message)
