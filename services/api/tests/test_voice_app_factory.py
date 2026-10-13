"""M14-265 语音应用层工厂装配契约测试（fake-only，零真实凭据/连接）。

覆盖（与任务验收面一一对应）：
1  provider-factory 构造成功零签发、零 runtime 协作者调用；
2  非法凭据/TTL/畸形 config 确定性失败——错误与 traceback 无任何
   敏感值（URL/key/secret）、无 __cause__/__context__；
3  provider factory 恰以 (room, identity) + ROLE_SERVER_SUBSCRIBER
   签发、只返回 .token（fake 签发器断言精确参数）；
4  真实离线签发路径：subscribe-only grants（join+subscribe，
   无 publish/数据通道/管理面）与 identity/room 恰确；
5  不同 (room, identity) 对彼此独立、调用与 token 互不串；
6  签发失败在装配/创建期不触发，仅订阅者 start 时进入既有
   净化 failed-start 路径（异常链彻底抑制）；
7  组合函数：凭据先于 runtime 工厂校验、恰一次原样 URL、返回
   ControlledVoiceSubscriberFactory、room-aware 行为保持、装配
   零协作者调用；非法 URL 复用 M14-264 净化（不重复实现）；
8  秘密只进 provider 闭包——绝不成为返回工厂的公开属性/持久值；
9  源码卫生：无 app.main/settings/环境/凭据/网络/SDK 副作用/
   隐式启动/readiness 公开声明。

全部使用显眼假凭据字符串；绝不引入真实 secret/token。
"""
from __future__ import annotations

import ast
import asyncio
import re
import traceback
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.domain.voice_app_factory as app_factory_module
import app.domain.voice_runtime_factory as runtime_module
from app.domain.voice_app_factory import (
    VoiceAppFactoryConfig,
    VoiceAppFactoryError,
    build_room_aware_token_provider_factory,
    build_voice_app_subscriber_factory,
)
from app.domain.voice_runtime_factory import VoiceRuntimeFactoryError
from app.domain.voice_subscriber_factory import (
    ControlledVoiceSubscriberFactory,
    VoiceSubscriberFactoryError,
)
from app.domain.voice_tokens import (
    DEFAULT_TTL_SECONDS,
    ROLE_SERVER_SUBSCRIBER,
    verify_voice_token,
)

#: 显眼假凭据（绝无真实凭据形态）。
FAKE_URL = "wss://livekit.example.com"
FAKE_KEY = "fake-api-key-000000"
FAKE_SECRET = "fake-api-secret-000000000000000000000000"
FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig-sig-sig"
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")


def make_config(**overrides) -> VoiceAppFactoryConfig:
    fields = {
        "livekit_url": FAKE_URL,
        "api_key": FAKE_KEY,
        "api_secret": FAKE_SECRET,
        "ttl_seconds": DEFAULT_TTL_SECONDS,
    }
    fields.update(overrides)
    return VoiceAppFactoryConfig(**fields)


class SigningRecorder:
    """fake 签发器：记录 build_voice_token 收到的全参数；可注入失败。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.raises = False

    def __call__(self, room, identity, **kwargs):
        self.calls.append({"room": room, "identity": identity, **kwargs})
        if self.raises:
            raise RuntimeError(f"sign boom {FAKE_JWT}")
        return SimpleNamespace(token=f"tok-{room}-{identity}")


@pytest.fixture()
def signer(monkeypatch) -> SigningRecorder:
    recorder = SigningRecorder()
    monkeypatch.setattr(app_factory_module, "build_voice_token", recorder)
    return recorder


# ---------- 1：provider 构造零签发零协作者 ----------


def test_provider_factory_construction_issues_nothing(signer) -> None:
    provider = build_room_aware_token_provider_factory(FAKE_KEY, FAKE_SECRET)
    assert callable(provider)
    assert signer.calls == []  # 零签发


def test_config_is_frozen_slots_dataclass() -> None:
    config = make_config()
    assert config.ttl_seconds == DEFAULT_TTL_SECONDS
    with pytest.raises(FrozenInstanceError):
        config.api_key = "mutated"  # type: ignore[misc]
    assert not hasattr(config, "__dict__")  # slots：无字典属性面


def test_config_repr_suppresses_credentials() -> None:
    """Codex R1 修正：凭据字段 repr 抑制——repr 绝不外泄秘密。

    有用的非秘密字段（类名/URL/TTL）保留在 repr 中；FAKE_KEY/
    FAKE_SECRET/JWT 形态绝不出现（防意外日志泄漏）。
    """
    config = make_config()
    text = repr(config)
    assert "VoiceAppFactoryConfig" in text
    assert FAKE_URL in text  # 非秘密字段保留（诊断有用）
    assert "ttl_seconds" in text
    assert str(DEFAULT_TTL_SECONDS) in text
    assert FAKE_KEY not in text
    assert FAKE_SECRET not in text
    assert JWT_RE.search(text) is None


# ---------- 2：非法凭据/TTL/畸形 config 确定性失败 ----------


@pytest.mark.parametrize(
    "bad_key",
    [None, 123, b"key-bytes", "", "   ", True, ["k"]],
)
def test_invalid_api_key_fails_sanitized(bad_key, signer) -> None:
    with pytest.raises(VoiceAppFactoryError, match="API key/API secret 非法") as ei:
        build_room_aware_token_provider_factory(bad_key, FAKE_SECRET)
    _assert_clean(ei.value, bad_key)


@pytest.mark.parametrize(
    "bad_secret",
    [None, 123, b"secret-bytes", "", "   ", True],
)
def test_invalid_api_secret_fails_sanitized(bad_secret, signer) -> None:
    with pytest.raises(VoiceAppFactoryError, match="API key/API secret 非法") as ei:
        build_room_aware_token_provider_factory(FAKE_KEY, bad_secret)
    _assert_clean(ei.value, bad_secret)


@pytest.mark.parametrize(
    "bad_ttl",
    [True, False, "3600", 29, 86401, None, 30.5],
)
def test_invalid_ttl_fails_sanitized(bad_ttl, signer) -> None:
    with pytest.raises(VoiceAppFactoryError, match="ttl_seconds 非法") as ei:
        build_room_aware_token_provider_factory(FAKE_KEY, FAKE_SECRET, bad_ttl)
    _assert_clean(ei.value, bad_ttl)


@pytest.mark.parametrize("bad_ttl", [29, 86401, True])
def test_invalid_ttl_in_composition_fails_sanitized(bad_ttl, signer) -> None:
    with pytest.raises(VoiceAppFactoryError, match="ttl_seconds 非法") as ei:
        build_voice_app_subscriber_factory(make_config(ttl_seconds=bad_ttl))
    _assert_clean(ei.value, bad_ttl)


@pytest.mark.parametrize("malformed", [None, {"api_key": FAKE_KEY}, "config", 123])
def test_malformed_config_fails_sanitized(malformed, signer) -> None:
    with pytest.raises(VoiceAppFactoryError, match="config 非法") as ei:
        build_voice_app_subscriber_factory(malformed)
    _assert_clean(ei.value, malformed)


def _assert_clean(error: BaseException, bad_value: object) -> None:
    """错误/traceback 无敏感值、异常链彻底抑制的统一断言。"""
    text = str(error)
    assert FAKE_KEY not in text
    assert FAKE_SECRET not in text
    assert FAKE_URL not in text
    assert JWT_RE.search(text) is None
    assert error.__cause__ is None
    assert error.__context__ is None
    serialized = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    assert FAKE_KEY not in serialized
    assert FAKE_SECRET not in serialized


# ---------- 3：fake 签发器断言精确参数 ----------


def test_provider_signs_exact_room_identity_server_subscriber(signer) -> None:
    provider = build_room_aware_token_provider_factory(
        FAKE_KEY, FAKE_SECRET, ttl_seconds=600
    )
    token = provider("voice-pf", "server-sub-pf")
    assert token == "tok-voice-pf-server-sub-pf"  # 只返回 .token
    assert len(signer.calls) == 1
    call = signer.calls[0]
    assert call["room"] == "voice-pf"
    assert call["identity"] == "server-sub-pf"
    assert call["api_key"] == FAKE_KEY
    assert call["api_secret"] == FAKE_SECRET
    assert call["ttl_seconds"] == 600
    assert call["role"] == ROLE_SERVER_SUBSCRIBER


def test_distinct_pairs_distinct_calls_isolated(signer) -> None:
    provider = build_room_aware_token_provider_factory(FAKE_KEY, FAKE_SECRET)
    first = provider("voice-a", "server-sub-a")
    second = provider("voice-b", "server-sub-b")
    assert first != second
    assert [(c["room"], c["identity"]) for c in signer.calls] == [
        ("voice-a", "server-sub-a"),
        ("voice-b", "server-sub-b"),
    ]


# ---------- 4：真实离线签发——subscribe-only grants ----------


def test_real_offline_signing_subscribe_only_grants() -> None:
    """真实 build_voice_token（本地 JWT、零网络）：只订阅权限面。

    只断言、不持久化/不输出 token。
    """
    provider = build_room_aware_token_provider_factory(
        FAKE_KEY, FAKE_SECRET, ttl_seconds=600
    )
    token = provider("voice-real", "server-sub-real")
    assert isinstance(token, str) and token
    claims = verify_voice_token(token, api_key=FAKE_KEY, api_secret=FAKE_SECRET)
    assert claims.identity == "server-sub-real"
    video = claims.video
    assert video.room == "voice-real"
    assert video.room_join is True
    assert video.can_subscribe is True
    assert video.can_publish is False
    assert video.can_publish_data is False
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


# ---------- 5：签发失败只在 start 时进入净化 failed-start ----------


class _FakeRoom:
    def on(self, *args): ...

    async def connect(self, token: str) -> None: ...

    async def disconnect(self) -> None: ...


class _FakeStream:
    def set_frame_handler(self, h) -> None: ...

    def set_error_handler(self, h) -> None: ...

    async def close(self) -> None: ...


def _patch_runtime_factories(monkeypatch) -> None:
    """把 runtime 工厂对换成最小 fake（零网络、零 SDK 构造）。"""
    monkeypatch.setattr(
        runtime_module,
        "livekit_runtime_factories",
        lambda url: (lambda: _FakeRoom(), lambda track: _FakeStream()),
    )


def test_signing_failure_not_during_assembly_reaches_failed_start(
    monkeypatch, signer
) -> None:
    signer.raises = True  # 签发器抛消息带 JWT 形态的异常
    _patch_runtime_factories(monkeypatch)
    factory = build_voice_app_subscriber_factory(make_config())
    handle = factory.create("voice-sf", "server-sub-sf")
    assert signer.calls == []  # 装配 + 创建期零签发（失败未触发）

    async def scenario() -> None:
        # 既有净化 failed-start 路径：M14-261 句柄的通用错误类型
        with pytest.raises(VoiceSubscriberFactoryError, match="启动失败"):
            await handle.start()

    asyncio.run(scenario())
    assert len(signer.calls) == 1  # 恰在订阅者启动时尝试一次
    caught: list[BaseException] = []

    async def again() -> None:
        with pytest.raises(VoiceSubscriberFactoryError) as ei:
            await handle.start()  # 终态重启同样净化
        caught.append(ei.value)

    asyncio.run(again())
    error = caught[0]
    assert error.__cause__ is None
    assert error.__context__ is None
    serialized = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    assert "boom" not in str(error) and "boom" not in serialized
    assert FAKE_JWT not in serialized
    assert handle.state()["status"] in ("failed", "stopped")


# ---------- 6：组合函数（M14-264 复用链） ----------


@pytest.fixture()
def runtime_recorder(monkeypatch) -> list[tuple[str, object]]:
    """记录组合层对 M14-264 装配函数的调用（透传真实实现）。"""
    calls: list[tuple[str, object]] = []
    real = runtime_module.build_room_aware_controlled_voice_subscriber_factory

    def passthrough(url, token_provider_factory):
        calls.append((url, token_provider_factory))
        return real(url, token_provider_factory)

    monkeypatch.setattr(
        app_factory_module,
        "build_room_aware_controlled_voice_subscriber_factory",
        passthrough,
    )
    return calls


def test_composition_validates_credentials_before_runtime_factory(
    runtime_recorder,
) -> None:
    with pytest.raises(VoiceAppFactoryError, match="API key/API secret 非法"):
        build_voice_app_subscriber_factory(make_config(api_key="   "))
    assert runtime_recorder == []  # 凭据先于 runtime 工厂


def test_composition_success_exact_url_once_room_aware_kept(
    runtime_recorder, monkeypatch, signer
) -> None:
    _patch_runtime_factories(monkeypatch)
    factory = build_voice_app_subscriber_factory(make_config())
    assert isinstance(factory, ControlledVoiceSubscriberFactory)
    assert [url for url, _ in runtime_recorder] == [FAKE_URL]  # 恰一次原样
    # 装配零协作者：签发器/房间/流 全零
    assert signer.calls == []
    # room-aware 行为保持：创建 idle 句柄、start 才签发
    handle = factory.create("voice-comp", "server-sub-comp")
    assert handle.state()["status"] == "idle"
    assert signer.calls == []

    asyncio.run(handle.start())
    assert [(c["room"], c["identity"]) for c in signer.calls] == [
        ("voice-comp", "server-sub-comp")
    ]

    async def stop() -> None:
        await handle.stop()

    asyncio.run(stop())


def test_invalid_url_reuses_m14_264_sanitization(monkeypatch, signer) -> None:
    called = {"runtime": 0}

    def no_runtime(url, provider_factory):
        called["runtime"] += 1
        raise AssertionError("runtime 工厂不应被调用")

    monkeypatch.setattr(
        app_factory_module,
        "build_room_aware_controlled_voice_subscriber_factory",
        no_runtime,
    )
    # 复用 M14-264 的错误类型与消息（非重复实现）
    with pytest.raises(VoiceRuntimeFactoryError, match="LiveKit URL 非法") as ei:
        build_voice_app_subscriber_factory(make_config(livekit_url="http://bad"))
    assert "http://bad" not in str(ei.value)
    assert called["runtime"] == 0


# ---------- 7：秘密不进返回工厂的公开面 ----------


def test_secrets_not_public_on_returned_factory(runtime_recorder, monkeypatch, signer) -> None:
    _patch_runtime_factories(monkeypatch)
    factory = build_voice_app_subscriber_factory(make_config())
    # 公开属性无秘密
    for name in dir(factory):
        if name.startswith("_"):
            continue
        assert FAKE_KEY not in repr(getattr(factory, name)), name
        assert FAKE_SECRET not in repr(getattr(factory, name)), name
    # 实例槽位的字符串值无秘密（provider 闭包按设计持有——非属性）
    for value in vars(factory).values():
        if isinstance(value, str):
            assert FAKE_KEY not in value and FAKE_SECRET not in value
    # 工厂 repr 无秘密
    assert FAKE_KEY not in repr(factory) and FAKE_SECRET not in repr(factory)
    # provider 闭包代码常量无秘密（秘密只在闭包单元里）
    provider = factory._token_provider_factory
    for constant in provider.__code__.co_consts:
        if isinstance(constant, str):
            assert FAKE_KEY not in constant and FAKE_SECRET not in constant


# ---------- 8：源码卫生 ----------


def test_source_hygiene_pure_domain_module() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "domain"
        / "voice_app_factory.py"
    )
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed = {
        "__future__",
        "collections.abc",
        "dataclasses",
        "app.domain.voice_runtime_factory",
        "app.domain.voice_subscriber_factory",
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
                "ensure_future",
                "create_task",
                "start_new_thread",
                "getenv",
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
    for forbidden in (
        "from app.main",
        "import app.main",
        "get_settings",
        "os.environ",
        "getenv(",
        "pydantic_settings",
        "__import__",
        "eval(",
        "exec(",
    ):
        assert forbidden not in source, forbidden
    # 装配函数绝不触发创建/启动路径
    assert ".create(" not in source
    assert ".start(" not in source
