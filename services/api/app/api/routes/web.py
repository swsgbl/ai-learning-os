"""M5-03 Web fetch gate API：抓取预检（URL 安全 / robots disallow / 频率限制）。

- POST /api/v1/web/fetch-check：综合预检，200 allowed 或 403 rejected（原因可见）；
- 超限频率 → 429（per-IP 固定窗口，上限可配 settings.fetch_rate_limit_per_minute）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.domain.web_gate import evaluate_fetch

router = APIRouter(prefix="/api/v1/web", tags=["web"])


class FetchCheckRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    path: str = Field(default="/", max_length=2048)
    robots_txt: str | None = Field(default=None, max_length=65536)


class FetchCheckOut(BaseModel):
    allowed: bool
    reason: str


@router.post("/fetch-check", response_model=FetchCheckOut)
async def fetch_check(payload: FetchCheckRequest, request: Request) -> FetchCheckOut:
    """抓取预检：危险协议/内网地址/disallow 路径 403（原因可见），超限 429。"""
    limiter = request.app.state.fetch_limiter
    client_ip = request.client.host if request.client else "unknown"
    if not limiter.allow(client_ip):
        raise HTTPException(status_code=429, detail="请求频率超限，请稍后再试")
    verdict = evaluate_fetch(payload.url, path=payload.path, robots_txt=payload.robots_txt)
    if not verdict["allowed"]:
        raise HTTPException(status_code=403, detail=verdict["reason"])
    return FetchCheckOut(allowed=True, reason="OK")
