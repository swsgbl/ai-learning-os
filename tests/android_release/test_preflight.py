"""Tests for tools.android_release.preflight (M14-171A signing readiness gate).

Fail-closed static contract over the apps/android Gradle release-signing
wiring declared in docs/MOBILE_DISTRIBUTION.md. All fixtures live in
temporary directories and use placeholder bytes — no real keystores are
ever created or read.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools.android_release.preflight import (
    DEFAULT_REPO_ROOT,
    REQUIRED_ENV_REFERENCES,
    GRADLE_FILE_RELPATH,
    run_preflight,
    render_json,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Minimal Gradle text that mirrors the real opt-in wiring: every external
# input is referenced by name, no literal passwords, no debug fallback.
MINIMAL_GRADLE = """\
// M14-171A minimal fixture mirroring apps/android/app/build.gradle.kts
val releaseSigningInputs: Map<String, String>? = run {
    val envInputs = listOf(
        "AIOS_ANDROID_KEYSTORE_PATH" to "keystore.path",
        "AIOS_ANDROID_KEYSTORE_STORE_PASSWORD" to "keystore.storePassword",
        "AIOS_ANDROID_KEYSTORE_KEY_ALIAS" to "keystore.keyAlias",
        "AIOS_ANDROID_KEYSTORE_KEY_PASSWORD" to "keystore.keyPassword",
    )
    val propsPath = envOrNull("AIOS_ANDROID_SIGNING_PROPERTIES")
    // ... resolve, fail closed on partial input ...
}
android {
    signingConfigs {
        if (releaseSigningInputs != null) {
            create("release") {
                storeFile = File(releaseSigningInputs.getValue("AIOS_ANDROID_KEYSTORE_PATH"))
                storePassword = releaseSigningInputs.getValue("AIOS_ANDROID_KEYSTORE_STORE_PASSWORD")
                keyAlias = releaseSigningInputs.getValue("AIOS_ANDROID_KEYSTORE_KEY_ALIAS")
                keyPassword = releaseSigningInputs.getValue("AIOS_ANDROID_KEYSTORE_KEY_PASSWORD")
            }
        }
    }
}
"""


def make_repo(tmp_path: Path, gradle_text: str = MINIMAL_GRADLE) -> Path:
    repo = tmp_path / "repo"
    app_dir = repo / "apps" / "android" / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "build.gradle.kts").write_text(gradle_text, encoding="utf-8")
    return repo


class TestCleanRepo:
    def test_clean_repo_ok_exit_zero(self, tmp_path):
        repo = make_repo(tmp_path)
        result, code = run_preflight(repo_root=repo)
        assert code == 0
        assert result["status"] == "ok"
        assert result["failures"] == []
        assert result["repo_materials"]["count"] == 0

    def test_real_repo_contract_pinned(self):
        """The actual repository must satisfy the contract at all times."""
        assert (REPO_ROOT / GRADLE_FILE_RELPATH).is_file()
        result, code = run_preflight(repo_root=REPO_ROOT)
        assert code == 0
        assert result["status"] == "ok"
        assert result["failures"] == []


class TestEnvReferences:
    @pytest.mark.parametrize("env_name", sorted(REQUIRED_ENV_REFERENCES))
    def test_missing_env_reference_fails(self, tmp_path, env_name):
        text = MINIMAL_GRADLE.replace(env_name, "AIOS_REMOVED_REFERENCE")
        repo = make_repo(tmp_path, gradle_text=text)
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "missing_env_reference" in codes
        detail_envs = [
            f["detail"]["env"]
            for f in result["failures"]
            if f["code"] == "missing_env_reference"
        ]
        assert detail_envs == [env_name]

    def test_missing_gradle_file_fails(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["gradle_file_missing"]


class TestForbiddenPatterns:
    @pytest.mark.parametrize(
        "bad_line",
        [
            'signingConfig = signingConfigs.debug',
            'signingConfig = signingConfigs.getByName( "debug" )',
            'signingConfig = signingConfigs.getByName("debug")',
        ],
    )
    def test_debug_fallback_fails(self, tmp_path, bad_line):
        repo = make_repo(tmp_path, gradle_text=MINIMAL_GRADLE + "\n" + bad_line + "\n")
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "forbidden_pattern" in codes

    @pytest.mark.parametrize(
        "bad_line",
        [
            'storePassword = "literal-secret-not-allowed"',
            "keyPassword = \"also-not-allowed\"",
            "storeFile = file(\"keystore-inside-repo.jks\")",
        ],
    )
    def test_literal_secret_fails(self, tmp_path, bad_line):
        repo = make_repo(tmp_path, gradle_text=MINIMAL_GRADLE + "\n" + bad_line + "\n")
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "forbidden_pattern" in codes

    def test_failures_never_echo_matched_text(self, tmp_path):
        secret_marker = "SUPER-SECRET-LITERAL"
        repo = make_repo(
            tmp_path,
            gradle_text=MINIMAL_GRADLE + f'\nstorePassword = "{secret_marker}"\n',
        )
        result, _code = run_preflight(repo_root=repo)
        rendered = render_json(result)
        assert secret_marker not in rendered
        for failure in result["failures"]:
            assert "matched_text" not in failure


class TestRepoMaterials:
    @pytest.mark.parametrize("suffix", [".jks", ".keystore", ".p12"])
    def test_material_in_repo_fails(self, tmp_path, suffix):
        repo = make_repo(tmp_path)
        (repo / f"release{suffix}").write_bytes(b"placeholder-not-a-real-keystore")
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "repo_material_present" in codes
        assert result["repo_materials"]["count"] == 1

    def test_material_deep_in_tree_fails(self, tmp_path):
        repo = make_repo(tmp_path)
        nested = repo / "apps" / "android" / "app" / "signing"
        nested.mkdir(parents=True)
        (nested / "release.jks").write_bytes(b"placeholder")
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        assert result["repo_materials"]["count"] == 1
        assert result["repo_materials"]["files"] == [
            "apps/android/app/signing/release.jks"
        ]

    @pytest.mark.parametrize(
        "pruned_dir", [".claude", "build", ".gradle", "__pycache__"]
    )
    def test_material_in_pruned_dir_ignored(self, tmp_path, pruned_dir):
        repo = make_repo(tmp_path)
        pruned = repo / pruned_dir / "nested"
        pruned.mkdir(parents=True)
        (pruned / "release.jks").write_bytes(b"placeholder")
        result, code = run_preflight(repo_root=repo)
        assert code == 0
        assert result["repo_materials"]["count"] == 0


class TestDeterminism:
    def test_render_json_is_deterministic(self, tmp_path):
        repo = make_repo(tmp_path)
        first, _ = run_preflight(repo_root=repo)
        second, _ = run_preflight(repo_root=repo)
        assert render_json(first).encode("utf-8") == render_json(second).encode(
            "utf-8"
        )

    def test_failures_sorted_for_stable_output(self, tmp_path):
        text = (
            MINIMAL_GRADLE.replace("AIOS_ANDROID_KEY_ALIAS", "AIOS_GONE_ALIAS")
            + '\nstorePassword = "x"\n'
        )
        repo = make_repo(tmp_path, gradle_text=text)
        result, code = run_preflight(repo_root=repo)
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert codes == sorted(codes)


class TestCli:
    def test_cli_exit_zero_on_clean_repo(self, tmp_path):
        repo = make_repo(tmp_path)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.android_release.preflight",
                "--repo-root",
                str(repo),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert proc.returncode == 0
        assert '"status": "ok"' in proc.stdout

    def test_cli_exit_one_on_violation(self, tmp_path):
        text = MINIMAL_GRADLE.replace(
            "AIOS_ANDROID_KEYSTORE_PATH", "AIOS_REMOVED_PATH"
        )
        repo = make_repo(tmp_path, gradle_text=text)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.android_release.preflight",
                "--repo-root",
                str(repo),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert proc.returncode == 1
