"""M2-01 question/paper schema acceptance: all 8 types validate; wrong shapes rejected."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.domain.questions import PaperSpec, QuestionSpec
from app.main import create_app

NL = chr(10)


def _valid_question(qtype: str) -> dict:
    base = {"question_type": qtype, "stem": "题干 " + qtype}
    answers = {
        "mcq": {"options": ["a", "b", "c", "d"], "answer": {"option_index": 2}},
        "multiple_select": {"options": ["a", "b", "c", "d"], "answer": {"option_indices": [1, 3]}},
        "true_false": {"answer": {"value": True}},
        "short_answer": {"answer": {"accepted": ["TCP", "tcp"]}},
        "numeric": {"answer": {"value": 3.14, "tolerance": 0.01}},
        "math": {"answer": {"latex": "E = mc^2"}},
        "coding": {"answer": {"reference_solution": "def f(): pass", "tests": ["assert f()"]}},
        "essay": {"answer": {"rubric_points": ["要点1", "要点2"]}},
    }
    return {**base, **answers[qtype]}


@pytest.mark.parametrize("qtype", [
    "mcq", "multiple_select", "true_false", "short_answer",
    "numeric", "math", "coding", "essay",
])
def test_all_eight_question_types_validate(qtype):
    spec = QuestionSpec(**_valid_question(qtype))
    assert spec.question_type == qtype


def test_rejects_wrong_answer_shapes():
    cases = [
        {"question_type": "mcq", "stem": "s", "options": ["a", "b"],
         "answer": {"value": True}},
        {"question_type": "true_false", "stem": "s", "answer": {"option_index": 0}},
        {"question_type": "numeric", "stem": "s", "answer": {"latex": "x"}},
        {"question_type": "coding", "stem": "s",
         "answer": {"reference_solution": "x", "tests": []}},
        {"question_type": "essay", "stem": "s", "answer": {"rubric_points": []}},
    ]
    for case in cases:
        with pytest.raises(ValidationError):
            QuestionSpec(**case)


def test_rejects_option_index_out_of_range_and_missing_options():
    with pytest.raises(ValidationError):
        QuestionSpec(question_type="mcq", stem="s", options=["a", "b"],
                     answer={"option_index": 5})
    with pytest.raises(ValidationError):
        QuestionSpec(question_type="multiple_select", stem="s",
                     options=["a"], answer={"option_indices": [0, 0]})
    with pytest.raises(ValidationError):
        QuestionSpec(question_type="true_false", stem="s", options=["a", "b"],
                     answer={"value": False})


def test_paper_spec_total_score_validation():
    paper = {
        "title": "期中卷", "duration_seconds": 3600,
        "questions": [
            {"question": _valid_question("mcq"), "score": 10},
            {"question": _valid_question("essay"), "score": 40},
        ],
    }
    spec = PaperSpec(**paper)
    assert spec.total_score is None
    consistent = PaperSpec(**{**paper, "total_score": 50})
    assert consistent.total_score == 50
    with pytest.raises(ValidationError):
        PaperSpec(**{**paper, "total_score": 100})


def test_validation_endpoints():
    with TestClient(create_app(None)) as client:  # 校验端点无需 DB
        ok = client.post("/api/v1/validate/question", json=_valid_question("numeric"))
        assert ok.status_code == 200
        assert ok.json()["question_type"] == "numeric"
        bad = client.post("/api/v1/validate/question",
                          json=_valid_question("mcq") | {"options": ["only"]})
        assert bad.status_code == 422

        paper = {
            "title": "t", "duration_seconds": 600,
            "questions": [
                {"question": _valid_question("mcq"), "score": 5},
                {"question": _valid_question("true_false"), "score": 5},
            ],
            "total_score": 10,
        }
        assert client.post("/api/v1/validate/paper", json=paper).status_code == 200
        inconsistent = {**paper, "total_score": 99}
        assert client.post("/api/v1/validate/paper", json=inconsistent).status_code == 422
