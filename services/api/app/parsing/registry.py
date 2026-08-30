"""parser 注册表：按 media_type 选择，Unavailable 自动跳过，支持指定重跑。"""
from __future__ import annotations

from collections.abc import Callable

from app.parsing.base import Parser, ParserError, ParserUnavailable
from app.parsing.docling import DoclingParser
from app.parsing.jsonparser import JsonDatasetParser


class ParserRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, Callable[[], Parser]] = {}
        self._instances: dict[str, Parser] = {}

    def register(self, name: str, factory: Callable[[], Parser]) -> None:
        self._entries[name] = factory
        self._instances.pop(name, None)

    def register_instance(self, parser: Parser) -> None:
        self._entries[parser.name] = lambda: parser
        self._instances[parser.name] = parser

    def _materialize(self, name: str) -> Parser:
        if name not in self._instances:
            self._instances[name] = self._entries[name]()
        return self._instances[name]

    def select(self, media_type: str, prefer: str | None = None) -> Parser:
        """prefer 指定名字（换 parser 重跑）；否则按注册顺序，跳过 Unavailable。"""
        if prefer:
            if prefer not in self._entries:
                raise ParserUnavailable(f"parser 未注册: {prefer}")
            parser = self._materialize(prefer)
            if media_type not in parser.supported_media_types:
                raise ParserError(f"{prefer} 不支持 {media_type}")
            return parser
        for name in self._entries:
            try:
                parser = self._materialize(name)
            except ParserUnavailable:
                continue
            if media_type in parser.supported_media_types:
                return parser
        raise ParserUnavailable(f"没有支持 {media_type} 的可用 parser")

    def available(self) -> list[str]:
        names = []
        for name in self._entries:
            try:
                self._materialize(name)
            except ParserUnavailable:
                continue
            names.append(name)
        return names


def make_default_registry() -> ParserRegistry:
    """真实部署默认注册表：docling（依赖未装自动降级）+ json-dataset。"""
    registry = ParserRegistry()
    registry.register("docling", DoclingParser)
    registry.register_instance(JsonDatasetParser())
    return registry
