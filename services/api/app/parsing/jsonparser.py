"""dataset(json) 真实解析实现（stdlib，无重依赖）。"""
from __future__ import annotations

import json

from app.parsing.base import ParsedBlock, ParsedDocument, ParserError


class JsonDatasetParser:
    name = "json-dataset"
    supported_media_types = frozenset({"dataset"})

    def parse(self, data: bytes, media_type: str) -> ParsedDocument:
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as cause:
            raise ParserError(f"JSON 解析失败: {cause}") from cause
        blocks: list[ParsedBlock] = []
        if isinstance(payload, list):
            for index, item in enumerate(payload, start=1):
                blocks.append(ParsedBlock(type="paragraph", text=json.dumps(item, ensure_ascii=False), meta={"row": index}))
        elif isinstance(payload, dict):
            for key, value in payload.items():
                blocks.append(ParsedBlock(type="heading", text=str(key)))
                blocks.append(ParsedBlock(type="paragraph", text=json.dumps(value, ensure_ascii=False)))
        else:
            raise ParserError("JSON 顶层必须是对象或数组")
        return ParsedDocument(
            blocks=blocks,
            parser_name=self.name,
            media_type=media_type,
            metrics={"block_count": len(blocks)},
        )
