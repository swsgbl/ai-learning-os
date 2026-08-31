"""M3-01 LearningEvent 标准化：答案、耗时、提示、题目难度、概念映射和 attempt 可重放。

derive_learning_events 是确定性纯函数：同一事件流两次投影结果全等。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.domain.learning_events import ANSWER_EVENT_TYPE, derive_learning_events
from app.domain.models import (
    Angles,
    AnswerEvent,
    ExamSessionRecord,
    ExamStatus,
    Option,
    Paper,
    Question,
)
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
T0 = datetime(2026, 8, 31, 10, 0, 0, tzinfo=UTC)

PAPER = Paper(
    id="p1",
    title="学习事件验证卷",
    subtitle="",
    source="M3-01",
    university=None,
    year=None,
    subject="computer science",
    difficulty="core",
    duration_minutes=10,
    tags=(),
    origin_url=None,
    license="",
    questions=(
        Question(
            id="q_mcq",
            type="mcq",
            stem="1+1=?",
            options=(Option("A", "1"), Option("B", "2")),
            answer='{"option_index": 1}',
            explanation="加法",
            angles=Angles("arithmetic", "", "", ""),
            knowledge=("arithmetic",),
            difficulty=2,
        ),
        Question(
            id="q_numeric",
            type="numeric",
            stem="圆周率",
            options=(),
            answer='{"value": 3.14, "tolerance": 0.01}',
            explanation="pi",
            angles=Angles("geometry", "", "", ""),
            knowledge=("geometry",),
            difficulty=4,
        ),
        Question(
            id="q_essay",
            type="essay",
            stem="论述快排",
            options=(),
            answer='{"rubric_points": ["分治", "递归"]}',
            explanation="分治 + 递归",
            angles=Angles("sorting", "", "", ""),
            knowledge=("sorting",),
        ),  # 省略 difficulty → 默认 3
    ),
)


def _record(events: list[AnswerEvent], *, status: ExamStatus = ExamStatus.ACTIVE) -> ExamSessionRecord:
    answers = {event.question_id: event.answer for event in events}
    return ExamSessionRecord(
        exam_id="exam_le1",
        paper_id="p1",
        paper_title=PAPER.title,
        mode="exam",
        status=status,
        started_at=T0,
        end_at=T0 + timedelta(minutes=10),
        answers=answers,
        events=events,
    )


def test_derive_is_deterministic_and_replayable() -> None:
    """重放验收：同一事件流投影两次，结果逐字段全等。"""
    record = _record(
        [
            AnswerEvent(1, "q_mcq", "B", T0 + timedelta(seconds=8)),
            AnswerEvent(2, "q_numeric", "3.2", T0 + timedelta(seconds=35)),
        ]
    )
    first = derive_learning_events(record, PAPER)
    second = derive_learning_events(record, PAPER)
    assert first == second
    assert [event.sequence for event in first.events] == [1, 2]


def test_attempt_number_counts_repeats_per_question() -> None:
    """同一题第二次作答 = attempt 2（覆盖作答语义），每次 attempt 独立判分。"""
    record = _record(
        [
            AnswerEvent(1, "q_mcq", "A", T0 + timedelta(seconds=5)),  # 错
            AnswerEvent(2, "q_mcq", "B", T0 + timedelta(seconds=9)),  # 改对
        ]
    )
    stream = derive_learning_events(record, PAPER)
    assert [event.attempt_number for event in stream.events] == [1, 2]
    assert [event.correctness for event in stream.events] == [False, True]


def test_latency_is_interval_from_previous_event() -> None:
    """耗时 = 与前一事件的间隔；首题 = 与考试开始的间隔。"""
    record = _record(
        [
            AnswerEvent(1, "q_mcq", "B", T0 + timedelta(seconds=8)),
            AnswerEvent(2, "q_numeric", "3.14", T0 + timedelta(seconds=8 + 27)),
            AnswerEvent(3, "q_essay", "分治 递归", T0 + timedelta(seconds=8 + 27 + 65)),
        ]
    )
    stream = derive_learning_events(record, PAPER)
    assert [event.latency_ms for event in stream.events] == [8000, 27000, 65000]


def test_concept_mapping_and_difficulty_come_from_question() -> None:
    """概念映射 = question.knowledge；难度 = 题目级 difficulty（缺省 3）。"""
    record = _record(
        [
            AnswerEvent(1, "q_numeric", "3.14", T0),
            AnswerEvent(2, "q_essay", "分治 递归", T0),
        ]
    )
    first, second = derive_learning_events(record, PAPER).events
    assert first.concept_ids == ("geometry",)
    assert first.difficulty == 4
    assert second.difficulty == 3  # 省略 → 默认
    assert first.event_type == ANSWER_EVENT_TYPE == "answer"
    assert first.hint_used is False  # 提示功能未上线，字段预留
    assert first.user_id is None
    assert first.occurred_at == T0


def test_correctness_covers_three_states_with_rubric() -> None:
    """objective 对/错 + numeric 非法复核 + essay rubric 部分命中，全在重放中成立。"""
    from app.domain.rubric_grader import KeywordRubricJudge

    record = _record(
        [
            AnswerEvent(1, "q_mcq", "B", T0),  # 对
            AnswerEvent(2, "q_numeric", "abc", T0),  # 复核 None
            AnswerEvent(3, "q_essay", "用分治思想", T0),  # keyword 部分命中 → False
        ]
    )
    stream = derive_learning_events(record, PAPER, rubric_judge=KeywordRubricJudge())
    assert [event.correctness for event in stream.events] == [True, None, False]


def test_unknown_question_derives_review_without_fabrication() -> None:
    """事件引用试卷外题目：correctness=复核、concept_ids 空，不虚构判定。"""
    record = _record([AnswerEvent(1, "q_ghost", "x", T0)])
    (event,) = derive_learning_events(record, PAPER).events
    assert event.correctness is None
    assert event.concept_ids == ()


def test_empty_event_stream_yields_empty_projection() -> None:
    assert derive_learning_events(_record([]), PAPER).events == ()


def test_learning_events_endpoint_replay_grows_with_answers() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        started = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
        exam_id = started.json()["exam_id"]
        questions = started.json()["questions"]

        response = client.get(f"/api/v1/exams/{exam_id}/learning-events")
        assert response.status_code == 200
        assert response.json()["events"] == []

        for sequence, question in enumerate(questions[:2], start=1):
            assert (
                client.put(
                    f"/api/v1/exams/{exam_id}/answers",
                    json={"sequence": sequence, "question_id": question["id"], "answer": "B"},
                ).status_code
                == 200
            )

        stream = client.get(f"/api/v1/exams/{exam_id}/learning-events").json()
        assert stream["exam_id"] == exam_id
        (first, second) = stream["events"]
        assert (first["sequence"], first["attempt_number"], first["event_type"]) == (1, 1, "answer")
        assert first["latency_ms"] >= 0
        assert second["sequence"] == 2
        assert 1 <= first["difficulty"] <= 5  # seed 卷默认难度 3
        assert isinstance(first["concept_ids"], list)

        # 未交卷也再次可查：重放幂等，事件流一致
        again = client.get(f"/api/v1/exams/{exam_id}/learning-events").json()
        assert again == stream


def test_learning_events_unknown_exam_returns_404() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        assert client.get("/api/v1/exams/exam_missing/learning-events").status_code == 404
