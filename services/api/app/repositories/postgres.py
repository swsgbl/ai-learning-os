"""PostgreSQL repository implementing the same Repository protocol as MemoryRepository.

关键不变量（与 docs/delivery/04 契约一致）：
- exam 时间由服务端时钟写入；
- answer_events (exam_session_id, sequence) 唯一、递增、append-only；
- submissions.exam_session_id 唯一，重复提交幂等返回同一结果；
- 超时提交以 min(now, end_at) 计算实际用时，客户端无法延长考试。
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.orm import (
    AnswerEventRow,
    ExamSessionRow,
    PaperRow,
    QuestionRow,
    SubmissionRow,
)
from app.domain.exam_fsm import assert_transition
from app.domain.grading import grade_answer
from app.domain.models import (
    Angles,
    AnswerEvent,
    ExamSessionRecord,
    ExamStatus,
    GradedItem,
    Option,
    Paper,
    Question,
    SubmissionRecord,
)
from app.repositories.memory import remaining_seconds, utc_now
from app.repositories.seed import seed_papers

RULE_VERSION = "objective-v1"


def _to_db(value: datetime) -> datetime:
    """写库前统一转 aware UTC。asyncpg 正确编码 aware 值（naive 会被按本机时区解释）；
    SQLite 存其 UTC 挂钟字符串，读回 naive 后由 _from_db 补 UTC。"""
    return value.astimezone(UTC)


def _from_db(value: datetime) -> datetime:
    """读回统一转 UTC-aware。SQLite 返回 naive；asyncpg 返回本机时区 aware，必须 astimezone 而非 replace。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _answers_from(events: Iterable[AnswerEventRow]) -> dict[str, str]:
    return {event.question_id: event.answer for event in events}


def _event_records(events: Iterable[AnswerEventRow]) -> list[AnswerEvent]:
    return [
        AnswerEvent(
            sequence=event.sequence,
            question_id=event.question_id,
            answer=event.answer,
            recorded_at=_from_db(event.occurred_at) if event.occurred_at else utc_now(),
        )
        for event in events
    ]


def _exam_record(
    row: ExamSessionRow, events: Sequence[AnswerEventRow], answers: dict[str, str]
) -> ExamSessionRecord:
    return ExamSessionRecord(
        exam_id=row.exam_id,
        paper_id=row.paper_id,
        paper_title=row.paper_title,
        mode=row.mode,
        status=ExamStatus(row.status),
        started_at=_from_db(row.started_at),
        end_at=_from_db(row.end_at),
        answers=answers,
        events=_event_records(events),
        submitted_at=_from_db(row.submitted_at) if row.submitted_at else None,
    )


class PostgresRepository:
    """SQLAlchemy async 实现。接口签名与 MemoryRepository 完全一致，API route 零改动。"""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def seed_papers_if_empty(self) -> None:
        async with self._sessionmaker() as session, session.begin():
            count = (await session.execute(select(func.count()).select_from(PaperRow))).scalar_one()
            if count:
                return
            for paper in seed_papers():
                session.add(self._paper_row(paper))
                for sort_order, question in enumerate(paper.questions):
                    session.add(self._question_row(paper.id, question, sort_order))

    async def list_papers(self) -> list[Paper]:
        async with self._sessionmaker() as session:
            rows = (await session.execute(select(PaperRow).order_by(PaperRow.id))).scalars().all()
            return [await self._paper_record(session, row) for row in rows]

    async def get_paper(self, paper_id: str) -> Paper | None:
        async with self._sessionmaker() as session:
            row = await session.get(PaperRow, paper_id)
            if not row:
                return None
            return await self._paper_record(session, row)

    async def create_exam(self, paper: Paper, mode: str) -> ExamSessionRecord:
        now = self._clock()
        record = ExamSessionRecord(
            exam_id=f"exam_{uuid4().hex}",
            paper_id=paper.id,
            paper_title=paper.title,
            mode=mode,
            status=ExamStatus.CREATED,
            started_at=now,
            end_at=now + timedelta(minutes=paper.duration_minutes),
        )
        assert_transition(ExamStatus.CREATED, ExamStatus.ACTIVE)
        record = replace(record, status=ExamStatus.ACTIVE)
        async with self._sessionmaker() as session, session.begin():
            session.add(
                ExamSessionRow(
                    exam_id=record.exam_id,
                    paper_id=record.paper_id,
                    paper_title=record.paper_title,
                    mode=record.mode,
                    status=record.status.value,
                    started_at=_to_db(record.started_at),
                    end_at=_to_db(record.end_at),
                )
            )
        return record

    async def get_exam(self, exam_id: str) -> ExamSessionRecord | None:
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(ExamSessionRow, exam_id)
            if not row:
                return None
            if (
                row.status == ExamStatus.ACTIVE.value
                and remaining_seconds(_exam_record(row, [], {}), self._clock()) == 0
            ):
                assert_transition(ExamStatus.ACTIVE, ExamStatus.EXPIRED)
                row.status = ExamStatus.EXPIRED.value
            events = await self._events(session, exam_id)
            return _exam_record(row, events, _answers_from(events))

    async def save_answer(
        self, exam_id: str, sequence: int, question_id: str, answer: str
    ) -> ExamSessionRecord:
        async with self._sessionmaker() as session, session.begin():
            exam = await session.get(ExamSessionRow, exam_id)
            if not exam:
                raise KeyError("考试不存在")
            if exam.status != ExamStatus.ACTIVE.value:
                raise PermissionError("考试已结束，不能再修改答案")
            if sequence < 1:
                raise ValueError("事件序号必须从 1 开始")
            events = await self._events(session, exam_id)
            duplicate = next((event for event in events if event.sequence == sequence), None)
            if duplicate:
                if duplicate.question_id == question_id and duplicate.answer == answer:
                    return _exam_record(exam, events, _answers_from(events))
                raise ValueError("同一事件序号不能承载不同答案")
            expected = events[-1].sequence + 1 if events else 1
            if sequence != expected:
                raise ValueError("事件序号必须从 1 开始并递增")
            await self._require_question_of_paper(session, exam.paper_id, question_id)
            session.add(
                AnswerEventRow(
                    exam_session_id=exam_id,
                    sequence=sequence,
                    question_id=question_id,
                    answer=answer,
                    occurred_at=_to_db(self._clock()),
                )
            )
            record = _exam_record(exam, events, _answers_from(events))
            record.events.append(
                AnswerEvent(sequence, question_id, answer, self._clock())
            )
            record.answers[question_id] = answer
            return record

    async def submit(self, exam_id: str) -> SubmissionRecord:
        try:
            return await self._submit_once(exam_id)
        except IntegrityError:
            # 并发提交撞 submissions.exam_session_id 唯一约束：幂等返回既有结果。
            existing = await self.get_submission(exam_id)
            if existing is None:
                raise
            return existing

    async def get_submission(self, exam_id: str) -> SubmissionRecord | None:
        async with self._sessionmaker() as session:
            row = (
                await session.execute(
                    select(SubmissionRow).where(SubmissionRow.exam_session_id == exam_id)
                )
            ).scalar_one_or_none()
            return self._submission_record(row) if row else None

    # ------------------------------------------------------------------ helpers

    async def _submit_once(self, exam_id: str) -> SubmissionRecord:
        async with self._sessionmaker() as session, session.begin():
            existing = (
                await session.execute(
                    select(SubmissionRow).where(SubmissionRow.exam_session_id == exam_id)
                )
            ).scalar_one_or_none()
            if existing:
                return self._submission_record(existing)
            exam = await session.get(ExamSessionRow, exam_id)
            if not exam:
                raise KeyError("考试不存在")
            assert_transition(ExamStatus(exam.status), ExamStatus.SUBMITTED)
            now = self._clock()
            questions = (
                (
                    await session.execute(
                        select(QuestionRow)
                        .where(QuestionRow.paper_id == exam.paper_id)
                        .order_by(QuestionRow.sort_order)
                    )
                )
                .scalars()
                .all()
            )
            answers = _answers_from(await self._events(session, exam_id))
            submission = self._build_submission(exam, questions, answers, now)
            exam.status = ExamStatus.SUBMITTED.value
            exam.submitted_at = _to_db(now)
            session.add(submission)
            return self._submission_record(submission)

    def _build_submission(
        self,
        exam: ExamSessionRow,
        questions: Sequence[QuestionRow],
        answers: dict[str, str],
        now: datetime,
    ) -> SubmissionRow:
        items = tuple(
            GradedItem(
                question_id=question.id,
                given=answers.get(question.id, ""),
                correct=grade_answer(
                    question.question_type, question.answer, answers.get(question.id, "")
                ),
                expected=question.answer,
                explanation=question.explanation,
                angles=Angles(**question.angles),
            )
            for question in questions
        )
        correct_count = sum(item.correct for item in items)
        effective_end = min(now, _from_db(exam.end_at))
        return SubmissionRow(
            exam_session_id=exam.exam_id,
            paper_id=exam.paper_id,
            paper_title=exam.paper_title,
            mode=exam.mode,
            status=ExamStatus.SUBMITTED.value,
            score=round(correct_count / len(items) * 100) if items else 0,
            correct_count=correct_count,
            total_count=len(items),
            duration_seconds=max(
                0, math.floor((effective_end - _from_db(exam.started_at)).total_seconds())
            ),
            items=[self._item_json(item) for item in items],
            created_at=_to_db(now),
            rule_version=RULE_VERSION,
        )

    @staticmethod
    def _item_json(item: GradedItem) -> dict:
        return {
            "question_id": item.question_id,
            "given": item.given,
            "correct": item.correct,
            "expected": item.expected,
            "explanation": item.explanation,
            "angles": {
                "concept": item.angles.concept,
                "method": item.angles.method,
                "mistake": item.angles.mistake,
                "variant": item.angles.variant,
            },
        }

    @staticmethod
    def _submission_record(row: SubmissionRow) -> SubmissionRecord:
        items = tuple(
            GradedItem(
                question_id=item["question_id"],
                given=item["given"],
                correct=item["correct"],
                expected=item["expected"],
                explanation=item["explanation"],
                angles=Angles(**item["angles"]),
            )
            for item in row.items
        )
        return SubmissionRecord(
            exam_id=row.exam_session_id,
            paper_id=row.paper_id,
            paper_title=row.paper_title,
            mode=row.mode,
            status=ExamStatus(row.status),
            score=row.score,
            correct_count=row.correct_count,
            total_count=row.total_count,
            duration_seconds=row.duration_seconds,
            items=items,
        )

    @staticmethod
    async def _events(session: AsyncSession, exam_id: str) -> list[AnswerEventRow]:
        result = await session.execute(
            select(AnswerEventRow)
            .where(AnswerEventRow.exam_session_id == exam_id)
            .order_by(AnswerEventRow.sequence)
        )
        return list(result.scalars().all())

    @staticmethod
    async def _require_question_of_paper(
        session: AsyncSession, paper_id: str, question_id: str
    ) -> None:
        found = (
            await session.execute(
                select(QuestionRow.id).where(
                    QuestionRow.paper_id == paper_id, QuestionRow.id == question_id
                )
            )
        ).scalar_one_or_none()
        if not found:
            raise KeyError("题目不存在")

    @staticmethod
    def _question_row(paper_id: str, question: Question, sort_order: int) -> QuestionRow:
        return QuestionRow(
            id=question.id,
            paper_id=paper_id,
            question_type=question.type,
            stem=question.stem,
            options=[{"key": option.key, "text": option.text} for option in question.options],
            answer=question.answer,
            explanation=question.explanation,
            angles={
                "concept": question.angles.concept,
                "method": question.angles.method,
                "mistake": question.angles.mistake,
                "variant": question.angles.variant,
            },
            knowledge=list(question.knowledge),
            sort_order=sort_order,
        )

    @staticmethod
    def _paper_row(paper: Paper) -> PaperRow:
        return PaperRow(
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
            license=paper.license,
        )

    @staticmethod
    async def _paper_record(session: AsyncSession, row: PaperRow) -> Paper:
        questions = (
            (
                await session.execute(
                    select(QuestionRow)
                    .where(QuestionRow.paper_id == row.id)
                    .order_by(QuestionRow.sort_order)
                )
            )
            .scalars()
            .all()
        )
        return Paper(
            id=row.id,
            title=row.title,
            subtitle=row.subtitle,
            source=row.source,
            university=row.university,
            year=row.year,
            subject=row.subject,
            difficulty=row.difficulty,
            duration_minutes=row.duration_minutes,
            tags=tuple(row.tags),
            origin_url=row.origin_url,
            license=row.license,
            questions=tuple(
                Question(
                    id=question.id,
                    type=question.question_type,
                    stem=question.stem,
                    options=tuple(
                        Option(key=option["key"], text=option["text"]) for option in question.options
                    ),
                    answer=question.answer,
                    explanation=question.explanation,
                    angles=Angles(**question.angles),
                    knowledge=tuple(question.knowledge),
                )
                for question in questions
            ),
        )
