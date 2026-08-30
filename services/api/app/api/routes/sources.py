"""M1-01 Source Registry API routes."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

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
async def list_sources(request: Request) -> list[SourceOut]:
    records = await _repo(request).list()
    return [_source_out(record) for record in records]


@router.post("", response_model=SourceOut, status_code=201)
async def create_source(payload: SourceCreate, request: Request) -> SourceOut:
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
    try:
        created = await _repo(request).create(record)
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
    record = await _repo(request).mark_verified(source_id)
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
