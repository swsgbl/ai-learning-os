"""Docling adapter：延迟导入，未安装时 ParserUnavailable（部署按需安装）。"""
from __future__ import annotations

from app.parsing.base import ParsedBlock, ParsedDocument, ParserError, ParserUnavailable


class DoclingParser:
    name = "docling"
    supported_media_types = frozenset({"pdf", "docx", "slide"})

    def __init__(self) -> None:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as cause:  # pragma: no cover - 依赖未装的部署
            raise ParserUnavailable("docling 未安装") from cause
        self._converter = DocumentConverter()

    def parse(self, data: bytes, media_type: str) -> ParsedDocument:
        try:
            import tempfile
            from pathlib import Path

            suffix = {"pdf": ".pdf", "docx": ".docx", "slide": ".pptx"}[media_type]
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
                handle.write(data)
                path = Path(handle.name)
            try:
                result = self._converter.convert(str(path))
                doc = result.document
            finally:
                path.unlink(missing_ok=True)
        except ParserUnavailable:
            raise
        except Exception as cause:
            raise ParserError(f"docling 解析失败: {cause}") from cause
        blocks: list[ParsedBlock] = []
        for item in doc.iterate_items():
            text = getattr(item, "text", "") or ""
            if not text.strip():
                continue
            kind = {"texts": "paragraph", "titles": "heading", "tables": "table"}.get(
                getattr(item, "label", "") or "texts", "paragraph"
            )
            page = getattr(getattr(item, "prov", [None])[0], "page", None) if getattr(item, "prov", None) else None
            blocks.append(ParsedBlock(type=kind, text=text, page=page))
        return ParsedDocument(
            blocks=blocks,
            parser_name=self.name,
            media_type=media_type,
            page_count=getattr(doc, "page_count", 0) or 0,
            table_count=sum(1 for b in blocks if b.type == "table"),
        )
