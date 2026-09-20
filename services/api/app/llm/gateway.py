"""M10-01 LLM gateway：OpenAI 兼容 chat completions 最小客户端。

设计边界（ADR 65）：
- OpenAI 兼容协议（/chat/completions），endpoint/key/model 全部来自部署配置，
  key 不入库不入码不入日志；
- 同步 httpx（判分管线是同步域函数）：单用户本地版可接受；并发化是后续演进；
- transport 可注入（单测不需要网络）；
- 任何网络/协议失败抛 LlmUnavailable——调用方按 fail-closed 处理（judge 侧
  转 None → 复核），gateway 自身绝不重试猜分、绝不编造响应。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class LlmUnavailable(Exception):
    """LLM 端点不可达 / 响应协议非法 / HTTP 非 2xx。"""


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


class Transport(Protocol):
    """最小传输协议：POST JSON -> 状态码 + 响应 JSON。可注入替身。"""

    def post_json(self, url: str, *, headers: dict[str, str], payload: dict) -> tuple[int, Any]: ...


class HttpxTransport:
    """真实传输：OpenAI 兼容端点的薄封装。"""

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        self._timeout = timeout_seconds

    def post_json(self, url: str, *, headers: dict[str, str], payload: dict) -> tuple[int, Any]:
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(url, headers=headers, json=payload)
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
    """

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        model: str,
        transport: Transport | None = None,
        num_ctx: int | None = None,
    ) -> None:
        if not endpoint or not api_key or not model:
            raise ValueError("LLM gateway 需要 endpoint / api_key / model 全部配置")
        if isinstance(num_ctx, bool) or (
            num_ctx is not None and (not isinstance(num_ctx, int) or num_ctx < 1)
        ):
            raise ValueError(f"LLM gateway num_ctx 必须是 >=1 的整数: {num_ctx!r}")
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._transport = transport or HttpxTransport()
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
        """返回首条 assistant 回复文本；任何失败抛 LlmUnavailable（不重试不猜分）。"""
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
