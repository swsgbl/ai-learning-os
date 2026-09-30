"""Tests for the read-only AGC signing-input preflight.

Every fixture is structural and local: fake build-profile JSON5 plus
placeholder bytes outside the repository. No real AGC credential, material,
tool, device, build, or signing operation is used.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tools.harmony_release.preflight import MATERIAL_ENV_VARS
from tools.harmony_release.sign_hap import CREDENTIAL_ENV_VARS

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_DECLARATION = (
    REPO_ROOT / "tools" / "harmony_release" / "agc_signing_inputs.json"
)
PLACEHOLDER = b"placeholder-not-a-real-signing-material"


@pytest.fixture(autouse=True)
def _isolated_signing_environment(monkeypatch):
    for name in (*MATERIAL_ENV_VARS, *CREDENTIAL_ENV_VARS):
        monkeypatch.delenv(name, raising=False)


def canonical_declaration() -> dict[str, Any]:
    return json.loads(CANONICAL_DECLARATION.read_text(encoding="utf-8"))


def write_declaration(tmp_path: Path, declaration: dict[str, Any]) -> Path:
    path = tmp_path / "agc-signing-inputs.json"
    path.write_text(json.dumps(declaration, indent=2), encoding="utf-8")
    return path


def write_repo(tmp_path: Path, profile: dict[str, Any] | None = None) -> Path:
    repo = tmp_path / "repo"
    harmony = repo / "apps" / "harmony"
    harmony.mkdir(parents=True)
    if profile is None:
        profile = {
            "app": {
                "signingConfigs": [{"name": "release", "type": "HarmonyOS"}],
                "products": [
                    {"name": "default", "signingConfig": "release"}
                ],
            }
        }
    (harmony / "build-profile.json5").write_text(
        json.dumps(profile, indent=2), encoding="utf-8"
    )
    app_scope = repo / "apps" / "harmony" / "AppScope"
    app_scope.mkdir()
    (app_scope / "app.json5").write_text(
        json.dumps(
            {"app": {"bundleName": "com.ailearningos.app"}}, indent=2
        ),
        encoding="utf-8",
    )
    return repo


def material_paths(tmp_path: Path) -> dict[str, Path]:
    outside = tmp_path / "external-fixture"
    outside.mkdir()
    paths = {
        "AIOS_HARMONY_CERT_PATH": outside / "certificate.cer",
        "AIOS_HARMONY_PROFILE_PATH": outside / "profile.p7b",
        "AIOS_HARMONY_KEYSTORE_PATH": outside / "keystore.p12",
    }
    for path in paths.values():
        path.write_bytes(PLACEHOLDER)
    return paths


def set_complete_environment(monkeypatch, tmp_path: Path) -> dict[str, str]:
    values = {
        name: str(path) for name, path in material_paths(tmp_path).items()
    }
    values.update(
        {
            "AIOS_HARMONY_KEY_ALIAS": "fake-alias",
            "AIOS_HARMONY_KEYSTORE_PASSWORD": "fake-keystore-password",
            "AIOS_HARMONY_KEY_PASSWORD": "fake-key-password",
        }
    )
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


def test_canonical_declaration_is_valid_and_value_free():
    from tools.harmony_release.agc_signing_preflight import (
        load_declaration,
    )

    declaration, failures = load_declaration(CANONICAL_DECLARATION)
    assert failures == []
    assert declaration == canonical_declaration()
    rendered = json.dumps(declaration, sort_keys=True)
    assert "PASSWORD" in rendered  # variable names are public contract labels
    assert "fake-" not in rendered
    assert "\\" not in rendered


def test_structural_fixture_passes_without_signing(tmp_path, monkeypatch):
    from tools.harmony_release.agc_signing_preflight import run_preflight

    repo = write_repo(tmp_path)
    declaration_path = write_declaration(tmp_path, canonical_declaration())
    set_complete_environment(monkeypatch, tmp_path)

    result, code = run_preflight(
        repo_root=repo, declaration_path=declaration_path
    )

    assert code == 0
    assert result["status"] == "ok"
    assert result["failures"] == []
    assert result["signing_performed"] is False
    assert result["signed_hap_generated"] is False
    assert result["agc_access"] is False
    assert result["production_ready"] is False
    assert result["build_profile"]["structure_valid"] is True
    assert result["external_inputs"]["materials"]["all_valid"] is True
    assert result["external_inputs"]["credentials"]["all_present"] is True


def test_missing_external_inputs_block_after_valid_structure(tmp_path):
    from tools.harmony_release.agc_signing_preflight import run_preflight

    repo = write_repo(tmp_path)
    declaration_path = write_declaration(tmp_path, canonical_declaration())
    result, code = run_preflight(
        repo_root=repo, declaration_path=declaration_path
    )

    assert code == 2
    assert result["status"] == "blocked_by_external_inputs"
    assert result["failures"] == []
    assert result["external_inputs"]["materials"]["all_present"] is False
    assert result["external_inputs"]["credentials"]["all_present"] is False


def test_partial_external_inputs_block(tmp_path, monkeypatch):
    from tools.harmony_release.agc_signing_preflight import run_preflight

    repo = write_repo(tmp_path)
    declaration_path = write_declaration(tmp_path, canonical_declaration())
    paths = material_paths(tmp_path)
    monkeypatch.setenv(
        "AIOS_HARMONY_CERT_PATH", str(paths["AIOS_HARMONY_CERT_PATH"])
    )
    monkeypatch.setenv("AIOS_HARMONY_KEY_ALIAS", "fake-alias")

    result, code = run_preflight(
        repo_root=repo, declaration_path=declaration_path
    )

    assert code == 2
    assert result["status"] == "blocked_by_external_inputs"
    assert result["external_inputs"]["materials"]["all_present"] is False
    assert result["external_inputs"]["credentials"]["all_present"] is False


def test_empty_credential_is_a_blocker_not_a_failure(tmp_path, monkeypatch):
    from tools.harmony_release.agc_signing_preflight import run_preflight

    repo = write_repo(tmp_path)
    declaration_path = write_declaration(tmp_path, canonical_declaration())
    set_complete_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("AIOS_HARMONY_KEY_ALIAS", "")

    result, code = run_preflight(
        repo_root=repo, declaration_path=declaration_path
    )

    assert code == 2
    entry = result["external_inputs"]["credentials"]["variables"][
        "AIOS_HARMONY_KEY_ALIAS"
    ]
    assert entry == {"present": False, "error": "empty_value"}


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_suffix",
        "missing_material",
        "extra_material",
        "missing_credential",
        "extra_credential",
        "wrong_bundle_name",
        "wrong_product",
        "wrong_signing_config",
        "unknown_top_level_key",
        "unknown_entry_key",
        "duplicate_env",
    ],
)
def test_declaration_drift_fails_closed(tmp_path, mutation):
    from tools.harmony_release.agc_signing_preflight import run_preflight

    declaration = canonical_declaration()
    if mutation == "wrong_suffix":
        declaration["material_inputs"][0]["expected_suffix"] = ".pem"
    elif mutation == "missing_material":
        declaration["material_inputs"].pop()
    elif mutation == "extra_material":
        declaration["material_inputs"].append(
            {"name": "extra", "env": "AIOS_EXTRA_PATH", "expected_suffix": ".cer"}
        )
    elif mutation == "missing_credential":
        declaration["credential_inputs"].pop()
    elif mutation == "extra_credential":
        declaration["credential_inputs"].append(
            {"name": "extra", "env": "AIOS_HARMONY_EXTRA"}
        )
    elif mutation == "wrong_bundle_name":
        declaration["bundle_name"] = "com.example.other"
    elif mutation == "wrong_product":
        declaration["product"] = "other-product"
    elif mutation == "wrong_signing_config":
        declaration["signing_config"] = "other-signing"
    elif mutation == "unknown_top_level_key":
        declaration["absolute_path"] = "must-not-exist"
    elif mutation == "unknown_entry_key":
        declaration["material_inputs"][0]["value"] = "must-not-exist"
    elif mutation == "duplicate_env":
        declaration["credential_inputs"][1]["env"] = (
            declaration["credential_inputs"][0]["env"]
        )

    repo = write_repo(tmp_path)
    declaration_path = write_declaration(tmp_path, declaration)
    result, code = run_preflight(
        repo_root=repo, declaration_path=declaration_path
    )

    assert code == 1
    assert result["status"] == "failure"
    assert result["declaration"]["valid"] is False
    assert result["declaration"]["canonical"] is False
    assert result["failures"]


def test_malformed_declaration_json_fails_closed(tmp_path):
    from tools.harmony_release.agc_signing_preflight import run_preflight

    repo = write_repo(tmp_path)
    declaration = tmp_path / "declaration.json"
    declaration.write_text(
        '{"schema_version": 1, "schema_version": 2}', encoding="utf-8"
    )
    result, code = run_preflight(
        repo_root=repo, declaration_path=declaration
    )

    assert code == 1
    assert result["status"] == "failure"
    assert result["declaration"]["valid"] is False
    assert result["declaration"]["error"] == "duplicate_json_key"


def test_missing_or_non_file_declaration_fails_closed(tmp_path):
    from tools.harmony_release.agc_signing_preflight import run_preflight

    repo = write_repo(tmp_path)
    result, code = run_preflight(
        repo_root=repo, declaration_path=tmp_path / "absent.json"
    )
    assert code == 1
    assert result["declaration"]["error"] == "path_not_found"

    result, code = run_preflight(repo_root=repo, declaration_path=tmp_path)
    assert code == 1
    assert result["declaration"]["error"] == "not_a_regular_file"


@pytest.mark.parametrize(
    "profile",
    [
        {"app": {"signingConfigs": [], "products": []}},
        {"app": {"products": [{"name": "default", "signingConfig": "release"}]}},
        {"app": {"signingConfigs": ["release"], "products": []}},
        {
            "app": {
                "signingConfigs": [{"name": "other"}],
                "products": [{"name": "default", "signingConfig": "release"}],
            }
        },
        {
            "app": {
                "signingConfigs": [{"name": "release"}],
                "products": [
                    {"name": "default", "signingConfig": "other"},
                ],
            }
        },
        {
            "app": {
                "signingConfigs": [{"name": "release"}],
                "products": [
                    {"name": "default", "signingConfig": "release"},
                    {"name": "default", "signingConfig": "release"},
                ],
            }
        },
        {
            "app": {
                "signingConfigs": [
                    {"name": "release"},
                    {"name": "release"},
                ],
                "products": [{"name": "default", "signingConfig": "release"}],
            }
        },
    ],
)
def test_malformed_or_ambiguous_build_profile_fails(
    tmp_path, monkeypatch, profile
):
    from tools.harmony_release.agc_signing_preflight import run_preflight

    repo = write_repo(tmp_path, profile)
    declaration_path = write_declaration(tmp_path, canonical_declaration())
    set_complete_environment(monkeypatch, tmp_path)
    result, code = run_preflight(
        repo_root=repo, declaration_path=declaration_path
    )

    assert code == 1
    assert result["status"] == "failure"
    assert result["build_profile"]["structure_valid"] is False
    assert result["failures"]


@pytest.mark.parametrize(
    ("relative_path", "record_name", "failure_code"),
    [
        (
            Path("apps/harmony/build-profile.json5"),
            "build_profile",
            "build_profile_rejected",
        ),
        (
            Path("apps/harmony/AppScope/app.json5"),
            "app_manifest",
            "app_manifest_rejected",
        ),
    ],
)
def test_structural_inputs_reject_non_regular_files(
    tmp_path, relative_path, record_name, failure_code
):
    from tools.harmony_release.agc_signing_preflight import run_preflight

    repo = write_repo(tmp_path)
    declaration_path = write_declaration(tmp_path, canonical_declaration())
    target = repo / relative_path
    target.unlink()
    target.mkdir()

    result, code = run_preflight(
        repo_root=repo, declaration_path=declaration_path
    )

    assert code == 1
    assert result[record_name]["exists"] is True
    assert result[record_name]["parseable"] is False
    assert result["failures"] == [
        {
            "code": failure_code,
            "detail": {"error": "not_a_regular_file"},
        }
    ]


def test_structural_inputs_reject_symlink_or_reparse_points(
    tmp_path, monkeypatch
):
    from tools.harmony_release.agc_signing_preflight import run_preflight

    module = __import__(
        "tools.harmony_release.agc_signing_preflight", fromlist=["run"]
    )
    original = module._regular_file_error
    repo = write_repo(tmp_path)
    declaration_path = write_declaration(tmp_path, canonical_declaration())
    targets = {
        repo / Path("apps/harmony/build-profile.json5"),
        repo / Path("apps/harmony/AppScope/app.json5"),
    }

    def reject_targets(path: Path) -> str | None:
        if Path(path) in targets:
            return "symlink_or_reparse_point"
        return original(path)

    monkeypatch.setattr(module, "_regular_file_error", reject_targets)
    result, code = run_preflight(
        repo_root=repo, declaration_path=declaration_path
    )

    assert code == 1
    assert result["failures"] == [
        {
            "code": "app_manifest_rejected",
            "detail": {"error": "symlink_or_reparse_point"},
        },
        {
            "code": "build_profile_rejected",
            "detail": {"error": "symlink_or_reparse_point"},
        },
    ]


def test_invalid_present_material_is_failure_not_blocker(
    tmp_path, monkeypatch
):
    from tools.harmony_release.agc_signing_preflight import run_preflight

    repo = write_repo(tmp_path)
    declaration_path = write_declaration(tmp_path, canonical_declaration())
    set_complete_environment(monkeypatch, tmp_path)
    bad = tmp_path / "external-fixture" / "certificate.pem"
    bad.write_bytes(PLACEHOLDER)
    monkeypatch.setenv("AIOS_HARMONY_CERT_PATH", str(bad))

    result, code = run_preflight(
        repo_root=repo, declaration_path=declaration_path
    )

    assert code == 1
    assert result["status"] == "failure"
    entry = result["external_inputs"]["materials"]["variables"][
        "AIOS_HARMONY_CERT_PATH"
    ]
    assert entry["present"] is True
    assert entry["valid"] is False
    assert entry["error"] == "wrong_extension"


@pytest.mark.parametrize(
    ("manifest", "code"),
    [
        ({"app": {}}, "bundle_name_missing"),
        ({"app": {"bundleName": "com.example.other"}}, "bundle_name_mismatch"),
        ({"app": []}, "app_manifest_app_invalid"),
    ],
)
def test_app_manifest_bundle_drift_fails_closed(
    tmp_path, monkeypatch, manifest, code
):
    from tools.harmony_release.agc_signing_preflight import run_preflight

    repo = write_repo(tmp_path)
    declaration_path = write_declaration(tmp_path, canonical_declaration())
    set_complete_environment(monkeypatch, tmp_path)
    app_manifest = repo / "apps" / "harmony" / "AppScope" / "app.json5"
    app_manifest.write_text(json.dumps(manifest), encoding="utf-8")

    result, exit_code = run_preflight(
        repo_root=repo, declaration_path=declaration_path
    )

    assert exit_code == 1
    assert result["app_manifest"]["bundle_name_matches"] is not True
    assert code in {failure["code"] for failure in result["failures"]}


def test_material_inside_repository_fails(tmp_path, monkeypatch):
    from tools.harmony_release.agc_signing_preflight import run_preflight

    repo = write_repo(tmp_path)
    declaration_path = write_declaration(tmp_path, canonical_declaration())
    set_complete_environment(monkeypatch, tmp_path)
    inside = repo / "build" / "fixture.p12"
    inside.parent.mkdir()
    inside.write_bytes(PLACEHOLDER)
    monkeypatch.setenv("AIOS_HARMONY_KEYSTORE_PATH", str(inside))

    result, code = run_preflight(
        repo_root=repo, declaration_path=declaration_path
    )

    assert code == 1
    entry = result["external_inputs"]["materials"]["variables"][
        "AIOS_HARMONY_KEYSTORE_PATH"
    ]
    assert entry["error"] == "inside_repository"


def test_output_never_contains_values_or_absolute_paths(
    tmp_path, monkeypatch
):
    from tools.harmony_release.agc_signing_preflight import (
        render_json,
        run_preflight,
    )

    repo = write_repo(tmp_path)
    declaration_path = write_declaration(tmp_path, canonical_declaration())
    values = set_complete_environment(monkeypatch, tmp_path)
    result, code = run_preflight(
        repo_root=repo, declaration_path=declaration_path
    )
    assert code == 0

    rendered = render_json(result)
    for value in values.values():
        assert value not in rendered
    assert str(tmp_path) not in rendered
    assert str(repo.resolve()) not in rendered
    assert "external-fixture" not in rendered
    assert "fake-alias" not in rendered
    assert "fake-keystore-password" not in rendered
    assert "fake-key-password" not in rendered


def test_output_is_deterministic(tmp_path, monkeypatch):
    from tools.harmony_release.agc_signing_preflight import (
        render_json,
        run_preflight,
    )

    repo = write_repo(tmp_path)
    declaration_path = write_declaration(tmp_path, canonical_declaration())
    set_complete_environment(monkeypatch, tmp_path)
    first, first_code = run_preflight(
        repo_root=repo, declaration_path=declaration_path
    )
    second, second_code = run_preflight(
        repo_root=repo, declaration_path=declaration_path
    )
    assert first_code == second_code == 0
    assert render_json(first) == render_json(second)


def test_cli_supports_local_fake_fixture(tmp_path):
    repo = write_repo(tmp_path)
    declaration_path = write_declaration(tmp_path, canonical_declaration())
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {*MATERIAL_ENV_VARS, *CREDENTIAL_ENV_VARS}
    }
    for name, path in material_paths(tmp_path).items():
        env[name] = str(path)
    env.update(
        {
            "AIOS_HARMONY_KEY_ALIAS": "fake-alias",
            "AIOS_HARMONY_KEYSTORE_PASSWORD": "fake-keystore-password",
            "AIOS_HARMONY_KEY_PASSWORD": "fake-key-password",
        }
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.harmony_release.agc_signing_preflight",
            "--repo-root",
            str(repo),
            "--declaration",
            str(declaration_path),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    assert payload["production_ready"] is False
