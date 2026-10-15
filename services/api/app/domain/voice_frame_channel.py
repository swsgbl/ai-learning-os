"""M14-269：有界音频帧管道（纯域层、零接线/零网络/零落盘）。

M14-268 之后的下一个窄基础件：把订阅者帧路径（M14-255/256 的
``event_sink.on_frame`` 直转）接入**有界可消费管道**——
``BoundedVoiceFrameChannel`` 以帧数/字节**双上限**缓冲 PCM 帧
（任一到顶即触发丢弃策略），``SplitVoiceSink`` 把订阅者的
``on_event``/``on_frame`` 分流：事件与帧计量继续进
``BoundedVoiceFrameSink``（M14-260），帧字节同时进入通道供后续
消费者（M14-270 流式 ASR）``drain`` 拉取。**这不是 ASR/VAD/
barge-in**：不做转写、不做语音活动检测、不加 HTTP 路由、不进
lifespan、不连真实房间服务。

安全与内存契约：
- **内存上界可证明**：任意时刻缓冲帧数 ≤ ``max_frames`` 且缓冲
  字节 ≤ ``max_bytes``（构造校验均为正；单帧超过总预算即拒绝
  入界并审计——绝不为凑预算放宽上界）；``drain`` 取走即归还
  预算；
- **丢弃确定性**：``drop_oldest``（默认——满时淘汰最旧、新帧
  入界，缓冲恒为最新 N）或 ``drop_newest``（满时拒绝新帧，缓冲
  恒为最先 N）；每次丢弃计数 + 有界环形审计事件（只含类型/
  原因常量/字节数——绝不含帧内容/凭据/token）；
- **零落盘（不可落盘）**：无任何文件 I/O——内存明文音频只在
  缓冲与 drain 交付物中短暂驻留；本切片不提供任何持久化路径
  （比"隐私配置允许才落盘"更保守：根本不存在落盘代码面）；
- **无效帧同口径**：与 M14-260 计量汇一致——data 仅
  bytes/bytearray/memoryview 有效，缺失/属性抛错/其他类型一律
  计 invalid_frame_count，绝不抛出、绝不做任何 I/O 或调度；
- **纯同步零副作用**：零事件循环/线程/进程/定时器/网络/磁盘；
  snapshot/audit_snapshot 每次全新拷贝，不含帧字节。

结构契约：``SplitVoiceSink`` 结构满足
``app.domain.voice_subscription.EventSinkLike``（on_event/on_frame）
——可直接作为 ``ServerVoiceSubscriber`` 的 event_sink 注入。
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.domain.voice_frame_sink import BoundedVoiceFrameSink
    from app.domain.voice_subscription import AudioFrameLike

#: 默认缓冲帧数上限。
DEFAULT_MAX_FRAMES = 256
#: 默认缓冲字节上限（512 KiB——内存驻留硬上界）。
DEFAULT_MAX_BYTES = 512 * 1024
#: 审计事件环形缓冲上限默认值。
DEFAULT_MAX_AUDIT_EVENTS = 32

#: 丢弃策略：满时淘汰最旧（缓冲恒为最新 N）。
DROP_OLDEST = "drop_oldest"
#: 丢弃策略：满时拒绝新帧（缓冲恒为最先 N）。
DROP_NEWEST = "drop_newest"
_DROP_POLICIES = (DROP_OLDEST, DROP_NEWEST)

#: 有效帧载荷类型（与 M14-260 计量汇同口径）。
_VALID_FRAME_DATA_TYPES = (bytes, bytearray, memoryview)

#: 审计事件类型常量。
EVENT_FRAME_DROPPED = "frame_dropped"
#: 丢弃原因常量（审计事件 reason 字段——绝不含帧内容）。
REASON_OLDEST_DROPPED = "buffer_full_oldest_dropped"
REASON_NEWEST_REJECTED = "buffer_full_newest_rejected"
REASON_OVER_BUDGET = "frame_over_budget"

#: 通用净化消息白名单（API 误用——绝不携带帧内容/异常文本）。
_MESSAGE_MAX_FRAMES = "max_frames 必须为正"
_MESSAGE_MAX_BYTES = "max_bytes 必须为正"
_MESSAGE_MAX_AUDIT = "max_audit_events 必须为正"
_MESSAGE_DROP_POLICY = "drop_policy 非法：drop_oldest 或 drop_newest"
_MESSAGE_DRAIN_LIMIT = "drain limit 必须为正整数"
_MESSAGE_METRICS_SINK = "metrics_sink 非法：需要 on_event/on_frame 结构面"
_MESSAGE_FRAME_CHANNEL = "frame_channel 非法：需要 on_frame 结构面"


class VoiceFrameChannelError(RuntimeError):
    """通道/分流汇 API 误用（消息为通用白名单文案）。"""


def _valid_bytes(data: object) -> int | None:
    """有效帧载荷 → 字节长度；无效（类型/长度非法）→ None。"""
    if not isinstance(data, _VALID_FRAME_DATA_TYPES):
        return None
    try:
        size = len(data)
    except Exception:  # noqa: BLE001 —— 不可计长 = 无效帧
        return None
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        return None
    return size


@runtime_checkable
class _OnFrameLike(Protocol):
    """帧接收面（计量汇与通道共用结构面）。"""

    def on_frame(self, frame: AudioFrameLike) -> None: ...


@runtime_checkable
class _MetricsSinkLike(_OnFrameLike, Protocol):
    """计量汇结构面（on_event + on_frame）。"""

    def on_event(self, event: Any) -> None: ...


class BoundedVoiceFrameChannel:
    """有界帧通道：双上限缓冲 + 丢弃策略 + 丢弃审计 + FIFO 消费。

    ``on_frame`` 满足订阅者帧回调面（与计量汇同口径的有效性判定）；
    ``drain(limit)`` 批量 FIFO 取走帧字节并归还预算（消费方获得
    字节所有权，通道不留副本）。
    """

    __slots__ = (
        "_audit_events",
        "_audit_max",
        "_buffer",
        "_buffered_bytes",
        "_drained_byte_count",
        "_drained_frame_count",
        "_drop_policy",
        "_dropped_byte_count",
        "_dropped_frame_count",
        "_invalid_frame_count",
        "_max_bytes",
        "_max_frames",
        "_received_frame_count",
    )

    def __init__(
        self,
        *,
        max_frames: int = DEFAULT_MAX_FRAMES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        drop_policy: str = DROP_OLDEST,
        max_audit_events: int = DEFAULT_MAX_AUDIT_EVENTS,
    ) -> None:
        if not isinstance(max_frames, int) or isinstance(max_frames, bool) or max_frames <= 0:
            raise VoiceFrameChannelError(_MESSAGE_MAX_FRAMES)
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            raise VoiceFrameChannelError(_MESSAGE_MAX_BYTES)
        if drop_policy not in _DROP_POLICIES:
            raise VoiceFrameChannelError(_MESSAGE_DROP_POLICY)
        if (
            not isinstance(max_audit_events, int)
            or isinstance(max_audit_events, bool)
            or max_audit_events <= 0
        ):
            raise VoiceFrameChannelError(_MESSAGE_MAX_AUDIT)
        self._max_frames = max_frames
        self._max_bytes = max_bytes
        self._drop_policy = drop_policy
        self._audit_max = max_audit_events
        self._buffer: deque[bytes] = deque()
        self._buffered_bytes = 0
        self._received_frame_count = 0
        self._invalid_frame_count = 0
        self._dropped_frame_count = 0
        self._dropped_byte_count = 0
        self._drained_frame_count = 0
        self._drained_byte_count = 0
        self._audit_events: deque[dict[str, object]] = deque(maxlen=max_audit_events)

    # --- 内部：丢弃审计 ---

    def _audit_drop(self, reason: str, frame_bytes: int) -> None:
        self._dropped_frame_count += 1
        self._dropped_byte_count += frame_bytes
        self._audit_events.append(
            {
                "type": EVENT_FRAME_DROPPED,
                "reason": reason,
                "frame_bytes": frame_bytes,
            }
        )

    # --- 帧路径 ---

    def on_frame(self, frame: AudioFrameLike) -> None:
        """入界一帧：有效性判定 → 双上限策略缓冲（满时按策略丢弃）。

        无效帧只计数（绝不抛出/不缓冲/不审计为丢弃）；单帧超过
        总字节预算直接拒绝并审计；缓冲满（帧数或字节任一到顶）
        按构造策略 drop_oldest/drop_newest。
        """
        try:
            data = frame.data
        except Exception:  # noqa: BLE001 —— 属性抛错 = 无效帧
            self._invalid_frame_count += 1
            return
        size = _valid_bytes(data)
        if size is None:
            self._invalid_frame_count += 1
            return
        self._received_frame_count += 1
        payload = bytes(data)  # 归一 bytes（drain 交付物所有权移交）
        # M14-272f：SDK 帧常为 int16 memoryview——len() 是样本数而非
        # 字节数（bytes() 归一后翻倍）。计账恒用归一后字节数（入账/
        # 出账/drop 对称——杜绝负账目）。
        size = len(payload)
        if size > self._max_bytes:
            # 单帧超总预算：拒绝入界（绝不为凑预算放宽上界）
            self._audit_drop(REASON_OVER_BUDGET, size)
            return
        if self._drop_policy == DROP_OLDEST:
            while (
                len(self._buffer) >= self._max_frames
                or self._buffered_bytes + size > self._max_bytes
            ) and self._buffer:
                evicted = self._buffer.popleft()
                self._buffered_bytes -= len(evicted)
                self._audit_drop(REASON_OLDEST_DROPPED, len(evicted))
        else:  # DROP_NEWEST：满时拒绝新帧（缓冲不动）
            if (
                len(self._buffer) >= self._max_frames
                or self._buffered_bytes + size > self._max_bytes
            ):
                self._audit_drop(REASON_NEWEST_REJECTED, size)
                return
        self._buffer.append(payload)
        self._buffered_bytes += size

    # --- 消费路径 ---

    def drain(self, limit: int) -> list[bytes]:
        """批量 FIFO 取走帧字节（归还帧数/字节预算；所有权移交）。

        limit 必须为正整数（API 误用确定性拒绝）；空缓冲返回
        空列表；二次 drain 绝不重复交付已取走帧。
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise VoiceFrameChannelError(_MESSAGE_DRAIN_LIMIT)
        taken: list[bytes] = []
        while self._buffer and len(taken) < limit:
            payload = self._buffer.popleft()
            self._buffered_bytes -= len(payload)
            self._drained_frame_count += 1
            self._drained_byte_count += len(payload)
            taken.append(payload)
        return taken

    # --- 快照（全新拷贝，无帧字节） ---

    def snapshot(self) -> dict[str, object]:
        return {
            "max_frames": self._max_frames,
            "max_bytes": self._max_bytes,
            "drop_policy": self._drop_policy,
            "buffered_frames": len(self._buffer),
            "buffered_bytes": self._buffered_bytes,
            "received_frame_count": self._received_frame_count,
            "invalid_frame_count": self._invalid_frame_count,
            "dropped_frame_count": self._dropped_frame_count,
            "dropped_byte_count": self._dropped_byte_count,
            "drained_frame_count": self._drained_frame_count,
            "drained_byte_count": self._drained_byte_count,
        }

    def audit_snapshot(self) -> dict[str, object]:
        """丢弃审计快照：全量计数 + 最新-N 审计事件（全新深拷贝）。"""
        return {
            "max_audit_events": self._audit_max,
            "audit_event_count": self._dropped_frame_count,
            "audit_events": [dict(event) for event in self._audit_events],
        }


class SplitVoiceSink:
    """分流汇：订阅者事件/帧路径的显式接线件（fan-out）。

    结构满足 ``EventSinkLike``——``on_event`` 只路由计量汇（通道
    零事件面）；``on_frame`` **fan-out**：计量汇继续纯计数（不保留
    字节），通道同时入界缓冲（供 drain 消费）。两条路径互不持有
    对方状态；任一路径的无效输入由各自已有口径处理（本层零包装）。
    """

    __slots__ = ("_channel", "_metrics")

    def __init__(
        self,
        *,
        metrics_sink: BoundedVoiceFrameSink,
        frame_channel: BoundedVoiceFrameChannel,
    ) -> None:
        if not isinstance(metrics_sink, _MetricsSinkLike):
            raise VoiceFrameChannelError(_MESSAGE_METRICS_SINK)
        if not isinstance(frame_channel, _OnFrameLike):
            raise VoiceFrameChannelError(_MESSAGE_FRAME_CHANNEL)
        self._metrics = metrics_sink
        self._channel = frame_channel

    def on_event(self, event: Any) -> None:
        """事件 → 计量汇（通道无事件面）。"""
        self._metrics.on_event(event)

    def on_frame(self, frame: AudioFrameLike) -> None:
        """帧 fan-out：计量汇计数 + 通道入界（同一帧两条路径）。"""
        self._metrics.on_frame(frame)
        self._channel.on_frame(frame)


__all__ = [
    "DEFAULT_MAX_AUDIT_EVENTS",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_FRAMES",
    "DROP_NEWEST",
    "DROP_OLDEST",
    "BoundedVoiceFrameChannel",
    "SplitVoiceSink",
    "VoiceFrameChannelError",
]
