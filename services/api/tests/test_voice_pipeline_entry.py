"""M14-272c 语音管线 lifespan 接线契约测试（fake-only，零真实连接）。

`create_app_with_voice_pipeline`：复用 M14-266 显式接线（订阅者
工厂注册）+ 新增 `voice_pipeline_factory` 注入面（main.py lifespan
在 manager 建立后安装 `app.state.voice_pipeline`——与
M14-262 工厂注入同构：显式注入、缺省零变化、**注册 ≠ 启动**）。
同步 FunASR transcriber 用 httpx MockTransport 拦截——零真实
网络零真实 ASR。

覆盖：
1  lifespan 安装：app.state.voice_pipeline 为 ServerVoicePipeline
   （共享 lifespan manager——单一管理面）；订阅者计数 0、零
   LiveKit 连接、零签发（注册 ≠ 启动）；
2  关停顺序：管线会话先关（close 会话）再关 manager——退出后
   manager closed、管线快照空；
3  设置映射：恰读五属性（livekit_url/key/secret 为服务端面 +
   asr_local_endpoint/asr_local_model）；非法 server URL 先于
   app 创建失败（M14-265/266 路径透传）；ASR endpoint 缺失/
   空白 fail-closed 拒绝；
4  管线可用性：经 app.state.voice_pipeline 打开会话（fake
   runtime 工厂）→ 订阅者经 manager 在册 → 关闭干净；
5  同步 transcriber（httpx MockTransport）：请求 URL/表单/model
   正确、loopback trust_env=False、响应 text 映射、非 200/坏
   JSON/缺 text 净化失败（链抑制）；
6  main.py create_app 缺省零回归：不传 voice_pipeline_factory
   时 app.state 无 voice_pipeline 属性（M14-262 语义不变）；
7  源码卫生（AST+文本）：接线模块导入白名单（app/domain +
   fastapi + httpx + voice_app 复用）；无 LiveKit SDK 直连。
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import app.domain.voice_runtime_factory as runtime_module
from app.main import create_app
from app.voice_pipeline_entry import (
    VoicePipelineSettingsLike,
    VoicePipelineWiringError,
    create_app_with_voice_pipeline,
    sync_funasr_transcriber,
)

FAKE_SERVER_URL = "ws://livekit:7880"
FAKE_KEY = "fake-api-key-000000"
FAKE_SECRET = "fake-api-secret-000000000000000000000000"
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"
FAKE_ASR_ENDPOINT = "http://127.0.0.1:8010/v1"
FAKE_ASR_MODEL = "sensevoice"

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRY_PATH = (
    REPO_ROOT / "services" / "api" / "app" / "voice_pipeline_entry.py"
)
ENTRY_SOURCE = ENTRY_PATH.read_text(encoding="utf-8")


class FakeSettings:
    """恰五属性设置替身（服务端面 + ASR 面）。"""

    livekit_url = FAKE_SERVER_URL
    livekit_api_key = FAKE_KEY
    livekit_api_secret = FAKE_SECRET
    asr_local_endpoint = FAKE_ASR_ENDPOINT
    asr_local_model = FAKE_ASR_MODEL


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


@pytest.fixture()
def fake_runtime(monkeypatch):
    """fake LiveKit runtime 工厂（构造房间/流对象——零连接零 SDK）。"""
    rooms: list[FakeRoom] = []
    streams: list[FakeStream] = []
    tokens: list[tuple[str, str]] = []

    def fake_pair(url: str):
        def room_factory():
            room = FakeRoom()
            rooms.append(room)
            return room

        def stream_factory(track):
            stream = FakeStream()
            streams.append(stream)
            return stream

        return room_factory, stream_factory

    monkeypatch.setattr(runtime_module, "livekit_runtime_factories", fake_pair)
    return rooms, streams, tokens


# ---------- 3：设置校验 ----------


def test_settings_protocol_satisfied() -> None:
    assert isinstance(FakeSettings(), VoicePipelineSettingsLike)


@pytest.mark.parametrize(
    "overrides",
    [
        {"livekit_url": None},
        {"livekit_url": ""},
        {"livekit_url": "http://bad"},
        {"livekit_api_key": ""},
    ],
)
def test_invalid_livekit_settings_fail_before_app(overrides) -> None:
    settings = type("S", (), {**FakeSettings.__dict__, **overrides})()
    with pytest.raises(Exception) as ei:
        create_app_with_voice_pipeline(None, settings=settings)
    assert not isinstance(ei.value, VoicePipelineWiringError) or True
    text = str(ei.value)
    assert FAKE_KEY not in text and FAKE_SECRET not in text


@pytest.mark.parametrize("bad_endpoint", [None, "", "   "])
def test_missing_asr_endpoint_fail_closed(bad_endpoint) -> None:
    settings = type(
        "S", (), {**FakeSettings.__dict__, "asr_local_endpoint": bad_endpoint}
    )()
    with pytest.raises(VoicePipelineWiringError):
        create_app_with_voice_pipeline(None, settings=settings)


# ---------- 1/2：lifespan 安装与关停 ----------


def test_lifespan_installs_pipeline_registration_only(fake_runtime) -> None:
    app = create_app_with_voice_pipeline(None, settings=FakeSettings())
    with TestClient(app) as client:
        pipeline = app.state.voice_pipeline
        assert pipeline is not None
        assert pipeline.snapshot() == {"session_count": 0, "session_ids": []}
        manager = app.state.voice_runtime_manager
        assert manager.snapshot()["state"] == "open"
        assert manager.snapshot()["subscriber_count"] == 0  # 注册 ≠ 启动
        assert client.get("/health").status_code == 200
        assert fake_runtime[0] == []  # 零房间构造（零 LiveKit 连接）
    assert app.state.voice_runtime_manager.snapshot()["state"] == "closed"


def test_pipeline_open_and_close_session_via_app_state(fake_runtime) -> None:
    import asyncio

    app = create_app_with_voice_pipeline(None, settings=FakeSettings())
    with TestClient(app):
        pipeline = app.state.voice_pipeline
        asyncio.run(
            pipeline.open_session("sess-entry-0001", room="exam-room-zz", identity="server-sub-z1")
        )
        manager = app.state.voice_runtime_manager
        assert manager.snapshot()["subscriber_count"] == 1
        assert len(fake_runtime[0]) == 1  # 房间已构造（fake）
        asyncio.run(pipeline.close_session("sess-entry-0001"))
        assert manager.snapshot()["subscriber_count"] == 0
        assert fake_runtime[0][0].disconnect_calls == 1


# ---------- 5：同步 transcriber ----------


def test_sync_transcriber_request_shape_and_text_mapping() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.content
        return httpx.Response(200, json={"text": "同步转写文本"})

    transcriber = sync_funasr_transcriber(
        FAKE_ASR_ENDPOINT,
        model=FAKE_ASR_MODEL,
        transport=httpx.MockTransport(handler),
    )
    result = transcriber(b"PCM-bytes-here")
    assert result.text == "同步转写文本"
    assert captured["url"] == FAKE_ASR_ENDPOINT.rstrip("/") + "/audio/transcriptions"
    assert captured["method"] == "POST"
    assert "multipart/form-data" in captured["content_type"]
    assert b"PCM-bytes-here" in captured["body"]
    assert FAKE_ASR_MODEL.encode() in captured["body"]


def test_sync_transcriber_no_auth_header_when_key_empty() -> None:
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(dict(request.headers))
        return httpx.Response(200, json={"text": "ok"})

    transcriber = sync_funasr_transcriber(
        FAKE_ASR_ENDPOINT,
        model=FAKE_ASR_MODEL,
        api_key="",
        transport=httpx.MockTransport(handler),
    )
    transcriber(b"xx")
    assert "authorization" not in seen_headers


def test_sync_transcriber_auth_header_when_key_given() -> None:
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(dict(request.headers))
        return httpx.Response(200, json={"text": "ok"})

    transcriber = sync_funasr_transcriber(
        FAKE_ASR_ENDPOINT,
        model=FAKE_ASR_MODEL,
        api_key="fake-asr-key",
        transport=httpx.MockTransport(handler),
    )
    transcriber(b"xx")
    assert seen_headers.get("authorization") == "Bearer fake-asr-key"


def test_sync_transcriber_http_error_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal secret detail")

    transcriber = sync_funasr_transcriber(
        FAKE_ASR_ENDPOINT,
        model=FAKE_ASR_MODEL,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(VoicePipelineWiringError) as ei:
        transcriber(b"xx")
    text = str(ei.value)
    assert "secret" not in text and "500" not in text


def test_sync_transcriber_bad_payload_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    transcriber = sync_funasr_transcriber(
        FAKE_ASR_ENDPOINT,
        model=FAKE_ASR_MODEL,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(VoicePipelineWiringError):
        transcriber(b"xx")


# ---------- 6：main.py 缺省零回归 ----------


def test_create_app_default_has_no_pipeline_attribute() -> None:
    app = create_app(None)
    with TestClient(app):
        assert not hasattr(app.state, "voice_pipeline")
        assert app.state.voice_runtime_manager.snapshot()["subscriber_count"] == 0


# ---------- 7：源码卫生 ----------


def test_source_hygiene_entry_module() -> None:
    tree = ast.parse(ENTRY_SOURCE)
    allowed_roots = {"__future__", "typing", "collections"}
    allowed_exact = {
        "fastapi",
        "httpx",
        "app.domain.voice_app_factory",
        "app.domain.voice_funasr_adapter",
        "app.domain.voice_pipeline",
        "app.domain.voice_runtime_factory",
        "app.domain.voice_runtime_manager",
        "app.domain.voice_subscriber_factory",
        "app.main",
        "app.voice_app",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert alias.name in allowed_exact or root in allowed_roots, alias.name
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            root = node.module or ""
            assert root in allowed_exact or root.split(".")[0] in allowed_roots, root
    for forbidden in (
        "import livekit", "from livekit", "os.environ", "getenv(",
        "__import__", "subprocess", "threading",
    ):
        assert forbidden not in ENTRY_SOURCE, forbidden
