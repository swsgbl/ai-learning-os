"""M1-08 quality report acceptance: pages/blocks/formulas/tables/OCR/anomalous pages."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.parsing.fake import FakeParser
from app.parsing.quality import build_quality_report
from app.parsing.registry import ParserRegistry

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
NL = chr(10)


def test_report_flags_missing_pages_and_counts():
    report = build_quality_report(
        {"page_count": 3, "block_count": 5, "ocr_confidence": 0.93},
        [
            {"page_start": 1, "page_end": 1, "block_types": ["paragraph"]},
            {"page_start": 2, "page_end": 2, "block_types": ["formula", "table"]},
        ],
        parser_name="fake",
        parse_status="parsed",
    )
    assert report == {
        "parse_status": "parsed",
        "parser_name": "fake",
        "page_count": 3,
        "block_count": 5,
        "chunk_count": 2,
        "formula_count": 1,
        "table_count": 1,
        "ocr_confidence": 0.93,
        "anomalous_pages": [3],
    }


def test_report_without_ocr_or_pages():
    report = build_quality_report(None, [], parse_status="failed")
    assert report["ocr_confidence"] is None
    assert report["anomalous_pages"] == []
    assert report["chunk_count"] == 0


def test_quality_endpoint_end_to_end():
    with TestClient(create_app(SQLITE_URL)) as client:
        uploaded = client.post(
            "/api/v1/resources/upload",
            files={"file": ("doc.csv", b"x", "text/csv")},
        ).json()
        # 未解析：pending 报告
        empty = client.get(f"/api/v1/resources/{uploaded['id']}/quality").json()
        assert empty["parse_status"] == "pending"

        # 注入带 OCR 置信度与页声明的 fake parser
        registry = ParserRegistry()
        registry.register_instance(FakeParser(blocks=[
            ParsedBlockForTest(type="page_marker", text="Page 1", page=1),
            ParsedBlockForTest(type="paragraph", text="content", page=1),
        ]))
        client.app.state.parsers = registry
        parsed = client.post(f"/api/v1/resources/{uploaded['id']}/parse")
        assert parsed.status_code == 200

        report = client.get(f"/api/v1/resources/{uploaded['id']}/quality").json()
        assert report["parse_status"] == "parsed"
        assert report["chunk_count"] >= 1
        assert report["block_count"] >= 1
        assert client.get("/api/v1/resources/res_missing/quality").status_code == 404


# 局部 helper：带 page 的块（避免与测试模块顶部 import 耦合）
from app.parsing.base import ParsedBlock as ParsedBlockForTest


def test_ocr_confidence_flows_through_pipeline():
    """parser 的 ocr_confidence 经 parse_metrics 透传到质量报告。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        uploaded = client.post(
            "/api/v1/resources/upload",
            files={"file": ("scan.csv", b"y", "text/csv")},
        ).json()
        registry = ParserRegistry()
        registry.register_instance(FakeParser(ocr_confidence=0.87))
        client.app.state.parsers = registry
        assert client.post(f"/api/v1/resources/{uploaded['id']}/parse").status_code == 200
        report = client.get(f"/api/v1/resources/{uploaded['id']}/quality").json()
        assert report["ocr_confidence"] == 0.87
