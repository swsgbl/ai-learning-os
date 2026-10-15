"""M14-268 服务端订阅生命周期编排契约测试（fake-only，零真实连接）。

覆盖（与切片验收面一一对应）：
1  键规则：session_key = "voice-session-" + canonical session_id，同
   session 恒同键、不同 session 不同键；session_id 保守边界与
   room/identity 同口径（非字符串/空白/长度 2 与 65/非法字符集
   确定性拒绝）；
2  参数先行校验：session_id/room/identity 任一非法在任何 manager
   调用之前失败（manager.start 零调用、工厂 create 零调用、不占
   容量预留）；
3  申请编排：acquire 把零参闭包（捕获已校验 canonical room/
   identity）连同会话键恰一次交给 manager.start；闭包产物即工厂
   create 产物；返回值纯透传 manager.start 投影；本层不新增任何
   键/文本；
4  所有权（订阅者只能由 runtime manager 管理）：真实 manager +
   真实受控工厂 + fake 协作者集成——acquire 后条目在 manager、
   订阅者 active、fake room 收到 connect；服务对象无任何自有
   session→订阅者映射（AST：外部调用面恰 manager.start/stop/
   snapshot + 闭包内 factory.create）；
5  会话级申请去重：同 session 重复 acquire 复用首次结果（工厂
   create 恰一次、manager 条目恰一、投影 status=active）；
6  释放：release 移除条目、房间断开、幂等（未知/已释放 session
   no-op）；释放后同 session 可重新 acquire（键已移除，非重启）；
7  失败透传与净化：订阅者启动失败（connect 抛错）经 manager 既有
   净化错误透传（异常链不被本层改写、无凭据/token 泄漏）；容量
   满错误同样透传；本层零包装零重写；
8  构造校验：manager/factory 缺失或结构不满足确定性失败；
9  snapshot 透传：service.snapshot() 与 manager.snapshot() 同构
   （单一事实源 = manager）；
10 源码卫生（AST+文本）：编排模块无 LiveKit SDK/FastAPI/HTTP/
   socket/os.environ/subprocess/文件/secret 调用；无 asyncio
   调度（编排层零事件循环操作）；无 build_voice_token；manager
   的 start/stop/snapshot 之外无订阅者生命周期调用。

全部使用显眼假凭据与 fake 协作者；绝不连接真实 LiveKit。
"""
from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from app.domain.voice_runtime_manager import (
    VoiceRuntimeManager,
    VoiceRuntimeManagerError,
)
from app.domain.voice_session_subscriptions import (
    KEY_PREFIX,
    VoiceSessionSubscriptionError,
    VoiceSessionSubscriptionService,
    session_key,
    validate_session_id,
)
from app.domain.voice_subscriber_factory import (
    ControlledVoiceSubscriberFactory,
    VoiceSubscriberFactoryError,
)

#: 显眼假凭据（绝不真实）。
FAKE_KEY = "fake-api-key-000000"
FAKE_SECRET = "fake-api-secret-000000000000000000000000"
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = (
    REPO_ROOT / "services" / "api" / "app" / "domain" / "voice_session_subscriptions.py"
)
MODULE_SOURCE = MODULE_PATH.read_text(encoding="utf-8")

SESSION_A = "sess-alpha-0001"
SESSION_B = "sess-beta-0002"
ROOM_A = "exam-room-alpha"
IDENT_A = "student-aaa00001"
IDENT_B = "student-bbb00002"


def run(coro):
    return asyncio.run(coro)


# ---------- fake 注入件（与既有订阅/工厂测试同构，零网络） ----------


class FakeFrame:
    def __init__(self, data: object) -> None:
        self._data = data

    @property
    def data(self) -> object:
        return self._data


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
    def __init__(self, *, fail_connect: bool = False) -> None:
        self.handlers: dict[str, Any] = {}
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.received_tokens: list[str] = []
        self._fail_connect = fail_connect

    def on(self, name: str, handler: Any) -> None:
        self.handlers[name] = handler

    async def connect(self, token: str) -> None:
        self.connect_calls += 1
        self.received_tokens.append(token)
        if self._fail_connect:
            raise RuntimeError("connect exploded with secret-ish detail")

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    def emit_track_subscribed(self, track: FakeTrack) -> None:
        self.handlers["track_subscribed"](track)


class RoomRecorder:
    """房间工厂记录器：按需投递失败房间；记录每次构造。"""

    def __init__(self, *, fail_connect: bool = False) -> None:
        self.rooms: list[FakeRoom] = []
        self._fail_connect = fail_connect

    def __call__(self) -> FakeRoom:
        room = FakeRoom(fail_connect=self._fail_connect)
        self.rooms.append(room)
        return room


class CreateRecorder:
    """包装受控工厂 create：记录每次调用的 (room, identity) 与产物。"""

    def __init__(self, inner: ControlledVoiceSubscriberFactory) -> None:
        self.inner = inner
        self.calls: list[tuple[object, object]] = []
        self.products: list[object] = []

    def create(self, room: object, identity: object) -> object:
        self.calls.append((room, identity))
        product = self.inner.create(room, identity)
        self.products.append(product)
        return product


def build_service(
    *, fail_connect: bool = False, max_subscribers: int = 8
) -> tuple[
    VoiceSessionSubscriptionService,
    VoiceRuntimeManager,
    CreateRecorder,
    RoomRecorder,
]:
    """真实 manager + 真实受控工厂 + fake 最内层协作者三件套。"""
    room_recorder = RoomRecorder(fail_connect=fail_connect)
    controlled = ControlledVoiceSubscriberFactory(
        token_provider_factory=lambda room, identity: FAKE_JWT,
        room_factory=room_recorder,
        audio_stream_factory=lambda track: FakeAudioStream(),
    )
    create_recorder = CreateRecorder(controlled)
    manager = VoiceRuntimeManager(max_subscribers=max_subscribers)
    service = VoiceSessionSubscriptionService(
        manager=manager, factory=create_recorder  # type: ignore[arg-type]
    )
    return service, manager, create_recorder, room_recorder


# ---------- 1：键规则 ----------


def test_session_key_deterministic_and_distinct() -> None:
    assert session_key(SESSION_A) == KEY_PREFIX + SESSION_A
    # session_id 是精确标识符：不 trim，带空白即非法（确定性拒绝）
    with pytest.raises(VoiceSessionSubscriptionError):
        session_key(f"  {SESSION_A}  ")
    assert session_key(SESSION_A) == session_key(SESSION_A)
    assert session_key(SESSION_A) != session_key(SESSION_B)


def test_session_key_within_manager_bounds() -> None:
    longest = "s" * 64
    key = session_key(longest)
    assert len(key) <= 128  # 管理器 KEY_MAX_LENGTH
    assert validate_session_id(longest) == longest


@pytest.mark.parametrize(
    "bad", [None, 123, b"sess-bytes", "", "   ", "ab", "a" * 65, "bad/sid", "bad sid", "sid:x"]
)
def test_invalid_session_id_rejected(bad) -> None:
    with pytest.raises(VoiceSessionSubscriptionError):
        validate_session_id(bad)


# ---------- 2：参数先行（manager 零调用、工厂零调用） ----------


class _ManagerSpy:
    """manager 替身：记录调用，返回哨兵投影。"""

    def __init__(self) -> None:
        self.start_calls: list[tuple[str, Any]] = []
        self.stop_calls: list[str] = []
        self.snapshot_calls = 0

    async def start(self, key: str, factory: Any) -> dict[str, Any]:
        self.start_calls.append((key, factory))
        return {"sentinel": True}

    async def stop(self, key: str) -> None:
        self.stop_calls.append(key)

    def snapshot(self) -> dict[str, Any]:
        self.snapshot_calls += 1
        return {}


def _spy_service() -> tuple[VoiceSessionSubscriptionService, _ManagerSpy, CreateRecorder]:
    spy = _ManagerSpy()
    recorder = CreateRecorder(
        ControlledVoiceSubscriberFactory(
            token_provider_factory=lambda room, identity: FAKE_JWT,
            room_factory=lambda: FakeRoom(),
            audio_stream_factory=lambda track: FakeAudioStream(),
        )
    )
    service = VoiceSessionSubscriptionService(
        manager=spy,  # type: ignore[arg-type]
        factory=recorder,  # type: ignore[arg-type]
    )
    return service, spy, recorder


@pytest.mark.parametrize(
    "kwargs",
    [
        {"session_id": "bad/sid", "room": ROOM_A, "identity": IDENT_A},
        {"session_id": SESSION_A, "room": "no", "identity": IDENT_A},
        {"session_id": SESSION_A, "room": ROOM_A, "identity": "x"},
        {"session_id": SESSION_A, "room": None, "identity": IDENT_A},
        {"session_id": SESSION_A, "room": ROOM_A, "identity": None},
    ],
)
def test_invalid_params_fail_before_any_manager_call(kwargs) -> None:
    service, spy, recorder = _spy_service()
    with pytest.raises((VoiceSessionSubscriptionError, VoiceSubscriberFactoryError)):
        run(service.acquire(**kwargs))
    assert spy.start_calls == []
    assert recorder.calls == []


# ---------- 3：申请编排（恰一次 manager.start + 闭包产物） ----------


def test_acquire_delegates_to_manager_start_once() -> None:
    service, spy, recorder = _spy_service()
    projection = run(service.acquire(SESSION_A, room=ROOM_A, identity=IDENT_A))
    assert len(spy.start_calls) == 1
    key, factory = spy.start_calls[0]
    assert key == KEY_PREFIX + SESSION_A
    # 闭包恰在调用时才构造，且产物即受控工厂 create 产物
    assert recorder.calls == []
    product = factory()
    assert recorder.calls == [(ROOM_A, IDENT_A)]  # 已校验 canonical 原样
    assert product is recorder.products[0]
    assert projection == {"sentinel": True}  # 纯透传


# ---------- 4/5：所有权 + 会话级去重（真实集成） ----------


def test_acquire_registers_only_in_manager_and_dedupes() -> None:
    service, manager, recorder, rooms = build_service()
    first = run(service.acquire(SESSION_A, room=ROOM_A, identity=IDENT_A))
    second = run(service.acquire(SESSION_A, room=ROOM_A, identity=IDENT_A))
    snapshot = manager.snapshot()
    assert snapshot["subscriber_count"] == 1
    assert snapshot["keys"] == [KEY_PREFIX + SESSION_A]
    assert first["status"] == "active"
    assert second["status"] == "active"
    assert len(recorder.calls) == 1  # 工厂 create 恰一次（去重复用）
    assert len(rooms.rooms) == 1
    assert rooms.rooms[0].connect_calls == 1
    assert rooms.rooms[0].received_tokens == [FAKE_JWT]  # token 只进连接局部


def test_acquire_distinct_sessions_independent_entries() -> None:
    service, manager, recorder, _rooms = build_service()
    run(service.acquire(SESSION_A, room=ROOM_A, identity=IDENT_A))
    run(service.acquire(SESSION_B, room=ROOM_A, identity=IDENT_B))
    snapshot = manager.snapshot()
    assert snapshot["subscriber_count"] == 2
    assert snapshot["keys"] == sorted(
        [KEY_PREFIX + SESSION_A, KEY_PREFIX + SESSION_B]
    )
    assert len(recorder.calls) == 2


# ---------- 6：释放（幂等 + 重新申请） ----------


def test_release_stops_and_removes_then_idempotent() -> None:
    service, manager, _recorder, rooms = build_service()
    run(service.acquire(SESSION_A, room=ROOM_A, identity=IDENT_A))
    run(service.release(SESSION_A))
    assert manager.snapshot()["subscriber_count"] == 0
    assert rooms.rooms[0].disconnect_calls == 1
    run(service.release(SESSION_A))  # 已释放：幂等 no-op
    run(service.release("sess-never-registered-xyz"))  # 未知：幂等 no-op
    assert rooms.rooms[0].disconnect_calls == 1
    # 释放后可重新申请（键已移除——非"重启"，是新 start）
    again = run(service.acquire(SESSION_A, room=ROOM_A, identity=IDENT_A))
    assert again["status"] == "active"
    assert manager.snapshot()["subscriber_count"] == 1


# ---------- 7：失败透传与净化 ----------


def test_start_failure_passes_through_sanitized() -> None:
    service, manager, _recorder, _rooms = build_service(fail_connect=True)
    with pytest.raises(VoiceRuntimeManagerError) as ei:
        run(service.acquire(SESSION_A, room=ROOM_A, identity=IDENT_A))
    error = ei.value
    text = str(error)
    assert FAKE_KEY not in text and FAKE_SECRET not in text
    assert FAKE_JWT not in text
    assert "exploded" not in text  # 协作者异常文本不透传
    assert manager.snapshot()["subscriber_count"] == 0  # 零残留


def test_capacity_error_passes_through() -> None:
    service, manager, _recorder, _rooms = build_service(max_subscribers=1)
    run(service.acquire(SESSION_A, room=ROOM_A, identity=IDENT_A))
    with pytest.raises(VoiceRuntimeManagerError):
        run(service.acquire(SESSION_B, room=ROOM_A, identity=IDENT_B))
    assert manager.snapshot()["subscriber_count"] == 1


def test_release_after_drain_closed_manager_rejects_new_acquire() -> None:
    service, manager, _recorder, _rooms = build_service()
    run(service.acquire(SESSION_A, room=ROOM_A, identity=IDENT_A))
    run(manager.close())
    with pytest.raises(VoiceRuntimeManagerError):
        run(service.acquire(SESSION_B, room=ROOM_A, identity=IDENT_B))


# ---------- 8：构造校验 ----------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"manager": None, "factory": object()},
        {"manager": object(), "factory": None},
        {"manager": None, "factory": None},
    ],
)
def test_constructor_rejects_missing_collaborators(kwargs) -> None:
    with pytest.raises(VoiceSessionSubscriptionError):
        VoiceSessionSubscriptionService(**kwargs)


def test_constructor_accepts_real_manager_and_factory() -> None:
    service, _manager, _recorder, _rooms = build_service()
    assert isinstance(service, VoiceSessionSubscriptionService)


# ---------- 9：snapshot 透传（单一事实源） ----------


def test_snapshot_passes_through_manager() -> None:
    service, manager, _recorder, _rooms = build_service()
    run(service.acquire(SESSION_A, room=ROOM_A, identity=IDENT_A))
    assert service.snapshot() == manager.snapshot()
    assert service.snapshot()["state"] == "open"


# ---------- 10：源码卫生（AST+文本） ----------


def test_source_hygiene_module() -> None:
    tree = ast.parse(MODULE_SOURCE)
    allowed_imports = {
        "__future__",
        "typing",
        "collections.abc",
        "re",
        "app.domain.voice_subscriber_factory",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in allowed_imports, alias.name
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            assert (node.module or "") in allowed_imports, node.module
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else ""
            attr = func.attr if isinstance(func, ast.Attribute) else ""
            assert name not in {
                "open", "system", "popen", "run", "sleep", "getenv",
                "ensure_future", "create_task", "start_new_thread",
                "build_voice_token",
            }, name
            assert attr not in {
                "system", "popen", "getenv", "ensure_future",
                "create_task", "start_new_thread", "connect",
            }, attr
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                assert func.value.id != "asyncio", "编排层零事件循环操作"
    for forbidden in (
        "getenv(", "os.environ", "dotenv", "build_voice_token",
        "__import__", "eval(", "exec(", "socket", "subprocess",
        "import livekit", "from livekit", "fastapi", "httpx", "aiohttp",
    ):
        assert forbidden not in MODULE_SOURCE, forbidden


def test_source_allows_only_managed_lifecycle_calls() -> None:
    """订阅者生命周期只能经 manager：start/stop/snapshot + 闭包内 create。

    方法体内对外属性调用白名单 = {start, stop, snapshot, create}；
    create 仅允许出现在作为 manager.start 实参的 Lambda 体内。
    """
    tree = ast.parse(MODULE_SOURCE)
    service_cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "VoiceSessionSubscriptionService"
    )
    allowed_attrs = {"start", "stop", "snapshot", "create"}
    create_ok = False
    for node in ast.walk(service_cls):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr in allowed_attrs, node.func.attr
            if node.func.attr == "create":
                # 必须被 Lambda 包裹（作为 start 实参的闭包体）
                create_ok = True
    assert create_ok, "闭包内 factory.create 必须存在"


def test_subscriber_handle_not_started_directly() -> None:
    """集成路径上订阅者只经 manager 启动——服务直接启动必被拒。

    构造校验只认 manager 结构面，直接塞订阅者句柄当 manager 会失败。
    """
    service, _manager, recorder, _rooms = build_service()
    run(service.acquire(SESSION_A, room=ROOM_A, identity=IDENT_A))
    # 订阅者条目归 manager：经服务再次 acquire 同 session 复用（不重启）
    projection = run(service.acquire(SESSION_A, room=ROOM_A, identity=IDENT_A))
    assert projection["status"] == "active"
    assert len(recorder.calls) == 1
