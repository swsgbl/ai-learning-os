"""M14-263 真实 LiveKit 工厂受控装配契约测试（fake-only，零真实连接）。

覆盖（与任务验收面一一对应）：
1  合法 ws:// / wss:// URL（含带端口形态）恰以原样 URL 调用一次
   被替换的 runtime-factory 函数；
2  装配结果是 ControlledVoiceSubscriberFactory；
3  token provider 与两个 runtime 工厂以 callable 透传，装配期间
   零调用（三计数恒 0、白盒身份同一）；
4  装配出的工厂可用 fake room/audio 协作者创建
   ControlledVoiceSubscriberHandle（协议兼容，不启动）；
5  非法 URL 确定性失败：非字符串/空白/错误 scheme/缺主机名/
   userinfo/query/fragment/非法端口——且 runtime 工厂零调用；
6  非 callable token provider 在 runtime 工厂调用之前失败；
7  runtime 工厂异常 → 通用净化域错误（无原始异常文本、
   __cause__/__context__ 均 None、traceback 干净）；
8  源码卫生：无 app.main 导入、无 settings/环境/凭据读取、
   无网络/LiveKit 服务调用（AST 导入白名单 + 禁止调用清单）、
   无 create 调用、无 token-provider 调用。

monkeypatch voice_runtime_factory.livekit_runtime_factories——
绝无真实 LiveKit 连接/网络。
"""
from __future__ import annotations

import ast
import json
import re
import traceback
from pathlib import Path

import pytest

import app.domain.voice_runtime_factory as factory_module
from app.domain.voice_runtime_factory import (
    VoiceRuntimeFactoryError,
    build_controlled_voice_subscriber_factory,
    build_room_aware_controlled_voice_subscriber_factory,
    validate_livekit_url,
)
from app.domain.voice_subscriber_factory import (
    ControlledVoiceSubscriberFactory,
    ControlledVoiceSubscriberHandle,
)

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")


# ---------- fake 记录件（零网络；结构满足被装配面） ----------


class FakeRoom:
    def __init__(self) -> None:
        self.handlers = {}

    def on(self, name, handler) -> None:
        self.handlers[name] = handler

    async def connect(self, token: str) -> None:
        return None

    async def disconnect(self) -> None:
        return None


class FakeAudioStream:
    def set_frame_handler(self, handler) -> None:
        self.handler = handler

    def set_error_handler(self, handler) -> None:
        self.error_handler = handler

    async def close(self) -> None:
        return None


class Recorder:
    """记录调用的三件套替身：token provider + 两个 runtime 工厂。"""

    def __init__(self) -> None:
        self.token_calls = 0
        self.room_factory_calls = 0
        self.stream_factory_calls = 0
        self.token_factory_calls: list[tuple[str, str]] = []
        self.runtime_calls: list[str] = []
        self.runtime_raises = False
        self.runtime_malformed: object | None = None  # Codex R1：形状错误注入面
        self.returned_factories: tuple = ()

    def token_provider(self) -> str:
        self.token_calls += 1
        return FAKE_JWT

    def token_provider_factory(self, room: str, identity: str) -> str:
        """M14-264：room-aware provider 替身——记录 (room, identity)。"""
        self.token_factory_calls.append((room, identity))
        return f"tok-{room}-{identity}"

    def room_factory(self) -> FakeRoom:
        self.room_factory_calls += 1
        return FakeRoom()

    def audio_stream_factory(self, track: object) -> FakeAudioStream:
        self.stream_factory_calls += 1
        return FakeAudioStream()

    def livekit_runtime_factories(self, url: str):
        self.runtime_calls.append(url)
        if self.runtime_raises:
            raise RuntimeError(f"runtime boom {FAKE_JWT}")
        if self.runtime_malformed is not None:
            return self.runtime_malformed  # 恶意/畸形形状原样返回
        # 记录返回的工厂对（bound method 对象每次访问均新建——
        # 身份断言必须用被返回的同一批引用）
        self.returned_factories = (self.room_factory, self.audio_stream_factory)
        return self.returned_factories


@pytest.fixture()
def recorder(monkeypatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(
        factory_module, "livekit_runtime_factories", rec.livekit_runtime_factories
    )
    return rec


# ---------- 1/2：合法 URL 装配 ----------


@pytest.mark.parametrize(
    "url",
    [
        "ws://localhost",
        "ws://127.0.0.1:7880",
        "wss://livekit.example.com",
        "wss://media.internal:9090",
    ],
)
def test_valid_urls_pass_exact_url_once_and_return_factory(url, recorder) -> None:
    result = build_controlled_voice_subscriber_factory(url, recorder.token_provider)
    assert isinstance(result, ControlledVoiceSubscriberFactory)
    assert recorder.runtime_calls == [url]  # 恰一次、原样 URL


def test_validate_livekit_url_returns_input_unchanged() -> None:
    for url in ("ws://host", "wss://host:7880"):
        assert validate_livekit_url(url) == url


# ---------- 3：callable 透传、装配零调用 ----------


def test_callables_passed_through_without_invocation(recorder) -> None:
    token_provider = recorder.token_provider  # 捕获同一 bound method 对象
    result = build_controlled_voice_subscriber_factory(
        "wss://livekit.example.com", token_provider
    )
    assert recorder.token_calls == 0
    assert recorder.room_factory_calls == 0
    assert recorder.stream_factory_calls == 0
    assert recorder.runtime_calls == ["wss://livekit.example.com"]
    # 白盒身份同一：三个 callable 原对象进入受控工厂
    room_factory, audio_stream_factory = recorder.returned_factories
    assert result._token_provider is token_provider
    assert result._room_factory is room_factory
    assert result._audio_stream_factory is audio_stream_factory


# ---------- 4：装配出的工厂可创建句柄（不启动） ----------


def test_assembled_factory_creates_handle_without_starting(recorder) -> None:
    factory = build_controlled_voice_subscriber_factory(
        "wss://livekit.example.com", recorder.token_provider
    )
    handle = factory.create("voice-asm", "server-sub-asm")
    assert isinstance(handle, ControlledVoiceSubscriberHandle)
    state = handle.state()
    assert state["status"] == "idle"  # 未启动
    assert state["room"] == "voice-asm"
    assert handle.sink_snapshot()["frame_count"] == 0
    # 创建 ≠ 启动：token/room/stream 全零调用
    assert recorder.token_calls == 0
    assert recorder.room_factory_calls == 0
    assert recorder.stream_factory_calls == 0


# ---------- 5：非法 URL 确定性失败（runtime 工厂零调用） ----------


@pytest.mark.parametrize(
    "bad_url",
    [
        None,
        123,
        b"ws://host",  # bytes 绝不当字符串用
        True,
        "",
        "   ",
        "\t\n",
        "livekit.example.com",  # 无 scheme
        "http://host",  # 错误 scheme
        "https://host",
        "ftp://host",
        "wsx://host",
        "ws://",  # 缺主机名
        "wss://",
        "ws:///path-only",
        "ws://user:pass@host",  # userinfo
        "wss://user@host",
        "ws://host?q=1",  # query
        "wss://host#frag",  # fragment
        "ws://host:0",  # 非法端口（保留 0）
        "ws://host:99999",  # 越界
        "ws://host:abc",  # 非数字
        "ws://host:-1",  # 负端口
    ],
)
def test_invalid_urls_fail_deterministically(bad_url, recorder) -> None:
    with pytest.raises(VoiceRuntimeFactoryError, match="LiveKit URL 非法"):
        build_controlled_voice_subscriber_factory(bad_url, recorder.token_provider)
    assert recorder.runtime_calls == []  # 校验先于 runtime 工厂
    assert recorder.token_calls == 0


def test_validate_livekit_url_rejects_directly() -> None:
    for bad in (None, "", "http://h", "ws://", "ws://u@h", "ws://h?q", "ws://h#f"):
        with pytest.raises(VoiceRuntimeFactoryError, match="LiveKit URL 非法"):
            validate_livekit_url(bad)


# ---------- 6：非 callable token provider 先于 runtime 工厂失败 ----------


@pytest.mark.parametrize("bad_provider", [None, 123, "provider", object()])
def test_non_callable_token_provider_fails_before_runtime_factory(
    bad_provider, recorder
) -> None:
    with pytest.raises(VoiceRuntimeFactoryError, match="token provider 非法"):
        build_controlled_voice_subscriber_factory("ws://host", bad_provider)
    assert recorder.runtime_calls == []


# ---------- 7：runtime 工厂异常 → 净化域错误 + 异常链抑制 ----------


def test_runtime_factory_failure_sanitized_and_chain_suppressed(recorder) -> None:
    recorder.runtime_raises = True
    with pytest.raises(VoiceRuntimeFactoryError) as exc_info:
        build_controlled_voice_subscriber_factory(
            "wss://host", recorder.token_provider
        )
    error = exc_info.value
    assert "boom" not in str(error)
    assert FAKE_JWT not in str(error)
    assert JWT_RE.search(str(error)) is None
    # 异常链彻底抑制：__cause__/__context__ 均 None（except 块外 raise）
    assert error.__cause__ is None
    assert error.__context__ is None
    serialized = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    assert "boom" not in serialized
    assert FAKE_JWT not in serialized
    assert JWT_RE.search(serialized) is None
    assert recorder.runtime_calls == ["wss://host"]  # 恰一次尝试
    # 错误消息可安全序列化（无凭据形态）
    assert JWT_RE.search(json.dumps(str(error), ensure_ascii=False)) is None


# ---------- 9（M14-264）：room-aware 装配 ----------


def test_room_aware_assembly_validates_url_before_runtime(recorder) -> None:
    for bad_url in ("", "http://host", "ws://", "ws://u@h", "ws://h?q", None):
        with pytest.raises(VoiceRuntimeFactoryError, match="LiveKit URL 非法"):
            build_room_aware_controlled_voice_subscriber_factory(
                bad_url, recorder.token_provider_factory
            )
    assert recorder.runtime_calls == []
    assert recorder.token_factory_calls == []


@pytest.mark.parametrize("bad_provider_factory", [None, 123, "provider", object()])
def test_room_aware_assembly_rejects_non_callable_before_runtime(
    bad_provider_factory, recorder
) -> None:
    with pytest.raises(VoiceRuntimeFactoryError, match="token provider 非法"):
        build_room_aware_controlled_voice_subscriber_factory(
            "wss://host", bad_provider_factory
        )
    assert recorder.runtime_calls == []


def test_room_aware_assembly_exact_url_once_reference_passthrough(recorder) -> None:
    provider_factory = recorder.token_provider_factory  # 捕获同一 bound method
    result = build_room_aware_controlled_voice_subscriber_factory(
        "wss://livekit.example.com", provider_factory
    )
    assert isinstance(result, ControlledVoiceSubscriberFactory)
    assert recorder.runtime_calls == ["wss://livekit.example.com"]  # 恰一次原样 URL
    # provider factory 引用透传；零参路径未配置
    assert result._token_provider_factory is provider_factory
    assert result._token_provider is None
    room_factory, audio_stream_factory = recorder.returned_factories
    assert result._room_factory is room_factory
    assert result._audio_stream_factory is audio_stream_factory
    # 装配零调用：provider factory / room / stream / token 全零
    assert recorder.token_factory_calls == []
    assert recorder.token_calls == 0
    assert recorder.room_factory_calls == 0
    assert recorder.stream_factory_calls == 0


def test_room_aware_assembled_factory_creates_idle_handle(recorder) -> None:
    factory = build_room_aware_controlled_voice_subscriber_factory(
        "wss://livekit.example.com", recorder.token_provider_factory
    )
    handle = factory.create("voice-asm", "server-sub-asm")
    assert isinstance(handle, ControlledVoiceSubscriberHandle)
    assert handle.state()["status"] == "idle"
    # 创建 ≠ 启动：装配与创建全程 provider factory 零调用
    assert recorder.token_factory_calls == []
    assert recorder.room_factory_calls == 0


def test_room_aware_assembly_runtime_failure_sanitized(recorder) -> None:
    recorder.runtime_raises = True
    with pytest.raises(VoiceRuntimeFactoryError) as exc_info:
        build_room_aware_controlled_voice_subscriber_factory(
            "wss://host", recorder.token_provider_factory
        )
    error = exc_info.value
    assert "boom" not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert recorder.runtime_calls == ["wss://host"]  # 恰一次尝试


# ---------- 10（Codex R1）：畸形 runtime 返回形状全转净化错误 ----------


def _malformed_shape(shape: str, recorder: Recorder) -> object:
    """构造畸形 runtime 返回值（携带假标记——证明绝不外泄）。"""
    if shape == "too-long":
        return (recorder.room_factory, recorder.audio_stream_factory, f"extra-{FAKE_JWT}")
    if shape == "too-short":
        return (f"boom-{FAKE_JWT}",)
    if shape == "non-iterable":
        return 42
    return (f"boom-{FAKE_JWT}", recorder.audio_stream_factory)  # 元素非 callable


@pytest.mark.parametrize("shape", ["too-long", "too-short", "non-iterable", "non-callable-element"])
def test_zero_arg_builder_malformed_runtime_return_sanitized(shape, recorder) -> None:
    recorder.runtime_malformed = _malformed_shape(shape, recorder)
    with pytest.raises(VoiceRuntimeFactoryError) as exc_info:
        build_controlled_voice_subscriber_factory("wss://host", recorder.token_provider)
    error = exc_info.value
    assert "boom" not in str(error)
    assert FAKE_JWT not in str(error)
    assert JWT_RE.search(str(error)) is None
    assert error.__cause__ is None
    assert error.__context__ is None
    serialized = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    assert "boom" not in serialized
    assert FAKE_JWT not in serialized
    assert recorder.runtime_calls == ["wss://host"]  # 恰一次尝试


@pytest.mark.parametrize("shape", ["too-long", "too-short", "non-iterable", "non-callable-element"])
def test_room_aware_builder_malformed_runtime_return_sanitized(shape, recorder) -> None:
    recorder.runtime_malformed = _malformed_shape(shape, recorder)
    with pytest.raises(VoiceRuntimeFactoryError) as exc_info:
        build_room_aware_controlled_voice_subscriber_factory(
            "wss://host", recorder.token_provider_factory
        )
    error = exc_info.value
    assert "boom" not in str(error)
    assert FAKE_JWT not in str(error)
    assert JWT_RE.search(str(error)) is None
    assert error.__cause__ is None
    assert error.__context__ is None
    serialized = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    assert "boom" not in serialized
    assert FAKE_JWT not in serialized
    assert recorder.runtime_calls == ["wss://host"]  # 恰一次尝试


def test_zero_arg_assembly_remains_compatible(recorder) -> None:
    """既有 M14-263 零参装配面不变（显式回归守卫）。"""
    provider = recorder.token_provider
    result = build_controlled_voice_subscriber_factory(
        "wss://livekit.example.com", provider
    )
    assert isinstance(result, ControlledVoiceSubscriberFactory)
    assert result._token_provider is provider
    assert result._token_provider_factory is None
    assert recorder.runtime_calls == ["wss://livekit.example.com"]


# ---------- 8：源码卫生 ----------


def test_source_hygiene_pure_domain_module() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "domain"
        / "voice_runtime_factory.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed_runtime_imports = {
        "__future__",
        "urllib.parse",
        "collections.abc",
        "typing",
        "app.domain.voice_runtime",
        "app.domain.voice_subscriber_factory",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in allowed_runtime_imports, alias.name
        if isinstance(node, ast.ImportFrom):
            root = node.module or ""
            if node.level == 0:
                assert root in allowed_runtime_imports, root
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else ""
            attr = func.attr if isinstance(func, ast.Attribute) else ""
            assert name not in {
                "open",
                "system",
                "popen",
                "run",
                "sleep",
                "ensure_future",
                "create_task",
                "start_new_thread",
            }, name
            assert attr not in {
                "system",
                "popen",
                "ensure_future",
                "create_task",
                "start_new_thread",
                "getenv",
                "connect",
            }, attr
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                assert func.value.id != "asyncio", "asyncio 调度禁止出现在本模块"
    # 无 app.main 导入 / settings / 环境 / 凭据访问
    for forbidden in (
        "from app.main",
        "import app.main",
        "get_settings",
        "os.environ",
        "getenv(",
        "pydantic_settings",
        "SECRET",
        "API_KEY",
    ):
        assert forbidden not in source, forbidden
    # 无动态逃逸口；绝不调用 create；不构造订阅者/句柄
    for forbidden in ("__import__", "eval(", "exec(", ".create(", "create(", "start("):
        assert forbidden not in source, forbidden
