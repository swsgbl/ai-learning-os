"""测试注入用 fake parser：可配置输出与失败模式。"""
from __future__ import annotations

from app.parsing.base import ParsedBlock, ParsedDocument, ParserUnavailable


class FakeParser:
    name = "fake"
    supported_media_types = frozenset({"pdf", "docx", "slide", "video", "audio", "dataset"})

    def __init__(
        self,
        name: str = "fake",
        blocks: list[ParsedBlock] | None = None,
        error: Exception | None = None,
        unavailable: bool = False,
    ) -> None:
        self.name = name
        self._blocks = blocks or [ParsedBlock(type="paragraph", text="fake body", page=1)]
        self._error = error
        self.unavailable = unavailable
        self.calls = 0

    def parse(self, data: bytes, media_type: str) -> ParsedDocument:
        self.calls += 1
        if self.unavailable:
            raise ParserUnavailable("fake marked unavailable")
        if self._error:
            raise self._error
        return ParsedDocument(
            blocks=list(self._blocks),
            parser_name=self.name,
            media_type=media_type,
            page_count=1,
        )
