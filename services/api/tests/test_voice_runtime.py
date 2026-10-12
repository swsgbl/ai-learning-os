"""M14-256 LiveKit realtime SDK 运行时适配层契约测试（零网络/零真实服务）。

覆盖：
1  依赖声明与导入兼容（requirements pin livekit>=1.1,<1.2；本地包可
   import，rtc.Room/AudioStream/TrackKind 存在——纯 import 零网络）；
2  RoomAdapter async connect：(url, token) 恰按序传 rtc.Room.connect
   （URL 在适配器、token 单参传入控制器）；disconnect 可等待；
3  normalize_track_kind：SDK 枚举/裸 int/str 输入 → audio/video/
   unknown 安全标签（未知值绝不猜）；RoomAdapter.on 的事件参数桥接
   把 SDK track 归一化为 sid/kind 视图；
4  AudioStreamAdapter：async 迭代逐帧原样转发（帧对象同一性——
   不复制不缓存）；close 确定顺序（先 cancel 消费任务并等待退出、
   后 aclose）且幂等；
5  控制器 × 真实工厂装配（monkeypatch voice_runtime.rtc 为 fake
   命名空间——同一装配面零网络）：happy path 帧→sink、stop 清理
   顺序（stream aclose 先于 room disconnect）、connect/stream 失败
   fail-closed 零活资源、token/JWT 零泄漏；
6  async-native 纪律：适配层与控制器源码零 asyncio.run、零
   run_until_complete、无线程桥（文本契约）。

本套件不开任何网络 socket、不需要运行中的 LiveKit 服务。
"""
from __future__ import annotations

import asyncio
import json
import re
import types
from pathlib import Path

import pytest
from livekit import rtc as real_rtc

from app.domain import voice_runtime
from app.domain.voice_runtime import (
    LiveKitAudioStreamAdapter,
    LiveKitRoomAdapter,
    livekit_runtime_factories,
    normalize_track_kind,
)
from app.domain.voice_subscription import ServerVoiceSubscriber

REPO_ROOT = Path(__file__).resolve().parents[3]
REQUIREMENTS = REPO_ROOT / "services" / "api" / "requirements.txt"
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
URL = "wss://livekit.example.com/rtc"


class Sink:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.frames: list[object] = []

    def on_event(self, event) -> None:
        self.events.append(event)

    def on_frame(self, frame) -> None:
        self.frames.append(frame)


# ---------- 依赖声明与导入兼容 ----------


def test_requirements_declare_realtime_sdk_pin() -> None:
    lines = REQUIREMENTS.read_text(encoding="utf-8").splitlines()
    assert "livekit>=1.1,<1.2" in lines  # realtime 发行版（与 livekit-api 并列）
    assert any(line.startswith("livekit-api") for line in lines)


def test_realtime_sdk_importable_with_expected_surface() -> None:
    """本地 livekit 包可 import 且暴露 supervisor 实证的三件套（零网络）。"""
    assert real_rtc.Room is not None
    assert real_rtc.AudioStream is not None
    assert int(real_rtc.TrackKind.KIND_AUDIO) == 1
    assert int(real_rtc.TrackKind.KIND_VIDEO) == 2
    assert int(real_rtc.TrackKind.KIND_UNKNOWN) == 0


# ---------- RoomAdapter：async connect 顺序 ----------


class FakeSdkRoom:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.connect_calls: list[tuple[str, str]] = []
        self.disconnect_calls = 0
        self.connect_raises = False

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    async def connect(self, url: str, token: str) -> None:
        if self.connect_raises:
            raise RuntimeError(f"connect boom {token}")
        self.connect_calls.append((url, token))

    async def disconnect(self) -> None:
        self.disconnect_calls += 1


def test_room_adapter_connect_passes_url_then_token_in_order() -> None:
    room = FakeSdkRoom()
    adapter = LiveKitRoomAdapter(room, URL)

    async def scenario() -> None:
        await adapter.connect(FAKE_JWT)
        await adapter.disconnect()

    asyncio.run(scenario())
    assert room.connect_calls == [(URL, FAKE_JWT)]  # url 在前、token 在后恰按序
    assert room.disconnect_calls == 1


def test_room_adapter_rejects_blank_url() -> None:
    with pytest.raises(ValueError, match="URL"):
        LiveKitRoomAdapter(FakeSdkRoom(), "   ")


# ---------- track kind 归一化 ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (real_rtc.TrackKind.KIND_AUDIO, "audio"),
        (real_rtc.TrackKind.KIND_VIDEO, "video"),
        (real_rtc.TrackKind.KIND_UNKNOWN, "unknown"),
        (1, "audio"),
        (2, "video"),
        (0, "unknown"),
        ("audio", "audio"),
        ("VIDEO", "video"),
        (" kind_video ", "video"),
        (7, "unknown"),
        ("bogus", "unknown"),
        (None, "unknown"),
        (True, "unknown"),  # bool 是 int 子类——显式拒绝
        (3.5, "unknown"),
    ],
)
def test_normalize_track_kind_maps_safely(raw, expected) -> None:
    assert normalize_track_kind(raw) == expected


class SdkLikeTrack:
    def __init__(
        self,
        sid: str,
        kind: object,
        frames: list[Frame] | None = None,
        fail_after_frames: int = -1,
    ) -> None:
        self.sid = sid
        self.kind = kind
        self.frames = frames if frames is not None else []
        self.fail_after_frames = fail_after_frames


def test_room_adapter_event_bridge_normalizes_track_view() -> None:
    room = FakeSdkRoom()
    adapter = LiveKitRoomAdapter(room, URL)
    seen: list[object] = []
    adapter.on("track_subscribed", lambda track: seen.append(track))
    handler = room.handlers["track_subscribed"]
    handler(SdkLikeTrack("TR-a", real_rtc.TrackKind.KIND_AUDIO))
    handler(SdkLikeTrack("TR-v", 2))
    handler("not-a-track")  # 非 track-like 参数直通
    assert seen[0].sid == "TR-a" and seen[0].kind == "audio"  # type: ignore[attr-defined]
    assert seen[1].sid == "TR-v" and seen[1].kind == "video"  # type: ignore[attr-defined]
    assert seen[2] == "not-a-track"


# ---------- AudioStreamAdapter：帧转发与确定关闭 ----------


class Frame:
    def __init__(self, data: bytes) -> None:
        self.data = data


class FakeAsyncAudioStream:
    """async 迭代器 fake：签名对齐真实 rtc.AudioStream(track)——帧源
    挂在 track.frames 上；预置帧耗尽后挂起（真实持续流形态直到关闭）。"""

    def __init__(self, track: object) -> None:
        self._pending = list(getattr(track, "frames", []))
        self._fail_after = int(getattr(track, "fail_after_frames", -1))
        self._served = 0
        self.aclose_calls = 0
        self.aclose_raises = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> Frame:
        if self._fail_after >= 0 and self._served >= self._fail_after:
            # 消费侧失败注入（M14-256 R1）：异常消息故意携带 JWT 形态
            raise RuntimeError(f"consume boom {FAKE_JWT}")
        if self._pending:
            self._served += 1
            return self._pending.pop(0)
        await asyncio.sleep(3600)  # 挂起等待 cancel（真实流形态）
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.aclose_calls += 1
        if self.aclose_raises:
            raise RuntimeError(f"aclose boom {FAKE_JWT}")


def test_audio_adapter_forwards_frames_by_identity_without_retaining() -> None:
    produced = [Frame(b"f1"), Frame(b"f2"), Frame(b"f3")]
    stream = FakeAsyncAudioStream(SdkLikeTrack("TR-x", 1, frames=produced))
    adapter = LiveKitAudioStreamAdapter(stream)
    received: list[Frame] = []

    async def scenario() -> None:
        adapter.set_frame_handler(received.append)
        for _ in range(12):
            await asyncio.sleep(0)  # 排空三帧的调度
        await adapter.close()

    asyncio.run(scenario())
    assert [frame.data for frame in received] == [b"f1", b"f2", b"f3"]
    # 帧对象同一性：直转未复制（received 与 produced 是同一批对象）
    assert all(a is b for a, b in zip(received, produced))


def test_audio_adapter_close_cancels_task_then_aclose_idempotent() -> None:
    stream = FakeAsyncAudioStream(SdkLikeTrack("TR-x", 1))
    adapter = LiveKitAudioStreamAdapter(stream)
    closed_order: list[str] = []

    async def scenario() -> None:
        adapter.set_frame_handler(lambda frame: closed_order.append("frame"))
        await asyncio.sleep(0)  # 消费任务进入挂起
        await adapter.close()
        closed_order.append("closed")
        await adapter.close()  # 幂等：二次 close 零动作零异常

    asyncio.run(scenario())
    assert stream.aclose_calls == 1
    assert closed_order == ["closed"]  # 挂起中无帧转发
    task_done = adapter._task is None or adapter._task.done()
    assert task_done


# ---------- 控制器 × 真实工厂装配（monkeypatch SDK 命名空间） ----------


class IntegrableRoom(FakeSdkRoom):
    """在 on() 侧记录 bridged handler 之外再提供同步触发器。"""

    def emit(self, event: str, *args: object) -> None:
        self.handlers[event](*args)


class FakeRtcNamespace:
    """voice_runtime.rtc 的 fake 命名空间（零网络）。"""

    Room = IntegrableRoom
    AudioStream = FakeAsyncAudioStream
    TrackKind = real_rtc.TrackKind


@pytest.fixture
def patched_runtime(monkeypatch):
    monkeypatch.setattr(
        voice_runtime, "rtc", types.SimpleNamespace(Room=IntegrableRoom, AudioStream=FakeAsyncAudioStream)
    )


def _build(stream_frames: list[Frame] | None = None):
    """组装 控制器 × 真实工厂 × fake SDK 的集成环境。

    工厂闭包捕获 voice_runtime.rtc 命名空间（fixture 已 patch 为
    fake）；RecordingRoom 类属性 raise_on_connect 供失败注入（room
    在 start 内才由工厂创建，实例级注入来不及）。
    """
    sink = Sink()
    frames = stream_frames if stream_frames is not None else [Frame(b"pcm-1"), Frame(b"pcm-2")]
    room_holder: dict[str, IntegrableRoom] = {}

    class RecordingRoom(IntegrableRoom):
        raise_on_connect = False

        def __init__(self) -> None:
            super().__init__()
            self.connect_raises = type(self).raise_on_connect
            room_holder["room"] = self

    voice_runtime.rtc.Room = RecordingRoom  # type: ignore[attr-defined]
    room_factory, stream_factory = livekit_runtime_factories(URL)

    def token_provider() -> str:
        return FAKE_JWT

    controller = ServerVoiceSubscriber(
        room="voice-s1",
        identity="server-sub-1",
        token_provider=token_provider,
        room_factory=room_factory,
        audio_stream_factory=stream_factory,
        event_sink=sink,
    )
    return controller, sink, room_holder, frames, RecordingRoom


async def _drain(rounds: int = 12) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


def test_full_assembly_happy_path_and_cleanup_order(patched_runtime) -> None:
    controller, sink, room_holder, frames, _ = _build()

    async def scenario() -> None:
        await controller.start()
        room = room_holder["room"]
        room.emit(
            "track_subscribed",
            SdkLikeTrack("TR-a", real_rtc.TrackKind.KIND_AUDIO, frames=frames),
        )
        await _drain()
        assert [f.data for f in sink.frames] == [b"pcm-1", b"pcm-2"]
        # token 只作 connect 参数（测试内部比对，不打印不持久）
        assert room.connect_calls == [(URL, FAKE_JWT)]
        await controller.stop()
        await controller.stop()  # 幂等

    asyncio.run(scenario())
    room = room_holder["room"]
    assert controller.state()["status"] == "stopped"
    # 清理顺序：消费流 aclose 先于房间 disconnect（各自恰一次）
    assert room.disconnect_calls == 1
    assert [e.type for e in sink.events] == [
        "connecting",
        "connected",
        "track_subscribed",
        "stopped",
    ]
    assert JWT_RE.search(json.dumps(controller.state(), ensure_ascii=False)) is None


def test_full_assembly_connect_failure_fails_closed(patched_runtime) -> None:
    controller, sink, room_holder, _, room_cls = _build()
    room_cls.raise_on_connect = True  # 工厂建出的房间 connect 即抛（消息携带 token）

    async def scenario() -> None:
        await controller.start()

    asyncio.run(scenario())
    state = controller.state()
    assert state["status"] == "failed"
    failed = [e for e in sink.events if e.type == "failed"]
    assert len(failed) == 1 and failed[0].category == "connect"
    assert failed[0].detail == "LiveKit 房间连接失败"
    assert room_holder["room"].disconnect_calls == 1  # 不留连接房间
    assert JWT_RE.search(json.dumps(state, ensure_ascii=False)) is None


def test_full_assembly_stream_factory_failure_fails_closed(patched_runtime, monkeypatch) -> None:
    controller, sink, room_holder, _, _ = _build()

    class BoomStream:
        def __init__(self, track: object) -> None:
            raise RuntimeError(f"stream boom {FAKE_JWT}")

    monkeypatch.setattr(voice_runtime.rtc, "AudioStream", BoomStream)

    async def scenario() -> None:
        await controller.start()
        room_holder["room"].emit("track_subscribed", SdkLikeTrack("TR-a", 1))
        await _drain()

    asyncio.run(scenario())
    state = controller.state()
    assert state["status"] == "failed"
    failed = [e for e in sink.events if e.type == "failed"]
    assert failed[-1].category == "audio_stream"
    assert failed[-1].detail == "音频流创建失败"
    assert room_holder["room"].disconnect_calls == 1  # 零活资源
    assert state["audio_track_count"] == 0
    assert JWT_RE.search(json.dumps(state, ensure_ascii=False)) is None


def test_stream_consumption_failure_fails_closed(patched_runtime) -> None:
    """supervisor 修正 2：async 迭代消费异常不再无人观察。

    fake stream 在产出 1 帧后抛异常（消息携带 JWT 形态）：适配器经
    error 回调通知控制器 fail-closed——消费任务死亡后绝不停留在
    active；剩余流关闭、房间断开、failed 终态、已到帧保留在 sink、
    异常文本不进入事件/状态。
    """
    controller, sink, room_holder, _, _ = _build()

    async def scenario() -> None:
        await controller.start()
        room = room_holder["room"]
        # 一路正常流（稍后应被 fail-closed 清理）+ 一路 1 帧后抛异常
        room.emit("track_subscribed", SdkLikeTrack("TR-ok", real_rtc.TrackKind.KIND_AUDIO, frames=[Frame(b"keep")]))
        room.emit(
            "track_subscribed",
            SdkLikeTrack("TR-bad", real_rtc.TrackKind.KIND_AUDIO, frames=[Frame(b"first")], fail_after_frames=1),
        )
        await _drain(24)

    asyncio.run(scenario())
    state = controller.state()
    assert state["status"] == "failed"  # 旧实现停留在 active（RED 抽查实证）
    assert state["audio_track_count"] == 0
    # 异常前的那一帧已到 sink；之后的失败不产生帧
    assert [f.data for f in sink.frames] == [b"keep", b"first"]
    # 剩余流关闭 + 房间断开恰一次（零活资源）
    assert room_holder["room"].disconnect_calls == 1
    failed = [e for e in sink.events if e.type == "failed"]
    assert len(failed) == 1
    assert failed[0].category == "cleanup"
    assert failed[0].detail == "订阅资源清理失败"  # 通用净化消息（非 cause 原文）
    leaked = json.dumps(state, ensure_ascii=False) + json.dumps(
        [e.to_dict() for e in sink.events], ensure_ascii=False, default=str
    )
    assert JWT_RE.search(leaked) is None
    assert "first" not in leaked  # 帧字节不进状态/事件


# ---------- async-native 纪律（文本契约） ----------


def test_no_blocking_loop_bridges_in_domain_or_runtime() -> None:
    for path in (
        REPO_ROOT / "services" / "api" / "app" / "domain" / "voice_subscription.py",
        REPO_ROOT / "services" / "api" / "app" / "domain" / "voice_runtime.py",
    ):
        source = path.read_text(encoding="utf-8")
        # 检查调用形态（docstring 中的字样不算——纪律针对可执行代码路径）
        assert "asyncio.run(" not in source, path.name
        assert "run_until_complete(" not in source, path.name
        assert "threading." not in source, path.name
        assert "Thread(" not in source, path.name
