from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.schemas import (
    ExamSessionOut,
    PaperOut,
    PublicQuestionOut,
    StartExamRequest,
)
from app.domain.models import ExamSessionRecord, Paper
from app.repositories.memory import remaining_seconds

router = APIRouter(prefix="/api/v1", tags=["papers"])


def paper_out(paper: Paper) -> PaperOut:
    return PaperOut(
        id=paper.id,
        title=paper.title,
        subtitle=paper.subtitle,
        source=paper.source,
        university=paper.university,
        year=paper.year,
        subject=paper.subject,
        difficulty=paper.difficulty,
        duration_minutes=paper.duration_minutes,
        tags=list(paper.tags),
        origin_url=paper.origin_url,
    )


def exam_out(record: ExamSessionRecord, paper: Paper) -> ExamSessionOut:
    return ExamSessionOut(
        exam_id=record.exam_id,
        paper_id=record.paper_id,
        paper_title=record.paper_title,
        mode=record.mode,
        status=record.status.value,
        server_started_at=record.started_at.isoformat(),
        server_end_at=record.end_at.isoformat(),
        server_remaining_seconds=remaining_seconds(record),
        questions=[
            PublicQuestionOut(
                id=item.id,
                type=item.type,
                stem=item.stem,
                options=[{"key": option.key, "text": option.text} for option in item.options],
            )
            for item in paper.questions
        ],
        answers=record.answers,
    )


@router.get("/papers", response_model=list[PaperOut])
async def list_papers(request: Request) -> list[PaperOut]:
    papers = await request.app.state.repository.list_papers()
    return [paper_out(paper) for paper in papers]


@router.post("/papers/{paper_id}/exams", response_model=ExamSessionOut, status_code=201)
async def start_exam(paper_id: str, payload: StartExamRequest, request: Request) -> ExamSessionOut:
    paper = await request.app.state.repository.get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail="试卷不存在")
    record = await request.app.state.repository.create_exam(paper, payload.mode)
    return exam_out(record, paper)
