from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

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
