"""M3-05 FSRS-like scheduler：错题生成 next_review_at；提前/延迟复习策略可测试。

derive_review_queue 是实时投影（不落库）：同一 (事件集, now) 恒等输出。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.domain.learning_events import LearningEvent, LearningEventStream
from app.domain.review_scheduler import (
    BASE_GROWTH,
    EARLY_PENALTY,
    LATE_BONUS,
    STATUS_REINFORCE,
    STATUS_RETRY,
    derive_review_queue,
    initial_interval,
)
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
T0 = datetime(2026, 8, 31, 10, 0, 0, tzinfo=UTC)
NOW = T0 + timedelta(hours=2)
FIXED_NOW = "2026-08-31T12:00:00+00:00"

PAPER = {
    "title": "复习调度验证卷",
    "duration_seconds": 1800,
    "questions": [
        {
            "question": {
                "question_type": "mcq",
                "stem": "会错的题",
                "options": ["1", "2"],
                "answer": {"option_index": 1},
                "explanation": "2",
                "concept_ids": ["arith"],
                "difficulty": 3,
            },
            "score": 1.0,
        },
        {
            "question": {
                "question_type": "numeric",
                "stem": "全对的题",
                "answer": {"value": 3.14, "tolerance": 0.01},
                "explanation": "pi",
                "concept_ids": ["geo"],
                "difficulty": 3,
            },
            "score": 1.0,
        },
    ],
}


def _event(
    question: str,
    concept: str,
    correct: bool | None,
    *,
    difficulty: int = 3,
    at: datetime | None = None,
) -> LearningEvent:
    return LearningEvent(
        sequence=1,
        event_type="answer",
        question_id=question,
        concept_ids=(concept,),
        difficulty=difficulty,
        answer="B",
        correctness=correct,
        latency_ms=5000,
        attempt_number=1,
        hint_used=False,
        occurred_at=at or T0,
    )


def _one(*events: LearningEvent, now: datetime = NOW):
    return derive_review_queue([LearningEventStream("exam_1", "p1", events)], now=now)


def test_wrong_answer_generates_next_review_at() -> None:
    """验收核心 1：错题生成 next_review_at = last_seen + 初始间隔。"""
    (item,) = _one(_event("q1", "arith", False)).items
    assert item.status == STATUS_RETRY
    assert item.interval_days == initial_interval(3) == 1.0
    assert item.next_review_at == T0 + timedelta(days=1)
    assert item.last_correctness is False
    assert item.overdue_ratio == pytest.approx(2 / 24)  # 2 小时 / 1 天间隔


def test_difficulty_modulates_initial_interval() -> None:
    easy = _one(_event("q1", "c", False, difficulty=1)).items[0]
    hard = _one(_event("q2", "c", False, difficulty=5)).items[0]
    assert easy.interval_days > hard.interval_days  # 难题忘得更快
    assert hard.interval_days == initial_interval(5) == 0.6


def test_never_wrong_questions_do_not_enter_queue() -> None:
    """从没错的题（首答就对/复核/未答）不产生复习任务。"""
    assert _one(_event("q1", "c", True)).items == ()
    assert _one(_event("q1", "c", None)).items == ()
    assert _one(_event("q1", "c", True), _event("q1", "c", True, at=T0 + timedelta(hours=1))).items == ()


def test_recovered_mistake_schedules_reinforce_with_growth() -> None:
    """错过但最后答对：reinforce，正常窗口内回忆间隔 ×BASE_GROWTH。"""
    item = _one(
        _event("q1", "c", False),
        _event("q1", "c", True, at=T0 + timedelta(days=1)),  # 恰好按期复习
    ).items[0]
    assert item.status == STATUS_REINFORCE
    assert item.interval_days == pytest.approx(1.0 * BASE_GROWTH)
    assert item.next_review_at == T0 + timedelta(days=1) + timedelta(days=2.0)


def test_early_review_reduces_growth() -> None:
    """验收核心 2a：提前复习（远早于计划）增长打折。"""
    early = _one(
        _event("q1", "c", False),
        _event("q1", "c", True, at=T0 + timedelta(hours=2)),  # elapsed 0.083d << 0.6×1d
    ).items[0]
    assert early.interval_days == pytest.approx(1.0 * BASE_GROWTH * EARLY_PENALTY)
    ontime = _one(
        _event("q1", "c", False),
        _event("q1", "c", True, at=T0 + timedelta(days=1)),
    ).items[0]
    assert early.interval_days < ontime.interval_days


def test_late_review_bonuses_growth() -> None:
    """验收核心 2b：延迟复习（远超计划）增长加成。"""
    late = _one(
        _event("q1", "c", False),
        _event("q1", "c", True, at=T0 + timedelta(days=5)),  # elapsed 5d >> 2×1d
    ).items[0]
    assert late.interval_days == pytest.approx(1.0 * BASE_GROWTH * LATE_BONUS)
    assert late.interval_days > _one(
        _event("q1", "c", False), _event("q1", "c", True, at=T0 + timedelta(days=1))
    ).items[0].interval_days


def test_wrong_again_resets_interval() -> None:
    """按期复习再错：间隔重置回初始（新的一轮）。"""
    item = _one(
        _event("q1", "c", False),
        _event("q1", "c", True, at=T0 + timedelta(days=1)),
        _event("q1", "c", False, at=T0 + timedelta(days=3)),
    ).items[0]
    assert item.status == STATUS_RETRY
    assert item.interval_days == initial_interval(3)
    assert item.next_review_at == T0 + timedelta(days=3, hours=24)


def test_queue_orders_by_overdue_then_retry_first() -> None:
    """队列排序：overdue 降序；同 overdue 时 retry 优先于 reinforce。"""
    fresh = _event("q_fresh", "c", False, at=T0 + timedelta(hours=1))  # 刚错：overdue≈0
    overdue = _event("q_overdue", "c", False, at=T0 - timedelta(days=2))  # 逾期 2 天
    reinforce = _event("q_reinforce", "c", False, at=T0 - timedelta(days=2) - timedelta(hours=1))
    reinforce_ok = LearningEvent(
        sequence=2,
        event_type="answer",
        question_id=reinforce.question_id,
        concept_ids=("c",),
        difficulty=3,
        answer="B",
        correctness=True,
        latency_ms=1,
        attempt_number=2,
        hint_used=False,
        occurred_at=T0 - timedelta(days=1),
    )
    queue = _one(fresh, overdue, reinforce, reinforce_ok)
    assert [item.question_id for item in queue.items] == ["q_overdue", "q_reinforce", "q_fresh"]
    assert queue.items[0].status == STATUS_RETRY
    assert queue.items[1].status == STATUS_REINFORCE  # overdue 略高所以排前，虽是 reinforce


def test_cross_exam_replay_is_deterministic() -> None:
    stream = LearningEventStream("exam_1", "p1", (_event("q1", "c", False),))
    assert _one(_event("q1", "c", False)) == _one(_event("q1", "c", False))
    # 跨流合并按时间序：exam_1 答错 → exam_2 按期答对（elapsed = 计划间隔）→ 增长
    merged = derive_review_queue(
        [
            stream,
            LearningEventStream(
                "exam_2", "p1", (_event("q1", "c", True, at=T0 + timedelta(days=1)),)
            ),
        ],
        now=NOW,
    )
    assert merged.items[0].interval_days == pytest.approx(1.0 * BASE_GROWTH)


def _start(client: TestClient, paper_id: str) -> tuple[str, list[dict]]:
    started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
    return started.json()["exam_id"], started.json()["questions"]


def test_review_queue_endpoint_end_to_end() -> None:
    """端到端：错题入队（retry）+ 全对题不入队 + 重放幂等 + 422/503。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        (paper_id,) = client.post("/api/v1/papers/import", json=[PAPER]).json()["imported"]
        exam_id, questions = _start(client, paper_id)
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": questions[0]["id"], "answer": "A"},  # 错
        )
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 2, "question_id": questions[1]["id"], "answer": "3.14"},  # 对
        )
        assert client.post(f"/api/v1/exams/{exam_id}/submit", json={}).status_code == 200

        first = client.get("/api/v1/student/review-queue", params={"now": FIXED_NOW}).json()
        assert first["item_count"] == 1  # 全对题不入队
        (item,) = first["items"]
        assert item["question_id"] == questions[0]["id"]
        assert item["status"] == STATUS_RETRY
        assert item["concept_ids"] == ["arith"]
        assert item["next_review_at"] > item["last_seen_at"]

        # 实时投影：重放幂等
        again = client.get("/api/v1/student/review-queue", params={"now": FIXED_NOW}).json()
        assert again == first

        assert (
            client.get(
                "/api/v1/student/review-queue", params={"now": "not-a-time"}
            ).status_code
            == 422
        )
    with TestClient(create_app(None)) as client:
        assert client.get("/api/v1/student/review-queue").status_code == 503
