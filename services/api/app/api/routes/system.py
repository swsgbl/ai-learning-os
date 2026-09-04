from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.routes.auth import require_admin
from app.core.config import get_settings
from app.ops.snapshot import OpsSnapshotError, OpsSnapshotOut, build_ops_snapshot

router = APIRouter(prefix="/api/v1/system", tags=["system"])


class PrivacyOut(BaseModel):
    model_route: Literal["local", "cloud", "hybrid"]
    voice_mode: Literal["local", "cloud", "hybrid"]
    search_mode: Literal["local", "cloud", "hybrid"]
    store_audio: bool
    send_context_to_cloud: bool


@router.get("/privacy", response_model=PrivacyOut)
async def get_privacy() -> PrivacyOut:
    """当前隐私路由快照（不含任何凭据）。UI 用它展示当前模式，见架构文档 6.2。"""
    settings = get_settings()
    return PrivacyOut(
        model_route=settings.model_route,
        voice_mode=settings.voice_mode,
        search_mode=settings.search_mode,
        store_audio=settings.privacy_store_audio,
        send_context_to_cloud=settings.privacy_send_context_to_cloud,
    )


@router.get("/ops-snapshot", response_model=OpsSnapshotOut)
async def get_ops_snapshot(request: Request) -> OpsSnapshotOut:
    """M10-14 运行观测快照：admin-only 只读聚合（learner 403 / 未认证 401）。

    仅持久 DB 模式可用（内存模式无 sessionmaker -> 503）；数据库异常同样 503
    固定脱敏 detail——不透出异常文本/连接串。字段语义见 DEVELOPMENT.md M10-14。
    """
    await require_admin(request)  # auth on: learner 403；auth off: 本地单用户放行
    sessionmaker = getattr(request.app.state, "sessionmaker", None)
    engine = getattr(request.app.state, "engine", None)
    if sessionmaker is None or engine is None:
        raise HTTPException(status_code=503, detail="Ops snapshot requires a database")
    worker = getattr(request.app.state, "worker", None)
    try:
        return await build_ops_snapshot(
            sessionmaker,
            engine,
            worker_running=worker is not None and worker.is_running(),
        )
    except OpsSnapshotError:
        # detail 固定脱敏：不嵌 OpsSnapshotError/SQLAlchemy 异常链（可能含连接串）
        raise HTTPException(
            status_code=503, detail="Ops snapshot temporarily unavailable"
        ) from None
