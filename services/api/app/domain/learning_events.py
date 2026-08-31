"""M3-01 LearningEvent 标准化：答案事件投影为标准学习事件流（04 号文档 §2.14）。

- 事实源仍是 append-only 的答案事件（ADR 3）；LearningEvent 是确定性投影，
  可随时从事件 + 试卷元数据重算（pack F「更新幂等、可从事件重算」的基础）。
- 每个事件携带：作答、耗时（与前一事件的间隔，首题为与考试开始的间隔）、
  提示（当前无提示功能，预留恒 False）、题目难度（question.difficulty 1-5，
  导入卷自 QuestionSpec 透传，seed 卷默认 3）、概念映射（question.knowledge）
  与 attempt 序号（同一题第 N 次作答，覆盖作答 = 新 attempt）。
- correctness 复用与交卷判分同一条 judge_question 管线（objective + rubric），
  保证重放判分与报告一致；None=复核沿用 ADR 21 三态语义。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from app.domain.models import ExamSessionRecord, Paper
from app.domain.rubric_grader import RubricJudge, judge_question

ANSWER_EVENT_TYPE: Final = "answer"


@dataclass(frozen=True, slots=True)
class LearningEvent:
    """标准化学习事件：M3-03 StudentConceptState 重算的输入。"""

    sequence: int
    event_type: str
    question_id: str
    concept_ids: tuple[str, ...]
    difficulty: int  # 题目级难度 1-5
    answer: str
    correctness: bool | None
    latency_ms: int
    attempt_number: int
    hint_used: bool
    occurred_at: datetime
    user_id: str | None = None  # MVP 单用户；认证引入后填真实身份


@dataclass(frozen=True, slots=True)
class LearningEventStream:
    exam_id: str
    paper_id: str
    events: tuple[LearningEvent, ...]


def derive_learning_events(
    record: ExamSessionRecord, paper: Paper, *, rubric_judge: RubricJudge | None = None
) -> LearningEventStream:
    """从考试事件流 + 试卷元数据确定性投影学习事件（纯函数，可重放）。"""
    questions = {question.id: question for question in paper.questions}
    attempts: dict[str, int] = {}
    events: list[LearningEvent] = []
    previous_at: datetime = record.started_at
    for event in record.events:
        question = questions.get(event.question_id)
        attempt = attempts.get(event.question_id, 0) + 1
        attempts[event.question_id] = attempt
        if question is not None:
            correct, _ = judge_question(
                question.type,
                question.stem,
                question.answer,
                event.answer,
                rubric_judge=rubric_judge,
            )
        else:
            correct = None  # 试卷中找不到题目：不虚构判定，交由复核
        latency_ms = max(0, round((event.recorded_at - previous_at).total_seconds() * 1000))
        previous_at = event.recorded_at
        events.append(
            LearningEvent(
                sequence=event.sequence,
                event_type=ANSWER_EVENT_TYPE,
                question_id=event.question_id,
                concept_ids=tuple(question.knowledge) if question else (),
                difficulty=question.difficulty if question else 3,
                answer=event.answer,
                correctness=correct,
                latency_ms=latency_ms,
                attempt_number=attempt,
                hint_used=False,
                occurred_at=event.recorded_at,
            )
        )
    return LearningEventStream(exam_id=record.exam_id, paper_id=record.paper_id, events=tuple(events))
