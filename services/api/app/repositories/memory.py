from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.domain.exam_fsm import assert_transition
from app.domain.grading import OBJECTIVE_RULE_VERSION
from app.domain.models import (
    AnswerEvent,
    ExamSessionRecord,
    ExamStatus,
    GradedItem,
    Paper,
    SubmissionRecord,
)
from app.domain.rubric_grader import RubricJudge, judge_question
from app.repositories.seed import seed_papers


def utc_now() -> datetime:
    return datetime.now(UTC)


def remaining_seconds(record: ExamSessionRecord, now: datetime | None = None) -> int:
    current = now or utc_now()
    return max(0, math.floor((record.end_at - current).total_seconds()))


class MemoryRepository:
    """API contract-first repository. PostgreSQL will replace this without route changes."""

    def __init__(
        self, clock: Callable[[], datetime] = utc_now, rubric_judge: RubricJudge | None = None
    ) -> None:
        self._papers = {paper.id: paper for paper in seed_papers()}
        # M10-03: 试卷归属（与 PostgresRepository 同可见性规则）；
        # seed 卷不在映射中 = 无主（系统公共卷），auth off 导入亦无主
        self._paper_owners: dict[str, str] = {}
        self._exams: dict[str, ExamSessionRecord] = {}
        self._exam_owners: dict[str, str] = {}
        self._submissions: dict[str, SubmissionRecord] = {}
        self._clock = clock
        self._rubric_judge = rubric_judge
        self._lock = asyncio.Lock()

    async def list_papers(self, owner_id: str | None = None) -> list[Paper]:
        # M10-03: owner_id 给定时 = 系统公共卷 + 自有卷；None = 不过滤（与 PG 语义一致）
        if owner_id is None:
            return list(self._papers.values())
        return [
            paper
            for paper in self._papers.values()
            if self._paper_owners.get(paper.id) in (None, owner_id)
        ]

    async def get_paper(self, paper_id: str, owner_id: str | None = None) -> Paper | None:
        paper = self._papers.get(paper_id)
        if paper is None:
            return None
        if owner_id is not None and self._paper_owners.get(paper_id) not in (None, owner_id):
            return None
        return paper

    def add_paper(self, paper: Paper, owner_id: str | None = None) -> None:
        """M10-03: 测试/嵌入用导入入口（内存模式无 /papers/import 端点，保持协议面最小）。"""
        self._papers[paper.id] = paper
        if owner_id is not None:
            self._paper_owners[paper.id] = owner_id

    async def create_exam(self, paper: Paper, mode: str, owner_id: str | None = None) -> ExamSessionRecord:
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
        if owner_id is not None:
            self._exam_owners[record.exam_id] = owner_id
        self._exams[record.exam_id] = record
        return record

    async def get_exam_owner(self, exam_id: str) -> str | None:
        return self._exam_owners.get(exam_id)

    async def get_exam(self, exam_id: str) -> ExamSessionRecord | None:
        record = self._exams.get(exam_id)
        if record and record.status == ExamStatus.ACTIVE and remaining_seconds(record, self._clock()) == 0:
            assert_transition(ExamStatus.ACTIVE, ExamStatus.EXPIRED)
            record.status = ExamStatus.EXPIRED
        return record

    async def save_answer(self, exam_id: str, sequence: int, question_id: str, answer: str) -> ExamSessionRecord:
        async with self._lock:
            record = await self._require_exam(exam_id)
            if record.status != ExamStatus.ACTIVE:
                raise PermissionError("考试已结束，不能再修改答案")
            if sequence < 1:
                raise ValueError("事件序号必须从 1 开始")
            if any(event.sequence == sequence for event in record.events):
                existing = next(event for event in record.events if event.sequence == sequence)
                if existing.question_id == question_id and existing.answer == answer:
                    return record
                raise ValueError("同一事件序号不能承载不同答案")
            expected = record.events[-1].sequence + 1 if record.events else 1
            if sequence != expected:
                raise ValueError("事件序号必须从 1 开始并递增")
            paper = self._papers[record.paper_id]
            if question_id not in {question.id for question in paper.questions}:
                raise KeyError("题目不存在")
            record.events.append(AnswerEvent(sequence, question_id, answer, self._clock()))
            record.answers[question_id] = answer
            return record

    async def submit(self, exam_id: str) -> SubmissionRecord:
        async with self._lock:
            record = await self._require_exam(exam_id)
            if existing := self._submissions.get(exam_id):
                return existing
            assert_transition(record.status, ExamStatus.SUBMITTED)
            now = self._clock()
            effective_end = min(now, record.end_at)
            record.status = ExamStatus.SUBMITTED
            record.submitted_at = now
            paper = self._papers[record.paper_id]
            items: list[GradedItem] = []
            rule_versions: set[str] = set()
            for question in paper.questions:
                given = record.answers.get(question.id, "")
                correct, rubric = judge_question(
                    question.type,
                    question.stem,
                    question.answer,
                    given,
                    rubric_judge=self._rubric_judge,
                    valid_evidence_ids=frozenset(),  # memory 无 evidence 基础设施
                )
                rule_versions.add(rubric.rule_version if rubric else OBJECTIVE_RULE_VERSION)
                items.append(
                    GradedItem(
                        question_id=question.id,
                        given=given,
                        correct=correct,
                        expected=question.answer,
                        explanation=question.explanation,
                        angles=question.angles,
                        rubric_json=rubric.criteria_json if rubric else None,
                    )
                )
            # 三态判分：None（待复核）不计入分子分母；total_count 仍为全量题数
            decided = [item for item in items if item.correct is not None]
            correct_count = sum(item.correct for item in decided)
            submission = SubmissionRecord(
                exam_id=record.exam_id,
                paper_id=record.paper_id,
                paper_title=record.paper_title,
                mode=record.mode,
                status=record.status,
                score=round(correct_count / len(decided) * 100) if decided else 0,
                correct_count=correct_count,
                total_count=len(items),
                duration_seconds=max(0, math.floor((effective_end - record.started_at).total_seconds())),
                items=tuple(items),
                rule_version="+".join(sorted(rule_versions)),
            )
            self._submissions[exam_id] = submission
            return submission

    async def get_submission(self, exam_id: str) -> SubmissionRecord | None:
        return self._submissions.get(exam_id)

    async def _require_exam(self, exam_id: str) -> ExamSessionRecord:
        record = self._exams.get(exam_id)
        if not record:
            raise KeyError("考试不存在")
        return record
