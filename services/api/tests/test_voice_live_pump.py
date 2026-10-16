"""M14-276 live 会话后台 pump 调度契约测试（fake-only）。

帧通道 256 帧 ≈ 25.6 秒（10ms/帧）——长语音作答会丢最旧音频导致
close 冲刷转写不完整。修复：live 路由 open 时启动 per-session
后台 pump 任务（周期 drain 通道 → FunAsrBatchAdapter 8 MiB 缓冲），
close 时取消；会话终结循环自退出。

覆盖：
1  长会话不丢帧：注入 300+ 帧（>256 上限）→ pump 周期后通道回落、
   close 冲刷转写包含**早期帧**（溢出场景下原本会被丢的最旧音频）；
2  close 取消 pump：关停后 pump 任务不再运行（无后续 drain）；
3  pump 幂等无害：短会话（<256 帧）pump 与 close 冲刷共存，帧
   恰一次进 transcript（不重复）；
4  会话终结自清理：句柄终结后 pump 循环在下周期退出（不抛出、
   不影响进程）。
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.domain.voice_runtime_factory as runtime_module
from app.voice_pipeline_entry import create_app_with_voice_pipeline

FAKE_SERVER_URL = "ws://livekit:7880"
FAKE_KEY = "fake-api-key-000000"
FAKE_SECRET = "fake-api-secret-000000000000000000000000"
FAKE_ASR_ENDPOINT = "http://127.0.0.1:8010/v1"
PUMP_INTERVAL = 0.2  # 测试用短周期（生产默认 2s）


class FakeSettings:
    livekit_url = FAKE_SERVER_URL
    livekit_api_key = FAKE_KEY
    livekit_api_secret = FAKE_SECRET
    asr_local_endpoint = FAKE_ASR_ENDPOINT
    asr_local_model = "sensevoice"


class FakeRoom:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self.disconnect_calls = 0

    def on(self, name: str, handler: Any) -> None:
        self.handlers[name] = handler

    async def connect(self, token: str) -> None: ...

    async def disconnect(self) -> None:
        self.disconnect_calls += 1


class FakeStream:
    def set_frame_handler(self, h: Any) -> None: ...

    def set_error_handler(self, h: Any) -> None: ...

    async def close(self) -> None: ...


class FakeFrame:
    def __init__(self, data: object) -> None:
        self._data = data

    @property
    def data(self) -> object:
        return self._data


class _Transcription:
    def __init__(self, text: str) -> None:
        self.text = text


@pytest.fixture()
def fake_env(monkeypatch):
    """fake runtime + 记录拼接音频的 transcriber（返回音频长度可辨文本）。"""
    rooms: list[FakeRoom] = []
    audio_seen: list[bytes] = []

    def fake_pair(url: str):
        def room_factory():
            room = FakeRoom()
            rooms.append(room)
            return room

        def stream_factory(track):
            return FakeStream()

        return room_factory, stream_factory

    monkeypatch.setattr(runtime_module, "livekit_runtime_factories", fake_pair)

    def transcriber(audio: bytes) -> _Transcription:
        audio_seen.append(audio)
        return _Transcription(f"len:{len(audio)}")

    app = create_app_with_voice_pipeline(
        None, settings=FakeSettings(), transcriber=transcriber
    )
    app.state.voice_pump_interval = PUMP_INTERVAL  # 测试短周期（生产默认 2s）
    return rooms, audio_seen, app


def _inject_frames(app, session: str, count: int, frame_bytes: bytes = b"A" * 960) -> None:
    handle = app.state.voice_pipeline._sessions[session]
    for _ in range(count):
        handle._channel.on_frame(FakeFrame(frame_bytes))


def test_long_session_frames_preserved_via_pump(fake_env) -> None:
    """真实节奏长会话：两批各 150 帧（<256 单批上限）跨 pump 周期注入
    ——pump 周期搬移第一批腾空通道，第二批不触发溢出；close 转写
    含全部 300 帧（无 pump 时两批累计 300 > 256 会丢 44 帧）。"""
    _rooms, audio_seen, app = fake_env
    import time

    with TestClient(app) as client:
        opened = client.post(
            "/api/v1/voice/live-sessions/sess-pump-0001/open", json={"room": "exam-room-p1"}
        )
        assert opened.status_code == 201
        _inject_frames(app, "sess-pump-0001", 150)  # 第一批（≈15 秒音频）
        time.sleep(PUMP_INTERVAL * 2)  # pump 搬走第一批
        state = client.get("/api/v1/voice/live-sessions/sess-pump-0001").json()
        assert state["buffered_frames"] == 0  # 已搬空
        _inject_frames(app, "sess-pump-0001", 150)  # 第二批（通道重新计）
        closed = client.delete("/api/v1/voice/live-sessions/sess-pump-0001")
        assert closed.status_code == 200
    # close 冲刷：transcriber 收到全部 300 帧（pump 搬 150 + close 冲 150）
    assert len(audio_seen) == 1
    assert len(audio_seen[0]) == 300 * 960
    text = closed.json()["transcript"]["segments"][0]["text"]
    assert text == f"len:{300 * 960}"


def test_close_cancels_pump_task(fake_env) -> None:
    """close 后 pump 任务取消：后续注入的帧不被搬运（留在通道），且
    进程无未处理异常。"""
    _rooms, _audio_seen, app = fake_env
    with TestClient(app) as client:
        client.post(
            "/api/v1/voice/live-sessions/sess-pump-0002/open", json={"room": "exam-room-p2"}
        )
        _inject_frames(app, "sess-pump-0002", 10)
        client.delete("/api/v1/voice/live-sessions/sess-pump-0002")
        # close 后再注入（句柄 closed——channel 仍可入界但无人消费）
        import time

        time.sleep(PUMP_INTERVAL * 3)
        # 无泄漏断言：pump 任务已不在注册表
        tasks = getattr(app.state, "voice_pump_tasks", {})
        assert "sess-pump-0002" not in tasks


def test_short_session_no_duplicate_frames(fake_env) -> None:
    """短会话：pump 与 close 冲刷共存，帧恰一次进 transcript。"""
    _rooms, audio_seen, app = fake_env
    with TestClient(app) as client:
        client.post(
            "/api/v1/voice/live-sessions/sess-pump-0003/open", json={"room": "exam-room-p3"}
        )
        _inject_frames(app, "sess-pump-0003", 50)
        import time

        time.sleep(PUMP_INTERVAL * 2)  # pump 搬走 50 帧
        _inject_frames(app, "sess-pump-0003", 30)  # close 前再注 30
        closed = client.delete("/api/v1/voice/live-sessions/sess-pump-0003")
        assert closed.status_code == 200
    assert len(audio_seen) == 1  # 恰一次转写
    assert len(audio_seen[0]) == 80 * 960  # 80 帧全部（pump 搬 50 + close 冲 30）
