"""M3-07 选题策略：第二次任务受弱概念、难度和历史错题影响。

build_selection 是实时投影：同一 (事件集, 题库, now) 恒等输出。
场景：第一次考试 = 错题重做探针（r_c）+ 薄弱探针（weak_c 答错）+
强项探针（strong_c 难度 5 连对 3 次 → mastery ≈ 0.675 ≥ 0.6）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.domain.learning_events import LearningEvent, LearningEventStream
from app.domain.models import Angles, Option, Paper, Question
from app.domain.selection import (
    KIND_ADVANCED,
    KIND_RETRY,
    KIND_WEAK,
    build_selection,
)
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
T0 = datetime(2026, 8, 30, 10, 0, 0, tzinfo=UTC)
NOW = T0 + timedelta(hours=1)

POOL = Paper(
    id="pool",
    title="选题池",
    subtitle="",
    source="M3-07",
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
            id="q_w_easy",
            type="mcq",
            stem="弱概念易题",
            options=(Option("A", "1"), Option("B", "2")),
            answer='{"option_index": 1}',
            explanation="2",
            angles=Angles("weak_c", "", "", ""),
            knowledge=("weak_c",),
            difficulty=1,
        ),
        Question(
            id="q_w_hard",
            type="mcq",
            stem="弱概念难题",
            options=(Option("A", "1"), Option("B", "2")),
            answer='{"option_index": 1}',
            explanation="2",
            angles=Angles("weak_c", "", "", ""),
            knowledge=("weak_c",),
            difficulty=4,
        ),
        Question(
            id="q_s_easy",
            type="mcq",
            stem="强概念易题",
            options=(Option("A", "1"), Option("B", "2")),
            answer='{"option_index": 1}',
            explanation="2",
            angles=Angles("strong_c", "", "", ""),
            knowledge=("strong_c",),
            difficulty=1,
        ),
        Question(
            id="q_s_hard",
            type="mcq",
            stem="强概念难题",
            options=(Option("A", "1"), Option("B", "2")),
            answer='{"option_index": 1}',
            explanation="2",
            angles=Angles("strong_c", "", "", ""),
            knowledge=("strong_c",),
            difficulty=5,
        ),
        Question(
            id="q_neutral",
            type="mcq",
            stem="无关新概念题",
            options=(Option("A", "1"), Option("B", "2")),
            answer='{"option_index": 1}',
            explanation="2",
            angles=Angles("fresh_c", "", "", ""),
            knowledge=("fresh_c",),
            difficulty=3,
        ),
    ),
)


def _event(
    sequence: int,
    question_id: str,
    concept: str,
    difficulty: int,
    answer: str,
    correct: bool,
    at: datetime,
) -> LearningEvent:
    return LearningEvent(
        sequence=sequence,
        event_type="answer",
        question_id=question_id,
        concept_ids=(concept,),
        difficulty=difficulty,
        answer=answer,
        correctness=correct,
        latency_ms=5000,
        attempt_number=1,
        hint_used=False,
        occurred_at=at,
    )


def _first_exam_stream() -> LearningEventStream:
    """第一次考试：r_c 错、weak_c 错、strong_c 难度 5 连对 3 次。"""
    events = [
        _event(1, "q_r", "r_c", 2, "A", False, T0),
        _event(2, "q_weak_probe", "weak_c", 3, "A", False, T0 + timedelta(minutes=1)),
    ]
    for offset in range(3):
        events.append(
            _event(
                3 + offset,
                "q_strong_probe",
                "strong_c",
                5,
                "B",
                True,
                T0 + timedelta(minutes=2 + offset),
            )
        )
    return LearningEventStream("exam_1", "pool", tuple(events))


def _selection(**kwargs):
    return build_selection([_first_exam_stream()], [POOL], now=NOW, **kwargs)


def test_retry_wrong_question_is_selected() -> None:
    """历史错题影响选题：答错的探针题（q_r 与 q_weak_probe）均进 retry。"""
    selection = _selection()
    retries = [item for item in selection.items if item.kind == KIND_RETRY]
    assert {item.question_id for item in retries} == {"q_r", "q_weak_probe"}
    (target,) = [item for item in retries if item.question_id == "q_r"]
    assert "错误" in target.reason


def test_weak_concept_questions_selected_easy_before_hard() -> None:
    """弱概念影响选题：weak_c 未答题入选；难度影响排序：易题先于难题。"""
    selection = _selection()
    weak = [item for item in selection.items if item.kind == KIND_WEAK]
    assert [item.question_id for item in weak] == ["q_w_easy", "q_w_hard"]
    for item in weak:
        assert "掌握度" in item.reason and "低于阈值" in item.reason


def test_strong_concept_only_hard_question_selected() -> None:
    """难度影响选题（反向）：强概念只选难题（difficulty>=3），易题不入选。"""
    selection = _selection()
    advanced = [item for item in selection.items if item.kind == KIND_ADVANCED]
    assert [item.question_id for item in advanced] == ["q_s_hard"]
    assert "已达标" in advanced[0].reason
    all_ids = [item.question_id for item in selection.items]
    assert "q_s_easy" not in all_ids  # 强概念的易题不因强概念入选


def test_unrelated_new_concept_not_selected() -> None:
    """与历史无关的新概念题不进入选题（选题受学习历史驱动）。"""
    selection = _selection()
    assert "q_neutral" not in [item.question_id for item in selection.items]


def test_selection_groups_ordered_and_deduplicated() -> None:
    """分组有序（retry → weak → advanced）且同一题不重复入选。"""
    selection = _selection()
    kinds = [item.kind for item in selection.items]
    assert kinds == sorted(kinds, key=lambda k: [KIND_RETRY, KIND_WEAK, KIND_ADVANCED].index(k))
    question_ids = [item.question_id for item in selection.items]
    assert len(question_ids) == len(set(question_ids))
    for item in selection.items:
        assert item.reason.strip()


def test_selection_is_pure_for_same_inputs() -> None:
    assert _selection() == _selection()


def _import_and_exam(client: TestClient) -> tuple[list[dict], str]:
    paper = {
        "title": "选题验证卷",
        "duration_seconds": 1800,
        "questions": [
            {
                "question": {
                    "question_type": "mcq",
                    "stem": f"题{i}",
                    "options": ["A", "B"],
                    "answer": {"option_index": 1},
                    "explanation": "B",
                    "concept_ids": ["sel_c"],
                    "difficulty": 2,
                },
                "score": 1.0,
            }
            for i in (1, 2)
        ],
    }
    (paper_id,) = client.post("/api/v1/papers/import", json=[paper]).json()["imported"]
    started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
    return started.json()["questions"], started.json()["exam_id"]


def test_selection_endpoint_end_to_end() -> None:
    """端到端：第一次考试答错 → 第二次选题含该错题（retry）+ 同概念新题（weak）。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        questions, exam_id = _import_and_exam(client)
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": questions[0]["id"], "answer": "A"},  # 错
        )
        assert client.post(f"/api/v1/exams/{exam_id}/submit", json={}).status_code == 200

        anchor = "2030-01-01T00:00:00+00:00"  # 固定锚点保证重放幂等（事件为真实时钟）
        selection = client.get("/api/v1/student/selection", params={"now": anchor}).json()
        assert selection["retry_count"] >= 1
        retry = [item for item in selection["items"] if item["kind"] == KIND_RETRY]
        (target,) = [item for item in retry if item["question_id"] == questions[0]["id"]]
        assert "错误" in target["reason"]
        # 同概念（sel_c 已薄弱）的未作答新题进 weak 通道
        weak = [item for item in selection["items"] if item["kind"] == KIND_WEAK]
        assert questions[1]["id"] in [item["question_id"] for item in weak]

        again = client.get("/api/v1/student/selection", params={"now": anchor}).json()
        assert again == selection  # 实时投影重放幂等

        bad_now = client.get("/api/v1/student/selection", params={"now": "not-a-time"})
        assert bad_now.status_code == 422
    with TestClient(create_app(None)) as client:
        assert client.get("/api/v1/student/selection").status_code == 503
