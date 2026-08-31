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


class ReportItemOut(BaseModel):
    question_id: str
    sequence: int
    question_type: str
    stem: str
    given: str
    expected: str
    correct: bool | None
    score: float | None
    max_score: float
    explanation: str
    knowledge: list[str]
    evidence_ids: list[str]
    score_ratio: float | None = None


class ConceptScoreOut(BaseModel):
    concept: str
    correct: int
    total: int
    reviewed: int
    ratio: float | None = None


class RemediationTaskOut(BaseModel):
    kind: str
    title: str
    detail: str
    question_id: str


class MistakeEntryOut(BaseModel):
    question_id: str
    stem: str
    given: str
    expected: str
    explanation: str
    diagnosis: str
    knowledge: list[str]
    evidence_ids: list[str]
    remediation_task_ids: list[int]


class ReportOut(BaseModel):
    """M2-11 考试报告：总分/题分/概念分/错题/解析/补救任务/证据链接。"""

    exam_id: str
    paper_title: str
    mode: str
    score: int
    score_earned: float
    score_max: float
    correct_count: int
    total_count: int
    reviewed_count: int
    items: list[ReportItemOut]
    concepts: list[ConceptScoreOut]
    mistakes: list[MistakeEntryOut]
    remediation_tasks: list[RemediationTaskOut]
    evidence_ids: list[str]


class LearningEventOut(BaseModel):
    """M3-01 标准化学习事件：answer_events 的确定性投影（04 号文档 §2.14）。"""

    sequence: int
    event_type: str
    question_id: str
    concept_ids: list[str]
    difficulty: int
    answer: str
    correctness: bool | None
    latency_ms: int
    attempt_number: int
    hint_used: bool
    occurred_at: str
    user_id: str | None = None


class LearningEventStreamOut(BaseModel):
    exam_id: str
    paper_id: str
    events: list[LearningEventOut]


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
