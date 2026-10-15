"""M14-272a 服务端语音管线编排契约测试（fake-only，零真实连接）。

把 M14-268（订阅生命周期）/M14-269（帧通道+分流汇）/M14-270
（ASR 消费链）/M14-271（会话控制）四件真实积木组装成一台
「服务端语音会话管线」，并扩展受控工厂支持**每订阅者独立
event_sink 注入**（默认行为零回归）。全部真实积木 + 最内层
fake 协作者（token/房间/流/ASR adapter）——零网络零真实连接。

覆盖：
1  工厂 event_sink_factory 注入：给定则 create 用注入 sink（帧
   到达注入 sink）；缺省行为零回归（内部 BoundedVoiceFrameSink，
   既有 sink_snapshot 语义不变）；注入 sink 无 snapshot 面时
   handle.sink_snapshot 确定性拒绝；
2  open_session：订阅者经 manager 在册（订阅只能由 manager
   管理）、split 生效（emit 帧同时计量+入界）、句柄与会话键
   对应；
3  pump：通道帧 → adapter → transcript（顺序逐字一致）；
4  控制：handle.submit / submit_vad 驱动 FSM（barge-in 播报期
   迁移、VAD speech_start 同义、pause 幂等）；
5  幂等 open：同 session 重复 open 返回同一句柄（manager 条目
   恰一、工厂 create 恰一次）；
6  close：停帧源（订阅者 release/房间断开）→ 残余 pump → ASR
   finish 冲刷 → 管线终结（后续 pump/submit 拒绝、close 幂等、
   manager 零条目）；
7  会话隔离：两会话各自通道/transcript/控制器互不串扰；
8  ASR 失败恢复传播：adapter feed 抛错只计 failed、管线不崩、
   后续恢复；
9  构造校验：缺失协作者确定性拒绝；
10 源码卫生（AST+文本）：域内导入白名单；无网络/文件/线程/
   asyncio 调度/LiveKit SDK/HTTP client。
"""
from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from app.domain.voice_frame_channel import (
    BoundedVoiceFrameChannel,
    SplitVoiceSink,
)
from app.domain.voice_frame_sink import BoundedVoiceFrameSink
from app.domain.voice_pipeline import (
    ServerVoicePipeline,
    ServerVoiceSessionHandle,
    VoicePipelineError,
)
from app.domain.voice_runtime_manager import VoiceRuntimeManager
from app.domain.voice_session_control import VAD_SPEECH_START
from app.domain.voice_session_fsm import READING_QUESTION, WAITING_ANSWER
from app.domain.voice_subscriber_factory import (
    ControlledVoiceSubscriberFactory,
    VoiceSubscriberFactoryError,
)

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")

REPO_ROOT = Path(__file__).resolve().parents[3]
PIPELINE_PATH = (
    REPO_ROOT / "services" / "api" / "app" / "domain" / "voice_pipeline.py"
)
FACTORY_PATH = (
    REPO_ROOT / "services" / "api" / "app" / "domain" / "voice_subscriber_factory.py"
)
PIPELINE_SOURCE = PIPELINE_PATH.read_text(encoding="utf-8")

SESSION_A = "sess-alpha-0001"
SESSION_B = "sess-beta-0002"
ROOM = "exam-room-alpha"


# ---------- fake 协作者（最内层） ----------


class FakeFrame:
    def __init__(self, data: object) -> None:
        self._data = data

    @property
    def data(self) -> object:
        return self._data


class FakeTrack:
    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.kind = "audio"


class FakeAudioStream:
    def __init__(self) -> None:
        self.handler: Any = None
        self.error_handler: Any = None

    def set_frame_handler(self, handler: Any) -> None:
        self.handler = handler

    def set_error_handler(self, handler: Any) -> None:
        self.error_handler = handler

    async def close(self) -> None: ...


class FakeRoom:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self.disconnect_calls = 0

    def on(self, name: str, handler: Any) -> None:
        self.handlers[name] = handler

    async def connect(self, token: str) -> None: ...

    async def disconnect(self) -> None:
        self.disconnect_calls += 1


class RecordingAdapter:
    """记录 chunk + 脚本化产出段的 fake ASR adapter。"""

    def __init__(self, *, fail_indexes: set[int] | None = None) -> None:
        self.fed: list[bytes] = []
        self.finish_calls = 0
        self.fail_indexes = fail_indexes or set()

    def feed(self, chunk: bytes) -> list[Any]:
        index = len(self.fed)
        self.fed.append(chunk)
        if index in self.fail_indexes:
            raise RuntimeError("asr exploded secret")
        return [type("Seg", (), {"text": f"w{index}", "is_final": True})()]

    def finish(self) -> list[Any]:
        self.finish_calls += 1
        return [type("Seg", (), {"text": "tail", "is_final": True})()]


class Collaborators:
    """每管线一套房间/流记录器 + ASR adapter 工厂。"""

    def __init__(self) -> None:
        self.rooms: list[FakeRoom] = []
        self.streams: list[FakeAudioStream] = []
        self.adapters: list[RecordingAdapter] = []
        self.create_calls = 0

    def make_factory(self, sink: Any) -> ControlledVoiceSubscriberFactory:
        outer = self

        class _CountingFactory:
            def create(self, room: object, identity: object) -> object:
                outer.create_calls += 1
                return ControlledVoiceSubscriberFactory(
                    token_provider_factory=lambda r, i: FAKE_JWT,
                    room_factory=lambda: outer._room(),
                    audio_stream_factory=lambda t: outer._stream(),
                    event_sink_factory=lambda: sink,
                ).create(room, identity)

        return _CountingFactory()  # type: ignore[return-value]

    def _room(self) -> FakeRoom:
        room = FakeRoom()
        self.rooms.append(room)
        return room

    def _stream(self) -> FakeAudioStream:
        stream = FakeAudioStream()
        self.streams.append(stream)
        return stream

    def adapter_factory(self) -> RecordingAdapter:
        adapter = RecordingAdapter()
        self.adapters.append(adapter)
        return adapter


def build_pipeline(collabs: Collaborators) -> tuple[ServerVoicePipeline, VoiceRuntimeManager]:
    manager = VoiceRuntimeManager()
    pipeline = ServerVoicePipeline(
        manager=manager,
        factory_builder=collabs.make_factory,
        asr_adapter_factory=collabs.adapter_factory,
    )
    return pipeline, manager


async def emit_frames(
    handle: ServerVoiceSessionHandle,
    collabs: Collaborators,
    payloads: list[bytes],
    *,
    room_index: int = -1,
) -> None:
    """经订阅者真实路径 emit 帧（fake 流 handler → split → 通道）。

    先触发 track_subscribed（订阅者只在音轨到达后才创建音频流）；
    room_index 指定会话房间（默认最新——单会话场景）。
    """
    room = collabs.rooms[room_index]
    room.handlers["track_subscribed"](FakeTrack(f"TR-{len(collabs.streams)}"))
    await asyncio.sleep(0)
    stream = collabs.streams[-1]
    for payload in payloads:
        stream.handler(FakeFrame(payload))
    await asyncio.sleep(0)


# ---------- 9：构造校验 ----------


def test_constructor_rejects_missing_collaborators() -> None:
    manager = VoiceRuntimeManager()
    collabs = Collaborators()
    with pytest.raises(VoicePipelineError):
        ServerVoicePipeline(
            manager=None,  # type: ignore[arg-type]
            factory_builder=collabs.make_factory,
            asr_adapter_factory=collabs.adapter_factory,
        )
    with pytest.raises(VoicePipelineError):
        ServerVoicePipeline(
            manager=manager,
            factory_builder=None,  # type: ignore[arg-type]
            asr_adapter_factory=collabs.adapter_factory,
        )
    with pytest.raises(VoicePipelineError):
        ServerVoicePipeline(
            manager=manager,
            factory_builder=collabs.make_factory,
            asr_adapter_factory=None,  # type: ignore[arg-type]
        )


# ---------- 1：工厂 event_sink_factory 注入 ----------


def test_factory_default_sink_behavior_unchanged() -> None:
    """缺省：内部 BoundedVoiceFrameSink（既有 sink_snapshot 语义不变）。"""
    factory = ControlledVoiceSubscriberFactory(
        token_provider_factory=lambda r, i: FAKE_JWT,
        room_factory=lambda: FakeRoom(),
        audio_stream_factory=lambda t: FakeAudioStream(),
    )
    handle = factory.create(ROOM, "server-sub-1")

    async def scenario() -> None:
        await handle.start()
        await handle.stop()

    asyncio.run(scenario())
    snap = handle.sink_snapshot()
    assert "frame_count" in snap and "event_count" in snap  # 计量语义原样


def test_factory_injected_sink_receives_frames() -> None:
    """注入 event_sink_factory：帧到达注入 sink（split 双路径）。"""
    metrics = BoundedVoiceFrameSink()
    channel = BoundedVoiceFrameChannel(max_frames=8, max_bytes=4096)
    split = SplitVoiceSink(metrics_sink=metrics, frame_channel=channel)
    rooms: list[FakeRoom] = []
    streams: list[FakeAudioStream] = []

    async def scenario() -> None:
        factory = ControlledVoiceSubscriberFactory(
            token_provider_factory=lambda r, i: FAKE_JWT,
            room_factory=lambda: _new_room(),
            audio_stream_factory=lambda t: _new_stream(),
            event_sink_factory=lambda: split,
        )
        handle = factory.create(ROOM, "server-sub-2")
        await handle.start()
        rooms[0].handlers["track_subscribed"](FakeTrack("TR-1"))
        await asyncio.sleep(0)
        streams[0].handler(FakeFrame(b"AAAA"))
        await handle.stop()

    def _new_room() -> FakeRoom:
        room = FakeRoom()
        rooms.append(room)
        return room

    def _new_stream() -> FakeAudioStream:
        stream = FakeAudioStream()
        streams.append(stream)
        return stream

    asyncio.run(scenario())
    assert channel.snapshot()["received_frame_count"] == 1  # 入界
    assert metrics.snapshot()["frame_count"] == 1  # 计量同步


def test_factory_injected_sink_without_snapshot_rejected_on_query() -> None:
    class SinkNoSnapshot:
        def on_event(self, event: Any) -> None: ...

        def on_frame(self, frame: Any) -> None: ...

    factory = ControlledVoiceSubscriberFactory(
        token_provider_factory=lambda r, i: FAKE_JWT,
        room_factory=lambda: FakeRoom(),
        audio_stream_factory=lambda t: FakeAudioStream(),
        event_sink_factory=lambda: SinkNoSnapshot(),
    )
    handle = factory.create(ROOM, "server-sub-3")
    with pytest.raises(VoiceSubscriberFactoryError):
        handle.sink_snapshot()


def test_factory_rejects_non_callable_event_sink_factory() -> None:
    with pytest.raises(VoiceSubscriberFactoryError):
        ControlledVoiceSubscriberFactory(
            token_provider_factory=lambda r, i: FAKE_JWT,
            room_factory=lambda: FakeRoom(),
            audio_stream_factory=lambda t: FakeAudioStream(),
            event_sink_factory="not-callable",  # type: ignore[arg-type]
        )


def test_factory_rejects_invalid_injected_sink_product() -> None:
    """注入工厂产物必须满足 on_event/on_frame 结构面（构造期确定性拒绝）。"""
    factory = ControlledVoiceSubscriberFactory(
        token_provider_factory=lambda r, i: FAKE_JWT,
        room_factory=lambda: FakeRoom(),
        audio_stream_factory=lambda t: FakeAudioStream(),
        event_sink_factory=lambda: object(),  # 无结构面
    )
    with pytest.raises(VoiceSubscriberFactoryError):
        factory.create(ROOM, "server-sub-4")


# ---------- 2/3/4：管线 open → pump → 控制 ----------


def test_open_session_registers_via_manager_and_pump_transcribes() -> None:
    collabs = Collaborators()
    pipeline, manager = build_pipeline(collabs)

    async def scenario() -> ServerVoiceSessionHandle:
        handle = await pipeline.open_session(SESSION_A, room=ROOM, identity="student-a1")
        await asyncio.sleep(0)
        assert manager.snapshot()["subscriber_count"] == 1
        assert manager.snapshot()["keys"] == ["voice-session-" + SESSION_A]
        await emit_frames(handle, collabs, [b"AA", b"BB", b"CC"])
        return handle

    handle = asyncio.run(scenario())
    result = handle.pump(10)
    assert result.delivered_chunk_count == 3
    assert result.new_segment_count == 3
    transcript = handle.transcript()
    assert [seg["text"] for seg in transcript["segments"]] == ["w0", "w1", "w2"]
    assert collabs.adapters[0].fed == [b"AA", b"BB", b"CC"]  # 顺序逐字一致
    asyncio.run(pipeline.close_session(SESSION_A))


def test_handle_control_submit_and_vad() -> None:
    collabs = Collaborators()
    pipeline, _manager = build_pipeline(collabs)

    async def scenario() -> ServerVoiceSessionHandle:
        handle = await pipeline.open_session(SESSION_A, room=ROOM, identity="student-a2")
        await asyncio.sleep(0)
        return handle

    handle = asyncio.run(scenario())
    assert handle.control_snapshot()["state"] == "SESSION_READY"
    handle.submit("start_reading")
    assert handle.control_snapshot()["state"] == READING_QUESTION
    handle.submit_vad(VAD_SPEECH_START)  # 播报期 → barge-in
    assert handle.control_snapshot()["state"] == WAITING_ANSWER
    handle.submit("pause")
    handle.submit("pause")  # 幂等
    assert handle.control_snapshot()["state"] == WAITING_ANSWER
    asyncio.run(pipeline.close_session(SESSION_A))


# ---------- 5：幂等 open ----------


def test_open_same_session_idempotent() -> None:
    collabs = Collaborators()
    pipeline, manager = build_pipeline(collabs)

    async def scenario() -> None:
        first = await pipeline.open_session(SESSION_A, room=ROOM, identity="student-a3")
        await asyncio.sleep(0)
        second = await pipeline.open_session(SESSION_A, room=ROOM, identity="student-a3")
        assert first is second  # 同一句柄
        assert manager.snapshot()["subscriber_count"] == 1
        assert collabs.create_calls == 1  # 工厂 create 恰一次
        await pipeline.close_session(SESSION_A)

    asyncio.run(scenario())


# ---------- 6：close ----------


def test_close_releases_pumps_tail_and_terminates() -> None:
    collabs = Collaborators()
    pipeline, _manager = build_pipeline(collabs)

    async def scenario() -> None:
        handle = await pipeline.open_session(SESSION_A, room=ROOM, identity="student-a4")
        await asyncio.sleep(0)
        await emit_frames(handle, collabs, [b"XX", b"YY"])
        await pipeline.close_session(SESSION_A)

    asyncio.run(scenario())
    assert _manager.snapshot()["subscriber_count"] == 0  # 释放
    assert collabs.rooms[0].disconnect_calls == 1  # 帧源已停
    adapter = collabs.adapters[0]
    assert adapter.finish_calls == 1  # ASR 冲刷
    # 残余帧经 close 内部 pump 进入 transcript（含尾段）
    # close 后操作拒绝 + close 幂等
    with pytest.raises(VoicePipelineError):
        pipeline.pump_session(SESSION_A, 5)
    with pytest.raises(VoicePipelineError):
        pipeline.submit_session(SESSION_A, "pause")
    asyncio.run(pipeline.close_session(SESSION_A))  # 幂等 no-op
    assert adapter.finish_calls == 1


def test_close_flushes_residual_frames_into_transcript() -> None:
    collabs = Collaborators()
    pipeline, _manager = build_pipeline(collabs)

    async def scenario() -> None:
        handle = await pipeline.open_session(SESSION_A, room=ROOM, identity="student-a5")
        await asyncio.sleep(0)
        await emit_frames(handle, collabs, [b"R1", b"R2"])
        await pipeline.close_session(SESSION_A)

    asyncio.run(scenario())
    # close 前从未 pump——残余帧应在 close 中被消费并进 transcript
    final = pipeline.final_transcript(SESSION_A)
    texts = [seg["text"] for seg in final["segments"]]
    assert "w0" in texts and "w1" in texts and "tail" in texts


# ---------- 7：会话隔离 ----------


def test_two_sessions_isolated() -> None:
    collabs = Collaborators()
    pipeline, manager = build_pipeline(collabs)

    async def scenario() -> None:
        handle_a = await pipeline.open_session(SESSION_A, room=ROOM, identity="student-b1")
        await asyncio.sleep(0)
        handle_b = await pipeline.open_session(SESSION_B, room=ROOM, identity="student-b2")
        await asyncio.sleep(0)
        assert manager.snapshot()["subscriber_count"] == 2
        # A 会话 emit 的帧只进 A 通道（A 房间 = rooms[0]）
        await emit_frames(handle_a, collabs, [b"A-only"], room_index=0)
        handle_a.pump(5)
        assert handle_a.transcript()["segment_count"] >= 1
        assert handle_b.transcript()["segment_count"] == 0  # B 未收到
        # 控制器独立
        handle_a.submit("start_reading")
        assert handle_b.control_snapshot()["state"] == "SESSION_READY"
        await pipeline.close_session(SESSION_A)
        await pipeline.close_session(SESSION_B)

    asyncio.run(scenario())
    assert manager.snapshot()["subscriber_count"] == 0


# ---------- 8：ASR 失败恢复传播 ----------


def test_asr_failure_isolated_pipeline_survives() -> None:
    collabs = Collaborators()
    manager = VoiceRuntimeManager()
    failing: list[bool] = [True]

    def adapter_factory() -> Any:
        adapter = RecordingAdapter(
            fail_indexes={0, 1, 2} if failing[0] else set()
        )
        collabs.adapters.append(adapter)
        return adapter

    pipeline = ServerVoicePipeline(
        manager=manager,
        factory_builder=collabs.make_factory,
        asr_adapter_factory=adapter_factory,
    )

    async def scenario() -> None:
        handle = await pipeline.open_session(SESSION_A, room=ROOM, identity="student-c1")
        await asyncio.sleep(0)
        await emit_frames(handle, collabs, [b"F1", b"F2", b"F3"])
        return handle

    handle = asyncio.run(scenario())
    result = handle.pump(10)
    assert result.failed_chunk_count == 3  # 全部 feed 失败
    assert result.new_segment_count == 0
    assert handle.transcript()["segment_count"] == 0
    # 管线仍活着：状态可查、控制可用
    handle.submit("start_reading")
    asyncio.run(pipeline.close_session(SESSION_A))


# ---------- 未知会话操作 ----------


def test_unknown_session_operations_rejected() -> None:
    collabs = Collaborators()
    pipeline, _manager = build_pipeline(collabs)
    with pytest.raises(VoicePipelineError):
        pipeline.pump_session("sess-unknown-xyz", 5)
    with pytest.raises(VoicePipelineError):
        pipeline.submit_session("sess-unknown-xyz", "pause")
    with pytest.raises(VoicePipelineError):
        pipeline.final_transcript("sess-unknown-xyz")
    asyncio.run(pipeline.close_session("sess-unknown-xyz"))  # 幂等 no-op


# ---------- 10：源码卫生 ----------


def test_source_hygiene_pipeline_module() -> None:
    tree = ast.parse(PIPELINE_SOURCE)
    allowed_roots = {"__future__", "typing", "collections"}
    allowed_domain = {
        "app.domain.voice_asr_stream",
        "app.domain.voice_frame_channel",
        "app.domain.voice_frame_sink",
        "app.domain.voice_session_control",
        "app.domain.voice_session_subscriptions",
        "app.domain.voice_subscriber_factory",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed_roots, alias.name
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            root = node.module or ""
            assert root.split(".")[0] in allowed_roots or root in allowed_domain, root
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else ""
            attr = func.attr if isinstance(func, ast.Attribute) else ""
            assert name not in {
                "open", "system", "popen", "run", "sleep", "getenv",
                "ensure_future", "create_task", "start_new_thread",
            }, name
            assert attr not in {
                "system", "popen", "getenv", "ensure_future",
                "create_task", "start_new_thread", "connect",
            }, attr
    for forbidden in (
        "getenv(", "os.environ", "dotenv", "__import__", "eval(",
        "socket", "subprocess", "threading", "import asyncio",
        "import livekit", "from livekit", "fastapi", "pathlib",
        "httpx", "aiohttp", "requests",
    ):
        assert forbidden not in PIPELINE_SOURCE, forbidden
