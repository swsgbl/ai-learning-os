"""M16-002 掌握度证据状态机域层测试（升级方案 Phase 2 验收）。

覆盖：无证据 unknown；提示/未判分 → emerging；独立正确过半 → practicing；
多证据多上下文 → provisional；+延迟/迁移 → mastered；复测失败 → at_risk；
幂等重算；高信心错误信号；confidence 边界拒绝。
"""
from __future__ import annotations

import pytest

from app.domain.mastery import (
    AttemptEvidence,
    MasteryRuleError,
    derive_mastery,
    is_high_confidence_error,
)


def _e(
    attempt: str = "a1",
    correct: bool | None = True,
    concept: str = "c_sql_select",
    **kw: object,
) -> AttemptEvidence:
    return AttemptEvidence(attempt_id=attempt, concept_id=concept, correct=correct, **kw)  # type: ignore[arg-type]


def test_no_evidence_is_unknown() -> None:
    outcome = derive_mastery([])
    assert outcome.state == "unknown"
    assert outcome.evidence_count == 0


def test_hint_or_ungraded_only_is_emerging() -> None:
    evidence = [
        _e("a1", correct=None),  # 未判分（三态——不折算为对/错）
        _e("a2", correct=True, hints_used=2, independent=False),  # 提示下答对
        _e("a3", correct=False, hints_used=1, independent=False),
    ]
    assert derive_mastery(evidence).state == "emerging"


def test_solo_independent_correct_is_practicing() -> None:
    evidence = [
        _e("a1", correct=True, context="mcq:baseline"),
        _e("a2", correct=False, context="mcq:baseline"),
        _e("a3", correct=True, context="mcq:baseline"),
    ]
    outcome = derive_mastery(evidence)
    assert outcome.state == "practicing"  # 2/3 独立正确过半，但单上下文证据不足


def test_multi_context_independent_is_provisional_not_mastered() -> None:
    """方案验收：同一题型反复答对不能直接 mastered——需多上下文+延迟/迁移。"""
    same_context = [_e(f"a{i}", correct=True, context="mcq:practice") for i in range(5)]
    outcome = derive_mastery(same_context)
    assert outcome.state == "practicing"  # 单上下文再多也不到 provisional

    multi_context = [
        _e("a1", correct=True, context="mcq:baseline"),
        _e("a2", correct=True, context="essay:practice"),
        _e("a3", correct=True, context="apply:posttest"),
    ]
    outcome = derive_mastery(multi_context)
    assert outcome.state == "provisional"  # 3 独立正确 + 3 上下文，但无延迟/迁移
    assert outcome.delayed_or_transfer_correct == 0


def test_delayed_or_transfer_evidence_upgrades_to_mastered() -> None:
    evidence = [
        _e("a1", correct=True, context="mcq:baseline"),
        _e("a2", correct=True, context="essay:practice"),
        _e("a3", correct=True, context="apply:posttest"),
        _e("a4", correct=True, delayed=True, context="mcq:delayed"),
    ]
    outcome = derive_mastery(evidence)
    assert outcome.state == "mastered"
    assert outcome.delayed_or_transfer_correct == 1


def test_failed_delayed_retest_downgrades_to_at_risk() -> None:
    """mastered 后延迟复测失败 → at_risk（复测失败是有意义的降级）。"""
    built_up = [
        _e("a1", correct=True, context="mcq:baseline"),
        _e("a2", correct=True, context="essay:practice"),
        _e("a3", correct=True, context="apply:posttest"),
        _e("a4", correct=True, delayed=True, context="mcq:delayed"),
    ]
    assert derive_mastery(built_up).state == "mastered"
    failed_retest = built_up + [_e("a5", correct=False, delayed=True, context="mcq:delayed")]
    outcome = derive_mastery(failed_retest)
    assert outcome.state == "at_risk"


def test_early_failure_then_later_pass_stays_mastered() -> None:
    """时间序敏感：延迟失败发生在通过**之前**不降级（列表序=时间序）。"""
    evidence = [
        _e("a1", correct=True, context="mcq:baseline"),
        _e("a2", correct=True, context="essay:practice"),
        _e("a3", correct=True, context="apply:posttest"),
        _e("a4", correct=False, delayed=True, context="mcq:delayed"),  # 先失败
        _e("a5", correct=True, delayed=True, context="mcq:delayed"),  # 后通过
    ]
    assert derive_mastery(evidence).state == "mastered"


def test_derive_is_deterministic_idempotent() -> None:
    evidence = [
        _e("a1", correct=True, context="mcq:baseline"),
        _e("a2", correct=False, confidence=5, context="essay:practice"),
    ]
    first = derive_mastery(evidence)
    second = derive_mastery(list(evidence))
    assert first == second  # 可重算：同一证据恒同一输出


def test_high_confidence_error_signal() -> None:
    assert is_high_confidence_error(_e("a1", correct=False, confidence=4))
    assert is_high_confidence_error(_e("a1", correct=False, confidence=5))
    assert not is_high_confidence_error(_e("a1", correct=False, confidence=3))
    assert not is_high_confidence_error(_e("a1", correct=True, confidence=5))  # 答对不算
    assert not is_high_confidence_error(_e("a1", correct=False))  # 无信心值
    outcome = derive_mastery([_e("a1", correct=False, confidence=5)])
    assert outcome.high_confidence_errors == 1


def test_confidence_out_of_range_rejected() -> None:
    with pytest.raises(MasteryRuleError):
        _e("a1", confidence=0)
    with pytest.raises(MasteryRuleError):
        _e("a1", confidence=6)


def test_non_independent_correct_does_not_count_toward_provisional() -> None:
    """提示下答对不算独立证据（方案：提示作答与独立作答区分）。"""
    evidence = [
        _e("a1", correct=True, hints_used=2, independent=False, context="mcq:baseline"),
        _e("a2", correct=True, hints_used=1, independent=False, context="essay:practice"),
        _e("a3", correct=True, hints_used=3, independent=False, context="apply:posttest"),
        _e("a4", correct=True, delayed=True, independent=False),
    ]
    outcome = derive_mastery(evidence)
    assert outcome.independent_correct == 0
    assert outcome.state == "emerging"
