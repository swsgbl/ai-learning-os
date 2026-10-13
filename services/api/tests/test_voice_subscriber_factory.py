"""M14-261 受控语音订阅者工厂契约测试（全 fake，零网络/零运行时服务）。

覆盖（与任务验收面一一对应）：
1  构造零副作用：不调用任何注入协作者、无任何 I/O；
2  room 校验成功/失败（非字符串/空白/长度 2 与 65/非法字符集），
   与既有公开房间边界 ^[A-Za-z0-9_-]{3,64}$ 同口径；
3  identity 校验成功/失败——保守既有风格边界：与 room 同正则
   （兼容 new_identity 的 student-xxxxxxxx 形态；绝不放宽 room 语义）；
4  无效 room/identity 在 token provider / room factory / audio-stream
   factory 任何调用之前确定性失败（三计数恒 0）；
5  每次 create 返回彼此独立的 subscriber 与 sink（无共享可变状态）；
6  fake 音轨/帧只到达该订阅者自己的 sink 并递增帧计数；
7  handle 与 ManagedSubscriberLike 结构兼容（runtime_checkable）；
8  fake-only VoiceRuntimeManager.start() 集成：零参工厂闭包成功启动、
   stop 移除条目；
9  停止清理顺序保持订阅者拥有：残留事件任务 → 音频流 → 房间断开；
10 快照有界且净化：序列化工厂/handle/sink 输出无 JWT 形态 token、
   无原始音频字节、无协作者异常文本、无绝对私有路径、无无界历史；
11 token-provider / room-factory 失败产生通用净化错误且异常链被抑制
   （__cause__/__context__/序列化 traceback 均不含原始异常文本）；
12 源码卫生（AST 级）：新生产模块无网络/进程/线程/定时器/asyncio
   调度；无 LiveKit/FastAPI/HTTP client/settings/logging/DB/
   persistence 导入；运行时导入严格限定白名单。
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.domain.voice_runtime_manager import ManagedSubscriberLike, VoiceRuntimeManager
from app.domain.voice_subscriber_factory import (
    ControlledVoiceSubscriberFactory,
    ControlledVoiceSubscriberHandle,
    VoiceSubscriberFactoryError,
    validate_identity,
    validate_room,
)

#: 合成 JWT 形态样本（结构同真 JWT，签名段占位——仅供泄漏防线断言）
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
FRAME_A = b"PCM-frame-a"  # 11 字节
FRAME_B = b"PCM-bb"  # 6 字节

#: 与 API 公开房间边界同口径（routes/voice.py _ROOM_RE）。
API_ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")


def run(coro):
    asyncio.run(coro)


# ---------- fake 注入件（零网络；行为与既有订阅/汇测试同构） ----------


class FakeFrame:
    def __init__(self, data: object) -> None:
        self._data = data

    @property
    def data(self) -> object:
        return self._data


class FakeTrack:
    def __init__(self, sid: str, kind: str = "audio") -> None:
        self.sid = sid
        self.kind = kind


class FakeAudioStream:
    def __init__(self, ops: list[tuple[str, str]], sid: str) -> None:
        self._ops = ops
        self._sid = sid
        self.handler: Callable[[object], None] | None = None
        self.error_handler: Callable[[], None] | None = None

    def set_frame_handler(self, handler: Callable[[object], None]) -> None:
        self.handler = handler

    def set_error_handler(self, handler: Callable[[], None]) -> None:
        self.error_handler = handler

    async def close(self) -> None:
        self._ops.append(("stream_close", self._sid))

    def emit(self, frame: object) -> None:
        assert self.handler is not None
        self.handler(frame)


class FakeRoom:
    def __init__(self, ops: list[tuple[str, str]]) -> None:
        self._ops = ops
        self.handlers: dict[str, Callable[..., Any]] = {}
        self.connect_calls = 0
        self.disconnect_calls = 0

    def on(self, name: str, handler: Callable[..., Any]) -> None:
        self.handlers[name] = handler

    async def connect(self, token: str) -> None:
        self.connect_calls += 1  # token 只作参数出现，测试不持久化

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._ops.append(("room_disconnect", ""))

    def emit_track_subscribed(self, track: FakeTrack) -> None:
        self.handlers["track_subscribed"](track)


class Collaborators:
    """记录调用的注入协作者三件套（可注入失败；消息故意带 JWT 形态）。"""

    def __init__(
        self,
        *,
        token_raises: bool = False,
        room_raises: bool = False,
    ) -> None:
        self.token_calls = 0
        self.room_calls = 0
        self.stream_calls = 0
        self.token_raises = token_raises
        self.room_raises = room_raises
        self.ops: list[tuple[str, str]] = []
        self.rooms: list[FakeRoom] = []
        self.streams: list[FakeAudioStream] = []

    def token_provider(self) -> str:
        self.token_calls += 1
        if self.token_raises:
            raise RuntimeError(f"token boom {FAKE_JWT}")
        return FAKE_JWT

    def room_factory(self) -> FakeRoom:
        self.room_calls += 1
        if self.room_raises:
            raise RuntimeError(f"room boom {FAKE_JWT}")
        room = FakeRoom(self.ops)
        self.rooms.append(room)
        return room

    def audio_stream_factory(self, track: object) -> FakeAudioStream:
        self.stream_calls += 1
        sid = getattr(track, "sid", "track-?")
        stream = FakeAudioStream(self.ops, sid)
        self.streams.append(stream)
        return stream

    def make_factory(self, **kwargs: Any) -> ControlledVoiceSubscriberFactory:
        return ControlledVoiceSubscriberFactory(
            token_provider=self.token_provider,
            room_factory=self.room_factory,
            audio_stream_factory=self.audio_stream_factory,
            **kwargs,
        )


async def drain(rounds: int = 16) -> None:
    """排空订阅者 dispatch 任务链（与既有订阅测试同口径）。"""
    for _ in range(rounds):
        await asyncio.sleep(0)


# ---------- 1：构造零副作用 ----------


def test_constructor_zero_side_effects_and_no_collaborator_calls() -> None:
    collaborators = Collaborators()
    collaborators.make_factory()  # 构造本身
    assert collaborators.token_calls == 0
    assert collaborators.room_calls == 0
    assert collaborators.stream_calls == 0
    assert collaborators.rooms == []
    assert collaborators.streams == []
    assert collaborators.ops == []


def test_constructor_rejects_non_callable_collaborators() -> None:
    collaborators = Collaborators()
    for bad in (None, 123, "provider", object()):
        with pytest.raises(VoiceSubscriberFactoryError, match="协作者"):
            ControlledVoiceSubscriberFactory(
                token_provider=bad,
                room_factory=collaborators.room_factory,
                audio_stream_factory=collaborators.audio_stream_factory,
            )
        with pytest.raises(VoiceSubscriberFactoryError, match="协作者"):
            ControlledVoiceSubscriberFactory(
                token_provider=collaborators.token_provider,
                room_factory=bad,
                audio_stream_factory=collaborators.audio_stream_factory,
            )
        with pytest.raises(VoiceSubscriberFactoryError, match="协作者"):
            ControlledVoiceSubscriberFactory(
                token_provider=collaborators.token_provider,
                room_factory=collaborators.room_factory,
                audio_stream_factory=bad,
            )


def test_constructor_rejects_non_positive_max_events() -> None:
    collaborators = Collaborators()
    for bad in (0, -1, -64):
        with pytest.raises(VoiceSubscriberFactoryError, match="max_events"):
            collaborators.make_factory(max_events=bad)


# ---------- 2：room 校验（与既有公开边界同口径） ----------


def test_validate_room_accepts_public_room_boundary() -> None:
    for room in ("abc", "a" * 64, "voice-room-1", "room_2026", "s3rver-42"):
        assert validate_room(room) == room
        assert API_ROOM_RE.fullmatch(room)  # 与 API 公开边界一致


@pytest.mark.parametrize(
    "bad_room",
    [
        None,
        123,
        b"voice-room-1",  # bytes 绝不当字符串用
        True,
        ["voice-room-1"],
        "",
        "   ",
        "\t\n",
        "ab",  # 长度 2（< 3）
        "a" * 65,  # 长度 65（> 64）
        "bad room!",  # 空格与感叹号
        "bad/room",
        "bad\\room",
        "房间名",  # 非 ASCII 字符集
        "bad\x00room",  # 控制字符
        "bad\x7froom",
    ],
)
def test_validate_room_rejects_invalid(bad_room) -> None:
    with pytest.raises(VoiceSubscriberFactoryError, match="房间名非法"):
        validate_room(bad_room)


# ---------- 3：identity 校验（保守既有风格边界：与 room 同正则） ----------


def test_validate_identity_accepts_existing_style_values() -> None:
    # new_identity() 生成形态（student-xxxxxxxx）与既有服务端命名均兼容
    for identity in ("server-sub-1", "student-ab12cd34", "abc", "b" * 64, "sub_01"):
        assert validate_identity(identity) == identity
        assert API_ROOM_RE.fullmatch(identity)  # 同一保守边界


@pytest.mark.parametrize(
    "bad_identity",
    [
        None,
        123,
        b"server-sub-1",
        True,
        ["server-sub-1"],
        "",
        "   ",
        "ab",
        "c" * 65,
        "bad identity",
        "身份",
        "bad\x1bidentity",
    ],
)
def test_validate_identity_rejects_invalid(bad_identity) -> None:
    with pytest.raises(VoiceSubscriberFactoryError, match="身份标识非法"):
        validate_identity(bad_identity)


# ---------- 4：无效参数在任何协作者调用之前失败 ----------


@pytest.mark.parametrize(
    "bad_room",
    ["ab", "bad room", None, 123, b"voice-room-1", "", "a" * 65, "bad\x00room"],
)
def test_invalid_room_rejected_before_any_collaborator_call(bad_room) -> None:
    collaborators = Collaborators()
    factory = collaborators.make_factory()
    with pytest.raises(VoiceSubscriberFactoryError):
        factory.create(bad_room, "server-sub-1")
    assert collaborators.token_calls == 0
    assert collaborators.room_calls == 0
    assert collaborators.stream_calls == 0


@pytest.mark.parametrize(
    "bad_identity",
    ["  ", None, 123, b"server-sub-1", "", "x" * 65, "bad identity", "bad\x1bid"],
)
def test_invalid_identity_rejected_before_any_collaborator_call(bad_identity) -> None:
    collaborators = Collaborators()
    factory = collaborators.make_factory()
    with pytest.raises(VoiceSubscriberFactoryError):
        factory.create("voice-room-1", bad_identity)
    assert collaborators.token_calls == 0
    assert collaborators.room_calls == 0
    assert collaborators.stream_calls == 0


# ---------- 5：每次 create 独立实例 ----------


def test_create_returns_distinct_subscriber_and_sink_instances() -> None:
    collaborators = Collaborators()
    factory = collaborators.make_factory()
    first = factory.create("voice-r1", "server-sub-1")
    second = factory.create("voice-r2", "server-sub-2")
    assert isinstance(first, ControlledVoiceSubscriberHandle)
    assert first is not second
    # 白盒身份断言：订阅者与汇均独立（无共享可变状态）
    assert first._subscriber is not second._subscriber
    assert first._sink is not second._sink
    # 初始投影：idle、未启动、零帧
    state = first.state()
    assert state["status"] == "idle"
    assert state["room"] == "voice-r1"
    assert state["identity"] == "server-sub-1"
    assert first.sink_snapshot()["frame_count"] == 0
    assert second.sink_snapshot()["frame_count"] == 0
    # 构造后仍未调用任何协作者（start 才触发）
    assert collaborators.token_calls == 0
    assert collaborators.room_calls == 0


# ---------- 6：帧只到达本订阅者的汇 ----------


def test_fake_frame_reaches_only_own_subscriber_sink() -> None:
    collaborators = Collaborators()
    factory = collaborators.make_factory()
    first = factory.create("voice-r1", "server-sub-1")
    second = factory.create("voice-r2", "server-sub-2")

    async def scenario() -> None:
        await first.start()
        await second.start()
        assert collaborators.token_calls == 2
        assert collaborators.room_calls == 2  # 每订阅者独立房间连接
        # 各自房间收到各自音轨
        collaborators.rooms[0].emit_track_subscribed(FakeTrack("tr-a"))
        collaborators.rooms[1].emit_track_subscribed(FakeTrack("tr-b"))
        await drain()
        assert collaborators.stream_calls == 2
        # 帧只进本订阅者的流 → 本订阅者的汇
        collaborators.streams[0].emit(FakeFrame(FRAME_A))
        collaborators.streams[0].emit(FakeFrame(FRAME_A))
        collaborators.streams[1].emit(FakeFrame(FRAME_B))
        first_snap = first.sink_snapshot()
        second_snap = second.sink_snapshot()
        assert first_snap["frame_count"] == 2
        assert first_snap["frame_byte_count"] == 2 * len(FRAME_A)
        assert second_snap["frame_count"] == 1
        assert second_snap["frame_byte_count"] == len(FRAME_B)
        assert first_snap["event_count"] == 3  # connecting/connected/track_subscribed
        await first.stop()
        await second.stop()

    run(scenario())


# ---------- 7：ManagedSubscriberLike 结构兼容 ----------


def test_handle_structurally_satisfies_managed_subscriber_like() -> None:
    collaborators = Collaborators()
    factory = collaborators.make_factory()
    handle = factory.create("voice-r1", "server-sub-1")
    assert isinstance(handle, ManagedSubscriberLike)
    assert callable(handle.start)
    assert callable(handle.stop)
    assert callable(handle.state)
    # handle 不暴露原始 sink / 订阅者对象（公开面只有净化投影）
    public_names = [name for name in dir(handle) if not name.startswith("_")]
    assert "sink" not in public_names
    assert "subscriber" not in public_names
    assert "token" not in public_names


# ---------- 8：VoiceRuntimeManager 集成（fake-only） ----------


def test_voice_runtime_manager_integration_start_and_stop() -> None:
    collaborators = Collaborators()
    factory = collaborators.make_factory()
    manager = VoiceRuntimeManager()

    def subscriber_factory() -> ControlledVoiceSubscriberHandle:
        return factory.create("voice-int", "server-sub-int")  # 零参闭包

    async def scenario() -> None:
        projection = await manager.start("voice-int", subscriber_factory)
        assert projection["status"] == "active"  # handle.state() 委托生效
        assert projection["room"] == "voice-int"
        assert projection["identity"] == "server-sub-int"
        assert collaborators.token_calls == 1
        assert collaborators.rooms[0].connect_calls == 1
        # stop：移除条目 + 订阅者断开
        await manager.stop("voice-int")
        assert manager.snapshot()["subscriber_count"] == 0
        assert collaborators.rooms[0].disconnect_calls == 1

    run(scenario())


# ---------- 9：停止清理顺序（订阅者拥有） ----------


def test_stop_cleanup_ordering_streams_then_room_disconnect() -> None:
    collaborators = Collaborators()
    factory = collaborators.make_factory()
    handle = factory.create("voice-order", "server-sub-order")

    async def scenario() -> None:
        await handle.start()
        collaborators.rooms[0].emit_track_subscribed(FakeTrack("tr-x"))
        collaborators.rooms[0].emit_track_subscribed(FakeTrack("tr-y"))
        await drain()
        await handle.stop()
        # 订阅者确定顺序：全部流先关、房间后断（无交错）
        assert [op for op, _ in collaborators.ops] == [
            "stream_close",
            "stream_close",
            "room_disconnect",
        ]
        assert handle.state()["status"] == "stopped"

    run(scenario())


# ---------- 10：快照有界且净化 ----------


def test_handle_and_sink_snapshots_bounded_and_sanitized() -> None:
    collaborators = Collaborators()
    factory = collaborators.make_factory(max_events=4)
    handle = factory.create("voice-san", "server-sub-san")

    async def scenario() -> None:
        await handle.start()
        # 脏注入：音轨 sid 携带 JWT 形态（外部输入面）
        collaborators.rooms[0].emit_track_subscribed(FakeTrack(f"tr-{FAKE_JWT}"))
        collaborators.rooms[0].emit_track_subscribed(FakeTrack("tr-clean"))
        await drain()
        collaborators.streams[0].emit(FakeFrame(FRAME_A))
        await handle.stop()

    run(scenario())
    state = handle.state()
    sink_snapshot = handle.sink_snapshot()
    blob = json.dumps(
        {"state": state, "sink": sink_snapshot}, ensure_ascii=False, default=repr
    )
    # 无 JWT 形态 token（handle.state 深度净化 + sink 字段净化双防线）
    assert JWT_RE.search(blob) is None
    assert "[REDACTED-JWT]" in json.dumps(state, ensure_ascii=False)
    # 无原始音频字节（含其可解码文本）
    assert FRAME_A.decode(errors="ignore") not in blob
    assert "b'" not in blob
    # 无协作者异常文本
    assert "boom" not in blob
    # 无绝对私有路径形态
    for path_marker in ("D:\\", "C:\\", "/home/", "/Users/"):
        assert path_marker not in blob
    # 有界：events 截断到注入上限、sink 事件投影同界
    assert len(state["events"]) <= 4
    assert len(sink_snapshot["events"]) <= 4
    assert sink_snapshot["frame_count"] == 1
    assert sink_snapshot["frame_byte_count"] == len(FRAME_A)
    # 帧只计数不出字节：sink 顶层恰六个白名单键
    assert set(sink_snapshot) == {
        "max_events",
        "event_count",
        "events",
        "frame_count",
        "frame_byte_count",
        "invalid_frame_count",
    }


# ---------- 11：协作者失败 → 通用净化错误 + 异常链抑制 ----------


def _assert_chain_suppressed(error: BaseException) -> None:
    """__cause__/__context__/序列化 traceback 三面均不含原始异常。"""
    assert error.__cause__ is None
    assert error.__context__ is None
    serialized = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    assert FAKE_JWT not in serialized
    assert "boom" not in serialized
    assert JWT_RE.search(serialized) is None


def test_token_provider_failure_generic_error_suppressed_chain() -> None:
    collaborators = Collaborators(token_raises=True)
    factory = collaborators.make_factory()
    handle = factory.create("voice-tok", "server-sub-tok")

    async def scenario() -> None:
        with pytest.raises(VoiceSubscriberFactoryError, match="启动失败"):
            await handle.start()

    run(scenario())
    # 事后取证：捕获的错误对象上重放断言
    caught: list[BaseException] = []

    async def again() -> None:
        with pytest.raises(VoiceSubscriberFactoryError) as exc_info:
            await handle.start()  # 终态重启同样转通用错误
        caught.append(exc_info.value)

    run(again())
    _assert_chain_suppressed(caught[0])
    # 订阅者侧状态封存 failed（通用净化事件，无 token 文本）
    state_text = json.dumps(handle.state(), ensure_ascii=False)
    assert FAKE_JWT not in state_text
    assert handle.state()["status"] in ("failed", "stopped")


def test_room_factory_failure_generic_error_suppressed_chain() -> None:
    collaborators = Collaborators(room_raises=True)
    factory = collaborators.make_factory()
    handle = factory.create("voice-rf", "server-sub-rf")

    async def scenario() -> None:
        with pytest.raises(VoiceSubscriberFactoryError) as exc_info:
            await handle.start()
        error = exc_info.value
        assert "room boom" not in str(error)
        assert FAKE_JWT not in str(error)
        _assert_chain_suppressed(error)

    run(scenario())


# ---------- 12：源码卫生（AST 级） ----------


def test_source_hygiene_no_forbidden_imports_or_calls() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "domain"
        / "voice_subscriber_factory.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    allowed_runtime_imports = {
        "__future__",
        "re",
        "collections.abc",
        "typing",
        "app.domain.voice_frame_sink",
        "app.domain.voice_subscription",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in allowed_runtime_imports, alias.name
        if isinstance(node, ast.ImportFrom):
            root = node.module or ""
            if node.level == 0:
                assert root in allowed_runtime_imports, root
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else ""
            attr = func.attr if isinstance(func, ast.Attribute) else ""
            assert name not in {
                "open",
                "system",
                "popen",
                "run",
                "sleep",
                "ensure_future",
                "create_task",
                "start_new_thread",
            }, name
            assert attr not in {
                "system",
                "popen",
                "ensure_future",
                "create_task",
                "start_new_thread",
            }, attr
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                assert func.value.id != "asyncio", "asyncio 调度禁止出现在本模块"
    # AST 导入白名单之上再堵动态导入/动态执行逃逸口
    source = source_path.read_text(encoding="utf-8")
    for forbidden in ("__import__", "eval(", "exec("):
        assert forbidden not in source, forbidden
