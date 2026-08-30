"""M1-08 解析质量报告：页数/块数/公式/表格/OCR 置信度/异常页。"""
from __future__ import annotations

from typing import Any


def build_quality_report(
    parse_metrics: dict[str, Any] | None,
    chunks: list[dict],
    parser_name: str | None = None,
    parse_status: str = "pending",
) -> dict[str, Any]:
    """聚合解析质量指标；异常页 = 已声明页数中无任何内容块的页。"""
    metrics = parse_metrics or {}
    declared_pages = int(metrics.get("page_count") or 0)
    content_pages: set[int] = set()
    formula_count = 0
    table_count = 0
    for chunk in chunks:
        types = chunk.get("block_types") or []
        formula_count += 1 if "formula" in types else 0
        table_count += 1 if "table" in types else 0
        for page in (chunk.get("page_start"), chunk.get("page_end")):
            if page is not None:
                content_pages.add(int(page))
    anomalous_pages = sorted(
        set(range(1, declared_pages + 1)) - content_pages
    ) if declared_pages else []
    return {
        "parse_status": parse_status,
        "parser_name": parser_name,
        "page_count": declared_pages,
        "block_count": int(metrics.get("block_count") or 0),
        "chunk_count": len(chunks),
        "formula_count": formula_count,
        "table_count": table_count,
        "ocr_confidence": metrics.get("ocr_confidence"),
        "anomalous_pages": anomalous_pages,
    }
