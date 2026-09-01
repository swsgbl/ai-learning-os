"""M5-06 Paper extractor API：试卷题目抽取草稿 -> 人工审核队列。

- POST /api/v1/papers/import-drafts：从已解析资源抽取题目草稿（页码/题型/分值保留）；
  资源不存在 404；未解析(parse_status != parsed) 409 带原因（不虚报）；
- GET  /api/v1/papers/import-drafts?status=：审核队列（缺省全部）；
- GET  /{id}：草稿详情（题目 JSON 可审计，含原文页码）；
- POST /{id}/approve | /{id}/reject：人工决定；终态重复审核 409。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.domain.paper_extractor import extract_questions

router = APIRouter(prefix="/api/v1/papers/import-drafts", tags=["papers"])


class ExtractRequest(BaseModel):
    resource_id: str = Field(min_length=1, max_length=64)


class ReviewRequest(BaseModel):
    note: str | None = Field(default=None, max_length=512)


class QuestionOut(BaseModel):
    question_no: int
    stem: str
    question_type: str
    score: float | None
    page_start: int | None
    page_end: int | None
    options: list[dict]


class PaperDraftOut(BaseModel):
    id: str
    resource_id: str
    status: str
    questions: list[QuestionOut]
    question_count: int
    extraction_note: str
    review_note: str | None
    reviewed_at: str | None
    created_at: str


def _repo(request: Request):
    repo = getattr(request.app.state, "paper_question_drafts", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Paper extraction requires a database")
    return repo


def _deps(request: Request):
    """路由依赖：chunks 与 resources 仓储（无 DB 时 FastAPI app.state 上为 None）。"""
    chunks = request.app.state.chunks
    resources = request.app.state.resources
    if chunks is None or resources is None:
        raise HTTPException(status_code=503, detail="Paper extraction requires a database")
    return chunks, resources


@router.post("", response_model=PaperDraftOut, status_code=201)
async def create_paper_draft(payload: ExtractRequest, request: Request) -> PaperDraftOut:
    """从已解析资源抽取题目草稿；未解析 409 带原因（不虚报）。"""
    chunks, resources = _deps(request)
    resource = await resources.get(payload.resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    if resource.parse_status != "parsed":
        raise HTTPException(
            status_code=409,
            detail=f"资源未解析完成，无法抽取题目（parse_status={resource.parse_status}）",
        )
    chunk_items = await chunks.list_chunks(payload.resource_id)
    draft = extract_questions(chunk_items)
    record = await _repo(request).create({"resource_id": payload.resource_id, **draft})
    return PaperDraftOut(**record)


@router.get("", response_model=list[PaperDraftOut])
async def list_paper_drafts(request: Request, status: str | None = None) -> list[PaperDraftOut]:
    """审核队列：status 过滤（pending_review/approved/rejected），缺省全部。"""
    return [PaperDraftOut(**item) for item in await _repo(request).list_by_status(status)]


@router.get("/{draft_id}", response_model=PaperDraftOut)
async def get_paper_draft(draft_id: str, request: Request) -> PaperDraftOut:
    record = await _repo(request).get(draft_id)
    if record is None:
        raise HTTPException(status_code=404, detail="草稿不存在")
    return PaperDraftOut(**record)


def _terminal_guard(record: dict | str | None) -> PaperDraftOut:
    """MISSING -> 404、TERMINAL -> 409、正常 dict -> 模型（供 approve/reject 复用）。"""
    if record == "MISSING":
        raise HTTPException(status_code=404, detail="草稿不存在")
    if record == "TERMINAL":
        raise HTTPException(status_code=409, detail="草稿已终态（approved/rejected），不可再次审核")
    return PaperDraftOut(**record)


@router.post("/{draft_id}/approve", response_model=PaperDraftOut)
async def approve_paper_draft(draft_id: str, payload: ReviewRequest, request: Request) -> PaperDraftOut:
    """人工通过：pending_review -> approved；终态重复 409。"""
    record = await _repo(request).review(draft_id, "approved", payload.note)
    return _terminal_guard(record)


@router.post("/{draft_id}/reject", response_model=PaperDraftOut)
async def reject_paper_draft(draft_id: str, payload: ReviewRequest, request: Request) -> PaperDraftOut:
    """人工驳回：pending_review -> rejected；终态重复 409。"""
    record = await _repo(request).review(draft_id, "rejected", payload.note)
    return _terminal_guard(record)
