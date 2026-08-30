from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class ParsedBlock:
    """规范化块；locator 保留页码/slide 定位（M1-05 layout normalize 的输入）。"""

    type: str  # heading | paragraph | table | formula | page_marker
    text: str
    page: int | None = None
    slide: int | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class ParsedDocument:
    blocks: list[ParsedBlock]
    parser_name: str
    media_type: str
    page_count: int = 0
    table_count: int = 0
    formula_count: int = 0
    metrics: dict = field(default_factory=dict)


@runtime_checkable
class Parser(Protocol):
    """解析 adapter 统一接口；真实实现与 fake 同签名（prompt pack C）。"""

    name: str
    supported_media_types: frozenset[str]

    def parse(self, data: bytes, media_type: str) -> ParsedDocument: ...


class ParserError(Exception):
    """解析失败（内容问题，可换 parser 重跑）。"""


class ParserUnavailable(Exception):
    """parser 依赖未安装或不可用（部署差异，注册表自动跳过）。"""
