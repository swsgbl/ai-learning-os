"""M14-246 结构化 JSON 日志（API bootstrap 路径）。

设计边界：
- 单行 JSON 固定字段：ts（UTC ISO-8601）/ level / logger / message；请求
  关联 request_id（record 属性优先，其次请求上下文 ContextVar，无则省略）；
- 只格式化既有 LogRecord，不记录请求头/请求体/响应体——敏感值不落日志的
  第一道边界；extra_fields 显式附带的敏感键（authorization/cookie/token/
  secret/password/api_key/credential）一律脱敏为 "***"；
- 异常不落原始形态：exc_info 只结构化为异常类型 + 脱敏栈位置（函数名 +
  文件 basename + 行号，栈帧数有上限）——原始异常消息（可能含连接串/
  凭据/请求派生文本）与完整 traceback 文本一律不出现在序列化输出；
- configure_logging 幂等：重复调用（含多次 create_app/测试反复建 app）不
  叠加 handler；level 仅在显式传入时设置——LOG_LEVEL 未配置 = 不改变既有
  logger 级别语义（零漂移）；
- uvicorn 自身 logger（uvicorn/uvicorn.error/uvicorn.access）由 uvicorn
  的 logging config 管理，本模块不动（边界见 m14-246 证据 README）。
"""
from __future__ import annotations

import json
import logging
import os
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from types import TracebackType

#: 请求关联：request-id 中间件 set，请求期间的应用日志自动携带。
request_id_var: ContextVar[str] = ContextVar("aios_request_id", default="")

#: 敏感键标记（小写子串匹配）——命中即把值脱敏为 _REDACTED。
_SENSITIVE_KEY_MARKERS: tuple[str, ...] = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "credential",
)
_REDACTED = "***"

#: 栈位置条目数上限（有界输出；超出截断）。
_MAX_STACK_FRAMES = 64


def _sanitize_exc_info(
    exc_info: tuple[type[BaseException] | None, BaseException | None, TracebackType | None],
) -> dict[str, object]:
    """异常脱敏结构化：类型 + 栈位置（函数名/文件 basename/行号）。

    绝不包含原始异常消息（str(exc) 可能携带凭据/连接串/请求派生文本）、
    完整 traceback 文本、源码行文本、完整文件路径或调用参数。
    """
    exc_type, _exc_value, tb = exc_info
    stack: list[dict[str, object]] = []
    if tb is not None:
        for frame in traceback.extract_tb(tb)[:_MAX_STACK_FRAMES]:
            stack.append(
                {
                    "func": frame.name,
                    "file": os.path.basename(frame.filename),
                    "line": frame.lineno,
                }
            )
    type_name = exc_type.__name__ if exc_type is not None else "UnknownError"
    return {"type": type_name, "stack": stack}


def _redact(key: str, value: object) -> object:
    lowered = key.lower()
    if any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS):
        return _REDACTED
    return value


class JsonLogFormatter(logging.Formatter):
    """单行 JSON formatter：固定字段 + request_id 关联 + 敏感键脱敏。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", "") or request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload["extra"] = {str(key): _redact(str(key), value) for key, value in extra.items()}
        if record.exc_info:
            payload["exc"] = _sanitize_exc_info(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str | int | None = None) -> logging.StreamHandler:
    """幂等安装 JSON handler 到 root logger 并返回该 handler。

    level 仅在显式提供时设置（None = 保留既有级别语义）；已安装过
    JsonLogFormatter 的 StreamHandler 时直接复用，不重复添加。
    """
    root = logging.getLogger()
    if level is not None:
        root.setLevel(level)
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and isinstance(
            handler.formatter, JsonLogFormatter
        ):
            return handler
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root.addHandler(handler)
    return handler
