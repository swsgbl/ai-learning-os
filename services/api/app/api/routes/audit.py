"""M9-04 治理审计读取：admin-only（learner 不可读）；auth off 本地单用户可读。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.routes.auth import require_admin

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


class AuditEntryOut(BaseModel):
    id: int
    actor_id: str | None
    actor_username: str | None
    action: str
    target_type: str
    target_id: str
    before: dict | None
    after: dict | None
    request_id: str
    created_at: str


@router.get("", response_model=list[AuditEntryOut])
async def list_audit(request: Request, limit: int = 100) -> list[AuditEntryOut]:
    """审计日志只读面（治理动作留痕回查）。"""
    await require_admin(request)  # auth on: learner 403；auth off: 本地单用户放行
    repo = getattr(request.app.state, "audit", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Audit log requires a database")
    limit = max(1, min(limit, 500))
    return [AuditEntryOut(**entry) for entry in await repo.list_recent(limit=limit)]
