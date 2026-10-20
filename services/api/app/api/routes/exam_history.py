"""M15-023 我的考试历史（两端回看审阅报告的公共底座）。

- GET /api/v1/exams/history：当前用户的考试列表（含 submission 摘要——
  分数/对错计数/提交时间；未交卷只列会话不列分）。回看报告经既有
  /exams/{id}/submission + /report（权限校验复用 require_exam 归属门）。
- 归属语义与 require_exam 同款：auth on 严格 owner 过滤；本地模式
  （无 AUTH_SECRET）无主可见（与既有考试读写一致）。
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.api.routes.auth import current_owner_id
from app.db.orm import ExamSessionRow, SubmissionRow

router = APIRouter(prefix="/api/v1/exams", tags=["exams"])


class ExamHistoryItem(BaseModel):
    exam_id: str
    paper_id: str
    paper_title: str
    mode: str
    status: str
    started_at: str
    submitted_at: str | None = None
    score: int | None = None
    correct_count: int | None = None
    question_count: int | None = None
    has_report: bool = False


class ExamHistoryOut(BaseModel):
    items: list[ExamHistoryItem]


@router.get("/history", response_model=ExamHistoryOut)
async def my_exam_history(request: Request) -> ExamHistoryOut:

    owner = current_owner_id(request)
    async with request.app.state.sessionmaker() as session:
        query = select(ExamSessionRow).order_by(ExamSessionRow.started_at.desc()).limit(100)
        if owner is not None:
            query = query.where(ExamSessionRow.owner_id == owner)
        rows = (await session.execute(query)).scalars().all()
        subs = {
            s.exam_session_id: s
            for s in (
                await session.execute(
                    select(SubmissionRow).where(
                        SubmissionRow.exam_session_id.in_([r.exam_id for r in rows])
                    )
                )
            ).scalars()
        }
        items = []
        for r in rows:
            s = subs.get(r.exam_id)
            items.append(
                ExamHistoryItem(
                    exam_id=r.exam_id,
                    paper_id=r.paper_id,
                    paper_title=r.paper_title,
                    mode=r.mode,
                    status=r.status,
                    started_at=r.started_at.isoformat(),
                    submitted_at=r.submitted_at.isoformat() if r.submitted_at else None,
                    score=s.score if s else None,
                    correct_count=s.correct_count if s else None,
                    question_count=s.total_count if s else None,
                    has_report=s is not None,
                )
            )
        return ExamHistoryOut(items=items)
