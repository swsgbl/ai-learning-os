"""M1-01 Source Registry acceptance tests (backlog: S/A/B/C/U + robots + rate limit
+ license state + last_verified_at persistence)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _client() -> TestClient:
    return TestClient(create_app(SQLITE_URL))


def test_seed_sources_are_public_course_sources_with_tiers() -> None:
    with _client() as client:
        sources = client.get("/api/v1/sources").json()
        assert len(sources) >= 5
        tiers = {s["trust_tier"] for s in sources}
        assert {"S", "A", "B"} <= tiers
        mit = next(s for s in sources if s["id"] == "src_mit_ocw")
        assert mit["license_state"] == "UNKNOWN"
        assert mit["rate_limit"]["requests_per_minute"] > 0
        assert mit["last_verified_at"] is None


def test_create_source_persists_all_fields() -> None:
    with _client() as client:
        payload = {
            "id": "src_test_univ",
            "name": "Test University OCW",
            "source_type": "university",
            "homepage": "https://ocw.test-univ.example",
            "authority_score": 88,
            "rate_limit": {"requests_per_minute": 6, "max_concurrent": 1},
            "trust_tier": "A",
            "notes": "created by test",
        }
        created = client.post("/api/v1/sources", json=payload)
        assert created.status_code == 201
        fetched = client.get("/api/v1/sources/src_test_univ")
        assert fetched.status_code == 200
        body = fetched.json()
        assert body["license_state"] == "UNKNOWN"
        assert body["rate_limit"]["requests_per_minute"] == 6
        assert body["trust_tier"] == "A"


def test_create_source_rejects_invalid_type_and_duplicate() -> None:
    with _client() as client:
        bad = client.post(
            "/api/v1/sources",
            json={
                "id": "src_x",
                "name": "x",
                "source_type": "blog",
                "homepage": "https://x.example",
            },
        )
        assert bad.status_code == 422

        dup = client.post(
            "/api/v1/sources",
            json={
                "id": "src_mit_ocw",
                "name": "MIT OpenCourseWare",
                "source_type": "oer",
                "homepage": "https://ocw.mit.edu",
            },
        )
        assert dup.status_code == 409


def test_verify_updates_last_verified_at() -> None:
    with _client() as client:
        before = client.get("/api/v1/sources/src_cs61a").json()
        assert before["last_verified_at"] is None
        verified = client.post("/api/v1/sources/src_cs61a/verify")
        assert verified.status_code == 200
        assert verified.json()["last_verified_at"] is not None


def test_sources_api_requires_database() -> None:
    with _client() as client:
        pass
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/sources")
        assert response.status_code == 503
