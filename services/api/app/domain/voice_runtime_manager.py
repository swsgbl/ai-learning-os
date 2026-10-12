"""M14-257：FastAPI 独立的语音运行时管理器/注册表（fake-only 验证）。

为后续 API lifespan 接线预置的显式所有者：有界地管理一个或多个
`ServerVoiceSubscriber` 形态的运行时对象的生命周期作用域。**本切片
fake-only**——不连接 LiveKit、不接 FastAPI startup/lifespan、不新增/
改动任何 HTTP 路由；不做流式 ASR/VAD/barge-in/pause-resume。

设计契约：
- **显式构造、零全局态**：管理器由调用方显式构造（未来 lifespan 拥有
  其实例）；模块级无单例、无可变全局。并发全部在当前 asyncio 循环上
  （无 `asyncio.run`、无嵌套循环、无线程/进程池）。
- **不透明键**：key 只是非空白、无控制字符、长度受限的普通字符串，
  按原文存取（绝不重新解释为文件系统路径或 URL，不做任何拆解）；
  同一 trim 后的键指向同一注册项。
- **单实例单键**：`start(key, factory)` 每键至多创建一个订阅者；
  并发/重复 start 等待并复用首次结果（factory 恰调用一次；调用方
  取消不破坏去重——_starting 归属清理由启动任务自身的完成回调
  负责，R1 修正 3）；已在册且处于终态（stopped/failed）的键拒绝
  重启（抛错，绝不静默替换）；容量（**含进行中启动的预留**，R1
  修正 1）在 **factory 构造之前** 检查（满载即拒，不构造不注册）。
- **失败零残留**：factory 构造失败或订阅者 start 失败 → 尽力
  stop 清理、不注册任何条目、向调用方抛通用净化错误（订阅者异常
  文本绝不透传）。
- **停止确定性**：`stop(key)` 该键有进行中启动时先等待其落定
  （R1 修正 2：成功则停掉产物并移除条目；启动已自清失败则 no-op）；
  成功即移除条目（释放容量）且幂等（未知键 no-op）；订阅者 stop
  抛错时同样移除条目（fail-closed：清理状态未知绝不继续显示
  active）并向调用方抛通用错误；`stop_all()` 覆盖注册条目 ∪
  进行中启动键，按键排序确定性遍历，单项失败不阻断其余清理。
- **异常链抑制**（R1 修正 4）：包装订阅者/factory 异常的管理器
  错误一律 `from None`——__cause__/__context__/traceback 序列化
  均不含原始异常文本（通用净化消息唯一出口）。
- **投影净化有界**：`snapshot()`/`start()` 返回的每订阅者投影只含
  白名单键（status/room/identity/audio_track_count/audio_track_ids/
  event_count/events），events 再截断到构造注入的上限；全部字符串
  值经 JWT 形态正则替换（纵深防御——即使订阅者实现失误，token
  形态也进不了公开投影）；无原始帧、无凭据、无异常文本、无无界
  历史。
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

#: 键的边界：非空白普通字符串、无控制字符、长度 ≤ 128。
KEY_MAX_LENGTH = 128
#: 每订阅者投影中 events 的截断上限（构造可注入）。
DEFAULT_MAX_EVENTS_PER_SUBSCRIBER = 16
#: 默认容量（构造可注入；必须为正）。
DEFAULT_MAX_SUBSCRIBERS = 8

#: JWT 三段形态（与验收 harness 同口径）：投影字符串的纵深脱敏。
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")

#: 订阅者 state() 投影的**白名单键**——只透传这些（任意其他键
#: （异常文本/帧/凭据字段）在投影处即被丢弃，不依赖订阅者自觉）。
_SUBSCRIBER_STATE_KEYS = (
    "status",
    "room",
    "identity",
    "audio_track_count",
    "audio_track_ids",
    "event_count",
)

#: 与 M14-255/256 订阅者终态语义同口径。
_TERMINAL_STATUSES = ("stopped", "failed")

#: 通用净化消息白名单（绝不拼接订阅者异常文本）。
_MESSAGE_KEY_INVALID = "管理键非法：非空普通字符串、无控制字符、长度 ≤ 128"
_MESSAGE_CAPACITY = "订阅者数量达到容量上限，拒绝创建（在构造之前拒绝）"
_MESSAGE_FACTORY_FAILED = "订阅者构造失败（无注册残留）"
_MESSAGE_START_FAILED = "订阅者启动失败（已尽力清理，无注册残留）"
_MESSAGE_TERMINAL_RESTART = "该键的订阅者已终态，拒绝重启（请使用新键）"
_MESSAGE_CONCURRENT_START_FAILED = "同键并发启动失败（无注册残留）"
_MESSAGE_STOP_FAILED = "订阅者停止清理失败（条目已移除，状态不再显示 active）"


class VoiceRuntimeManagerError(RuntimeError):
    """管理器错误（消息为通用白名单文案，绝不携带 token/异常文本）。"""


def validate_key(key: object) -> str:
    """不透明键校验：非空白普通字符串、无控制字符、长度受限。

    返回 strip 后的规范键（" a " 与 "a" 指向同一注册项）；绝不把键
    解释为路径/URL——只按原文存取。
    """
    if not isinstance(key, str):
        raise VoiceRuntimeManagerError(_MESSAGE_KEY_INVALID)
    trimmed = key.strip()
    if not trimmed or len(trimmed) > KEY_MAX_LENGTH:
        raise VoiceRuntimeManagerError(_MESSAGE_KEY_INVALID)
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in trimmed):
        raise VoiceRuntimeManagerError(_MESSAGE_KEY_INVALID)
    return trimmed


@runtime_checkable
class ManagedSubscriberLike(Protocol):
    """被管理订阅者的最小生命周期面（async-native）。

    只覆盖管理器真正需要的面：async start/stop 与已净化有界的
    state()——不要求继承 ServerVoiceSubscriber（结构满足即可），
    管理器不暴露房间/token/流内部。
    """

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def state(self) -> Mapping[str, Any]: ...


def _sanitize_text(text: str) -> str:
    return _JWT_RE.sub("[REDACTED-JWT]", text)


def _sanitize_value(value: Any) -> Any:
    """递归净化：字符串过 JWT 正则；列表/字典逐层复制（不改写原值）。"""
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_value(item) for key, item in value.items()}
    return value


class _Entry:
    """单注册项：订阅者 + 启动任务（并发去重面）。"""

    __slots__ = ("subscriber",)

    def __init__(self, subscriber: ManagedSubscriberLike) -> None:
        self.subscriber = subscriber


class VoiceRuntimeManager:
    """有界语音订阅者注册表（显式构造，单事件循环，fake-only 验证）。

    生命周期：`await start(key, factory)`（每键一实例、并发去重、
    容量前置）→ `snapshot()`（白名单净化投影）→ `await stop(key)` /
    `await stop_all()`（确定性、幂等、单项失败不阻断其余）。
    """

    def __init__(
        self,
        *,
        max_subscribers: int = DEFAULT_MAX_SUBSCRIBERS,
        max_events_per_subscriber: int = DEFAULT_MAX_EVENTS_PER_SUBSCRIBER,
    ) -> None:
        if max_subscribers <= 0:
            raise VoiceRuntimeManagerError("max_subscribers 必须为正")
        if max_events_per_subscriber <= 0:
            raise VoiceRuntimeManagerError("max_events_per_subscriber 必须为正")
        self._max_subscribers = max_subscribers
        self._max_events = max_events_per_subscriber
        self._entries: dict[str, _Entry] = {}
        self._starting: dict[str, asyncio.Future[dict[str, Any]]] = {}

    # --- 投影（白名单 + 截断 + 深度净化） ---

    def _project(self, key: str, subscriber: ManagedSubscriberLike) -> dict[str, Any]:
        state = subscriber.state()
        projection: dict[str, Any] = {"key": key}
        for field in _SUBSCRIBER_STATE_KEYS:
            if field in state:
                projection[field] = _sanitize_value(state[field])
        events = state.get("events")
        if isinstance(events, list):
            projection["events"] = [
                _sanitize_value(event) for event in events[: self._max_events]
            ]
        return projection

    def snapshot(self) -> dict[str, Any]:
        """有界净化总投影：条目计数 + 每订阅者白名单投影（键排序确定）。"""
        keys = sorted(self._entries)
        return {
            "subscriber_count": len(self._entries),
            "max_subscribers": self._max_subscribers,
            "keys": keys,
            "subscribers": [
                self._project(key, self._entries[key].subscriber) for key in keys
            ],
        }

    # --- 生命周期 ---

    async def start(self, key: str, subscriber_factory: Callable[[], ManagedSubscriberLike]) -> dict[str, Any]:
        """每键至多一个订阅者：并发/重复调用复用首次结果。

        容量（含进行中的启动预留）在 factory 构造之前检查；
        factory/start 失败零注册残留（尽力 stop 后抛通用净化错误，
        **异常链抑制**——订阅者异常文本不进 __cause__/traceback）；
        已终态的键拒绝重启。调用方取消不破坏同键去重（启动任务的
        归属清理由任务自身的完成回调负责）。返回净化有界投影。
        """
        canonical = validate_key(key)
        if canonical in self._entries:
            entry = self._entries[canonical]
            if str(entry.subscriber.state().get("status", "")) in _TERMINAL_STATUSES:
                raise VoiceRuntimeManagerError(_MESSAGE_TERMINAL_RESTART)
            return self._project(canonical, entry.subscriber)
        pending = self._starting.get(canonical)
        if pending is not None:
            return await asyncio.shield(pending)
        # R1 修正 1：进行中的启动计入容量预留——并发不同键在满载时
        # 于 factory 构造之前拒绝（恰一个 factory/start 幸存）
        if len(self._entries) + len(self._starting) >= self._max_subscribers:
            raise VoiceRuntimeManagerError(_MESSAGE_CAPACITY)
        task = asyncio.ensure_future(self._start_new(canonical, subscriber_factory))
        self._starting[canonical] = task
        # R1 修正 3：_starting 归属清理挂在任务自身——调用方取消
        # （shield 吞掉传播，任务继续）不会让同键第二次 start 期间
        # 失去去重映射；回调同时消费任务异常，杜绝 unobserved-task 报警
        task.add_done_callback(lambda done, key=canonical: self._on_start_done(key, done))
        return await asyncio.shield(task)

    def _on_start_done(self, key: str, task: asyncio.Future[dict[str, Any]]) -> None:
        """启动任务完成回调：归属清理映射 + 消费任务异常。

        仅当映射仍指向本任务时清理（防竞态误删后续新任务）；任务
        异常在此被 exception() 检索——即使全部调用方被取消也不会
        出现 "exception was never retrieved"。
        """
        if self._starting.get(key) is task:
            self._starting.pop(key, None)
        if not task.cancelled():
            task.exception()  # 消费（标记已检索）；错误经 await 侧传播

    async def _start_new(
        self, key: str, subscriber_factory: Callable[[], ManagedSubscriberLike]
    ) -> dict[str, Any]:
        try:
            subscriber = subscriber_factory()
        except Exception:  # noqa: BLE001 —— 转通用净化错误（异常链抑制）
            # R1 修正 4：from None 抑制异常链——订阅者异常文本绝不
            # 经 __cause__/__context__/traceback 外泄（通用净化消息）
            raise VoiceRuntimeManagerError(_MESSAGE_FACTORY_FAILED) from None
        try:
            await subscriber.start()
        except Exception:  # noqa: BLE001 —— 转通用净化错误（异常链抑制）
            await self._best_effort_stop(subscriber)
            raise VoiceRuntimeManagerError(_MESSAGE_START_FAILED) from None
        self._entries[key] = _Entry(subscriber)
        return self._project(key, subscriber)

    async def stop(self, key: str) -> None:
        """停止并移除该键条目（幂等：未知键 no-op）。

        R1 修正 2：该键存在进行中的启动时先等待其落定——启动成功则
        停掉产物订阅者并移除条目；启动失败（已自行尽力清理、抛净化
        错误）则 stop 为 no-op（吞掉启动错误，不向停止调用方传播）。
        订阅者 stop 抛错：条目**仍被移除**（fail-closed——清理状态
        未知绝不继续显示 active），并向调用方抛通用净化错误（异常
        链抑制）。
        """
        canonical = validate_key(key)
        pending = self._starting.get(canonical)
        if pending is not None:
            try:
                await asyncio.shield(pending)
            except VoiceRuntimeManagerError:
                return  # 启动已自清失败：无条目可停，no-op
        entry = self._entries.pop(canonical, None)
        if entry is None:
            return  # 幂等 no-op（从未注册 / 已停止移除）
        try:
            await entry.subscriber.stop()
        except Exception:  # noqa: BLE001 —— 转通用净化错误（异常链抑制）
            raise VoiceRuntimeManagerError(_MESSAGE_STOP_FAILED) from None

    async def stop_all(self) -> dict[str, int]:
        """按键排序确定性停止全部条目与进行中的启动；单项失败不阻断。

        R1 修正 2：覆盖面 = 注册条目 ∪ 进行中启动键——pending 键经
        stop(key) 等待落定后同样清理。
        返回 {stopped: 成功数, failed: 清理失败数}（计数非文本）。
        """
        stopped = 0
        failed = 0
        for key in sorted(set(self._entries) | set(self._starting)):
            try:
                await self.stop(key)
                stopped += 1
            except VoiceRuntimeManagerError:
                failed += 1  # 条目已被 stop() 移除（fail-closed），继续其余
        return {"stopped": stopped, "failed": failed}

    async def _best_effort_stop(self, subscriber: ManagedSubscriberLike) -> None:
        try:
            await subscriber.stop()
        except Exception:  # noqa: BLE001 —— 启动失败路径的尽力清理，不掩盖原错误
            return


__all__ = [
    "DEFAULT_MAX_EVENTS_PER_SUBSCRIBER",
    "DEFAULT_MAX_SUBSCRIBERS",
    "KEY_MAX_LENGTH",
    "ManagedSubscriberLike",
    "VoiceRuntimeManager",
    "VoiceRuntimeManagerError",
    "validate_key",
]
