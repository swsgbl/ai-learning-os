"""M14-259 FastAPI lifespan 拥有 VoiceRuntimeManager 的接线契约测试（fake-only）。

覆盖：
1  lifespan 进入前 app.state.voice_runtime_manager 未接线；
2  活跃 lifespan（内存模式）期间管理器存在且 snapshot 报 open；
3  lifespan 退出后同一管理器实例报 closed（身份一致——非新建）；
4  SQLite 启动模式同样接线并关闭；
5  两个独立 create_app() 得到不同管理器实例（每 app/lifespan 恰一）；
6  活跃期间经管理器注册的 fake 订阅者在应用关停时恰被停止一次；
7  关停对管理器幂等（退出后再次 close 不重复停止该订阅者）；
8  源码卫生：main.py 无模块级/全局 VoiceRuntimeManager（唯一实例
   在 lifespan 闭包内构造）。

全合成 fake：不连真实 LiveKit 房间/语音引擎/DB 凭据/外部网络。
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.domain.voice_runtime_manager import VoiceRuntimeManager
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]
MAIN_SOURCE = (REPO_ROOT / "services" / "api" / "app" / "main.py").read_text(encoding="utf-8")


class FakeSubscriber:
    """最小 fake 订阅者（结构满足 ManagedSubscriberLike）。"""

    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0

    async def start(self) -> None:
        self.start_calls += 1

    async def stop(self) -> None:
        self.stop_calls += 1

    def state(self) -> dict:
        return {
            "status": "stopped" if self.stop_calls else "active",
            "room": "voice-fake",
            "identity": "server-sub-fake",
            "audio_track_count": 0,
            "audio_track_ids": [],
            "event_count": 2,
            "events": [
                {"type": "connecting", "detail": ""},
                {"type": "connected", "detail": ""},
            ],
        }


def test_manager_not_wired_before_lifespan_enters() -> None:
    """进入 lifespan 前：app.state 上无 voice_runtime_manager。"""
    app = create_app(None)
    assert not hasattr(app.state, "voice_runtime_manager") or (
        getattr(app.state, "voice_runtime_manager", None) is None
    )


def test_manager_open_during_lifespan_and_closed_after_exit() -> None:
    """活跃期间 open；退出后**同一实例** closed（身份一致）。"""
    app = create_app(None)
    seen: dict[str, object] = {}

    with TestClient(app) as client:
        manager = app.state.voice_runtime_manager
        assert isinstance(manager, VoiceRuntimeManager)
        assert manager.snapshot()["state"] == "open"
        seen["manager"] = manager
        assert client.get("/health").status_code == 200  # 应用阶段真实可用

    same = seen["manager"]
    assert isinstance(same, VoiceRuntimeManager)
    assert same.snapshot()["state"] == "closed"
    assert app.state.voice_runtime_manager is same  # 退出后仍可回读同一实例


def test_sqlite_startup_mode_wires_and_closes_manager(tmp_path) -> None:
    """SQLite 启动模式：同样接线（open）并在退出时关闭（closed）。"""
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'lifespan.db').as_posix()}"
    app = create_app(database_url)
    with TestClient(app) as client:
        manager = app.state.voice_runtime_manager
        assert manager.snapshot()["state"] == "open"
        assert client.get("/health").status_code == 200
    assert manager.snapshot()["state"] == "closed"


def test_two_apps_get_distinct_manager_instances() -> None:
    """每 create_app()/lifespan 恰一实例：两个独立 app 实例互不相同。"""
    with TestClient(create_app(None)) as first_client:
        first_manager = first_client.app.state.voice_runtime_manager
    with TestClient(create_app(None)) as second_client:
        second_manager = second_client.app.state.voice_runtime_manager
    assert first_manager is not second_manager
    assert first_manager.snapshot()["state"] == "closed"
    assert second_manager.snapshot()["state"] == "closed"


def test_subscriber_stopped_exactly_once_on_shutdown_and_close_idempotent() -> None:
    """活跃期间注册的订阅者：应用关停恰停一次；退出后手动再 close 不双停。"""
    app = create_app(None)
    subscriber = FakeSubscriber()

    def factory() -> FakeSubscriber:
        return subscriber

    with TestClient(app) as client:
        manager = app.state.voice_runtime_manager
        projection = asyncio.run(manager.start("voice-fake-key", factory))
        assert projection["key"] == "voice-fake-key"
        assert subscriber.start_calls == 1
        assert subscriber.stop_calls == 0
        assert client.get("/health").status_code == 200

    # lifespan 退出（manager.close() 排空）：订阅者恰被停止一次
    assert subscriber.stop_calls == 1
    assert manager.snapshot()["subscriber_count"] == 0
    assert manager.snapshot()["state"] == "closed"
    # 幂等：对已关闭管理器再次 close 不重复停止
    outcome = asyncio.run(manager.close())
    assert subscriber.stop_calls == 1
    assert outcome == {"stopped": 1, "failed": 0}  # 关闭时的排空结果记录


def test_main_has_no_module_level_manager_instance() -> None:
    """源码卫生：VoiceRuntimeManager 仅在 lifespan 闭包内构造。

    模块级（列 0 起）不得出现 VoiceRuntimeManager( 调用；唯一构造点
    必须缩进在 lifespan 函数体内。
    """
    # 模块级调用：行首（无缩进）即构造 → 全局单例形态，禁止
    for line in MAIN_SOURCE.splitlines():
        if line.startswith("app = create_app"):
            continue  # 既有 FastAPI 模块实例（非本切片面）
        assert not re.match(r"^\S.*VoiceRuntimeManager\(", line), line
    # 唯一构造点存在且缩进（lifespan 体内）
    constructors = [
        line for line in MAIN_SOURCE.splitlines() if "VoiceRuntimeManager(" in line
    ]
    assert len(constructors) == 1
    assert constructors[0].startswith("        voice_runtime_manager = VoiceRuntimeManager()")
    # 关停顺序：close 先于 worker.stop / engine.dispose（文本序断言）
    close_at = MAIN_SOURCE.index("await voice_runtime_manager.close()")
    worker_at = MAIN_SOURCE.index("await worker.stop()")
    engine_at = MAIN_SOURCE.index("await engine.dispose()")
    assert close_at < worker_at < engine_at
    # 绝不双排空：无独立 stop_all() 调用（close 即排空，M14-258 契约；
    # 断言调用形态——注释中的提及不算）
    assert "stop_all(" not in MAIN_SOURCE
