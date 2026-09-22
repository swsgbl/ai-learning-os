"""M14-100 本地 LLM 冒烟超时与生成预算契约（离线、确定性）。

背景（M14-98 实证，docs/evidence/m14-98-current-provider-smoke/）：
- gateway 默认 30s 超时硬编码于 HttpxTransport 且无任何配置通道——本地
  拓扑（饱和 16GB GPU 上的 9B thinking 模型）max_tokens=2048 生成 >30s，
  两跑 llm provider-smoke 真实 FAIL；
- httpx 默认 trust_env=True 会消费 HTTP(S)_PROXY 环境变量——attempt1
  traceback 含 httpcore http_proxy 帧（loopback 请求被路由进系统代理）；
- httpx.TimeoutException 未映射为 LlmUnavailable，冒烟脚本只能以裸
  traceback 形态失败。

本套件锁定修复契约（全部 fake transport/client，零网络、零 GPU、零 live
provider）：超时显式可配且默认 30s 零漂移（cloud 语义不变）、loopback
端点强制绕过环境代理、httpx 异常映射 LlmUnavailable、有界生成预算
序列化、失败消息不泄漏 api_key。
"""
from __future__ import annotations

from typing import Any, Self

import httpx
import pytest

import app.llm.gateway as gateway_module
from app.llm.gateway import ChatMessage, HttpxTransport, LlmGateway, LlmUnavailable

_SECRET_KEY = "sk-smoke-secret-do-not-log"


# --- M14-100 timeout_seconds 构造契约 -------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, -0.5, True, "30", None])
def test_gateway_rejects_invalid_timeout_seconds(bad: Any) -> None:
    """0/负数/bool/字符串形态一律 ValueError（None 是合法的未配置语义）。"""
    if bad is None:
        return  # None = 未配置（合法），由默认构造测试覆盖
    with pytest.raises(ValueError, match="timeout_seconds"):
        LlmGateway(
            endpoint="http://llm.test/v1",
            api_key="k",
            model="m",
            timeout_seconds=bad,
        )


def test_gateway_timeout_none_is_valid_default_semantics() -> None:
    """timeout_seconds=None（默认）合法——等价既有行为（transport 默认 30s）。"""
    gateway = LlmGateway(endpoint="http://llm.test/v1", api_key="k", model="m")
    assert isinstance(gateway._transport, HttpxTransport)


def test_gateway_timeout_and_transport_are_exclusive() -> None:
    """显式 transport 注入与 timeout_seconds 同时出现必须拒绝——防静默丢弃
    超时配置（transport 自带超时语义，二者并存无从裁决）。"""

    class _Stub:
        def post_json(self, url: str, *, headers: dict, payload: dict) -> tuple[int, Any]:
            return 200, {}

    with pytest.raises(ValueError, match="互斥"):
        LlmGateway(
            endpoint="http://llm.test/v1",
            api_key="k",
            model="m",
            transport=_Stub(),  # type: ignore[arg-type]
            timeout_seconds=60.0,
        )


def test_gateway_default_construction_passes_no_timeout(monkeypatch) -> None:
    """未配置超时：默认 HttpxTransport 构造不携带 timeout_seconds——30s
    默认值留在 HttpxTransport 自身（既有 cloud 语义零漂移）。"""
    captured: list[dict] = []

    class _SpyTransport:
        def __init__(self, **kwargs: Any) -> None:
            captured.append(kwargs)

    monkeypatch.setattr(gateway_module, "HttpxTransport", _SpyTransport)
    LlmGateway(endpoint="http://llm.test/v1", api_key="k", model="m")
    assert captured == [{}]


def test_gateway_passes_explicit_timeout_to_default_transport(monkeypatch) -> None:
    """本地拓扑显式超时（如饱和 GPU 上的 thinking 模型需要 >30s）原样
    传播到默认 HttpxTransport 构造。"""
    captured: list[dict] = []

    class _SpyTransport:
        def __init__(self, **kwargs: Any) -> None:
            captured.append(kwargs)

    monkeypatch.setattr(gateway_module, "HttpxTransport", _SpyTransport)
    LlmGateway(
        endpoint="http://127.0.0.1:11434/v1",
        api_key="k",
        model="aios-qwen3.5-9b-4096",
        timeout_seconds=300.0,
    )
    assert captured == [{"timeout_seconds": 300.0}]


# --- settings 契约 ---------------------------------------------------------------


def test_settings_blank_llm_timeout_is_none() -> None:
    """compose `${AIOS_LLM_TIMEOUT_SECONDS:-}` 空串形态归一为 None（未配置
    语义——gateway 保持既有默认 30s，零漂移）。"""
    from app.core.config import Settings

    for raw in (None, "", "   "):
        assert Settings(_env_file=None, llm_timeout_seconds=raw).llm_timeout_seconds is None


def test_settings_accepts_valid_and_rejects_invalid_llm_timeout() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    assert Settings(_env_file=None, llm_timeout_seconds="300.5").llm_timeout_seconds == 300.5
    assert Settings(_env_file=None, llm_timeout_seconds=30).llm_timeout_seconds == 30.0
    for raw in ("0", "0.0", "-1", "abc", "true"):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, llm_timeout_seconds=raw)


def test_build_llm_judge_passes_timeout_through(monkeypatch) -> None:
    """build_llm_judge 把 timeout_seconds 透传进 LlmGateway 构造（零网络）。"""
    from app.llm.rubric_judge import build_llm_judge

    captured: dict = {}

    class _SpyGateway:
        def __init__(self, *, endpoint, api_key, model, num_ctx=None, timeout_seconds=None):
            captured.update(
                endpoint=endpoint,
                api_key=api_key,
                model=model,
                num_ctx=num_ctx,
                timeout_seconds=timeout_seconds,
            )

    monkeypatch.setattr("app.llm.rubric_judge.LlmGateway", _SpyGateway)
    judge = build_llm_judge(
        {
            "endpoint": "http://127.0.0.1:11434/v1",
            "api_key": "k",
            "model": "m",
            "timeout_seconds": 300.0,
        }
    )
    assert judge is not None
    assert captured["timeout_seconds"] == 300.0


def test_build_llm_judge_omits_timeout_when_unset(monkeypatch) -> None:
    """未配置超时：透传 None（gateway 默认 30s——cloud 部署零行为漂移）。"""
    from app.llm.rubric_judge import build_llm_judge

    captured: dict = {}

    class _SpyGateway:
        def __init__(self, *, endpoint, api_key, model, num_ctx=None, timeout_seconds=None) -> None:
            captured.update(timeout_seconds=timeout_seconds)

    monkeypatch.setattr("app.llm.rubric_judge.LlmGateway", _SpyGateway)
    assert (
        build_llm_judge(
            {"endpoint": "http://x/v1", "api_key": "k", "model": "m"}
        )
        is not None
    )
    assert captured["timeout_seconds"] is None


# --- 有界生成预算序列化 ----------------------------------------------------------


def test_gateway_max_tokens_budget_serialized_into_payload() -> None:
    """有界输出预算经既有 OpenAI 兼容字段 max_tokens 序列化进 payload——
    本地 thinking 模型在饱和 GPU 上的预算控制不发明规范外字段（M14-71
    options.num_ctx 教训：Ollama /v1 对非规范字段不可靠消费）。"""

    class _CaptureTransport:
        def __init__(self) -> None:
            self.payload: dict = {}

        def post_json(self, url: str, *, headers: dict, payload: dict) -> tuple[int, Any]:
            self.payload = payload
            return 200, {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    transport = _CaptureTransport()
    LlmGateway(
        endpoint="http://127.0.0.1:11434/v1", api_key="k", model="m", transport=transport
    ).chat((ChatMessage(role="user", content="q"),), max_tokens=256)
    assert transport.payload["max_tokens"] == 256


# --- loopback 代理绕过与 httpx 异常映射（monkeypatch httpx.Client） --------------


class _FakeClient:
    """httpx.Client 替身：记录 post 调用；按注入的 response/exc 行为。"""

    def __init__(self, *, response: httpx.Response | None, exc: Exception | None) -> None:
        self._response = response
        self._exc = exc
        self.post_calls: list[dict] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.post_calls.append({"url": url, **kwargs})
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response


def _ok_response(url: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        request=httpx.Request("POST", url),
    )


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: httpx.Response | None = None,
    exc: Exception | None = None,
) -> list[dict]:
    """把 gateway 模块用的 httpx.Client 换成记录构造参数的工厂；返回
    构造 kwargs 列表（每次 post_json 新建一个 client——与真实行为同构）。"""
    created: list[dict] = []

    def _factory(**kwargs: Any) -> _FakeClient:
        created.append(kwargs)
        return _FakeClient(response=response, exc=exc)

    monkeypatch.setattr(gateway_module.httpx, "Client", _factory)
    return created


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1/chat/completions",
        "http://localhost:11434/v1/chat/completions",
        "http://[::1]:11434/v1/chat/completions",
        "http://127.3.4.5:11434/v1/chat/completions",  # 127.0.0.0/8 全段
    ],
)
def test_transport_loopback_urls_bypass_env_proxy(monkeypatch, url: str) -> None:
    """loopback 端点构造 client 必须显式 trust_env=False——Windows 系统代理
    环境变量（HTTP(S)_PROXY）不得劫持本机 LLM 请求（M14-98 attempt1
    traceback 含 httpcore http_proxy 帧的根因）。"""
    created = _patch_client(monkeypatch, response=_ok_response(url))
    status, body = HttpxTransport().post_json(
        url, headers={"Authorization": f"Bearer {_SECRET_KEY}"}, payload={"q": 1}
    )
    assert status == 200
    assert body["choices"]
    assert created == [{"timeout": 30.0, "trust_env": False}]


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1/chat/completions",
        "https://llm.example.cn/v1/chat/completions",
        "http://10.0.0.5:8000/v1/chat/completions",  # 内网非 loopback
    ],
)
def test_transport_cloud_urls_keep_default_trust_env(monkeypatch, url: str) -> None:
    """云端/内网端点不传 trust_env——保持 httpx 默认（尊重部署环境的代理
    配置，cloud 语义零漂移）。"""
    created = _patch_client(monkeypatch, response=_ok_response(url))
    HttpxTransport().post_json(url, headers={}, payload={})
    assert created == [{"timeout": 30.0}]


def test_transport_custom_timeout_reaches_client(monkeypatch) -> None:
    """本地显式超时值原样进入 httpx.Client(timeout=...)。"""
    url = "http://127.0.0.1:11434/v1/chat/completions"
    created = _patch_client(monkeypatch, response=_ok_response(url))
    HttpxTransport(timeout_seconds=300.0).post_json(url, headers={}, payload={})
    assert created == [{"timeout": 300.0, "trust_env": False}]


def test_transport_timeout_exception_maps_to_llm_unavailable(monkeypatch) -> None:
    """httpx.ReadTimeout（M14-98 两跑 llm 冒烟的真实失败形态）必须映射为
    LlmUnavailable（冒烟脚本/判分管线的统一失败语义），消息含超时秒数。"""
    url = "http://127.0.0.1:11434/v1/chat/completions"
    _patch_client(monkeypatch, exc=httpx.ReadTimeout("read timed out"))
    with pytest.raises(LlmUnavailable, match="超时.*30"):
        HttpxTransport().post_json(url, headers={}, payload={})


def test_transport_http_error_maps_to_llm_unavailable(monkeypatch) -> None:
    """连接失败等 httpx.HTTPError 一律 LlmUnavailable（不裸抛 httpx 异常）。"""
    url = "http://127.0.0.1:11434/v1/chat/completions"
    _patch_client(monkeypatch, exc=httpx.ConnectError("connection refused"))
    with pytest.raises(LlmUnavailable, match="传输失败"):
        HttpxTransport().post_json(url, headers={}, payload={})


def test_transport_malformed_json_maps_to_llm_unavailable(monkeypatch) -> None:
    """HTTP 200 但响应非 JSON（malformed）→ LlmUnavailable（既有行为回归锁定）。"""
    url = "http://127.0.0.1:11434/v1/chat/completions"
    response = httpx.Response(
        200, content=b"<html>gateway error page</html>", request=httpx.Request("POST", url)
    )
    _patch_client(monkeypatch, response=response)
    with pytest.raises(LlmUnavailable, match="非 JSON"):
        HttpxTransport().post_json(url, headers={}, payload={})


def test_failure_messages_never_leak_api_key(monkeypatch) -> None:
    """所有失败形态的 LlmUnavailable 消息不含 api_key（key 只在 Authorization
    header，绝不进入异常文本/日志）。"""
    url = "http://127.0.0.1:11434/v1/chat/completions"
    headers = {"Authorization": f"Bearer {_SECRET_KEY}"}
    failures: list[Exception] = [
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectError("connection refused"),
        httpx.ProxyError("proxy connect failed"),
    ]
    for exc in failures:
        _patch_client(monkeypatch, exc=exc)
        with pytest.raises(LlmUnavailable) as excinfo:
            HttpxTransport().post_json(url, headers=headers, payload={})
        assert _SECRET_KEY not in str(excinfo.value)

    # HTTP 非 2xx 的失败语义在 gateway 层（transport 只管传输与 JSON 解码）
    class _ErrorTransport:
        def post_json(self, url: str, *, headers: dict, payload: dict) -> tuple[int, Any]:
            return 500, {"error": "boom"}

    with pytest.raises(LlmUnavailable) as excinfo:
        LlmGateway(
            endpoint="http://127.0.0.1:11434/v1",
            api_key=_SECRET_KEY,
            model="m",
            transport=_ErrorTransport(),  # type: ignore[arg-type]
        ).chat((ChatMessage(role="user", content="q"),))
    assert _SECRET_KEY not in str(excinfo.value)
