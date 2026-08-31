"""M2-03 ExamSession FSM：全状态迁移单测 + 非法迁移全部拒绝。"""
from __future__ import annotations

import itertools

import pytest

from app.domain.exam_fsm import _TRANSITIONS, assert_transition, can_transition
from app.domain.models import ExamStatus

LEGAL = {
    (ExamStatus.CREATED, ExamStatus.ACTIVE),
    (ExamStatus.ACTIVE, ExamStatus.SUBMITTED),
    (ExamStatus.ACTIVE, ExamStatus.EXPIRED),
    (ExamStatus.SUBMITTED, ExamStatus.REPORT_READY),
    (ExamStatus.EXPIRED, ExamStatus.SUBMITTED),
    (ExamStatus.EXPIRED, ExamStatus.REPORT_READY),
}
ALL_PAIRS = set(itertools.product(ExamStatus, ExamStatus))


def test_matrix_matches_declared_legal_edges() -> None:
    declared = {
        (current, target)
        for current, targets in _TRANSITIONS.items()
        for target in targets
    }
    assert declared == LEGAL


@pytest.mark.parametrize(("current", "target"), sorted(LEGAL))
def test_legal_transitions_pass(current: ExamStatus, target: ExamStatus) -> None:
    assert can_transition(current, target) is True
    assert_transition(current, target)  # 不抛


@pytest.mark.parametrize(("current", "target"), sorted(ALL_PAIRS - LEGAL))
def test_illegal_transitions_all_rejected(current: ExamStatus, target: ExamStatus) -> None:
    assert can_transition(current, target) is False
    with pytest.raises(ValueError) as caught:
        assert_transition(current, target)
    assert current.value in str(caught.value)
    assert target.value in str(caught.value)


def test_submit_rejects_exam_that_never_started() -> None:
    """接线验证：未开始（created）的考试提交被 FSM 拒绝为 409。"""
    import asyncio

    from fastapi.testclient import TestClient

    from app.main import create_app

    SQLITE_URL = "sqlite+aiosqlite:///:memory:"

    async def _force_created(sessionmaker, exam_id: str) -> None:
        from sqlalchemy import update

        from app.db.orm import ExamSessionRow

        async with sessionmaker() as session, session.begin():
            await session.execute(
                update(ExamSessionRow)
                .where(ExamSessionRow.exam_id == exam_id)
                .values(status=ExamStatus.CREATED.value)
            )

    with TestClient(create_app(SQLITE_URL)) as client:
        started = client.post(
            "/api/v1/papers/functions-basics/exams", json={"mode": "exam"}
        )
        exam_id = started.json()["exam_id"]
        asyncio.run(
            _force_created(client.app.state.sessionmaker, exam_id)
        )
        response = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
        assert response.status_code == 409
        assert "created" in response.json()["detail"]
        assert "submitted" in response.json()["detail"]
