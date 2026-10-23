"""M18-005 课程结构域层测试：嵌套栈/跳深收敛/非法拒绝（审计 Prompt 5）。"""
from __future__ import annotations

import pytest

from app.domain.course_structure import CourseStructureError, toc_to_candidates


def test_nested_tree_with_parents() -> None:
    toc = [
        {"level": 1, "title": "第一章 集合", "page": 1},
        {"level": 2, "title": "1.1 集合的概念", "page": 2},
        {"level": 2, "title": "1.2 集合的运算", "page": 8},
        {"level": 1, "title": "第二章 函数", "page": 20},
        {"level": 2, "title": "2.1 函数概念", "page": 21},
    ]
    units = toc_to_candidates(toc)
    assert [u.title for u in units] == [e["title"] for e in toc]
    # 嵌套：1.1/1.2 挂第一章；2.1 挂第二章
    by_seq = {u.sequence: u for u in units}
    assert by_seq[2].parent_sequence == 1
    assert by_seq[3].parent_sequence == 1
    assert by_seq[5].parent_sequence == 4
    assert by_seq[1].parent_sequence is None
    assert by_seq[1].unit_type == "chapter" and by_seq[2].unit_type == "section"
    assert by_seq[1].page == 1 and by_seq[3].page == 8


def test_level_jump_collapsed() -> None:
    """跳深（1→3）收敛为 +1（出版 outline 跳跃不虚构中间层）。"""
    toc = [
        {"level": 1, "title": "章", "page": 1},
        {"level": 3, "title": "小节", "page": 2},
    ]
    units = toc_to_candidates(toc)
    assert units[1].level == 2  # 收敛
    assert units[1].parent_sequence == 1


def test_return_to_shallow_level() -> None:
    toc = [
        {"level": 1, "title": "一", "page": 1},
        {"level": 2, "title": "一之一", "page": 2},
        {"level": 1, "title": "二", "page": 10},
    ]
    units = toc_to_candidates(toc)
    assert units[2].parent_sequence is None  # 回到顶层


def test_empty_toc_rejected() -> None:
    with pytest.raises(CourseStructureError, match="outline 为空"):
        toc_to_candidates([])


def test_invalid_entries_rejected() -> None:
    with pytest.raises(CourseStructureError, match="level 非法"):
        toc_to_candidates([{"level": 0, "title": "x", "page": 1}])
    with pytest.raises(CourseStructureError, match="缺少标题"):
        toc_to_candidates([{"level": 1, "title": "  ", "page": 1}])
