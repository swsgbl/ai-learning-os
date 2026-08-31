"""M3-06 Daily planner：今日任务包含新学、复习和错题重测；解释为什么被选中。

build_daily_plan 是实时投影：同一 (事件集, 题库, now) 恒等输出。
注：q_wrong 用 difficulty=3（FSRS-lite 初始间隔恰为 1.0 天），
使「T0 答错 → T0+1d 到期复习」成立，直接对应验收的昨日错题场景。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.domain.daily_planner import (
    KIND_MISTAKE_RETRY,
    KIND_NEW_LEARNING,
    KIND_REVIEW,
    build_daily_plan,
)
from app.domain.learning_events import LearningEvent, LearningEventStream
from app.domain.models import Angles, Option, Paper, Question
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
T0 = datetime(2026, 8, 30, 10, 0, 0, tzinfo=UTC)  # 「昨天」考试
TOMORROW = T0 + timedelta(days=1)  # 「今天」计划锚点

POOL = Paper(
    id="pool",
    title="选题池",
    subtitle="",
    source="M3-06",
    university=None,
    year=None,
    subject="cs",
    difficulty="core",
    duration_minutes=30,
    tags=(),
    origin_url=None,
    license="",
    questions=(
        Question(
            id="q_wrong",
            type="mcq",
            stem="会答错的题",
            options=(Option("A", "1"), Option("B", "2")),
            answer='{"option_index": 1}',
            explanation="2",
            angles=Angles("arith", "", "", ""),
            knowledge=("arith",),
            difficulty=3,
        ),
        Question(
            id="q_new_weak",
            type="mcq",
            stem="薄弱概念新题",
            options=(Option("A", "1"), Option("B", "2")),
            answer='{"option_index": 1}',
            explanation="2",
            angles=Angles("arith", "", "", ""),
            knowledge=("arith",),
            difficulty=1,
        ),
        Question(
            id="q_new_fresh",
            type="mcq",
            stem="全新概念新题",
            options=(Option("A", "1"), Option("B", "2")),
            answer='{"option_index": 1}',
            explanation="2",
            angles=Angles("fresh", "", "", ""),
            knowledge=("fresh",),
            difficulty=3,
        ),
    ),
)


def _stream(events: list[LearningEvent], exam_id: str = "exam_y") -> LearningEventStream:
    return LearningEventStream(exam_id, "pool", tuple(events))


def _plan(*streams: LearningEventStream, now: datetime = TOMORROW, **kwargs):
    return build_daily_plan(list(streams), [POOL], now=now, **kwargs)


def _wrong_event(question_id: str, at: datetime, sequence: int = 1) -> LearningEvent:
    return LearningEvent(
        sequence=sequence,
        event_type="answer",
        question_id=question_id,
        concept_ids=("arith",),
        difficulty=3,
        answer="A",
        correctness=False,
        latency_ms=5000,
        attempt_number=1,
        hint_used=False,
        occurred_at=at,
    )


def test_yesterdays_wrong_question_shows_up_tomorrow() -> None:
    """验收核心：昨天答错的题出现在今日计划的复习任务里，且带入选理由。"""
    plan = _plan(_stream([_wrong_event("q_wrong", T0)]))
    review = [task for task in plan.tasks if task.kind == KIND_REVIEW]
    assert [task.question_id for task in review] == ["q_wrong"]
    assert review[0].reason.strip()


def test_plan_contains_new_learning_with_reasons() -> None:
    """新学任务按薄弱概念优先排序，每任务 reason/title 非空。"""
    streams = _stream(
        [
            _wrong_event("q_wrong", T0),
            LearningEvent(
                sequence=2,
                event_type="answer",
                question_id="q_done",
                concept_ids=("arith",),
                difficulty=3,
                answer="B",
                correctness=True,
                latency_ms=5000,
                attempt_number=1,
                hint_used=False,
                occurred_at=T0,
            ),
        ]
    )
    plan = _plan(streams)
    kinds = {task.kind for task in plan.tasks}
    assert KIND_REVIEW in kinds
    assert KIND_NEW_LEARNING in kinds
    for task in plan.tasks:
        assert task.reason.strip()
        assert task.title
    # 新学池：薄弱概念 arith（mastery 低）优先于全新概念 fresh
    new_tasks = [task for task in plan.tasks if task.kind == KIND_NEW_LEARNING]
    assert [task.question_id for task in new_tasks] == ["q_new_weak", "q_new_fresh"]
    assert "掌握度" in new_tasks[0].reason
    assert "尚无学习记录" in new_tasks[1].reason


def test_mistake_retry_from_confirmed_misconception() -> None:
    """confirmed 误解候选的题目进入重测任务，reason 引用误解模式与置信度。

    now 取 T0+12h：此时复习尚未到期（间隔 1 天），三类任务中只有
    mistake_retry 与 new_learning，验证误解通道独立于复习通道工作。
    """
    events = [
        _wrong_event(question_id, T0 + timedelta(hours=index), sequence=index + 1)
        for index, question_id in enumerate(("q_wrong", "q_new_weak", "q_new_fresh"))
    ]
    plan = _plan(_stream(events, "exam_mis"), now=T0 + timedelta(hours=12))
    retries = [task for task in plan.tasks if task.kind == KIND_MISTAKE_RETRY]
    assert {task.question_id for task in retries} == {"q_wrong", "q_new_weak", "q_new_fresh"}
    first = retries[0]
    assert "误解候选已升级" in first.reason and "置信度" in first.reason


def test_not_due_reviews_are_excluded() -> None:
    """未到期的复习不进今日计划（next_review_at > now）。"""
    plan = _plan(_stream([_wrong_event("q_wrong", T0)]), now=T0 + timedelta(hours=12))
    assert not [task for task in plan.tasks if task.kind == KIND_REVIEW]


def test_reviewed_questions_do_not_duplicate_across_kinds() -> None:
    """同一题不重复入选：review 已选的题不再出现在误解重测。"""
    events = [
        _wrong_event(question_id, T0 - timedelta(days=2) + timedelta(hours=index), sequence=index + 1)
        for index, question_id in enumerate(("q_wrong", "q_new_weak", "q_new_fresh"))
    ]
    plan = _plan(_stream(events, "exam_dup"))
    question_ids = [task.question_id for task in plan.tasks]
    assert len(question_ids) == len(set(question_ids))
    # 三题的复习均已到期 → review 先拿走全部，误解重测不再重复给出
    assert [task.kind for task in plan.tasks].count(KIND_REVIEW) >= 1
    assert question_ids.count("q_wrong") == 1


def test_quotas_limit_each_kind() -> None:
    """配额生效：各类任务数不超过配额。"""
    events = [_wrong_event("q_wrong", T0 - timedelta(days=5))]
    plan = _plan(_stream(events), review_quota=1, mistake_quota=1, new_quota=1)
    assert len([task for task in plan.tasks if task.kind == KIND_REVIEW]) <= 1
    assert len([task for task in plan.tasks if task.kind == KIND_MISTAKE_RETRY]) <= 1
    assert len([task for task in plan.tasks if task.kind == KIND_NEW_LEARNING]) <= 1


def test_plan_is_pure_for_same_inputs() -> None:
    """同一 (事件集, 题库, now) 恒等输出（实时投影幂等）。"""
    streams = _stream([_wrong_event("q_wrong", T0)])
    assert _plan(streams) == _plan(streams)


def _import_and_exam(client: TestClient) -> tuple[str, list[dict], str]:
    paper = {
        "title": "今日计划验证卷",
        "duration_seconds": 1800,
        "questions": [
            {
                "question": {
                    "question_type": "mcq",
                    "stem": "会错的题",
                    "options": ["A", "B"],
                    "answer": {"option_index": 1},
                    "explanation": "B",
                    "concept_ids": ["plan_arith"],
                    "difficulty": 3,
                },
                "score": 1.0,
            }
        ],
    }
    (paper_id,) = client.post("/api/v1/papers/import", json=[paper]).json()["imported"]
    started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
    return paper_id, started.json()["questions"], started.json()["exam_id"]


def test_daily_plan_endpoint_end_to_end() -> None:
    """端到端：答错的题出现在日后计划的复习任务，且 reason 解释入选理由。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        _paper_id, questions, exam_id = _import_and_exam(client)
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": questions[0]["id"], "answer": "A"},
        )
        assert client.post(f"/api/v1/exams/{exam_id}/submit", json={}).status_code == 200

        # now 用远期锚点保证复习到期（事件发生在真实时钟）
        anchor = "2030-01-01T00:00:00+00:00"
        plan = client.get("/api/v1/student/daily-plan", params={"now": anchor}).json()
        assert plan["review_count"] >= 1
        review = [task for task in plan["tasks"] if task["kind"] == KIND_REVIEW]
        (target,) = [task for task in review if task["question_id"] == questions[0]["id"]]
        assert target["concept_ids"] == ["plan_arith"]
        assert "复习" in target["reason"]

        again = client.get("/api/v1/student/daily-plan", params={"now": anchor}).json()
        assert again == plan  # 实时投影重放幂等

        bad_now = client.get("/api/v1/student/daily-plan", params={"now": "not-a-time"})
        assert bad_now.status_code == 422
    with TestClient(create_app(None)) as client:
        assert client.get("/api/v1/student/daily-plan").status_code == 503
