from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class StartExamRequest(BaseModel):
    mode: Literal["voice", "exam"] = "exam"


class SaveAnswerRequest(BaseModel):
    sequence: int = Field(gt=0)
    question_id: str
    answer: str


class SubmitRequest(BaseModel):
    pass


class OptionOut(BaseModel):
    key: str
    text: str


class AnglesOut(BaseModel):
    concept: str
    method: str
    mistake: str
    variant: str


class PaperOut(BaseModel):
    id: str
    title: str
    subtitle: str
    source: str
    university: str | None
    year: int | None
    subject: str
    difficulty: str
    duration_minutes: int
    tags: list[str]
    origin_url: str | None


class PublicQuestionOut(BaseModel):
    id: str
    type: str
    stem: str
    options: list[OptionOut]


class ReviewQuestionOut(PublicQuestionOut):
    answer: str
    explanation: str
    angles: AnglesOut
    knowledge: list[str]


class ExamSessionOut(BaseModel):
    exam_id: str
    paper_id: str
    paper_title: str
    mode: str
    status: str
    server_started_at: str
    server_end_at: str
    server_remaining_seconds: int
    questions: list[PublicQuestionOut]
    answers: dict[str, str]
    next_sequence: int


class RubricCriterionOut(BaseModel):
    point: str
    achieved: bool | None
    evidence_id: str | None = None


class RubricOut(BaseModel):
    """M2-10 essay 结构化判分明细（prompt pack E schema）。"""

    rule_version: str
    criteria: list[RubricCriterionOut]
    score_ratio: float | None = None
    confidence: float
    judge_model: str
    prompt_hash: str = ""


class GradedItemOut(BaseModel):
    question_id: str
    given: str
    correct: bool | None
    expected: str
    explanation: str
    angles: AnglesOut
    rubric: RubricOut | None = None


class SubmissionOut(BaseModel):
    exam_id: str
    paper_id: str
    paper_title: str
    mode: str
    status: str
    score: int
    correct_count: int
    total_count: int
    duration_seconds: int
    rule_version: str
    items: list[GradedItemOut]
    questions: list[ReviewQuestionOut]
