"""M14-260：有界语音帧计量汇（纯域层、零接线/零网络/零副作用）。

M14-259 之后的下一个窄后端基础件：一个结构上满足
``app.domain.voice_subscription.EventSinkLike`` 协议的同步、纯域
计量汇——只做两件事：①把订阅事件压成有界净化投影（环形保留
最新 N 条，全量计数含被淘汰者）；②对音频帧做纯计数计量（帧数
与字节数，绝不复制/保留帧字节）。**这不是帧消费者管线**：不做
流式 ASR/VAD/barge-in/pause-resume，不进应用生命周期、不加
HTTP 路由、不连真实房间服务、不注册任何生产订阅者工厂——
运行时接线是后续切片的职责。

安全契约：
- 事件投影只复制白名单字段（type/room/identity/category/detail）；
  已知字段只接受 str/None——其他类型（bytes/数值/列表等）统一
  映射为固定占位 [INVALID-FIELD]（绝不 str() 化 bytes——杜绝
  b'...' 形态的音频字节经字段值渗入投影）；str 值先过 JWT 三段
  形态正则净化、再确定性截断到 MAX_FIELD_LENGTH（512 字符；
  顺序：先净化后截断）；原始事件对象绝不保留（投影为新建
  dict，不改写原值）；
- 帧数据仅 bytes/bytearray/memoryview 三类视为有效——只读
  len 计量，不复制、不切片、不保留帧对象或帧字节；其他类型
  （str/list/dict/任意 Sized——即使 len 可用）一律计为无效帧；
  snapshot() 不含音频字节/token/异常文本/私有路径/时间戳/无界
  历史。

确定性契约：
- 纯同步、零事件循环操作、零线程、零进程、零磁盘、零网络、
  零定时器；on_frame 对任何无效/缺失帧数据均不抛出（一律计为
  invalid_frame_count）；
- snapshot() 每次返回全新拷贝（含 events 内层 dict），调用方
  改写不影响后续快照；
- 运行时导入严格限定在纯标准库（re/collections/typing）——
  事件/帧协议类型仅 TYPE_CHECKING 引用（结构满足即兼容，不
  拉入订阅控制器的任何运行时依赖）。
"""
from __future__ import annotations

import re
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.voice_subscription import AudioFrameLike, SubscriptionEvent

#: 事件环形缓冲上限默认值（与订阅域 DEFAULT_MAX_EVENTS 同约定：64）。
DEFAULT_MAX_EVENTS = 64

#: JWT 三段形态（与运行时管理器/验收 harness 同口径）：投影字符串
#: 的纵深脱敏。
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")

_REDACTED = "[REDACTED-JWT]"

#: 事件投影单字段字符上限（净化后确定性截断——约束任意 str 字段
#: 的最大长度，使 snapshot 逐字段有界）。
MAX_FIELD_LENGTH = 512

#: 已知字段非 str 且非 None 时的固定占位（绝不 str() 化 bytes——
#: 防止帧字节/任意对象经 b'...' 形态渗入投影）。
INVALID_FIELD = "[INVALID-FIELD]"

#: 有效帧载荷类型（仅 bytes/bytearray/memoryview；其余 Sized——
#: 如 str/list/dict——即使 len 可用也计为无效帧）。
_VALID_FRAME_DATA_TYPES = (bytes, bytearray, memoryview)

#: 事件投影白名单字段（只复制这些；其余属性在投影处即被丢弃）。
_EVENT_FIELDS = ("type", "room", "identity", "category", "detail")


class VoiceFrameSinkError(RuntimeError):
    """汇 API 误用（消息为通用白名单文案，绝不携带 token/cause 细节）。"""


def _sanitize_field(value: object) -> object:
    """字段级净化：None 原样保留；str 先替换 JWT 形态再截断；其他
    类型（含 bytes）统一映射为固定占位——绝不 str() 化 bytes。"""
    if value is None:
        return None
    if not isinstance(value, str):
        return INVALID_FIELD
    return _JWT_RE.sub(_REDACTED, value)[:MAX_FIELD_LENGTH]


class BoundedVoiceFrameSink:
    """有界计量汇：事件环形净化投影 + 帧纯计数（不保留任何字节）。

    结构满足 ``EventSinkLike``（on_event/on_frame）；额外提供
    ``snapshot()`` 有界净化快照。构造注入正数 ``max_events``
    （默认 64，与订阅域约定一致）。
    """

    def __init__(self, *, max_events: int = DEFAULT_MAX_EVENTS) -> None:
        if max_events <= 0:
            raise VoiceFrameSinkError("max_events 必须为正")
        self._max_events = max_events
        self._events: deque[dict[str, object]] = deque(maxlen=max_events)
        self._event_count = 0
        self._frame_count = 0
        self._frame_byte_count = 0
        self._invalid_frame_count = 0

    def on_event(self, event: SubscriptionEvent) -> None:
        """记录一条事件：全量计数 + 白名单净化投影进环形缓冲。

        只读事件的五个已知安全字段并新建投影 dict——原始事件
        对象与白名单外字段（凭据/帧等）绝不保留。
        """
        self._event_count += 1
        projection: dict[str, object] = {
            name: _sanitize_field(getattr(event, name, None))
            for name in _EVENT_FIELDS
        }
        self._events.append(projection)

    def on_frame(self, frame: AudioFrameLike) -> None:
        """计量一帧：仅 bytes/bytearray/memoryview 有效，只读 len，
        绝不复制/保留字节或帧对象。

        帧数据缺失/属性访问抛错/非有效类型（str/list/dict/任意
        Sized——即使 len 可用）/不可计长/长度非法——一律计为
        invalid_frame_count，绝不抛出、绝不做任何 I/O 或调度。
        """
        try:
            data = frame.data
            size = len(data) if isinstance(data, _VALID_FRAME_DATA_TYPES) else None
        except Exception:  # noqa: BLE001 —— 任何不可读/不可计长 = 无效帧
            self._invalid_frame_count += 1
            return
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            self._invalid_frame_count += 1
            return
        self._frame_count += 1
        self._frame_byte_count += size

    def snapshot(self) -> dict[str, object]:
        """有界净化快照：计数器 + 最新-N 事件投影（全新深拷贝）。"""
        return {
            "max_events": self._max_events,
            "event_count": self._event_count,
            "events": [dict(projection) for projection in self._events],
            "frame_count": self._frame_count,
            "frame_byte_count": self._frame_byte_count,
            "invalid_frame_count": self._invalid_frame_count,
        }
