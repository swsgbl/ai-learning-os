"""M1-06 chunk + evidence acceptance: locator traceability and evidence fields."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
NL = chr(10)


def _client():
    return TestClient(create_app(SQLITE_URL))


def _upload_and_parse(client, content: bytes, name="doc.json") -> dict:
    uploaded = client.post(
        "/api/v1/resources/upload",
        files={"file": (name, content, "application/json")},
    ).json()
    parsed = client.post(f"/api/v1/resources/{uploaded['id']}/parse")
    assert parsed.status_code == 200
    return uploaded


def test_chunks_have_page_locator_and_evidence_fields():
    with _client() as client:
        body = _upload_and_parse(client, b'[{"q": "1+1"}, {"q": "2+2"}, {"q": "3+3"}]')
        chunks = client.get(f"/api/v1/resources/{body['id']}/chunks").json()
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk["chunk_hash"]
            assert chunk["chunk_index"] >= 0
            assert chunk["text"]
        evidence = client.get(f"/api/v1/resources/{body['id']}/evidence").json()
        assert len(evidence) == len(chunks)
        for item in evidence:
            assert item["parser_name"] == "json-dataset"
            assert item["snippet_hash"]
            assert "page" in item["locator"]
            assert item["license_state"] == "UNKNOWN"


def test_reparse_replaces_chunks_idempotently():
    """换 parser 重跑语义：旧 chunk/evidence 全量替换，不残留。"""
    with _client() as client:
        body = _upload_and_parse(client, b'{"a": 1}')
        first = client.get(f"/api/v1/resources/{body['id']}/chunks").json()
        again = client.post(f"/api/v1/resources/{body['id']}/parse")
        assert again.status_code == 200
        second = client.get(f"/api/v1/resources/{body['id']}/chunks").json()
        assert [c["chunk_hash"] for c in first] == [c["chunk_hash"] for c in second]
        assert len(second) == len(first)
        evidence = client.get(f"/api/v1/resources/{body['id']}/evidence").json()
        assert len(evidence) == len(second)
        chunk_ids = {c["id"] for c in second}
        assert all(item["chunk_id"] in chunk_ids for item in evidence)


def test_chunks_evidence_404():
    with _client() as client:
        assert client.get("/api/v1/resources/res_missing/chunks").status_code == 404
        assert client.get("/api/v1/resources/res_missing/evidence").status_code == 404


def test_long_document_splits_into_chunks():
    """超过 max_chars 的长文档切分为多个 chunk，页码各自保留。"""
    from app.parsing.base import ParsedBlock
    from app.parsing.chunking import chunk_blocks

    blocks = [
        ParsedBlock(type="page_marker", text="p1", page=1),
        ParsedBlock(type="paragraph", text="a" * 500),
        ParsedBlock(type="paragraph", text="b" * 500, page=2),
    ]
    chunks = chunk_blocks(blocks, max_chars=800)
    assert len(chunks) == 2
    assert (chunks[0].page_start, chunks[0].page_end) == (1, 1)
    assert (chunks[1].page_start, chunks[1].page_end) == (2, 2)
