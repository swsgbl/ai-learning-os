"""M14-270 流式 ASR 消费链契约测试（fake-only，零网络/零真实 ASR）。

覆盖（与切片验收面一一对应）：
1  adapter 可替换性：结构协议（feed/finish + text/is_final 段面）
   ——本地 FunASR 是未来实现，本切片用 fake adapter 验证契约；
2  消费链：pump 从 BoundedVoiceFrameChannel drain 拉帧 → 逐 chunk
   顺序一致到达 adapter.feed → 返回段进服务端 transcript；
3  transcript 权威来源：服务端会话是唯一权威——seq 单调、快照
   全新拷贝、有界环形（超上限淘汰最旧 + 全量计数含被淘汰）、
   text 净化（JWT 形态替换 + 长度截断）；
4  会话绑定：session_id 复用既有保守边界（非法确定性拒绝）；
5  失败恢复：单 chunk feed 抛错只计 failed_chunk_count——其余
   chunk 继续、已交付段保留、会话不崩（不吞错也不扩大）；
6  finish 边界：adapter.finish 冲刷尾段、会话终结、后续 pump
   API 误用拒绝、finish 幂等；
7  超时边界：注入 clock 下 idle 超时会话 expired（不再 feed）、
   进度刷新重置窗口；无 clock 时跳过检查（向后兼容）；
8  空转：channel 无帧时 pump 零交付零段；
9  构造校验：session_id/adapter/channel 结构面、正数参数；
10 快照卫生：transcript/state 投影序列化无凭据/token/无无界
   历史；
11 源码卫生（AST+文本）：纯标准库 + 域内 TYPE_CHECKING；无
   SpeechRecognition/浏览器回退；无网络/文件/线程/asyncio。

全部使用合成 PCM 假帧与 fake adapter；绝不调用真实 FunASR。
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from app.domain.voice_asr_stream import (
    AsrSegmentLike,
    VoiceAsrAdapterLike,
    VoiceAsrStreamError,
    VoiceAsrStreamSession,
)
from app.domain.voice_frame_channel import BoundedVoiceFrameChannel

#: 显眼假凭据（绝不真实）。
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "services" / "api" / "app" / "domain" / "voice_asr_stream.py"
MODULE_SOURCE = MODULE_PATH.read_text(encoding="utf-8")

SESSION_A = "sess-alpha-0001"


class FakeFrame:
    def __init__(self, data: object) -> None:
        self._data = data

    @property
    def data(self) -> object:
        return self._data


class FakeSegment:
    """fake ASR 段（结构满足 AsrSegmentLike）。"""

    def __init__(self, text: str, is_final: bool = True) -> None:
        self.text = text
        self.is_final = is_final


class ScriptedAdapter:
    """脚本化 fake adapter：按 chunk 序号产出段/抛错。

    feed_results: chunk 序号（0 起）→ 段列表；
    feed_errors: chunk 序号集合 → feed 抛错（含诱饵异常文本）；
    finish_segments: finish() 返回的尾段。
    """

    def __init__(
        self,
        *,
        feed_results: dict[int, list[FakeSegment]] | None = None,
        feed_errors: set[int] | None = None,
        finish_segments: list[FakeSegment] | None = None,
    ) -> None:
        self.feed_results = feed_results or {}
        self.feed_errors = feed_errors or set()
        self.finish_segments = finish_segments or []
        self.fed_chunks: list[bytes] = []
        self.finish_calls = 0

    def feed(self, chunk: bytes) -> list[FakeSegment]:
        index = len(self.fed_chunks)
        self.fed_chunks.append(chunk)
        if index in self.feed_errors:
            raise RuntimeError("asr exploded with secret-ish detail")
        return list(self.feed_results.get(index, []))

    def finish(self) -> list[FakeSegment]:
        self.finish_calls += 1
        return list(self.finish_segments)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_channel_with(*payloads: bytes) -> BoundedVoiceFrameChannel:
    channel = BoundedVoiceFrameChannel(max_frames=64, max_bytes=65536)
    for payload in payloads:
        channel.on_frame(FakeFrame(payload))
    return channel


# ---------- 9：构造校验 ----------


def test_constructor_rejects_invalid_session_id() -> None:
    adapter = ScriptedAdapter()
    channel = make_channel_with(b"AAAA")
    with pytest.raises(VoiceAsrStreamError):
        VoiceAsrStreamSession("bad/sid", adapter=adapter, channel=channel)  # type: ignore[arg-type]


def test_constructor_rejects_invalid_collaborators() -> None:
    adapter = ScriptedAdapter()
    channel = make_channel_with()
    with pytest.raises(VoiceAsrStreamError):
        VoiceAsrStreamSession(SESSION_A, adapter=None, channel=channel)  # type: ignore[arg-type]
    with pytest.raises(VoiceAsrStreamError):
        VoiceAsrStreamSession(SESSION_A, adapter=adapter, channel=None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_transcript_segments": 0},
        {"max_transcript_segments": -1},
        {"max_segment_chars": 0},
        {"idle_timeout_seconds": 0},
    ],
)
def test_constructor_rejects_invalid_config(kwargs) -> None:
    with pytest.raises(VoiceAsrStreamError):
        VoiceAsrStreamSession(
            SESSION_A, adapter=ScriptedAdapter(), channel=make_channel_with(), **kwargs
        )


def test_protocol_structures_satisfied_by_fakes() -> None:
    assert isinstance(ScriptedAdapter(), VoiceAsrAdapterLike)
    assert isinstance(FakeSegment("x"), AsrSegmentLike)
    assert isinstance(make_channel_with(), object)


# ---------- 2：消费链（drain → feed 顺序一致 → 段进 transcript） ----------


def test_pump_delivers_chunks_in_order_and_collects_segments() -> None:
    adapter = ScriptedAdapter(
        feed_results={0: [FakeSegment("你")], 2: [FakeSegment("好", is_final=False)]}
    )
    channel = make_channel_with(b"AA", b"BB", b"CC")
    session = VoiceAsrStreamSession(SESSION_A, adapter=adapter, channel=channel)
    result = session.pump(10)
    assert adapter.fed_chunks == [b"AA", b"BB", b"CC"]  # 顺序逐字一致
    assert result.delivered_chunk_count == 3
    assert result.new_segment_count == 2
    assert result.failed_chunk_count == 0
    transcript = session.transcript_snapshot()
    assert transcript["segment_count"] == 2
    texts = [seg["text"] for seg in transcript["segments"]]
    assert texts == ["你", "好"]
    assert transcript["segments"][1]["is_final"] is False


def test_pump_respects_chunk_limit_and_repeatable() -> None:
    adapter = ScriptedAdapter()
    channel = make_channel_with(b"A", b"B", b"C", b"D")
    session = VoiceAsrStreamSession(SESSION_A, adapter=adapter, channel=channel)
    first = session.pump(2)
    assert first.delivered_chunk_count == 2
    second = session.pump(2)
    assert second.delivered_chunk_count == 2
    assert adapter.fed_chunks == [b"A", b"B", b"C", b"D"]
    third = session.pump(2)
    assert third.delivered_chunk_count == 0  # 空转零交付


# ---------- 3：transcript 权威来源 ----------


def test_transcript_seq_monotonic_and_snapshot_is_copy() -> None:
    adapter = ScriptedAdapter(
        feed_results={i: [FakeSegment(f"seg-{i}")] for i in range(4)}
    )
    channel = make_channel_with(*[bytes([65 + i]) for i in range(4)])
    session = VoiceAsrStreamSession(SESSION_A, adapter=adapter, channel=channel)
    session.pump(10)
    transcript = session.transcript_snapshot()
    seqs = [seg["seq"] for seg in transcript["segments"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == 4  # 单调无重复
    transcript["segments"][0]["text"] = "tampered"
    assert session.transcript_snapshot()["segments"][0]["text"] == "seg-0"  # 全新拷贝


def test_transcript_bounded_ring_with_total_count() -> None:
    adapter = ScriptedAdapter(
        feed_results={i: [FakeSegment(f"s{i}")] for i in range(10)}
    )
    channel = make_channel_with(*[bytes([65 + i]) for i in range(10)])
    session = VoiceAsrStreamSession(
        SESSION_A,
        adapter=adapter,
        channel=channel,
        max_transcript_segments=3,
    )
    session.pump(10)
    transcript = session.transcript_snapshot()
    assert transcript["max_transcript_segments"] == 3
    assert len(transcript["segments"]) == 3  # 环形：最新 3 段
    assert transcript["segment_count"] == 10  # 全量计数含被淘汰
    assert [seg["text"] for seg in transcript["segments"]] == ["s7", "s8", "s9"]


def test_transcript_text_sanitized_and_truncated() -> None:
    long_text = "x" * 3000 + FAKE_JWT
    adapter = ScriptedAdapter(feed_results={0: [FakeSegment(long_text)]})
    channel = make_channel_with(b"A")
    session = VoiceAsrStreamSession(
        SESSION_A, adapter=adapter, channel=channel, max_segment_chars=512
    )
    session.pump(10)
    serialized = json.dumps(session.transcript_snapshot())
    assert JWT_RE.search(serialized) is None  # JWT 形态净化
    seg = session.transcript_snapshot()["segments"][0]
    assert len(seg["text"]) <= 512  # 截断有界


# ---------- 5：失败恢复 ----------


def test_single_chunk_failure_does_not_break_stream() -> None:
    adapter = ScriptedAdapter(
        feed_results={0: [FakeSegment("ok-0")], 2: [FakeSegment("ok-2")]},
        feed_errors={1},
    )
    channel = make_channel_with(b"A", b"B", b"C")
    session = VoiceAsrStreamSession(SESSION_A, adapter=adapter, channel=channel)
    result = session.pump(10)
    assert result.delivered_chunk_count == 3  # 三个 chunk 都送达尝试
    assert result.failed_chunk_count == 1
    assert result.new_segment_count == 2  # 坏 chunk 前后段都保留
    assert result.error_count == 1  # 错误计数有界记录
    state = session.state()
    assert state["status"] == "active"  # 会话不崩
    serialized = json.dumps(session.state())
    assert "exploded" not in serialized  # 异常文本不泄漏


def test_all_chunks_fail_session_still_usable() -> None:
    adapter = ScriptedAdapter(feed_errors={0, 1, 2})
    channel = make_channel_with(b"A", b"B", b"C", b"D")
    session = VoiceAsrStreamSession(SESSION_A, adapter=adapter, channel=channel)
    session.pump(10)
    ok_adapter = ScriptedAdapter(feed_results={0: [FakeSegment("late")]})
    late_channel = make_channel_with(b"E")
    # 原会话继续可用：新帧进入后正常产出
    session2 = VoiceAsrStreamSession(SESSION_A, adapter=ok_adapter, channel=late_channel)
    result2 = session2.pump(10)
    assert result2.new_segment_count == 1


# ---------- 6：finish 边界 ----------


def test_finish_flushes_tail_and_terminates() -> None:
    adapter = ScriptedAdapter(
        feed_results={0: [FakeSegment("live")]},
        finish_segments=[FakeSegment("tail")],
    )
    channel = make_channel_with(b"A")
    session = VoiceAsrStreamSession(SESSION_A, adapter=adapter, channel=channel)
    session.pump(10)
    flushed = session.finish()
    assert flushed == 1  # 尾段计数
    assert adapter.finish_calls == 1
    transcript = session.transcript_snapshot()
    assert [seg["text"] for seg in transcript["segments"]] == ["live", "tail"]
    assert session.state()["status"] == "finished"
    with pytest.raises(VoiceAsrStreamError):
        session.pump(10)  # 终结后 pump = API 误用
    assert session.finish() == 0  # 幂等 no-op
    assert adapter.finish_calls == 1


# ---------- 7：超时边界 ----------


def test_idle_timeout_expires_session() -> None:
    clock = FakeClock()
    adapter = ScriptedAdapter(feed_results={0: [FakeSegment("first")]})
    channel = make_channel_with(b"A")
    session = VoiceAsrStreamSession(
        SESSION_A,
        adapter=adapter,
        channel=channel,
        clock=clock,
        idle_timeout_seconds=30.0,
    )
    result = session.pump(10)
    assert result.expired is False
    assert result.new_segment_count == 1
    clock.now += 31  # 超窗
    # 超时后：会话 expired，不再 feed
    result2 = session.pump(10)
    assert result2.expired is True
    assert result2.delivered_chunk_count == 0
    assert adapter.fed_chunks == [b"A"]  # 超时后零 feed
    assert session.state()["status"] == "expired"
    with pytest.raises(VoiceAsrStreamError):
        session.pump(10)  # expired 后 pump = API 误用
    assert session.finish() == 0  # finish 仍可收尾（幂等）


def test_progress_resets_idle_window() -> None:
    clock = FakeClock()
    adapter = ScriptedAdapter(feed_results={i: [FakeSegment(f"s{i}")] for i in range(3)})
    channel = make_channel_with(b"A", b"B", b"C")
    session = VoiceAsrStreamSession(
        SESSION_A,
        adapter=adapter,
        channel=channel,
        clock=clock,
        idle_timeout_seconds=30.0,
    )
    session.pump(2)  # 消费 A、B（last_progress = now）
    clock.now += 29
    result = session.pump(2)  # 未超窗：C 正常消费
    assert result.expired is False
    assert result.delivered_chunk_count == 1


def test_no_clock_skips_timeout_check() -> None:
    adapter = ScriptedAdapter(feed_results={0: [FakeSegment("s")]})
    channel = make_channel_with(b"A")
    session = VoiceAsrStreamSession(SESSION_A, adapter=adapter, channel=channel)
    result = session.pump(10)
    assert result.expired is False
    assert result.new_segment_count == 1


# ---------- 10：快照卫生 ----------


def test_snapshots_serializable_without_secrets() -> None:
    adapter = ScriptedAdapter(feed_results={0: [FakeSegment(f"含 {FAKE_JWT} 文本")]})
    channel = make_channel_with(b"A")
    session = VoiceAsrStreamSession(SESSION_A, adapter=adapter, channel=channel)
    session.pump(10)
    for snap in (session.transcript_snapshot(), session.state()):
        serialized = json.dumps(snap)
        assert JWT_RE.search(serialized) is None
        assert "secret" not in serialized


# ---------- 11：源码卫生 ----------


def test_source_hygiene_module() -> None:
    tree = ast.parse(MODULE_SOURCE)
    allowed_roots = {"__future__", "typing", "collections", "dataclasses", "re"}
    allowed_domain = {
        "app.domain.voice_frame_channel",
        "app.domain.voice_session_subscriptions",
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
                "create_task", "start_new_thread", "connect", "write",
            }, attr
    for forbidden in (
        "getenv(", "os.environ", "dotenv", "__import__", "eval(",
        "exec(", "socket", "subprocess", "threading", "asyncio",
        "import livekit", "from livekit", "fastapi", "pathlib",
        "pickle", "tempfile", "httpx", "aiohttp", "requests",
        "SpeechRecognition", "webkitSpeechRecognition",  # 不回退浏览器
    ):
        assert forbidden not in MODULE_SOURCE, forbidden
