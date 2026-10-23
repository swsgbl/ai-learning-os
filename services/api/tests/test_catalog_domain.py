"""M18-001 课程目录域层测试：路径归一化/分片归组/同步汇总（审计 Prompt 1）。"""
from __future__ import annotations

import pytest

from app.domain.catalog import (
    CatalogSyncError,
    group_parts,
    normalize_path,
    summarize_sync,
)


def test_normalize_basic_textbook_path() -> None:
    entry = normalize_path("初中/数学/人教版/七年级上册.pdf")
    assert entry is not None
    assert entry.education_stage == "初中"
    assert entry.subject == "数学"
    assert entry.edition_hint == "人教版"
    assert entry.is_part is False
    assert entry.title == "七年级上册.pdf"


def test_normalize_stage_alias_variant() -> None:
    entry = normalize_path("初中（五·四学制）/数学/六年级下册.pdf")
    assert entry is not None
    assert entry.education_stage == "初中"  # 别名规整
    assert entry.subject == "数学"


def test_normalize_university_math() -> None:
    entry = normalize_path("大学/高等数学/同济第七版上册.pdf")
    assert entry is not None
    assert entry.education_stage == "大学"
    assert entry.subject == "高等数学"
    assert entry.volume == "上册"


def test_normalize_non_pdf_ignored() -> None:
    assert normalize_path("README.md") is None
    assert normalize_path("初中/数学/目录") is None  # 目录节点
    assert normalize_path("初中/讲义.docx") is None


def test_normalize_does_not_invent_subject() -> None:
    """未知学科不猜：subject 留空（审计：不虚构元数据）。"""
    entry = normalize_path("高中/通用技术/必修一.pdf")
    assert entry is not None
    assert entry.subject == ""
    assert entry.volume == "必修一"


def test_part_files_grouped_numerically() -> None:
    """分片归组：数值序（非字典序）+ 逻辑路径去 .N（审计 02 §4.2）。"""
    entries = [
        normalize_path("大学/线性代数/课本.pdf.2"),
        normalize_path("大学/线性代数/课本.pdf.10"),
        normalize_path("大学/线性代数/课本.pdf.1"),
    ]
    assert all(e is not None for e in entries)
    grouped = group_parts([e for e in entries if e is not None])
    assert len(grouped) == 1
    group = grouped[0]
    assert group.source_path == "大学/线性代数/课本.pdf"
    assert [p.part_index for p in group.parts] == [1, 2, 10]  # 数值序
    assert group.parts[0].source_path.endswith(".pdf.1")


def test_missing_part_gap_preserved() -> None:
    """缺片如实保留缺口（完整性校验属导入阶段——目录层不虚构完整）。"""
    entries = [
        normalize_path("a/书.pdf.1"),
        normalize_path("a/书.pdf.3"),
    ]
    grouped = group_parts([e for e in entries if e])
    assert [p.part_index for p in grouped[0].parts] == [1, 3]


def test_standalone_pdf_and_parts_coexist() -> None:
    entries = [
        normalize_path("小学/语文/一年级上.pdf"),
        normalize_path("初中/数学/大书.pdf.1"),
        normalize_path("初中/数学/大书.pdf.2"),
    ]
    grouped = group_parts([e for e in entries if e])
    paths = {g.source_path for g in grouped}
    assert paths == {"小学/语文/一年级上.pdf", "初中/数学/大书.pdf"}


def test_summarize_sync_counts_discovered() -> None:
    tree = [
        ("初中/数学/人教版/七年级上.pdf", "blob-1", 1024),
        ("初中/数学/人教版/七年级上.pdf.1", "blob-2", None),
        ("初中/数学/人教版/七年级上.pdf.2", "blob-3", None),
        ("README.md", "blob-4", 100),  # 非 PDF 不计
    ]
    outcome = summarize_sync("chinatextbook", "abc123", tree)
    assert outcome.counters["discovered"] == 3
    # 归组后 1 个组条目
    assert len(outcome.entries) == 1
    assert outcome.entries[0].parts and len(outcome.entries[0].parts) == 2
    assert "discovered=3" in outcome.counter_line()


def test_summarize_sync_requires_pinned_sha() -> None:
    """空 commit_sha 拒绝（审计：不跟随浮动 master）。"""
    with pytest.raises(CatalogSyncError, match="pin"):
        summarize_sync("chinatextbook", "", [("a/b.pdf", "b1", 1)])
