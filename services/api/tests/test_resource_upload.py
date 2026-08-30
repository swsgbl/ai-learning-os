"""M1-03 upload dedup acceptance: same content -> same resource, file in object store."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _client():
    return TestClient(create_app(SQLITE_URL))


def _upload(client, data: bytes, name="notes.pdf", **extra):
    files = {"file": (name, data, "application/pdf")}
    return client.post("/api/v1/resources/upload", files=files, data=extra or None)


def test_same_content_deduplicates_to_one_resource():
    with _client() as client:
        first = _upload(client, b"PDF-content-v1")
        assert first.status_code == 201
        body = first.json()
        assert body["deduplicated"] is False
        assert body["media_type"] == "pdf"

        second = _upload(client, b"PDF-content-v1", name="renamed.pdf")
        assert second.status_code == 201
        again = second.json()
        assert again["deduplicated"] is True
        assert again["id"] == body["id"]
        assert again["content_hash"] == body["content_hash"]
        assert again["size_bytes"] == body["size_bytes"]

        different = _upload(client, b"PDF-content-v2")
        assert different.json()["id"] != body["id"]


def test_object_really_stored_once():
    with _client() as client:
        app = client.app
        body = _upload(client, b"stored-bytes").json()
        store = app.state.objects
        assert store.exists(body["storage_key"])
        assert store.get(body["storage_key"]) == b"stored-bytes"
        # 同内容重传不新增对象（内容寻址，key 即 hash 路径）
        _upload(client, b"stored-bytes")
        assert store.exists(body["storage_key"])


def test_license_guard_blocks_unknown_source():
    with _client() as client:
        created = client.post("/api/v1/sources", json={
            "id": "src_blocked", "name": "Blocked", "source_type": "university",
            "homepage": "https://example.org",
        }).json()
        assert created["license_state"] == "UNKNOWN"
        resp = _upload(client, b"secret", source_id="src_blocked")
        assert resp.status_code == 403
        assert "UNKNOWN" in resp.json()["detail"]


def test_license_guard_allows_open_license():
    with _client() as client:
        client.post("/api/v1/sources", json={
            "id": "src_open", "name": "Open", "source_type": "oer",
            "homepage": "https://example.org",
        })
        client.post("/api/v1/sources/src_open/license", json={"state": "OPEN_LICENSE"})
        resp = _upload(client, b"open-content", source_id="src_open")
        assert resp.status_code == 201
        # 来源 license 在上传时点快照到资源上（安全审查修复）
        assert resp.json()["license_state"] == "OPEN_LICENSE"


def test_rejects_bad_type_and_empty_file():
    with _client() as client:
        assert _upload(client, b"x", name="virus.exe").status_code == 422
        assert _upload(client, b"").status_code == 422


def test_get_resource_and_404():
    with _client() as client:
        body = _upload(client, b"lookup-me").json()
        fetched = client.get(f"/api/v1/resources/{body['id']}").json()
        assert fetched["content_hash"] == body["content_hash"]
        assert client.get("/api/v1/resources/res_missing").status_code == 404


def test_upload_requires_database():
    app = create_app(None)
    with TestClient(app) as client:
        assert _upload(client, b"x").status_code == 503


def test_private_upload_defaults_to_unknown_access():
    """无 source 上传 = 用户私有文档（access_state=unknown），不经公共池。"""
    with _client() as client:
        body = _upload(client, b"my-private-notes").json()
        assert body["license_state"] == "UNKNOWN"


def test_dedup_hit_rebinds_missing_source():
    """同内容重传补绑来源并快照 license（安全审查 logic-data-integrity 修复）。"""
    with _client() as client:
        first = _upload(client, b"shared-bytes").json()  # 先匿名上传
        client.post("/api/v1/sources", json={
            "id": "src_bind", "name": "Bind", "source_type": "oer",
            "homepage": "https://example.org",
        })
        client.post("/api/v1/sources/src_bind/license", json={"state": "OPEN_LICENSE"})
        second = _upload(client, b"shared-bytes", source_id="src_bind")
        assert second.status_code == 201
        assert second.json()["deduplicated"] is True
        # 既有行被补绑来源 + license 快照
        detail = client.get(f"/api/v1/resources/{second.json()['id']}").json()
        assert detail["id"] == first["id"]
        assert detail["license_state"] == "OPEN_LICENSE"
