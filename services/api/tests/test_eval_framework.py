"""M6-01 Golden grading set：覆盖所有题型 + agreement report 可重复生成。

- 域层：28 case 覆盖 8 题型 + 别名 + 双形态 + 三态；agreement==1.0；报告两次
  生成 JSON 字节级一致；注入坏 case 时 mismatch 全量透出、coverage 降级；
- API：GET report 恒可用（含无 DB）、POST runs 落库留痕、GET runs 回查、404。
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.domain.eval_framework import (
    ALL_QUESTION_TYPES,
    GOLDEN_GRADING_CASES,
    judge_case,
    run_grading_eval,
)
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def test_golden_set_covers_all_types() -> None:
    """28 case 覆盖全部 8 种规范题型（别名/未知题型为补充覆盖）。"""
    assert len(GOLDEN_GRADING_CASES) == 28
    raw = {case["question_type"] for case in GOLDEN_GRADING_CASES}
    assert set(ALL_QUESTION_TYPES) <= raw


def test_full_agreement_and_three_state_coverage() -> None:
    """全量一致（agreement==1.0）；三态（True/False/None）均有样例。"""
    report = run_grading_eval()
    assert report["agreement"] == 1.0
    assert report["mismatches"] == []
    assert report["coverage_complete"] is True
    verdicts = {case["expected_verdict"] for case in GOLDEN_GRADING_CASES}
    assert verdicts == {True, False, None}


def test_report_repeatable_byte_identical() -> None:
    """可重复生成：同 golden set 两次报告 JSON 字节级一致（无时间戳/随机）。"""
    a = json.dumps(run_grading_eval(), sort_keys=True)
    b = json.dumps(run_grading_eval(), sort_keys=True)
    assert a == b


def test_mismatch_transparency() -> None:
    """注入坏 case：mismatch 全量透出 + agreement 下降 + coverage 降级不虚报。"""
    bad = {**GOLDEN_GRADING_CASES[0], "case_id": "injected-bad", "expected_verdict": False}
    report = run_grading_eval((bad,))
    assert report["agreement"] < 1.0
    assert report["mismatches"] == [{
        "case_id": "injected-bad",
        "question_type": "mcq",
        "expected_verdict": False,
        "actual_verdict": True,
    }]
    assert report["coverage_complete"] is False


def test_empty_cases_rejected() -> None:
    try:
        run_grading_eval(())
    except ValueError as cause:
        assert "golden set 为空" in str(cause)
    else:
        raise AssertionError


def test_judge_case_routes_by_type() -> None:
    """judge_case 按题型路由（numeric/math/essay/objective 四管线抽查）。"""
    by_id = {case["case_id"]: case for case in GOLDEN_GRADING_CASES}
    assert judge_case(by_id["numeric-exact"]) is True
    assert judge_case(by_id["math-exact"]) is True
    assert judge_case(by_id["essay-full"]) is True
    assert judge_case(by_id["unknown-type"]) is False


# ---------- API ----------


def test_api_report_no_db() -> None:
    """report 现算恒可用：无 DB 下仍可重复生成。"""
    with TestClient(create_app(None)) as client:
        r1 = client.get("/api/v1/eval/grading/report")
        r2 = client.get("/api/v1/eval/grading/report")
        assert r1.status_code == r2.status_code == 200
        assert r1.json() == r2.json()
        assert r1.json()["agreement"] == 1.0


def test_api_run_record_and_query() -> None:
    """POST runs 落库留痕 -> GET runs 回查 -> 详情一致 -> 404。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        r = client.post("/api/v1/eval/grading/runs")
        assert r.status_code == 201, r.text
        run = r.json()
        assert run["kind"] == "grading"
        assert run["case_count"] == 28
        assert run["agreement"] == 1.0
        assert run["rule_version"] == "eval-v1"
        assert run["report"]["coverage_complete"] is True

        listed = client.get("/api/v1/eval/grading/runs?kind=grading").json()
        assert any(item["id"] == run["id"] for item in listed)

        detail = client.get(f"/api/v1/eval/grading/runs/{run['id']}").json()
        # created_at 序列化差异（写入带 tz、读回 naive 补齐约定同 exams）：前缀一致即可
        assert detail["created_at"][:19] == run["created_at"][:19]
        assert {k: v for k, v in detail.items() if k != "created_at"} == {
            k: v for k, v in run.items() if k != "created_at"
        }

        assert client.get("/api/v1/eval/grading/runs/99999").status_code == 404


def test_api_runs_no_db_503() -> None:
    """落库类端点无 DB 503（report 不受影响）。"""
    with TestClient(create_app(None)) as client:
        assert client.post("/api/v1/eval/grading/runs").status_code == 503
        assert client.get("/api/v1/eval/grading/runs").status_code == 503
