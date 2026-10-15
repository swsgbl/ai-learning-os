"""M14-267 显式语音部署入口契约测试（fake-only，零真实凭据/连接）。

覆盖（与任务验收面一一对应）：
1  设置映射：恰 livekit_server_url 进入 M14-266 服务端 livekit_url
   字段；浏览器面 livekit_url/public_livekit_url 被硬性忽略；
2  纯包装函数恰一次调 create_app_with_voice_subscriber（同
   database_url + 映射设置对象）；不直接调 create_app（AST 锚定）；
3  缺失/空白/非法 livekit_server_url 经既有 M14-265/266 校验路径
   在任何 FastAPI app 创建之前失败（净化、零协作者调用）；
4  get_settings() 至多一次且仅用于模块级默认（AST 恰一调用节点
   且在模块级）；纯函数接受显式设置、不触 get_settings；
5  fake-only lifespan：入口 app 安装恰一工厂、管理器 open、订阅者
   计数 0、零签发、零房间/流、零启动、关停干净；
6  源码卫生：入口模块无 LiveKit SDK/token 签发/网络/进程/secret
   文件调用、不重复凭据/URL 校验、无 create()/start()；
7  main.py 仍是裸 create_app() 且不导入入口；Dockerfile 默认
   app.main:app；
8  compose 渲染：API command 为 app.voice_entrypoint:app、注入
   LIVEKIT_SERVER_URL（AIOS_ 插值）、浏览器面 LIVEKIT_URL/
   PUBLIC_LIVEKIT_URL 行为不变；.env.example 有 AIOS_ 键与
   服务端 URL 注释。

全部使用显眼假凭据与 fake runtime 协作者；绝不在本地跑 Docker。
"""
from __future__ import annotations

import ast
import importlib
import re
import traceback
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import app.domain.voice_app_factory as app_factory_module
import app.domain.voice_runtime_factory as runtime_module
import app.voice_pipeline_entry as pipeline_entry_module
from app.core.config import get_settings
from app.domain.voice_runtime_factory import VoiceRuntimeFactoryError
from app.domain.voice_subscriber_factory import ControlledVoiceSubscriberFactory
from app.voice_pipeline_entry import VoicePipelineSettingsLike

FAKE_SERVER_URL = "ws://livekit:7880"  # 服务端容器地址（compose 默认同款）
FAKE_BROWSER_URL = "ws://127.0.0.1:7880"  # 浏览器面（必须被忽略）
FAKE_PUBLIC_URL = "ws://lan.example:7880"  # 公开面（必须被忽略）
FAKE_KEY = "fake-api-key-000000"
FAKE_SECRET = "fake-api-secret-000000000000000000000000"
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT_PATH = REPO_ROOT / "services" / "api" / "app" / "voice_entrypoint.py"
ENTRYPOINT_SOURCE = ENTRYPOINT_PATH.read_text(encoding="utf-8")
MAIN_SOURCE = (REPO_ROOT / "services" / "api" / "app" / "main.py").read_text(
    encoding="utf-8"
)
DOCKERFILE_SOURCE = (
    REPO_ROOT / "services" / "api" / "Dockerfile"
).read_text(encoding="utf-8")
COMPOSE_PATH = REPO_ROOT / "infra" / "docker-compose.yml"
COMPOSE_TEXT = COMPOSE_PATH.read_text(encoding="utf-8")
ENV_EXAMPLE_TEXT = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")


# ---------- fake 注入件 ----------


class FakeEntrypointSettings:
    """部署入口设置替身：三个 URL 显式不同——只有 server 被读。"""

    def __init__(self, **overrides) -> None:
        fields = {
            "livekit_url": FAKE_BROWSER_URL,
            "public_livekit_url": FAKE_PUBLIC_URL,
            "livekit_server_url": FAKE_SERVER_URL,
            "livekit_api_key": FAKE_KEY,
            "livekit_api_secret": FAKE_SECRET,
            "voice_asr_endpoint": "http://127.0.0.1:8010/v1",
            "asr_local_model": "sensevoice",
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


def load_entrypoint(monkeypatch, **env_overrides):
    """以受控 env 加载/重载入口模块（模块级 app 用显眼假凭据构建）。

    get_settings 的 lru_cache 在设置/用后各清一次，杜绝假 env
    设置泄漏到其他测试；monkeypatch 退出时自动还原 env。
    """
    env = {
        "LIVEKIT_SERVER_URL": FAKE_SERVER_URL,
        "LIVEKIT_API_KEY": FAKE_KEY,
        "LIVEKIT_API_SECRET": FAKE_SECRET,
        "VOICE_ASR_ENDPOINT": "http://127.0.0.1:8010/v1",
    }
    env.update(env_overrides)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    module = importlib.import_module("app.voice_entrypoint")
    module = importlib.reload(module)
    get_settings.cache_clear()
    return module


def _assert_clean(error: BaseException) -> None:
    text = str(error)
    assert FAKE_KEY not in text and FAKE_SECRET not in text
    assert FAKE_SERVER_URL not in text and FAKE_BROWSER_URL not in text
    assert JWT_RE.search(text) is None
    assert error.__cause__ is None
    assert error.__context__ is None
    serialized = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    assert FAKE_KEY not in serialized and FAKE_SECRET not in serialized


# ---------- 1/2：设置映射 + 恰一次包装 ----------


def test_server_url_mapping_ignores_browser_urls(monkeypatch) -> None:
    module = load_entrypoint(monkeypatch)
    captured: list[tuple[tuple, dict]] = []

    def recording_wrapper(database_url=None, *, settings):
        captured.append(((database_url,), {"settings": settings}))
        return object()

    # 直接替换入口模块已绑定的名字（from-import 绑定不受源模块影响）
    monkeypatch.setattr(
        module, "create_app_with_voice_pipeline", recording_wrapper
    )
    settings = FakeEntrypointSettings()  # 三个 URL 显式不同
    sentinel_db = object()
    module.create_voice_entrypoint_app(sentinel_db, settings=settings)
    assert len(captured) == 1  # 恰一次
    args, kwargs = captured[0]
    assert args == (sentinel_db,)  # database_url 原样透传
    mapped = kwargs["settings"]
    assert isinstance(mapped, VoicePipelineSettingsLike)  # M14-272c 设置面
    assert mapped.livekit_url == FAKE_SERVER_URL  # 只有服务端 URL 进入
    assert mapped.livekit_url != FAKE_BROWSER_URL
    assert mapped.livekit_url != FAKE_PUBLIC_URL
    assert mapped.livekit_api_key == FAKE_KEY
    assert mapped.livekit_api_secret == FAKE_SECRET
    assert mapped.asr_local_endpoint == "http://127.0.0.1:8010/v1"  # ASR 直通
    assert mapped.asr_local_model == "sensevoice"


def test_wrapper_not_create_app_directly() -> None:
    """入口只经 M14-266 包装器——绝不直接导入/调用 create_app。"""
    assert "from app.main import" not in ENTRYPOINT_SOURCE
    assert "import app.main" not in ENTRYPOINT_SOURCE
    assert "create_app(" not in ENTRYPOINT_SOURCE.replace(
        "create_app_with_voice_pipeline(", ""
    ).replace("create_voice_entrypoint_app(", "")


# ---------- 3：非法 server URL 先于 app 创建失败 ----------


@pytest.mark.parametrize(
    "bad_url", [None, "", "   ", "http://bad", "ws://", "ws://u@h"]
)
def test_invalid_server_url_fails_before_app_creation(
    bad_url, runtime_pair, signer, monkeypatch
) -> None:
    module = load_entrypoint(monkeypatch)
    create_app_calls = []

    def spy_create_app(*args, **kwargs):
        create_app_calls.append((args, kwargs))
        raise AssertionError("create_app 不应被调用")

    monkeypatch.setattr(pipeline_entry_module, "create_app", spy_create_app)
    # 清除模块级 app 构建的基线记录（合法 URL 恰一次），断言失败路径增量
    runtime_pair.calls.clear()
    with pytest.raises(VoiceRuntimeFactoryError, match="LiveKit URL 非法") as ei:
        module.create_voice_entrypoint_app(
            None, settings=FakeEntrypointSettings(livekit_server_url=bad_url)
        )
    _assert_clean(ei.value)
    assert runtime_pair.calls == []  # 失败路径 runtime 工厂零调用
    assert signer.calls == []  # 零签发
    assert create_app_calls == []  # FastAPI app 创建零调用


def test_missing_asr_endpoint_fails_closed_before_app(
    runtime_pair, signer, monkeypatch
) -> None:
    """ASR 端点缺失/空白：M14-272c fail-closed 先于任何 app 创建。"""
    module = load_entrypoint(monkeypatch)
    create_app_calls: list[tuple] = []

    def spy_create_app(*args, **kwargs):
        create_app_calls.append((args, kwargs))
        raise AssertionError("create_app 不应被调用")

    monkeypatch.setattr(pipeline_entry_module, "create_app", spy_create_app)
    for bad_endpoint in (None, "", "   "):
        with pytest.raises(Exception) as ei:
            module.create_voice_entrypoint_app(
                None,
                settings=FakeEntrypointSettings(voice_asr_endpoint=bad_endpoint),
            )
        text_error = str(ei.value)
        assert FAKE_KEY not in text_error and FAKE_SECRET not in text_error
    assert create_app_calls == []


# ---------- 4：get_settings 恰一次且仅模块级 ----------


def test_get_settings_once_module_level_only() -> None:
    tree = ast.parse(ENTRYPOINT_SOURCE)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_settings"
    ]
    assert len(calls) == 1  # 恰一次
    # 且只在模块级默认 app 行（列 0 上下文）
    assert re.search(
        r"^app = create_voice_entrypoint_app\(.*get_settings\(\)\)$",
        ENTRYPOINT_SOURCE,
        re.MULTILINE,
    )


def test_pure_function_accepts_explicit_settings_no_get_settings(
    monkeypatch,
) -> None:
    """纯映射/包装函数接受显式设置——绝不触碰全局设置入口。"""
    module = load_entrypoint(monkeypatch)

    def guard_get_settings():
        raise AssertionError("纯函数不得读取全局设置")

    # 同时守住源模块与入口模块内绑定，证明函数运行期不再调用
    monkeypatch.setattr("app.core.config.get_settings", guard_get_settings)
    monkeypatch.setattr(module, "get_settings", guard_get_settings)
    sentinel = object()

    def recording_wrapper(database_url=None, *, settings):
        return sentinel

    monkeypatch.setattr(
        module, "create_app_with_voice_pipeline", recording_wrapper
    )
    result = module.create_voice_entrypoint_app(
        None, settings=FakeEntrypointSettings()
    )
    assert result is sentinel


# ---------- 5：fake-only lifespan（入口 app 注册而已） ----------


def test_entrypoint_lifespan_registration_only(
    runtime_pair, signer, monkeypatch
) -> None:
    module = load_entrypoint(monkeypatch)
    built: list[ControlledVoiceSubscriberFactory] = []
    real_build = pipeline_entry_module.build_voice_app_subscriber_factory

    def recording_build(config):
        factory = real_build(config)
        built.append(factory)
        return factory

    monkeypatch.setattr(
        pipeline_entry_module, "build_voice_app_subscriber_factory", recording_build
    )
    app = module.create_voice_entrypoint_app(
        None, settings=FakeEntrypointSettings()
    )
    factory = built[0]
    with TestClient(app) as client:
        assert app.state.voice_subscriber_factory is factory  # 恰一工厂同实例
        manager = app.state.voice_runtime_manager
        assert manager.snapshot()["state"] == "open"
        assert manager.snapshot()["subscriber_count"] == 0  # 注册 ≠ 启动
        assert client.get("/health").status_code == 200
        assert signer.calls == []  # 零签发
        assert runtime_pair.room_calls == 0  # 零房间构造
        assert runtime_pair.stream_calls == 0  # 零流构造
    assert manager.snapshot()["state"] == "closed"  # 关停干净
    assert manager.snapshot()["subscriber_count"] == 0
    assert signer.calls == []
    assert runtime_pair.room_calls == 0
    assert runtime_pair.stream_calls == 0


def test_module_level_app_exists_and_registers_factory(
    runtime_pair, signer, monkeypatch
) -> None:
    """模块级默认（get_settings 恰一次）同样只注册不启动。"""
    module = load_entrypoint(monkeypatch)
    assert hasattr(module, "app")
    with TestClient(module.app) as client:
        assert isinstance(
            module.app.state.voice_subscriber_factory,
            ControlledVoiceSubscriberFactory,
        )
        manager = module.app.state.voice_runtime_manager
        assert manager.snapshot()["state"] == "open"
        assert manager.snapshot()["subscriber_count"] == 0
        assert client.get("/health").status_code == 200
        assert signer.calls == []
        assert runtime_pair.room_calls == 0
    assert manager.snapshot()["state"] == "closed"
    assert signer.calls == []
    assert runtime_pair.room_calls == 0


# ---------- 6：源码卫生（入口模块） ----------


def test_source_hygiene_entrypoint_module() -> None:
    tree = ast.parse(ENTRYPOINT_SOURCE)
    allowed = {
        "__future__",
        "typing",
        "fastapi",
        "app.core.config",
        "app.voice_pipeline_entry",
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
                "getenv",
                "ensure_future",
                "create_task",
                "start_new_thread",
                "build_voice_token",
                "validate_livekit_url",
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
    for forbidden in (
        "getenv(",
        "os.environ",
        "dotenv",
        "build_voice_token",
        "__import__",
        "eval(",
        "exec(",
        ".create(",
        ".start(",
    ):
        assert forbidden not in ENTRYPOINT_SOURCE, forbidden


# ---------- 7：main.py 与 Dockerfile 默认不变 ----------


def test_main_and_dockerfile_defaults_unchanged() -> None:
    assert re.search(r"^app = create_app\(\)$", MAIN_SOURCE, re.MULTILINE)
    assert "voice_entrypoint" not in MAIN_SOURCE
    assert re.search(
        r'^CMD \["uvicorn", "app\.main:app", "--host", "0\.0\.0\.0", "--port", "8000"\]$',
        DOCKERFILE_SOURCE,
        re.MULTILINE,
    )


# ---------- 8：compose 渲染与 .env.example ----------


def _compose_api_service() -> dict:
    compose = yaml.safe_load(COMPOSE_TEXT)
    return compose["services"]["api"]


def test_compose_api_runs_voice_entrypoint() -> None:
    api = _compose_api_service()
    assert api["command"] == [
        "uvicorn",
        "app.voice_entrypoint:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]


def test_compose_api_receives_livekit_server_url() -> None:
    api = _compose_api_service()
    assert api["environment"]["LIVEKIT_SERVER_URL"] == (
        "${AIOS_LIVEKIT_SERVER_URL:-ws://livekit:7880}"
    )


def test_compose_browser_facing_urls_unchanged() -> None:
    api = _compose_api_service()
    env = api["environment"]
    assert env["LIVEKIT_URL"] == "${AIOS_LIVEKIT_URL:-ws://127.0.0.1:7880}"
    assert env["PUBLIC_LIVEKIT_URL"] == "${AIOS_PUBLIC_LIVEKIT_URL:-}"
    assert env["LIVEKIT_API_KEY"] == "${AIOS_LIVEKIT_API_KEY:-devkey}"


def test_compose_api_receives_voice_asr_topology_default() -> None:
    """管线专用 ASR 槽位有拓扑默认；provider 面 ASR 默认留空不变。"""
    api = _compose_api_service()
    assert api["environment"]["VOICE_ASR_ENDPOINT"] == (
        "${AIOS_VOICE_ASR_ENDPOINT:-http://host.docker.internal:8010/v1}"
    )
    assert api["environment"]["ASR_LOCAL_ENDPOINT"] == "${AIOS_ASR_LOCAL_ENDPOINT:-}"


def test_env_example_documents_server_url_key() -> None:
    assert "AIOS_LIVEKIT_SERVER_URL" in ENV_EXAMPLE_TEXT
    assert "AIOS_VOICE_ASR_ENDPOINT" in ENV_EXAMPLE_TEXT
    # 键附近的注释块明确：服务端容器 URL，非浏览器 URL
    lines = ENV_EXAMPLE_TEXT.splitlines()
    anchor = next(
        i for i, line in enumerate(lines) if "AIOS_LIVEKIT_SERVER_URL" in line
    )
    block = "\n".join(lines[max(0, anchor - 10) : anchor + 1])
    assert "服务端" in block or "server" in block.lower()
    assert "浏览器" in block or "browser" in block.lower()
