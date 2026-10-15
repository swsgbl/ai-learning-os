"""M14-272b：FunASR 批式转写适配器（纯域层，fake-only）。

把本地 FunASR 的**批式**转写（整段音频 → 文本；M4-02
``LocalFunAsrAsrProvider.transcribe`` 形态）适配为 M14-270
``VoiceAsrAdapterLike`` **流式协议**（feed 增量缓冲 + finish
一次转写）：

- ``feed(chunk)``：只累积不转写（批式无中间段——返回空列表；
  transcriber 零调用），无效 chunk（非 bytes/bytearray/
  memoryview、data 抛错）跳过计数绝不抛出；
- ``finish()``：拼接缓冲音频 → 恰一次调用注入的同步
  ``transcriber`` → 返回单一 final 段；二次 finish 空缓冲
  返回空列表（幂等——transcriber 仍恰一次）；
- **内存有界**：``max_buffer_bytes`` 上界（默认 8 MiB）——超界
  丢最旧保最新（拼接音频恒 ≤ 上界）；单 chunk 超总预算直接
  丢弃计审计；
- **失败净化**：transcriber 抛错转通用净化错误（守卫后
  ``raise ... from None``——异常链不含端点/凭据/异常文本），
  失败后缓冲清空（不重试）；
- transcriber 为**同步 callable** 注入（``Callable[[bytes], object]``
  ——产物仅需 ``.text`` 属性）；真实 HTTP 客户端包装与线程池
  调度属接线层切片——本切片零网络零真实 ASR。
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.voice_asr_stream import AsrSegmentLike

#: 批式音频缓冲默认上界（8 MiB——内存驻留硬上界）。
DEFAULT_MAX_BUFFER_BYTES = 8 * 1024 * 1024

#: 有效 chunk 载荷类型（与 M14-259/270 同口径）。
_VALID_CHUNK_TYPES = (bytes, bytearray, memoryview)

#: 通用净化消息白名单。
_MESSAGE_INVALID_TRANSCRIBER = "transcriber 非法：必须为可调用对象"
_MESSAGE_MAX_BUFFER = "max_buffer_bytes 必须为正"
_MESSAGE_TRANSCRIBE_FAILED = "批式转写失败（通用净化消息）"


class VoiceFunAsrAdapterError(RuntimeError):
    """适配器错误（消息为通用白名单文案，绝不携带端点/凭据/异常文本）。"""


class _FinalSegment:
    """finish 产出的单一 final 段（结构满足 AsrSegmentLike）。"""

    __slots__ = ("_text",)

    def __init__(self, text: str) -> None:
        self._text = text

    @property
    def text(self) -> str:
        return self._text

    @property
    def is_final(self) -> bool:
        return True


def _coerce_chunk(chunk: object) -> bytes | None:
    """有效 chunk → bytes；无效（类型不符/不可读/负长）→ None。"""
    if not isinstance(chunk, _VALID_CHUNK_TYPES):
        return None
    try:
        payload = bytes(chunk)
    except Exception:  # noqa: BLE001 —— 不可读 = 无效 chunk
        return None
    if not isinstance(payload, bytes):
        return None
    return payload


class FunAsrBatchAdapter:
    """FunASR 批式转写 → 流式 ASR 协议适配器（有界缓冲 + 一次转写）。

    结构满足 ``VoiceAsrAdapterLike``——可直接注入
    ``VoiceAsrStreamSession``；批式语义：feed 零转写、finish
    恰一次。
    """

    __slots__ = (
        "_buffer",
        "_buffered_bytes",
        "_dropped_byte_count",
        "_dropped_chunk_count",
        "_failed_transcribe_count",
        "_finished",
        "_invalid_chunk_count",
        "_max_buffer_bytes",
        "_received_chunk_count",
        "_transcribe_calls",
        "_transcriber",
    )

    def __init__(
        self,
        *,
        transcriber: Callable[[bytes], object],
        max_buffer_bytes: int = DEFAULT_MAX_BUFFER_BYTES,
    ) -> None:
        if not callable(transcriber):
            raise VoiceFunAsrAdapterError(_MESSAGE_INVALID_TRANSCRIBER)
        if (
            not isinstance(max_buffer_bytes, int)
            or isinstance(max_buffer_bytes, bool)
            or max_buffer_bytes <= 0
        ):
            raise VoiceFunAsrAdapterError(_MESSAGE_MAX_BUFFER)
        self._transcriber = transcriber
        self._max_buffer_bytes = max_buffer_bytes
        self._buffer: deque[bytes] = deque()
        self._buffered_bytes = 0
        self._received_chunk_count = 0
        self._invalid_chunk_count = 0
        self._dropped_chunk_count = 0
        self._dropped_byte_count = 0
        self._transcribe_calls = 0
        self._failed_transcribe_count = 0
        self._finished = False

    def feed(self, chunk: bytes) -> list[AsrSegmentLike]:
        """批式累积（不转写）：无效 chunk 跳过计数；超界丢最旧。

        返回空列表（批式无中间段）；transcriber 零调用。
        """
        payload = _coerce_chunk(chunk)
        if payload is None:
            self._invalid_chunk_count += 1
            return []
        self._received_chunk_count += 1
        if len(payload) > self._max_buffer_bytes:
            # 单 chunk 超总预算：直接丢弃（绝不放宽上界）
            self._dropped_chunk_count += 1
            self._dropped_byte_count += len(payload)
            return []
        while (
            self._buffered_bytes + len(payload) > self._max_buffer_bytes
            and self._buffer
        ):
            evicted = self._buffer.popleft()
            self._buffered_bytes -= len(evicted)
            self._dropped_chunk_count += 1
            self._dropped_byte_count += len(evicted)
        self._buffer.append(payload)
        self._buffered_bytes += len(payload)
        return []

    def finish(self) -> list[AsrSegmentLike]:
        """拼接缓冲 → 恰一次批式转写 → 单一 final 段（幂等）。

        幂等：二次 finish（或空缓冲 finish）返回空列表——
        transcriber 恰一次。transcriber 失败转净化错误（链彻底
        抑制），缓冲清空（不重试）。
        """
        if self._finished or not self._buffer:
            self._finished = True
            return []
        self._finished = True
        audio = b"".join(self._buffer)
        self._buffer.clear()
        self._buffered_bytes = 0
        self._transcribe_calls += 1
        failed = False
        try:
            result = self._transcriber(audio)
            text = getattr(result, "text", None)
        except Exception:  # noqa: BLE001 —— 转通用净化错误（链抑制）
            failed = True
        if failed or not isinstance(text, str):
            self._failed_transcribe_count += 1
            # 在 except 块之外 raise：__context__ 亦为 None
            raise VoiceFunAsrAdapterError(_MESSAGE_TRANSCRIBE_FAILED) from None
        return [_FinalSegment(text)]

    def state(self) -> dict[str, object]:
        """适配器状态投影（计数——无音频内容/无文本）。"""
        return {
            "max_buffer_bytes": self._max_buffer_bytes,
            "buffered_chunks": len(self._buffer),
            "buffered_bytes": self._buffered_bytes,
            "received_chunk_count": self._received_chunk_count,
            "invalid_chunk_count": self._invalid_chunk_count,
            "dropped_chunk_count": self._dropped_chunk_count,
            "dropped_byte_count": self._dropped_byte_count,
            "transcribe_calls": self._transcribe_calls,
            "failed_transcribe_count": self._failed_transcribe_count,
            "finished": self._finished,
        }


__all__ = [
    "DEFAULT_MAX_BUFFER_BYTES",
    "FunAsrBatchAdapter",
    "VoiceFunAsrAdapterError",
]
