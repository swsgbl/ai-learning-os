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
    # M2-06：next_sequence 为服务端权威序号，断线恢复后客户端由此继续，不再自行计数
    next_sequence = record.events[-1].sequence + 1 if record.events else 1
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
        next_sequence=next_sequence,
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
    # M9-02: 认证开启时考试归属开启者；auth off 记 NULL（本地调试模式）
    owner = current_owner_id(request)
    record = await request.app.state.repository.create_exam(paper, payload.mode, owner)
    return exam_out(record, paper)


# ---------- M2-02 试卷 JSON 导入（逐行报错 + 落库不变） ----------
from fastapi import Body
from pydantic import BaseModel, ValidationError

from app.api.routes.auth import current_owner_id
from app.domain.questions import PaperSpec as PaperSpecModel
from app.repositories.paper_importer import import_papers as _run_import


class ImportResult(BaseModel):
    imported: list[str]


class LineError(BaseModel):
    index: int
    errors: list[dict]


@router.post("/papers/import", response_model=ImportResult, status_code=201)
async def import_papers_endpoint(request: Request, payload: list[dict] = Body(...)) -> ImportResult:
    """批量导入试卷 JSON：全部合法才落库；任一行失败则逐行报错且不写入。"""
    sessionmaker = getattr(request.app.state, "sessionmaker", None)
    if sessionmaker is None:
        raise HTTPException(status_code=503, detail="Paper import requires a database")
    specs: list[PaperSpecModel] = []
    line_errors: list[LineError] = []
    for index, raw in enumerate(payload):
        try:
            specs.append(PaperSpecModel.model_validate(raw))
        except ValidationError as cause:
            line_errors.append(
                LineError(
                    index=index,
                    errors=cause.errors(include_url=False, include_context=False),
                )
            )
    if line_errors:
        raise HTTPException(
            status_code=422,
            detail={"imported": [], "errors": [error.model_dump() for error in line_errors]},
        )
    return ImportResult(imported=await _run_import(sessionmaker, specs))
