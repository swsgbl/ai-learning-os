"""M18-006 概念候选域层测试：id 形态/关系枚举/置信夹取/幽灵端点（审计 Prompt 5）。"""
from __future__ import annotations

import pytest

from app.domain.concept_candidates import (
    ConceptCandidateError,
    validate_concept_candidate,
    validate_edge_candidate,
)


def test_concept_candidate_valid() -> None:
    cand = validate_concept_candidate(
        {"id": "math:quadratic-equation", "canonical_name": "一元二次方程",
         "subject": "数学", "difficulty": 3, "confidence": 0.8,
         "provenance": {"page": 42, "unit": "第二章"}}
    )
    assert cand.concept_id == "math:quadratic-equation"
    assert cand.confidence == 0.8
    assert cand.provenance["page"] == 42


def test_concept_id_pattern_enforced() -> None:
    """与 M3-02 ConceptNodeIn 同形态（大写/空格拒绝）。"""
    with pytest.raises(ConceptCandidateError, match="id 非法"):
        validate_concept_candidate({"id": "Bad ID", "canonical_name": "x"})
    with pytest.raises(ConceptCandidateError, match="id 非法"):
        validate_concept_candidate({"id": "", "canonical_name": "x"})


def test_missing_name_rejected() -> None:
    with pytest.raises(ConceptCandidateError, match="canonical_name"):
        validate_concept_candidate({"id": "a:b", "canonical_name": "  "})


def test_confidence_clamped_and_defaulted() -> None:
    assert validate_concept_candidate({"id": "a:b", "canonical_name": "x", "confidence": 1.7}).confidence == 1.0
    assert validate_concept_candidate({"id": "a:b", "canonical_name": "x", "confidence": "junk"}).confidence == 0.0


def test_difficulty_invalid_falls_back() -> None:
    assert validate_concept_candidate({"id": "a:b", "canonical_name": "x", "difficulty": 9}).difficulty == 3


def test_edge_valid_typed_relations() -> None:
    known = {"a", "b"}
    for relation in ("prerequisite_of", "similar_to", "often_confused_with"):
        edge = validate_edge_candidate({"from": "a", "to": "b", "relation": relation}, known)
        assert edge.relation == relation


def test_edge_ghost_endpoint_rejected() -> None:
    with pytest.raises(ConceptCandidateError, match="端点不在概念集"):
        validate_edge_candidate({"from": "a", "to": "ghost", "relation": "similar_to"}, {"a", "b"})


def test_edge_self_loop_and_bad_relation_rejected() -> None:
    known = {"a", "b"}
    with pytest.raises(ConceptCandidateError, match="自环"):
        validate_edge_candidate({"from": "a", "to": "a", "relation": "part_of"}, known)
    with pytest.raises(ConceptCandidateError, match="关系类型非法"):
        validate_edge_candidate({"from": "a", "to": "b", "relation": "next"}, known)
