"""M16-001 Sprint 状态机域层测试（升级方案 Phase 1 验收）。

覆盖：全主线合法转换、全部非法转换（跳段/回退/未知阶段）拒绝、
同状态幂等 no-op、暂停/恢复前置校验、重计划阶段边界、时间窗口判断。
"""
from __future__ import annotations

import pytest

from app.domain.sprint_state import (
    ALLOWED_TRANSITIONS,
    PAUSABLE_PHASES,
    SPRINT_PHASES,
    SprintWindow,
    advance,
    can_advance,
    check_pausable,
    check_replannable,
    check_resumable,
)


def test_sprint_phase_chain_is_linear() -> None:
    """主线必须是一条单向链（方案 §4），无分叉无环。"""
    assert SPRINT_PHASES[0] == "created"
    assert SPRINT_PHASES[-1] == "follow_up"
    for index, phase in enumerate(SPRINT_PHASES[:-1]):
        # 每个非终态的合法目标恰是链上的下一个
        assert ALLOWED_TRANSITIONS[phase] == frozenset({SPRINT_PHASES[index + 1]}), phase
    assert ALLOWED_TRANSITIONS["follow_up"] == frozenset()


def test_advance_full_chain() -> None:
    current = "created"
    for target in SPRINT_PHASES[1:]:
        current = advance(current, target)
    assert current == "follow_up"


def test_advance_idempotent_same_phase() -> None:
    assert advance("learning", "learning") == "learning"


def test_advance_rejects_all_illegal_pairs() -> None:
    """穷举 (current, target) 全组合：非法对必须抛 IllegalSprintTransition。"""
    from app.domain.sprint_state import IllegalSprintTransition

    total_illegal = 0
    for current in SPRINT_PHASES:
        for target in SPRINT_PHASES:
            legal = target == current or target in ALLOWED_TRANSITIONS[current]
            if legal:
                assert can_advance(current, target)
                continue
            total_illegal += 1
            assert not can_advance(current, target)
            with pytest.raises(IllegalSprintTransition):
                advance(current, target)
    # 全非法对数量 = 全组合 - 合法对（主线 10 步相邻 + 幂等 11 态）
    assert total_illegal == len(SPRINT_PHASES) ** 2 - (len(SPRINT_PHASES) - 1) - len(SPRINT_PHASES)


def test_advance_rejects_unknown_phase() -> None:
    from app.domain.sprint_state import IllegalSprintTransition

    with pytest.raises(IllegalSprintTransition):
        advance("created", "mastered")  # 未知阶段名（掌握度是概念状态不是 Sprint 阶段）
    with pytest.raises(IllegalSprintTransition):
        advance("unknown", "created")


def test_pause_resume_rules() -> None:
    from app.domain.sprint_state import IllegalSprintTransition

    # created/follow_up 不可暂停；进行中阶段可暂停
    with pytest.raises(IllegalSprintTransition):
        check_pausable("created", paused=False)
    with pytest.raises(IllegalSprintTransition):
        check_pausable("follow_up", paused=False)
    for phase in PAUSABLE_PHASES:
        check_pausable(phase, paused=False)  # 全部合法
    # 已暂停状态下重复 pause 幂等合法
    check_pausable("learning", paused=True)
    # 未暂停不可恢复
    with pytest.raises(IllegalSprintTransition):
        check_resumable("learning", paused=False)
    check_resumable("learning", paused=True)


def test_replannable_phases() -> None:
    from app.domain.sprint_state import IllegalSprintTransition

    # 计划确认前可修改范围；进入学习后必须新开 Sprint
    for phase in ("contract_confirmed", "baseline_assessment", "concept_map_review", "plan_confirmed"):
        check_replannable(phase)
    for phase in ("created", "learning", "targeted_practice", "transfer_check", "posttest", "report", "follow_up"):
        with pytest.raises(IllegalSprintTransition):
            check_replannable(phase)


def test_sprint_window_expiry() -> None:
    window = SprintWindow(started_at=1000.0, budget_hours=48.0)
    assert not window.is_expired(1000.0 + 47 * 3600)
    assert window.is_expired(1000.0 + 48 * 3600 + 1)
