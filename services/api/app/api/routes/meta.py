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
    """响应同时携带 snake_case 与 camelCase 字段名。

    v0.1.0~v0.1.3 客户端 DTO 误用 camelCase（versionCode/downloadUrl），
    而本端点自 M15-006 起一直是 snake_case——「检查更新」全系报
    「API 返回数据格式异常」（真机 v0.1.3 复现）。双字段兼容：旧客户端
    （kotlinx ignoreUnknownKeys=true）忽略 snake 字段命中 camel 字段；
    v0.1.4+ 客户端已对齐 snake_case，两组字段等值由构造点保证。
    camelCase 别名不独立演化，仅作已发布版本的兼容通道。
    """

    platform: str
    version: str
    version_code: int
    download_url: str
    versionCode: int
    downloadUrl: str
    # M15-012 多通道：主通道不可达时的回退地址（可空 = 单通道语义不变）
    download_url_fallback: str | None = None
    downloadUrlFallback: str | None = None
    # M21 应用内自更新：安装包 SHA-256（主/回退同文件同哈希）。可空 =
    # 服务端未配置（旧客户端跳转下载语义不变）。
    sha256: str | None = None


@router.get("/latest-app", response_model=LatestAppOut)
async def latest_app(platform: str = "android") -> LatestAppOut:
    if platform not in _PLATFORMS:
        raise HTTPException(
            status_code=422,
            detail=f"platform 必须是 {'/'.join(_PLATFORMS)} 之一",
        )
    s = get_settings()
    if platform == "android":
        version, code, url, fallback, sha256 = (
            s.latest_android_version,
            s.latest_android_version_code,
            s.latest_android_download_url,
            s.latest_android_download_url_fallback,
            s.latest_android_sha256,
        )
    else:
        version, code, url, fallback, sha256 = (
            s.latest_harmony_version,
            s.latest_harmony_version_code,
            s.latest_harmony_download_url,
            s.latest_harmony_download_url_fallback,
            s.latest_harmony_sha256,
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
        versionCode=code,
        downloadUrl=url,
        download_url_fallback=fallback,
        downloadUrlFallback=fallback,
        sha256=sha256,
    )
