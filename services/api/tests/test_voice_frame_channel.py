"""M14-269 有界音频帧管道契约测试（fake-only，零网络/零真实连接）。

覆盖（与切片验收面一一对应）：
1  构造校验：max_frames/max_bytes 正数、丢弃策略合法（drop_oldest/
   drop_newest）、审计缓冲正数；非法确定性拒绝；
2  帧入缓冲：FIFO 顺序、字节预算累计、有效类型恰
   bytes/bytearray/memoryview（与 M14-260 计量汇同口径）；
3  内存上界可证明：帧数上限与字节上限**任一**到顶即触发丢弃；
   任意时刻缓冲帧数 ≤ max_frames 且缓冲字节 ≤ max_bytes；
4  丢弃策略：drop_oldest（满时淘汰最旧、新帧入界——缓冲恒为
   最新 N）；drop_newest（满时拒绝新帧——缓冲恒为最先 N）；
5  丢弃审计：dropped_frame_count/dropped_byte_count 单调递增、
   审计事件有界环形（溢出淘汰最旧）、事件只含计数与原因常量
   （绝不含帧字节/凭据）；
6  drain 消费：批量 FIFO 取走帧字节、缓冲与字节预算同步归还、
   二次 drain 不重复交付、limit 必须为正整数（API 误用确定性
   拒绝）、空缓冲 drain 返回空列表；
7  无效帧：data 缺失/属性抛错/str/list/dict/长度非法——计
   invalid_frame_count、不入缓冲、绝不抛出（与计量汇同口径）；
8  分流汇 SplitVoiceSink：on_event 只路由计量汇；on_frame
   **fan-out**（计量汇计数 + 通道入界同时发生）——「把 LiveKit
   音频帧接入 BoundedVoiceFrameSink 链路」的接线件；
9  fake-only 集成：真实 ServerVoiceSubscriber + fake 房间/流 +
   SplitVoiceSink(计量汇, 通道)——emit 帧经流 handler 到达通道
   （字节与顺序逐字一致）、计量汇 frame_count 同步、drain 全量
   取回后缓冲空；
10 快照卫生：channel 快照/审计快照序列化后无帧字节形态（PCM
   样本串）、无凭据/token、无无界历史；
11 源码卫生（AST+文本）：纯标准库白名单导入（collections/
   typing）；无文件/网络/线程/定时器/asyncio/落盘调用（open/
   write/Path/pickle 全禁）；无 LiveKit/FastAPI 导入。

全部使用合成 PCM 假帧字节；绝不连接真实 LiveKit、绝不落盘。
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.domain.voice_frame_channel import (
    DROP_NEWEST,
    DROP_OLDEST,
    BoundedVoiceFrameChannel,
    SplitVoiceSink,
    VoiceFrameChannelError,
)
from app.domain.voice_frame_sink import BoundedVoiceFrameSink
from app.domain.voice_subscription import ServerVoiceSubscriber

#: 显眼假凭据（绝不真实）。
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = (
    REPO_ROOT / "services" / "api" / "app" / "domain" / "voice_frame_channel.py"
)
MODULE_SOURCE = MODULE_PATH.read_text(encoding="utf-8")

ROOM = "exam-room-alpha"
IDENT = "student-aaa00001"


class FakeFrame:
    """合成 PCM 假帧（与既有订阅测试同构）。"""

    def __init__(self, data: object) -> None:
        self._data = data

    @property
    def data(self) -> object:
        return self._data


class BrokenFrame:
    """data 属性抛错的坏帧。"""

    @property
    def data(self) -> object:
        raise RuntimeError("frame exploded with secret-ish detail")


class FakeTrack:
    def __init__(self, sid: str, kind: str = "audio") -> None:
        self.sid = sid
        self.kind = kind


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

    def on(self, name: str, handler: Any) -> None:
        self.handlers[name] = handler

    async def connect(self, token: str) -> None: ...

    async def disconnect(self) -> None: ...


# ---------- 1：构造校验 ----------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_frames": 0},
        {"max_frames": -1},
        {"max_bytes": 0},
        {"max_bytes": -5},
        {"drop_policy": "drop-all"},
        {"max_audit_events": 0},
    ],
)
def test_constructor_rejects_invalid_config(kwargs) -> None:
    with pytest.raises(VoiceFrameChannelError):
        BoundedVoiceFrameChannel(**kwargs)


def test_constructor_defaults() -> None:
    channel = BoundedVoiceFrameChannel()
    snap = channel.snapshot()
    assert snap["max_frames"] > 0 and snap["max_bytes"] > 0
    assert snap["drop_policy"] == DROP_OLDEST
    assert snap["buffered_frames"] == 0 and snap["buffered_bytes"] == 0


# ---------- 2：帧入缓冲（FIFO + 字节累计） ----------


def test_frames_buffer_fifo_with_byte_accounting() -> None:
    channel = BoundedVoiceFrameChannel(max_frames=8, max_bytes=1024)
    channel.on_frame(FakeFrame(b"AAAA"))
    channel.on_frame(FakeFrame(bytearray(b"BB")))
    channel.on_frame(FakeFrame(memoryview(b"C")))
    snap = channel.snapshot()
    assert snap["buffered_frames"] == 3
    assert snap["buffered_bytes"] == 4 + 2 + 1
    assert snap["received_frame_count"] == 3
    assert channel.drain(10) == [b"AAAA", b"BB", b"C"]


def test_single_frame_over_byte_budget_rejected_not_buffered() -> None:
    """单帧超过总字节预算：拒绝入界并审计（绝不为凑预算放宽上界）。"""
    channel = BoundedVoiceFrameChannel(max_frames=8, max_bytes=8)
    channel.on_frame(FakeFrame(b"X" * 16))
    snap = channel.snapshot()
    assert snap["buffered_frames"] == 0
    assert snap["dropped_frame_count"] == 1
    assert snap["dropped_byte_count"] == 16


# ---------- 3：内存上界（帧数限/字节限任一触发） ----------


def test_frame_count_cap_enforced() -> None:
    channel = BoundedVoiceFrameChannel(max_frames=3, max_bytes=1024)
    for i in range(6):
        channel.on_frame(FakeFrame(bytes([65 + i]) * 2))
    snap = channel.snapshot()
    assert snap["buffered_frames"] == 3
    assert snap["buffered_bytes"] == 6
    assert snap["received_frame_count"] == 6


def test_byte_cap_enforced() -> None:
    channel = BoundedVoiceFrameChannel(max_frames=100, max_bytes=10)
    for i in range(5):
        channel.on_frame(FakeFrame(bytes([65 + i]) * 4))
    snap = channel.snapshot()
    assert snap["buffered_frames"] == 2  # 4+4=8 ≤10；第三帧 4 会超 → 触发策略
    assert snap["buffered_bytes"] <= 10
    assert snap["received_frame_count"] == 5


@pytest.mark.parametrize("policy", [DROP_OLDEST, DROP_NEWEST])
def test_memory_bounds_hold_under_pressure(policy) -> None:
    channel = BoundedVoiceFrameChannel(max_frames=4, max_bytes=16, drop_policy=policy)
    for i in range(50):
        channel.on_frame(FakeFrame(bytes([i % 26 + 65]) * 3))
        snap = channel.snapshot()
        assert snap["buffered_frames"] <= 4
        assert snap["buffered_bytes"] <= 16


# ---------- 4：丢弃策略 ----------


def test_drop_oldest_keeps_newest() -> None:
    channel = BoundedVoiceFrameChannel(max_frames=2, max_bytes=1024)
    channel.on_frame(FakeFrame(b"old-1"))
    channel.on_frame(FakeFrame(b"old-2"))
    channel.on_frame(FakeFrame(b"new-3"))
    assert channel.drain(10) == [b"old-2", b"new-3"]


def test_drop_newest_keeps_first() -> None:
    channel = BoundedVoiceFrameChannel(
        max_frames=2, max_bytes=1024, drop_policy=DROP_NEWEST
    )
    channel.on_frame(FakeFrame(b"first"))
    channel.on_frame(FakeFrame(b"second"))
    channel.on_frame(FakeFrame(b"third-rejected"))
    assert channel.drain(10) == [b"first", b"second"]


# ---------- 5：丢弃审计 ----------


def test_drop_audit_monotonic_and_bounded() -> None:
    channel = BoundedVoiceFrameChannel(
        max_frames=1, max_bytes=1024, max_audit_events=3
    )
    for i in range(10):
        channel.on_frame(FakeFrame(bytes([65 + i]) * 4))
    snap = channel.snapshot()
    audit = channel.audit_snapshot()
    assert snap["dropped_frame_count"] == 9
    assert snap["dropped_byte_count"] == 36
    assert snap["received_frame_count"] == 10
    # 审计环形有界：最多 3 条，淘汰最旧
    assert len(audit["audit_events"]) == 3
    assert audit["audit_event_count"] == 9  # 全量计数含被淘汰者
    for event in audit["audit_events"]:
        assert event["type"] == "frame_dropped"
        assert event["reason"] in {"buffer_full_oldest_dropped", "frame_over_budget"}
        assert isinstance(event["frame_bytes"], int)
        assert "data" not in event  # 绝不含帧内容


# ---------- 6：drain 消费 ----------


def test_drain_fifo_partial_and_returns_budget() -> None:
    channel = BoundedVoiceFrameChannel(max_frames=8, max_bytes=64)
    channel.on_frame(FakeFrame(b"one"))
    channel.on_frame(FakeFrame(b"two"))
    channel.on_frame(FakeFrame(b"three"))
    first = channel.drain(2)
    assert first == [b"one", b"two"]
    snap = channel.snapshot()
    assert snap["buffered_frames"] == 1
    assert snap["buffered_bytes"] == 5
    second = channel.drain(2)
    assert second == [b"three"]
    assert channel.drain(2) == []  # 空：不抛、不重复交付
    snap = channel.snapshot()
    assert snap["buffered_frames"] == 0 and snap["buffered_bytes"] == 0
    assert snap["drained_frame_count"] == 3
    assert snap["drained_byte_count"] == 3 + 3 + 5


def test_drain_budget_returned_allows_refill() -> None:
    channel = BoundedVoiceFrameChannel(max_frames=2, max_bytes=8)
    channel.on_frame(FakeFrame(b"AAAA"))
    channel.on_frame(FakeFrame(b"BBBB"))  # 满（8 字节）
    channel.on_frame(FakeFrame(b"CCCC"))  # 触发丢弃最旧
    assert channel.drain(10) == [b"BBBB", b"CCCC"]
    channel.on_frame(FakeFrame(b"DDDD"))
    channel.on_frame(FakeFrame(b"EEEE"))
    assert channel.snapshot()["buffered_bytes"] == 8  # 预算已归还


@pytest.mark.parametrize("bad_limit", [0, -1, 1.5, "3", None, True])
def test_drain_rejects_invalid_limit(bad_limit) -> None:
    channel = BoundedVoiceFrameChannel()
    channel.on_frame(FakeFrame(b"X"))
    with pytest.raises(VoiceFrameChannelError):
        channel.drain(bad_limit)


# ---------- 7：无效帧 ----------


def test_invalid_frames_counted_never_raised() -> None:
    channel = BoundedVoiceFrameChannel(max_frames=4, max_bytes=64)
    channel.on_frame(FakeFrame("not-bytes"))  # str 无效
    channel.on_frame(FakeFrame([1, 2, 3]))  # list 无效
    channel.on_frame(FakeFrame(None))  # 缺失
    channel.on_frame(BrokenFrame())  # 属性抛错
    channel.on_frame(FakeFrame(b"ok"))  # 唯一有效
    snap = channel.snapshot()
    assert snap["invalid_frame_count"] == 4
    assert snap["received_frame_count"] == 1
    assert channel.drain(10) == [b"ok"]


# ---------- 8：SplitVoiceSink 分流汇 ----------


def test_split_sink_event_routes_only_to_metrics() -> None:
    metrics = BoundedVoiceFrameSink()
    channel = BoundedVoiceFrameChannel()
    split = SplitVoiceSink(metrics_sink=metrics, frame_channel=channel)

    class FakeEvent:
        type = "track_subscribed"
        room = ROOM
        identity = IDENT
        category = None
        detail = None

    split.on_event(FakeEvent())
    assert metrics.snapshot()["event_count"] == 1
    assert channel.snapshot()["received_frame_count"] == 0


def test_split_sink_frame_fans_out_to_both() -> None:
    metrics = BoundedVoiceFrameSink()
    channel = BoundedVoiceFrameChannel()
    split = SplitVoiceSink(metrics_sink=metrics, frame_channel=channel)
    split.on_frame(FakeFrame(b"AAAA"))
    split.on_frame(FakeFrame(b"BB"))
    assert metrics.snapshot()["frame_count"] == 2  # 计量继续
    snap = channel.snapshot()
    assert snap["received_frame_count"] == 2  # 通道入界
    assert snap["buffered_frames"] == 2


def test_split_sink_constructor_rejects_missing() -> None:
    channel = BoundedVoiceFrameChannel()
    metrics = BoundedVoiceFrameSink()
    with pytest.raises(VoiceFrameChannelError):
        SplitVoiceSink(metrics_sink=None, frame_channel=channel)  # type: ignore[arg-type]
    with pytest.raises(VoiceFrameChannelError):
        SplitVoiceSink(metrics_sink=metrics, frame_channel=None)  # type: ignore[arg-type]


# ---------- 9：fake-only 集成（真实订阅者 + 分流汇） ----------


class _StreamEmit(FakeAudioStream):
    def __init__(self) -> None:
        super().__init__()
        self.stream_sid = "TR-AAA"


def test_integration_subscriber_frames_reach_channel_via_split() -> None:
    metrics = BoundedVoiceFrameSink()
    channel = BoundedVoiceFrameChannel(max_frames=16, max_bytes=4096)
    split = SplitVoiceSink(metrics_sink=metrics, frame_channel=channel)
    room = FakeRoom()
    streams: list[_StreamEmit] = []
    subscriber = ServerVoiceSubscriber(
        room=ROOM,
        identity=IDENT,
        token_provider=lambda: FAKE_JWT,
        room_factory=lambda: room,
        audio_stream_factory=lambda track: _make_stream(streams),
        event_sink=split,
        max_events=32,
    )

    def _make_stream(sink_list: list[_StreamEmit]) -> _StreamEmit:
        stream = _StreamEmit()
        sink_list.append(stream)
        return stream

    payloads = [bytes([65 + i]) * 4 for i in range(5)]

    async def scenario() -> None:
        await subscriber.start()
        room.handlers["track_subscribed"](FakeTrack("TR-AAA"))
        await asyncio.sleep(0)
        stream = streams[0]
        for payload in payloads:
            stream.handler(FakeFrame(payload))
        await subscriber.stop()

    asyncio.run(scenario())
    snap = channel.snapshot()
    assert snap["received_frame_count"] == 5
    assert snap["buffered_bytes"] == 20
    assert metrics.snapshot()["frame_count"] == 5  # 计量同步
    assert channel.drain(16) == payloads  # 字节与顺序逐字一致
    assert channel.snapshot()["buffered_frames"] == 0


# ---------- 10：快照卫生 ----------


def test_snapshots_serializable_without_frame_bytes_or_secrets() -> None:
    channel = BoundedVoiceFrameChannel(max_frames=4, max_bytes=64, max_audit_events=4)
    secret_payload = FAKE_JWT.encode("ascii") + b"-pcm"
    channel.on_frame(FakeFrame(secret_payload))
    channel.on_frame(FakeFrame(b"X" * 64))  # 触发丢弃审计
    for snap in (channel.snapshot(), channel.audit_snapshot()):
        serialized = json.dumps(snap)
        assert "pcm" not in serialized  # 帧字节绝不入快照
        assert JWT_RE.search(serialized) is None
        assert "secret" not in serialized


# ---------- 11：源码卫生（AST+文本） ----------


def test_source_hygiene_module() -> None:
    tree = ast.parse(MODULE_SOURCE)
    allowed = {"__future__", "typing", "collections"}
    # TYPE_CHECKING 域内协议引用（仅类型面，零运行时依赖）
    allowed_domain = {"app.domain.voice_frame_sink", "app.domain.voice_subscription"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in allowed, alias.name
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            root = node.module or ""
            assert (
                root.split(".")[0] in allowed or root in allowed_domain
            ), root
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else ""
            attr = func.attr if isinstance(func, ast.Attribute) else ""
            assert name not in {
                "open", "system", "popen", "run", "sleep", "getenv",
                "ensure_future", "create_task", "start_new_thread",
                "dump", "dumps", "loads",
            }, name
            assert attr not in {
                "system", "popen", "getenv", "ensure_future",
                "create_task", "start_new_thread", "connect", "write",
                "write_bytes", "to_disk", "flush",
            }, attr
    for forbidden in (
        "getenv(", "os.environ", "dotenv", "__import__", "eval(",
        "exec(", "socket", "subprocess", "threading", "asyncio",
        "import livekit", "from livekit", "fastapi", "pathlib",
        "pickle", "tempfile", "shutil",
    ):
        assert forbidden not in MODULE_SOURCE, forbidden
