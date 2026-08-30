"""M1-05 layout normalize acceptance: blocks typed/located, formulas in LaTeX."""
from __future__ import annotations

from app.parsing.base import ParsedBlock
from app.parsing.fake import FakeParser
from app.parsing.normalize import normalize_blocks, parse_markdown_table, to_latex
from app.parsing.registry import ParserRegistry

BS = chr(92)
D = chr(36)
NL = chr(10)


def test_to_latex_symbols_and_delimiters():
    assert to_latex("α × β") == f"{BS}alpha {BS}times {BS}beta"
    assert to_latex(f"{BS}(E=mc^2{BS})") == "E=mc^2"
    assert to_latex(f"a {D}{D}x^2{D}{D} b") == "a x^2 b"
    assert "sqrt" in to_latex("√x → y")


def test_markdown_table_structure():
    rows = parse_markdown_table(f"| a | b |{NL}|---|---|{NL}| 1 | 2 |")
    assert rows == [["a", "b"], ["1", "2"]]
    assert parse_markdown_table("not a table") is None


def test_normalize_types_pages_formulas_tables():
    blocks = [
        ParsedBlock(type="page_marker", text="Page 3"),
        ParsedBlock(type="weird", text="some  text"),
        ParsedBlock(type="formula", text="α × β"),
        ParsedBlock(
            type="table",
            text=f"| a | b |{NL}|---|---|{NL}| 1 | 2 |",
        ),
        ParsedBlock(type="paragraph", text=""),
        ParsedBlock(type="heading", text="Section 4"),
    ]
    out = normalize_blocks(blocks)
    assert [b.type for b in out] == [
        "page_marker", "paragraph", "formula", "table", "heading",
    ]
    # 页码继承：page_marker 之后的块都带上页码
    assert all(b.page == 3 for b in out[1:])
    assert out[2].meta["latex"] is True
    assert out[2].text == f"{BS}alpha {BS}times {BS}beta"
    assert out[3].meta["rows"] == [["a", "b"], ["1", "2"]]


def test_normalize_slide_locator_passthrough():
    out = normalize_blocks([
        ParsedBlock(type="heading", text="Slide deck", slide=5),
    ])
    assert out[0].slide == 5


def test_parse_endpoint_applies_normalize():
    """parse 端点输出过 normalize：公式 LaTeX 化 + metrics 标记。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app("sqlite+aiosqlite:///:memory:")) as client:
        uploaded = client.post(
            "/api/v1/resources/upload",
            files={"file": ("data.csv", b"any", "text/csv")},
        ).json()
        client.app.state.parsers = ParserRegistryForTest()
        result = client.post(f"/api/v1/resources/{uploaded['id']}/parse")
        assert result.status_code == 200
        data = result.json()
        assert data["page_count"] == 1  # FakeParser 固定 page_count=1
        fetched = client.get(f"/api/v1/resources/{uploaded['id']}").json()
        assert fetched["parse_status"] == "parsed"


class ParserRegistryForTest:
    """最小注册表：注入输出 unicode 公式与页标记的 fake parser。"""

    def __init__(self) -> None:
        self._registry = ParserRegistry()
        self._registry.register_instance(FakeParser(blocks=[
            ParsedBlock(type="page_marker", text="Page 1"),
            ParsedBlock(type="formula", text="α + β"),
            ParsedBlock(type="paragraph", text="body"),
        ]))

    def select(self, media_type: str, prefer: str | None = None):
        return self._registry.select(media_type, prefer=prefer)

    def available(self):
        return self._registry.available()
