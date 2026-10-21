"""M5-07 Course generation API：学习目标 -> 课程方案草稿 -> 人工审核队列。

- POST /api/v1/courses/generation-drafts：Goal -> Competency -> DAG -> Resource ->
  Outline -> Lessons -> Assessments -> Remediation（M5-07 验收链路）；
  DAG 未发布 409、目标无概念匹配 422（拒绝生成空课程，不虚报）；
- GET  ?status=：审核队列（缺省全部）；
- GET  /{id}：草稿详情（plan JSON 可审计）；
- POST /{id}/approve | /{id}/reject：人工决定；终态重复审核 409。
- POST /{id}/materialize（M15-001）：草稿 -> 调用者私有练习卷——学习者
  生成后即可开练（admin 或草稿创建者本人；不要求 approved：自用材料
  不等于全局课程库入库，治理路径 approve 保持 admin-only 不变）。
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
from app.db.orm import CourseGenerationDraftRow
from app.domain.course_workflow import (
    NoCompetencyMatch,
    generate_course_plan,
    match_competencies,
    prerequisite_closure,
)
from app.domain.llm_course_generator import (
    LlmCourseConfig,
    LlmCourseUnavailable,
    generate_course_plan_with_llm,
)
from app.domain.questions import (
    EssayAnswer,
    PaperQuestionRef,
    PaperSpec,
    QuestionSpec,
)
from app.repositories.paper_importer import import_papers as _run_import

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


async def _generate_with_llm_fallback(request: Request, goal: str) -> dict:
    """解析 LLM 来源（BYOK active > 全局）并动态生成课程 plan。"""
    from app.api.routes.me_ai_keys import resolve_user_llm_config
    from app.core.config import get_settings

    s = get_settings()
    endpoint, api_key, model = s.llm_endpoint, s.llm_api_key, s.llm_model
    personal = await resolve_user_llm_config(request.app, current_owner_id(request))
    if personal:
        endpoint = personal["endpoint"] or endpoint
        api_key = personal["api_key"]
        model = personal["model"] or model
    return generate_course_plan_with_llm(goal, LlmCourseConfig(endpoint, api_key, model))


async def _require_draft_readable(request: Request, draft_id: str) -> None:
    """M9-05 草稿读取门：auth on 时普通用户只能读自己的（他人/无主 404）。"""
    if await current_is_admin(request):
        return
    owner = current_owner_id(request)
    if owner is not None:
        draft_owner = await _repo(request).get_owner(draft_id)
        if draft_owner != owner:
            raise HTTPException(status_code=404, detail="课程生成草稿不存在")


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
        # M9-05: 语料边界 —— auth on 只检索自有+public 资源（他人私有绝不进入）
        owner = current_owner_id(request)
        rows = await chunks.search_text(
            nodes_by_id[concept_id].canonical_name, limit=5, owner=owner
        )
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
        # M15-019：概念图未覆盖该主题 → LLM 动态生成兜底（用户初衷：
        # 任意主题/学科/行业可定制）。LLM 来源：BYOK active profile 优先，
        # 回落服务端全局 LLM。LLM 不可用才 422（附概念图支持主题引导）。
        try:
            plan = await _generate_with_llm_fallback(request, payload.goal)
        except LlmCourseUnavailable as llm_cause:
            topics = "、".join(node.canonical_name for node in dag.nodes[:20])
            raise HTTPException(
                status_code=422,
                detail=f"{cause}；LLM 动态生成也未成功（{llm_cause}）。"
                f"当前已发布概念图支持的主题：{topics}",
            ) from llm_cause
    record = await _repo(request).create({
        "goal": payload.goal,
        "dag_version": plan["dag_version"],
        "plan": plan,
        "chapter_count": len(plan["outline"]),
        "generation_note": plan["generation_note"],
    }, owner_id=current_owner_id(request))  # M9-05: 草稿归属发起者
    return GenerationDraftOut(**record)


@router.get("", response_model=list[GenerationDraftOut])
async def list_generation_drafts(request: Request, status: str | None = None) -> list[GenerationDraftOut]:
    """审核队列；M9-05 auth on 时普通用户只见自己的草稿（admin/本地模式见全部）。"""
    owner = None if await current_is_admin(request) else current_owner_id(request)
    records = await _repo(request).list_by_status(status, owner)
    return [GenerationDraftOut(**r) for r in records]


@router.get("/{draft_id}", response_model=GenerationDraftOut)
async def get_generation_draft(draft_id: str, request: Request) -> GenerationDraftOut:
    await _require_draft_readable(request, draft_id)
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
    audit = await build_audit_payload(
        request, action="course_generation.approve", target_type="generation_draft",
        target_id=draft_id, before={"status": "pending_review"},
        after={"status": "approved"},
    )
    record = await _repo(request).review(draft_id, "approved", payload.note, audit)
    return _terminal_guard(record)


@router.post("/{draft_id}/reject", response_model=GenerationDraftOut)
async def reject_generation_draft(draft_id: str, payload: ReviewRequest, request: Request) -> GenerationDraftOut:
    """人工驳回：pending_review -> rejected；终态重复 409。"""
    await require_admin(request)  # M9-04: 草稿审核是全局治理动作
    audit = await build_audit_payload(
        request, action="course_generation.reject", target_type="generation_draft",
        target_id=draft_id, before={"status": "pending_review"},
        after={"status": "rejected"},
    )
    record = await _repo(request).review(draft_id, "rejected", payload.note, audit)
    return _terminal_guard(record)


class MaterializeOut(BaseModel):
    """M15-001：草稿 -> 私有练习卷结果。"""

    paper_id: str
    question_count: int
    title: str


@router.post("/{draft_id}/materialize", response_model=MaterializeOut, status_code=201)
async def materialize_generation_draft(draft_id: str, request: Request) -> MaterializeOut:
    """草稿 -> 调用者私有练习卷（学习者定制闭环的最后一公里）。

    权限：admin 或草稿创建者本人（auth off 本地模式草稿无主，放行）；
    不要求 approved——自用练习材料不是全局课程库入库（治理 approve
    保持 admin-only 语义零变化）。assessments 转 essay 模板题（开放
    作答 + rubric 要点），概念挂 chapter 对应 concept_id；卷归属
    current_owner_id（auth on 时仅本人可见）。
    """
    repo = _repo(request)
    draft = await repo.get(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="课程草稿不存在")
    caller = current_owner_id(request)
    if not await current_is_admin(request):
        draft_owner = await repo.get_owner(draft_id)
        if draft_owner is not None and draft_owner != caller:
            raise HTTPException(status_code=403, detail="仅草稿创建者或治理员可生成练习卷")
    plan = draft.get("plan") or {}
    assessments = plan.get("assessments") or []
    if not assessments:
        raise HTTPException(status_code=422, detail="课程草稿无评估题，无法组卷")
    outline = {c["chapter_no"]: c for c in plan.get("outline") or []}
    sessionmaker = getattr(request.app.state, "sessionmaker", None)
    if sessionmaker is None:
        raise HTTPException(status_code=503, detail="Course generation requires a database")
    questions: list[PaperQuestionRef] = []
    for item in assessments:
        chapter = outline.get(item.get("chapter_no"), {})
        questions.append(
            PaperQuestionRef(
                question=QuestionSpec(
                    question_type="essay",
                    stem=item.get("stem") or f"第 {item.get('chapter_no')} 章练习",
                    difficulty=int(chapter.get("difficulty", 3)),
                    estimated_seconds=300,
                    concept_ids=[item.get("concept_id", "")],
                    answer=EssayAnswer(
                        rubric_points=["说明该概念的核心含义", "给出一个具体例子"]
                    ),
                ),
                score=10.0,
            )
        )
    title = f"定制课程 · {(draft.get('goal') or '练习')[:40]}"
    spec = PaperSpec(
        title=title,
        duration_seconds=max(60, 300 * len(questions)),
        policy="practice",
        generation_method="ai_generated",
        questions=questions,
        total_score=float(10 * len(questions)),
    )
    imported = await _run_import(sessionmaker, [spec], owner_id=caller)
    if not imported:
        raise HTTPException(status_code=500, detail="练习卷写入失败")
    # M15-024：回写 materialized_paper_id（学习视图反查锚点）
    paper_id = imported[0].get("id") if isinstance(imported[0], dict) else getattr(imported[0], "id", None)
    if paper_id:
        from sqlalchemy import update as _update
        async with sessionmaker() as _session:
            await _session.execute(
                _update(CourseGenerationDraftRow)
                .where(CourseGenerationDraftRow.id == draft_id)
                .values(materialized_paper_id=paper_id)
            )
            await _session.commit()
    return MaterializeOut(
        paper_id=imported[0], question_count=len(questions), title=title
    )
