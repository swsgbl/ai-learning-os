"""M16-005 间隔复习调度域层测试（升级方案 Phase 6 验收）。

覆盖：初始队列模板；表现调整（独立对/高信心跳档/提示对不长档/错回短档/
未判分保持）；复习状态机（presented≠attempted≠completed；未判分不
completed）；非法转换拒绝。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.review_schedule import (
    ReviewOutcome,
    ReviewScheduleError,
    initial_schedule,
    next_interval_days,
    review_status_transition,
)


def test_initial_schedule_template() -> None:
    now = datetime(2026, 10, 22, tzinfo=UTC)
    queue = initial_schedule(["c1", "c2"], now)
    assert [c for c, _ in queue] == ["c1", "c2"]
    assert all(due == now + timedelta(days=1) for _, due in queue)


def test_first_round_independent_correct_advances() -> None:
    review = next_interval_days(None, ReviewOutcome(correct=True, confidence=3))
    assert review.due_in_days >= 1
    assert "独立答对" in review.note


def test_high_confidence_advances_faster() -> None:
    steady = next_interval_days(7, ReviewOutcome(correct=True, confidence=3))
    confident = next_interval_days(7, ReviewOutcome(correct=True, confidence=5))
    assert confident.due_in_days > steady.due_in_days


def test_hinted_correct_does_not_extend() -> None:
    review = next_interval_days(7, ReviewOutcome(correct=True, independent=False))
    assert review.due_in_days == 7


def test_failure_resets_to_shortest() -> None:
    review = next_interval_days(30, ReviewOutcome(correct=False))
    assert review.due_in_days == 1


def test_ungraded_holds_interval() -> None:
    review = next_interval_days(7, ReviewOutcome(correct=None))
    assert review.due_in_days == 7
    assert "未判分" in review.note


def test_interval_grows_monotonically_from_template() -> None:
    """模板档是默认起点；表现调整产生连续增长值（7 → ~9 → ~12 → …）。"""
    step1 = next_interval_days(7, ReviewOutcome(correct=True, confidence=3))
    step2 = next_interval_days(step1.due_in_days, ReviewOutcome(correct=True, confidence=3))
    assert step1.due_in_days > 7
    assert step2.due_in_days > step1.due_in_days


def test_review_status_machine_honest_boundaries() -> None:
    """scheduled 不能直接 completed——必须经 presented/attempted。"""
    assert review_status_transition("scheduled", "presented") == "presented"
    assert review_status_transition("presented", "attempted") == "attempted"
    assert review_status_transition("attempted", "completed") == "completed"
    with pytest.raises(ReviewScheduleError):
        review_status_transition("scheduled", "attempted")  # 未展示不能作答
    with pytest.raises(ReviewScheduleError):
        review_status_transition("scheduled", "completed")
    with pytest.raises(ReviewScheduleError):
        review_status_transition("presented", "completed")  # 未作答不能完成
    # completed 后可重看（下一轮新记录）
    assert review_status_transition("completed", "presented") == "presented"
    with pytest.raises(ReviewScheduleError):
        review_status_transition("weird", "presented")


# ---------------------------------------------------------------- M17-003 FSRS 调度器


def test_fsrs_rating_mapping() -> None:
    from app.domain.review_schedule import _rating_from_outcome

    assert _rating_from_outcome(ReviewOutcome(correct=False)) == 1  # Again
    assert _rating_from_outcome(ReviewOutcome(correct=True, confidence=2)) == 2  # Hard
    assert _rating_from_outcome(ReviewOutcome(correct=True, confidence=3)) == 3  # Good
    assert _rating_from_outcome(ReviewOutcome(correct=True, confidence=5)) == 4  # Easy
    assert _rating_from_outcome(ReviewOutcome(correct=None)) == 1


def test_fsrs_memory_state_evolves_across_reviews() -> None:
    from app.domain.review_schedule import (
        FSRS_ALGORITHM_VERSION,
        next_review_fsrs,
    )

    first = next_review_fsrs(None, ReviewOutcome(correct=True, confidence=3))
    assert first.algorithm_version == FSRS_ALGORITHM_VERSION
    assert first.memory_state is not None and first.due_in_days >= 1
    # 高信心二轮：stability 演化（间隔增长）——记忆状态跨轮追踪是 FSRS 核心
    second = next_review_fsrs(first.memory_state, ReviewOutcome(correct=True, confidence=5))
    assert second.due_in_days > first.due_in_days
    assert second.memory_state is not None
    assert second.memory_state != first.memory_state


def test_fsrs_failure_resets_interval_but_keeps_state() -> None:
    from app.domain.review_schedule import next_review_fsrs

    first = next_review_fsrs(None, ReviewOutcome(correct=True, confidence=4))
    failed = next_review_fsrs(first.memory_state, ReviewOutcome(correct=False))
    assert failed.due_in_days == 1  # 答错回最短（Again）
    assert failed.memory_state is not None  # 状态仍在演化（difficulty 上升）


def test_fsrs_ungraded_schedules_nothing() -> None:
    from app.domain.review_schedule import next_review_fsrs

    result = next_review_fsrs(None, ReviewOutcome(correct=None))
    assert result.due_in_days == 0
    assert result.memory_state is None
    assert "未判分" in result.note
