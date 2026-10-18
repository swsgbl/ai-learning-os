"""M15-006 应用最新版本透出（应用内更新检查的服务端面）。

诚实边界：
- 版本事实由部署 env 注入（Settings 三项：version / version_code /
  download_url），**三项齐全才透出**；缺任一 = 未配置 → 404 如实
  （不虚报有新版本，不让客户端猜默认下载地址）。
- platform 白名单 {android, harmony}；其余 422。harmony 未配置同样
  404（不虚报双端就绪）。
- 端点在认证门禁豁免列表：更新检查发生在登录之前（首启流程先连
  服务器、后注册/登录）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter(prefix="/api/v1/meta", tags=["meta"])

_PLATFORMS = ("android", "harmony")


class LatestAppOut(BaseModel):
    platform: str
    version: str
    version_code: int
    download_url: str


@router.get("/latest-app", response_model=LatestAppOut)
async def latest_app(platform: str = "android") -> LatestAppOut:
    if platform not in _PLATFORMS:
        raise HTTPException(
            status_code=422,
            detail=f"platform 必须是 {'/'.join(_PLATFORMS)} 之一",
        )
    s = get_settings()
    if platform == "android":
        version, code, url = (
            s.latest_android_version,
            s.latest_android_version_code,
            s.latest_android_download_url,
        )
    else:
        version, code, url = (
            s.latest_harmony_version,
            s.latest_harmony_version_code,
            s.latest_harmony_download_url,
        )
    if not (version and code and url):
        raise HTTPException(
            status_code=404,
            detail=f"{platform} 最新版本未配置（部署 env 三项齐全后透出）",
        )
    return LatestAppOut(
        platform=platform,
        version=version,
        version_code=code,
        download_url=url,
    )
