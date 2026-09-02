"""M5-05 Course import drafts API：授权门禁 -> 草稿 -> 人工审核队列。

- POST /api/v1/courses/import-drafts：资源授权不可复用 403 带原因（不虚报）；
- GET  /api/v1/courses/import-drafts?status=：审核队列（缺省全部）；
- POST /{id}/approve | /{id}/reject：人工决定；终态重复审核 409；
- GET  /{id}：草稿详情（含授权快照与提取说明，可审计）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.routes.auth import audit_from_request, require_admin
from app.domain.course_importer import NotAdmissible, build_import_draft

router = APIRouter(prefix="/api/v1/courses", tags=["courses"])


class ImportDraftRequest(BaseModel):
    resource_id: str = Field(min_length=1, max_length=64)


class ReviewRequest(BaseModel):
    note: str | None = Field(default=None, max_length=512)


class ImportDraftOut(BaseModel):
    id: str
    title: str
    status: str
    source_resource_id: str
    source_license_state: str
    reuse_admission: str
    concepts: list[str]
    resource_refs: list[str]
    extraction_note: str
    review_note: str | None
    reviewed_at: str | None
    created_at: str


def _repo(request: Request):
    repo = getattr(request.app.state, "course_import_drafts", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Course import requires a database")
    return repo


def _resource(request: Request, resource_id: str):
    repo = request.app.state.resources
    if repo is None:
        raise HTTPException(status_code=503, detail="Course import requires a database")
    return repo.get(resource_id)


@router.post("/import-drafts", response_model=ImportDraftOut, status_code=201)
async def create_import_draft(payload: ImportDraftRequest, request: Request) -> ImportDraftOut:
    """从授权资源生成草稿：门禁拒绝 403 带原因；资源不存在 404；成功进审核队列。"""
    repo = _repo(request)
    chunks = request.app.state.chunks
    if chunks is None:
        raise HTTPException(status_code=503, detail="Course import requires a database")

    resource = await _resource(request, payload.resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="资源不存在")

    texts = [c["text"] for c in await chunks.list_chunks(payload.resource_id)]
    try:
        draft = build_import_draft(
            resource_id=payload.resource_id,
            title=resource.title,
            license_state=resource.license_state.value,
            chunk_texts=texts,
        )
    except NotAdmissible as cause:
        raise HTTPException(status_code=403, detail=str(cause)) from cause

    return ImportDraftOut(**await repo.create(draft))


@router.get("/import-drafts", response_model=list[ImportDraftOut])
async def list_import_drafts(request: Request, status: str | None = None) -> list[ImportDraftOut]:
    """审核队列：status 过滤（pending_review/approved/rejected），缺省全部。"""
    return [ImportDraftOut(**item) for item in await _repo(request).list_by_status(status)]


@router.get("/import-drafts/{draft_id}", response_model=ImportDraftOut)
async def get_import_draft(draft_id: str, request: Request) -> ImportDraftOut:
    record = await _repo(request).get(draft_id)
    if record is None:
        raise HTTPException(status_code=404, detail="草稿不存在")
    return ImportDraftOut(**record)


@router.post("/import-drafts/{draft_id}/approve", response_model=ImportDraftOut)
async def approve_draft(draft_id: str, payload: ReviewRequest, request: Request) -> ImportDraftOut:
    """人工通过：pending_review -> approved；终态重复 409。"""
    await require_admin(request)  # M9-04: 草稿审核是全局治理动作
    record = await _repo(request).review(draft_id, "approved", payload.note)
    await audit_from_request(
        request, action="course_import.approve", target_type="import_draft",
        target_id=draft_id, before={"status": "pending_review"},
        after={"status": "approved"},
    )
    if record == "MISSING":
        raise HTTPException(status_code=404, detail="草稿不存在")
    if record == "TERMINAL":
        raise HTTPException(status_code=409, detail="草稿已终态（approved/rejected），不可再次审核")
    return ImportDraftOut(**record)


@router.post("/import-drafts/{draft_id}/reject", response_model=ImportDraftOut)
async def reject_draft(draft_id: str, note: ReviewRequest, request: Request) -> ImportDraftOut:
    """人工驳回：pending_review -> rejected；终态重复 409。"""
    await require_admin(request)  # M9-04: 草稿审核是全局治理动作
    record = await _repo(request).review(draft_id, "rejected", note.note)
    await audit_from_request(
        request, action="course_import.reject", target_type="import_draft",
        target_id=draft_id, before={"status": "pending_review"},
        after={"status": "rejected"},
    )
    if record == "MISSING":
        raise HTTPException(status_code=404, detail="草稿不存在")
    if record == "TERMINAL":
        raise HTTPException(status_code=409, detail="草稿已终态（approved/rejected），不可再次审核")
    return ImportDraftOut(**record)
