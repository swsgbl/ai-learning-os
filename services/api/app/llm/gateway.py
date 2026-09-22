"""M10-01 LLM gateway：OpenAI 兼容 chat completions 最小客户端。

设计边界（ADR 65）：
- OpenAI 兼容协议（/chat/completions），endpoint/key/model 全部来自部署配置，
  key 不入库不入码不入日志；
- 同步 httpx（判分管线是同步域函数）：单用户本地版可接受；并发化是后续演进；
- transport 可注入（单测不需要网络）；
- 任何网络/协议失败抛 LlmUnavailable——调用方按 fail-closed 处理（judge 侧
  转 None → 复核），gateway 自身绝不重试猜分、绝不编造响应。

M14-100 超时与 loopback 代理边界（docs/evidence/m14-100-local-llm-timeout-budget/）：
- ``timeout_seconds`` 显式可配（默认 None = 既有 30s，cloud 语义零漂移）；
  本地慢速拓扑（饱和 GPU 上的 thinking 模型）由部署显式调大；
- loopback 端点强制 ``trust_env=False``——Windows 系统代理环境变量不得
  劫持本机 LLM 请求（M14-98 attempt1 traceback 含 httpcore http_proxy 帧）；
  云端端点保持 httpx 默认（尊重部署代理配置，语义不变）；
- httpx 超时/传输异常一律映射 LlmUnavailable（统一失败语义，绝不裸抛
  httpx 异常给判分管线/冒烟脚本）。
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx


class LlmUnavailable(Exception):
    """LLM 端点不可达 / 响应协议非法 / HTTP 非 2xx / 请求超时。"""


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


class Transport(Protocol):
    """最小传输协议：POST JSON -> 状态码 + 响应 JSON。可注入替身。"""

    def post_json(self, url: str, *, headers: dict[str, str], payload: dict) -> tuple[int, Any]: ...


def _is_loopback_url(url: str) -> bool:
    """URL 的 host 是否 loopback（localhost / 127.0.0.0/8 / ::1）。

    hostname 非 IP 字面量（云端域名、容器服务名）一律 False——只有确定
    的本机地址才绕过环境代理。
    """
    host = urlsplit(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class HttpxTransport:
    """真实传输：OpenAI 兼容端点的薄封装。

    M14-100: 30s 默认超时是既有行为（cloud 部署零漂移）；本地慢速拓扑
    由 ``timeout_seconds`` 显式覆写。loopback URL 构造 client 时强制
    ``trust_env=False``（系统/环境代理不得劫持本机请求），云端 URL 保持
    httpx 默认 trust_env（部署代理配置照常生效）。
    """

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self._timeout = timeout_seconds

    def post_json(self, url: str, *, headers: dict[str, str], payload: dict) -> tuple[int, Any]:
        client_kwargs: dict[str, Any] = {"timeout": self._timeout}
        if _is_loopback_url(url):
            client_kwargs["trust_env"] = False
        try:
            with httpx.Client(**client_kwargs) as client:
                response = client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as cause:
            raise LlmUnavailable(
                f"LLM 请求超时（>{self._timeout:g}s）: {type(cause).__name__}"
            ) from cause
        except httpx.HTTPError as cause:
            # 消息只含异常类型名——不含 URL/headers（api_key 在 header，绝不进异常文本）
            raise LlmUnavailable(f"LLM 传输失败: {type(cause).__name__}") from cause
        try:
            body: Any = response.json()
        except ValueError as cause:
            raise LlmUnavailable(f"响应非 JSON（HTTP {response.status_code}）") from cause
        return response.status_code, body


class LlmGateway:
    """chat completions 调用 + 响应解包；失败一律 LlmUnavailable。

    M14-71: ``num_ctx``（可选正整数）是 provider 特定的请求级上下文窗口
    提示——仅在配置时以顶层 ``options.num_ctx`` 随 payload 出示。兼容性
    不保证（OpenAI 规范外字段；Ollama /v1 实证不可靠消费，不据此声称
    生效），本地固定上下文应走模型别名（模型层 num_ctx，
    ``infra/provision_ollama_model.ps1``）；未配置（None）时 payload
    与既有形态逐字节一致（零行为漂移）。值本身非敏感（窗口大小），随
    endpoint/model 一样只来自部署配置，不入库不入码。

    M14-100: ``timeout_seconds``（可选 >0 数值，默认 None）显式配置请求
    超时——None 时默认 HttpxTransport 保持既有 30s（cloud 语义零漂移）；
    本地拓扑（饱和 GPU 上的 thinking 模型单次生成可 >30s，M14-98 两跑
    llm 冒烟真实超时）由部署经 ``LLM_TIMEOUT_SECONDS`` 显式调大。与
    ``transport`` 注入互斥（transport 自带超时语义，并存无从裁决）。
    """

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str,
        transport: Transport | None = None,
        num_ctx: int | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        if not endpoint or not api_key or not model:
            raise ValueError("LLM gateway 需要 endpoint / api_key / model 全部配置")
        if isinstance(num_ctx, bool) or (
            num_ctx is not None and (not isinstance(num_ctx, int) or num_ctx < 1)
        ):
            raise ValueError(f"LLM gateway num_ctx 必须是 >=1 的整数: {num_ctx!r}")
        if transport is not None and timeout_seconds is not None:
            raise ValueError(
                "transport 注入与 timeout_seconds 互斥（显式 transport 自带超时语义）"
            )
        if isinstance(timeout_seconds, bool) or (
            timeout_seconds is not None
            and (
                not isinstance(timeout_seconds, (int, float))
                or timeout_seconds <= 0
            )
        ):
            raise ValueError(
                f"LLM gateway timeout_seconds 必须是 >0 的数值: {timeout_seconds!r}"
            )
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._model = model
        if transport is None:
            transport = (
                HttpxTransport()
                if timeout_seconds is None
                else HttpxTransport(timeout_seconds=timeout_seconds)
            )
        self._transport = transport
        self._num_ctx = num_ctx

    @property
    def model(self) -> str:
        return self._model

    def chat(
        self,
        messages: tuple[ChatMessage, ...],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> str:
        """返回首条 assistant 回复文本；任何失败抛 LlmUnavailable（不重试不猜分）。

        M14-100: ``max_tokens`` 是既有 OpenAI 兼容字段上的**有界输出预算**——
        thinking 模型的推理消耗计入该预算（M14-98 实证 max_tokens=2048 在饱和
        GPU 上生成 >30s）。预算控制不发明规范外 reasoning 字段（M14-71
        options.num_ctx 教训：Ollama /v1 对非规范字段不可靠消费）；provider
        级 thinking 控制（如 Ollama native /api/chat 的 ``think``）属另一
        协议面，超出本 gateway 的 OpenAI 兼容契约。
        """
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # M14-71: 仅在配置时携带顶层 options（provider 特定请求级上下文窗口
        # 提示，兼容性不保证——Ollama /v1 实证不可靠消费）；默认不加键——
        # 未配置网关的 payload 与既有形态完全一致。
        if self._num_ctx is not None:
            payload["options"] = {"num_ctx": self._num_ctx}
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        status, body = self._transport.post_json(
            f"{self._endpoint}/chat/completions", headers=headers, payload=payload
        )
        if status != 200:
            raise LlmUnavailable(f"LLM 端点 HTTP {status}")
        try:
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as cause:
            raise LlmUnavailable("LLM 响应缺少 choices[0].message.content") from cause
