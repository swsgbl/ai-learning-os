"""M1-01 Source Registry API routes."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.routes.auth import build_audit_payload, require_admin
from app.domain.license import LicenseState, TrustTier

router = APIRouter(prefix="/api/v1/sources", tags=["sources"])

SourceType = Literal["university", "library", "oer", "government", "github", "community", "vendor"]


class SourceOut(BaseModel):
    id: str
    name: str
    source_type: str
    homepage: str
    authority_score: int
    terms_url: str | None
    robots_policy_snapshot: dict
    rate_limit: dict
    trust_tier: str
    license_state: str
    last_verified_at: datetime | None
    notes: str | None


class SourceCreate(BaseModel):
    id: str = Field(min_length=3, max_length=64, pattern=r"^src_[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=256)
    source_type: SourceType
    homepage: str = Field(min_length=1, max_length=1024)
    authority_score: int = Field(default=50, ge=0, le=100)
    terms_url: str | None = None
    rate_limit: dict = Field(default_factory=dict)
    trust_tier: TrustTier = TrustTier.U
    license_state: LicenseState = LicenseState.UNKNOWN
    notes: str | None = None


def _repo(request: Request):
    repo = getattr(request.app.state, "sources", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Source registry requires a database")
    return repo


@router.get("", response_model=list[SourceOut])
async def list_sources(request: Request, reuse_pool: bool = False) -> list[SourceOut]:
    """reuse_pool=true 时只返回可进入公共复用池的来源（M1-02）。"""
    records = await _repo(request).list(reuse_pool_only=reuse_pool)
    return [_source_out(record) for record in records]


@router.post("", response_model=SourceOut, status_code=201)
async def create_source(payload: SourceCreate, request: Request) -> SourceOut:
    await require_admin(request)  # M9-04: 来源登记是全局治理动作
    from app.domain.source import SourceRecord

    record = SourceRecord(
        id=payload.id,
        name=payload.name,
        source_type=payload.source_type,
        homepage=payload.homepage,
        authority_score=payload.authority_score,
        terms_url=payload.terms_url,
        robots_policy_snapshot={},
        rate_limit=payload.rate_limit,
        trust_tier=payload.trust_tier,
        license_state=payload.license_state,
        notes=payload.notes,
    )
    audit = await build_audit_payload(
        request, action="source.create", target_type="source", target_id=record.id,
        after={"license_state": record.license_state.value, "trust_tier": record.trust_tier},
    )
    try:
        created = await _repo(request).create(record, audit)
    except ValueError as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    return _source_out(created)


@router.get("/{source_id}", response_model=SourceOut)
async def get_source(source_id: str, request: Request) -> SourceOut:
    record = await _repo(request).get(source_id)
    if not record:
        raise HTTPException(status_code=404, detail="来源不存在")
    return _source_out(record)


@router.post("/{source_id}/verify", response_model=SourceOut)
async def verify_source(source_id: str, request: Request) -> SourceOut:
    await require_admin(request)  # M9-04
    # M9-06: 先构建审计 payload，单次 mark_verified 同事务落库（404 不写成功审计）
    audit = await build_audit_payload(
        request, action="source.verify", target_type="source", target_id=source_id,
        after={"verified": True},
    )
    record = await _repo(request).mark_verified(source_id, audit)
    if not record:
        raise HTTPException(status_code=404, detail="来源不存在")
    return _source_out(record)


class LicenseStateUpdate(BaseModel):
    state: LicenseState


@router.post("/{source_id}/license", response_model=SourceOut)
async def set_license_state(payload: LicenseStateUpdate, source_id: str, request: Request) -> SourceOut:
    """人工认定/改判 license 状态；非法迁移返回 409（M1-02 状态机）。"""
    await require_admin(request)  # M9-04: license 改判是全局治理动作
    previous = await _repo(request).get(source_id)
    audit = await build_audit_payload(
        request, action="source.license_change", target_type="source", target_id=source_id,
        before={"license_state": previous.license_state.value} if previous else None,
        after={"license_state": payload.state.value},
    )
    try:
        record = await _repo(request).set_license_state(source_id, payload.state, audit)
    except ValueError as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    if not record:
        raise HTTPException(status_code=404, detail="来源不存在")
    return _source_out(record)


def _source_out(record) -> SourceOut:
    return SourceOut(
        id=record.id,
        name=record.name,
        source_type=record.source_type,
        homepage=record.homepage,
        authority_score=record.authority_score,
        terms_url=record.terms_url,
        robots_policy_snapshot=record.robots_policy_snapshot,
        rate_limit=record.rate_limit,
        trust_tier=record.trust_tier.value,
        license_state=record.license_state.value,
        last_verified_at=record.last_verified_at,
        notes=record.notes,
    )
