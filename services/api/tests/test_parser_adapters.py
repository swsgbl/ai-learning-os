"""M1-04 parser adapter framework: registry fallback, fake injection, failure switch."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.parsing.base import ParserError, ParserUnavailable
from app.parsing.fake import FakeParser
from app.parsing.registry import ParserRegistry
from tests._stub import JsonDatasetParserStub

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _client():
    return TestClient(create_app(SQLITE_URL))


def _upload_json(client, payload: bytes) -> dict:
    resp = client.post(
        "/api/v1/resources/upload",
        files={"file": ("grades.json", payload, "application/json")},
    )
    assert resp.status_code == 201
    return resp.json()


def test_registry_skips_unavailable():
    registry = ParserRegistry()
    registry.register("docling", lambda: (_ for _ in ()).throw(ParserUnavailable("no dep")))
    registry.register_instance(FakeParser(name="fake"))
    assert registry.available() == ["fake"]
    assert registry.select("pdf").name == "fake"


def test_registry_prefer_named_parser():
    registry = ParserRegistry()
    registry.register_instance(FakeParser())
    registry.register_instance(JsonDatasetParserStub())
    assert registry.select("pdf", prefer="fake").name == "fake"


def test_registry_prefer_unknown_raises():
    registry = ParserRegistry()
    registry.register_instance(FakeParser())
    with pytest.raises(ParserUnavailable):
        registry.select("pdf", prefer="missing")


def test_registry_rejects_wrong_media_type():
    registry = ParserRegistry()
    registry.register_instance(JsonDatasetParserStub())
    with pytest.raises(ParserError):
        registry.select("pdf", prefer="json-stub")


def test_api_parse_json_dataset_end_to_end():
    with _client() as client:
        body = _upload_json(client, b'[{"q": "1+1"}, {"q": "2+2"}]')
        result = client.post(f"/api/v1/resources/{body['id']}/parse")
        assert result.status_code == 200
        data = result.json()
        assert data == {
            "resource_id": body["id"],
            "status": "parsed",
            "parser_name": "json-dataset",
            "block_count": 2,
            "page_count": 0,
            "table_count": 0,
            "formula_count": 0,
        }
        fetched = client.get(f"/api/v1/resources/{body['id']}").json()
        assert fetched["parse_status"] == "parsed"


def test_api_parse_failure_persists_error():
    with _client() as client:
        body = _upload_json(client, b"not-json{{{")
        result = client.post(f"/api/v1/resources/{body['id']}/parse")
        assert result.status_code == 422
        fetched = client.get(f"/api/v1/resources/{body['id']}").json()
        assert fetched["parse_status"] == "failed"
        assert fetched["parse_error"]


def test_api_switch_parser_after_failure():
    """prompt pack C：失败后可换 parser 重跑（?parser= 指定）。"""
    with _client() as client:
        body = _upload_json(client, b'{"k": "v"}')
        app = client.app
        failing = ParserRegistry()
        failing.register_instance(
            FakeParser(name="json-dataset", error=ParserError("模拟坏 parser"))
        )
        app.state.parsers = failing
        assert client.post(f"/api/v1/resources/{body['id']}/parse").status_code == 422

        good = ParserRegistry()
        good.register_instance(JsonDatasetParserStub())
        good.register_instance(FakeParser(name="fake"))
        app.state.parsers = good
        result = client.post(f"/api/v1/resources/{body['id']}/parse?parser=json-stub")
        assert result.status_code == 200
        assert result.json()["parser_name"] == "json-stub"
        fetched = client.get(f"/api/v1/resources/{body['id']}").json()
        assert fetched["parse_status"] == "parsed"
        assert fetched["parser_name"] == "json-stub"


def test_api_parse_fake_injection_and_404():
    with _client() as client:
        body = _upload_json(client, b'{"a": 1}')
        client.app.state.parsers = ParserRegistry()
        client.app.state.parsers.register_instance(FakeParser(name="fake"))
        result = client.post(f"/api/v1/resources/{body['id']}/parse?parser=fake")
        assert result.status_code == 200
        assert result.json()["parser_name"] == "fake"
        assert client.post("/api/v1/resources/res_missing/parse").status_code == 404
