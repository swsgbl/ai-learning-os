"""M14-272b FunASR 批式转写适配器契约测试（fake-only，零真实 ASR/零网络）。

`FunAsrBatchAdapter` 把本地 FunASR 的**批式**转写（整段音频 →
文本）适配为 M14-270 `VoiceAsrAdapterLike` 流式协议（feed 增量
缓冲 + finish 一次转写）。transcriber 为同步 callable 注入——
真实 HTTP 接线（httpx 同步包装 + 线程池调度）属接线层切片，
本切片全 fake。

覆盖：
1  结构协议：满足 VoiceAsrAdapterLike（feed/finish）；
2  feed 批式语义：累积不转写（transcriber 零调用）、返回空段
   列表（无中间产物）、无效 chunk（str/None/抛错 data）跳过
   计数不抛；
3  finish 批式转写：恰一次调用、拼接顺序逐字一致、返回单一
   final 段（text 映射 transcriber 产物、is_final=True）；
4  内存有界：max_buffer_bytes 上界（超界丢最旧保最新——拼接
   音频恒 ≤ 上界）、单 chunk 超总预算直接丢弃计审计；
5  幂等 finish：二次 finish 空缓冲返回空列表（transcriber 仍
   恰一次）；
6  失败净化：transcriber 抛错转通用净化错误（from None——
   异常链不含凭据/端点/异常文本）；
7  构造校验：transcriber 非 callable / max_buffer_bytes 非正
   确定性拒绝；
8  与 M14-270 消费链集成：真实 VoiceAsrStreamSession + 通道 →
   feed 帧缓冲 → finish 转写进服务端 transcript（全链 fake）；
9  源码卫生（AST+文本）：纯标准库 + 域内协议引用；无网络/
   httpx/文件/线程/asyncio。
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from app.domain.voice_asr_stream import (
    VoiceAsrAdapterLike,
    VoiceAsrStreamSession,
)
from app.domain.voice_frame_channel import BoundedVoiceFrameChannel
from app.domain.voice_funasr_adapter import (
    FunAsrBatchAdapter,
    VoiceFunAsrAdapterError,
)

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = (
    REPO_ROOT / "services" / "api" / "app" / "domain" / "voice_funasr_adapter.py"
)
MODULE_SOURCE = MODULE_PATH.read_text(encoding="utf-8")


class FakeFrame:
    def __init__(self, data: object) -> None:
        self._data = data

    @property
    def data(self) -> object:
        return self._data


class BrokenFrame:
    @property
    def data(self) -> object:
        raise RuntimeError("frame exploded")


class FakeTranscription:
    def __init__(self, text: str) -> None:
        self.text = text


class RecordingTranscriber:
    """fake 同步转写器：记录调用与拼接音频。"""

    def __init__(self, text: str = "转写结果文本") -> None:
        self.text = text
        self.calls: list[bytes] = []

    def __call__(self, audio: bytes) -> FakeTranscription:
        self.calls.append(audio)
        return FakeTranscription(self.text)


class ExplodingTranscriber:
    def __call__(self, audio: bytes) -> Any:
        raise RuntimeError("funasr exploded with secret endpoint detail")


# ---------- 7：构造校验 ----------


def test_constructor_rejects_invalid() -> None:
    with pytest.raises(VoiceFunAsrAdapterError):
        FunAsrBatchAdapter(transcriber="not-callable")  # type: ignore[arg-type]
    with pytest.raises(VoiceFunAsrAdapterError):
        FunAsrBatchAdapter(transcriber=RecordingTranscriber(), max_buffer_bytes=0)
    with pytest.raises(VoiceFunAsrAdapterError):
        FunAsrBatchAdapter(transcriber=RecordingTranscriber(), max_buffer_bytes=-1)


def test_satisfies_stream_adapter_protocol() -> None:
    adapter = FunAsrBatchAdapter(transcriber=RecordingTranscriber())
    assert isinstance(adapter, VoiceAsrAdapterLike)


# ---------- 2：feed 批式语义 ----------


def test_feed_accumulates_without_transcribing() -> None:
    transcriber = RecordingTranscriber()
    adapter = FunAsrBatchAdapter(transcriber=transcriber)
    result_a = adapter.feed(b"AAAA")
    result_b = adapter.feed(bytearray(b"BB"))
    result_c = adapter.feed(memoryview(b"C"))
    assert result_a == [] and result_b == [] and result_c == []  # 无中间段
    assert transcriber.calls == []  # 零转写调用
    state = adapter.state()
    assert state["buffered_bytes"] == 7
    assert state["received_chunk_count"] == 3


def test_invalid_chunks_skipped_counted_never_raised() -> None:
    transcriber = RecordingTranscriber()
    adapter = FunAsrBatchAdapter(transcriber=transcriber)
    adapter.feed("not-bytes")  # type: ignore[arg-type]
    adapter.feed(None)  # type: ignore[arg-type]
    adapter.feed(BrokenFrame().__class__ and b"")  # 空 bytes 合法（0 字节）
    state = adapter.state()
    assert state["invalid_chunk_count"] == 2
    assert state["received_chunk_count"] == 1


def test_broken_data_attribute_skipped() -> None:
    adapter = FunAsrBatchAdapter(transcriber=RecordingTranscriber())

    class _ChunkObj:
        data = property(lambda self: (_ for _ in ()).throw(RuntimeError("x")))

    adapter.feed(_ChunkObj())  # type: ignore[arg-type]
    assert adapter.state()["invalid_chunk_count"] == 1


# ---------- 3：finish 批式转写 ----------


def test_finish_transcribes_once_in_order_single_final_segment() -> None:
    transcriber = RecordingTranscriber(text="你好世界")
    adapter = FunAsrBatchAdapter(transcriber=transcriber)
    adapter.feed(b"AA")
    adapter.feed(b"BBB")
    adapter.feed(b"C")
    segments = adapter.finish()
    assert len(transcriber.calls) == 1  # 恰一次
    assert transcriber.calls[0] == b"AABBBC"  # 拼接顺序逐字一致
    assert len(segments) == 1  # 单一 final 段
    assert segments[0].text == "你好世界"
    assert segments[0].is_final is True
    state = adapter.state()
    assert state["transcribe_calls"] == 1
    assert state["buffered_bytes"] == 0  # 交付后缓冲清空


def test_finish_idempotent_second_returns_empty() -> None:
    transcriber = RecordingTranscriber()
    adapter = FunAsrBatchAdapter(transcriber=transcriber)
    adapter.feed(b"XX")
    first = adapter.finish()
    assert len(first) == 1
    second = adapter.finish()  # 空缓冲
    assert second == []
    assert len(transcriber.calls) == 1  # 仍恰一次
    assert adapter.state()["transcribe_calls"] == 1


# ---------- 4：内存有界 ----------


def test_buffer_cap_drops_oldest_keeps_newest() -> None:
    transcriber = RecordingTranscriber()
    adapter = FunAsrBatchAdapter(transcriber=transcriber, max_buffer_bytes=8)
    adapter.feed(b"AAAA")  # 4
    adapter.feed(b"BBBB")  # 8 = 满
    adapter.feed(b"CC")  # 触发丢最旧（AAAA）→ 缓冲 BBBBCC(6)
    state = adapter.state()
    assert state["buffered_bytes"] == 6
    assert state["dropped_chunk_count"] == 1
    assert state["dropped_byte_count"] == 4
    adapter.finish()
    assert transcriber.calls[0] == b"BBBBCC"  # 最旧已丢、保最新


def test_single_chunk_over_budget_rejected() -> None:
    transcriber = RecordingTranscriber()
    adapter = FunAsrBatchAdapter(transcriber=transcriber, max_buffer_bytes=8)
    adapter.feed(b"X" * 16)
    state = adapter.state()
    assert state["buffered_bytes"] == 0
    assert state["dropped_chunk_count"] == 1
    assert state["dropped_byte_count"] == 16


@pytest.mark.parametrize("chunks", [[b"A" * 4] * 10, [b"B" * 2] * 20])
def test_memory_bound_holds_under_pressure(chunks) -> None:
    adapter = FunAsrBatchAdapter(
        transcriber=RecordingTranscriber(), max_buffer_bytes=16
    )
    for chunk in chunks:
        adapter.feed(chunk)
        assert adapter.state()["buffered_bytes"] <= 16  # 上界恒成立


# ---------- 6：失败净化 ----------


def test_transcriber_failure_sanitized_chain_suppressed() -> None:
    adapter = FunAsrBatchAdapter(transcriber=ExplodingTranscriber())
    adapter.feed(b"AA")
    with pytest.raises(VoiceFunAsrAdapterError) as ei:
        adapter.finish()
    error = ei.value
    text = str(error)
    assert "exploded" not in text  # 异常文本不泄漏
    assert "endpoint" not in text
    assert error.__cause__ is None and error.__context__ is None  # 链抑制
    assert adapter.state()["failed_transcribe_count"] == 1
    assert adapter.state()["buffered_bytes"] == 0  # 失败后清缓冲（不重试）


# ---------- 8：与 M14-270 消费链集成 ----------


def test_integration_stream_session_with_batch_adapter() -> None:
    transcriber = RecordingTranscriber(text="集成转写")
    channel = BoundedVoiceFrameChannel(max_frames=16, max_bytes=65536)
    adapter = FunAsrBatchAdapter(transcriber=transcriber)
    session = VoiceAsrStreamSession(
        "sess-integration-01", adapter=adapter, channel=channel
    )
    for payload in (b"P1", b"P2", b"P3"):
        channel.on_frame(FakeFrame(payload))
    result = session.pump(10)
    assert result.delivered_chunk_count == 3
    assert result.new_segment_count == 0  # 批式：feed 无中间段
    assert transcriber.calls == []  # 零转写
    flushed = session.finish()
    assert flushed == 1  # finish 冲刷单一 final 段
    assert transcriber.calls == [b"P1P2P3"]  # 拼接一致
    transcript = session.transcript_snapshot()
    assert [seg["text"] for seg in transcript["segments"]] == ["集成转写"]
    assert transcript["segments"][0]["is_final"] is True


# ---------- 9：源码卫生 ----------


def test_source_hygiene_module() -> None:
    tree = ast.parse(MODULE_SOURCE)
    allowed_roots = {"__future__", "typing", "collections"}
    allowed_domain = {"app.domain.voice_asr_stream"}
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
                "create_task", "start_new_thread", "connect", "post",
                "request", "write",
            }, attr
    for forbidden in (
        "getenv(", "os.environ", "dotenv", "__import__", "eval(",
        "socket", "subprocess", "threading", "asyncio", "httpx",
        "urllib", "requests", "import livekit", "from livekit",
        "fastapi", "pathlib",
    ):
        assert forbidden not in MODULE_SOURCE, forbidden
