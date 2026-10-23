"""M18-004 PyMuPDF parser 测试：页锚点/低文本页/outline/非 PDF 拒绝（审计 Prompt 4）。"""
from __future__ import annotations

import pymupdf
import pytest

from app.parsing.base import ParserError
from app.parsing.pymupdf_parser import PyMuPdfParser


def _make_pdf(pages: list[str], *, toc: list | None = None) -> bytes:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 96), text)
    if toc:
        doc.set_toc(toc)
    data = doc.tobytes()
    doc.close()
    return data


def test_parse_extracts_pages_with_anchors() -> None:
    pdf = _make_pdf(["第一章 概述 " + "正文内容。" * 40, "第二章 方法 " + "方法细节。" * 40])
    parsed = PyMuPdfParser().parse(pdf, "pdf")
    assert parsed.page_count == 2
    assert parsed.parser_name == "pymupdf"
    pages = {b.page for b in parsed.blocks}
    assert pages == {1, 2}
    assert all(b.type == "paragraph" for b in parsed.blocks if b.text.startswith("第"))


def test_low_text_page_counted_not_invented() -> None:
    """扫描候选页：如实计数（不虚构文本）。"""
    pdf = _make_pdf(["满页文本。" * 60, ""])  # 第二页空（扫描形态）
    parsed = PyMuPdfParser().parse(pdf, "pdf")
    assert parsed.metrics["low_text_pages"] == 1
    markers = [b for b in parsed.blocks if b.type == "page_marker"]
    assert len(markers) == 1 and markers[0].page == 2


def test_toc_extracted_for_course_candidates() -> None:
    """outline 透出（Prompt 5 章节树候选来源——不依赖 LLM）。"""
    pdf = _make_pdf(
        ["目录后的正文页。" * 30],
        toc=[[1, "第一章 集合与函数", 1], [2, "1.1 集合", 1], [1, "第二章 函数", 20]],
    )
    parsed = PyMuPdfParser().parse(pdf, "pdf")
    toc = parsed.metrics["toc"]
    assert toc[0] == {"level": 1, "title": "第一章 集合与函数", "page": 1}
    assert toc[2]["title"] == "第二章 函数"


def test_non_pdf_rejected() -> None:
    with pytest.raises(ParserError, match="只支持 pdf"):
        PyMuPdfParser().parse(b"not a pdf", "docx")


def test_corrupt_pdf_raises_parser_error() -> None:
    with pytest.raises(ParserError, match="打开失败"):
        PyMuPdfParser().parse(b"%PDF-broken", "pdf")
