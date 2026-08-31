"""M2-11 考试报告：总分、题分、概念分、错题、解析、补救任务和证据链接完整。

报告聚合为纯函数 build_report；API 走 GET /exams/{id}/report。
keyword-v1 judge 双审必然收敛：essay 未命中全点 → correct=False + 部分得分（非复核）；
复核（correct=None）分支在纯函数用例中构造。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.report import build_report
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"

# 混合卷：mcq(答对) + numeric(答错) + essay(部分得分) + tf(留空→错) + essay(乱码→0 分)
REPORT_PAPER = {
    "title": "M2-11 报告验证卷",
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
            },
            "score": 2.0,
        },
        {
            "question": {
                "question_type": "numeric",
                "stem": "圆周率保留两位",
                "answer": {"value": 3.14, "tolerance": 0.01},
                "explanation": "pi",
                "concept_ids": ["arithmetic", "geometry"],
            },
            "score": 3.0,
        },
        {
            "question": {
                "question_type": "essay",
                "stem": "论述快排",
                "answer": {"rubric_points": ["分治", "递归"]},
                "explanation": "分治 + 递归",
                "concept_ids": ["sorting"],
            },
            "score": 4.0,
        },
        {
            "question": {
                "question_type": "true_false",
                "stem": "快排是稳定排序？",
                "answer": {"value": False},
                "explanation": "不稳定",
                "concept_ids": ["sorting"],
            },
            "score": 1.0,
        },
        {
            "question": {
                "question_type": "essay",
                "stem": "论述哈希表原理",
                "answer": {"rubric_points": ["散列函数"]},
                "explanation": "hash",
                "concept_ids": ["hashing"],
            },
            "score": 2.0,
        },
    ],
}


def _import_paper(client: TestClient, paper: dict) -> str:
    (paper_id,) = client.post("/api/v1/papers/import", json=[paper]).json()["imported"]
    return paper_id


def _answer(client: TestClient, exam_id: str, sequence: int, question: dict, text: str) -> None:
    response = client.put(
        f"/api/v1/exams/{exam_id}/answers",
        json={"sequence": sequence, "question_id": question["id"], "answer": text},
    )
    assert response.status_code == 200


def test_report_aggregates_all_required_sections() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        paper_id = _import_paper(client, REPORT_PAPER)
        started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
        exam_id, questions = started.json()["exam_id"], started.json()["questions"]
        # mcq 对；numeric 错(3.2)；essay 部分命中(只提分治)；tf 空串→错；essay 乱码→0 分
        # （答案事件序号必须连续递增，留空的题也要落一个空串事件）
        givens = ["B", "3.2", "快速排序用分治思想", "", "无法解析的内容"]
        for sequence, (question, given) in enumerate(zip(questions, givens), start=1):
            _answer(client, exam_id, sequence, question, given)
        client.post(f"/api/v1/exams/{exam_id}/submit", json={})

        report = client.get(f"/api/v1/exams/{exam_id}/report").json()

        # 总分（两种口径）+ 百分制
        assert report["score_max"] == 12.0  # 2+3+4+1+2
        assert report["score_earned"] == 4.0  # mcq 2 + essay 4×0.5
        assert report["score"] == 20  # 1/5 正确
        assert report["reviewed_count"] == 0  # keyword judge 双审收敛，无复核
        assert report["correct_count"] == 1
        assert report["total_count"] == 5

        # 题分：对=满分；objective 错=0；rubric=比例；keyword 全未命中 ratio=0.0
        scores = [(item["score"], item["max_score"]) for item in report["items"]]
        assert scores == [(2.0, 2.0), (0.0, 3.0), (2.0, 4.0), (0.0, 1.0), (0.0, 2.0)]
        assert report["items"][2]["score_ratio"] == 0.5

        # 概念分：arithmetic 1/2、geometry 0/1、sorting 0/2、hashing 0/1
        concepts = {c["concept"]: c for c in report["concepts"]}
        assert concepts["arithmetic"]["correct"] == 1
        assert concepts["arithmetic"]["total"] == 2
        assert concepts["arithmetic"]["ratio"] == 0.5
        assert concepts["geometry"]["reviewed"] == 0
        assert concepts["geometry"]["ratio"] == 0.0
        assert concepts["sorting"]["total"] == 2
        assert concepts["sorting"]["ratio"] == 0.0
        assert concepts["hashing"]["ratio"] == 0.0

        # 错题：numeric + essay(部分) + tf + essay(乱码)，全部 correct=False
        mistake_ids = {m["question_id"] for m in report["mistakes"]}
        assert mistake_ids == {
            questions[1]["id"],
            questions[2]["id"],
            questions[3]["id"],
            questions[4]["id"],
        }
        numeric_mistake = next(
            m for m in report["mistakes"] if m["question_id"] == questions[1]["id"]
        )
        assert numeric_mistake["given"] == "3.2"
        assert numeric_mistake["expected"].startswith("{")
        assert "arithmetic" in numeric_mistake["knowledge"]

        # 补救任务：每道错题 × 每个关联概念一条 review_concept
        kinds = {(task["kind"], task["detail"]) for task in report["remediation_tasks"]}
        assert ("review_concept", "arithmetic") in kinds
        assert ("review_concept", "geometry") in kinds
        assert ("review_concept", "sorting") in kinds
        assert ("review_concept", "hashing") in kinds
        linked = {
            m["question_id"]: m["remediation_task_ids"] for m in report["mistakes"]
        }
        assert len(linked[questions[1]["id"]]) == 2  # arithmetic + geometry
        for mistake in report["mistakes"]:
            for task_id in mistake["remediation_task_ids"]:
                task = report["remediation_tasks"][task_id]
                assert task["question_id"] == mistake["question_id"]
                assert task["kind"] == "review_concept"

        # keyword judge 不产生 evidence_id → 全卷证据链接为空（不虚构）
        assert report["evidence_ids"] == []


def test_report_requires_submission() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        started = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
        response = client.get(f"/api/v1/exams/{started.json()['exam_id']}/report")
        assert response.status_code == 404
        assert response.json()["detail"] == "审阅报告尚未生成"


def test_report_default_scores_and_full_credit_flow() -> None:
    """全对卷：earned==max、无错题无补救任务、reviewed=0、百分制 100。"""
    paper = {
        "title": "M2-11 满分卷",
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
                },
                "score": 1.5,
            },
            {
                "question": {
                    "question_type": "essay",
                    "stem": "论述快排",
                    "answer": {"rubric_points": ["分治", "递归"]},
                    "explanation": "分治 + 递归",
                    "concept_ids": ["sorting"],
                },
                "score": 2.5,
            },
        ],
    }
    with TestClient(create_app(SQLITE_URL)) as client:
        paper_id = _import_paper(client, paper)
        started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
        exam_id, questions = started.json()["exam_id"], started.json()["questions"]
        _answer(client, exam_id, 1, questions[0], "B")
        _answer(client, exam_id, 2, questions[1], "分治 递归")  # 双点全命中 → confidence 1.0
        client.post(f"/api/v1/exams/{exam_id}/submit", json={})

        report = client.get(f"/api/v1/exams/{exam_id}/report").json()

        assert report["score_max"] == 4.0
        assert report["score_earned"] == 4.0
        assert report["score"] == 100
        assert report["reviewed_count"] == 0
        assert report["mistakes"] == []
        assert report["remediation_tasks"] == []
        assert report["items"][0]["max_score"] == 1.5
        assert report["items"][1]["score"] == 2.5
        assert report["items"][1]["score_ratio"] == 1.0


def _submission_with_item(correct: bool | None, rubric_json: str | None):
    """纯函数用例构造：不经过 API，直接驱动 build_report 的三态分支。"""
    from app.domain.models import (
        Angles,
        ExamStatus,
        GradedItem,
        Paper,
        Question,
        SubmissionRecord,
    )

    question = Question(
        id="q1",
        type="essay",
        stem="s",
        options=(),
        answer="{}",
        explanation="e",
        angles=Angles("", "", "", ""),
        knowledge=("c1",),
        score=5.0,
    )
    paper = Paper(
        id="p1",
        title="t",
        subtitle="",
        source="s",
        university=None,
        year=None,
        subject="cs",
        difficulty="core",
        duration_minutes=10,
        tags=(),
        origin_url=None,
        license="",
        questions=(question,),
    )
    submission = SubmissionRecord(
        exam_id="exam_test",
        paper_id="p1",
        paper_title="t",
        mode="exam",
        status=ExamStatus.SUBMITTED,
        score=100,
        correct_count=1,
        total_count=1,
        duration_seconds=60,
        items=(
            GradedItem(
                question_id="q1",
                given="answer",
                correct=correct,
                expected="{}",
                explanation="e",
                angles=Angles("", "", "", ""),
                rubric_json=rubric_json,
            ),
        ),
        rule_version="rubric-v1",
    )
    return build_report(submission, paper)


def test_build_report_reviewed_item_scores_none_and_concept_ratio_none() -> None:
    """复核题（correct=None）：题分不给、reviewed 计数、概念 ratio 分母不含复核。"""
    report = _submission_with_item(correct=None, rubric_json=None)
    assert report.items[0].score is None
    assert report.items[0].score_ratio is None
    assert report.score_earned == 0.0
    assert report.score_max == 5.0
    assert report.reviewed_count == 1
    assert not report.mistakes  # 复核不算错题
    concept = report.concepts[0]
    assert (concept.total, concept.reviewed, concept.ratio) == (1, 1, None)


def test_build_report_rubric_ratio_drives_partial_score() -> None:
    """rubric score_ratio=0.5 → 题分 = max × ratio，不依赖 correct 直接给满分。"""
    rubric_json = (
        '{"rule_version": "rubric-v1", "criteria": ['
        '{"point": "分治", "achieved": true, "evidence_id": null},'
        '{"point": "递归", "achieved": false, "evidence_id": null}'
        '], "score_ratio": 0.5, "confidence": 1.0, "judge_model": "keyword-v1", "prompt_hash": ""}'
    )
    report = _submission_with_item(correct=False, rubric_json=rubric_json)
    assert report.items[0].score == 2.5  # 5.0 × 0.5
    assert report.items[0].score_ratio == 0.5
    assert report.score_earned == 2.5
    assert report.mistakes[0].question_id == "q1"  # 未全对 → 错题


def test_build_report_ignores_malformed_rubric_json() -> None:
    """rubric 留痕损坏时报告不崩溃，题分按 correct 降级计算。"""
    report = _submission_with_item(correct=True, rubric_json="broken json {")
    assert report.items[0].score == 5.0  # correct=True → 满分
    assert report.items[0].score_ratio is None
    assert report.score_earned == 5.0
