"""M14-256：LiveKit Python realtime SDK 的 async-native 适配层（fake-only 验证）。

把 `livekit` realtime 发行版（PyPI 包 `livekit`，依赖 pin
`livekit>=1.1,<1.2`；与 REST 签发用的 `livekit-api` 是两个包）的真实
对象适配为 voice_subscription.py 的控制器协议：

- **Room 适配**（LiveKitRoomAdapter）：LiveKit URL 保存在适配器里
  （配置注入），控制器仍然只收一个 token——`await adapter.connect(
  token)` 把 (url, token) 按序传给 `rtc.Room.connect`；`on()` 注册的
  handler 经参数桥接：带 kind 的 track-like 参数被替换为归一化视图
  （sid 直通 + kind 标签），控制器零 SDK 知识。
- **AudioStream 适配**（LiveKitAudioStreamAdapter）：`rtc.AudioStream`
  是 async 迭代器——`set_frame_handler` 启动消费任务，`async for` 每
  帧原样转发注入的 sink（帧对象直转，不复制、不保留字节）；
  `await close()` 按确定顺序先 cancel 消费任务并等待其退出、再
  `await stream.aclose()`（幂等）。
- **track kind 归一化**（normalize_track_kind）：SDK 枚举
  `rtc.TrackKind`（KIND_UNKNOWN=0/KIND_AUDIO=1/KIND_VIDEO=2，supervisor
  实证）与裸 int/str 输入统一映射为控制器既有标签
  "audio"/"video"/"unknown"——未知值安全落到 unknown，绝不猜。

**边界（非声明）**：本模块零 FastAPI startup/lifespan 接线、零 HTTP
端点、零真实网络——测试用 fake async Room/AudioStream 全覆盖；不做
ASR/VAD/barge-in/pause-resume；真实 LiveKit 服务运行留给 supervisor
后续受控切片。
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from livekit import rtc

#: 控制器既有 kind 标签（voice_subscription._track_kind 的取值域）。
TRACK_KIND_AUDIO = "audio"
TRACK_KIND_VIDEO = "video"
TRACK_KIND_UNKNOWN = "unknown"

#: supervisor 实证的 SDK 枚举值（rtc.TrackKind.KIND_*）——按值映射，
#: 兼容枚举成员 / 裸 int / 已归一化 str 输入。
_KIND_VALUE_TO_LABEL = {
    int(rtc.TrackKind.KIND_AUDIO): TRACK_KIND_AUDIO,
    int(rtc.TrackKind.KIND_VIDEO): TRACK_KIND_VIDEO,
    int(rtc.TrackKind.KIND_UNKNOWN): TRACK_KIND_UNKNOWN,
}
_KIND_STR_TO_LABEL = {
    TRACK_KIND_AUDIO: TRACK_KIND_AUDIO,
    TRACK_KIND_VIDEO: TRACK_KIND_VIDEO,
    TRACK_KIND_UNKNOWN: TRACK_KIND_UNKNOWN,
    # 宽容同义词（仅供既有 fake/文档形态；SDK 枚举路径不走这里）
    "kind_audio": TRACK_KIND_AUDIO,
    "kind_video": TRACK_KIND_VIDEO,
}


def normalize_track_kind(kind: Any) -> str:
    """SDK track kind → 控制器安全标签（audio/video/unknown）。

    接受 rtc.TrackKind 枚举成员、裸 int（0/1/2）、已归一化 str；
    任何未知值（越界 int/陌生字符串/None）安全落到 "unknown"——
    绝不猜、绝不抛（归一化是消费前置过滤器）。
    """
    value = getattr(kind, "value", kind)  # 枚举成员 → 裸值
    if isinstance(value, bool):  # bool 是 int 子类：显式拒绝到 unknown
        return TRACK_KIND_UNKNOWN
    if isinstance(value, int):
        return _KIND_VALUE_TO_LABEL.get(value, TRACK_KIND_UNKNOWN)
    if isinstance(value, str):
        return _KIND_STR_TO_LABEL.get(value.strip().lower(), TRACK_KIND_UNKNOWN)
    return TRACK_KIND_UNKNOWN


class _NormalizedTrackView:
    """track 参数的归一化视图：kind 归一化，其余属性直通原始 track。

    只覆写 kind（无论底层是 SDK 枚举、裸 int 还是字符串，控制器看到
    恒定标签）；sid/frames/SDK 内部媒体句柄等一切其余属性经
    __getattr__ 代理到原始对象——下游工厂（如 rtc.AudioStream(track)）
    拿到的仍是可用的真实 track 能力面。
    """

    def __init__(self, track: Any) -> None:
        self._track = track

    @property
    def kind(self) -> str:
        return normalize_track_kind(getattr(self._track, "kind", None))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._track, name)


def _adapt_event_arg(arg: Any) -> Any:
    """事件参数桥接：track-like（带 kind 属性）→ 归一化视图，其余直通。"""
    if hasattr(arg, "kind"):
        return _NormalizedTrackView(arg)
    return arg


class LiveKitRoomAdapter:
    """rtc.Room → 控制器 RoomLike 协议（URL 留在适配器，token 单参传入）。

    结构上满足 voice_subscription.RoomLike（on/connect/disconnect）；
    鸭子协议实现——控制器不做 isinstance 检查。
    """

    def __init__(self, room: rtc.Room, url: str) -> None:
        if not url.strip():
            raise ValueError("LiveKit URL 不能为空")
        self._room = room
        self._url = url

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        """注册事件：参数经桥接（track kind 归一化）后转交 handler。"""

        def bridged(*args: Any) -> None:
            handler(*(_adapt_event_arg(arg) for arg in args))

        self._room.on(event, bridged)

    async def connect(self, token: str) -> None:
        """await rtc.Room.connect(url, token)——URL 来自适配器配置。"""
        await self._room.connect(self._url, token)

    async def disconnect(self) -> None:
        await self._room.disconnect()


class LiveKitAudioStreamAdapter:
    """rtc.AudioStream（async 迭代器）→ 帧回调协议 + 异步确定关闭。

    结构上满足 voice_subscription.AudioStreamLike（set_frame_handler/
    set_error_handler/await close）。

    set_frame_handler 在当前事件循环上启动消费任务：`async for` 每帧
    **原样**转发 handler（帧对象直转——适配器不复制、不缓存字节）；
    set_error_handler 注册消费侧意外失败通知（M14-256 R1：async 迭代
    抛非取消异常时回调——由控制器 fail-closed，消费任务死亡后绝不停
    留 active；异常文本不传入回调，杜绝 cause 泄漏）；close 先 cancel
    消费任务并等待其真正退出、再 await aclose——顺序保证不再有帧在
    关闭后到达（幂等：二次 close 零动作）。
    """

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._handler: Callable[[Any], None] | None = None
        self._error_handler: Callable[[], None] | None = None
        self._task: asyncio.Future[object] | None = None

    def set_frame_handler(self, handler: Callable[[Any], None]) -> None:
        self._handler = handler
        if self._task is None:
            self._task = asyncio.ensure_future(self._consume())

    def set_error_handler(self, handler: Callable[[], None]) -> None:
        self._error_handler = handler

    async def _consume(self) -> None:
        try:
            async for frame in self._stream:
                if self._handler is not None:
                    self._handler(frame)  # 直转——适配器不持有帧
        except asyncio.CancelledError:
            raise  # close() 的正常取消路径——不是错误
        except Exception:  # noqa: BLE001
            # 消费侧意外失败：通知控制器 fail-closed（回调不带异常文本）
            if self._error_handler is not None:
                self._error_handler()

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)  # 等待真正退出
        if self._stream is not None:
            stream, self._stream = self._stream, None
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                await aclose()


def livekit_runtime_factories(
    url: str,
) -> tuple[Callable[[], Any], Callable[[Any], Any]]:
    """构造真实 SDK 的工厂对（room_factory / audio_stream_factory）。

    供 ServerVoiceSubscriber 注入：URL 只进 room 工厂闭包（配置面），
    控制器 connect 仍只收 token。**测试用 monkeypatch 替换 rtc.Room/
    rtc.AudioStream 后即可零网络覆盖同一装配面。**
    """

    def room_factory() -> Any:
        return LiveKitRoomAdapter(rtc.Room(), url)

    def audio_stream_factory(track: Any) -> Any:
        return LiveKitAudioStreamAdapter(rtc.AudioStream(track))

    return room_factory, audio_stream_factory


__all__ = [
    "TRACK_KIND_AUDIO",
    "TRACK_KIND_UNKNOWN",
    "TRACK_KIND_VIDEO",
    "LiveKitAudioStreamAdapter",
    "LiveKitRoomAdapter",
    "livekit_runtime_factories",
    "normalize_track_kind",
]
