"""M1-05 layout normalize：parser 原始块 -> 规范化 blocks。

验收（backlog）：标题、段落、表格、公式、页码/slide 定位进入 blocks；公式转 LaTeX。
"""
from __future__ import annotations

import re

from app.parsing.base import ParsedBlock

BLOCK_TYPES = frozenset({"heading", "paragraph", "table", "formula", "page_marker"})

_SYMBOL_MAP = {
    "α": "alpha",
    "β": "beta",
    "γ": "gamma",
    "δ": "delta",
    "ε": "epsilon",
    "θ": "theta",
    "λ": "lambda",
    "μ": "mu",
    "π": "pi",
    "ρ": "rho",
    "σ": "sigma",
    "φ": "phi",
    "ω": "omega",
    "×": "times",
    "÷": "div",
    "±": "pm",
    "≤": "leq",
    "≥": "geq",
    "≠": "neq",
    "≈": "approx",
    "∞": "infty",
    "∑": "sum",
    "∏": "prod",
    "∫": "int",
    "√": "sqrt",
    "→": "to",
    "∂": "partial",
}
_PAGE_LINE = re.compile(r"^\s*(?:page|p\.?|第)\s*(\d+)\s*(?:页)?\s*$", re.IGNORECASE)
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_BS = "\\"
_MATH_DELIM = re.compile(
    f"{re.escape(_BS + chr(40))}(.+?){re.escape(_BS + chr(41))}"
    f"|{re.escape(_BS + chr(91))}(.+?){re.escape(_BS + chr(93))}"
    f"|{re.escape(chr(36) * 2)}(.+?){re.escape(chr(36) * 2)}",
    re.DOTALL,
)


def to_latex(text: str) -> str:
    """unicode 数学符号与定界符归一为 LaTeX。"""
    result = text
    for symbol, command in _SYMBOL_MAP.items():
        result = result.replace(symbol, _BS + command)
    result = _MATH_DELIM.sub(lambda m: m.group(1) or m.group(2) or m.group(3) or "", result)
    return result.strip()


def parse_markdown_table(text: str) -> list[list[str]] | None:
    """markdown 表格 -> 结构化 rows；非表格返回 None。"""
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if len(lines) < 2 or not all(_TABLE_ROW.match(line) for line in lines):
        return None
    rows = [
        [cell.strip() for cell in _TABLE_ROW.match(line).group(1).split("|")]
        for line in lines
    ]
    separator = rows[1]
    if all(set(cell) <= {"-", ":", ""} for cell in separator):
        rows.pop(1)
    return rows


def normalize_blocks(blocks: list[ParsedBlock]) -> list[ParsedBlock]:
    """规范化：类型归一、页码/slide 继承、公式 LaTeX 化、表格结构化。"""
    normalized: list[ParsedBlock] = []
    current_page: int | None = None
    current_slide: int | None = None
    for block in blocks:
        kind = block.type if block.type in BLOCK_TYPES else "paragraph"
        # 表格保留换行结构，其余块折叠空白
        text = (
            block.text.strip()
            if kind == "table"
            else " ".join(block.text.split())
        )
        if not text and kind != "page_marker":
            continue
        page = block.page
        slide = block.slide
        if kind == "page_marker":
            if page is None:
                page = _extract_page_number(text)
            current_page = page
            normalized.append(ParsedBlock(type="page_marker", text=text, page=page))
            continue
        page = page if page is not None else current_page
        slide = slide if slide is not None else current_slide
        meta = dict(block.meta)
        if kind == "table":
            rows = parse_markdown_table(text)
            if rows:
                meta["rows"] = rows
        if kind == "formula":
            text = to_latex(text)
            meta["latex"] = True
        normalized.append(
            ParsedBlock(type=kind, text=text, page=page, slide=slide, meta=meta)
        )
    return normalized


def _extract_page_number(text: str) -> int | None:
    match = _PAGE_LINE.match(text)
    return int(match.group(1)) if match else None
