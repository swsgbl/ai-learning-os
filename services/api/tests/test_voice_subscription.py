"""M14-255 服务端 LiveKit 订阅基础契约测试（零网络/零运行时/全 fake 注入）。

覆盖：
1  subscribe-only token 角色边界（server-subscriber 恰好 join+subscribe，
   无发布/数据/管理/录制/agent/hidden；student grants 与公开端点行为不变）；
2  connect/subscribe/帧转发/轨清理/断开的 happy path（清理顺序确定：
   先全部音频流、后房间断开）；
3  token/connect/audio_stream 失败与 cleanup 失败（失败后零活资源、
   安全类别 + 通用净化消息）；
4  start/stop 幂等、无双重连接、终态不可重启；
5  有界状态与序列化泄漏防线（token/JWT/原始音频字节不出现在
   state()/事件/错误中——失败注入的异常消息故意携带 JWT 形态与
   音频字节，断言它们无法穿透）；
6  仅消费 audio 轨：非 audio 轨安全忽略。

本套件不发任何网络请求、不启动任何服务；fake Room/track/AudioStream
为鸭子协议替身（与域层 Protocol 对齐）。
"""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domain.voice_subscription import (
    CATEGORY_AUDIO_STREAM,
    CATEGORY_CLEANUP,
    CATEGORY_CONNECT,
    CATEGORY_TOKEN,
    ServerVoiceSubscriber,
    VoiceSubscriptionError,
)
from app.domain.voice_tokens import (
    ROLE_SERVER_SUBSCRIBER,
    ROLE_STUDENT,
    build_voice_token,
    verify_voice_token,
)
from app.main import create_app

API_KEY = "devkey"
API_SECRET = "test-secret-32-bytes-long-enough!"
ROOM = "voice-s1"
IDENTITY = "server-sub-1"
#: 合成 JWT 形态样本（结构同真 JWT，签名段占位——仅供泄漏防线断言）
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")
FRAME_BYTES = b"PCM-frame-bytes-do-not-persist"


# ---------- fake 注入件（与域层 Protocol 对齐的鸭子替身） ----------


class FakeFrame:
    def __init__(self, data: bytes) -> None:
        self._data = data

    @property
    def data(self) -> bytes:
        return self._data


class FakeAudioStream:
    def __init__(self, sid: str, ops: list[tuple[str, str]]) -> None:
        self.sid = sid
        self.ops = ops
        self.handler = None
        self.close_calls = 0
        self.close_raises = False

    def set_frame_handler(self, handler) -> None:
        self.handler = handler

    def close(self) -> None:
        self.close_calls += 1
        self.ops.append(("stream_close", self.sid))
        if self.close_raises:
            # 异常消息故意携带 JWT 形态——断言它无法穿透到事件/状态
            raise RuntimeError(f"close boom {FAKE_JWT}")

    def emit(self, frame: FakeFrame) -> None:
        assert self.handler is not None
        self.handler(frame)


class FakeTrack:
    def __init__(self, sid: str, kind: str = "audio") -> None:
        self.sid = sid
        self.kind = kind


class FakeRoom:
    def __init__(self, ops: list[tuple[str, str]]) -> None:
        self.ops = ops
        self.handlers: dict[str, object] = {}
        self.connect_calls = 0
        self.connected_tokens: list[str] = []
        self.connect_raises = False
        self.disconnect_calls = 0
        self.disconnect_raises = False

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler

    def connect(self, token: str) -> None:
        self.connect_calls += 1
        if self.connect_raises:
            # 异常消息故意携带 token——断言它无法穿透到事件/状态
            raise RuntimeError(f"connect boom {token}")
        self.connected_tokens.append(token)

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.ops.append(("room_disconnect", ""))
        if self.disconnect_raises:
            raise RuntimeError(f"disconnect boom {FAKE_JWT}")

    # 测试驱动用的触发器（对应注入的真实事件名）
    def emit_track_subscribed(self, track: FakeTrack) -> None:
        self.handlers["track_subscribed"](track)

    def emit_track_unsubscribed(self, track: FakeTrack) -> None:
        self.handlers["track_unsubscribed"](track)

    def emit_disconnected(self) -> None:
        self.handlers["disconnected"]()


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.frames: list[FakeFrame] = []

    def on_event(self, event) -> None:
        self.events.append(event)

    def on_frame(self, frame) -> None:
        self.frames.append(frame)


class Harness:
    """组装一个可编排失败的注入环境。"""

    def __init__(self) -> None:
        self.ops: list[tuple[str, str]] = []
        self.room = FakeRoom(self.ops)
        self.streams: dict[str, FakeAudioStream] = {}
        self.sink = RecordingSink()
        self.room_factory_calls = 0
        self.token_calls = 0
        self.token_raises = False
        self.stream_factory_raises = False

    def token_provider(self) -> str:
        self.token_calls += 1
        if self.token_raises:
            raise RuntimeError(f"token boom {FAKE_JWT}")
        return FAKE_JWT

    def room_factory(self) -> FakeRoom:
        self.room_factory_calls += 1
        return self.room

    def audio_stream_factory(self, track: FakeTrack) -> FakeAudioStream:
        if self.stream_factory_raises:
            raise RuntimeError(f"stream boom {FAKE_JWT} {FRAME_BYTES!r}")
        stream = FakeAudioStream(track.sid, self.ops)
        self.streams[track.sid] = stream
        return stream

    def controller(self, **kwargs) -> ServerVoiceSubscriber:
        return ServerVoiceSubscriber(
            room=kwargs.pop("room", ROOM),
            identity=kwargs.pop("identity", IDENTITY),
            token_provider=self.token_provider,
            room_factory=self.room_factory,
            audio_stream_factory=self.audio_stream_factory,
            event_sink=self.sink,
            **kwargs,
        )


def event_types(sink: RecordingSink) -> list[str]:
    return [event.type for event in sink.events]


def serialized(harness: Harness) -> str:
    """控制器 state + 全部收到事件的序列化文本（泄漏断言靶面）。"""
    controller_state = json.dumps(harness.controller_state, ensure_ascii=False)
    events_text = json.dumps(
        [event.to_dict() for event in harness.sink.events], ensure_ascii=False, default=str
    )
    return controller_state + events_text


@pytest.fixture
def harness() -> Harness:
    unit = Harness()
    controller = unit.controller()
    unit.controller_state = controller.state()  # 占位；泄漏断言前刷新
    unit._controller = controller
    return unit


def controller_of(unit: Harness) -> ServerVoiceSubscriber:
    return unit._controller


def refresh_state(unit: Harness) -> str:
    unit.controller_state = controller_of(unit).state()
    return serialized(unit)


# ---------- subscribe-only token 角色边界 ----------


def _subscriber_token():
    return build_voice_token(
        ROOM, IDENTITY, api_key=API_KEY, api_secret=API_SECRET,
        role=ROLE_SERVER_SUBSCRIBER,
    )


def test_server_subscriber_grants_are_join_and_subscribe_only() -> None:
    """恰好 join + subscribe：无发布/数据通道/管理/录制/agent/hidden。"""
    token = _subscriber_token()
    assert token.room == ROOM and token.identity == IDENTITY
    claims = verify_voice_token(token.token, api_key=API_KEY, api_secret=API_SECRET)
    video = claims.video
    assert video.room_join is True and video.room == ROOM
    assert video.can_subscribe is True
    # 不允许的能力逐项显式为空
    assert not video.can_publish
    assert not video.can_publish_data
    for forbidden in (
        video.room_admin,
        video.room_create,
        video.room_record,
        video.hidden,
        video.recorder,
        video.agent,
        video.ingress_admin,
    ):
        assert not forbidden


def test_student_grants_unchanged_alongside_new_role() -> None:
    """student 角色四项权限保持（新角色不侵蚀既有 grants）。"""
    token = build_voice_token(ROOM, "student-1", api_key=API_KEY, api_secret=API_SECRET)
    video = verify_voice_token(token.token, api_key=API_KEY, api_secret=API_SECRET).video
    assert video.room_join and video.can_publish and video.can_subscribe and video.can_publish_data


@pytest.fixture
def livekit_env(monkeypatch):
    monkeypatch.setenv("LIVEKIT_API_KEY", API_KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", API_SECRET)
    monkeypatch.setenv("LIVEKIT_URL", "ws://127.0.0.1:7880")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_public_endpoint_rejects_server_subscriber_role(livekit_env) -> None:
    """公开 /token 端点行为零变化：server-subscriber 请求依旧 422。"""
    with TestClient(create_app(None)) as client:
        response = client.post(
            "/api/v1/voice/token",
            json={"room": ROOM, "role": ROLE_SERVER_SUBSCRIBER},
        )
        assert response.status_code == 422
        assert "未知角色" in response.json()["detail"]
        # student 依旧正常签发（公开行为不变）
        ok = client.post("/api/v1/voice/token", json={"room": ROOM})
        assert ok.status_code == 200
        assert ok.json()["role"] == ROLE_STUDENT


# ---------- happy path：connect → subscribe → 帧转发 → 清理 → 断开 ----------


def test_start_connects_with_in_memory_token_only(harness) -> None:
    controller = controller_of(harness)
    controller.start()
    assert controller.state()["status"] == "active"
    assert harness.room.connect_calls == 1
    # token 只作为 connect 参数出现（测试内部比对，不打印不持久）
    assert harness.room.connected_tokens == [FAKE_JWT]
    assert event_types(harness.sink) == ["connecting", "connected"]
    assert JWT_RE.search(refresh_state(harness)) is None


def test_subscribed_audio_track_forwards_frames_without_retaining(harness) -> None:
    controller = controller_of(harness)
    controller.start()
    track = FakeTrack("TR-audio-1")
    harness.room.emit_track_subscribed(track)
    state = controller.state()
    assert state["audio_track_count"] == 1
    assert state["audio_track_ids"] == ["TR-audio-1"]
    assert "track_subscribed" in event_types(harness.sink)
    # 帧直转 sink：控制器不缓存（state 序列化无帧字节）
    harness.streams["TR-audio-1"].emit(FakeFrame(FRAME_BYTES))
    assert len(harness.sink.frames) == 1
    assert FRAME_BYTES.decode() not in refresh_state(harness)


def test_non_audio_track_is_ignored(harness) -> None:
    controller = controller_of(harness)
    controller.start()
    harness.room.emit_track_subscribed(FakeTrack("TR-video-1", kind="video"))
    assert controller.state()["audio_track_count"] == 0
    assert harness.streams == {}
    ignored = [e for e in harness.sink.events if e.type == "track_ignored"]
    assert len(ignored) == 1 and "kind=video" in ignored[0].detail
    assert not any(e.type == "track_subscribed" for e in harness.sink.events)


def test_track_unsubscribe_closes_stream(harness) -> None:
    controller = controller_of(harness)
    controller.start()
    track = FakeTrack("TR-audio-1")
    harness.room.emit_track_subscribed(track)
    harness.room.emit_track_unsubscribed(track)
    assert harness.streams["TR-audio-1"].close_calls == 1
    assert controller.state()["audio_track_count"] == 0
    assert "track_finished" in event_types(harness.sink)


def test_stop_cleans_streams_before_room_and_is_idempotent(harness) -> None:
    controller = controller_of(harness)
    controller.start()
    for sid in ("TR-a", "TR-b"):
        harness.room.emit_track_subscribed(FakeTrack(sid))
    controller.stop()
    # 确定顺序：全部音频流先关（按 sid 排序），房间最后断开
    assert harness.ops == [
        ("stream_close", "TR-a"),
        ("stream_close", "TR-b"),
        ("room_disconnect", ""),
    ]
    assert harness.room.disconnect_calls == 1
    assert controller.state()["status"] == "stopped"
    # 幂等：重复 stop 零新事件/零新清理
    events_before = len(harness.sink.events)
    controller.stop()
    assert harness.room.disconnect_calls == 1
    assert len(harness.sink.events) == events_before


def test_room_disconnected_cleans_up_and_later_stop_is_noop(harness) -> None:
    controller = controller_of(harness)
    controller.start()
    harness.room.emit_track_subscribed(FakeTrack("TR-a"))
    harness.room.emit_disconnected()
    assert controller.state()["status"] == "stopped"
    assert harness.streams["TR-a"].close_calls == 1
    assert harness.room.disconnect_calls == 0  # 远端已断，无需再调
    events_before = len(harness.sink.events)
    controller.stop()
    assert len(harness.sink.events) == events_before  # 幂等 no-op


# ---------- 失败路径：零活资源 + 安全类别 + 通用净化消息 ----------


def test_token_failure_fails_closed_without_leaking(harness) -> None:
    harness.token_raises = True  # 异常消息携带 JWT 形态
    controller = controller_of(harness)
    controller.start()
    state = controller.state()
    assert state["status"] == "failed"
    failed = [e for e in harness.sink.events if e.type == "failed"]
    assert len(failed) == 1 and failed[0].category == CATEGORY_TOKEN
    assert failed[0].detail == "语音房间凭据获取失败"
    assert harness.room.connect_calls == 0  # 从未连接
    assert JWT_RE.search(refresh_state(harness)) is None


def test_connect_failure_disconnects_room_without_leaking(harness) -> None:
    controller = controller_of(harness)
    harness.room.connect_raises = True  # 异常消息携带 token 原文
    controller.start()
    state = controller.state()
    assert state["status"] == "failed"
    failed = [e for e in harness.sink.events if e.type == "failed"]
    assert len(failed) == 1 and failed[0].category == CATEGORY_CONNECT
    assert failed[0].detail == "LiveKit 房间连接失败"
    assert harness.room.disconnect_calls == 1  # 不留连接房间（尽力断开）
    assert harness.streams == {}
    assert JWT_RE.search(refresh_state(harness)) is None


def test_audio_stream_failure_cleans_everything(harness) -> None:
    controller = controller_of(harness)
    controller.start()
    harness.room.emit_track_subscribed(FakeTrack("TR-ok"))  # 先有一路成功
    harness.stream_factory_raises = True  # 第二路建流失败（异常携带 JWT+帧字节）
    harness.room.emit_track_subscribed(FakeTrack("TR-bad"))
    state = controller.state()
    assert state["status"] == "failed"
    failed = [e for e in harness.sink.events if e.type == "failed"]
    assert failed[-1].category == CATEGORY_AUDIO_STREAM
    assert failed[-1].detail == "音频流创建失败"
    # 失败不留活资源：已建流已关、房间已断
    assert harness.streams["TR-ok"].close_calls == 1
    assert state["audio_track_count"] == 0
    assert harness.room.disconnect_calls == 1
    leaked = refresh_state(harness)
    assert JWT_RE.search(leaked) is None
    assert FRAME_BYTES.decode() not in leaked


def test_cleanup_failure_still_reaches_stopped(harness) -> None:
    controller = controller_of(harness)
    controller.start()
    track = FakeTrack("TR-a")
    harness.room.emit_track_subscribed(track)
    harness.streams["TR-a"].close_raises = True
    harness.room.disconnect_raises = True
    controller.stop()
    state = controller.state()
    assert state["status"] == "stopped"  # 清理失败不阻塞终态
    assert harness.room.disconnect_calls == 1  # 恰一次尝试
    cleanup_failures = [
        e for e in harness.sink.events if e.type == "failed" and e.category == CATEGORY_CLEANUP
    ]
    assert len(cleanup_failures) == 2  # 流关失败 + 房间断失败（尽力记录）
    assert JWT_RE.search(refresh_state(harness)) is None


def test_terminal_controller_cannot_restart(harness) -> None:
    controller = controller_of(harness)
    controller.start()
    controller.stop()
    with pytest.raises(VoiceSubscriptionError, match="不可重启"):
        controller.start()
    assert harness.room.connect_calls == 1  # 无双重连接


def test_failed_controller_cannot_restart(harness) -> None:
    harness.token_raises = True
    controller = controller_of(harness)
    controller.start()
    with pytest.raises(VoiceSubscriptionError, match="不可重启"):
        controller.start()


# ---------- 幂等 start / 构造校验 / 有界性 ----------


def test_start_is_idempotent_while_active(harness) -> None:
    controller = controller_of(harness)
    controller.start()
    controller.start()  # active 期重复 start：no-op
    assert harness.room_factory_calls == 1
    assert harness.token_calls == 1
    assert harness.room.connect_calls == 1
    assert event_types(harness.sink) == ["connecting", "connected"]


def test_stop_before_start_seals_lifecycle(harness) -> None:
    controller = controller_of(harness)
    controller.stop()
    assert controller.state()["status"] == "stopped"
    with pytest.raises(VoiceSubscriptionError, match="不可重启"):
        controller.start()


def test_constructor_validation() -> None:
    unit = Harness()
    with pytest.raises(VoiceSubscriptionError, match="room"):
        unit.controller(room="  ")
    with pytest.raises(VoiceSubscriptionError, match="identity"):
        unit.controller(identity="")
    with pytest.raises(VoiceSubscriptionError, match="max_events"):
        unit.controller(max_events=0)


def test_event_log_is_bounded(harness) -> None:
    controller = controller_of(harness)
    controller.start()
    for _ in range(80):  # 超过默认环形上限 64
        harness.room.emit_track_subscribed(FakeTrack("TR-video", kind="video"))
    state = controller.state()
    assert state["event_count"] == 82  # connecting + connected + 80（全量计数）
    assert len(state["events"]) == 64  # 投影有界（滚动淘汰最旧）
    # 严格滚动断言：connecting/connected 已被淘汰出窗口，保留的 64 条
    # 恰为第 17..80 个 track_ignored——最旧与最新都是 track_ignored，
    # 且窗口内不存在更早的 connecting/connected
    types = [event["type"] for event in state["events"]]
    assert types[0] == "track_ignored"
    assert types[-1] == "track_ignored"
    assert set(types) == {"track_ignored"}
    assert all("kind=video" in event["detail"] for event in state["events"])


def test_track_unsubscribe_close_failure_fails_closed(harness) -> None:
    """supervisor 修正 2：单轨 close 失败 = 可能仍活的资源——fail-closed。

    剩余流被关闭、房间被断开、终态 failed、事件只记安全 cleanup
    类别 + 通用净化消息（close 异常携带 JWT 形态亦无法穿透）。
    """
    controller = controller_of(harness)
    controller.start()
    harness.room.emit_track_subscribed(FakeTrack("TR-a"))
    harness.room.emit_track_subscribed(FakeTrack("TR-b"))
    harness.streams["TR-a"].close_raises = True  # close 异常携带 FAKE_JWT
    harness.room.emit_track_unsubscribed(FakeTrack("TR-a"))
    state = controller.state()
    assert state["status"] == "failed"
    assert state["audio_track_count"] == 0
    # 剩余流 TR-b 也被关闭（零活资源），房间被断开恰一次
    assert harness.streams["TR-b"].close_calls == 1
    assert harness.streams["TR-a"].close_calls == 1
    assert harness.room.disconnect_calls == 1
    # 事件面：恰一条 failed，类别 cleanup，通用净化消息（非 cause 原文）
    failed = [e for e in harness.sink.events if e.type == "failed"]
    assert len(failed) == 1
    assert failed[0].category == CATEGORY_CLEANUP
    assert failed[0].detail == "订阅资源清理失败"
    assert not any(e.type == "track_finished" for e in harness.sink.events)
    assert JWT_RE.search(refresh_state(harness)) is None
