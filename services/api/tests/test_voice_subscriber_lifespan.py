"""M14-262 受控订阅者工厂 lifespan 注册契约测试（fake-only）。

覆盖：
1  非法非 None 注入类型在返回 app 之前确定性失败（lifespan 绝不
   半启动）；
2  省略/None 注入不安装 app.state.voice_subscriber_factory（仅
   管理器被接线——无隐式默认工厂）；
3  注入工厂仅在 lifespan 期间安装且保持实例同一性（退出后仍为
   同一对象供诊断，管理器已关闭）；
4  两个独立 create_app 的独立注入工厂互不共享；
5  fake-only 管理器/工厂集成（fake room/stream/track/frame）：
   注册工厂可创建句柄、管理器 start 拥有并启动、帧到达有界汇、
   订阅者投影净化；
6  显式管理器 stop 移除条目；此后 lifespan close 仍成功且幂等；
7  关停时仍活跃的订阅者被既有管理器 close 路径恰排空一次；
8  源码卫生：main.py 无默认工厂构造、无 LiveKit 导入/调用、
   无 HTTP/readiness 变化、无新增环境/凭据读取、管理器关停
   顺序不变；注入参数 keyword-only 且默认 None。

全合成 fake：不连真实 LiveKit 房间/语音引擎/DB 凭据/外部网络。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domain.voice_runtime_manager import VoiceRuntimeManager
from app.domain.voice_subscriber_factory import (
    ControlledVoiceSubscriberFactory,
    ControlledVoiceSubscriberHandle,
)
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]
MAIN_SOURCE = (REPO_ROOT / "services" / "api" / "app" / "main.py").read_text(
    encoding="utf-8"
)

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
FRAME_A = b"PCM-frame-a"  # 11 字节


# ---------- fake 注入件（与 M14-261 工厂测试同构，零网络） ----------


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
        self.handler: Callable[[object], None] | None = None

    def set_frame_handler(self, handler: Callable[[object], None]) -> None:
        self.handler = handler

    def set_error_handler(self, handler: Callable[[], None]) -> None:
        self.error_handler = handler

    async def close(self) -> None:
        return None

    def emit(self, frame: object) -> None:
        assert self.handler is not None
        self.handler(frame)


class FakeRoom:
    def __init__(self) -> None:
        self.handlers: dict[str, Callable[..., Any]] = {}
        self.connect_calls = 0
        self.disconnect_calls = 0

    def on(self, name: str, handler: Callable[..., Any]) -> None:
        self.handlers[name] = handler

    async def connect(self, token: str) -> None:
        self.connect_calls += 1  # token 只作参数出现，测试不持久化

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    def emit_track_subscribed(self, track: FakeTrack) -> None:
        self.handlers["track_subscribed"](track)


class Collaborators:
    """记录调用的注入协作者三件套（零网络）。"""

    def __init__(self) -> None:
        self.token_calls = 0
        self.room_calls = 0
        self.stream_calls = 0
        self.rooms: list[FakeRoom] = []
        self.streams: list[FakeAudioStream] = []

    def token_provider(self) -> str:
        self.token_calls += 1
        return FAKE_JWT

    def room_factory(self) -> FakeRoom:
        self.room_calls += 1
        room = FakeRoom()
        self.rooms.append(room)
        return room

    def audio_stream_factory(self, track: object) -> FakeAudioStream:
        self.stream_calls += 1
        stream = FakeAudioStream()
        self.streams.append(stream)
        return stream

    def make_factory(self) -> ControlledVoiceSubscriberFactory:
        return ControlledVoiceSubscriberFactory(
            token_provider=self.token_provider,
            room_factory=self.room_factory,
            audio_stream_factory=self.audio_stream_factory,
        )


async def drain(rounds: int = 16) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


def make_injected_app(collaborators: Collaborators | None = None):
    collaborators = collaborators or Collaborators()
    factory = collaborators.make_factory()
    app = create_app(None, voice_subscriber_factory=factory)
    return app, factory, collaborators


# ---------- 1：非法注入类型在返回 app 之前失败 ----------


@pytest.mark.parametrize(
    "bad_factory",
    [object(), "factory", 123, lambda: None, ControlledVoiceSubscriberHandle],
)
def test_invalid_injection_type_fails_before_app_return(bad_factory) -> None:
    with pytest.raises(ValueError, match="voice_subscriber_factory"):
        create_app(None, voice_subscriber_factory=bad_factory)


# ---------- 2：省略/None 注入不安装属性（无隐式默认工厂） ----------


def test_omitted_injection_installs_no_factory_attribute() -> None:
    app = create_app(None)
    assert not hasattr(app.state, "voice_subscriber_factory")
    with TestClient(app) as client:
        assert not hasattr(app.state, "voice_subscriber_factory")
        assert isinstance(app.state.voice_runtime_manager, VoiceRuntimeManager)
        assert client.get("/health").status_code == 200


def test_none_injection_installs_no_factory_attribute() -> None:
    app = create_app(None, voice_subscriber_factory=None)
    assert not hasattr(app.state, "voice_subscriber_factory")
    with TestClient(app) as client:
        assert not hasattr(app.state, "voice_subscriber_factory")
        # 仅管理器被接线（create_app(None) 语义不变）
        assert isinstance(app.state.voice_runtime_manager, VoiceRuntimeManager)
        assert client.get("/health").status_code == 200
    assert not hasattr(app.state, "voice_subscriber_factory")


# ---------- 3：注入工厂仅在 lifespan 期间安装且保持同一性 ----------


def test_supplied_factory_identity_during_and_after_lifespan() -> None:
    app, factory, _ = make_injected_app()
    assert not hasattr(app.state, "voice_subscriber_factory")  # lifespan 前：未安装
    seen: dict[str, object] = {}
    with TestClient(app) as client:
        assert app.state.voice_subscriber_factory is factory  # 同一实例
        assert isinstance(app.state.voice_runtime_manager, VoiceRuntimeManager)
        assert app.state.voice_runtime_manager.snapshot()["state"] == "open"
        assert client.get("/health").status_code == 200
        seen["manager"] = app.state.voice_runtime_manager
    # 退出后：属性仍为同一对象（诊断面），管理器已关闭
    assert app.state.voice_subscriber_factory is factory
    manager = seen["manager"]
    assert isinstance(manager, VoiceRuntimeManager)
    assert manager.snapshot()["state"] == "closed"


# ---------- 4：独立 app/工厂互不共享 ----------


def test_independent_apps_keep_independent_factories() -> None:
    first_app, first_factory, _ = make_injected_app()
    second_app, second_factory, _ = make_injected_app()
    assert first_factory is not second_factory
    with TestClient(first_app) as first_client, TestClient(second_app) as second_client:
        first_manager = first_app.state.voice_runtime_manager
        second_manager = second_app.state.voice_runtime_manager
        assert first_app.state.voice_subscriber_factory is first_factory
        assert second_app.state.voice_subscriber_factory is second_factory
        assert first_manager is not second_manager
        assert first_client.get("/health").status_code == 200
        assert second_client.get("/health").status_code == 200
    # 退出后各自保持注入的工厂实例
    assert first_app.state.voice_subscriber_factory is first_factory
    assert second_app.state.voice_subscriber_factory is second_factory
    assert first_manager.snapshot()["state"] == "closed"
    assert second_manager.snapshot()["state"] == "closed"


# ---------- 5：fake-only 管理器/工厂集成 ----------


def test_registered_factory_manager_start_frames_and_sanitized_projection() -> None:
    app, factory, collaborators = make_injected_app()
    owned: list[ControlledVoiceSubscriberHandle] = []

    with TestClient(app) as client:
        manager = app.state.voice_runtime_manager
        assert app.state.voice_subscriber_factory is factory

        async def scenario() -> None:
            # 注册工厂可创建句柄（注册 ≠ 启动——此刻零连接、零 token 调用）
            probe = factory.create("voice-life", "server-sub-life")
            assert isinstance(probe, ControlledVoiceSubscriberHandle)
            assert probe.state()["status"] == "idle"
            assert collaborators.token_calls == 0
            # 管理器以零参工厂闭包拥有并启动它（闭包创建的实例才是被管实例）
            def subscriber_factory() -> ControlledVoiceSubscriberHandle:
                handle = factory.create("voice-life", "server-sub-life")
                owned.append(handle)
                return handle

            projection = await manager.start("voice-life", subscriber_factory)
            assert projection["status"] == "active"
            assert projection["room"] == "voice-life"
            assert projection["identity"] == "server-sub-life"
            # 净化投影：无凭据键、无 JWT 形态
            assert "token" not in projection
            assert JWT_RE.search(json.dumps(projection, ensure_ascii=False)) is None
            # 帧到达有界汇：fake 音轨 → fake 帧 → 仅计数计量
            collaborators.rooms[0].emit_track_subscribed(FakeTrack("tr-life"))
            await drain()
            collaborators.streams[0].emit(FakeFrame(FRAME_A))
            handle = owned[0]
            snapshot = handle.sink_snapshot()
            assert snapshot["frame_count"] == 1
            assert snapshot["frame_byte_count"] == len(FRAME_A)
            serialized = json.dumps(
                {"state": handle.state(), "sink": snapshot},
                ensure_ascii=False,
            )
            assert FAKE_JWT not in serialized
            assert JWT_RE.search(serialized) is None
            assert FRAME_A.decode(errors="ignore") not in serialized
            # 显式 stop 移除条目
            await manager.stop("voice-life")
            assert manager.snapshot()["subscriber_count"] == 0
            assert collaborators.rooms[0].disconnect_calls == 1

        asyncio.run(scenario())
        assert client.get("/health").status_code == 200
        manager_state = manager.snapshot()["state"]

    # 退出后：close 排空（已无条目）仍成功且幂等
    assert manager_state == "open"
    outcome = asyncio.run(manager.close())
    assert outcome == {"stopped": 0, "failed": 0}
    assert asyncio.run(manager.close()) == {"stopped": 0, "failed": 0}
    assert collaborators.rooms[0].disconnect_calls == 1  # 无双重停止


# ---------- 7：关停时活跃订阅者恰被排空一次 ----------


def test_active_subscriber_drained_exactly_once_at_shutdown() -> None:
    app, factory, collaborators = make_injected_app()
    with TestClient(app):
        manager = app.state.voice_runtime_manager

        async def scenario() -> None:
            projection = await manager.start(
                "voice-hold",
                lambda: factory.create("voice-hold", "server-sub-hold"),
            )
            assert projection["status"] == "active"
            # 保持活跃：不显式 stop——留给 lifespan 关停排空

        asyncio.run(scenario())
        assert manager.snapshot()["subscriber_count"] == 1
    # lifespan 退出（manager.close 排空）：恰停一次
    assert collaborators.rooms[0].disconnect_calls == 1
    assert manager.snapshot()["state"] == "closed"
    assert manager.snapshot()["subscriber_count"] == 0
    # 再次 close 幂等：不重复停止（返回首次排空的记录结果——M14-258 契约）
    outcome = asyncio.run(manager.close())
    assert outcome == {"stopped": 1, "failed": 0}
    assert collaborators.rooms[0].disconnect_calls == 1


# ---------- 8：源码卫生 ----------


def test_source_hygiene_main_injection_surface() -> None:
    """main.py 卫生：无默认工厂构造、无 LiveKit、无新增环境读取、
    关停顺序不变；注入参数 keyword-only 且默认 None。"""
    # 无任何 ControlledVoiceSubscriberFactory 构造调用（isinstance
    # 引用与类型注解都不带调用括号）
    assert "ControlledVoiceSubscriberFactory(" not in MAIN_SOURCE
    # 无 LiveKit 导入/调用
    for forbidden in (
        "from livekit",
        "import livekit",
        "livekit_runtime_factories",
    ):
        assert forbidden not in MAIN_SOURCE, forbidden
    # 无新增环境/凭据读取面
    for forbidden in ("os.environ", "getenv("):
        assert forbidden not in MAIN_SOURCE, forbidden
    # 恰一个守卫式 app.state 赋值（不注入不安装）
    assignments = [
        line
        for line in MAIN_SOURCE.splitlines()
        if "app.state.voice_subscriber_factory = " in line
    ]
    assert len(assignments) == 1
    # 注入参数 keyword-only 且默认 None
    params = inspect.signature(create_app).parameters
    assert "voice_subscriber_factory" in params
    param = params["voice_subscriber_factory"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is None
    # 管理器关停顺序不变：close 先于 worker stop 先于 engine dispose；
    # 绝不另调 stop_all 双排空
    close_at = MAIN_SOURCE.index("await voice_runtime_manager.close()")
    worker_at = MAIN_SOURCE.index("await worker.stop()")
    engine_at = MAIN_SOURCE.index("await engine.dispose()")
    assert close_at < worker_at < engine_at
    assert "stop_all(" not in MAIN_SOURCE
    # 管理器构造仍恰一处（lifespan 闭包内，缩进锚定不变）
    constructors = [
        line for line in MAIN_SOURCE.splitlines() if "VoiceRuntimeManager(" in line
    ]
    assert len(constructors) == 1
    assert constructors[0].startswith("        voice_runtime_manager = VoiceRuntimeManager()")
