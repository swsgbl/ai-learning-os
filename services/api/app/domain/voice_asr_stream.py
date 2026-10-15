"""M14-270：流式 ASR 消费链（纯域层、fake-only、可替换 adapter）。

M14-269 之后的下一个窄消费件：把服务端订阅音频流（经
``BoundedVoiceFrameChannel.drain``）交给**可替换 ASR adapter**——
本地 FunASR 是未来的一个 adapter 实现，本切片交付协议与消费链
本体，全部行为用 fake adapter 确定性验证。**不回退浏览器语音
识别（本模块无任何 Web Speech API 路径——服务端消费链是唯一
transcript 来源）**；不做 VAD/barge-in/pause-resume（M14-271）；
不加 HTTP 路由、不进 lifespan、不连真实 ASR 服务、不落盘。

设计契约：
- **transcript 权威来源 = 服务端会话**：段序号 ``seq`` 由会话内
  单调分配（adapter 不参与编号）；transcript 有界环形（超上限
  淘汰最旧、全量计数含被淘汰者）；快照全新拷贝、text 先 JWT
  形态净化再截断到 ``max_segment_chars``。
- **会话绑定**：``session_id`` 复用 M14-268 保守边界（同口径
  正则——不另立宽松边界）；一个会话恰绑一个 adapter 与一个
  通道实例。
- **失败恢复（不扩大不吞错）**：单 chunk ``feed`` 抛错只计
  ``failed_chunk_count``/``error_count``（有界环形错误事件，
  通用净化文案）——其余 chunk 照常消费、已交付段保留、会话
  保持 active；adapter 异常文本绝不进入任何投影。
- **超时边界**：可选注入 ``clock``（monotonic 时间 callable）与
  ``idle_timeout_seconds``——pump 开始时检查距上次进度（收到
  chunk 或产出段）是否超窗，超窗则会话 ``expired``（终结：
  不再 feed、后续 pump 为 API 误用；``finish`` 仍可幂等收尾）；
  未注入 clock 则跳过检查（纯同步可测性优先）。
- **状态机**：active →（finish 冲刷 adapter 尾段）finished；
  active →（idle 超时）expired；finished/expired 后 pump 确定性
  拒绝、finish 幂等 no-op。
- **零副作用**：纯同步、零事件循环/线程/进程/定时器/网络/磁盘；
  运行时导入限定纯标准库（re/collections/dataclasses/typing）+
  域内 TYPE_CHECKING 引用。
"""
from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.domain.voice_session_subscriptions import (
    VoiceSessionSubscriptionError,
    validate_session_id,
)

#: transcript 环形缓冲上限默认值。
DEFAULT_MAX_TRANSCRIPT_SEGMENTS = 64
#: 单段 text 净化后截断上限。
DEFAULT_MAX_SEGMENT_CHARS = 512
#: 错误事件环形上限。
DEFAULT_MAX_ERROR_EVENTS = 16

#: 会话状态常量。
STATUS_ACTIVE = "active"
STATUS_FINISHED = "finished"
STATUS_EXPIRED = "expired"

#: JWT 三段形态（与域内各件同口径）：text 的纵深脱敏。
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")

_REDACTED = "[REDACTED-JWT]"

#: 通用净化消息白名单（API 误用——绝不携带 adapter 异常文本）。
_MESSAGE_INVALID_ADAPTER = "adapter 非法：需要 feed/finish 结构面"
_MESSAGE_SESSION_INVALID = "会话标识非法：仅 [A-Za-z0-9_-]，长度 3–64"
_MESSAGE_INVALID_CHANNEL = "channel 非法：需要 drain 结构面"
_MESSAGE_INVALID_CLOCK = "clock 非法：必须为可调用对象"
_MESSAGE_MAX_SEGMENTS = "max_transcript_segments 必须为正"
_MESSAGE_MAX_CHARS = "max_segment_chars 必须为正"
_MESSAGE_IDLE_TIMEOUT = "idle_timeout_seconds 必须为正"
_MESSAGE_PUMP_AFTER_TERMINAL = "会话已终结（finished/expired），pump 拒绝（请新建会话）"
_MESSAGE_PUMP_LIMIT = "pump limit 必须为正整数"


class VoiceAsrStreamError(RuntimeError):
    """消费链 API 误用（消息为通用白名单文案）。"""


@runtime_checkable
class AsrSegmentLike(Protocol):
    """ASR 段最小面：text + is_final（结构满足即可）。"""

    @property
    def text(self) -> str: ...  # noqa: E704

    @property
    def is_final(self) -> bool: ...  # noqa: E704


@runtime_checkable
class VoiceAsrAdapterLike(Protocol):
    """可替换 ASR adapter 最小面：feed 增量 + finish 冲刷。"""

    def feed(self, chunk: bytes) -> list[AsrSegmentLike]: ...

    def finish(self) -> list[AsrSegmentLike]: ...


@runtime_checkable
class _ChannelLike(Protocol):
    """消费链所需通道最小面（M14-269 BoundedVoiceFrameChannel）。"""

    def drain(self, limit: int) -> list[bytes]: ...


@dataclass(frozen=True)
class AsrPumpResult:
    """单次 pump 投影（计数与状态——不含任何文本内容）。"""

    delivered_chunk_count: int
    new_segment_count: int
    failed_chunk_count: int
    error_count: int
    expired: bool


@dataclass
class _TranscriptEntry:
    """transcript 环形项（内部已净化）。"""

    seq: int
    text: str
    is_final: bool


def _sanitize_text(text: object) -> str:
    """段 text 净化：非 str 置空串；JWT 形态替换后截断。"""
    if not isinstance(text, str):
        return ""
    return _JWT_RE.sub(_REDACTED, text)


class VoiceAsrStreamSession:
    """流式 ASR 消费会话：channel.drain → adapter.feed → 服务端 transcript。

    单调 ``seq`` 由会话分配（权威来源）；feed 失败按 chunk 隔离
    （计数 + 有界错误事件，会话不崩）；idle 超时（可选注入 clock）
    终结会话为 expired；finish 冲刷 adapter 尾段并终结为 finished。
    """

    __slots__ = (
        "_adapter",
        "_channel",
        "_clock",
        "_error_count",
        "_errors",
        "_idle_timeout",
        "_last_progress",
        "_max_chars",
        "_max_segments",
        "_next_seq",
        "_session_id",
        "_status",
        "_total_segments",
        "_transcript",
    )

    def __init__(
        self,
        session_id: object,
        *,
        adapter: VoiceAsrAdapterLike,
        channel: _ChannelLike,
        clock: Callable[[], float] | None = None,
        idle_timeout_seconds: float | None = None,
        max_transcript_segments: int = DEFAULT_MAX_TRANSCRIPT_SEGMENTS,
        max_segment_chars: int = DEFAULT_MAX_SEGMENT_CHARS,
    ) -> None:
        try:
            self._session_id = validate_session_id(session_id)
        except VoiceSessionSubscriptionError:
            raise VoiceAsrStreamError(_MESSAGE_SESSION_INVALID) from None
        if not isinstance(adapter, VoiceAsrAdapterLike):
            raise VoiceAsrStreamError(_MESSAGE_INVALID_ADAPTER)
        if not isinstance(channel, _ChannelLike):
            raise VoiceAsrStreamError(_MESSAGE_INVALID_CHANNEL)
        if clock is not None and not callable(clock):
            raise VoiceAsrStreamError(_MESSAGE_INVALID_CLOCK)
        if idle_timeout_seconds is not None and (
            not isinstance(idle_timeout_seconds, (int, float))
            or isinstance(idle_timeout_seconds, bool)
            or idle_timeout_seconds <= 0
        ):
            raise VoiceAsrStreamError(_MESSAGE_IDLE_TIMEOUT)
        if (
            not isinstance(max_transcript_segments, int)
            or isinstance(max_transcript_segments, bool)
            or max_transcript_segments <= 0
        ):
            raise VoiceAsrStreamError(_MESSAGE_MAX_SEGMENTS)
        if (
            not isinstance(max_segment_chars, int)
            or isinstance(max_segment_chars, bool)
            or max_segment_chars <= 0
        ):
            raise VoiceAsrStreamError(_MESSAGE_MAX_CHARS)
        self._adapter = adapter
        self._channel = channel
        self._clock = clock
        self._idle_timeout = idle_timeout_seconds
        self._max_segments = max_transcript_segments
        self._max_chars = max_segment_chars
        self._transcript: deque[_TranscriptEntry] = deque(maxlen=max_transcript_segments)
        self._total_segments = 0
        self._next_seq = 0
        self._status = STATUS_ACTIVE
        self._error_count = 0
        self._errors: deque[dict[str, object]] = deque(maxlen=DEFAULT_MAX_ERROR_EVENTS)
        self._last_progress: float | None = clock() if clock is not None else None

    # --- 内部：段入 transcript（权威编号 + 净化） ---

    def _accept_segments(self, segments: Any) -> int:
        accepted = 0
        for segment in segments:
            text = _sanitize_text(getattr(segment, "text", None))[: self._max_chars]
            is_final = bool(getattr(segment, "is_final", False))
            self._transcript.append(
                _TranscriptEntry(seq=self._next_seq, text=text, is_final=is_final)
            )
            self._next_seq += 1
            self._total_segments += 1
            accepted += 1
        return accepted

    def _record_feed_failure(self) -> None:
        self._error_count += 1
        self._errors.append(
            {"type": "chunk_feed_failed", "detail": "adapter feed 失败（通用净化文案）"}
        )

    # --- 消费路径 ---

    def pump(self, limit: int) -> AsrPumpResult:
        """拉取通道帧并送入 adapter（顺序一致；失败按 chunk 隔离）。

        注入 clock 且 idle 超窗时会话 expired（本次零消费零 feed）；
        终结（finished/expired）后调用为 API 误用。limit 必须为
        正整数。
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise VoiceAsrStreamError(_MESSAGE_PUMP_LIMIT)
        if self._status != STATUS_ACTIVE:
            raise VoiceAsrStreamError(_MESSAGE_PUMP_AFTER_TERMINAL)
        # 超时边界：距上次进度超窗 → expired（不再 feed）
        if self._clock is not None and self._idle_timeout is not None:
            now = self._clock()
            if self._last_progress is not None and (
                now - self._last_progress > self._idle_timeout
            ):
                self._status = STATUS_EXPIRED
                return AsrPumpResult(0, 0, 0, self._error_count, expired=True)
        chunks = self._channel.drain(limit)
        delivered = 0
        failed = 0
        new_segments = 0
        for chunk in chunks:
            delivered += 1
            try:
                produced = self._adapter.feed(chunk)
            except Exception:  # noqa: BLE001 —— 单 chunk 失败隔离（不吞会话）
                failed += 1
                self._record_feed_failure()
                continue
            new_segments += self._accept_segments(produced)
        if chunks or new_segments:
            self._last_progress = self._clock() if self._clock is not None else None
        return AsrPumpResult(
            delivered_chunk_count=delivered,
            new_segment_count=new_segments,
            failed_chunk_count=failed,
            error_count=self._error_count,
            expired=False,
        )

    def finish(self) -> int:
        """冲刷 adapter 尾段并终结会话（幂等：终结后 no-op）。

        返回本次冲刷新增段数；expired 后仍可收尾（adapter 内可能
        有已缓冲内容），语义与 finished 相同。
        """
        if self._status == STATUS_FINISHED:
            return 0
        self._status = STATUS_FINISHED
        try:
            tail = self._adapter.finish()
        except Exception:  # noqa: BLE001 —— 冲刷失败计错误事件，不掩盖不扩大
            self._record_feed_failure()
            return 0
        return self._accept_segments(tail)

    # --- 投影 ---

    def transcript_snapshot(self) -> dict[str, object]:
        """服务端权威 transcript 快照（全新拷贝；text 已净化截断）。"""
        return {
            "session_id": self._session_id,
            "max_transcript_segments": self._max_segments,
            "segment_count": self._total_segments,
            "segments": [
                {"seq": entry.seq, "text": entry.text, "is_final": entry.is_final}
                for entry in self._transcript
            ],
        }

    def state(self) -> dict[str, object]:
        """会话状态投影（计数与状态——无文本内容/无异常文本）。"""
        return {
            "session_id": self._session_id,
            "status": self._status,
            "total_segment_count": self._total_segments,
            "error_count": self._error_count,
            "idle_timeout_seconds": self._idle_timeout,
        }


__all__ = [
    "DEFAULT_MAX_SEGMENT_CHARS",
    "DEFAULT_MAX_TRANSCRIPT_SEGMENTS",
    "STATUS_ACTIVE",
    "STATUS_EXPIRED",
    "STATUS_FINISHED",
    "AsrPumpResult",
    "AsrSegmentLike",
    "VoiceAsrAdapterLike",
    "VoiceAsrStreamError",
    "VoiceAsrStreamSession",
]
