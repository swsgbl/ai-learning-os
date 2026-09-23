"""M14-106 本地 loopback 语音链路代理加固：trust_env 契约测试（零网络）。

背景：httpx 默认 trust_env=True 会消费 HTTP(S)_PROXY/ALL_PROXY 环境变量与
Windows 注册表系统代理（urllib.getproxies 在 Windows 回落 System Registry）——
宿主机开着系统代理时，对 127.0.0.1:8010/8011 的 loopback 请求会被劫持进
代理，本地语音链路与冒烟健康探测出现假失败。LLM 侧同款边界已在 M14-100
修复（app/llm/gateway.py）；本套件锁定语音侧契约：

- local-funasr / local-cosyvoice 构造 httpx.AsyncClient 必须显式
  trust_env=False（loopback 服务不吃任何系统/环境代理），且请求/响应契约
  与默认超时在代理暴露下保持不变；
- cloud-openai / cloud-openai-tts 调共享助手时不传 trust_env——解析为
  httpx 默认 True（部署代理配置照常生效，cloud 语义零漂移）；
- tools/voice/smoke_local_voice.py 的 _probe_health loopback 健康探测
  trust_env=False（健康 URL 与 10s 超时契约不变）。

模拟暴露（"Windows registry proxy exposure"）：环境变量 + monkeypatch
httpx 绑定的 getproxies（httpx._utils，其注释明言该层回落 Windows System
Registry——即环境变量清不掉的注册表通道）双通道注入不可达占位代理，并清空
NO_PROXY（loopback 不进任何绕过名单）。全程 MockTransport/替身响应 +
同步拨号禁令：零真实网络、零本地语音进程（异步路径不存在真实拨号——每个
client 要么注入 MockTransport，要么不发请求）。
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import socket
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.voice.providers import (
    CloudOpenAiAsrProvider,
    CloudOpenAiTtsProvider,
    LocalCosyVoiceTtsProvider,
    LocalFunAsrAsrProvider,
    _sine_wav,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SMOKE_SCRIPT = REPO_ROOT / "tools" / "voice" / "smoke_local_voice.py"

LOCAL_ASR_ENDPOINT = "http://127.0.0.1:8010/v1"
LOCAL_TTS_ENDPOINT = "http://127.0.0.1:8011/v1"
CLOUD_ASR_ENDPOINT = "https://voice-asr.example.com/v1"
CLOUD_TTS_ENDPOINT = "https://voice-tts.example.com/v1"

#: 不可达占位代理（TEST-NET-2 文档地址，绝不拨号）——只作暴露形态断言用
AMBIENT_PROXY_URL = "http://198.51.100.1:7892"
#: urllib.getproxies_registry() 风格的注册表代理 dict（键无 "://" 前缀）
REGISTRY_STYLE_PROXIES: dict[str, str] = {
    "http": AMBIENT_PROXY_URL,
    "https": AMBIENT_PROXY_URL,
    "all": AMBIENT_PROXY_URL,
}

_PROXY_ENV_KEYS = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")


def _forbid_real_dial(*args: Any, **kwargs: Any) -> None:
    """同步拨号禁令：任何真实网络连接尝试即测试失败（本文件只允许进程内 mock）。"""
    raise AssertionError("M14-106 契约测试禁止真实网络拨号（只允许进程内 mock）")


@pytest.fixture()
def ambient_windows_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟 Windows 系统代理暴露：环境变量通道 + 注册表回落通道（patch httpx
    绑定的 getproxies）同时注入占位代理，NO_PROXY 清空（loopback 不被任何
    绕过名单豁免），并封禁真实拨号。"""
    for key in _PROXY_ENV_KEYS:
        monkeypatch.setenv(key, AMBIENT_PROXY_URL)
    for key in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(key, raising=False)
    # httpx._utils 在 import 时绑定 urllib.request.getproxies——patch 该符号即
    # 模拟仅靠清环境变量防不住的「Windows 注册表系统代理」通道
    monkeypatch.setattr("httpx._utils.getproxies", lambda: dict(REGISTRY_STYLE_PROXIES))
    monkeypatch.setattr(socket, "create_connection", _forbid_real_dial)


def _install_async_client_recorder(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """把 httpx.AsyncClient 换成记录构造 kwargs 的真实子类（行为完全委托，
    配合 MockTransport 走完整请求/响应流）；返回构造 kwargs 列表。"""
    created: list[dict] = []
    real_async_client = httpx.AsyncClient

    class _RecordingAsyncClient(real_async_client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            created.append(dict(kwargs))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _RecordingAsyncClient)
    return created


def _load_smoke_module() -> Any:
    """按路径加载 tools/voice/smoke_local_voice.py（脚本无包结构）；其 import
    副作用（sys.path 注入 services/api）在加载后还原。"""
    spec = importlib.util.spec_from_file_location("smoke_local_voice_under_test", SMOKE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    saved_path = sys.path.copy()
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved_path
    return module


# ---------- 锚定：模拟暴露对 trusted 客户端确实生效（真实 httpx 行为） ----------


def test_simulated_proxy_exposure_grips_only_trusted_clients(ambient_windows_proxy: None) -> None:
    """模拟暴露有效性锚定（仅构造 client、不发请求）：默认 trust_env 的 httpx
    client 会为 http/https/all 挂上代理 transport（环境/注册表通道都会被消费），
    trust_env=False 则完全不挂——被测标志在当前 httpx（dev 钉版 >=0.28,<0.29，
    _mounts 结构受此约束）下确实决定 loopback 请求是否被劫持进代理。"""
    with httpx.Client() as trusted:
        assert trusted.trust_env is True
        assert trusted._mounts, "模拟暴露必须使默认客户端挂载代理（否则模拟无效）"
        assert all(mount is not None for mount in trusted._mounts.values())
    with httpx.Client(trust_env=False) as bypassed:
        assert bypassed.trust_env is False
        assert not bypassed._mounts  # 一个代理 mount 都不挂


# ---------- 本地 provider：loopback 旁路（trust_env=False） ----------


def test_local_funasr_asr_bypasses_ambient_proxy_trust(
    monkeypatch: pytest.MonkeyPatch, ambient_windows_proxy: None
) -> None:
    """local-funasr 构造 AsyncClient 必须 trust_env=False——loopback 转写不进
    系统/环境代理；代理暴露下请求/响应契约与本地默认超时不变。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"  # 直连 loopback，目标未被代理改写
        assert request.url.path == "/v1/audio/transcriptions"
        return httpx.Response(200, json={"text": "本地识别文本", "confidence": 0.9})

    created = _install_async_client_recorder(monkeypatch)

    async def body() -> None:
        provider = LocalFunAsrAsrProvider(LOCAL_ASR_ENDPOINT, transport=httpx.MockTransport(handler))
        result = await provider.transcribe(b"audio-bytes")
        assert (result.text, result.provider) == ("本地识别文本", "local-funasr")
        assert result.confidence == pytest.approx(0.9)

    asyncio.run(body())
    assert len(created) == 1
    assert created[0]["trust_env"] is False
    assert created[0]["timeout"] == 60.0  # 本地 ASR 默认超时契约不变


def test_local_cosyvoice_tts_bypasses_ambient_proxy_trust(
    monkeypatch: pytest.MonkeyPatch, ambient_windows_proxy: None
) -> None:
    """local-cosyvoice 构造 AsyncClient 必须 trust_env=False——loopback 合成不进
    系统/环境代理；代理暴露下 WAV 契约与本地默认超时不变。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        assert request.url.path == "/v1/audio/speech"
        return httpx.Response(200, content=_sine_wav(0.1), headers={"Content-Type": "audio/wav"})

    created = _install_async_client_recorder(monkeypatch)

    async def body() -> None:
        provider = LocalCosyVoiceTtsProvider(LOCAL_TTS_ENDPOINT, transport=httpx.MockTransport(handler))
        result = await provider.synthesize("本地合成文本")
        assert result.provider == "local-cosyvoice"
        assert result.audio[:4] == b"RIFF" and result.audio[8:12] == b"WAVE"

    asyncio.run(body())
    assert len(created) == 1
    assert created[0]["trust_env"] is False
    assert created[0]["timeout"] == 120.0  # 本地 TTS 默认超时契约不变


# ---------- 云端 provider：保持环境代理信任（cloud 语义零漂移） ----------


def test_cloud_asr_keeps_ambient_proxy_trust(
    monkeypatch: pytest.MonkeyPatch, ambient_windows_proxy: None
) -> None:
    """cloud-openai 不传 trust_env——保持 httpx 默认 True（部署代理配置照常
    生效）；代理暴露下鉴权头/请求/响应契约与云端默认超时不变。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "voice-asr.example.com"
        assert request.headers["Authorization"] == "Bearer cloud-test-key"
        assert request.url.path == "/v1/audio/transcriptions"
        return httpx.Response(200, json={"text": "云端识别文本"})

    created = _install_async_client_recorder(monkeypatch)

    async def body() -> None:
        provider = CloudOpenAiAsrProvider(
            CLOUD_ASR_ENDPOINT, api_key="cloud-test-key", model="whisper-1",
            transport=httpx.MockTransport(handler),
        )
        result = await provider.transcribe(b"audio-bytes")
        assert (result.text, result.provider) == ("云端识别文本", "cloud-openai")

    asyncio.run(body())
    assert len(created) == 1
    # 云端保持环境代理信任：共享助手不传 trust_env 时解析为 httpx 默认 True
    # （显式转发 True 与不传行为逐字节一致——cloud 语义零漂移）
    assert created[0].get("trust_env", True) is True
    assert created[0]["timeout"] == 30.0  # 云端 ASR 默认超时契约不变


def test_cloud_tts_keeps_ambient_proxy_trust(
    monkeypatch: pytest.MonkeyPatch, ambient_windows_proxy: None
) -> None:
    """cloud-openai-tts 不传 trust_env——保持 httpx 默认 True；代理暴露下音色
    透传、WAV 契约与云端默认超时不变。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "voice-tts.example.com"
        payload = json.loads(request.content)
        assert payload["voice"] == "tongtong"  # M14-65 音色透传契约不变
        return httpx.Response(200, content=_sine_wav(0.1), headers={"Content-Type": "audio/wav"})

    created = _install_async_client_recorder(monkeypatch)

    async def body() -> None:
        provider = CloudOpenAiTtsProvider(
            CLOUD_TTS_ENDPOINT, api_key="cloud-test-key", model="glm-tts",
            voice="tongtong", transport=httpx.MockTransport(handler),
        )
        result = await provider.synthesize("云端合成文本")
        assert result.provider == "cloud-openai-tts"
        assert result.audio[:4] == b"RIFF"

    asyncio.run(body())
    assert len(created) == 1
    assert created[0].get("trust_env", True) is True  # 云端保持环境代理信任（httpx 默认）
    assert created[0]["timeout"] == 60.0  # 云端 TTS 默认超时契约不变


# ---------- smoke 脚本：loopback 健康探测旁路 ----------


def test_smoke_health_probe_bypasses_ambient_proxy(
    monkeypatch: pytest.MonkeyPatch, ambient_windows_proxy: None
) -> None:
    """smoke _probe_health 的 loopback 健康探测必须 trust_env=False；健康 URL
    （endpoint 剥 /v1 挂服务根 /health）、10s 超时与 HTTP 200 判定契约不变。"""
    smoke = _load_smoke_module()
    calls: list[dict] = []

    def recording_get(url: str, **kwargs: Any) -> httpx.Response:
        calls.append({"url": url, **kwargs})
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", recording_get)
    assert smoke._probe_health(LOCAL_ASR_ENDPOINT, "asr") is True
    assert len(calls) == 1
    assert calls[0]["url"] == "http://127.0.0.1:8010/health"
    assert calls[0]["trust_env"] is False
    assert calls[0]["timeout"] == 10.0
