"""ORM models mirroring docs/delivery/04 数据模型与 API 契约.

约束对齐契约 §6 关键数据库约束：
- answer_events (exam_session_id, sequence) 唯一且单调；
- submissions.exam_session_id 唯一（重复提交幂等的数据库兜底）。
字段保持双方言（PostgreSQL / SQLite 测试替身）：不使用 JSONB、原生 UUID 等 PG 专属类型。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PaperRow(Base):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    subtitle: Mapped[str] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(256))
    university: Mapped[str | None] = mapped_column(String(128))
    year: Mapped[int | None] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(64))
    difficulty: Mapped[str] = mapped_column(String(32))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    origin_url: Mapped[str | None] = mapped_column(String(1024))
    license: Mapped[str] = mapped_column(String(64))


class QuestionRow(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id"), index=True)
    question_type: Mapped[str] = mapped_column(String(32))
    stem: Mapped[str] = mapped_column(String(4096))
    options: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # answer/explanation/angles 仅存服务端，任何 active 响应不得下发。
    answer: Mapped[str] = mapped_column(String(1024))
    explanation: Mapped[str] = mapped_column(String(4096))
    angles: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    knowledge: Mapped[list[Any]] = mapped_column(JSON, default=list)
    sort_order: Mapped[int] = mapped_column(Integer)


class ExamSessionRow(Base):
    __tablename__ = "exam_sessions"

    exam_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id"), index=True)
    paper_title: Mapped[str] = mapped_column(String(256))
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnswerEventRow(Base):
    __tablename__ = "answer_events"
    __table_args__ = (
        UniqueConstraint("exam_session_id", "sequence", name="uq_answer_events_exam_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_session_id: Mapped[str] = mapped_column(
        ForeignKey("exam_sessions.exam_id"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    question_id: Mapped[str] = mapped_column(String(64))
    answer: Mapped[str] = mapped_column(String(1024))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubmissionRow(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_session_id: Mapped[str] = mapped_column(
        ForeignKey("exam_sessions.exam_id"), unique=True, index=True
    )
    paper_id: Mapped[str] = mapped_column(String(64))
    paper_title: Mapped[str] = mapped_column(String(256))
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    score: Mapped[int] = mapped_column(Integer)
    correct_count: Mapped[int] = mapped_column(Integer)
    total_count: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[int] = mapped_column(Integer)
    # items 是已定稿的评分明细快照（含 expected/explanation），交卷后才可下发。
    items: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 判分规则版本，保证评分结果可追溯（docs/delivery M2-08 验收）。
    rule_version: Mapped[str] = mapped_column(String(32), default="objective-v1")


__all__ = [
    "AnswerEventRow",
    "ExamSessionRow",
    "PaperRow",
    "QuestionRow",
    "SubmissionRow",
]
