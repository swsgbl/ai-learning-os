"""M2-09 Numeric/math grader：单位、容差、等价表达式；不确定项进入复核。

三态语义：True / False / None（复核）。None 不计入 score 分子分母，逐题留痕。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.domain.grading import grade_math, grade_numeric
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.mark.parametrize(
    ("expected", "given", "verdict"),
    [
        # 容差
        ('{"value": 3.14, "tolerance": 0.01}', "3.14", True),
        ('{"value": 3.14, "tolerance": 0.01}', "3.145", True),
        ('{"value": 3.14, "tolerance": 0.01}', "3.2", False),
        ('{"value": 100, "tolerance": 0}', "100", True),
        ('{"value": 100, "tolerance": 0}', "100.0001", False),
        # 单位换算（同纲量）
        ('{"value": 1.5, "tolerance": 0.01, "unit": "m"}', "150 cm", True),
        ('{"value": 1.5, "tolerance": 0.01, "unit": "m"}', "1.5 m", True),
        ('{"value": 1.5, "tolerance": 0.01, "unit": "m"}', "120 cm", False),
        ('{"value": 90, "tolerance": 0.5, "unit": "min"}', "1.5 h", True),
        ('{"value": 2, "tolerance": 0.01, "unit": "kg"}', "2000 g", True),
        # 跨纲量必然错
        ('{"value": 1.5, "tolerance": 0.01, "unit": "m"}', "150 s", False),
        # 不确定 -> 复核
        ('{"value": 1.5, "tolerance": 0.01, "unit": "m"}', "150", None),  # 缺单位
        ('{"value": 1.5, "tolerance": 0.01, "unit": "furlong"}', "750 m", None),  # 未知单位
        ('{"value": 1.5, "tolerance": 0.01}', "约 1.5", None),  # 非数值文本
        ('{"value": 1.5, "tolerance": 0.01}', "15ab", None),  # 无法解析
    ],
)
def test_numeric_golden(expected: str, given: str, verdict: bool | None) -> None:
    assert grade_numeric(expected, given) is verdict


@pytest.mark.parametrize(
    ("expected", "given", "verdict"),
    [
        # 字面归一即相等
        (r"x + y", "x+y", True),
        (r'\frac{1}{2}', r"((1)/(2))", True),
        # sympy 恒等：等价表达式
        (r"x + y", "y + x", True),
        (r"2 \cdot x", "x*2", True),
        (r"\frac{x}{2}", "0.5*x", True),
        (r"x^2", "x*x", True),
        (r"(x+y)^2", "x**2 + 2*x*y + y**2", True),
        # 不等价
        (r"x^2", "2^x", False),
        (r"x + 1", "x - 1", False),
        # 不确定 -> 复核
        (r"\int_0^1 x dx", r"\frac{1}{2}", None),  # 积分超出色判定子集
        (r"\\ nonsense \frac{", "x", None),  # 非法 LaTeX 解析失败
    ],
)
def test_math_golden(expected: str, given: str, verdict: bool | None) -> None:
    assert grade_math(expected, given) is verdict


def test_math_json_expected_form() -> None:
    assert grade_math(json.dumps({"latex": "x+y"}), "y+x") is True


def test_review_flow_excluded_from_score() -> None:
    """待复核题（None）不计入 score 分子分母；逐题 correct 留痕为 null。"""
    paper = {
        "title": "三态判分卷",
        "duration_seconds": 600,
        "questions": [
            {
                "question": {
                    "question_type": "mcq",
                    "stem": "1+1=?",
                    "options": ["1", "2"],
                    "answer": {"option_index": 1},
                    "explanation": "加法",
                },
                "score": 1.0,
            },
            {
                "question": {
                    "question_type": "numeric",
                    "stem": "圆周率保留两位",
                    "answer": {"value": 3.14, "tolerance": 0.01},
                    "explanation": "pi",
                },
                "score": 1.0,
            },
            {
                "question": {
                    "question_type": "math",
                    "stem": "化简：与 x+x 等价的表达式",
                    "answer": {"latex": "2x"},
                    "explanation": "合并同类项",
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
        givens = ["B", "3.14", "x+x 的两倍诶（无法解析）"]
        for sequence, (question, given) in enumerate(zip(questions, givens), start=1):
            client.put(
                f"/api/v1/exams/{exam_id}/answers",
                json={"sequence": sequence, "question_id": question["id"], "answer": given},
            )
        report = client.post(f"/api/v1/exams/{exam_id}/submit", json={}).json()

        verdicts = [item["correct"] for item in report["items"]]
        assert verdicts == [True, True, None], "三态逐题留痕"
        assert report["total_count"] == 3
        assert report["correct_count"] == 2
        assert report["score"] == 100, "待复核题不计入分子分母：2 判定全对 -> 100"
        assert report["rule_version"].startswith("objective-")
