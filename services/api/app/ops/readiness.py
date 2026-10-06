"""M14-246 /readyz 依赖就绪检查（与 /health liveness 语义分离）。

- /health = 进程存活（liveness），恒 200 静态体；
- /readyz = 依赖就绪（readiness）：注入的检查全部通过才 200，否则 503；
- 检查可注入（app.state.readiness_checks），单测用假检查，绝不依赖真实
  DB/Redis/voice/LiveKit；部署级依赖检查由部署按需注入；
- reason 安全面净化（sanitize_reason）：正常安全文案（如 "db
  unreachable"、异常类名）原样保留；含控制字符、URL/连接串形态
  （scheme:// 或 user:pass@host）、敏感标记（token/secret/password/
  credential/authorization/bearer/api_key 等）、超长（>128）或不透明
  长串（无空白且 >48 字符——随机 base64/hex/API key 形态可不含敏感词
  或 URL 语法，保守拒绝）的 reason 一律替换为固定安全回退文案——部署
  检查误把凭据/连接串放进 reason 时不会经未认证的 /readyz 泄出；
- 检查注册表为空时 /readyz fail-closed（503 + 固定安全诊断条目），
  绝不因空序列聚合恒真而虚报 ready；
- 单项检查抛异常不炸端点：按 not_ready 记账，reason 只取异常类名
  （同样过净化面）。
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

#: reason 长度上限（超出视为不安全，走固定回退）。
_MAX_REASON_LENGTH = 128

#: 不透明串上限：无任何空白且超过该长度 = 疑似不透明凭据（随机
#: base64/hex/API key 形态可不含敏感词或 URL 语法），保守拒绝。
_OPAQUE_MAX_LENGTH = 48

#: 敏感标记（小写子串匹配）。注意不含裸 "key"——KeyError 等异常类名须放行。
_SENSITIVE_MARKERS: tuple[str, ...] = (
    "authorization",
    "bearer",
    "credential",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
)

#: user:pass@host 形态（连接串内嵌凭据）。
_USERINFO_PATTERN = re.compile(r"[A-Za-z0-9_.~+-]+:[^@\s]+@")

#: 不安全 reason 的固定安全回退文案。
UNSAFE_REASON_FALLBACK = "reason withheld (failed safety check)"


def sanitize_reason(reason: str | None) -> str | None:
    """readiness 边界的 reason 净化：安全文案保留，不安全形态固定回退。

    不安全 = 控制字符 / URL 或连接串形态（"://" 或 userinfo）/ 敏感标记 /
    超过 _MAX_REASON_LENGTH / 不透明长串（无空白且 > _OPAQUE_MAX_LENGTH，
    覆盖不含敏感词或 URL 语法的随机凭据形态）。空白归一为 None（省略键）。
    """
    if reason is None:
        return None
    text = reason.strip()
    if not text:
        return None
    if len(text) > _MAX_REASON_LENGTH:
        return UNSAFE_REASON_FALLBACK
    if len(text) > _OPAQUE_MAX_LENGTH and not any(ch.isspace() for ch in text):
        return UNSAFE_REASON_FALLBACK
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return UNSAFE_REASON_FALLBACK
    if "://" in text or _USERINFO_PATTERN.search(text):
        return UNSAFE_REASON_FALLBACK
    lowered = text.lower()
    if any(marker in lowered for marker in _SENSITIVE_MARKERS):
        return UNSAFE_REASON_FALLBACK
    return text


@dataclass(frozen=True)
class CheckOutcome:
    """单项检查结果：ready + 可选 reason（经 sanitize_reason 净化后透出）。"""

    ready: bool
    reason: str | None = None


#: 就绪检查：同步或异步可调用，返回 CheckOutcome。
Check = Callable[[], CheckOutcome | Awaitable[CheckOutcome]]


async def evaluate_checks(checks: Mapping[str, Check]) -> dict[str, dict[str, object]]:
    """逐项执行检查并聚合为响应体片段；异常仅记类名，端点不因检查失败而炸。"""
    results: dict[str, dict[str, object]] = {}
    for name, check in checks.items():
        try:
            outcome = check()
            if not isinstance(outcome, CheckOutcome):
                outcome = await outcome
        except Exception as exc:  # noqa: BLE001 —— 聚合为 not_ready，不让单检查炸端点
            outcome = CheckOutcome(ready=False, reason=type(exc).__name__)
        entry: dict[str, object] = {"ready": outcome.ready}
        safe_reason = sanitize_reason(outcome.reason)
        if safe_reason:
            entry["reason"] = safe_reason
        results[name] = entry
    return results
