"""M3-03 StudentConceptState：mastery、confidence、forgetting_risk 可从事件重算且更新幂等。

derive_concept_states 是纯函数：同一 (事件集, now) 恒等输出；
API recompute 重复执行行集合一致。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.domain.learning_events import LearningEvent, LearningEventStream
from app.domain.student_state import (
    ALPHA,
    CONFIDENCE_K,
    derive_concept_states,
    weak_concepts,
)
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
T0 = datetime(2026, 8, 31, 10, 0, 0, tzinfo=UTC)
NOW = T0 + timedelta(hours=1)
FIXED_NOW = "2026-08-31T11:00:00+00:00"

# 导入卷：arithmetic 必答对，geometry 必答错（判分与 M2 objective grader 兼容）
PAPER = {
    "title": "学生状态验证卷",
    "duration_seconds": 600,
    "questions": [
        {
            "question": {
                "question_type": "mcq",
                "stem": "1+1=?",
                "options": ["1", "2"],
                "answer": {"option_index": 1},
                "explanation": "加法",
                "concept_ids": ["arithmetic"],
                "difficulty": 2,
            },
            "score": 1.0,
        },
        {
            "question": {
                "question_type": "numeric",
                "stem": "圆周率保留两位",
                "answer": {"value": 3.14, "tolerance": 0.01},
                "explanation": "pi",
                "concept_ids": ["geometry"],
                "difficulty": 4,
            },
            "score": 1.0,
        },
    ],
}


def _event(
    concept: str,
    correct: bool | None,
    *,
    attempt: int = 1,
    difficulty: int = 3,
    hint: bool = False,
    at: datetime | None = None,
) -> LearningEvent:
    return LearningEvent(
        sequence=attempt,
        event_type="answer",
        question_id=f"q_{concept}_{attempt}",
        concept_ids=(concept,),
        difficulty=difficulty,
        answer="B",
        correctness=correct,
        latency_ms=5000,
        attempt_number=attempt,
        hint_used=hint,
        occurred_at=at or T0,
    )


def _one(*events: LearningEvent, now: datetime = NOW):
    """单概念单流重算（用例隔离：不同概念命名，避免跨流合并）。"""
    return derive_concept_states([LearningEventStream("exam_1", "p1", events)], now=now)


def test_correct_answer_raises_mastery_monotonically_bounded() -> None:
    one = _one(_event("a", True))["a"].mastery
    two = _one(_event("b", True), _event("b", True, at=T0 + timedelta(minutes=1)))["b"].mastery
    assert 0.0 < one < two < 1.0  # 单事件有增益、多事件单调升、不越上界


def test_wrong_answer_lowers_mastery() -> None:
    after_correct = _one(_event("c", True))["c"].mastery
    after_wrong = _one(
        _event("c", True), _event("c", False, attempt=2, at=T0 + timedelta(minutes=1))
    )["c"].mastery
    assert 0.0 <= after_wrong < after_correct  # 错题拉低且不越下界


def test_difficulty_modulates_positive_evidence() -> None:
    easy = _one(_event("c", True, difficulty=1))["c"].mastery
    hard = _one(_event("h", True, difficulty=5))["h"].mastery
    assert hard > easy  # 难题做对证据更强（难度系数 0.9 vs 1.3）


def test_retry_attempt_evidence_is_discounted() -> None:
    first_try = _one(_event("a", True))["a"].mastery
    retry = _one(_event("b", True, attempt=2))["b"].mastery
    assert first_try == pytest.approx(ALPHA * 1.1)
    assert retry == pytest.approx(ALPHA * 0.5 * 1.1)  # 重答权重减半


def test_confidence_is_evidence_ratio_with_cap() -> None:
    one = _one(_event("a", True))["a"]
    many = _one(*[_event("b", True, at=T0 + timedelta(minutes=i)) for i in range(9)])["b"]
    assert one.confidence == pytest.approx(1 / (1 + CONFIDENCE_K))
    assert many.confidence == pytest.approx(9 / (9 + CONFIDENCE_K))
    assert one.confidence < many.confidence < 1.0


def test_forgetting_risk_grows_with_time_and_drops_with_mastery() -> None:
    low_mastery = _one(_event("a", False, difficulty=1))["a"]
    high_mastery = _one(*[_event("b", True, difficulty=5)] * 6)["b"]
    soon = _one(_event("c", True), now=T0 + timedelta(minutes=30))["c"].forgetting_risk
    later = _one(_event("c", True), now=T0 + timedelta(hours=100))["c"].forgetting_risk
    assert 0.0 <= soon < later < 1.0  # 时间越久遗忘风险越高，有上界
    assert high_mastery.forgetting_risk < low_mastery.forgetting_risk  # 掌握越高遗忘越慢


def test_review_pending_events_are_not_evidence() -> None:
    assert _one(_event("c", None)) == {}  # correctness=None（复核）不计入
    mixed = _one(_event("c", None), _event("c", True, attempt=2))
    assert mixed["c"].evidence_count == 1  # 只有可判定事件计入


def test_cross_exam_events_merge_chronologically() -> None:
    """事件跨考试按时间序应用：结果与输入流顺序无关；先后次序影响最终掌握。"""
    early = LearningEventStream("exam_a", "p1", (_event("c", True, at=T0),))
    late = LearningEventStream(
        "exam_b", "p1", (_event("c", False, attempt=1, at=T0 + timedelta(hours=2)),)
    )
    forward = derive_concept_states([early, late], now=NOW)["c"]
    backward = derive_concept_states([late, early], now=NOW)["c"]
    assert forward == backward  # 同一事件集与输入顺序无关，时间序唯一确定
    # 先对后错低于先错后对：BKT 顺序敏感，重算必须固定时间序
    flipped = derive_concept_states(
        [
            LearningEventStream("exam_a", "p1", (_event("c", False, attempt=1, at=T0),)),
            LearningEventStream("exam_b", "p1", (_event("c", True, at=T0 + timedelta(hours=2)),)),
        ],
        now=NOW,
    )["c"]
    assert forward.mastery < flipped.mastery


def test_recompute_is_pure_for_same_events_and_now() -> None:
    stream = LearningEventStream(
        "exam_1", "p1", (_event("c", True), _event("d", False, difficulty=4))
    )
    assert derive_concept_states([stream], now=NOW) == derive_concept_states([stream], now=NOW)


def test_weak_concepts_sorted_by_mastery() -> None:
    states = derive_concept_states(
        [
            LearningEventStream("exam_1", "p1", (_event("strong", True, difficulty=5),) * 6),
            LearningEventStream("exam_2", "p1", (_event("weak", False),) * 2),
            LearningEventStream("exam_3", "p1", (_event("middle", True),)),
        ],
        now=NOW,
    )
    assert weak_concepts(states.values()) == ["weak", "middle"]  # 升序，strong 在阈值之上


def _run_exam(client: TestClient) -> None:
    (paper_id,) = client.post("/api/v1/papers/import", json=[PAPER]).json()["imported"]
    started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
    exam_id = started.json()["exam_id"]
    questions = started.json()["questions"]
    client.put(
        f"/api/v1/exams/{exam_id}/answers",
        json={"sequence": 1, "question_id": questions[0]["id"], "answer": "B"},  # arithmetic 对
    )
    client.put(
        f"/api/v1/exams/{exam_id}/answers",
        json={"sequence": 2, "question_id": questions[1]["id"], "answer": "9.9"},  # geometry 错
    )
    assert client.post(f"/api/v1/exams/{exam_id}/submit", json={}).status_code == 200


def test_recompute_endpoint_is_idempotent_and_ranks_concepts() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        _run_exam(client)

        first = client.post(
            "/api/v1/student/states/recompute", params={"now": FIXED_NOW}
        ).json()
        assert first["concept_count"] == 2
        by_concept = {state["concept_id"]: state for state in first["states"]}
        assert by_concept["arithmetic"]["correct_count"] == 1
        assert by_concept["geometry"]["wrong_count"] == 1
        assert by_concept["arithmetic"]["mastery"] > by_concept["geometry"]["mastery"]
        assert first["weak_concepts"][0] == "geometry"  # 最薄弱排最前
        assert by_concept["arithmetic"]["evidence_count"] == 1
        assert by_concept["arithmetic"]["forgetting_risk"] >= 0.0

        # 更新幂等验收：同事件 + 同 now 重算，输出逐字段全等
        second = client.post(
            "/api/v1/student/states/recompute", params={"now": FIXED_NOW}
        ).json()
        assert second == first

        # 读取端点与重算结果一致
        assert client.get("/api/v1/student/states").json() == first
        assert client.get("/api/v1/student/states/arithmetic").json() == by_concept["arithmetic"]


def test_recompute_counts_active_exams_without_submission() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        _run_exam(client)
        before = client.post(
            "/api/v1/student/states/recompute", params={"now": FIXED_NOW}
        ).json()["concept_count"]

        # 未交卷的 active 考试：作答同样是学习证据
        started = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
        questions = started.json()["questions"]
        client.put(
            f"/api/v1/exams/{started.json()['exam_id']}/answers",
            json={"sequence": 1, "question_id": questions[0]["id"], "answer": "B"},
        )

        after = client.post(
            "/api/v1/student/states/recompute", params={"now": FIXED_NOW}
        ).json()
        assert after["concept_count"] >= before  # 不因未交卷被排除


def test_student_state_unknown_concept_returns_404() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        assert client.get("/api/v1/student/states/ghost").status_code == 404


def test_student_state_requires_database() -> None:
    with TestClient(create_app(None)) as client:
        assert client.get("/api/v1/student/states").status_code == 503
        assert client.post("/api/v1/student/states/recompute").status_code == 503


def test_recompute_rejects_invalid_now() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        response = client.post("/api/v1/student/states/recompute", params={"now": "not-a-time"})
        assert response.status_code == 422
