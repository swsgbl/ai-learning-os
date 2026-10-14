"""M14-266 显式语音应用接线契约测试（fake-only，零真实凭据/连接）。

覆盖（与任务验收面一一对应）：
1  适配器成功：服务端 livekit_url/key/secret 映射进 M14-265 config
   （public_livekit_url 被忽略）、TTL 用 DEFAULT_TTL_SECONDS、
   零签发零 runtime 协作者；
2  缺属性/属性读取抛错的设置对象 → 通用净化域错误（无设置值/
   属性名/URL/凭据/协作者异常文本，__cause__/__context__ 均 None）；
3  非法凭据/URL 先于 runtime 工厂与 create_app 失败（复用
   M14-265/M14-264 净化）；
4  包装器恰构造一个工厂、恰一次以 keyword 参数把同一实例传给
   既有 create_app；工厂失败先于任何 FastAPI app 返回；
5  fake-only FastAPI lifespan 集成：app.state.voice_subscriber_
   factory is 工厂、管理器 open、订阅者计数 0、签发器与 runtime
   协作者零调用、关停关闭管理器且零启动；
6  源码卫生：新模块无 get_settings/.env/环境/网络/LiveKit SDK/
   token 生成/create()/start() 调用；
7  main.py 仍以裸 create_app() 构造模块级默认且不导入/不调用
   新包装器。

全部使用显眼假凭据与 fake runtime 工厂。
"""
from __future__ import annotations

import ast
import re
import traceback
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.domain.voice_app_factory as app_factory_module
import app.domain.voice_runtime_factory as runtime_module
import app.voice_app as voice_app_module
from app.domain.voice_app_factory import VoiceAppFactoryConfig, VoiceAppFactoryError
from app.domain.voice_runtime_factory import VoiceRuntimeFactoryError
from app.domain.voice_subscriber_factory import ControlledVoiceSubscriberFactory
from app.domain.voice_tokens import DEFAULT_TTL_SECONDS
from app.voice_app import (
    VoiceAppSettingsLike,
    VoiceAppWiringError,
    create_app_with_voice_subscriber,
    voice_app_factory_config,
)

FAKE_URL = "wss://livekit.internal.example"
FAKE_PUBLIC_URL = "wss://livekit.public.example"  # 故意不同：必须被忽略
FAKE_KEY = "fake-api-key-000000"
FAKE_SECRET = "fake-api-secret-000000000000000000000000"
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")

REPO_ROOT = Path(__file__).resolve().parents[3]
MAIN_SOURCE = (REPO_ROOT / "services" / "api" / "app" / "main.py").read_text(
    encoding="utf-8"
)


# ---------- fake 注入件（零网络；显眼假凭据） ----------


class FakeSettings:
    """设置替身：恰暴露服务端三字段 + 明确不同的 public URL。"""

    def __init__(self, **overrides) -> None:
        fields = {
            "livekit_url": FAKE_URL,
            "public_livekit_url": FAKE_PUBLIC_URL,
            "livekit_api_key": FAKE_KEY,
            "livekit_api_secret": FAKE_SECRET,
        }
        fields.update(overrides)
        for name, value in fields.items():
            setattr(self, name, value)


class _FakeRoom:
    def on(self, *args): ...

    async def connect(self, token: str) -> None: ...

    async def disconnect(self) -> None: ...


class _FakeStream:
    def set_frame_handler(self, h) -> None: ...

    def set_error_handler(self, h) -> None: ...

    async def close(self) -> None: ...


class RuntimePairRecorder:
    """记录 runtime 工厂对调用；返回最小 fake 对（零 SDK 构造）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.room_calls = 0
        self.stream_calls = 0

    def livekit_runtime_factories(self, url: str):
        self.calls.append(url)

        def room_factory():
            self.room_calls += 1
            return _FakeRoom()

        def stream_factory(track):
            self.stream_calls += 1
            return _FakeStream()

        return room_factory, stream_factory


class SigningRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, room, identity, **kwargs):
        self.calls.append((room, identity))
        return type("Tok", (), {"token": f"tok-{room}-{identity}"})()


@pytest.fixture()
def runtime_pair(monkeypatch) -> RuntimePairRecorder:
    recorder = RuntimePairRecorder()
    monkeypatch.setattr(
        runtime_module,
        "livekit_runtime_factories",
        recorder.livekit_runtime_factories,
    )
    return recorder


@pytest.fixture()
def signer(monkeypatch) -> SigningRecorder:
    recorder = SigningRecorder()
    monkeypatch.setattr(app_factory_module, "build_voice_token", recorder)
    return recorder


def _assert_clean(error: BaseException) -> None:
    text = str(error)
    assert FAKE_KEY not in text and FAKE_SECRET not in text
    assert FAKE_URL not in text and FAKE_PUBLIC_URL not in text
    assert JWT_RE.search(text) is None
    assert "boom" not in text
    assert error.__cause__ is None
    assert error.__context__ is None
    serialized = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    assert FAKE_KEY not in serialized and FAKE_SECRET not in serialized
    assert "boom" not in serialized


# ---------- 1：适配器成功（public URL 被忽略） ----------


def test_adapter_maps_server_side_fields_only(runtime_pair, signer) -> None:
    config = voice_app_factory_config(FakeSettings())
    assert isinstance(config, VoiceAppFactoryConfig)
    assert config.livekit_url == FAKE_URL  # 服务端 URL（非 public）
    assert config.api_key == FAKE_KEY
    assert config.api_secret == FAKE_SECRET
    assert config.ttl_seconds == DEFAULT_TTL_SECONDS  # 无既有 TTL 设置→默认
    # 适配零签发零 runtime 协作者
    assert signer.calls == []
    assert runtime_pair.calls == []


def test_settings_like_protocol_structural() -> None:
    assert isinstance(FakeSettings(), VoiceAppSettingsLike)


# ---------- 2：畸形设置对象净化失败 ----------


@pytest.mark.parametrize("missing", ["livekit_url", "livekit_api_key", "livekit_api_secret"])
def test_adapter_missing_attribute_sanitized(missing) -> None:
    settings = FakeSettings()
    delattr(settings, missing)
    with pytest.raises(VoiceAppWiringError) as ei:
        voice_app_factory_config(settings)
    _assert_clean(ei.value)


def test_adapter_property_failure_sanitized() -> None:
    class RaisingSettings:
        livekit_url = FAKE_URL

        @property
        def livekit_api_key(self) -> str:
            raise RuntimeError(f"prop boom {FAKE_JWT}")

        livekit_api_secret = FAKE_SECRET

    with pytest.raises(VoiceAppWiringError) as ei:
        voice_app_factory_config(RaisingSettings())
    _assert_clean(ei.value)


def test_adapter_non_object_settings_sanitized() -> None:
    for malformed in (None, "settings", 123, {"livekit_url": FAKE_URL}):
        with pytest.raises(VoiceAppWiringError) as ei:
            voice_app_factory_config(malformed)
        _assert_clean(ei.value)


# ---------- 3：非法凭据/URL 先于 runtime 与 create_app ----------


@pytest.mark.parametrize(
    ("overrides", "error_type", "match"),
    [
        ({"livekit_api_key": "   "}, VoiceAppFactoryError, "API key/API secret 非法"),
        ({"livekit_api_secret": ""}, VoiceAppFactoryError, "API key/API secret 非法"),
        ({"livekit_url": "http://bad"}, VoiceRuntimeFactoryError, "LiveKit URL 非法"),
    ],
)
def test_invalid_settings_fail_before_runtime_and_create_app(
    overrides, error_type, match, runtime_pair, monkeypatch
) -> None:
    create_app_calls = []

    def spy_create_app(*args, **kwargs):
        create_app_calls.append((args, kwargs))
        raise AssertionError("create_app 不应被调用")

    monkeypatch.setattr(voice_app_module, "create_app", spy_create_app)
    with pytest.raises(error_type, match=match) as ei:
        create_app_with_voice_subscriber(None, settings=FakeSettings(**overrides))
    _assert_clean(ei.value)
    assert runtime_pair.calls == []  # runtime 工厂零调用
    assert create_app_calls == []  # create_app 零调用


# ---------- 4：包装器恰一工厂、恰一 create_app、同实例 keyword 传递 ----------


def test_wrapper_builds_one_factory_calls_create_app_once(
    runtime_pair, signer, monkeypatch
) -> None:
    build_calls: list[VoiceAppFactoryConfig] = []
    real_build = voice_app_module.build_voice_app_subscriber_factory

    def recording_build(config):
        build_calls.append(config)
        return real_build(config)

    monkeypatch.setattr(
        voice_app_module, "build_voice_app_subscriber_factory", recording_build
    )
    create_app_calls: list[tuple[tuple, dict]] = []
    create_app_returns: list[object] = []

    def recording_create_app(*args, **kwargs):
        create_app_calls.append((args, kwargs))
        sentinel_app = object()  # 不进入 lifespan，仅需身份可辨
        create_app_returns.append(sentinel_app)
        return sentinel_app

    monkeypatch.setattr(
        voice_app_module, "create_app", recording_create_app
    )
    sentinel_marker = object()
    result = create_app_with_voice_subscriber(sentinel_marker, settings=FakeSettings())
    assert len(build_calls) == 1  # 恰构造一个 config → 一个工厂
    assert build_calls[0].livekit_url == FAKE_URL
    assert len(create_app_calls) == 1  # 恰一次 create_app
    args, kwargs = create_app_calls[0]
    assert args == (sentinel_marker,)  # 位置参数原样透传
    factory = kwargs["voice_subscriber_factory"]
    assert isinstance(factory, ControlledVoiceSubscriberFactory)
    assert set(kwargs) == {"voice_subscriber_factory"}  # 仅该 keyword
    assert result is create_app_returns[0]  # 返回 create_app 的返回值
    # 工厂内部与装配全程零协作者
    assert signer.calls == []
    assert runtime_pair.calls == ["wss://livekit.internal.example"]  # 恰一次（装配内）
    assert runtime_pair.room_calls == 0
    assert runtime_pair.stream_calls == 0


# ---------- 5：fake-only FastAPI lifespan 集成 ----------


def test_wrapper_lifespan_integration_registers_factory_only(
    runtime_pair, signer, monkeypatch
) -> None:
    built: list[ControlledVoiceSubscriberFactory] = []
    real_build = voice_app_module.build_voice_app_subscriber_factory

    def recording_build(config):
        factory = real_build(config)
        built.append(factory)
        return factory

    monkeypatch.setattr(
        voice_app_module, "build_voice_app_subscriber_factory", recording_build
    )
    app = create_app_with_voice_subscriber(None, settings=FakeSettings())
    factory = built[0]
    with TestClient(app) as client:
        # 显式注入只在 lifespan 期间安装；同一实例
        assert app.state.voice_subscriber_factory is factory
        manager = app.state.voice_runtime_manager
        assert manager.snapshot()["state"] == "open"
        assert manager.snapshot()["subscriber_count"] == 0  # 注册≠启动
        assert client.get("/health").status_code == 200
        # 注册全程零签发、零房间/流构造
        assert signer.calls == []
        assert runtime_pair.room_calls == 0
        assert runtime_pair.stream_calls == 0
    # 关停：管理器 closed，零启动零签发
    assert manager.snapshot()["state"] == "closed"
    assert manager.snapshot()["subscriber_count"] == 0
    assert signer.calls == []
    assert runtime_pair.room_calls == 0
    assert runtime_pair.stream_calls == 0
    # 退出后属性保留同实例（诊断面——M14-262 契约）
    assert app.state.voice_subscriber_factory is factory


# ---------- 6：源码卫生（新模块） ----------


def test_source_hygiene_new_module() -> None:
    source_path = Path(__file__).resolve().parents[1] / "app" / "voice_app.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed = {
        "__future__",
        "typing",
        "fastapi",
        "app.main",
        "app.domain.voice_app_factory",
        "app.domain.voice_tokens",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in allowed, alias.name
        if isinstance(node, ast.ImportFrom):
            root = node.module or ""
            if node.level == 0:
                assert root in allowed, root
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
                "get_settings",
                "getenv",
                "ensure_future",
                "create_task",
                "start_new_thread",
                "build_voice_token",
            }, name
            assert attr not in {
                "system",
                "popen",
                "getenv",
                "ensure_future",
                "create_task",
                "start_new_thread",
                "connect",
            }, attr
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                assert func.value.id != "asyncio", "asyncio 调度禁止"
    # 文本级：无配置/环境读取、无 token 生成、无动态逃逸、无
    # 创建/启动调用形态（LiveKit SDK 导入已由 AST 白名单排除——
    # 适配器必然引用 livekit_* 属性名，不能按子串禁词）
    for forbidden in (
        "get_settings",
        "os.environ",
        "getenv(",
        "pydantic_settings",
        "dotenv",
        "build_voice_token",
        "__import__",
        "eval(",
        "exec(",
        ".create(",
        ".start(",
    ):
        assert forbidden not in source, forbidden


# ---------- 7：main.py 默认不变 ----------


def test_main_module_default_unchanged() -> None:
    # 模块级默认仍是裸 create_app()（无工厂参数）
    assert re.search(r"^app = create_app\(\)$", MAIN_SOURCE, re.MULTILINE)
    # main.py 不导入/不调用新包装器（接线只能显式发生）
    assert "voice_app" not in MAIN_SOURCE
    assert "create_app_with_voice_subscriber" not in MAIN_SOURCE
    # M14-262 注入面锚定不变：keyword-only 参数与守卫式赋值保持
    assert "voice_subscriber_factory: ControlledVoiceSubscriberFactory | None = None" in MAIN_SOURCE
