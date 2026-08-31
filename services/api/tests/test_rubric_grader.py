"""M2-10 Subjective rubric grader：结构化 JSON；低置信度双审；evidence gate。

三态语义沿用 ADR 21：复核 = correct None，不计入 score 分子分母。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.domain.rubric_grader import (
    RUBRIC_CONFIDENCE_FLOOR,
    KeywordRubricJudge,
    RubricCriterion,
    RubricJudgement,
    grade_rubric,
    judge_question,
    parse_judgement,
)
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
POINTS = ("分治", "递归")


def _judgement(
    achieved=(True, True),
    confidence: float = 1.0,
    evidence: tuple[str | None, ...] | None = None,
    points: tuple[str, ...] = POINTS,
) -> RubricJudgement:
    criteria = tuple(
        RubricCriterion(point=point, achieved=value, evidence_id=ev)
        for point, value, ev in zip(points, achieved, evidence or (None,) * len(points))
    )
    return RubricJudgement(
        criteria=criteria,
        confidence=confidence,
        judge_model="fake",
        prompt_hash="hash-1",
        raw=json.dumps({"criteria": []}, ensure_ascii=False),
    )


# ---------------------------------------------------------------- parse_judgement

def test_parse_judgement_accepts_valid_output() -> None:
    raw = json.dumps(
        {
            "criteria": [{"point": "分治", "achieved": True, "evidence_id": "ev_1"}],
            "confidence": 0.9,
            "judge_model": "llm-x",
            "prompt_hash": "p1",
        },
        ensure_ascii=False,
    )
    parsed = parse_judgement(raw)
    assert parsed is not None
    assert parsed.criteria[0].point == "分治"
    assert parsed.criteria[0].achieved is True
    assert parsed.criteria[0].evidence_id == "ev_1"
    assert parsed.judge_model == "llm-x"


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        json.dumps({"criteria": [], "confidence": 0.9}),  # criteria 为空
        json.dumps({"criteria": [{"point": "分治", "achieved": True}], "confidence": 1.5}),  # 置信度越界
        json.dumps({"criteria": [{"point": "分治", "achieved": True}], "confidence": -0.1}),
        json.dumps({"criteria": [{"point": "分治", "achieved": "yes"}], "confidence": 0.9}),  # achieved 非布尔
        json.dumps({"criteria": [{"achieved": True}], "confidence": 0.9}),  # 缺 point
        json.dumps({"criteria": [{"point": "分治", "achieved": True}]}),  # 缺 confidence
        json.dumps([1, 2, 3]),  # 非 dict
    ],
)
def test_parse_judgement_rejects_invalid_output(raw: str) -> None:
    assert parse_judgement(raw) is None


# ---------------------------------------------------------------- grade_rubric

def test_no_judge_means_review() -> None:
    result = grade_rubric(POINTS, None)
    assert result.correct is None
    assert result.needs_second_judge is False


def test_low_confidence_without_second_judge_goes_to_review() -> None:
    result = grade_rubric(POINTS, _judgement((True, True), confidence=0.5))
    assert result.correct is None
    assert result.needs_second_judge is True


def test_agreed_second_judge_settles_score() -> None:
    first = _judgement((True, False), confidence=RUBRIC_CONFIDENCE_FLOOR - 0.01)
    second = _judgement((True, False), confidence=0.95)
    result = grade_rubric(POINTS, first, second)
    assert result.correct is False  # 部分达成：不完全对，且无不确定点
    assert result.needs_second_judge is True
    assert result.confidence == 0.95
    assert result.score_ratio == 0.5


def test_disagreeing_second_judge_goes_to_review() -> None:
    first = _judgement((True, True), confidence=0.5)
    second = _judgement((True, False), confidence=0.95)
    result = grade_rubric(POINTS, first, second)
    assert result.correct is None
    assert result.needs_second_judge is True


def test_criteria_must_cover_exactly_all_points() -> None:
    result = grade_rubric(POINTS, _judgement((True,), points=POINTS[:1]))
    assert result.correct is None  # 覆盖不全：不虚构缺失点结论
    extra = _judgement((True, True, True), points=POINTS + ("幻觉点",))
    assert grade_rubric(POINTS, extra).correct is None


def test_all_achieved_is_correct() -> None:
    result = grade_rubric(POINTS, _judgement((True, True), confidence=0.99))
    assert result.correct is True
    assert result.score_ratio == 1.0
    assert result.evidence_ids == ()


def test_evidence_gate_rejects_unknown_evidence_reference() -> None:
    """无 evidence 的课程事实不得作为依据：引用不存在的 evidence → 降为不确定。"""
    judged = _judgement((True, True), evidence=("ev_missing", None))
    result = grade_rubric(POINTS, judged, valid_evidence_ids=frozenset({"ev_real"}))
    assert result.correct is None  # 任一点依据失效 → 整题复核

    ok = grade_rubric(POINTS, judged, valid_evidence_ids=frozenset({"ev_missing"}))
    assert ok.correct is True
    assert ok.evidence_ids == ("ev_missing",)


# ---------------------------------------------------------------- judge_question 分流

def test_objective_questions_bypass_rubric_pipeline() -> None:
    correct, rubric = judge_question("mcq", "stem", "B", "b", rubric_judge=None)
    assert correct is True
    assert rubric is None


def test_essay_json_and_legacy_expected_forms() -> None:
    judge = KeywordRubricJudge()
    # 导入 JSON 形态
    correct, rubric = judge_question(
        "essay", "论述快排", json.dumps({"rubric_points": ["分治", "递归"]}, ensure_ascii=False),
        "快速排序用分治和递归", rubric_judge=judge,
    )
    assert correct is True
    assert rubric is not None and rubric.judge_model == "keyword-v1"
    # legacy 裸串按单点处理
    correct, rubric = judge_question("essay", "stem", "分治", "分治", rubric_judge=judge)
    assert correct is True


def test_keyword_judge_partial_hit_triggers_second_review_then_settles() -> None:
    """只命中部分评分点：低置信度触发双审；确定性 judge 双审一致后落分为错。"""
    judge = KeywordRubricJudge()
    correct, rubric = judge_question(
        "essay", "stem", json.dumps({"rubric_points": ["分治", "递归"]}, ensure_ascii=False),
        "只提到了分治", rubric_judge=judge,
    )
    assert correct is False
    assert rubric is not None
    assert rubric.needs_second_judge is True
    assert rubric.score_ratio == 0.5
    criteria = json.loads(rubric.criteria_json)["criteria"]
    assert {c["point"]: c["achieved"] for c in criteria} == {"分治": True, "递归": False}


# ---------------------------------------------------------------- API 集成

RUBRIC_PAPER = {
    "title": "rubric验证卷",
    "duration_seconds": 600,
    "questions": [
        {
            "question": {
                "question_type": "essay",
                "stem": "论述快速排序的核心思想",
                "answer": {"rubric_points": ["分治", "递归"]},
                "explanation": "分治 + 递归",
            },
            "score": 2.0,
        },
        {
            "question": {
                "question_type": "mcq",
                "stem": "快排平均复杂度",
                "options": ["O(n)", "O(n log n)"],
                "answer": {"option_index": 1},
                "explanation": "n log n",
            },
            "score": 1.0,
        },
    ],
}


def _start_rubric_exam(client: TestClient) -> tuple[str, list[dict]]:
    (paper_id,) = client.post("/api/v1/papers/import", json=[RUBRIC_PAPER]).json()["imported"]
    started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
    return started.json()["exam_id"], started.json()["questions"]


def test_essay_submission_outputs_structured_rubric() -> None:
    """端到端：essay 走 rubric 管线，结构化明细随报告透出；混合卷 rule_version 留痕。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        exam_id, questions = _start_rubric_exam(client)
        answers = ["快速排序是分治算法，通过递归实现", "B"]
        for sequence, (question, given) in enumerate(zip(questions, answers), start=1):
            response = client.put(
                f"/api/v1/exams/{exam_id}/answers",
                json={"sequence": sequence, "question_id": question["id"], "answer": given},
            )
            assert response.status_code == 200
        report = client.post(f"/api/v1/exams/{exam_id}/submit", json={}).json()

        essay_item = next(item for item in report["items"] if item["rubric"])
        assert essay_item["correct"] is True
        assert essay_item["rubric"]["rule_version"] == "rubric-v1"
        assert essay_item["rubric"]["judge_model"] == "keyword-v1"
        assert essay_item["rubric"]["score_ratio"] == 1.0
        assert {c["point"]: c["achieved"] for c in essay_item["rubric"]["criteria"]} == {
            "分治": True,
            "递归": True,
        }
        mcq_item = next(item for item in report["items"] if not item["rubric"])
        assert mcq_item["correct"] is True
        assert report["rule_version"] == "objective-v2+rubric-v1"


def test_essay_without_judge_configured_goes_to_review() -> None:
    """无 judge 部署：essay 不虚构结论，整题进复核（repo 层直测）。"""
    import asyncio

    from app.domain.models import Angles, Paper, Question
    from app.repositories.memory import MemoryRepository

    paper = Paper(
        id="rubric-review",
        title="复核卷",
        subtitle="",
        source="test",
        university=None,
        year=None,
        subject="cs",
        difficulty="core",
        duration_minutes=10,
        tags=(),
        origin_url=None,
        license="",
        questions=(
            Question(
                id="q1",
                type="essay",
                stem="论述分治",
                options=(),
                answer='{"rubric_points": ["分治"]}',
                explanation="",
                angles=Angles("", "", "", ""),
                knowledge=(),
            ),
        ),
    )

    async def flow():
        repo = MemoryRepository(rubric_judge=None)
        repo._papers[paper.id] = paper
        exam = await repo.create_exam(paper, "exam")
        await repo.save_answer(exam.exam_id, 1, "q1", "随便写的内容")
        return await repo.submit(exam.exam_id)

    submission = asyncio.run(flow())
    item = submission.items[0]
    assert item.correct is None
    assert item.rubric_json is not None
    assert json.loads(item.rubric_json)["score_ratio"] is None
    assert submission.score == 0  # 无已判定题：分母空，得分为 0
    assert submission.rule_version == "rubric-v1"
