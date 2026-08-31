"""M3-04 Misconception candidate：单次错误只生成 candidate；多次独立证据才提升置信度。

derive_misconceptions 是纯函数：同一 (事件集, now) 恒等输出。
独立证据 = 不同题目（distinct question_id）；同题重试不增强。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.domain.learning_events import LearningEvent, LearningEventStream
from app.domain.misconceptions import (
    CONFIDENCE_K,
    CONFIRM_THRESHOLD,
    STATUS_CANDIDATE,
    STATUS_CONFIRMED,
    derive_misconceptions,
    normalize_pattern,
)
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
T0 = datetime(2026, 8, 31, 10, 0, 0, tzinfo=UTC)
NOW = T0 + timedelta(hours=1)
FIXED_NOW = "2026-08-31T11:00:00+00:00"

# 同一概念三道题（正确答案均为 B）；q_extra 用于制造其他概念
PAPER = {
    "title": "误解候选验证卷",
    "duration_seconds": 1800,
    "questions": [
        {
            "question": {
                "question_type": "mcq",
                "stem": f"题{i}：1+1=?",
                "options": ["1", "2", "3"],
                "answer": {"option_index": 1},
                "explanation": "加法",
                "concept_ids": ["arith"],
                "difficulty": 2,
            },
            "score": 1.0,
        }
        for i in (1, 2, 3)
    ]
    + [
        {
            "question": {
                "question_type": "numeric",
                "stem": "圆周率",
                "answer": {"value": 3.14, "tolerance": 0.01},
                "explanation": "pi",
                "concept_ids": ["geo"],
                "difficulty": 3,
            },
            "score": 1.0,
        }
    ],
}


def _event(
    question: str,
    concept: str,
    answer: str,
    correct: bool | None,
    *,
    at: datetime | None = None,
) -> LearningEvent:
    return LearningEvent(
        sequence=1,
        event_type="answer",
        question_id=question,
        concept_ids=(concept,),
        difficulty=2,
        answer=answer,
        correctness=correct,
        latency_ms=5000,
        attempt_number=1,
        hint_used=False,
        occurred_at=at or T0,
    )


def _one(*events: LearningEvent, now: datetime = NOW):
    return derive_misconceptions([LearningEventStream("exam_1", "p1", events)], now=now)


def test_single_error_becomes_candidate_only() -> None:
    """验收核心 1：单次错误只生成 candidate（低置信度），不写成长期画像。"""
    (item,) = _one(_event("q1", "arith", "A", False)).values()
    assert item.status == STATUS_CANDIDATE
    assert item.independent_count == 1
    assert item.confidence == pytest.approx(1 / (1 + CONFIDENCE_K))
    assert 0.0 < item.confidence < 0.5


def test_same_question_retry_does_not_strengthen_evidence() -> None:
    """同题反复答错同一 pattern：occurrence 增加但独立题数不变，仍是 candidate。"""
    item = _one(
        _event("q1", "arith", "A", False),
        _event("q1", "arith", "A", False, at=T0 + timedelta(minutes=1)),
        _event("q1", "arith", "A", False, at=T0 + timedelta(minutes=2)),
    )[("arith", "a")]
    assert item.occurrence_count == 3
    assert item.independent_count == 1
    assert item.status == STATUS_CANDIDATE
    assert item.question_ids == ("q1",)


def test_independent_questions_raise_confidence_and_confirm() -> None:
    """验收核心 2：多个独立题重复同一误解才提升置信度并升级 confirmed。"""
    one = _one(_event("q1", "arith", "A", False))[("arith", "a")].confidence
    two = _one(
        _event("q1", "arith", "A", False),
        _event("q2", "arith", "A", False, at=T0 + timedelta(minutes=1)),
    )[("arith", "a")]
    assert two.independent_count == 2
    assert two.confidence > one  # 置信度随独立证据提升
    assert two.status == STATUS_CANDIDATE  # 未达阈值仍是 candidate
    assert two.confidence == pytest.approx(2 / (2 + CONFIDENCE_K))

    events = [
        _event("q1", "arith", "A", False),
        _event("q2", "arith", "A", False, at=T0 + timedelta(minutes=1)),
        _event("q3", "arith", "A", False, at=T0 + timedelta(minutes=2)),
    ]
    confirmed = _one(*events)[("arith", "a")]
    assert confirmed.independent_count == CONFIRM_THRESHOLD
    assert confirmed.status == STATUS_CONFIRMED
    assert confirmed.confidence == pytest.approx(3 / (3 + CONFIDENCE_K))
    assert confirmed.confidence > two.confidence


def test_different_patterns_stay_separate_candidates() -> None:
    """不同错误答案是不同误解模式：各自独立成 candidate，互不合并。"""
    items = _one(
        _event("q1", "arith", "A", False),
        _event("q2", "arith", "C", False, at=T0 + timedelta(minutes=1)),
    )
    assert set(items) == {("arith", "a"), ("arith", "c")}
    assert all(item.status == STATUS_CANDIDATE for item in items.values())


def test_cross_exam_evidence_merges_independent_of_stream_order() -> None:
    """跨考试证据合并：结果与输入流顺序无关（时间序唯一确定）。"""
    exam_a = LearningEventStream("exam_a", "p1", (_event("q1", "arith", "A", False, at=T0),))
    exam_b = LearningEventStream(
        "exam_b", "p1", (_event("q2", "arith", "A", False, at=T0 + timedelta(hours=2)),)
    )
    forward = derive_misconceptions([exam_a, exam_b], now=NOW)
    backward = derive_misconceptions([exam_b, exam_a], now=NOW)
    assert forward == backward
    (item,) = forward.values()
    assert item.independent_count == 2 and item.occurrence_count == 2
    assert item.first_seen_at == T0 and item.last_seen_at == T0 + timedelta(hours=2)


def test_correct_and_review_events_produce_nothing() -> None:
    """答对与复核（None）不产生候选；空答案（留空占位）不是误解。"""
    assert _one(_event("q1", "arith", "B", True)) == {}
    assert _one(_event("q1", "arith", "乱码?", None)) == {}
    assert _one(_event("q1", "arith", "  ", False)) == {}
    assert _one(_event("q1", "", "A", False)) == {}  # 无概念归属


def test_pattern_normalization_is_deterministic() -> None:
    """pattern 归一：空白折叠 + 小写 + 截断 64；等价作答合并为同一误解。"""
    assert normalize_pattern("  A  ") == "a"
    assert normalize_pattern("分治  递归") == "分治 递归"
    assert normalize_pattern("X" * 100) == "x" * 64
    merged = _one(
        _event("q1", "arith", " A ", False),
        _event("q2", "arith", "a", False, at=T0 + timedelta(minutes=1)),
    )[("arith", "a")]
    assert merged.independent_count == 2  # 等价作答合并为同一误解


def test_recompute_is_pure_for_same_events_and_now() -> None:
    stream = LearningEventStream("exam_1", "p1", (_event("q1", "arith", "A", False),))
    assert derive_misconceptions([stream], now=NOW) == derive_misconceptions([stream], now=NOW)


def _answer(client: TestClient, exam_id: str, sequence: int, question: dict, answer: str) -> None:
    assert (
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": sequence, "question_id": question["id"], "answer": answer},
        ).status_code
        == 200
    )


def _start(client: TestClient, paper_id: str) -> tuple[str, list[dict]]:
    started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
    return started.json()["exam_id"], started.json()["questions"]


def test_recompute_endpoint_candidate_lifecycle_and_idempotence() -> None:
    """端到端：单次错→candidate；三道独立题同错→confirmed；重算幂等。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        (paper_id,) = client.post("/api/v1/papers/import", json=[PAPER]).json()["imported"]

        # 考试 1：q1 答 A（错）→ 单次错误
        exam_id, questions = _start(client, paper_id)
        _answer(client, exam_id, 1, questions[0], "A")
        _answer(client, exam_id, 2, questions[1], "B")
        _answer(client, exam_id, 3, questions[2], "B")
        _answer(client, exam_id, 4, questions[3], "3.14")
        assert client.post(f"/api/v1/exams/{exam_id}/submit", json={}).status_code == 200

        first = client.post(
            "/api/v1/student/misconceptions/recompute", params={"now": FIXED_NOW}
        ).json()
        assert first["candidate_count"] == 1
        (item,) = first["candidates"]
        assert (item["concept_id"], item["pattern"]) == ("arith", "a")
        assert item["status"] == STATUS_CANDIDATE
        assert item["independent_count"] == 1

        # 考试 2 + 3：q2、q3 也答 A → 独立证据 ×3 → confirmed
        for question in (questions[1], questions[2]):
            exam_id, _ = _start(client, paper_id)
            _answer(client, exam_id, 1, question, "A")
            assert client.post(f"/api/v1/exams/{exam_id}/submit", json={}).status_code == 200

        second = client.post(
            "/api/v1/student/misconceptions/recompute", params={"now": FIXED_NOW}
        ).json()
        assert second["candidate_count"] == 1
        (item,) = second["candidates"]
        assert item["status"] == STATUS_CONFIRMED
        assert item["independent_count"] == 3
        assert set(item["question_ids"]) == {questions[i]["id"] for i in (0, 1, 2)}

        # 幂等：同事件 + 同 now 重算逐字段全等
        again = client.post(
            "/api/v1/student/misconceptions/recompute", params={"now": FIXED_NOW}
        ).json()
        assert again == second
        assert client.get("/api/v1/student/misconceptions").json() == second
        assert client.get("/api/v1/student/misconceptions/arith").json() == second


def test_misconceptions_404_and_503_and_invalid_now() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        assert client.get("/api/v1/student/misconceptions/ghost").status_code == 404
        response = client.post(
            "/api/v1/student/misconceptions/recompute", params={"now": "not-a-time"}
        )
        assert response.status_code == 422
    with TestClient(create_app(None)) as client:
        assert client.get("/api/v1/student/misconceptions").status_code == 503
        assert client.post("/api/v1/student/misconceptions/recompute").status_code == 503
