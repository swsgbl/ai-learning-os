"""M18-004 PyMuPDF adapter（审计升级包 Prompt 4：文本型 PDF 首选路径）。

设计边界（审计 02 §4.3）：
- 文本层逐页抽取（page/slide 定位保留——ParsedBlock.page）；扫描页
  （无文本层）**不虚构**：低文本页计入 metrics['low_text_pages']，
  上层标记 needs_ocr（OCR 属后续重基建——PaddleOCR 评估另立任务）；
- PDF outline（get_toc）透出为 metrics['toc']——章节树候选的结构化
  来源（Prompt 5 的 course_unit 候选输入，不依赖 LLM）；
- 数学符号不做「静默归一」——原文文本原样保留（负号/上标由上层
  渲染负责）；
- 延迟导入：未安装时 ParserUnavailable（与 DoclingParser 同款部署
  按需模式）。
"""
from __future__ import annotations

from app.parsing.base import ParsedBlock, ParsedDocument, ParserError, ParserUnavailable

#: 单页文本量低于该值（字符）视为低文本页（扫描候选——如实计数）
LOW_TEXT_PAGE_THRESHOLD = 24


class PyMuPdfParser:
    name = "pymupdf"
    supported_media_types = frozenset({"pdf"})

    def __init__(self) -> None:
        try:
            import pymupdf  # noqa: F401
        except ImportError as cause:  # pragma: no cover - 部署差异
            raise ParserUnavailable("pymupdf 未安装") from cause

    def parse(self, data: bytes, media_type: str) -> ParsedDocument:
        if media_type != "pdf":
            raise ParserError(f"pymupdf 只支持 pdf（收到 {media_type}）")
        import pymupdf

        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as cause:  # pymupdf 异常族较杂——统一 ParserError
            raise ParserError(f"PDF 打开失败：{type(cause).__name__}") from cause
        try:
            blocks: list[ParsedBlock] = []
            low_text_pages = 0
            total_chars = 0
            for page_number, page in enumerate(doc, start=1):
                text = page.get_text("text").strip()
                total_chars += len(text)
                if len(text) < LOW_TEXT_PAGE_THRESHOLD:
                    low_text_pages += 1
                    blocks.append(
                        ParsedBlock(
                            type="page_marker",
                            text=text or "（低文本页——扫描候选，需 OCR）",
                            page=page_number,
                        )
                    )
                    continue
                # 段落级切分（空行分隔）；heading 判定留给上层（字号信息
                # 在 dict 模式——MVP 文本层不猜层级，如实 paragraph）
                for paragraph in [p.strip() for p in text.split("\n\n") if p.strip()]:
                    blocks.append(ParsedBlock(type="paragraph", text=paragraph, page=page_number))
            toc = [
                {"level": entry[0], "title": entry[1], "page": entry[2]}
                for entry in doc.get_toc()
            ]
            page_count = doc.page_count
        finally:
            doc.close()
        return ParsedDocument(
            blocks=blocks,
            parser_name=self.name,
            media_type="pdf",
            page_count=page_count,
            metrics={
                "toc": toc,
                "low_text_pages": low_text_pages,
                "total_chars": total_chars,
                "avg_chars_per_page": round(total_chars / max(1, page_count)),
            },
        )
