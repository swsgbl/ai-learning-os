"""M2-08 Objective grader golden set：判分 100%；规则版本与输入事件可追溯。

golden set 覆盖 mcq / multiple_select / true_false / short_answer 四类 objective 题型，
含 legacy 字符串与导入 JSON 两种 expected 形态、服务端答案归一边界。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.domain.grading import OBJECTIVE_RULE_VERSION, grade_answer, normalize_type
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"

# (id, question_type, expected, given, correct)
GOLDEN_SET = [
    # --- mcq：大小写/空白归一 ---
    ("mcq-exact", "mcq", "A", "A", True),
    ("mcq-lower", "mcq", "A", "a", True),
    ("mcq-padded", "mcq", "A", "  A  ", True),
    ("mcq-wrong", "mcq", "A", "B", False),
    ("mcq-empty", "mcq", "A", "", False),
    ("mcq-seed-alias", "choice", "A", "A", True),
    ("mcq-json-index", "mcq", json.dumps({"option_index": 1}), "B", True),
    ("mcq-json-index-lower", "mcq", json.dumps({"option_index": 1}), "b", True),
    ("mcq-json-index-wrong", "mcq", json.dumps({"option_index": 1}), "A", False),
    # --- multiple_select：集合等价 ---
    ("ms-exact", "multiple_select", "ABD", "ABD", True),
    ("ms-permuted", "multiple_select", "ABD", "ADB", True),
    ("ms-lower", "multiple_select", "ABD", "abd", True),
    ("ms-dup-collapsed", "multiple_select", "ABD", "ABBD", True),
    ("ms-subset", "multiple_select", "ABD", "AB", False),
    ("ms-different", "multiple_select", "ABD", "ABC", False),
    ("ms-json-indices", "multiple_select", json.dumps({"option_indices": [0, 1, 3]}), "ADB", True),
    ("ms-json-wrong", "multiple_select", json.dumps({"option_indices": [0, 1, 3]}), "AD", False),
    # --- true_false：语义归一 ---
    ("tf-exact", "true_false", "T", "T", True),
    ("tf-lower", "true_false", "T", "t", True),
    ("tf-true-word", "true_false", "T", "true", True),
    ("tf-chinese", "true_false", "T", "正确", True),
    ("tf-false-word", "true_false", "T", "false", False),
    ("tf-f-exact", "true_false", "F", "F", True),
    ("tf-f-chinese", "true_false", "F", "错误", True),
    ("tf-t-mismatch", "true_false", "F", "T", False),
    ("tf-seed-alias", "tf", "T", "对", True),
    ("tf-json-true", "true_false", json.dumps({"value": True}), "对", True),
    ("tf-json-false-answered-f", "true_false", json.dumps({"value": False}), "F", True),
    ("tf-json-false-answered-t", "true_false", json.dumps({"value": False}), "T", False),
    # --- short_answer：JSON accepted + legacy 备选 ---
    ("sa-json-exact", "short_answer", json.dumps({"accepted": ["O(n log n)"]}), "O(n log n)", True),
    ("sa-json-nospace", "short_answer", json.dumps({"accepted": ["O(n log n)"]}), "O(nlogn)", True),
    ("sa-json-case", "short_answer", json.dumps({"accepted": ["O(n log n)"]}), "o(N LOG n)", True),
    ("sa-json-second", "short_answer", json.dumps({"accepted": ["O(n log n)", "n log n"]}), "n log n", True),
    ("sa-json-wrong", "short_answer", json.dumps({"accepted": ["O(n log n)"]}), "O(n)", False),
    ("sa-legacy-pipe", "short_answer", "快排 | 快速排序", "快速排序", True),
    ("sa-legacy-pipe-wrong", "short_answer", "快排 | 快速排序", "冒泡", False),
    ("sa-seed-alias", "short", "quicksort", "QuickSort", True),
]


@pytest.mark.parametrize(("qtype", "expected", "given", "correct"), [case[1:] for case in GOLDEN_SET], ids=[case[0] for case in GOLDEN_SET])
def test_golden_set_grades_correctly(qtype: str, expected: str, given: str, correct: bool) -> None:
    assert grade_answer(qtype, expected, given) is correct


def test_non_objective_types_left_for_later_graders() -> None:
    """coding/essay 归 M2-10：objective grader 一律不判（numeric/math 自 M2-09 起接入三态）。"""
    for qtype in ("coding", "essay"):
        assert grade_answer(qtype, "{}", "anything") is False


def test_type_aliases_normalize() -> None:
    assert normalize_type("tf") == "true_false"
    assert normalize_type("choice") == "mcq"
    assert normalize_type("short") == "short_answer"
    assert normalize_type("MCQ") == "mcq"
    assert normalize_type("essay") == "essay"


def test_submission_records_rule_version_and_input_events() -> None:
    """判分记录规则版本；输入事件保留 answer_events + items.given/expected 可重放。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        started = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
        exam_id = started.json()["exam_id"]
        questions = started.json()["questions"]
        givens = ["A", "B", ""]  # 答对一题、答错一题、留空一题
        for sequence, (question, given) in enumerate(zip(questions, givens), start=1):
            if not given:
                continue
            client.put(
                f"/api/v1/exams/{exam_id}/answers",
                json={"sequence": sequence, "question_id": question["id"], "answer": given},
            )
        report = client.post(f"/api/v1/exams/{exam_id}/submit", json={}).json()

        assert report["rule_version"] == OBJECTIVE_RULE_VERSION
        given_events = {item["question_id"]: item["given"] for item in report["items"]}
        expected_events = {item["question_id"]: item["expected"] for item in report["items"]}
        assert set(given_events.values()) == {"A", "B", ""}, "判分输入事件必须逐题留痕（含未答）"
        assert all(expected_events.values()), "期望答案逐题留痕"
        assert report["correct_count"] == 1


def test_imported_paper_short_answer_graded_from_json() -> None:
    """M2-02 导入卷的 short_answer（JSON accepted）判分打通：legacy 缺陷回归。"""
    paper = {
        "title": "判分回归卷",
        "duration_seconds": 600,
        "questions": [
            {
                "question": {
                    "question_type": "short_answer",
                    "stem": "写出快速排序平均复杂度",
                    "answer": {"accepted": ["O(n log n)"]},
                    "explanation": "平均 O(n log n)",
                },
                "score": 1.0,
            },
            {
                "question": {
                    "question_type": "multiple_select",
                    "stem": "哪些是稳定排序？",
                    "options": ["归并", "快排", "插入", "选择"],
                    "answer": {"option_indices": [0, 2]},
                    "explanation": "归并与插入稳定",
                },
                "score": 1.0,
            },
        ],
    }
    with TestClient(create_app(SQLITE_URL)) as client:
        (paper_id,) = client.post("/api/v1/papers/import", json=[paper]).json()["imported"]
        started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
        exam_id = started.json()["exam_id"]
        questions = started.json()["questions"]

        submits = ["O(n log n)", "AC"]  # short_answer 答对；multiple_select 集合等价
        for sequence, (question, given) in enumerate(zip(questions, submits), start=1):
            client.put(
                f"/api/v1/exams/{exam_id}/answers",
                json={"sequence": sequence, "question_id": question["id"], "answer": given},
            )
        report = client.post(f"/api/v1/exams/{exam_id}/submit", json={}).json()
        assert report["correct_count"] == 2, "导入卷 short_answer 与 multiple_select 均须判对"
