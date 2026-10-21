"""M16-002 作答证据与掌握度 API 契约测试（升级方案 Phase 2 验收）。

覆盖：三态判分落库、信心边界 422、掌握度实时重算（practicing→
provisional→mastered→at_risk 全链经 API）、概念过滤与概览聚合、
「掌握视图可从原始 attempts 重算」（同证据恒同输出）。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _client() -> TestClient:
    return TestClient(create_app(SQLITE_URL))


def _attempt(client: TestClient, **overrides: object) -> dict:
    payload: dict = {
        "item_id": "q1",
        "concept_id": "c_sql_select",
        "response": "SELECT * FROM t",
        "correct": True,
        "confidence": 3,
        "context": "mcq:practice",
        "grading_method": "answer_key",
    }
    payload.update(overrides)
    created = client.post("/api/v1/learning/attempts", json=payload)
    assert created.status_code == 201, created.text
    return created.json()


def test_attempt_three_state_grading() -> None:
    with _client() as client:
        graded = _attempt(client, correct=True, score=100)
        assert graded["correct"] is True
        ungraded = _attempt(client, item_id="q2", correct=None, grading_method="pending")
        assert ungraded["correct"] is None  # 三态：未判分不折算
        wrong = _attempt(client, item_id="q3", correct=False, score=0)
        assert wrong["correct"] is False


def test_attempt_validation() -> None:
    with _client() as client:
        # 信心越界 422（1-5）
        assert client.post(
            "/api/v1/learning/attempts",
            json={"item_id": "q1", "concept_id": "c1", "confidence": 6},
        ).status_code == 422
        assert client.post(
            "/api/v1/learning/attempts",
            json={"item_id": "q1", "concept_id": "c1", "confidence": 0},
        ).status_code == 422
        # 缺必填
        assert client.post("/api/v1/learning/attempts", json={"item_id": "q1"}).status_code == 422


def test_mastery_recomputed_from_raw_attempts_full_chain() -> None:
    """验收核心：掌握视图从原始 attempts 重算，全状态链经 API 验证。"""
    with _client() as client:
        # 起步：单上下文独立正确 → practicing
        _attempt(client, item_id="q1", context="mcq:baseline", correct=True)
        _attempt(client, item_id="q2", context="mcq:baseline", correct=False, confidence=5)
        early = client.get("/api/v1/learning/mastery/c_sql_select").json()
        assert early["state"] in {"practicing", "emerging"}
        assert early["high_confidence_errors"] == 1  # 信心 5 答错被检测

        # 多上下文 → provisional（3 独立正确 + 2 上下文）
        _attempt(client, item_id="q3", context="essay:practice", correct=True)
        _attempt(client, item_id="q3b", context="apply:practice", correct=True)
        provisional = client.get("/api/v1/learning/mastery/c_sql_select").json()
        assert provisional["state"] == "provisional"

        # 延迟复测通过 → mastered
        _attempt(client, item_id="q4", context="mcq:delayed", correct=True, delayed=True)
        mastered = client.get("/api/v1/learning/mastery/c_sql_select").json()
        assert mastered["state"] == "mastered"
        assert mastered["delayed_or_transfer_correct"] == 1

        # 复测失败 → at_risk（新证据追加后重算即降级）
        _attempt(client, item_id="q5", context="mcq:delayed", correct=False, delayed=True)
        at_risk = client.get("/api/v1/learning/mastery/c_sql_select").json()
        assert at_risk["state"] == "at_risk"


def test_mastery_overview_and_filtering() -> None:
    with _client() as client:
        _attempt(client, item_id="q1", concept_id="c_a", context="mcq:x", correct=True)
        _attempt(client, item_id="q2", concept_id="c_b", context="essay:y", correct=False)
        overview = client.get("/api/v1/learning/mastery")
        assert overview.status_code == 200
        concepts = {item["concept_id"] for item in overview.json()}
        assert concepts == {"c_a", "c_b"}

        filtered = client.get("/api/v1/learning/attempts", params={"concept_id": "c_a"})
        assert filtered.status_code == 200
        assert all(item["concept_id"] == "c_a" for item in filtered.json())
        assert len(filtered.json()) == 1


def test_mastery_deterministic_recompute() -> None:
    """同一证据集合重复查询恒同输出（可重算派生视图）。"""
    with _client() as client:
        _attempt(client, item_id="q1", context="mcq:a", correct=True)
        _attempt(client, item_id="q2", context="essay:b", correct=True)
        _attempt(client, item_id="q3", context="apply:c", correct=True)
        first = client.get("/api/v1/learning/mastery/c_sql_select").json()
        second = client.get("/api/v1/learning/mastery/c_sql_select").json()
        assert first == second
