"""M7-04 License report: four-section license report tests.

- dependencies: requirements parsing + real environment license metadata
  (uninstalled/missing metadata -> UNKNOWN honestly);
- web: package.json declared + node_modules measured (honest when absent);
- models: Settings slot provenance (not_configured honestly);
- content sources / derived objects: source license_state, resource license
  snapshot, import-draft admission snapshot.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.ops.license_report import (
    UNKNOWN,
    build_license_report,
    collect_api_dependencies,
    collect_model_slots,
    collect_web_dependencies,
    license_of,
    parse_requirements,
)

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def test_parse_requirements_strips_noise() -> None:
    text = (
        "# comment\n"
        "fastapi>=0.118,<0.119  # inline comment\n"
        "-r other.txt\n"
        "boto3>=1.35\n"
        "python_multipart>=0.0.20\n"
    )
    assert parse_requirements(text) == ["fastapi", "boto3", "python-multipart"]


def test_license_of_real_and_missing_package() -> None:
    version, lic = license_of("fastapi")
    assert version is not None
    assert lic and lic != UNKNOWN
    # uninstalled -> (None, None), caller marks UNKNOWN honestly
    assert license_of("not-a-real-package-xyz") == (None, None)


def test_collect_api_dependencies_real_requirements() -> None:
    requirements = Path(__file__).resolve().parents[1] / "requirements.txt"
    deps = collect_api_dependencies(requirements.read_text(encoding="utf-8"))
    assert deps, "no dependencies parsed from requirements.txt"
    fastapi = next(d for d in deps if d["name"] == "fastapi")
    assert fastapi["installed"] is True
    assert fastapi["version"] is not None
    assert fastapi["license"] != UNKNOWN
    # structure complete for every declared package; packages whose wheel metadata
    # carries no License field/classifier are honestly UNKNOWN
    assert all(set(d) == {"name", "version", "license", "installed"} for d in deps)
    assert all(d["installed"] is True for d in deps)


def test_collect_web_dependencies(tmp_path: Path) -> None:
    web = tmp_path / "web"
    (web / "node_modules" / "left-pad").mkdir(parents=True)
    (web / "node_modules" / "left-pad" / "package.json").write_text(
        json.dumps({"license": "MIT"}), encoding="utf-8"
    )
    (web / "node_modules" / "no-meta").mkdir(parents=True)
    (web / "package.json").write_text(json.dumps({
        "dependencies": {"left-pad": "^1.0.0"},
        "devDependencies": {"no-meta": "^2.0.0"},
    }), encoding="utf-8")
    result = collect_web_dependencies(web)
    assert result["status"] == "ok"
    by_name = {p["name"]: p for p in result["packages"]}
    assert by_name["left-pad"] == {
        "name": "left-pad", "declared_range": "^1.0.0",
        "license": "MIT", "installed": True,
    }
    assert by_name["no-meta"]["installed"] is False
    assert by_name["no-meta"]["license"] == UNKNOWN


def test_collect_web_dependencies_missing_dir(tmp_path: Path) -> None:
    result = collect_web_dependencies(tmp_path / "nope")
    assert result["status"] == "unavailable"
    assert result["packages"] == []


def test_collect_model_slots_not_configured_honest() -> None:
    s = Settings(_env_file=None)
    slots = {r["slot"]: r["value"] for r in collect_model_slots(s)}
    assert slots["llm"] == "not_configured"
    assert slots["asr_cloud_model"] == "not_configured"
    assert slots["rubric_judge"] == "keyword"


def test_build_license_report_structure() -> None:
    report = build_license_report(
        dependencies=[{"name": "fastapi", "version": "0.118.3", "license": "MIT", "installed": True}],
        web={"status": "ok", "note": None, "packages": []},
        model_slots=[{"slot": "rubric_judge", "value": "keyword"}],
        content_sources=[{"id": "src_x", "license_state": "OPEN_LICENSE"}],
        resources=[{"id": "res_x", "license_state": "OPEN_LICENSE"}],
        course_import_drafts=[{
            "id": "d1", "status": "approved",
            "source_license_state": "OPEN_LICENSE",
            "reuse_admission": "ATTRIBUTION_REQUIRED",
        }],
    )
    assert set(report) >= {"dependencies", "models", "content_sources", "derived_objects"}
    draft = report["derived_objects"]["course_import_drafts"][0]
    assert draft["reuse_admission"] == "ATTRIBUTION_REQUIRED"


def test_license_report_endpoint_requires_database() -> None:
    with TestClient(create_app(None)) as client:
        assert client.get("/api/v1/license/report").status_code == 503


def test_license_report_endpoint_end_to_end() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        src = client.post("/api/v1/sources", json={
            "id": "src_lr_e2e",
            "name": "license report e2e",
            "source_type": "oer",
            "license_state": "OPEN_LICENSE",
            "homepage": "https://example.edu/lr",
        })
        assert src.status_code == 201, src.text
        up = client.post(
            "/api/v1/resources/upload",
            files={"file": ("doc.json", b"license report e2e content", "application/json")},
            data={"source_id": "src_lr_e2e", "title": "e2e doc"},
        )
        assert up.status_code == 201, up.text
        rid = up.json()["id"]
        draft = client.post("/api/v1/courses/import-drafts", json={"resource_id": rid})
        assert draft.status_code == 201, draft.text

        resp = client.get("/api/v1/license/report")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) >= {"dependencies", "models", "content_sources", "derived_objects"}
        assert body["dependencies"]["api"], "api deps empty"
        assert body["dependencies"]["web"]["status"] == "ok"
        assert body["models"]
        src_row = next(s for s in body["content_sources"] if s["id"] == "src_lr_e2e")
        assert src_row["license_state"] == "OPEN_LICENSE"
        res_row = next(r for r in body["derived_objects"]["resources"] if r["id"] == rid)
        assert res_row["license_state"] == "OPEN_LICENSE"
        assert res_row["source_id"] == "src_lr_e2e"
        d_row = next(d for d in body["derived_objects"]["course_import_drafts"]
                     if d["id"] == draft.json()["id"])
        assert d_row["source_license_state"] == "OPEN_LICENSE"
        assert d_row["reuse_admission"] == "ATTRIBUTION_REQUIRED"
