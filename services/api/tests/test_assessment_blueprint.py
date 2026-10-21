"""M16-004 评估蓝图域层测试（升级方案 Phase 4 验收）。

核心：后测不复用前测原题（等值不重叠）；题量不足如实 partial 不虚报；
主观题不入蓝图；难度奇偶配平；阶段取题正确。
"""
from __future__ import annotations

import pytest

from app.domain.assessment_blueprint import (
    CandidateItem,
    blueprint_items,
    build_blueprint,
)


def _pool(concept: str, count: int, prefix: str = "q") -> list[CandidateItem]:
    return [
        CandidateItem(
            item_id=f"{prefix}{i}", concept_id=concept, difficulty=1 + i % 5, question_type="mcq"
        )
        for i in range(count)
    ]


def test_pretest_and_posttest_never_overlap() -> None:
    """方案验收：后测不可复用原题作为成功证据。"""
    bp = build_blueprint(_pool("c_sql", 8))
    assert bp.complete
    plan = bp.plans[0]
    assert set(plan.pretest_item_ids).isdisjoint(plan.posttest_item_ids)
    assert len(plan.pretest_item_ids) == 2
    assert len(plan.posttest_item_ids) == 2


def test_difficulty_balanced_between_phases() -> None:
    """奇偶交错分配：两阶段难度分布等值（MVP 等值策略）。"""
    items = [
        CandidateItem(f"q{i}", "c", difficulty=d, question_type="mcq")
        for i, d in enumerate([1, 1, 3, 3, 5, 5])
    ]
    bp = build_blueprint(items)
    plan = bp.plans[0]
    pre = {next(i.difficulty for i in items if i.item_id == q) for q in plan.pretest_item_ids}
    post = {next(i.difficulty for i in items if i.item_id == q) for q in plan.posttest_item_ids}
    # 排序后奇偶交错：两阶段各拿一个难度 1/3/5 档（2 题时各覆盖不同档）
    assert sum(pre) == sum(post)


def test_insufficient_pool_is_partial_not_fake_complete() -> None:
    """题量不足：如实 partial + gaps 说明（不虚报完整）。"""
    bp = build_blueprint(_pool("c_thin", 3))  # 需要 4 只有 3
    assert not bp.complete
    assert any("c_thin" in gap for gap in bp.gaps)
    plan = bp.plans[0]
    assert not plan.complete
    # 3 题仍不重叠尽量平均：2+1
    assert len(plan.pretest_item_ids) + len(plan.posttest_item_ids) == 3
    assert set(plan.pretest_item_ids).isdisjoint(plan.posttest_item_ids)


def test_essay_and_unknown_types_excluded() -> None:
    """主观题与判分器不认识的题型均不入蓝图（parser differential 修复回归）。

    M16-004b：此前 "single"/"multiple" 在蓝图白名单但 grading 不认识——
    会落入 return False 兜底被记为答错（虚假证据）。现在白名单以
    grading.normalize_type 为单一事实源，归一后不在客观集的一律排除。
    """
    pool = (
        _pool("c1", 4)
        + [CandidateItem("e1", "c1", question_type="essay")]
        + [CandidateItem("u1", "c1", question_type="single")]
        + [CandidateItem("u2", "c1", question_type="multiple")]
    )
    bp = build_blueprint(pool)
    all_ids = set(blueprint_items(bp, "pretest")) | set(blueprint_items(bp, "posttest"))
    assert "e1" not in all_ids
    assert "u1" not in all_ids  # grading 不认识的题不进蓝图
    assert "u2" not in all_ids
    # alias 归一后是客观题——正常收录
    pool_alias = _pool("c1", 2, "p") + [CandidateItem("a1", "c1", question_type="choice")]
    bp_alias = build_blueprint(pool_alias)
    assert len(bp_alias.plans[0].pretest_item_ids) + len(bp_alias.plans[0].posttest_item_ids) >= 1


def test_empty_pool_yields_empty_blueprint() -> None:
    bp = build_blueprint([])
    assert bp.plans == ()
    assert not bp.complete


def test_multiple_concepts_partitioned_independently() -> None:
    pool = _pool("c_a", 4, "a") + _pool("c_b", 6, "b")
    bp = build_blueprint(pool)
    by_concept = {p.concept_id: p for p in bp.plans}
    assert by_concept["c_a"].complete
    assert by_concept["c_b"].complete
    a_ids = set(by_concept["c_a"].pretest_item_ids) | set(by_concept["c_a"].posttest_item_ids)
    b_ids = set(by_concept["c_b"].pretest_item_ids) | set(by_concept["c_b"].posttest_item_ids)
    assert a_ids.isdisjoint(b_ids)


def test_blueprint_items_by_phase() -> None:
    bp = build_blueprint(_pool("c1", 4, "x") + _pool("c2", 4, "y"))
    pre = set(blueprint_items(bp, "pretest"))
    post = set(blueprint_items(bp, "posttest"))
    assert pre.isdisjoint(post)
    assert len(pre) == 4 and len(post) == 4
    with pytest.raises(ValueError):
        blueprint_items(bp, "practice")
