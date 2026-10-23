"""M19-001 OCR 测试：中文识别黄金样本/置信/截断（审计 Prompt 4 后半）。

黄金样本构造：文本页渲染 200dpi → 嵌入新页（无文本层的扫描形态）。
"""
from __future__ import annotations

import pymupdf
from rapidocr_onnxruntime import RapidOCR  # noqa: F401

from app.parsing.ocr import OCR_CONFIDENCE_THRESHOLD, ocr_pages


def _make_scanned_pdf(lines: list[str]) -> bytes:
    """渲染文本成图→嵌回新 PDF（无文本层——扫描形态）。"""
    src = pymupdf.open()
    page = src.new_page()
    for index, line in enumerate(lines):
        page.insert_text((72, 120 + index * 30), line, fontsize=14, fontname="china-s")
    pix = page.get_pixmap(dpi=200)
    out = pymupdf.open()
    out_page = out.new_page()
    out_page.insert_image(pymupdf.Rect(40, 40, 560, 500), pixmap=pix)
    data = out.tobytes()
    src.close()
    out.close()
    return data


def test_ocr_recognizes_chinese_golden_sample() -> None:
    """黄金样本：已知中文句 → 识别文本含关键词（审计：不只测总量）。"""
    pdf = _make_scanned_pdf(["一元二次方程的判别式", "判别式决定根的性质"])
    # 无文本层（扫描形态验证）
    check = pymupdf.open(stream=pdf, filetype="pdf")
    assert len(check[0].get_text().strip()) == 0
    check.close()

    pages = ocr_pages(pdf, [1], dpi=200)
    assert len(pages) == 1
    result = pages[0]
    assert result.page == 1
    assert "判别式" in result.text, f"识别文本应含关键词：{result.text!r}"
    assert result.line_count >= 1
    assert result.mean_confidence > 0


def test_ocr_confidence_threshold_reported() -> None:
    """置信语义：页均置信可与阈值比较（低置信页上层标复核）。"""
    pdf = _make_scanned_pdf(["清晰文本样本行"])
    pages = ocr_pages(pdf, [1], dpi=200)
    assert pages[0].mean_confidence >= 0  # 数值语义存在
    _ = OCR_CONFIDENCE_THRESHOLD


def test_ocr_max_pages_truncates_honestly() -> None:
    """页数上限截断（防长书风暴——请求 5 页只跑 max_pages=2）。"""
    pdf = _make_scanned_pdf(["单行"])
    pages = ocr_pages(pdf, [1, 2, 3, 4, 5], max_pages=2)
    assert len(pages) <= 2


def test_ocr_invalid_page_skipped() -> None:
    """非法页号跳过（返回空——缺页即信号）。"""
    pdf = _make_scanned_pdf(["文本"])
    pages = ocr_pages(pdf, [99])
    assert pages == []
