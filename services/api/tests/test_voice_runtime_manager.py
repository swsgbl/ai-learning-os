"""M14-257 语音运行时管理器契约测试（零网络/零运行时/全 fake）。

覆盖：
1  键校验（非字符串/空白/超长/控制字符拒绝；strip 规范化）与容量
   上限（满载在 **factory 构造之前** 拒绝——factory 计数为 0）；
2  成功注册 + 有界净化 snapshot（白名单键、events 截断、键排序）；
3  并发 start 同键恰创建一个订阅者、factory 恰调用一次、各调用方
   拿到等价投影；
4  不同键独立管理；
5  factory 失败零残留（无条目、无活资源——fake 记录 stop）；
6  订阅者 start 失败：尽力清理 + 零注册 + 通用净化错误（异常文本
   不透传）；
7  stop 幂等（未知键 no-op；已停键再停 no-op）、成功即移除条目
   （释放容量）；
8  stop_all：单项 stop 抛错不阻断其余（计数 stopped/failed，条目
   全部移除——fail-closed 不再显示 active）；
9  终态订阅者同键拒绝重启（不静默替换）；
10 投影排除异常文本/token 形态/帧/无界历史（脏 fake 故意塞入
   token 形态、exception 键、帧字节、超长 events——断言全部被
   白名单/正则/截断挡下）；
11 新域模块零 asyncio.run/嵌套循环/线程桥（文本契约）；
12 集成式：真实 ServerVoiceSubscriber（M14-255/256 域类，配结构
   等价的 fake room/stream 注入）交由管理器全生命周期管理——
   覆盖管理协议与订阅者真实 state 键域的契合；此边界足够的理由：
   订阅者自身行为已由 test_voice_subscription 26+ 项锁定，这里
   只验管理面与其投影契约，无需重复整套订阅者 harness 断言。
"""
from __future__ import annotations

import asyncio
import re

import pytest

from app.domain.voice_runtime_manager import (
    DEFAULT_MAX_EVENTS_PER_SUBSCRIBER,
    DEFAULT_MAX_SUBSCRIBERS,
    KEY_MAX_LENGTH,
    VoiceRuntimeManager,
    VoiceRuntimeManagerError,
    validate_key,
)
from app.domain.voice_subscription import ServerVoiceSubscriber

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")


def run(coro):
    asyncio.run(coro)


# ---------- fake 订阅者（结构满足 ManagedSubscriberLike） ----------


class FakeSubscriber:
    """可编排的 fake：记录 start/stop、可注入失败与脏状态字段。"""

    def __init__(
        self,
        *,
        status: str = "active",
        start_raises: bool = False,
        stop_raises: bool = False,
        dirty_state: dict | None = None,
        extra_events: int = 0,
        start_delay: float = 0.0,
    ) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.start_raises = start_raises
        self.stop_raises = stop_raises
        self.start_delay = start_delay
        self._status = status
        self._dirty = dirty_state or {}
        self._extra_events = extra_events

    async def start(self) -> None:
        self.start_calls += 1
        if self.start_delay > 0:
            await asyncio.sleep(self.start_delay)
        if self.start_raises:
            raise RuntimeError(f"start boom {FAKE_JWT}")

    async def stop(self) -> None:
        self.stop_calls += 1
        self._status = "stopped"
        if self.stop_raises:
            raise RuntimeError(f"stop boom {FAKE_JWT}")

    def state(self) -> dict:
        base = {
            "status": self._status,
            "room": "voice-s1",
            "identity": "server-sub-1",
            "audio_track_count": 0,
            "audio_track_ids": [],
            "event_count": 2 + self._extra_events,
            "events": [
                {"type": "connecting", "detail": ""},
                {"type": "connected", "detail": ""},
            ]
            + [
                {"type": "track_ignored", "detail": f"kind=video#{i}"}
                for i in range(self._extra_events)
            ],
        }
        base.update(self._dirty)  # 脏键注入（异常文本/帧/token 键）
        return base


class Factory:
    """记录构造次数的工厂（容量前置断言的靶面）。"""

    def __init__(self, subscriber: FakeSubscriber) -> None:
        self.subscriber = subscriber
        self.calls = 0

    def __call__(self) -> FakeSubscriber:
        self.calls += 1
        return self.subscriber


def make_factory(**kwargs) -> tuple[Factory, FakeSubscriber]:
    subscriber = FakeSubscriber(**kwargs)
    return Factory(subscriber), subscriber


# ---------- 键校验 ----------


@pytest.mark.parametrize(
    "bad_key",
    [
        None,
        123,
        "",
        "   ",
        "\t\n",
        "a" * (KEY_MAX_LENGTH + 1),
        "bad\x00key",
        "bad\x1bkey",
        "bad\x7fkey",
    ],
)
def test_validate_key_rejects_invalid(bad_key) -> None:
    with pytest.raises(VoiceRuntimeManagerError, match="管理键非法"):
        validate_key(bad_key)


def test_validate_key_normalizes_whitespace() -> None:
    assert validate_key("  voice-s1  ") == "voice-s1"
    assert validate_key("voice-s1") == "voice-s1"  # 同一规范键


def test_validate_key_accepts_reasonable_keys() -> None:
    for key in ("voice-s1", "session_0f1e-2d3c", "a" * KEY_MAX_LENGTH, "键-中文"):
        assert validate_key(key) == key


# ---------- 容量（factory 构造之前拒绝） ----------


def test_capacity_enforced_before_factory_construction() -> None:
    manager = VoiceRuntimeManager(max_subscribers=1)
    first_factory, first = make_factory()
    second_factory, _ = make_factory()

    async def scenario() -> None:
        await manager.start("k1", first_factory)
        assert first.start_calls == 1
        with pytest.raises(VoiceRuntimeManagerError, match="容量上限"):
            await manager.start("k2", second_factory)
        assert second_factory.calls == 0  # 满载在构造之前拒绝
        await manager.stop("k1")  # 停止释放容量
        await manager.start("k2", second_factory)
        assert second_factory.calls == 1

    run(scenario())


def test_constructor_rejects_non_positive_limits() -> None:
    with pytest.raises(VoiceRuntimeManagerError, match="max_subscribers"):
        VoiceRuntimeManager(max_subscribers=0)
    with pytest.raises(VoiceRuntimeManagerError, match="max_events"):
        VoiceRuntimeManager(max_events_per_subscriber=0)


# ---------- 成功注册与有界净化 snapshot ----------


def test_start_registers_and_snapshot_is_bounded_sanitized() -> None:
    manager = VoiceRuntimeManager()
    factory, _ = make_factory()

    async def scenario() -> None:
        projection = await manager.start("voice-s1", factory)
        assert projection["key"] == "voice-s1"
        assert projection["status"] == "active"
        snap = manager.snapshot()
        assert snap["subscriber_count"] == 1
        assert snap["max_subscribers"] == DEFAULT_MAX_SUBSCRIBERS
        assert snap["keys"] == ["voice-s1"]
        assert len(snap["subscribers"]) == 1
        # 管理器自身投影无任何内部对象引用
        assert all(
            not isinstance(value, FakeSubscriber)
            for value in snap["subscribers"][0].values()
        )

    run(scenario())


def test_snapshot_events_truncated_to_limit() -> None:
    manager = VoiceRuntimeManager(max_events_per_subscriber=4)
    factory, _ = make_factory(extra_events=50)  # 52 条事件 >> 上限 4

    async def scenario() -> None:
        await manager.start("k", factory)
        projection = manager.snapshot()["subscribers"][0]
        assert projection["event_count"] == 52  # 全量计数保留（真实）
        assert len(projection["events"]) == 4  # 历史截断（有界）

    run(scenario())


def test_snapshot_keys_sorted_deterministic() -> None:
    manager = VoiceRuntimeManager()
    factories = {key: make_factory()[0] for key in ("c-key", "a-key", "b-key")}

    async def scenario() -> None:
        for key, factory in factories.items():
            await manager.start(key, factory)
        assert manager.snapshot()["keys"] == ["a-key", "b-key", "c-key"]

    run(scenario())


# ---------- 并发与重复 start：每键恰一实例 ----------


def test_concurrent_start_single_factory_call_single_subscriber() -> None:
    manager = VoiceRuntimeManager()
    factory, subscriber = make_factory()

    async def scenario() -> None:
        results = await asyncio.gather(
            manager.start("k", factory),
            manager.start("k", factory),
            manager.start(" k ", factory),  # strip 后同键
        )
        assert factory.calls == 1  # 恰构造一次
        assert subscriber.start_calls == 1  # 恰启动一次
        keys = {projection["key"] for projection in results}
        assert keys == {"k"}  # 各调用方拿到等价投影（规范键）
        assert manager.snapshot()["subscriber_count"] == 1

    run(scenario())


def test_duplicate_start_after_success_returns_projection_without_new_factory() -> None:
    manager = VoiceRuntimeManager()
    factory, _ = make_factory()

    async def scenario() -> None:
        first = await manager.start("k", factory)
        second = await manager.start("k", factory)
        assert factory.calls == 1
        assert second["key"] == first["key"] == "k"

    run(scenario())


def test_different_keys_managed_independently() -> None:
    manager = VoiceRuntimeManager()
    factory_a, sub_a = make_factory()
    factory_b, sub_b = make_factory()

    async def scenario() -> None:
        await manager.start("k-a", factory_a)
        await manager.start("k-b", factory_b)
        assert manager.snapshot()["keys"] == ["k-a", "k-b"]
        await manager.stop("k-a")
        snap = manager.snapshot()
        assert snap["keys"] == ["k-b"]
        assert sub_a.stop_calls == 1
        assert sub_b.stop_calls == 0  # k-b 不受 k-a 停止影响

    run(scenario())


# ---------- 失败路径：零残留 ----------


def test_factory_failure_leaves_no_entry_no_resource() -> None:
    manager = VoiceRuntimeManager()

    def boom_factory() -> FakeSubscriber:
        raise RuntimeError(f"factory boom {FAKE_JWT}")

    async def scenario() -> None:
        with pytest.raises(VoiceRuntimeManagerError, match="订阅者构造失败"):
            await manager.start("k", boom_factory)
        assert manager.snapshot()["subscriber_count"] == 0
        await manager.stop("k")  # 幂等 no-op（无残留可清）

    run(scenario())


def test_start_failure_cleans_up_and_leaves_no_entry() -> None:
    manager = VoiceRuntimeManager()
    factory, subscriber = make_factory(start_raises=True)

    async def scenario() -> None:
        with pytest.raises(VoiceRuntimeManagerError, match="订阅者启动失败"):
            await manager.start("k", factory)
        assert manager.snapshot()["subscriber_count"] == 0
        assert subscriber.stop_calls == 1  # 尽力清理被调用

    run(scenario())


def test_start_failure_error_message_is_sanitized() -> None:
    manager = VoiceRuntimeManager()
    factory, _ = make_factory(start_raises=True)  # 异常消息携带 JWT 形态

    async def scenario() -> None:
        try:
            await manager.start("k", factory)
        except VoiceRuntimeManagerError as error:
            assert FAKE_JWT not in str(error)
            assert JWT_RE.search(str(error)) is None
        else:
            pytest.fail("应抛 VoiceRuntimeManagerError")

    run(scenario())


# ---------- stop 语义：幂等 / 移除 / fail-closed ----------


def test_stop_is_idempotent_and_releases_entry() -> None:
    manager = VoiceRuntimeManager()
    factory, subscriber = make_factory()

    async def scenario() -> None:
        await manager.start("k", factory)
        await manager.stop("k")
        events = subscriber.stop_calls
        await manager.stop("k")  # 二次 stop：no-op
        await manager.stop("never-existed")  # 未知键：no-op
        assert subscriber.stop_calls == events
        assert manager.snapshot()["subscriber_count"] == 0

    run(scenario())


def test_stop_all_continues_after_failure_and_removes_all() -> None:
    manager = VoiceRuntimeManager(max_subscribers=4)
    factory_a, _ = make_factory()
    factory_bad, bad = make_factory(stop_raises=True)
    factory_c, _ = make_factory()

    async def scenario() -> None:
        await manager.start("k-a", factory_a)
        await manager.start("k-bad", factory_bad)
        await manager.start("k-c", factory_c)
        outcome = await manager.stop_all()
        assert outcome == {"stopped": 2, "failed": 1}
        # fail-closed：清理失败的条目也已移除，绝不继续显示 active
        assert manager.snapshot()["subscriber_count"] == 0
        assert bad.stop_calls == 1

    run(scenario())


def test_stop_failure_error_is_sanitized() -> None:
    manager = VoiceRuntimeManager()
    factory, _ = make_factory(stop_raises=True)  # 异常消息携带 JWT 形态

    async def scenario() -> None:
        await manager.start("k", factory)
        with pytest.raises(VoiceRuntimeManagerError, match="停止清理失败"):
            await manager.stop("k")
        assert manager.snapshot()["subscriber_count"] == 0  # 条目仍被移除

    run(scenario())


def test_terminal_subscriber_same_key_rejects_restart() -> None:
    manager = VoiceRuntimeManager()
    first_factory, first = make_factory()
    second_factory, _ = make_factory()

    async def scenario() -> None:
        await manager.start("k", first_factory)
        first._status = "failed"  # 模拟订阅者自身进入终态（条目仍在册）
        with pytest.raises(VoiceRuntimeManagerError, match="拒绝重启"):
            await manager.start("k", second_factory)
        assert second_factory.calls == 0  # 绝不静默替换

    run(scenario())


# ---------- 投影净化：脏状态被白名单/正则/截断挡下 ----------


def test_projection_excludes_dirty_fields_from_subscriber_state() -> None:
    manager = VoiceRuntimeManager()
    dirty = {
        "error": f"RuntimeError: boom {FAKE_JWT}",  # 异常文本 + token 形态
        "token": FAKE_JWT,  # 凭据键
        "frames": [b"\x00\x01 raw audio"],  # 原始帧
        "exception": "ValueError: secret detail",
        "internal_room_handle": object(),  # 内部对象
    }
    factory, _ = make_factory(dirty_state=dirty, extra_events=100)

    async def scenario() -> None:
        await manager.start("k", factory)

    run(scenario())
    projection = manager.snapshot()["subscribers"][0]
    text = repr(projection)
    for forbidden_key in ("error", "token", "frames", "exception", "internal_room_handle"):
        assert forbidden_key not in projection
    assert FAKE_JWT not in text
    assert JWT_RE.search(text) is None
    assert "raw audio" not in text
    assert len(projection["events"]) == DEFAULT_MAX_EVENTS_PER_SUBSCRIBER


def test_dirty_event_details_are_jwt_sanitized() -> None:
    manager = VoiceRuntimeManager()
    dirty = {
        "events": [
            {"type": "failed", "detail": f"boom {FAKE_JWT}"},
            {"type": "stopped", "detail": ""},
        ]
    }
    factory, _ = make_factory(dirty_state=dirty)

    async def scenario() -> None:
        await manager.start("k", factory)

    run(scenario())
    projection = manager.snapshot()["subscribers"][0]
    serialized = repr(projection["events"])
    assert JWT_RE.search(serialized) is None
    assert "[REDACTED-JWT]" in serialized


# ---------- async-native 纪律（文本契约） ----------


def test_no_forbidden_blocking_or_thread_constructs() -> None:
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "domain"
        / "voice_runtime_manager.py"
    ).read_text(encoding="utf-8")
    for pattern in (
        "asyncio.run(",
        "run_until_complete(",
        "threading.",
        "Thread(",
        "ProcessPool",
        "new_event_loop(",
    ):
        assert pattern not in source, pattern
    # 无模块级可变单例（显式构造；模块级只允许常量/类/函数定义）
    assert "VoiceRuntimeManager(" not in source.split("class VoiceRuntimeManager")[0]


# ---------- 集成式：真实 ServerVoiceSubscriber 交由管理器 ----------


class _MiniHarness:
    """结构等价的迷你注入环境（与 test_voice_subscription 的 Harness
    同构——仅保留管理器集成所需的最小面；订阅者自身行为已由
    test_voice_subscription 的 21 项测试锁定，此处不重复断言）。
    """

    def __init__(self) -> None:
        self.connect_calls = 0
        self.disconnect_calls = 0

        class _Room:
            def __init__(inner) -> None:
                inner.handlers = {}

            def on(inner, event, handler) -> None:
                inner.handlers[event] = handler

            async def connect(inner, token: str) -> None:
                self.connect_calls += 1

            async def disconnect(inner) -> None:
                self.disconnect_calls += 1

        class _Stream:
            def __init__(inner, track) -> None:
                inner._handler = None

            def set_frame_handler(inner, handler) -> None:
                inner._handler = handler

            def set_error_handler(inner, handler) -> None:
                inner._error = handler

            async def close(inner) -> None:
                inner._closed = True

        class _Sink:
            def on_event(inner, event) -> None: ...

            def on_frame(inner, frame) -> None: ...

        self.room = _Room()
        self.stream = _Stream(None)
        self.sink = _Sink()
        calls = {"factory": 0}

        def factory() -> ServerVoiceSubscriber:
            calls["factory"] += 1
            return ServerVoiceSubscriber(
                room="voice-int",
                identity="server-sub-int",
                token_provider=lambda: FAKE_JWT,
                room_factory=lambda: self.room,
                audio_stream_factory=lambda track: self.stream,
                event_sink=self.sink,
            )

        self.factory_calls = calls
        self.factory = factory


def test_manager_manages_real_server_voice_subscriber_lifecycle() -> None:
    manager = VoiceRuntimeManager()
    harness = _MiniHarness()

    async def scenario() -> None:
        projection = await manager.start("voice-int", harness.factory)
        assert projection["key"] == "voice-int"
        assert projection["status"] == "active"  # 真订阅者真实状态键域
        assert projection["room"] == "voice-int"
        assert projection["identity"] == "server-sub-int"
        assert harness.factory_calls["factory"] == 1
        assert harness.connect_calls == 1  # 真订阅者真的启动了
        # 同键重复 start：不新实例不新连接
        await manager.start("voice-int", harness.factory)
        assert harness.factory_calls["factory"] == 1
        assert harness.connect_calls == 1
        # stop：移除条目 + 真订阅者断开房间
        await manager.stop("voice-int")
        assert harness.disconnect_calls == 1
        assert manager.snapshot()["subscriber_count"] == 0
        # 移除后同键可重新注册（新实例、新连接——非重启旧终态实例）
        await manager.start("voice-int", harness.factory)
        assert harness.factory_calls["factory"] == 2
        assert harness.connect_calls == 2
        await manager.stop_all()

    run(scenario())
    assert manager.snapshot()["subscriber_count"] == 0


# ---------- supervisor R1 修正：容量预留 / pending 停止 / 取消去重 / 异常链 ----------


def test_concurrent_distinct_keys_reserve_capacity_r1() -> None:
    """R1 修正 1：进行中启动计入容量预留。

    max=1 时并发不同键 start：恰一个 factory/start 幸存，另一个在
    factory 构造之前被拒（工厂调用总数恰 1）。
    """
    manager = VoiceRuntimeManager(max_subscribers=1)
    factory_a, _ = make_factory(start_delay=0.02)
    factory_b, _ = make_factory()

    async def scenario() -> None:
        results = await asyncio.gather(
            manager.start("k-a", factory_a),
            manager.start("k-b", factory_b),
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, VoiceRuntimeManagerError)]
        successes = [r for r in results if isinstance(r, dict)]
        assert len(errors) == 1 and "容量上限" in str(errors[0])
        assert len(successes) == 1
        # 两工厂总调用恰 1（被拒一侧零构造）
        assert factory_a.calls + factory_b.calls == 1
        assert manager.snapshot()["subscriber_count"] == 1

    run(scenario())


def test_stop_during_pending_start_awaits_then_stops_r1() -> None:
    """R1 修正 2a：pending 期间 stop 等待启动落定——成功则停产物并移除。

    旧缺陷：stop 立刻查不到条目返回（count 0），启动随后完成留下
    count 1 且 stop_calls=0 的活资源。
    """
    manager = VoiceRuntimeManager()
    factory, subscriber = make_factory(start_delay=0.02)

    async def scenario() -> None:
        start_task = asyncio.ensure_future(manager.start("k", factory))
        await asyncio.sleep(0.005)  # 进入 pending（慢启动进行中）
        assert manager.snapshot()["subscriber_count"] == 0  # 尚未注册
        await manager.stop("k")  # 必须等待 pending 落定再决定
        assert await start_task  # 启动本身完成（拿到投影）
        assert subscriber.stop_calls == 1  # 产物订阅者被停
        assert manager.snapshot()["subscriber_count"] == 0

    run(scenario())


def test_stop_during_pending_failed_start_is_noop_r1() -> None:
    """R1 修正 2b：pending 启动自清失败后 stop 为 no-op（不传播启动错误）。"""
    manager = VoiceRuntimeManager()
    factory, subscriber = make_factory(start_delay=0.02, start_raises=True)

    async def scenario() -> None:
        start_task = asyncio.ensure_future(manager.start("k", factory))
        await asyncio.sleep(0.005)
        await manager.stop("k")  # 不抛——启动失败已自清，stop no-op
        with pytest.raises(VoiceRuntimeManagerError, match="订阅者启动失败"):
            await start_task
        assert subscriber.stop_calls == 1  # 启动失败的尽力清理
        assert manager.snapshot()["subscriber_count"] == 0

    run(scenario())


def test_stop_all_includes_pending_starts_r1() -> None:
    """R1 修正 2c：stop_all 覆盖进行中启动键，不只注册条目。"""
    manager = VoiceRuntimeManager(max_subscribers=4)
    factory_live, _ = make_factory()
    factory_pending, pending_subscriber = make_factory(start_delay=0.02)

    async def scenario() -> None:
        await manager.start("k-live", factory_live)
        asyncio.ensure_future(manager.start("k-pending", factory_pending))
        await asyncio.sleep(0.005)  # k-pending 进行中（未注册）
        outcome = await manager.stop_all()
        assert outcome == {"stopped": 2, "failed": 0}
        assert pending_subscriber.stop_calls == 1  # pending 落定后也被停
        assert manager.snapshot()["subscriber_count"] == 0

    run(scenario())


def test_cancelled_caller_keeps_same_key_dedup_r1() -> None:
    """R1 修正 3：调用方取消不破坏同键去重。

    首个调用方在慢启动中途被取消；同键第二个 start 仍复用进行中的
    启动任务（第二个工厂零调用），最终恰一实例注册。
    """
    manager = VoiceRuntimeManager()
    first_factory, _ = make_factory(start_delay=0.05)
    second_factory, _ = make_factory()

    async def scenario() -> None:
        first_caller = asyncio.ensure_future(manager.start("k", first_factory))
        await asyncio.sleep(0.01)  # 首次启动进行中（任务存活）
        first_caller.cancel()
        try:
            await first_caller
        except asyncio.CancelledError:
            pass  # 调用方已取消（底层启动任务不受影响——shield 隔离）
        # 同键二次 start：必须复用仍进行中的首次任务（去重映射仍存在）
        projection = await manager.start("k", second_factory)
        assert projection["key"] == "k"
        assert first_factory.calls == 1
        assert second_factory.calls == 0  # 绝不给同键第二个工厂
        assert manager.snapshot()["subscriber_count"] == 1
        await manager.stop_all()

    run(scenario())


def test_manager_error_chain_sanitized_r1() -> None:
    """R1 修正 4：管理器错误异常链抑制——__cause__/traceback 零泄漏。

    factory 抛错、订阅者 start 抛错、订阅者 stop 抛错三条路径的
    包装错误：__cause__ 为 None；str/repr/traceback 序列化均不含
    合成 JWT 形态文本（异常消息均故意携带 FAKE_JWT）。
    """
    import traceback

    def assert_sanitized(error: VoiceRuntimeManagerError) -> None:
        assert error.__cause__ is None
        serialized = "\n".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        assert FAKE_JWT not in str(error)
        assert FAKE_JWT not in repr(error)
        assert FAKE_JWT not in serialized
        assert JWT_RE.search(serialized) is None

    manager = VoiceRuntimeManager()

    def boom_factory() -> FakeSubscriber:
        raise RuntimeError(f"factory boom {FAKE_JWT}")

    async def scenario() -> None:
        with pytest.raises(VoiceRuntimeManagerError) as factory_error:
            await manager.start("k-factory", boom_factory)
        assert_sanitized(factory_error.value)

        start_factory, _ = make_factory(start_raises=True)
        with pytest.raises(VoiceRuntimeManagerError) as start_error:
            await manager.start("k-start", start_factory)
        assert_sanitized(start_error.value)

        stop_factory, _ = make_factory(stop_raises=True)
        await manager.start("k-stop", stop_factory)
        with pytest.raises(VoiceRuntimeManagerError) as stop_error:
            await manager.stop("k-stop")
        assert_sanitized(stop_error.value)

    run(scenario())


# ---------- M14-258：关闭/排空边界（fake-only） ----------


def test_stop_all_barrier_rejects_new_start_before_factory() -> None:
    """stop_all 进行中（慢启动排空）：并发新 start 在 factory 构造之前被拒，
    pending 产物仍被排空清理。"""
    manager = VoiceRuntimeManager(max_subscribers=4)
    slow_factory, slow_sub = make_factory(start_delay=0.03)
    new_factory, _ = make_factory()

    async def scenario() -> None:
        asyncio.ensure_future(manager.start("k-slow", slow_factory))
        await asyncio.sleep(0.005)  # k-slow 进入 pending
        stop_all_task = asyncio.ensure_future(manager.stop_all())
        await asyncio.sleep(0.001)  # stop_all 已置屏障
        with pytest.raises(VoiceRuntimeManagerError, match="正在排空"):
            await manager.start("k-new", new_factory)
        assert new_factory.calls == 0  # 拒绝发生在构造之前
        outcome = await stop_all_task
        assert outcome == {"stopped": 1, "failed": 0}
        assert slow_sub.stop_calls == 1  # pending 产物被排空
        assert manager.snapshot()["subscriber_count"] == 0

    run(scenario())


def test_stop_all_restores_open_state_after_completion() -> None:
    """正常 stop_all 完成后回 open——管理器仍可接受新启动。"""
    manager = VoiceRuntimeManager()
    factory, _ = make_factory()

    async def scenario() -> None:
        await manager.start("k", factory)
        assert manager.snapshot()["state"] == "open"
        await manager.stop_all()
        assert manager.snapshot()["state"] == "open"
        await manager.start("k", factory)  # 仍可重新启动
        assert factory.calls == 2

    run(scenario())


def test_close_waits_pending_stops_product_and_closes() -> None:
    """close 期间有 pending 启动：等待其落定、停掉产物、移除、终态 closed。"""
    manager = VoiceRuntimeManager()
    factory, subscriber = make_factory(start_delay=0.03)

    async def scenario() -> None:
        asyncio.ensure_future(manager.start("k", factory))
        await asyncio.sleep(0.005)
        assert manager.snapshot()["state"] == "open"
        outcome = await manager.close()
        assert outcome == {"stopped": 1, "failed": 0}
        assert subscriber.stop_calls == 1  # 产物被停
        assert manager.snapshot()["subscriber_count"] == 0
        assert manager.snapshot()["state"] == "closed"

    run(scenario())


def test_close_is_permanently_terminal_and_idempotent() -> None:
    """close 永久终态：后续 start 在 factory 构造之前永拒；重复 close no-op。"""
    manager = VoiceRuntimeManager()
    factory, subscriber = make_factory()
    later_factory, later_sub = make_factory()

    async def scenario() -> None:
        await manager.start("k", factory)
        first = await manager.close()
        assert first == {"stopped": 1, "failed": 0}
        assert subscriber.stop_calls == 1
        with pytest.raises(VoiceRuntimeManagerError, match="已关闭"):
            await manager.start("k2", later_factory)
        assert later_factory.calls == 0  # 拒绝在构造之前
        assert later_sub.start_calls == 0
        second = await manager.close()  # 幂等 no-op
        assert second == {"stopped": 1, "failed": 0}
        assert subscriber.stop_calls == 1  # 绝不双停
        assert manager.snapshot()["state"] == "closed"

    run(scenario())


def test_concurrent_close_calls_do_not_double_stop() -> None:
    """并发 close 共享同一排空任务——订阅者 stop 恰一次、结果一致。"""
    manager = VoiceRuntimeManager()
    factory, subscriber = make_factory(start_delay=0.02)

    async def scenario() -> None:
        asyncio.ensure_future(manager.start("k", factory))
        await asyncio.sleep(0.005)
        first, second, third = await asyncio.gather(
            manager.close(), manager.close(), manager.close()
        )
        assert first == second == third == {"stopped": 1, "failed": 0}
        assert subscriber.stop_calls == 1  # 恰一次停止
        assert manager.snapshot()["state"] == "closed"

    run(scenario())


def test_close_continues_after_single_stop_failure() -> None:
    """close 中单项停止失败：其余条目仍被停/移除，结果为有界确定性计数。"""
    manager = VoiceRuntimeManager(max_subscribers=4)
    factory_a, sub_a = make_factory()
    factory_bad, _ = make_factory(stop_raises=True)  # 异常消息携带 JWT 形态
    factory_c, sub_c = make_factory()

    async def scenario() -> None:
        await manager.start("k-a", factory_a)
        await manager.start("k-bad", factory_bad)
        await manager.start("k-c", factory_c)
        outcome = await manager.close()
        assert outcome == {"stopped": 2, "failed": 1}
        assert sub_a.stop_calls == 1 and sub_c.stop_calls == 1
        assert manager.snapshot()["subscriber_count"] == 0  # 全部移除
        assert manager.snapshot()["state"] == "closed"
        # 排空结果是纯计数——无异常文本/token 形态
        assert FAKE_JWT not in repr(outcome) and JWT_RE.search(repr(outcome)) is None

    run(scenario())


def test_snapshot_exposes_lifecycle_state_without_internals() -> None:
    """snapshot 暴露 open/draining/closed 显式状态且不含订阅者内部对象。"""
    manager = VoiceRuntimeManager()
    factory, _ = make_factory(start_delay=0.05)

    async def scenario() -> None:
        assert manager.snapshot()["state"] == "open"
        asyncio.ensure_future(manager.start("k", factory))
        await asyncio.sleep(0.005)
        drain_task = asyncio.ensure_future(manager.stop_all())
        await asyncio.sleep(0.001)
        mid = manager.snapshot()
        assert mid["state"] == "draining"
        assert not any(
            isinstance(value, FakeSubscriber)
            for value in mid.values()
        )
        await drain_task
        assert manager.snapshot()["state"] == "open"
        await manager.close()
        assert manager.snapshot()["state"] == "closed"

    run(scenario())


def test_shutdown_errors_and_states_sanitized() -> None:
    """排空/关闭面的错误与投影零泄漏：draining/closed 拒绝消息与 close
    结果不含订阅者异常文本/token 形态（stop 抛错消息携带 FAKE_JWT）。"""
    manager = VoiceRuntimeManager()
    bad_factory, _ = make_factory(stop_raises=True)

    async def scenario() -> None:
        await manager.start("k-bad", bad_factory)
        outcome = await manager.close()
        assert outcome == {"stopped": 0, "failed": 1}
        serialized = repr(outcome) + repr(manager.snapshot())
        assert FAKE_JWT not in serialized
        assert JWT_RE.search(serialized) is None
        try:
            await manager.start("k-after", make_factory()[0])
        except VoiceRuntimeManagerError as error:
            assert error.__cause__ is None
            assert FAKE_JWT not in str(error)
        else:
            pytest.fail("closed 后 start 应拒绝")

    run(scenario())
