"""M16-003 教学微循环策略域层测试（升级方案 Phase 3 验收）。

覆盖：先尝试起步；骨架前进；六类错误标签→定向再教学；未判分不推进；
exit_ticket 完成；交错队列（未达标跳过/易混淆交替）；未知标签拒绝。
"""
from __future__ import annotations

import pytest

from app.domain.practice_loop import (
    AttemptSummary,
    ConceptForInterleave,
    PracticeLoopError,
    interleave_queue,
    next_step,
)


def test_loop_starts_with_first_attempt() -> None:
    decision = next_step([])
    assert decision.next_step == "first_attempt"
    assert "先尝试" in decision.rationale


def test_skeleton_advances_on_complete() -> None:
    decision = next_step(["first_attempt"])
    assert decision.next_step == "minimal_explanation"
    assert next_step(["minimal_explanation"]).next_step == "worked_example"
    assert next_step(["worked_example"]).next_step == "contrast_case"


def test_correct_answer_advances_answering_step() -> None:
    decision = next_step(["first_attempt"], AttemptSummary(correct=True))
    assert decision.next_step == "minimal_explanation"
    # 回忆步骤答对 → 下一步渐隐支架
    decision = next_step(["recall_practice"], AttemptSummary(correct=True))
    assert decision.next_step == "faded_practice"


def test_wrong_answer_without_tags_retries_same_step() -> None:
    decision = next_step(["independent_practice"], AttemptSummary(correct=False))
    assert decision.next_step == "independent_practice"
    assert decision.strategy == "retry"


@pytest.mark.parametrize(
    ("tag", "expected_action"),
    [
        ("missing_knowledge", "prerequisite_review"),
        ("confusion", "contrast_case"),
        ("procedure_error", "worked_example"),
        ("reasoning_gap", "minimal_explanation"),
        ("careless", "self_check_list"),
        ("material_failure", "simpler_source"),
    ],
)
def test_error_tag_routes_reteach_strategy(tag: str, expected_action: str) -> None:
    """六类错误标签 → 六种定向再教学动作（方案 Prompt 3 映射）。"""
    decision = next_step(
        ["independent_practice"], AttemptSummary(correct=False, error_tags=(tag,))
    )
    assert decision.next_step == expected_action
    assert decision.strategy == f"reteach:{tag}"
    assert decision.retry_after_step == "independent_practice"


def test_reteach_action_returns_to_recall_retry() -> None:
    """再教学动作完成 → 回到闭卷回忆重试（支架更细）。

    contrast_case 是骨架/动作双名步骤：无论从哪条路径到达，完成后下一步
    都是 recall_practice（骨架顺序天然等价于重试回退——同名冲突的语义
    兼容性在域层注释中声明）。纯动作（非骨架名）走显式重试回退。
    """
    decision = next_step(["contrast_case"])
    assert decision.next_step == "recall_practice"
    pure_action = next_step(["prerequisite_review"])
    assert pure_action.next_step == "recall_practice"
    assert pure_action.strategy == "retry_after_reteach"


def test_ungraded_attempt_holds_progress() -> None:
    """未判分（三态）不推进——诚实状态，不虚报完成。"""
    decision = next_step(["exit_ticket"], AttemptSummary(correct=None))
    assert decision.next_step == "exit_ticket"
    assert decision.strategy == "await_grading"


def test_exit_ticket_completes_loop() -> None:
    decision = next_step(["exit_ticket"], AttemptSummary(correct=True))
    assert decision.loop_complete is True
    assert decision.strategy == "loop_complete"


def test_unknown_step_or_tag_rejected() -> None:
    with pytest.raises(PracticeLoopError):
        next_step(["master_the_universe"])
    with pytest.raises(PracticeLoopError):
        next_step(["first_attempt"], AttemptSummary(correct=False, error_tags=("lazy",)))


def test_full_microcycle_e2e() -> None:
    """完整微循环走通：先试→解释→示例→对比→回忆→渐隐→独立→出口。

    中途一次「概念混淆」错误触发对比案例再教学后重试恢复（方案 E2E：
    诊断→练习→错误反馈→重试→独立终测的域层骨架）。
    """
    steps: list[str] = []
    # 1. 先尝试（答错：混淆）
    decision = next_step(steps, None)
    assert decision.next_step == "first_attempt"
    steps.append("first_attempt")
    decision = next_step(steps, AttemptSummary(correct=False, error_tags=("confusion",)))
    assert decision.next_step == "contrast_case"
    steps.append("contrast_case")
    # 2. 再教学后重试回忆（对）
    decision = next_step(steps)
    assert decision.next_step == "recall_practice"
    steps.append("recall_practice")
    decision = next_step(steps, AttemptSummary(correct=True, confidence=4))
    assert decision.next_step == "faded_practice"
    steps.append("faded_practice")
    # 3. 渐隐答对 → 独立练习
    decision = next_step(steps, AttemptSummary(correct=True))
    assert decision.next_step == "independent_practice"
    steps.append("independent_practice")
    decision = next_step(steps, AttemptSummary(correct=True))
    assert decision.next_step == "exit_ticket"
    steps.append("exit_ticket")
    final = next_step(steps, AttemptSummary(correct=True))
    assert final.loop_complete is True


def test_interleave_queue_purposeful_not_random() -> None:
    concepts = [
        ConceptForInterleave("c_join", "provisional", confusable_with=("c_subquery",)),
        ConceptForInterleave("c_subquery", "practicing"),
        ConceptForInterleave("c_select", "unknown"),  # 未达标——不参与
    ]
    plan = interleave_queue(concepts)
    assert "c_select" in plan.skipped
    assert set(plan.order) == {"c_join", "c_subquery"}
    # 易混淆对相邻（join 后立刻 subquery——交替判断训练）
    assert plan.order == ("c_join", "c_subquery")


def test_interleave_skips_all_when_none_ready() -> None:
    plan = interleave_queue([ConceptForInterleave("c1", "unknown")])
    assert plan.order == ()
    assert plan.skipped == ("c1",)
