"""M1-06 chunk 切分与 Evidence 支撑：每个 chunk 可回到页码/slide。"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.parsing.base import ParsedBlock

DEFAULT_MAX_CHARS = 800
DEFAULT_OVERLAP = 100


@dataclass
class Chunk:
    text: str
    chunk_hash: str
    page_start: int | None
    page_end: int | None
    slide: int | None
    block_types: list[str] = field(default_factory=list)


def _blocks_text(blocks: list[ParsedBlock]) -> str:
    return "\n".join(block.text for block in blocks)


def chunk_blocks(
    blocks: list[ParsedBlock],
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """顺序聚合段落/标题；表格独立成 chunk；页码继承 normalize 后的 blocks。"""
    chunks: list[Chunk] = []
    buffer: list[ParsedBlock] = []
    current_page: int | None = None

    def _flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        chunks.append(_make_chunk(buffer))
        buffer = []

    def _make_chunk(source: list[ParsedBlock]) -> Chunk:
        import hashlib

        text = _blocks_text(source)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        pages = [b.page for b in source if b.page is not None]
        slides = [b.slide for b in source if b.slide is not None]
        return Chunk(
            text=text,
            chunk_hash=digest,
            page_start=min(pages) if pages else None,
            page_end=max(pages) if pages else None,
            slide=min(slides) if slides else None,
            block_types=sorted({b.type for b in source}),
        )

    for block in blocks:
        if block.page is None:
            block.page = current_page  # normalize 未跑时兜底继承
        current_page = block.page  # 显式页码推进兜底上下文
        if block.type == "page_marker":
            _flush()
            if block.page is not None:
                current_page = block.page
            continue
        if block.type == "table":
            _flush()
            chunks.append(_make_chunk([block]))
            continue
        if buffer and len(_blocks_text(buffer)) + len(block.text) + 1 > max_chars:
            _flush()
        buffer.append(block)
    _flush()
    return chunks
