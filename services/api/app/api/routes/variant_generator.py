"""M5-08 Variant question generator API：源题 -> 解保持变式草稿 -> 人工审核队列。

- POST /api/v1/questions/variant-drafts：对源题（1..20 道）生成解保持变式
  （context_swap 语境替换解不变 / numeric_scale 精确 x2 重标定选项同步）；
  变式逐题继承概念映射并携带原题 evidence；0 变式题 note 明示不虚报；
- GET  ?status=：审核队列（缺省全部）；
- GET  /{id}：草稿详情（variants JSON 可审计）；
- POST /{id}/approve | /{id}/reject：人工决定；终态重复审核 409。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.api.routes.auth import (
    build_audit_payload,
    current_is_admin,
    current_owner_id,
    require_admin,
)
from app.domain.variant_generator import generate_variant_draft

router = APIRouter(prefix="/api/v1/questions/variant-drafts", tags=["questions"])

VALID_QTYPES = frozenset({"mcq", "multiple_select", "true_false", "fill_blank", "solved", "unknown", "numeric"})


class SourceOption(BaseModel):
    label: str = Field(min_length=1, max_length=4)
    text: str = Field(min_length=1, max_length=200)


class SourceQuestion(BaseModel):
    question_no: int = Field(ge=1)
    stem: str = Field(min_length=1, max_length=1000)
    question_type: str
    score: float | None = Field(default=None, ge=0)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    resource_id: str | None = Field(default=None, max_length=64)
    options: list[SourceOption] = Field(default_factory=list)
    concept_ids: list[str] = Field(default_factory=list)

    @field_validator("question_type")
    @classmethod
    def _check_type(cls, value: str) -> str:
        if value not in VALID_QTYPES:
            raise ValueError(f"未知题型: {value}（支持: {sorted(VALID_QTYPES)}）")
        return value


class GenerateVariantRequest(BaseModel):
    questions: list[SourceQuestion] = Field(min_length=1, max_length=20)


class EvidenceOut(BaseModel):
    source_question_no: int | None
    page_start: int | None
    page_end: int | None
    resource_id: str | None
    source_stem_excerpt: str


class VariantOut(BaseModel):
    variant_no: int
    transform: str
    transform_detail: str
    stem: str
    question_type: str
    score: float | None
    options: list[dict]
    concept_ids: list[str]
    evidence: EvidenceOut
    solvable_note: str


class VariantDraftOut(BaseModel):
    id: str
    status: str
    variants: list[VariantOut]
    variant_count: int
    generation_note: str
    review_note: str | None
    reviewed_at: str | None
    created_at: str


def _repo(request: Request):
    repo = getattr(request.app.state, "variant_question_drafts", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Variant generation requires a database")
    return repo


async def _require_draft_readable(request: Request, draft_id: str) -> None:
    """M9-05 草稿读取门：auth on 时普通用户只能读自己的（他人/无主 404）。"""
    if await current_is_admin(request):
        return
    owner = current_owner_id(request)
    if owner is not None:
        draft_owner = await _repo(request).get_owner(draft_id)
        if draft_owner != owner:
            raise HTTPException(status_code=404, detail="变式草稿不存在")


@router.post("", response_model=VariantDraftOut, status_code=201)
async def create_variant_draft(payload: GenerateVariantRequest, request: Request) -> VariantDraftOut:
    """解保持变换生成变式草稿；0 变式题明示（不虚报），全部进审核队列。"""
    questions = [q.model_dump() for q in payload.questions]
    try:
        draft = generate_variant_draft(questions)
    except ValueError as cause:
        raise HTTPException(status_code=422, detail=str(cause)) from cause
    if draft["variant_count"] == 0:
        raise HTTPException(
            status_code=422,
            detail=f"全部源题均无法生成解保持变式（无实体映射命中且无数值）: {draft['generation_note']}",
        )
    record = await _repo(request).create(
        variants=draft["variants"],
        variant_count=draft["variant_count"],
        generation_note=draft["generation_note"],
        owner_id=current_owner_id(request),  # M9-05: 草稿归属发起者
    )
    return VariantDraftOut(**record)


@router.get("", response_model=list[VariantDraftOut])
async def list_variant_drafts(request: Request, status: str | None = None) -> list[VariantDraftOut]:
    """审核队列；M9-05 auth on 时普通用户只见自己的草稿（admin/本地模式见全部）。"""
    owner = None if await current_is_admin(request) else current_owner_id(request)
    records = await _repo(request).list_by_status(status, owner)
    return [VariantDraftOut(**r) for r in records]


@router.get("/{draft_id}", response_model=VariantDraftOut)
async def get_variant_draft(draft_id: str, request: Request) -> VariantDraftOut:
    await _require_draft_readable(request, draft_id)
    record = await _repo(request).get(draft_id)
    if record is None:
        raise HTTPException(status_code=404, detail="变式草稿不存在")
    return VariantDraftOut(**record)


def _terminal_guard(record: dict | str | None) -> VariantDraftOut:
    """MISSING -> 404、TERMINAL -> 409、正常 dict -> 模型（approve/reject 复用）。"""
    if record == "MISSING":
        raise HTTPException(status_code=404, detail="变式草稿不存在")
    if record == "TERMINAL":
        raise HTTPException(status_code=409, detail="草稿已终态（approved/rejected），不可再次审核")
    return VariantDraftOut(**record)


class ReviewRequest(BaseModel):
    note: str | None = Field(default=None, max_length=512)


@router.post("/{draft_id}/approve", response_model=VariantDraftOut)
async def approve_variant_draft(draft_id: str, payload: ReviewRequest, request: Request) -> VariantDraftOut:
    """人工通过：pending_review -> approved；终态重复 409。"""
    await require_admin(request)  # M9-04: 草稿审核是全局治理动作
    audit = await build_audit_payload(
        request, action="variant_generation.approve", target_type="variant_draft",
        target_id=draft_id, before={"status": "pending_review"},
        after={"status": "approved"},
    )
    record = await _repo(request).review(draft_id, "approved", payload.note, audit)
    return _terminal_guard(record)


@router.post("/{draft_id}/reject", response_model=VariantDraftOut)
async def reject_variant_draft(draft_id: str, payload: ReviewRequest, request: Request) -> VariantDraftOut:
    """人工驳回：pending_review -> rejected；终态重复 409。"""
    await require_admin(request)  # M9-04: 草稿审核是全局治理动作
    audit = await build_audit_payload(
        request, action="variant_generation.reject", target_type="variant_draft",
        target_id=draft_id, before={"status": "pending_review"},
        after={"status": "rejected"},
    )
    record = await _repo(request).review(draft_id, "rejected", payload.note, audit)
    return _terminal_guard(record)
