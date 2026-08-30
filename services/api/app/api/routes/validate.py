"""M2-01 schema validation endpoints (pure validation, no persistence)."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.domain.questions import PaperSpec, QuestionSpec

router = APIRouter(prefix="/api/v1/validate", tags=["validate"])


class QuestionValidationOut(BaseModel):
    valid: bool
    question_type: str
    estimated_seconds: int


class PaperValidationOut(BaseModel):
    valid: bool
    question_count: int
    computed_total_score: float
    duration_seconds: int


@router.post("/question", response_model=QuestionValidationOut)
async def validate_question(spec: QuestionSpec) -> QuestionValidationOut:
    """题型 schema 校验；失败由 pydantic -> 422。"""
    return QuestionValidationOut(
        valid=True,
        question_type=spec.question_type,
        estimated_seconds=spec.estimated_seconds,
    )


@router.post("/paper", response_model=PaperValidationOut)
async def validate_paper(spec: PaperSpec) -> PaperValidationOut:
    """试卷 schema 校验；总分不一致或题型非法 -> 422。"""
    return PaperValidationOut(
        valid=True,
        question_count=len(spec.questions),
        computed_total_score=sum(q.score for q in spec.questions),
        duration_seconds=spec.duration_seconds,
    )
