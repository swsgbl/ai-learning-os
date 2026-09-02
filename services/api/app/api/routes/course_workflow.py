"""M5-07 Course generation API：学习目标 -> 课程方案草稿 -> 人工审核队列。

- POST /api/v1/courses/generation-drafts：Goal -> Competency -> DAG -> Resource ->
  Outline -> Lessons -> Assessments -> Remediation（M5-07 验收链路）；
  DAG 未发布 409、目标无概念匹配 422（拒绝生成空课程，不虚报）；
- GET  ?status=：审核队列（缺省全部）；
- GET  /{id}：草稿详情（plan JSON 可审计）；
- POST /{id}/approve | /{id}/reject：人工决定；终态重复审核 409。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.api.routes.auth import audit_from_request, require_admin
from app.domain.course_workflow import (
    NoCompetencyMatch,
    generate_course_plan,
    match_competencies,
    prerequisite_closure,
)

router = APIRouter(prefix="/api/v1/courses/generation-drafts", tags=["courses"])


class GenerateRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=512)

    @field_validator("goal")
    @classmethod
    def _reject_blank_goal(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("学习目标不能为空白")
        return value.strip()


class AssessmentOut(BaseModel):
    chapter_no: int
    concept_id: str
    assessment_kind: str
    stem: str


class LessonOut(BaseModel):
    chapter_no: int
    objectives: str
    resources: list[dict]


class ChapterOut(BaseModel):
    chapter_no: int
    concept_id: str
    title: str
    difficulty: str


class RemediationOut(BaseModel):
    chapter_no: int
    concept_id: str
    review_chapters: list[int]


class GenerationDraftOut(BaseModel):
    id: str
    goal: str
    status: str
    dag_version: int
    chapter_count: int
    generation_note: str
    plan: dict
    review_note: str | None
    reviewed_at: str | None
    created_at: str


class ReviewRequest(BaseModel):
    note: str | None = Field(default=None, max_length=512)


def _repo(request: Request):
    repo = getattr(request.app.state, "course_generation_drafts", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Course generation requires a database")
    return repo


def _deps(request: Request):
    """生成依赖：DAG 仓储 + chunks 仓储（无 DB 时 app.state 上为 None）。"""
    dag_repo = getattr(request.app.state, "concept_dag", None)
    chunks = request.app.state.chunks
    if dag_repo is None or chunks is None:
        raise HTTPException(status_code=503, detail="Course generation requires a database")
    return dag_repo, chunks


@router.post("", response_model=GenerationDraftOut, status_code=201)
async def create_generation_draft(payload: GenerateRequest, request: Request) -> GenerationDraftOut:
    """八阶段管线生成课程方案草稿；DAG 未发布 409、无概念匹配 422（不虚报空课程）。"""
    dag_repo, chunks = _deps(request)
    dag = await dag_repo.latest()
    if dag is None:
        raise HTTPException(status_code=409, detail="概念 DAG 尚未发布，无法生成课程")
    # Resource 阶段：按闭包概念逐一检索本地语料，按 resource_id 去重后挂接
    matched = match_competencies(payload.goal, dag)
    closure = prerequisite_closure(matched, dag) if matched else []
    nodes_by_id = {node.id: node for node in dag.nodes}
    resources_by_concept: dict[str, list[dict]] = {}
    for concept_id in closure:
        rows = await chunks.search_text(nodes_by_id[concept_id].canonical_name, limit=5)
        items: list[dict] = []
        seen_rids: set[str] = set()
        for row in rows:
            rid = row["resource_id"]
            if rid in seen_rids:
                continue
            seen_rids.add(rid)
            items.append({
                "resource_id": rid,
                "title": f"{rid}#chunk-{row['chunk_index']}",
                "snippet": row["text"][:120],
            })
        resources_by_concept[concept_id] = items
    try:
        plan = generate_course_plan(payload.goal, dag, resources_by_concept)
    except NoCompetencyMatch as cause:
        raise HTTPException(status_code=422, detail=str(cause)) from cause
    record = await _repo(request).create({
        "goal": payload.goal,
        "dag_version": plan["dag_version"],
        "plan": plan,
        "chapter_count": len(plan["outline"]),
        "generation_note": plan["generation_note"],
    })
    return GenerationDraftOut(**record)


@router.get("", response_model=list[GenerationDraftOut])
async def list_generation_drafts(request: Request, status: str | None = None) -> list[GenerationDraftOut]:
    """审核队列：status 过滤（pending_review/approved/rejected），缺省全部。"""
    return [GenerationDraftOut(**item) for item in await _repo(request).list_by_status(status)]


@router.get("/{draft_id}", response_model=GenerationDraftOut)
async def get_generation_draft(draft_id: str, request: Request) -> GenerationDraftOut:
    record = await _repo(request).get(draft_id)
    if record is None:
        raise HTTPException(status_code=404, detail="课程生成草稿不存在")
    return GenerationDraftOut(**record)


def _terminal_guard(record: dict | str | None) -> GenerationDraftOut:
    """MISSING -> 404、TERMINAL -> 409、正常 dict -> 模型（approve/reject 复用）。"""
    if record == "MISSING":
        raise HTTPException(status_code=404, detail="课程生成草稿不存在")
    if record == "TERMINAL":
        raise HTTPException(status_code=409, detail="草稿已终态（approved/rejected），不可再次审核")
    return GenerationDraftOut(**record)


@router.post("/{draft_id}/approve", response_model=GenerationDraftOut)
async def approve_generation_draft(draft_id: str, payload: ReviewRequest, request: Request) -> GenerationDraftOut:
    """人工通过：pending_review -> approved；终态重复 409。"""
    await require_admin(request)  # M9-04: 草稿审核是全局治理动作
    record = await _repo(request).review(draft_id, "approved", payload.note)
    await audit_from_request(
        request, action="course_generation.approve", target_type="generation_draft",
        target_id=draft_id, before={"status": "pending_review"},
        after={"status": "approved"},
    )
    return _terminal_guard(record)


@router.post("/{draft_id}/reject", response_model=GenerationDraftOut)
async def reject_generation_draft(draft_id: str, payload: ReviewRequest, request: Request) -> GenerationDraftOut:
    """人工驳回：pending_review -> rejected；终态重复 409。"""
    await require_admin(request)  # M9-04: 草稿审核是全局治理动作
    record = await _repo(request).review(draft_id, "rejected", payload.note)
    await audit_from_request(
        request, action="course_generation.reject", target_type="generation_draft",
        target_id=draft_id, before={"status": "pending_review"},
        after={"status": "rejected"},
    )
    return _terminal_guard(record)
