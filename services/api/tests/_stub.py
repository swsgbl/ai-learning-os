from app.parsing.base import ParsedBlock, ParsedDocument


class JsonDatasetParserStub:
    name = "json-stub"
    supported_media_types = frozenset({"dataset"})

    def parse(self, data: bytes, media_type: str) -> ParsedDocument:
        return ParsedDocument(
            blocks=[ParsedBlock(type="paragraph", text="stub")],
            parser_name=self.name,
            media_type=media_type,
        )
