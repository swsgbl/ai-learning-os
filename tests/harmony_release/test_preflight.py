"""Tests for tools.harmony_release.preflight (signing preflight gate).

All fixtures live in temporary directories and use placeholder bytes —
no real certificates/keystores are ever created or read.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.harmony_release.preflight import (
    DEFAULT_REPO_ROOT,
    MATERIAL_ENV_VARS,
    check_build_profile,
    render_json,
    run_preflight,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = b"placeholder-not-a-real-certificate"

DEFAULT_PROFILE = json.dumps(
    {"app": {"signingConfigs": [], "products": [{"name": "default"}]}},
    indent=2,
)


@pytest.fixture(autouse=True)
def _no_material_env(monkeypatch):
    """Keep host environment from leaking material vars into tests."""
    for name in MATERIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def make_repo(tmp_path: Path, profile_text: str = DEFAULT_PROFILE) -> Path:
    repo = tmp_path / "repo"
    harmony = repo / "apps" / "harmony"
    harmony.mkdir(parents=True)
    (harmony / "build-profile.json5").write_text(profile_text, encoding="utf-8")
    return repo


def make_outside_materials(tmp_path: Path) -> dict:
    """Placeholder material files strictly outside the repo root."""
    outside = tmp_path / "outside_materials"
    outside.mkdir()
    paths = {
        "AIOS_HARMONY_CERT_PATH": outside / "release.cer",
        "AIOS_HARMONY_PROFILE_PATH": outside / "release.p7b",
        "AIOS_HARMONY_KEYSTORE_PATH": outside / "release.p12",
    }
    for path in paths.values():
        path.write_bytes(PLACEHOLDER)
    return paths


def set_material_env(monkeypatch, paths: dict) -> None:
    for name, path in paths.items():
        monkeypatch.setenv(name, str(path))


class TestCleanRepo:
    def test_clean_repo_blocked_and_exit_zero(self, tmp_path):
        repo = make_repo(tmp_path)
        result, code = run_preflight(repo_root=repo)
        assert code == 0
        assert result["status"] == "blocked_by_external_materials"
        assert result["failures"] == []
        assert result["repo_materials"]["count"] == 0
        assert result["build_profile"]["unsigned_boundary"] is True

    def test_missing_materials_is_blocked_status_not_warning(self, tmp_path):
        repo = make_repo(tmp_path)
        result, _ = run_preflight(repo_root=repo)
        assert result["status"] == "blocked_by_external_materials"
        assert result["warnings"] == []
        entry = result["external_materials"]["variables"]["AIOS_HARMONY_CERT_PATH"]
        assert entry == {
            "expected_extension": ".cer",
            "present": False,
            "valid": None,
            "error": "not_set",
        }

    def test_require_materials_missing_exit_2(self, tmp_path):
        repo = make_repo(tmp_path)
        result, code = run_preflight(repo_root=repo, require_materials=True)
        assert code == 2
        assert result["status"] == "blocked_by_external_materials"
        assert result["require_materials"] is True

    def test_partial_materials_still_blocked(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        paths = make_outside_materials(tmp_path)
        monkeypatch.setenv("AIOS_HARMONY_CERT_PATH", str(paths["AIOS_HARMONY_CERT_PATH"]))
        result, code = run_preflight(repo_root=repo, require_materials=True)
        assert code == 2
        assert result["status"] == "blocked_by_external_materials"
        assert result["external_materials"]["all_present"] is False
        assert result["external_materials"]["all_valid"] is None

    def test_deterministic_output(self, tmp_path):
        repo = make_repo(tmp_path)
        first, _ = run_preflight(repo_root=repo)
        second, _ = run_preflight(repo_root=repo)
        assert render_json(first) == render_json(second)


class TestRepoMaterials:
    def test_repo_material_fails_closed(self, tmp_path):
        repo = make_repo(tmp_path)
        (repo / "stray").mkdir()
        (repo / "stray" / "leak.p12").write_bytes(PLACEHOLDER)
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        assert result["status"] == "failure"
        assert result["repo_materials"]["count"] == 1
        assert result["repo_materials"]["findings"] == [
            {"extension": ".p12", "path": "stray/leak.p12"}
        ]
        codes = {f["code"] for f in result["failures"]}
        assert "repo_signing_material_present" in codes

    def test_extension_match_is_case_insensitive(self, tmp_path):
        repo = make_repo(tmp_path)
        (repo / "ca.CER").write_bytes(PLACEHOLDER)
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        assert result["repo_materials"]["findings"][0]["extension"] == ".cer"

    @pytest.mark.parametrize("relpath", [
        "build/x.p12",
        ".verify/x.cer",
        "node_modules/x.jks",
        "apps/harmony/entry/build/default/x.p7b",
    ])
    def test_pruned_directories_excluded_from_scan(self, tmp_path, relpath):
        repo = make_repo(tmp_path)
        target = repo / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PLACEHOLDER)
        result, _ = run_preflight(repo_root=repo)
        assert result["repo_materials"]["count"] == 0
        assert result["status"] == "blocked_by_external_materials"


class TestExternalMaterials:
    def test_valid_external_materials_pass(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        paths = make_outside_materials(tmp_path)
        set_material_env(monkeypatch, paths)
        result, code = run_preflight(repo_root=repo, require_materials=True)
        assert code == 0
        assert result["status"] == "ok"
        assert result["failures"] == []
        assert result["external_materials"]["all_present"] is True
        assert result["external_materials"]["all_valid"] is True
        # ok status still records the honest unsigned build boundary
        assert result["build_profile"]["unsigned_boundary"] is True

    def test_wrong_extension_fails(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        paths = make_outside_materials(tmp_path)
        bad = paths["AIOS_HARMONY_CERT_PATH"].with_suffix(".pem")
        bad.write_bytes(PLACEHOLDER)
        monkeypatch.setenv("AIOS_HARMONY_CERT_PATH", str(bad))
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        entry = result["external_materials"]["variables"]["AIOS_HARMONY_CERT_PATH"]
        assert entry["present"] is True
        assert entry["valid"] is False
        assert entry["error"] == "wrong_extension"

    def test_path_not_found_fails(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        missing = tmp_path / "outside_materials" / "absent.cer"
        monkeypatch.setenv("AIOS_HARMONY_CERT_PATH", str(missing))
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        entry = result["external_materials"]["variables"]["AIOS_HARMONY_CERT_PATH"]
        assert entry["error"] == "path_not_found"

    def test_directory_instead_of_file_fails(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        folder = tmp_path / "a_dir.cer"
        folder.mkdir()
        monkeypatch.setenv("AIOS_HARMONY_CERT_PATH", str(folder))
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        entry = result["external_materials"]["variables"]["AIOS_HARMONY_CERT_PATH"]
        assert entry["error"] == "not_a_regular_file"

    def test_material_inside_repository_fails(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        # "build" is pruned from the repo scan, isolating the env-var check.
        inside = repo / "build" / "keystore.p12"
        inside.parent.mkdir(parents=True)
        inside.write_bytes(PLACEHOLDER)
        monkeypatch.setenv("AIOS_HARMONY_KEYSTORE_PATH", str(inside))
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        entry = result["external_materials"]["variables"]["AIOS_HARMONY_KEYSTORE_PATH"]
        assert entry["error"] == "inside_repository"

    def test_empty_value_treated_as_not_set(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        monkeypatch.setenv("AIOS_HARMONY_CERT_PATH", "")
        result, code = run_preflight(repo_root=repo)
        assert code == 0
        entry = result["external_materials"]["variables"]["AIOS_HARMONY_CERT_PATH"]
        assert entry["present"] is False
        assert entry["error"] == "empty_value"
        assert result["status"] == "blocked_by_external_materials"


class TestJsonSafety:
    def test_json_has_no_env_values_or_absolute_paths(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        paths = make_outside_materials(tmp_path)
        set_material_env(monkeypatch, paths)
        result, _ = run_preflight(repo_root=repo)
        text = render_json(result)
        for name, path in paths.items():
            assert str(path) not in text
            assert path.name not in text
        assert str(tmp_path) not in text
        assert str(repo.resolve()) not in text
        assert "outside_materials" not in text

    def test_json_has_no_values_for_invalid_paths_either(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        bad = tmp_path / "sneaky_name.pem"
        bad.write_bytes(PLACEHOLDER)
        monkeypatch.setenv("AIOS_HARMONY_CERT_PATH", str(bad))
        result, _ = run_preflight(repo_root=repo)
        text = render_json(result)
        assert "sneaky_name" not in text
        assert str(tmp_path) not in text

    def test_repo_findings_use_relative_paths_only(self, tmp_path):
        repo = make_repo(tmp_path)
        (repo / "a.csr").write_bytes(PLACEHOLDER)
        result, _ = run_preflight(repo_root=repo)
        text = render_json(result)
        assert str(tmp_path) not in text
        assert result["repo_materials"]["findings"][0]["path"] == "a.csr"


class TestBuildProfile:
    def test_real_repo_build_profile_unsigned_boundary(self):
        result, failures = check_build_profile(DEFAULT_REPO_ROOT)
        assert failures == []
        assert result["exists"] is True
        assert result["parseable"] is True
        assert result["signing_configs_count"] == 0
        assert result["unsigned_boundary"] is True

    def test_non_empty_signing_configs_fails_closed(self, tmp_path):
        profile = json.dumps(
            {"app": {"signingConfigs": [{"name": "release"}]}}
        )
        repo = make_repo(tmp_path, profile)
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        assert result["build_profile"]["signing_configs_count"] == 1
        assert result["build_profile"]["unsigned_boundary"] is False
        assert {f["code"] for f in result["failures"]} == {
            "signing_configs_not_empty"
        }

    def test_json5_comments_and_trailing_commas_parse(self, tmp_path):
        profile = (
            "{\n"
            "  // DevEco-style comment\n"
            "  \"app\": {\n"
            "    \"signingConfigs\": [/* none yet */],\n"
            "    \"products\": [{\"name\": \"default\",},],\n"
            "  },\n"
            "}\n"
        )
        repo = make_repo(tmp_path, profile)
        result, _ = run_preflight(repo_root=repo)
        assert result["build_profile"]["parseable"] is True
        assert result["build_profile"]["unsigned_boundary"] is True

    def test_bracket_like_string_content_survives_fallback(self, tmp_path):
        profile = (
            "// force fallback parse\n"
            "{\"app\": {\"signingConfigs\": [], \"note\": \"x, } y\"}}"
        )
        repo = make_repo(tmp_path, profile)
        result, _ = run_preflight(repo_root=repo)
        assert result["build_profile"]["parseable"] is True
        assert result["build_profile"]["signing_configs_count"] == 0

    def test_missing_signing_configs_key_fails(self, tmp_path):
        repo = make_repo(tmp_path, "{\"app\": {\"products\": []}}")
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        assert {f["code"] for f in result["failures"]} == {
            "signing_configs_missing"
        }

    def test_signing_configs_not_a_list_fails(self, tmp_path):
        repo = make_repo(tmp_path, "{\"app\": {\"signingConfigs\": \"nope\"}}")
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        assert {f["code"] for f in result["failures"]} == {
            "signing_configs_invalid"
        }

    def test_unparseable_profile_fails_closed(self, tmp_path):
        repo = make_repo(tmp_path, "{ not json at all ]]")
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        assert result["build_profile"]["parseable"] is False
        assert "build_profile_unparseable" in {f["code"] for f in result["failures"]}

    def test_missing_profile_file_fails_closed(self, tmp_path):
        repo = tmp_path / "empty_repo"
        repo.mkdir()
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        assert "build_profile_missing" in {f["code"] for f in result["failures"]}


class TestHapRecord:
    def test_hap_recorded_without_signing_inference(self, tmp_path):
        repo = make_repo(tmp_path)
        hap = tmp_path / "entry-default-unsigned.hap"
        hap.write_bytes(b"abc")
        result, code = run_preflight(repo_root=repo, hap=str(hap))
        assert code == 0
        record = result["hap"]
        assert record["exists"] is True
        assert record["size_bytes"] == 3
        assert record["sha256"] == hashlib.sha256(b"abc").hexdigest().upper()
        assert record["filename_has_unsigned"] is True

    def test_hap_path_missing_is_warning_only(self, tmp_path):
        repo = make_repo(tmp_path)
        result, code = run_preflight(
            repo_root=repo, hap=str(tmp_path / "absent-unsigned.hap")
        )
        assert code == 0
        assert result["hap"]["exists"] is False
        assert {w["code"] for w in result["warnings"]} == {"hap_path_missing"}

    def test_hap_filename_without_unsigned_warns(self, tmp_path):
        repo = make_repo(tmp_path)
        hap = tmp_path / "entry-default.hap"
        hap.write_bytes(b"abc")
        result, code = run_preflight(repo_root=repo, hap=str(hap))
        assert code == 0
        assert {w["code"] for w in result["warnings"]} == {
            "hap_filename_not_unsigned"
        }

    def test_strict_upgrades_hap_warnings_to_failure(self, tmp_path):
        repo = make_repo(tmp_path)
        hap_arg = str(tmp_path / "absent-unsigned.hap")
        result, code = run_preflight(repo_root=repo, hap=hap_arg, strict=True)
        assert code == 1
        assert result["status"] == "failure"
        assert result["warnings"] == []
        assert "hap_path_missing" in {f["code"] for f in result["failures"]}
        assert result["strict"] is True


class TestCli:
    def _run_cli(self, repo: Path, *extra: str, env_extra: dict = None) -> tuple:
        env = {k: v for k, v in os.environ.items() if k not in MATERIAL_ENV_VARS}
        if env_extra:
            env.update(env_extra)
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools/harmony_release/preflight.py"),
             "--repo-root", str(repo), *extra],
            capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
        )
        return proc.returncode, json.loads(proc.stdout)

    def test_cli_clean_repo_exit_0(self, tmp_path):
        repo = make_repo(tmp_path)
        code, payload = self._run_cli(repo)
        assert code == 0
        assert payload["status"] == "blocked_by_external_materials"

    def test_cli_require_materials_exit_2(self, tmp_path):
        repo = make_repo(tmp_path)
        code, payload = self._run_cli(repo, "--require-materials")
        assert code == 2
        assert payload["exit_code"] == 2

    def test_cli_default_repo_root_is_this_checkout(self):
        code, payload = self._run_cli(DEFAULT_REPO_ROOT)
        assert code == 0
        assert payload["build_profile"]["unsigned_boundary"] is True
        assert payload["repo_materials"]["count"] == 0
