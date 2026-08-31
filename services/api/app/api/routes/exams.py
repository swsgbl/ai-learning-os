from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from app.api.routes.papers import exam_out
from app.api.schemas import (
    AnglesOut,
    ConceptScoreOut,
    ExamSessionOut,
    GradedItemOut,
    MistakeEntryOut,
    RemediationTaskOut,
    ReportItemOut,
    ReportOut,
    ReviewQuestionOut,
    RubricCriterionOut,
    RubricOut,
    SaveAnswerRequest,
    SubmissionOut,
    SubmitRequest,
)
from app.domain.models import SubmissionRecord
from app.domain.report import build_report

router = APIRouter(prefix="/api/v1/exams", tags=["exams"])


async def require_exam(request: Request, exam_id: str):
    record = await request.app.state.repository.get_exam(exam_id)
    if not record:
        raise HTTPException(status_code=404, detail="考试不存在")
    paper = await request.app.state.repository.get_paper(record.paper_id)
    if not paper:
        raise HTTPException(status_code=500, detail="试卷数据缺失")
    return record, paper


@router.get("/{exam_id}", response_model=ExamSessionOut)
async def get_exam(exam_id: str, request: Request) -> ExamSessionOut:
    record, paper = await require_exam(request, exam_id)
    return exam_out(record, paper)


@router.put("/{exam_id}/answers", response_model=ExamSessionOut)
async def save_answer(exam_id: str, payload: SaveAnswerRequest, request: Request) -> ExamSessionOut:
    try:
        record = await request.app.state.repository.save_answer(
            exam_id,
            payload.sequence,
            payload.question_id,
            payload.answer,
        )
    except KeyError as cause:
        raise HTTPException(status_code=404, detail=str(cause)) from cause
    except ValueError as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    except PermissionError as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    _, paper = await require_exam(request, exam_id)
    return exam_out(record, paper)


@router.post("/{exam_id}/submit", response_model=SubmissionOut)
async def submit_exam(exam_id: str, _payload: SubmitRequest, request: Request) -> SubmissionOut:
    try:
        submission = await request.app.state.repository.submit(exam_id)
    except KeyError as cause:
        raise HTTPException(status_code=404, detail=str(cause)) from cause
    except ValueError as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    return await submission_out(submission, request)


@router.get("/{exam_id}/submission", response_model=SubmissionOut)
async def get_submission(exam_id: str, request: Request) -> SubmissionOut:
    submission = await request.app.state.repository.get_submission(exam_id)
    if not submission:
        raise HTTPException(status_code=404, detail="审阅报告尚未生成")
    return await submission_out(submission, request)


@router.get("/{exam_id}/report", response_model=ReportOut)
async def get_report(exam_id: str, request: Request) -> ReportOut:
    """M2-11 考试报告：总分、题分、概念分、错题、解析、补救任务和证据链接。"""
    submission = await request.app.state.repository.get_submission(exam_id)
    if not submission:
        raise HTTPException(status_code=404, detail="审阅报告尚未生成")
    paper = await request.app.state.repository.get_paper(submission.paper_id)
    if not paper:
        raise HTTPException(status_code=500, detail="试卷数据缺失")
    return _report_out(build_report(submission, paper))


def _report_out(report) -> ReportOut:
    return ReportOut(
        exam_id=report.exam_id,
        paper_title=report.paper_title,
        mode=report.mode,
        score=report.score,
        score_earned=report.score_earned,
        score_max=report.score_max,
        correct_count=report.correct_count,
        total_count=report.total_count,
        reviewed_count=report.reviewed_count,
        items=[
            ReportItemOut(
                question_id=item.question_id,
                sequence=item.sequence,
                question_type=item.question_type,
                stem=item.stem,
                given=item.given,
                expected=item.expected,
                correct=item.correct,
                score=item.score,
                max_score=item.max_score,
                explanation=item.explanation,
                knowledge=list(item.knowledge),
                evidence_ids=list(item.evidence_ids),
                score_ratio=item.score_ratio,
            )
            for item in report.items
        ],
        concepts=[
            ConceptScoreOut(
                concept=concept.concept,
                correct=concept.correct,
                total=concept.total,
                reviewed=concept.reviewed,
                ratio=concept.ratio,
            )
            for concept in report.concepts
        ],
        mistakes=[
            MistakeEntryOut(
                question_id=mistake.question_id,
                stem=mistake.stem,
                given=mistake.given,
                expected=mistake.expected,
                explanation=mistake.explanation,
                diagnosis=mistake.diagnosis,
                knowledge=list(mistake.knowledge),
                evidence_ids=list(mistake.evidence_ids),
                remediation_task_ids=list(mistake.remediation_task_ids),
            )
            for mistake in report.mistakes
        ],
        remediation_tasks=[
            RemediationTaskOut(
                kind=task.kind,
                title=task.title,
                detail=task.detail,
                question_id=task.question_id,
            )
            for task in report.remediation_tasks
        ],
        evidence_ids=list(report.evidence_ids),
    )


async def submission_out(submission: SubmissionRecord, request: Request) -> SubmissionOut:
    questions: list[ReviewQuestionOut] = []
    if paper := await request.app.state.repository.get_paper(submission.paper_id):
        questions = [
            ReviewQuestionOut(
                id=item.id,
                type=item.type,
                stem=item.stem,
                options=[{"key": option.key, "text": option.text} for option in item.options],
                answer=item.answer,
                explanation=item.explanation,
                angles=angles_out(item.angles),
                knowledge=list(item.knowledge),
            )
            for item in paper.questions
        ]
    return SubmissionOut(
        exam_id=submission.exam_id,
        paper_id=submission.paper_id,
        paper_title=submission.paper_title,
        mode=submission.mode,
        status=submission.status.value,
        score=submission.score,
        correct_count=submission.correct_count,
        total_count=submission.total_count,
        duration_seconds=submission.duration_seconds,
        rule_version=submission.rule_version,
        items=[
            GradedItemOut(
                question_id=item.question_id,
                given=item.given,
                correct=item.correct,
                expected=item.expected,
                explanation=item.explanation,
                angles=angles_out(item.angles),
                rubric=rubric_out(item.rubric_json),
            )
            for item in submission.items
        ],
        questions=questions,
    )


def angles_out(angles) -> AnglesOut:
    return AnglesOut(
        concept=angles.concept,
        method=angles.method,
        mistake=angles.mistake,
        variant=angles.variant,
    )


def rubric_out(rubric_json: str | None) -> RubricOut | None:
    """rubric 留痕 JSON -> API 结构；解析失败按无明细处理（不阻塞报告）。"""
    if not rubric_json:
        return None
    try:
        data = json.loads(rubric_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    criteria = data.get("criteria")
    if not isinstance(criteria, list):
        return None
    return RubricOut(
        rule_version=str(data.get("rule_version", "")),
        criteria=[RubricCriterionOut(**c) for c in criteria if isinstance(c, dict)],
        score_ratio=data.get("score_ratio"),
        confidence=float(data.get("confidence", 0.0)),
        judge_model=str(data.get("judge_model", "")),
        prompt_hash=str(data.get("prompt_hash", "")),
    )
