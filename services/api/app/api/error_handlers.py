"""M14-246 全局异常处理：domain 错误 HTTP 映射 + 未捕获异常 500 兜底。

兼容边界（不得削弱既有契约）：
- HTTPException / RequestValidationError 不在此注册——保留 FastAPI 既有
  处理（401/404 JSON detail 形状、422 校验错误列表形状均不变）；
- DomainError 族：NotFoundError→404、ConflictError→409、其余 DomainError
  →400，响应体与 HTTPException 同形 {"detail": str(exc)}（此前未注册
  handler 时这类异常会以 500 逃逸）；
- 未捕获异常：500 固定脱敏 detail（不透出异常类/文本），并经
  request.state 回填 X-Request-ID——该响应由最外层 ServerErrorMiddleware
  生成，不经过内层 request-id 中间件；服务端结构化日志只落异常类型与
  脱敏栈位置（函数名+文件 basename+行号；原始异常消息/traceback 文本
  由 JsonLogFormatter 统一净化，不进日志），带 request_id 关联；处理后
  异常继续上抛（uvicorn/测试客户端仍可见）。
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse

from app.core.errors import ConflictError, DomainError, NotFoundError

logger = logging.getLogger(__name__)

#: DomainError 子类 → HTTP 状态码（按 MRO 顺序首个命中生效）。
_DOMAIN_STATUS: tuple[tuple[type[DomainError], int], ...] = (
    (NotFoundError, 404),
    (ConflictError, 409),
)


def _domain_status(exc: DomainError) -> int:
    for exc_type, status in _DOMAIN_STATUS:
        if isinstance(exc, exc_type):
            return status
    return 400


def register_exception_handlers(app: FastAPI) -> None:
    """在 create_app 里注册全局异常处理（幂等语义：每 app 恰一次）。"""

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=_domain_status(exc))

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.exception(
            "unhandled_exception",
            extra={
                "request_id": request_id,
                "extra_fields": {"exception_class": type(exc).__name__},
            },
        )
        return JSONResponse(
            {"detail": "Internal server error"},
            status_code=500,
            headers={"X-Request-ID": request_id},
        )
