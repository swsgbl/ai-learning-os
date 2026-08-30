from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class ExamStatus(StrEnum):
    ACTIVE = "active"
    SUBMITTED = "submitted"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class Option:
    key: str
    text: str


@dataclass(frozen=True, slots=True)
class Angles:
    concept: str
    method: str
    mistake: str
    variant: str


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    type: str
    stem: str
    options: tuple[Option, ...]
    answer: str
    explanation: str
    angles: Angles
    knowledge: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Paper:
    id: str
    title: str
    subtitle: str
    source: str
    university: str | None
    year: int | None
    subject: str
    difficulty: str
    duration_minutes: int
    tags: tuple[str, ...]
    origin_url: str | None
    license: str
    questions: tuple[Question, ...]


@dataclass(frozen=True, slots=True)
class AnswerEvent:
    sequence: int
    question_id: str
    answer: str
    recorded_at: datetime


@dataclass(slots=True)
class ExamSessionRecord:
    exam_id: str
    paper_id: str
    paper_title: str
    mode: str
    status: ExamStatus
    started_at: datetime
    end_at: datetime
    answers: dict[str, str] = field(default_factory=dict)
    events: list[AnswerEvent] = field(default_factory=list)
    submitted_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class GradedItem:
    question_id: str
    given: str
    correct: bool
    expected: str
    explanation: str
    angles: Angles


@dataclass(frozen=True, slots=True)
class SubmissionRecord:
    exam_id: str
    paper_id: str
    paper_title: str
    mode: str
    status: ExamStatus
    score: int
    correct_count: int
    total_count: int
    duration_seconds: int
    items: tuple[GradedItem, ...]
