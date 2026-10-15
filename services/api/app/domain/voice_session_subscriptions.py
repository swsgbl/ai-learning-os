"""M14-268：服务端订阅生命周期编排（纯域服务，fake-only 验证）。

M14-267 之后的窄编排切片：voice session **显式申请/释放**服务端
订阅者。把 ``(session_id → 管理键)`` 的确定性映射与参数先行校验
叠在既有积木之上——``VoiceRuntimeManager``（M14-257/258：每键
去重、容量前置、排空/关闭屏障）与 ``ControlledVoiceSubscriberFactory``
（M14-261/264/265：受控装配）——**订阅者只能由 runtime manager
管理**：本服务绝不直接构造/启动/停止订阅者，工厂 ``create`` 只
作为 ``manager.start(key, factory)`` 的零参闭包进入（构造恰发生
在管理器启动路径内、容量检查之后）。

设计契约：
- **单一事实源**：服务无自身 session→订阅者映射/计数/历史——
  申请去重、容量、排空、终态拒绝、净化投影全部复用 manager
  既有语义；``snapshot()`` 纯透传 manager 投影。
- **键确定性**：``session_key = "voice-session-" + canonical
  session_id``；同一 session 恒同键（manager 每键去重直接给出
  会话级申请去重）。session_id 保守边界与 room/identity 同口径
  （``^[A-Za-z0-9_-]{3,64}$``——复用工厂公开正则，不另立宽松
  边界）；键总长 ≤ 13+64=77 < 管理器 ``KEY_MAX_LENGTH`` 128。
- **参数先行校验**：session_id/room/identity 任一非法在任何
  manager 调用之前确定性失败（不占容量预留、不进启动去重面、
  工厂零调用）——room/identity 复用 ``validate_room``/
  ``validate_identity``，不重复实现。
- **申请语义**：``acquire`` = ``manager.start(key, 闭包)``；闭包
  捕获**已校验** canonical room/identity（恰确参数到达工厂）。
  重复 acquire 同 session 复用首次结果；终态在册键的重新申请经
  manager 既有错误透传（调用方先 ``release`` 再 ``acquire``——
  release 对终态条目同样有效：订阅者 stop 幂等，条目被移除后
  同键可重新 start，非"重启"）。
- **释放语义**：``release`` = ``manager.stop(key)``；未知/已释放
  session 幂等 no-op（manager 语义透传）。
- **错误零包装**：manager/工厂错误原样透传——本层不包装、不改写、
  不拼接任何值/文本（既有净化消息唯一出口不变）；本层自身错误
  仅参数与构造校验的白名单文案。
- **零运行时**：不导入/不调用 LiveKit SDK、不签 token、无网络/
  进程/文件/secret/线程/定时器/事件循环操作、无 HTTP 路由、
  不进 lifespan、不做流式 ASR/VAD/barge-in/pause-resume。
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from app.domain.voice_subscriber_factory import (
    ROOM_IDENTITY_PATTERN,
    validate_identity,
    validate_room,
)

#: 会话键前缀（13 字符；+64 上限 = 77 < 管理器键长上限 128）。
KEY_PREFIX = "voice-session-"

_SESSION_ID_RE = re.compile(ROOM_IDENTITY_PATTERN)

#: 通用净化消息白名单（本层仅参数/构造校验错误）。
_MESSAGE_SESSION_INVALID = "会话标识非法：仅 [A-Za-z0-9_-]，长度 3–64"
_MESSAGE_INVALID_MANAGER = "manager 非法：需要 start/stop/snapshot 结构面"
_MESSAGE_INVALID_FACTORY = "factory 非法：需要 create 结构面"


class VoiceSessionSubscriptionError(RuntimeError):
    """编排层错误（消息为通用白名单文案，绝不携带 token/异常文本）。"""


def validate_session_id(session_id: object) -> str:
    """session_id 保守边界：与 room/identity 同口径（复用工厂正则）。

    精确标识符不 trim——非字符串（含 bytes/bool）/空白/长度越界
    （<3 或 >64）/非法字符集一律确定性拒绝。
    """
    if not isinstance(session_id, str) or _SESSION_ID_RE.fullmatch(session_id) is None:
        raise VoiceSessionSubscriptionError(_MESSAGE_SESSION_INVALID)
    return session_id


def session_key(session_id: object) -> str:
    """确定性会话键：``"voice-session-" + canonical session_id``。"""
    return KEY_PREFIX + validate_session_id(session_id)


@runtime_checkable
class _ManagerLike(Protocol):
    """编排所需的管理器最小面（结构满足即可，不要求具体类）。"""

    async def start(
        self, key: str, subscriber_factory: Callable[[], Any]
    ) -> Mapping[str, Any]: ...

    async def stop(self, key: str) -> None: ...

    def snapshot(self) -> Mapping[str, Any]: ...


@runtime_checkable
class _SubscriberFactoryLike(Protocol):
    """编排所需的订阅者工厂最小面（结构满足即可）。"""

    def create(self, room: object, identity: object) -> object: ...


class VoiceSessionSubscriptionService:
    """voice session → 服务端订阅者的显式申请/释放编排（纯透传）。

    生命周期：``await acquire(session_id, room=…, identity=…)`` →
    ``snapshot()``（manager 净化投影）→ ``await release(session_id)``
    （幂等）。订阅者的构造/启动/停止/注册/容量/排空全部且只能
    经注入的 runtime manager 发生。
    """

    __slots__ = ("_factory", "_manager")

    def __init__(
        self,
        *,
        manager: _ManagerLike,
        factory: _SubscriberFactoryLike,
    ) -> None:
        if not isinstance(manager, _ManagerLike):
            raise VoiceSessionSubscriptionError(_MESSAGE_INVALID_MANAGER)
        if not isinstance(factory, _SubscriberFactoryLike):
            raise VoiceSessionSubscriptionError(_MESSAGE_INVALID_FACTORY)
        self._manager = manager
        self._factory = factory

    async def acquire(
        self,
        session_id: object,
        *,
        room: object,
        identity: object,
    ) -> Mapping[str, Any]:
        """显式申请该会话的服务端订阅者（参数先行校验 → manager）。

        零参闭包捕获已校验 canonical room/identity——工厂 ``create``
        只在管理器启动路径内（容量检查之后）被调用恰一次；重复
        acquire 复用 manager 首次结果。返回 manager 净化有界投影
        （本层零包装零改写）。
        """
        canonical_room = validate_room(room)
        canonical_identity = validate_identity(identity)
        key = session_key(session_id)
        return await self._manager.start(
            key, lambda: self._factory.create(canonical_room, canonical_identity)
        )

    async def release(self, session_id: object) -> None:
        """显式释放该会话的服务端订阅者（幂等：未知/已释放 no-op）。

        条目移除后同键可重新 ``acquire``（非"重启"——manager 既有
        语义：终态在册才拒绝，移除后即新 start）。
        """
        await self._manager.stop(session_key(session_id))

    def snapshot(self) -> Mapping[str, Any]:
        """透传 manager 净化总投影（单一事实源 = manager）。"""
        return self._manager.snapshot()


__all__ = [
    "KEY_PREFIX",
    "VoiceSessionSubscriptionError",
    "VoiceSessionSubscriptionService",
    "session_key",
    "validate_session_id",
]
