"""Tests for tools.android_release.stage_download (M14-174 readiness).

Every test injects a fake apksigner/aapt runner and resolver and uses
placeholder APK bytes plus temporary manifests: apksigner/aapt are never
executed, no Android SDK is required, nothing reaches the network, and no
real keystore or secret is ever created or read. Atomicity is exercised by
injecting a failing ``os.replace`` and a tampering staged-file read.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from tools.android_release.stage_download import (
    SCHEMA_VERSION,
    TOOL_NAME,
    main,
    run_stage,
)
from tools.android_release.verify_artifact import (
    ACCEPTED_SIGNATURE_SCHEME,
    MANIFEST_SCHEMA,
    CommandOutcome,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

APK_NAME = "ai-learning-os-0.14.0-release-signed.apk"
OLD_APK_NAME = "ai-learning-os-0.13.0-release-signed.apk"
APK_BYTES = b"placeholder-apk-bytes-not-a-real-artifact"
PACKAGE_NAME = "com.ailearningos.app"
VERSION_CODE = 15
PREVIOUS_VERSION_CODE = 14
VERSION_NAME = "0.14.0"

# Markers that must never reach the rendered JSON, whatever happens.
PAYLOAD_MARKER = "payload-marker-must-never-appear"
DN_MARKER = "CN=Leaky-Certificate-Subject-Must-Never-Appear"
RELEASE_DN = "CN=AI Learning OS Release, O=AI Learning OS, C=CN"

APKSIGNER_OK = f"""Verifies
Verified using v1 scheme (JAR signing): false
Verified using v2 scheme (APK Signature Scheme v2): true
Verified using v3 scheme (APK Signature Scheme v3): true
Signer #1 certificate DN: {RELEASE_DN}
Signer #1 certificate SHA-256 digest: {PAYLOAD_MARKER}
"""

BADGING_OK = (
    f"package: name='{PACKAGE_NAME}' versionCode='{VERSION_CODE}' "
    f"versionName='{VERSION_NAME}'\n"
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Keep host SDK pointers, signing inputs and proxies out of tests."""
    for name in (
        "ANDROID_HOME", "ANDROID_SDK_ROOT",
        "AIOS_ANDROID_KEYSTORE_PATH",
        "AIOS_ANDROID_KEYSTORE_STORE_PASSWORD",
        "AIOS_ANDROID_KEYSTORE_KEY_ALIAS",
        "AIOS_ANDROID_KEYSTORE_KEY_PASSWORD",
        "AIOS_ANDROID_SIGNING_PROPERTIES",
        "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
    ):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def apk_sha256(data: bytes = APK_BYTES) -> str:
    return hashlib.sha256(data).hexdigest()


def make_apk(tmp_path: Path, data: bytes = APK_BYTES, name: str = APK_NAME) -> Path:
    apk = tmp_path / name
    apk.write_bytes(data)
    return apk


def manifest_model(
    *,
    entry_name: str = APK_NAME,
    sha256: str | None = None,
    size_bytes: int | None = None,
    url: str | None = None,
    signed: bool = True,
    version_code: int | str = VERSION_CODE,
    schema: str = MANIFEST_SCHEMA,
) -> dict:
    channel: dict = {
        "package_name": PACKAGE_NAME,
        "versionCode": version_code,
        "versionName": VERSION_NAME,
        "files": [
            {
                "name": entry_name,
                "url": url if url is not None else f"/android/{entry_name}",
                "sha256": sha256 if sha256 is not None else apk_sha256(),
                "size_bytes": (
                    size_bytes if size_bytes is not None else len(APK_BYTES)
                ),
                "signed": signed,
                "signature_scheme": ACCEPTED_SIGNATURE_SCHEME,
            }
        ],
    }
    return {
        "schema": schema,
        "version": VERSION_NAME,
        "channels": {"android": channel},
    }


def write_manifest(path: Path, model: dict) -> Path:
    path.write_text(json.dumps(model, indent=2), encoding="utf-8")
    return path


def make_manifest(tmp_path: Path, name: str = "expected.json", **kwargs) -> Path:
    return write_manifest(tmp_path / name, manifest_model(**kwargs))


class FakeAndroidTools:
    """Records calls, replays canned apksigner/aapt outcomes."""

    def __init__(self, *, apksigner: CommandOutcome | None = None,
                 aapt: CommandOutcome | None = None):
        self.apksigner = apksigner or CommandOutcome(0, APKSIGNER_OK, "")
        self.aapt = aapt or CommandOutcome(0, BADGING_OK, "")
        self.calls: list[tuple] = []

    def resolver(self, kind: str, explicit=None):
        name = "apksigner" if kind == "apksigner" else "aapt2"
        return Path(f"/fake-tools/{name}"), name

    def __call__(self, argv, env):
        argv = tuple(argv)
        self.calls.append(argv)
        program = Path(argv[0]).name
        if "apksigner" in program:
            return self.apksigner
        return self.aapt


def staging(tmp_path: Path) -> Path:
    root = tmp_path / "staging"
    root.mkdir(exist_ok=True)
    return root


def make_junction(link: Path, target: Path) -> bool:
    """Create a real Windows directory junction; False when unavailable.

    Output is captured as bytes (localized cmd output breaks utf-8 decode).
    No network, no elevation: ``mklink /J`` needs no symlink privilege.
    """
    if os.name != "nt" or link.exists():
        return False
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
    )
    return proc.returncode == 0 and link.exists()


def previous_manifest(tmp_path: Path, version_code: int = PREVIOUS_VERSION_CODE,
                      name: str = "previous.json") -> Path:
    prev_dir = tmp_path / "prev"
    prev_dir.mkdir(exist_ok=True)
    return write_manifest(
        prev_dir / name, manifest_model(version_code=version_code)
    )


def stage(tmp_path: Path, *, apk: Path | None = None,
          manifest: Path | None = None,
          prev: Path | None = None,
          root: Path | None = None,
          tools: FakeAndroidTools | None = None):
    tools = tools or FakeAndroidTools()
    return run_stage(
        apk=str(apk if apk is not None else make_apk(tmp_path)),
        manifest=str(manifest if manifest is not None else make_manifest(tmp_path)),
        staging_root=str(root if root is not None else staging(tmp_path)),
        previous_manifest=str(prev) if prev is not None else None,
        runner=tools,
        tool_resolver=tools.resolver,
    )


def codes_of(result: dict) -> list[str]:
    return [f["code"] for f in result["failures"]]


# ---------------------------------------------------------------------------
# staging root and target validation
# ---------------------------------------------------------------------------


class TestStagingValidation:
    def test_staging_root_must_be_a_directory(self, tmp_path):
        not_a_dir = tmp_path / "file-root"
        not_a_dir.write_text("x", encoding="ascii")
        result, code = stage(tmp_path, root=not_a_dir)
        assert code == 1
        assert "staging_root_invalid" in codes_of(result)

    def test_android_child_that_is_a_file_is_rejected(self, tmp_path):
        root = staging(tmp_path)
        (root / "android").write_text("not a dir", encoding="ascii")
        result, code = stage(tmp_path, root=root)
        assert code == 1
        assert "staging_android_invalid" in codes_of(result)
        # the impostor file is untouched
        assert (root / "android").read_text(encoding="ascii") == "not a dir"

    @pytest.mark.skipif(
        os.name == "nt", reason="symlink privileges vary on Windows"
    )
    def test_symlinked_staging_root_rejected(self, tmp_path):
        real = staging(tmp_path)
        link = tmp_path / "staging-link"
        os.symlink(real, link, target_is_directory=True)
        result, code = stage(tmp_path, root=link)
        assert code == 1
        assert "staging_root_symlink" in codes_of(result)

    def test_junctioned_staging_root_rejected_windows(self, tmp_path):
        """Real directory junction as staging root: is_symlink() is False on
        Python 3.11, the reparse-aware detector must still fail closed."""
        real = staging(tmp_path)
        link = tmp_path / "staging-junction"
        if not make_junction(link, real):
            pytest.skip("junction creation unavailable on this host")
        result, code = stage(tmp_path, root=link)
        assert code == 1
        assert "staging_root_symlink" in codes_of(result)
        # nothing landed inside the junction target
        assert not any(real.iterdir())

    def test_junctioned_android_child_rejected_windows(self, tmp_path):
        """Junction named android/ under a real staging root: the staging
        shape gate fails closed on the link-shaped child before staging."""
        root = staging(tmp_path)
        real_android = tmp_path / "real-android"
        real_android.mkdir()
        if not make_junction(root / "android", real_android):
            pytest.skip("junction creation unavailable on this host")
        result, code = stage(tmp_path, root=root)
        assert code == 1
        assert "staging_android_invalid" in codes_of(result)
        # nothing was written through the junction
        assert not any(real_android.iterdir())

    def test_detector_seam_rejects_flagged_staging_root(self, tmp_path,
                                                        monkeypatch):
        """Platform-independent wiring proof: a flagged (e.g. junctioned)
        staging root fails closed even where junctions cannot be created."""
        real = staging(tmp_path)

        def flag_staging(path):
            return Path(path) == real

        monkeypatch.setattr(
            "tools.android_release.stage_download._link_detector",
            flag_staging,
        )
        result, code = stage(tmp_path, root=real)
        assert code == 1
        assert "staging_root_symlink" in codes_of(result)

    def test_detector_seam_rejects_flagged_android_child(self, tmp_path,
                                                         monkeypatch):
        root = staging(tmp_path)
        android = root / "android"
        android.mkdir()

        def flag_android(path):
            return Path(path) == android

        monkeypatch.setattr(
            "tools.android_release.stage_download._link_detector",
            flag_android,
        )
        result, code = stage(tmp_path, root=root)
        assert code == 1
        assert "staging_android_invalid" in codes_of(result)
        # the staging-shape gate fired before any write; android/ stays empty
        assert not any(android.iterdir())

    def test_entry_name_with_path_separator_rejected(self):
        # defense in depth: a verify-passing entry name can never contain a
        # separator (the URL identity check enforces it), and the staging
        # validator independently rejects anything that slips through
        from tools.android_release.stage_download import (
            validate_stage_target,
        )

        assert validate_stage_target("sub/dir.apk") == "staging_target_invalid"
        assert validate_stage_target("a\\b.apk") == "staging_target_invalid"
        assert validate_stage_target("..") == "staging_target_invalid"
        assert validate_stage_target(".") == "staging_target_invalid"
        assert validate_stage_target("") == "staging_target_invalid"
        assert validate_stage_target(APK_NAME) is None

    def test_manifest_entry_not_found_leaves_staging_untouched(self, tmp_path):
        manifest = make_manifest(tmp_path, entry_name="other-name.apk")
        apk = make_apk(tmp_path, name="dir.apk")
        result, code = stage(tmp_path, apk=apk, manifest=manifest)
        assert code == 1
        assert "manifest_entry_not_found" in codes_of(result)
        root = tmp_path / "staging"
        assert not (root / "manifest.json").exists()

    def test_existing_target_apk_refused_not_overwritten(self, tmp_path):
        root = staging(tmp_path)
        android = root / "android"
        android.mkdir()
        existing = android / APK_NAME
        existing.write_bytes(b"previous-release-bytes")
        result, code = stage(tmp_path, root=root)
        assert code == 1
        assert "staging_target_exists" in codes_of(result)
        assert existing.read_bytes() == b"previous-release-bytes"


# ---------------------------------------------------------------------------
# verify gate (reuses M14-173 semantics)
# ---------------------------------------------------------------------------


class TestVerifyGate:
    def test_verify_failure_blocks_all_writes(self, tmp_path):
        manifest = make_manifest(tmp_path, sha256="0" * 64)
        root = staging(tmp_path)
        result, code = stage(tmp_path, manifest=manifest, root=root)
        assert code == 1
        assert "sha256_mismatch" in codes_of(result)
        assert result["checks"]["verify"]["status"] == "failed"
        assert not (root / "android").exists() or not any(
            (root / "android").iterdir()
        )
        assert not (root / "manifest.json").exists()

    def test_unsigned_entry_blocked_by_verify(self, tmp_path):
        manifest = make_manifest(tmp_path, signed=False)
        result, code = stage(tmp_path, manifest=manifest)
        assert code == 1
        assert "manifest_entry_not_signed" in codes_of(result)

    def test_version_code_must_strictly_increase(self, tmp_path):
        prev = previous_manifest(tmp_path, version_code=VERSION_CODE)
        result, code = stage(tmp_path, prev=prev)
        assert code == 1
        assert "version_code_not_greater" in codes_of(result)
        root = tmp_path / "staging"
        assert not (root / "manifest.json").exists()

    def test_previous_manifest_with_wrong_schema_blocked(self, tmp_path):
        prev = previous_manifest(tmp_path, name="imposter.json")
        # rewrite the previous manifest without the download-manifest schema
        model = manifest_model(version_code=PREVIOUS_VERSION_CODE)
        model["schema"] = "something-else/9"
        write_manifest(prev, model)
        result, code = stage(tmp_path, prev=prev)
        assert code == 1
        assert "previous_manifest_invalid" in codes_of(result)

    def test_without_previous_manifest_monotonicity_is_honest(
        self, tmp_path
    ):
        result, code = stage(tmp_path)
        assert code == 0
        mono = result["checks"]["verify"]["version_code_monotonic"]
        assert mono["status"] == "not_provided"


# ---------------------------------------------------------------------------
# success path and recheck
# ---------------------------------------------------------------------------


class TestStageSuccess:
    def test_stages_apk_and_manifest_atomically(self, tmp_path):
        root = staging(tmp_path)
        manifest = make_manifest(tmp_path)
        result, code = stage(tmp_path, manifest=manifest, root=root)
        assert code == 0
        assert result["status"] == "staged"
        assert result["tool"] == TOOL_NAME
        assert result["schema_version"] == SCHEMA_VERSION
        staged_apk = root / "android" / APK_NAME
        assert staged_apk.is_file()
        assert staged_apk.read_bytes() == APK_BYTES
        staged_manifest = root / "manifest.json"
        assert staged_manifest.read_bytes() == manifest.read_bytes()
        assert result["staged"]["apk_basename"] == APK_NAME
        assert result["staged"]["sha256"] == apk_sha256()
        assert result["staged"]["size_bytes"] == len(APK_BYTES)
        assert result["staged"]["version_code"] == VERSION_CODE
        assert result["staged"]["version_name"] == VERSION_NAME

    def test_recheck_passes_and_is_reported(self, tmp_path):
        result, code = stage(tmp_path)
        assert code == 0
        recheck = result["checks"]["recheck"]
        assert recheck["apk_digest"]["status"] == "pass"
        assert recheck["manifest_reparsed"]["status"] == "pass"

    def test_previous_apk_in_android_dir_is_never_touched(self, tmp_path):
        root = staging(tmp_path)
        android = root / "android"
        android.mkdir()
        old = android / OLD_APK_NAME
        old.write_bytes(b"old-release-bytes")
        prev = previous_manifest(tmp_path)
        _result, code = stage(tmp_path, prev=prev, root=root)
        assert code == 0
        assert old.read_bytes() == b"old-release-bytes"
        assert (android / APK_NAME).is_file()

    def test_previous_manifest_at_staging_manifest_path_is_replaced(
        self, tmp_path
    ):
        root = staging(tmp_path)
        prev = write_manifest(
            root / "manifest.json",
            manifest_model(version_code=PREVIOUS_VERSION_CODE),
        )
        _result, code = stage(tmp_path, prev=prev, root=root)
        assert code == 0
        model = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        assert model["channels"]["android"]["versionCode"] == VERSION_CODE

    def test_output_is_deterministic_and_value_free(
        self, tmp_path, capsys, monkeypatch
    ):
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        from tools.android_release.stage_download import render_json

        result_a, _ = run_stage(
            apk=str(make_apk(tmp_path / "a")),
            manifest=str(make_manifest(tmp_path / "a")),
            staging_root=str(root_a),
            runner=FakeAndroidTools(),
            tool_resolver=FakeAndroidTools().resolver,
        )
        result_b, _ = run_stage(
            apk=str(make_apk(tmp_path / "b")),
            manifest=str(make_manifest(tmp_path / "b")),
            staging_root=str(root_b),
            runner=FakeAndroidTools(),
            tool_resolver=FakeAndroidTools().resolver,
        )
        rendered_a = render_json(result_a)
        rendered_b = render_json(result_b)
        assert rendered_a == rendered_b
        assert str(tmp_path) not in rendered_a
        assert PAYLOAD_MARKER not in rendered_a
        assert DN_MARKER not in rendered_a
        assert ".tmp" not in rendered_a


# ---------------------------------------------------------------------------
# failure recovery (half-written state, tampered recheck)
# ---------------------------------------------------------------------------


class TestFailureRecovery:
    def test_apk_replace_failure_cleans_temp_and_writes_nothing(self, tmp_path,
                                                                monkeypatch):
        import tools.android_release.stage_download as sd

        def failing_replace(src, dst):
            raise OSError(f"replace failed {PAYLOAD_MARKER}")

        monkeypatch.setattr(sd, "_os_replace", failing_replace)
        root = staging(tmp_path)
        result, code = stage(tmp_path, root=root)
        assert code == 1
        assert "staging_write_failed" in codes_of(result)
        android = root / "android"
        if android.exists():
            assert not any(android.iterdir())
        assert not (root / "manifest.json").exists()

    def test_manifest_replace_failure_restores_previous_manifest(
        self, tmp_path, monkeypatch
    ):
        import tools.android_release.stage_download as sd

        root = staging(tmp_path)
        previous_bytes = write_manifest(
            root / "manifest.json",
            manifest_model(version_code=PREVIOUS_VERSION_CODE),
        ).read_bytes()

        state = {"replaces": 0}
        real_replace = sd._os_replace

        def replace_twice_then_fail(src, dst):
            state["replaces"] += 1
            if state["replaces"] == 1:
                return real_replace(src, dst)  # APK lands
            raise OSError("manifest replace fails")

        monkeypatch.setattr(sd, "_os_replace", replace_twice_then_fail)
        result, code = stage(tmp_path, root=root)
        assert code == 1
        assert "staging_write_failed" in codes_of(result)
        # previous manifest restored byte-for-byte; staged APK cleaned up
        assert (root / "manifest.json").read_bytes() == previous_bytes
        assert not (root / "android" / APK_NAME).exists()

    def test_tampered_staged_apk_detected_by_recheck(self, tmp_path,
                                                     monkeypatch):
        import tools.android_release.stage_download as sd

        root = staging(tmp_path)
        real_read = sd._reread_file

        def tamper(path: Path) -> bytes:
            data = real_read(path)
            if data == APK_BYTES:
                return b"tampered" + data
            return data

        monkeypatch.setattr(sd, "_reread_file", tamper)
        result, code = stage(tmp_path, root=root)
        assert code == 1
        assert "recheck_sha256_mismatch" in codes_of(result)
        # tampered artifact removed; staging left without the new APK
        assert not (root / "android" / APK_NAME).exists()

    def test_recheck_failure_restores_previous_manifest(self, tmp_path,
                                                        monkeypatch):
        import tools.android_release.stage_download as sd

        root = staging(tmp_path)
        previous_bytes = write_manifest(
            root / "manifest.json",
            manifest_model(version_code=PREVIOUS_VERSION_CODE),
        ).read_bytes()
        real_read = sd._reread_file

        def tamper(path: Path) -> bytes:
            data = real_read(path)
            if data == APK_BYTES:
                return b"tampered" + data
            return data

        monkeypatch.setattr(sd, "_reread_file", tamper)
        _result, code = stage(tmp_path, root=root)
        assert code == 1
        assert (root / "manifest.json").read_bytes() == previous_bytes

    def test_verify_spawn_failure_leaves_staging_untouched(self, tmp_path):
        class FailingTools(FakeAndroidTools):
            def __call__(self, argv, env):
                program = Path(argv[0]).name
                if "apksigner" in program:
                    raise OSError(f"spawn failed {PAYLOAD_MARKER}")
                return self.aapt

        root = staging(tmp_path)
        result, code = stage(tmp_path, root=root, tools=FailingTools())
        assert code == 1
        assert "tool_spawn_failed" in codes_of(result)
        assert not (root / "manifest.json").exists()


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestCli:
    def test_cli_stage_prints_json_and_exits_zero(self, tmp_path, monkeypatch,
                                                  capsys):
        root = staging(tmp_path)
        tools = FakeAndroidTools()
        # run_verify resolves its own module-level defaults; patch there
        monkeypatch.setattr(
            "tools.android_release.verify_artifact.default_runner", tools
        )
        monkeypatch.setattr(
            "tools.android_release.verify_artifact.default_tool_resolver",
            tools.resolver,
        )
        code = main(
            [
                "--apk", str(make_apk(tmp_path)),
                "--manifest", str(make_manifest(tmp_path)),
                "--staging-root", str(root),
            ]
        )
        rendered = capsys.readouterr().out
        result = json.loads(rendered)
        assert code == 0
        assert result["status"] == "staged"
        assert (root / "android" / APK_NAME).is_file()

    def test_cli_missing_staging_root_fails_closed(self, tmp_path, capsys):
        code = main(
            [
                "--apk", str(make_apk(tmp_path)),
                "--manifest", str(make_manifest(tmp_path)),
                "--staging-root", str(tmp_path / "does-not-exist"),
            ]
        )
        assert code == 1
        result = json.loads(capsys.readouterr().out)
        assert "staging_root_invalid" in codes_of(result)
