"""M14-260 有界语音帧计量汇契约测试（全 fake，零网络/零运行时服务）。

覆盖（与任务验收面一一对应；R1 修正后收紧）：
1  默认上限（与订阅域约定一致，64）与自定义正数上限；
2  非正上限以确定性错误拒绝；
3  最新-N 事件保留 + 含被淘汰者的全量事件计数；
4  事件投影字段白名单 + JWT 形态文本净化 + 原始事件对象绝不保留；
4b 已知字段仅接受 str/None——bytes 等其他类型映射固定占位
   [INVALID-FIELD]，序列化无 b'/原始字节表示（R1 修正 2）；
4c 事件字段净化后确定性截断到 512 字符，尾部内容不入快照（R1 修正 3）；
5  有效帧（仅 bytes/bytearray/memoryview）计数与字节累计（R1 修正 1）；
6  无效/缺失帧数据（含 str/list/dict 等 len 可用但非有效载荷类型）
   不抛出并计入 invalid_frame_count；
7  snapshot 无原始帧对象/原始音频字节/异常文本，顶层字段有界；
8  与 EventSinkLike 的结构兼容（runtime_checkable 协议面）；
9  与 ServerVoiceSubscriber 的小型集成契约（沿用既有 fake room/stream
   模式，证明汇可接收订阅者事件与帧且订阅者行为零变化）；
10 源码卫生（新模块无网络/进程/线程/事件循环/FastAPI 导入或调用，
    运行时导入严格限定在纯标准库白名单）。
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.domain import voice_frame_sink as voice_frame_sink_module
from app.domain.voice_frame_sink import (
    DEFAULT_MAX_EVENTS,
    INVALID_FIELD,
    MAX_FIELD_LENGTH,
    BoundedVoiceFrameSink,
    VoiceFrameSinkError,
)
from app.domain.voice_subscription import (
    DEFAULT_MAX_EVENTS as SUBSCRIBER_DEFAULT_MAX_EVENTS,
)
from app.domain.voice_subscription import (
    EventSinkLike,
    ServerVoiceSubscriber,
    SubscriptionEvent,
)

#: 合成 JWT 形态样本（结构同真 JWT，签名段占位——仅供泄漏防线断言）
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
REDACTED = "[REDACTED-JWT]"
FRAME_A = b"PCM-frame-a"  # 11 字节
FRAME_B = b"PCM-bb"  # 6 字节


def event(**overrides: Any) -> SubscriptionEvent:
    fields: dict[str, Any] = {
        "type": "connected",
        "room": "voice-room-1",
        "identity": "server-sub-1",
        "category": None,
        "detail": "",
    }
    fields.update(overrides)
    return SubscriptionEvent(**fields)


# ---------- fake 注入件（帧替身与订阅者集成环境，零网络） ----------


class FakeFrame:
    def __init__(self, data: object) -> None:
        self._data = data

    @property
    def data(self) -> object:
        return self._data


class NoDataFrame:
    """完全没有 data 属性的帧替身。"""


class RaisingDataFrame:
    @property
    def data(self) -> bytes:
        raise RuntimeError(f"data boom {FAKE_JWT}")


class BrokenLenData:
    """__len__ 返回非数字——任何把 length 当数字用的实现都会炸。"""

    def __len__(self) -> object:  # type: ignore[override]
        return "not-a-number"  # noqa: PLE0303 —— 故意破坏的 __len__ 正是被测容错面


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

    def on(self, name: str, handler: Callable[..., Any]) -> None:
        self.handlers[name] = handler

    async def connect(self, token: str) -> None:
        return None  # token 只作参数出现，测试不持久化

    async def disconnect(self) -> None:
        self._ops.append(("room_disconnect", ""))

    def emit_track_subscribed(self, track: FakeTrack) -> None:
        self.handlers["track_subscribed"](track)


async def drain(rounds: int = 16) -> None:
    """排空订阅者 dispatch 任务链（与既有订阅测试同口径）。"""
    for _ in range(rounds):
        await asyncio.sleep(0)


# ---------- 1/2：默认与自定义上限、非正上限确定性拒绝 ----------


def test_default_bound_matches_voice_domain_convention() -> None:
    sink = BoundedVoiceFrameSink()
    snapshot = sink.snapshot()
    assert snapshot["max_events"] == 64
    assert DEFAULT_MAX_EVENTS == SUBSCRIBER_DEFAULT_MAX_EVENTS  # 域约定一致
    assert snapshot["event_count"] == 0
    assert snapshot["events"] == []
    assert snapshot["frame_count"] == 0
    assert snapshot["frame_byte_count"] == 0
    assert snapshot["invalid_frame_count"] == 0


def test_custom_positive_bound_keeps_only_newest() -> None:
    sink = BoundedVoiceFrameSink(max_events=2)
    sink.on_event(event(type="e1"))
    sink.on_event(event(type="e2"))
    sink.on_event(event(type="e3"))
    snapshot = sink.snapshot()
    assert snapshot["max_events"] == 2
    assert [proj["type"] for proj in snapshot["events"]] == ["e2", "e3"]


def test_non_positive_bounds_rejected_deterministically() -> None:
    for bad in (0, -1, -64):
        with pytest.raises(VoiceFrameSinkError) as exc_info:
            BoundedVoiceFrameSink(max_events=bad)
        assert str(exc_info.value) == "max_events 必须为正"


# ---------- 3：最新-N 保留 + 全量计数 + 快照全新拷贝 ----------


def test_newest_n_retention_with_total_count_after_eviction() -> None:
    sink = BoundedVoiceFrameSink(max_events=3)
    for index in range(10):
        sink.on_event(event(type=f"e{index}", detail=f"seq={index}"))
    snapshot = sink.snapshot()
    assert snapshot["event_count"] == 10  # 全量计数含被淘汰者
    assert len(snapshot["events"]) == 3  # 投影有界
    assert [proj["type"] for proj in snapshot["events"]] == ["e7", "e8", "e9"]
    assert [proj["detail"] for proj in snapshot["events"]] == [
        "seq=7",
        "seq=8",
        "seq=9",
    ]
    # 快照全新拷贝：调用方改写（含内层 dict）不影响后续快照
    snapshot["events"].append({"type": "tampered"})
    snapshot["events"][0]["type"] = "tampered"
    again = sink.snapshot()
    assert [proj["type"] for proj in again["events"]] == ["e7", "e8", "e9"]


# ---------- 4：投影白名单 + JWT 净化 + 原始事件不保留 ----------


def test_projection_whitelist_and_jwt_sanitization() -> None:
    sink = BoundedVoiceFrameSink()
    dirty = event(
        room=f"room-{FAKE_JWT}",
        detail=f"connect 失败 {FAKE_JWT}",
        category="connect",
    )
    sink.on_event(dirty)
    projection = sink.snapshot()["events"][0]
    # 字段白名单：恰好五个已知安全字段，别无其他
    assert set(projection) == {"type", "room", "identity", "category", "detail"}
    assert projection["category"] == "connect"
    assert projection["room"] == f"room-{REDACTED}"
    assert projection["detail"] == f"connect 失败 {REDACTED}"
    # JWT 三段形态在快照序列化文本中零命中
    assert JWT_RE.search(json.dumps(sink.snapshot(), ensure_ascii=False)) is None
    # 原始事件对象绝不保留：投影是新建 dict，且与原事件无对象同一性
    assert isinstance(projection, dict)
    assert all(proj is not dirty for proj in sink.snapshot()["events"])


def test_duck_event_extra_attributes_are_not_projected() -> None:
    class DuckEvent:
        type = "connected"
        room = "voice-room-1"
        identity = "server-sub-1"
        category = None
        detail = "ok"
        secret_token = FAKE_JWT  # 白名单之外的字段绝进投影
        audio_bytes = FRAME_A  # 同上

    sink = BoundedVoiceFrameSink()
    sink.on_event(DuckEvent())  # type: ignore[arg-type]
    projection = sink.snapshot()["events"][0]
    assert set(projection) == {"type", "room", "identity", "category", "detail"}
    assert FAKE_JWT not in json.dumps(sink.snapshot())
    assert FRAME_A.decode() not in json.dumps(sink.snapshot())


def test_non_string_known_fields_map_to_placeholder_no_bytes_leak() -> None:
    """R1 修正 2：已知字段仅接受 str/None——bytes 映射固定占位。

    事件字段被注入 PCM 形态 bytes 与数值/列表——投影必须是占位
    字符串，序列化文本中既无 b' 字节 repr、也无任何原始/解码
    字节表示（绝不 str() 化 bytes）。
    """
    pcm = b"\x00\x01\x02PCM-bytes-do-not-persist\xfe\xff"
    sink = BoundedVoiceFrameSink()
    sink.on_event(
        event(
            room=pcm,  # type: ignore[arg-type]
            identity=["server-sub-1"],  # type: ignore[arg-type]
            category=123,  # type: ignore[arg-type]
            detail=pcm,  # type: ignore[arg-type]
        )
    )
    projection = sink.snapshot()["events"][0]
    assert projection["room"] == INVALID_FIELD
    assert projection["identity"] == INVALID_FIELD
    assert projection["category"] == INVALID_FIELD
    assert projection["detail"] == INVALID_FIELD
    serialized = json.dumps(sink.snapshot(), ensure_ascii=False)
    # 无 bytes repr、无原始/解码字节表示
    assert "b'" not in serialized
    assert 'b"' not in serialized
    assert "PCM-bytes-do-not-persist" not in serialized
    assert "\u0000" not in serialized  # 控制字符亦未进入序列化文本


def test_long_field_truncated_to_max_field_length() -> None:
    """R1 修正 3：事件字段净化后确定性截断到 MAX_FIELD_LENGTH（512）。"""
    long_detail = "a" * MAX_FIELD_LENGTH + "TAIL-MARKER-REMOVED" + "b" * 40
    assert len(long_detail) > 512
    assert MAX_FIELD_LENGTH == 512  # 显式常量、恒定边界
    sink = BoundedVoiceFrameSink()
    sink.on_event(event(detail=long_detail))
    projection = sink.snapshot()["events"][0]
    assert len(projection["detail"]) == MAX_FIELD_LENGTH
    assert projection["detail"] == long_detail[:MAX_FIELD_LENGTH]
    serialized = json.dumps(sink.snapshot(), ensure_ascii=False)
    assert "TAIL-MARKER-REMOVED" not in serialized  # 截掉的尾部绝不入快照
    assert "b" * 40 not in serialized


# ---------- 5/6：帧计量与无效帧容错 ----------


def test_frame_counts_and_byte_counts() -> None:
    sink = BoundedVoiceFrameSink()
    sink.on_frame(FakeFrame(FRAME_A))  # bytes
    sink.on_frame(FakeFrame(bytearray(FRAME_B)))  # bytearray 同样有效
    sink.on_frame(FakeFrame(memoryview(b"mv")))  # memoryview 同样有效
    sink.on_frame(FakeFrame(b""))  # 空帧有效（0 字节）
    snapshot = sink.snapshot()
    assert snapshot["frame_count"] == 4
    assert snapshot["frame_byte_count"] == len(FRAME_A) + len(FRAME_B) + 2
    assert snapshot["invalid_frame_count"] == 0


def test_invalid_frame_data_never_raises_and_counts() -> None:
    sink = BoundedVoiceFrameSink()
    invalid_frames = [
        NoDataFrame(),  # 缺 data 属性
        FakeFrame(None),  # data 为 None
        FakeFrame(12345),  # data 非有效载荷类型且不可计长
        FakeFrame("PCM-frame-a"),  # str：len 可用但非有效载荷类型
        FakeFrame([1, 2, 3]),  # list：len 可用但非有效载荷类型
        FakeFrame({"k": 1}),  # dict：同上
        RaisingDataFrame(),  # data 属性访问即抛（异常文本含 JWT 形态）
        FakeFrame(BrokenLenData()),  # __len__ 返回非数字
    ]
    for frame in invalid_frames:
        sink.on_frame(frame)  # 每一种都不抛出
    sink.on_frame(FakeFrame(FRAME_A))  # 无效帧之后有效帧照常计量
    snapshot = sink.snapshot()
    assert snapshot["invalid_frame_count"] == len(invalid_frames)
    assert snapshot["frame_count"] == 1  # 无效帧不计入有效帧数
    assert snapshot["frame_byte_count"] == len(FRAME_A)


# ---------- 7：快照安全面（无原始帧/字节/异常文本，顶层字段有界） ----------


def test_snapshot_has_no_raw_frame_or_bytes_or_exception_text() -> None:
    sink = BoundedVoiceFrameSink()
    sink.on_event(event(detail=f"connect 失败 {FAKE_JWT}"))
    sink.on_frame(FakeFrame(FRAME_A))
    sink.on_frame(RaisingDataFrame())  # 帧数据异常文本 = "data boom <JWT>"
    snapshot = sink.snapshot()
    # 顶层字段恰为有界白名单（无时间戳/路径/凭据/无界历史）
    assert set(snapshot) == {
        "max_events",
        "event_count",
        "events",
        "frame_count",
        "frame_byte_count",
        "invalid_frame_count",
    }
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert FRAME_A.decode() not in serialized  # 无原始音频字节
    assert JWT_RE.search(serialized) is None  # 无 JWT 形态
    assert "data boom" not in serialized  # 帧数据异常文本绝不入快照
    assert all(isinstance(proj, dict) for proj in snapshot["events"])


# ---------- 8：EventSinkLike 结构兼容 ----------


def test_satisfies_event_sink_like_structurally() -> None:
    sink = BoundedVoiceFrameSink()
    assert isinstance(sink, EventSinkLike)  # runtime_checkable 协议面
    assert callable(sink.on_event)
    assert callable(sink.on_frame)
    assert callable(sink.snapshot)


# ---------- 9：与 ServerVoiceSubscriber 的集成契约 ----------


def test_integration_with_server_voice_subscriber() -> None:
    ops: list[tuple[str, str]] = []
    room = FakeRoom(ops)
    streams: dict[str, FakeAudioStream] = {}
    sink = BoundedVoiceFrameSink(max_events=8)

    def audio_stream_factory(track: FakeTrack) -> FakeAudioStream:
        stream = FakeAudioStream(ops, track.sid)
        streams[track.sid] = stream
        return stream

    controller = ServerVoiceSubscriber(
        room="voice-room-1",
        identity="server-sub-1",
        token_provider=lambda: FAKE_JWT,
        room_factory=lambda: room,
        audio_stream_factory=audio_stream_factory,
        event_sink=sink,
    )

    async def scenario() -> None:
        await controller.start()
        room.emit_track_subscribed(FakeTrack("TR-audio-1"))
        await drain()
        streams["TR-audio-1"].emit(FakeFrame(FRAME_A))
        streams["TR-audio-1"].emit(FakeFrame(FRAME_B))
        await controller.stop()

    asyncio.run(scenario())
    # 订阅者行为零变化：状态/清理顺序与既有订阅测试同口径
    state = controller.state()
    assert state["status"] == "stopped"
    assert state["audio_track_count"] == 0
    assert ops == [("stream_close", "TR-audio-1"), ("room_disconnect", "")]
    # 汇接住了订阅者全部事件与帧
    snapshot = sink.snapshot()
    assert snapshot["event_count"] == 4
    assert [proj["type"] for proj in snapshot["events"]] == [
        "connecting",
        "connected",
        "track_subscribed",
        "stopped",
    ]
    assert all(proj["room"] == "voice-room-1" for proj in snapshot["events"])
    assert snapshot["frame_count"] == 2
    assert snapshot["frame_byte_count"] == len(FRAME_A) + len(FRAME_B)
    assert snapshot["invalid_frame_count"] == 0
    # 泄漏防线：token 只作 connect 参数，快照序列化零 JWT/零音频字节
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert JWT_RE.search(serialized) is None
    assert FRAME_A.decode() not in serialized


# ---------- 10：源码卫生 ----------


def test_source_hygiene_no_forbidden_imports_or_calls() -> None:
    source_path = Path(voice_frame_sink_module.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))

    # 运行时导入严格限定在纯标准库白名单（TYPE_CHECKING 块内除外）
    allowed = {"__future__", "re", "collections", "typing"}
    type_checking_block: set[ast.stmt] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        ):
            type_checking_block.update(ast.walk(node))
    for node in ast.walk(tree):
        if node in type_checking_block:
            continue
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            roots = {node.module.split(".")[0]}
        else:
            continue
        assert roots <= allowed, f"模块外导入: {roots - allowed}"

    # 无网络/进程/线程/事件循环/定时器/磁盘调用
    forbidden_calls = {
        "open",
        "system",
        "popen",
        "run",
        "sleep",
        "ensure_future",
        "create_task",
        "gather",
        "start_new_thread",
        "Thread",
        "Popen",
        "urlopen",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else ""
            )
            assert name not in forbidden_calls, f"禁止调用: {name}"

    # 纯同步模块：无 async 函数/await/async for/async with
    for node in ast.walk(tree):
        assert not isinstance(
            node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)
        )
