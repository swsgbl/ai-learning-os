"""M14-272a：服务端语音会话管线编排（纯域组装器，fake-only）。

把 M14-267~271 已交付的纯域积木组装成一台「服务端语音会话
管线」——每会话独立的一束：

``订阅者（只能经 runtime manager 注册）→ SplitVoiceSink（计量
+ 有界帧通道）→ VoiceAsrStreamSession（transcript 权威）``
并联 ``VoiceSessionController（FSM 控制）``。

- ``ServerVoicePipeline.open_session``：按会话组装 split（计量
  汇 + 通道）→ 经注入 ``factory_builder`` 构造**绑定了该 split**
  的受控订阅者工厂（M14-272a 工厂 event_sink_factory 注入面）
  → ``VoiceSessionSubscriptionService.acquire``（订阅只能由
  manager 管理——M14-268 契约）→ ASR 消费会话 + 控制器；
- ``ServerVoiceSessionHandle``：会话级操作面——``pump``（驱动
  ASR 消费）、``submit``/``submit_vad``（FSM 控制）、transcript/
  控制/通道快照；
- ``close_session``：确定性关停顺序——先停帧源（订阅者
  release）→ 消费残余帧（drain→feed）→ ASR finish 冲刷 → 句柄
  移除（幂等）；终态 transcript 仍可查询（审计面）。

边界（非声明）：本切片仍是**纯域 fake-only 组装器**——不接
HTTP 路由/lifespan、不实现 FunASR adapter（adapter 由构造注入）、
不连真实 LiveKit、不做 VAD 检测器；这些属后续接线切片
（M14-272b/c）。零事件循环原语（async 面只 await 协作者）。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from app.domain.voice_asr_stream import (
    AsrPumpResult,
    VoiceAsrAdapterLike,
    VoiceAsrStreamSession,
)
from app.domain.voice_frame_channel import (
    BoundedVoiceFrameChannel,
    SplitVoiceSink,
)
from app.domain.voice_frame_sink import BoundedVoiceFrameSink
from app.domain.voice_session_control import VoiceSessionController
from app.domain.voice_session_subscriptions import (
    VoiceSessionSubscriptionService,
)
from app.domain.voice_subscriber_factory import (
    VoiceSubscriberFactoryError,
    validate_identity,
)

#: 每会话帧通道默认上限（M14-269 默认同款）。
DEFAULT_MAX_FRAMES = 256
DEFAULT_MAX_BYTES = 512 * 1024

#: 通用净化消息白名单。
_MESSAGE_INVALID_MANAGER = "manager 非法：需要 start/stop/snapshot 结构面"
_MESSAGE_INVALID_FACTORY_BUILDER = "factory_builder 非法：必须为可调用对象"
_MESSAGE_INVALID_ADAPTER_FACTORY = "asr_adapter_factory 非法：必须为可调用对象"
_MESSAGE_SESSION_UNKNOWN = "未知语音会话（已关闭或从未打开）"
_MESSAGE_SESSION_TERMINATED = "会话已终结，该操作不可用"


class VoicePipelineError(RuntimeError):
    """管线 API 误用（消息为通用白名单文案）。"""


def validate_identity_of(identity: object) -> str:
    """open 入参 identity 复用工厂保守边界（转管线净化错误）。"""
    try:
        return validate_identity(identity)
    except VoiceSubscriberFactoryError:
        raise VoicePipelineError("identity 非法：仅 [A-Za-z0-9_-]，长度 3–64") from None



@runtime_checkable
class _ManagerLike(Protocol):
    """管线所需 manager 最小面（与 M14-268 同口径）。"""

    async def start(self, key: str, factory: Callable[[], Any]) -> Any: ...

    async def stop(self, key: str) -> None: ...

    def snapshot(self) -> Any: ...


class ServerVoiceSessionHandle:
    """单会话操作面（管线内部构造；调用方只消费）。

    close 后句柄保留为终态审计面（transcript/state 可查、
    pump/submit 拒绝）。
    """

    __slots__ = (
        "_asr",
        "_channel",
        "_closed",
        "_control",
        "_identity",
        "_session_id",
        "_subscription",
    )

    def __init__(
        self,
        session_id: str,
        *,
        identity: str,
        subscription: VoiceSessionSubscriptionService,
        channel: BoundedVoiceFrameChannel,
        asr: VoiceAsrStreamSession,
        control: VoiceSessionController,
    ) -> None:
        self._session_id = session_id
        self._identity = identity
        self._subscription = subscription
        self._channel = channel
        self._asr = asr
        self._control = control
        self._closed = False

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def identity(self) -> str:
        """该会话服务端订阅者身份（open 时服务端给定）。"""
        return self._identity

    def _require_active(self) -> None:
        if self._closed:
            raise VoicePipelineError(_MESSAGE_SESSION_TERMINATED)

    @property
    def session_id(self) -> str:
        return self._session_id

    def pump(self, limit: int) -> AsrPumpResult:
        """驱动 ASR 消费：通道残余帧 → adapter → transcript。"""
        self._require_active()
        return self._asr.pump(limit)

    def submit(self, event: str, *, revision: int | None = None) -> dict[str, Any]:
        """FSM 控制事件（服务端权威；详见 M14-271）。"""
        self._require_active()
        return self._control.submit(event, revision=revision)

    def submit_vad(self, signal: str) -> dict[str, Any]:
        """VAD 信号映射（speech_start/speech_end；详见 M14-271）。"""
        self._require_active()
        return self._control.submit_vad(signal)

    def transcript(self) -> dict[str, Any]:
        return self._asr.transcript_snapshot()

    def control_snapshot(self) -> dict[str, Any]:
        return self._control.snapshot()

    def channel_snapshot(self) -> dict[str, Any]:
        return self._channel.snapshot()

    def state(self) -> dict[str, Any]:
        """组合状态投影（ASR 会话 + FSM + 通道计数——净化白名单）。"""
        asr_state = self._asr.state()
        control = self._control.snapshot()
        channel = self._channel.snapshot()
        return {
            "session_id": self._session_id,
            "identity": self._identity,
            "asr_status": asr_state["status"],
            "fsm_state": control["state"],
            "fsm_revision": control["revision"],
            "buffered_frames": channel["buffered_frames"],
            "buffered_bytes": channel["buffered_bytes"],
            "closed": self._closed,
        }


class ServerVoicePipeline:
    """服务端语音会话管线组装器（显式构造、零全局态）。

    ``factory_builder(split)``：调用方注入——按会话 split 构造
    绑定该 split 的受控订阅者工厂（工厂校验由工厂自身完成）；
    ``asr_adapter_factory()``：每会话新建 ASR adapter（结构满足
    ``VoiceAsrAdapterLike``——本地 FunASR 适配属后续切片）。
    """

    __slots__ = (
        "_adapter_factory",
        "_factory_builder",
        "_manager",
        "_sessions",
    )

    def __init__(
        self,
        *,
        manager: _ManagerLike,
        factory_builder: Callable[[SplitVoiceSink], Any],
        asr_adapter_factory: Callable[[], VoiceAsrAdapterLike],
        max_frames: int = DEFAULT_MAX_FRAMES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        if not isinstance(manager, _ManagerLike):
            raise VoicePipelineError(_MESSAGE_INVALID_MANAGER)
        if not callable(factory_builder):
            raise VoicePipelineError(_MESSAGE_INVALID_FACTORY_BUILDER)
        if not callable(asr_adapter_factory):
            raise VoicePipelineError(_MESSAGE_INVALID_ADAPTER_FACTORY)
        channel = BoundedVoiceFrameChannel(max_frames=max_frames, max_bytes=max_bytes)  # 参数校验复用通道构造
        channel.snapshot()
        self._manager = manager
        self._factory_builder = factory_builder
        self._adapter_factory = asr_adapter_factory
        self._sessions: dict[str, ServerVoiceSessionHandle] = {}

    async def open_session(
        self,
        session_id: object,
        *,
        room: object,
        identity: object,
    ) -> ServerVoiceSessionHandle:
        """组装并注册一个会话（同 session 幂等返回既有句柄）。

        订阅者只能经 runtime manager 注册（M14-268 订阅服务
        透传 acquire）；帧路径经 split 同时计量与入界（M14-269）；
        ASR 会话与控制器每会话独立（M14-270/271）。identity 由
        调用方（路由层服务端生成）给定——句柄携带供状态投影。
        """
        from app.domain.voice_session_subscriptions import validate_session_id

        canonical = validate_session_id(session_id)
        existing = self._sessions.get(canonical)
        if existing is not None and not existing.is_closed:
            return existing
        canonical_identity = validate_identity_of(identity)
        channel = BoundedVoiceFrameChannel()
        metrics = BoundedVoiceFrameSink()
        split = SplitVoiceSink(metrics_sink=metrics, frame_channel=channel)
        subscription = VoiceSessionSubscriptionService(
            manager=self._manager,
            factory=self._factory_builder(split),
        )
        await subscription.acquire(canonical, room=room, identity=identity)
        asr = VoiceAsrStreamSession(
            canonical, adapter=self._adapter_factory(), channel=channel
        )
        handle = ServerVoiceSessionHandle(
            canonical,
            identity=canonical_identity,
            subscription=subscription,
            channel=channel,
            asr=asr,
            control=VoiceSessionController(),
        )
        self._sessions[canonical] = handle
        return handle

    def _require(self, session_id: str) -> ServerVoiceSessionHandle:
        handle = self._sessions.get(session_id)
        if handle is None:
            raise VoicePipelineError(_MESSAGE_SESSION_UNKNOWN)
        return handle

    def pump_session(self, session_id: str, limit: int) -> AsrPumpResult:
        return self._require(session_id).pump(limit)

    def submit_session(
        self, session_id: str, event: str, *, revision: int | None = None
    ) -> dict[str, Any]:
        return self._require(session_id).submit(event, revision=revision)

    def submit_vad_session(self, session_id: str, signal: str) -> dict[str, Any]:
        """VAD 信号提交（M14-271 submit_vad 透传——HTTP 面用）。"""
        return self._require(session_id).submit_vad(signal)

    def session_state(self, session_id: str) -> dict[str, Any]:
        return self._require(session_id).state()

    def final_transcript(self, session_id: str) -> dict[str, Any]:
        """终态 transcript（close 后仍可查询——审计面）。"""
        handle = self._sessions.get(session_id)
        if handle is None:
            raise VoicePipelineError(_MESSAGE_SESSION_UNKNOWN)
        return handle.transcript()

    async def close_session(self, session_id: object) -> dict[str, Any]:
        """确定性关停：停帧源 → 消费残余 → ASR 冲刷 → 移除句柄。

        幂等（未知/已关会话 no-op）；返回终态组合投影。
        """
        from app.domain.voice_session_subscriptions import validate_session_id

        canonical = validate_session_id(session_id)
        handle = self._sessions.get(canonical)
        if handle is None or handle.is_closed:
            return {"session_id": canonical, "closed": True}  # 幂等 no-op
        # 1. 停帧源（订阅者 release——房间断开，帧不再流入）
        await handle._subscription.release(canonical)
        # 2. 消费残余（通道内已缓冲帧 → adapter → transcript）
        handle._asr.pump(DEFAULT_MAX_FRAMES)
        # 3. ASR 冲刷尾段并终结
        handle._asr.finish()
        handle._closed = True
        final = handle.state()
        final["closed"] = True
        # 句柄保留为终态审计面（final_transcript/state 可查；
        # pump/submit 拒绝；重新 open 同 session = 新束）
        return final

    def snapshot(self) -> dict[str, Any]:
        """管线级投影（会话键列表 + 计数——净化白名单）。"""
        return {
            "session_count": len(self._sessions),
            "session_ids": sorted(self._sessions),
        }


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_FRAMES",
    "ServerVoicePipeline",
    "ServerVoiceSessionHandle",
    "VoicePipelineError",
]
