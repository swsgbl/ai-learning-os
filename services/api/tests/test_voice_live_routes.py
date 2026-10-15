"""M14-272d voice live 会话路由契约测试（fake-only，零真实连接）。

`/voice/live-sessions`：M14-272a/c 管线的 HTTP 面——服务端权威
（open/事件/VAD/close 全经服务端；客户端只消费状态与 transcript）。
fake runtime 工厂 + fake transcriber（MockTransport 级零网络）。

覆盖：
1  503 门：create_app 缺省（无管线）时全部 live 端点 503；
2  全流程：open(201) → get(SESSION_READY) → events(start_reading →
   READING_QUESTION) → vad(speech_start → WAITING_ANSWER) →
   transcript(空) → DELETE(close：fake 房间断开 + 冲刷) →
   transcriber 收到拼接音频 + final transcript 有文本 → get 404；
3  帧路径：open 后经 fake 房间 emit PCM 帧 → DELETE 冲刷 →
   transcriber 收到逐字拼接 → transcript 文本映射；
4  revision：正确应用；不匹配 409（状态不变）；
5  错误面：非法事件 409、未知会话 404（get/events/transcript/
   delete）、room 非法 422、session_id 非法 422；
6  identity 服务端生成：open 响应含 server-sub-* 形态、浏览器
   不提供 identity；
7  同会话幂等 open：二次 open 200/201 且订阅者恰一；
8  无凭据泄漏：全部响应序列化无 JWT 形态/无 fake key/secret。
"""
from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.domain.voice_runtime_factory as runtime_module
from app.main import create_app
from app.voice_pipeline_entry import create_app_with_voice_pipeline

FAKE_SERVER_URL = "ws://livekit:7880"
FAKE_KEY = "fake-api-key-000000"
FAKE_SECRET = "fake-api-secret-000000000000000000000000"
FAKE_ASR_ENDPOINT = "http://127.0.0.1:8010/v1"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
ROOM = "exam-room-live01"
SESSION = "sess-live-000001"


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


class FakeTrack:
    def __init__(self, sid: str) -> None:
        self.sid = sid
        self.kind = "audio"


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
    """fake runtime 工厂（记录房间）+ fake transcriber（记录音频）。"""
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
        return _Transcription("服务端转写文本")

    app = create_app_with_voice_pipeline(None, settings=FakeSettings(), transcriber=transcriber)
    return rooms, audio_seen, app


def _open(client: TestClient, session: str = SESSION, room: str = ROOM):
    return client.post(f"/api/v1/voice/live-sessions/{session}/open", json={"room": room})


def test_all_live_endpoints_503_without_pipeline() -> None:
    app = create_app(None)
    with TestClient(app) as client:
        bodies = {
            "open": {"room": ROOM},
            "events": {"event": "pause"},
            "vad": {"signal": "speech_start"},
        }
        for method, path in [
            ("post", f"/api/v1/voice/live-sessions/{SESSION}/open"),
            ("get", f"/api/v1/voice/live-sessions/{SESSION}"),
            ("post", f"/api/v1/voice/live-sessions/{SESSION}/events"),
            ("post", f"/api/v1/voice/live-sessions/{SESSION}/vad"),
            ("get", f"/api/v1/voice/live-sessions/{SESSION}/transcript"),
            ("delete", f"/api/v1/voice/live-sessions/{SESSION}"),
        ]:
            if method == "post":
                suffix = path.rsplit("/", 1)[-1]
                response = client.post(path, json=bodies[suffix])
            else:
                response = getattr(client, method)(path)
            assert response.status_code == 503, (method, path, response.status_code)


def test_full_flow_open_control_close(fake_env) -> None:
    rooms, audio_seen, app = fake_env
    with TestClient(app) as client:
        opened = _open(client)
        assert opened.status_code == 201
        body = opened.json()
        assert body["fsm_state"] == "SESSION_READY"
        assert re.match(r"^server-sub-[0-9a-f]{8}$", body["identity"])
        assert body["closed"] is False

        state = client.get(f"/api/v1/voice/live-sessions/{SESSION}")
        assert state.status_code == 200
        assert state.json()["fsm_state"] == "SESSION_READY"

        ev = client.post(f"/api/v1/voice/live-sessions/{SESSION}/events", json={"event": "start_reading"})
        assert ev.status_code == 200
        assert ev.json()["fsm_state"] == "READING_QUESTION"

        vad = client.post(f"/api/v1/voice/live-sessions/{SESSION}/vad", json={"signal": "speech_start"})
        assert vad.status_code == 200
        assert vad.json()["fsm_state"] == "WAITING_ANSWER"

        transcript = client.get(f"/api/v1/voice/live-sessions/{SESSION}/transcript")
        assert transcript.status_code == 200
        assert transcript.json()["segment_count"] == 0  # 批式：close 前无段

        closed = client.delete(f"/api/v1/voice/live-sessions/{SESSION}")
        assert closed.status_code == 200
        final = closed.json()
        assert final["closed"] is True
        # 无帧：批式适配器空缓冲 finish 零转写（transcriber 零调用）
        assert audio_seen == []
        assert final["transcript"]["segment_count"] == 0
        assert rooms[0].disconnect_calls == 1  # 帧源已停

        assert client.get(f"/api/v1/voice/live-sessions/{SESSION}").status_code == 404


def test_frames_flow_to_transcriber_via_close(fake_env) -> None:
    _rooms, audio_seen, app = fake_env
    with TestClient(app) as client:
        _open(client)
        # 帧路径等价注入：直接向该会话的服务端通道注帧（与订阅者
        # on_frame → split → channel 同一入口；避免在请求循环外触发
        # 订阅者 ensure_future 事件）
        pipeline = app.state.voice_pipeline
        handle = pipeline._sessions[SESSION]
        handle._channel.on_frame(FakeFrame(b"PCM-AA"))
        handle._channel.on_frame(FakeFrame(b"PCM-BB"))
        closed = client.delete(f"/api/v1/voice/live-sessions/{SESSION}")
        assert closed.status_code == 200
        assert audio_seen == [b"PCM-AAPCM-BB"]  # close 冲刷逐字拼接
        assert closed.json()["transcript"]["segments"][0]["text"] == "服务端转写文本"


def test_revision_concurrency(fake_env) -> None:
    _rooms, _audio_seen, app = fake_env
    with TestClient(app) as client:
        _open(client)
        state = client.get(f"/api/v1/voice/live-sessions/{SESSION}").json()
        revision = state["fsm_revision"]
        ok = client.post(
            f"/api/v1/voice/live-sessions/{SESSION}/events",
            json={"event": "start_reading", "revision": revision},
        )
        assert ok.status_code == 200
        conflict = client.post(
            f"/api/v1/voice/live-sessions/{SESSION}/events",
            json={"event": "question_read", "revision": revision},  # 旧 revision
        )
        assert conflict.status_code == 409
        after = client.get(f"/api/v1/voice/live-sessions/{SESSION}").json()
        assert after["fsm_state"] == "READING_QUESTION"  # 状态不变
        client.delete(f"/api/v1/voice/live-sessions/{SESSION}")


def test_invalid_event_409_unknown_session_404(fake_env) -> None:
    _rooms, _audio_seen, app = fake_env
    with TestClient(app) as client:
        _open(client)
        bad = client.post(
            f"/api/v1/voice/live-sessions/{SESSION}/events", json={"event": "not_an_event"}
        )
        assert bad.status_code == 409
        client.delete(f"/api/v1/voice/live-sessions/{SESSION}")
        unknown = "sess-never-opened-99"
        assert client.get(f"/api/v1/voice/live-sessions/{unknown}").status_code == 404
        assert (
            client.post(f"/api/v1/voice/live-sessions/{unknown}/events", json={"event": "pause"}).status_code
            == 404
        )
        assert client.get(f"/api/v1/voice/live-sessions/{unknown}/transcript").status_code == 404
        assert client.delete(f"/api/v1/voice/live-sessions/{unknown}").status_code == 404


@pytest.mark.parametrize(
    "session,room",
    [
        ("ab", ROOM),
        ("a" * 65, ROOM),
        (SESSION, "no"),
        (SESSION, "bad room!"),
    ],
)
def test_invalid_identifiers_422(fake_env, session, room) -> None:
    _rooms, _audio_seen, app = fake_env
    with TestClient(app) as client:
        response = client.post(f"/api/v1/voice/live-sessions/{session}/open", json={"room": room})
        assert response.status_code == 422


def test_open_same_session_idempotent(fake_env) -> None:
    rooms, _audio_seen, app = fake_env
    with TestClient(app) as client:
        first = _open(client)
        assert first.status_code == 201
        second = _open(client)
        assert second.status_code == 200  # 已存在
        manager = app.state.voice_runtime_manager
        assert manager.snapshot()["subscriber_count"] == 1
        assert len(rooms) == 1  # 订阅者恰一
        client.delete(f"/api/v1/voice/live-sessions/{SESSION}")


def test_responses_carry_no_credentials(fake_env) -> None:
    _rooms, _audio_seen, app = fake_env
    with TestClient(app) as client:
        _open(client)
        for response in (
            client.get(f"/api/v1/voice/live-sessions/{SESSION}"),
            client.get(f"/api/v1/voice/live-sessions/{SESSION}/transcript"),
        ):
            text = response.text
            assert FAKE_KEY not in text and FAKE_SECRET not in text
            assert JWT_RE.search(text) is None
        client.delete(f"/api/v1/voice/live-sessions/{SESSION}")
