"""M15-024 学习模式（先学习再考试）：章节学习材料 + 逐章练习。

- GET /api/v1/papers/{paper_id}/study：该卷的学习视图——按章组织
  （标题/学习目标/概念解释/该章练习题），来自课程 plan 的 lessons +
  outline + assessments（materialize 时固化进 paper 关联的课程草稿）。
- 学习流：读章节目标与解释 → 练该章的题（逐题提交即时判分）→ 下一章
  → 全部完成后可整卷模拟考。此前只有"直接考试"无学习阶段（用户核心
  反馈：学习系统应先学习再考试）。
- 非 materialize 卷（无课程来源）404 如实。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.api.routes.auth import current_owner_id, require_user
from app.db.orm import CourseGenerationDraftRow, PaperRow, QuestionRow

router = APIRouter(prefix="/api/v1/papers", tags=["papers"])


class StudyQuestionOut(BaseModel):
    question_id: str
    stem: str
    question_type: str
    options: dict | None = None


class StudyChapterOut(BaseModel):
    chapter_no: int
    title: str
    difficulty: int
    objectives: str
    assessment_stem: str
    questions: list[StudyQuestionOut]


class StudyViewOut(BaseModel):
    paper_id: str
    paper_title: str
    goal: str
    chapter_count: int
    chapters: list[StudyChapterOut]


def _build_study_view(paper: PaperRow, questions: list[QuestionRow], draft: CourseGenerationDraftRow) -> StudyViewOut:
    plan = draft.plan if isinstance(draft.plan, dict) else {}
    outline = plan.get("outline", [])
    lessons = plan.get("lessons", [])
    assessments = plan.get("assessments", [])
    # materialize 的题目按章概念归属（LLM 动态课程 concept_id=llm:N）
    by_concept: dict[str, list[QuestionRow]] = {}
    for q in questions:
        # QuestionRow 用 knowledge（JSON list）存概念 id 列表
        for cid in (q.knowledge if isinstance(q.knowledge, list) else []):
            if isinstance(cid, str):
                by_concept.setdefault(cid, []).append(q)
    chapters = []
    for i, ch in enumerate(outline):
        lesson = lessons[i] if i < len(lessons) else {}
        assessment = assessments[i] if i < len(assessments) else {}
        concept_id = ch.get("concept_id", f"llm:{i+1}")
        ch_questions = by_concept.get(concept_id, [])
        # 概念未命中题目时按序号兜底（单题/章的 LLM 课程常见）
        if not ch_questions:
            ch_questions = [q for q in questions if q.sequence == i + 1]
        chapters.append(
            StudyChapterOut(
                chapter_no=ch.get("chapter_no", i + 1),
                title=ch.get("title", f"第{i+1}章"),
                difficulty=int(ch.get("difficulty", 3)),
                objectives=lesson.get("objectives", ""),
                assessment_stem=assessment.get("stem", ""),
                questions=[
                    StudyQuestionOut(
                        question_id=q.id,
                        stem=q.stem,
                        question_type=q.question_type,
                        options=q.options if isinstance(q.options, dict) else None,
                    )
                    for q in ch_questions
                ],
            )
        )
    return StudyViewOut(
        paper_id=paper.id,
        paper_title=paper.title,
        goal=plan.get("goal", draft.goal),
        chapter_count=len(chapters),
        chapters=chapters,
    )


@router.get("/{paper_id}/study", response_model=StudyViewOut)
async def paper_study_view(request: Request, paper_id: str) -> StudyViewOut:
    """学习视图（章节目标+解释+逐章练习题）——材料卷（materialize）专属。"""
    await require_user(request)
    owner = current_owner_id(request)
    async with request.app.state.sessionmaker() as session:
        paper = (
            await session.execute(select(PaperRow).where(PaperRow.id == paper_id))
        ).scalar_one_or_none()
        if paper is None:
            raise HTTPException(status_code=404, detail="试卷不存在")
        if owner is not None and paper.owner_id not in (None, owner):
            raise HTTPException(status_code=404, detail="试卷不存在")
        questions = (
            (
                await session.execute(
                    select(QuestionRow)
                    .where(QuestionRow.paper_id == paper_id)
                    .order_by(QuestionRow.sort_order)
                )
            )
            .scalars()
            .all()
        )
        # M15-024：经 materialized_paper_id 反查课程草稿（plan 含学习材料）
        draft = (
            await session.execute(
                select(CourseGenerationDraftRow)
                .where(CourseGenerationDraftRow.materialized_paper_id == paper_id)
                .order_by(CourseGenerationDraftRow.created_at.desc())
            )
        ).scalar_one_or_none()
        if draft is None:
            # 存量兜底：owner + 标题前缀匹配（0032 迁移已回填，此路径仅防边缘）
            draft_rows = (
                (
                    await session.execute(
                        select(CourseGenerationDraftRow)
                        .where(
                            CourseGenerationDraftRow.owner_id == paper.owner_id,
                            CourseGenerationDraftRow.goal.isnot(None),
                        )
                        .order_by(CourseGenerationDraftRow.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            draft = next(
                (d for d in draft_rows if paper.title == f"定制课程 · {(d.goal or '')[:40]}"), None
            )
        if draft is None or not draft.plan:
            raise HTTPException(status_code=404, detail="该试卷无课程来源（非定制课程，无学习视图）")
        return _build_study_view(paper, list(questions), draft)
