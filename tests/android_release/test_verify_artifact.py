"""Tests for tools.android_release.verify_artifact (M14-173 readiness gate).

Every test injects a fake subprocess runner and a fake tool resolver and uses
placeholder APK bytes plus temporary manifests: apksigner/aapt are never
executed, no Android SDK is required, nothing reaches the network, and no real
keystore or secret is ever created or read.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.android_release.verify_artifact import (
    ACCEPTED_SIGNATURE_SCHEME,
    EXIT_FAILURE,
    EXIT_OK,
    MANIFEST_SCHEMA,
    SCRUBBED_ENV_VARS,
    SCHEMA_VERSION,
    TOOL_NAME,
    CommandOutcome,
    main,
    render_json,
    resolve_android_tool,
    run_verify,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

APK_NAME = "ai-learning-os-0.14.0-release-signed.apk"
APK_BYTES = b"placeholder-apk-bytes-not-a-real-artifact"
PACKAGE_NAME = "com.ailearningos.app"
VERSION_CODE = 15
VERSION_NAME = "0.14.0"
PREVIOUS_VERSION_CODE = 14

# Markers that must never reach the rendered JSON, whatever happens.
PAYLOAD_MARKER = "payload-marker-must-never-appear"
DN_MARKER = "CN=Leaky-Certificate-Subject-Must-Never-Appear"
RELEASE_DN = "CN=AI Learning OS Release, O=AI Learning OS, C=CN"

APKSIGNER_OK = f"""Verifies
Verified using v1 scheme (JAR signing): false
Verified using v2 scheme (APK Signature Scheme v2): true
Verified using v3 scheme (APK Signature Scheme v3): true
Verified using v3.1 scheme (APK Signature Scheme v3.1): false
Verified using v4 scheme (APK Signature Scheme v4): false
Signer #1 certificate DN: {RELEASE_DN}
Signer #1 certificate SHA-256 digest: {PAYLOAD_MARKER}
{PAYLOAD_MARKER}
"""

APKSIGNER_DEBUG_DN = APKSIGNER_OK.replace(
    RELEASE_DN, "CN=Android Debug, O=Android, C=US"
)
APKSIGNER_ALIAS_DN = APKSIGNER_OK.replace(
    RELEASE_DN, "CN=something, OU=androiddebugkey, O=Android"
)

BADGING_OK = (
    f"package: name='{PACKAGE_NAME}' versionCode='{VERSION_CODE}' "
    f"versionName='{VERSION_NAME}' platformBuildVersionName='placeholder'\n"
    "sdkVersion:'26'\n"
    "application-label:'AI Learning OS'\n"
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Keep host SDK pointers, signing inputs and proxies out of tests."""
    for name in (
        "ANDROID_HOME", "ANDROID_SDK_ROOT", *SCRUBBED_ENV_VARS,
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


def make_manifest(
    tmp_path: Path,
    *,
    sha256: str | None = None,
    size_bytes: int | None = None,
    entry_name: str = APK_NAME,
    url: str | None = None,
    signed: bool = True,
    signature_scheme: str | None = ACCEPTED_SIGNATURE_SCHEME,
    package_name: str | None = PACKAGE_NAME,
    version_code: int | str = VERSION_CODE,
    version_name: str | None = VERSION_NAME,
    schema: str = MANIFEST_SCHEMA,
    drop_android_channel: bool = False,
    duplicate_entry: bool = False,
) -> Path:
    channel: dict = {
        "versionCode": version_code,
        "versionName": version_name,
    }
    if package_name is not None:
        channel["package_name"] = package_name
    entry: dict = {
        "name": entry_name,
        "url": url if url is not None else f"/android/{entry_name}",
        "sha256": sha256 if sha256 is not None else apk_sha256(),
        "size_bytes": (
            size_bytes if size_bytes is not None else len(APK_BYTES)
        ),
        "signed": signed,
    }
    if signature_scheme is not None:
        entry["signature_scheme"] = signature_scheme
    channel["files"] = [entry, dict(entry)] if duplicate_entry else [entry]
    manifest = {
        "schema": schema,
        "version": version_name,
        "channels": {} if drop_android_channel else {"android": channel},
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def make_previous_manifest(tmp_path: Path, version_code: int) -> Path:
    """Previous manifest in its own directory (never overwrites the current one)."""
    prev_dir = tmp_path / "prev"
    prev_dir.mkdir(exist_ok=True)
    return make_manifest(prev_dir, version_code=version_code)


class FakeAndroidTools:
    """Records calls, replays canned apksigner/aapt outcomes.

    Never spawns a process. Dispatches on the program basename, so the same
    fake serves both the runner and the resolver roles.
    """

    def __init__(
        self,
        *,
        apksigner: CommandOutcome | None = None,
        aapt: CommandOutcome | None = None,
        apksigner_spawn_error: bool = False,
        apksigner_missing: bool = False,
        aapt_missing: bool = False,
    ):
        self.apksigner = apksigner or CommandOutcome(0, APKSIGNER_OK, "")
        self.aapt = aapt or CommandOutcome(0, BADGING_OK, "")
        self.apksigner_spawn_error = apksigner_spawn_error
        self.apksigner_missing = apksigner_missing
        self.aapt_missing = aapt_missing
        self.calls: list[dict] = []

    def resolver(self, kind: str, explicit=None):
        missing = self.apksigner_missing if kind == "apksigner" else self.aapt_missing
        if missing:
            return None, None
        name = "apksigner" if kind == "apksigner" else "aapt2"
        return Path(f"/fake-tools/{name}"), name

    def __call__(self, argv, env):
        argv = tuple(argv)
        program = Path(argv[0]).name
        self.calls.append({"program": program, "argv": argv, "env": dict(env)})
        if self.apksigner_spawn_error and "apksigner" in program:
            raise OSError(f"spawn failed {PAYLOAD_MARKER}")
        if "apksigner" in program:
            return self.apksigner
        return self.aapt


def verify(
    tmp_path: Path,
    *,
    apk: Path | None = None,
    manifest: Path | None = None,
    previous_manifest: Path | None = None,
    previous_version_code: int | None = None,
    tools: FakeAndroidTools | None = None,
):
    tools = tools or FakeAndroidTools()
    return run_verify(
        apk=str(apk if apk is not None else make_apk(tmp_path)),
        manifest=str(manifest if manifest is not None else make_manifest(tmp_path)),
        previous_manifest=(
            str(previous_manifest) if previous_manifest is not None else None
        ),
        previous_version_code=previous_version_code,
        runner=tools,
        tool_resolver=tools.resolver,
    )


# ---------------------------------------------------------------------------
# success
# ---------------------------------------------------------------------------


class TestSuccess:
    def test_all_checks_pass_with_previous_manifest(self, tmp_path):
        apk = make_apk(tmp_path)
        prev_dir = tmp_path / "prev"
        prev_dir.mkdir()
        prev = make_manifest(prev_dir, version_code=PREVIOUS_VERSION_CODE)
        tools = FakeAndroidTools()
        result, code = run_verify(
            apk=str(apk),
            manifest=str(make_manifest(tmp_path)),
            previous_manifest=str(prev),
            runner=tools,
            tool_resolver=tools.resolver,
        )
        assert code == EXIT_OK
        assert result["status"] == "verified"
        assert result["failures"] == []
        checks = result["checks"]
        assert checks["apk_file"]["status"] == "pass"
        assert checks["apk_digest"]["status"] == "pass"
        assert checks["apk_digest"]["sha256_match"] is True
        assert checks["apk_digest"]["size_match"] is True
        assert checks["manifest_contract"]["status"] == "pass"
        assert checks["apksigner"]["status"] == "pass"
        assert checks["apksigner"]["schemes"] == {"v1": False, "v2": True, "v3": True}
        assert checks["apksigner"]["debug_certificate"] is False
        assert checks["aapt_badging"]["status"] == "pass"
        assert checks["aapt_badging"]["matches_manifest"] is True
        mono = checks["version_code_monotonic"]
        assert mono["status"] == "pass"
        assert mono["strictly_greater"] is True
        assert mono["previous"] == PREVIOUS_VERSION_CODE
        assert mono["actual"] == VERSION_CODE

    def test_schema_version_and_tool_recorded(self, tmp_path):
        result, _ = verify(tmp_path)
        assert result["schema_version"] == SCHEMA_VERSION
        assert result["tool"] == TOOL_NAME
        assert result["exit_code"] == EXIT_OK

    def test_v1_false_does_not_block_verification(self, tmp_path):
        """v1 允许为 false：仅 v2/v3 必需。"""
        result, code = verify(tmp_path)
        assert code == EXIT_OK
        assert result["checks"]["apksigner"]["schemes"]["v1"] is False


# ---------------------------------------------------------------------------
# apk file checks
# ---------------------------------------------------------------------------


class TestApkFile:
    def test_missing_apk_fails_closed(self, tmp_path):
        result, code = verify(tmp_path, apk=tmp_path / "no-such.apk")
        assert code == EXIT_FAILURE
        assert result["status"] == "failed"
        codes = [f["code"] for f in result["failures"]]
        assert "apk_missing" in codes
        assert result["checks"]["apk_file"]["status"] == "fail"

    def test_missing_apk_skips_downstream_checks(self, tmp_path):
        result, _ = verify(tmp_path, apk=tmp_path / "no-such.apk")
        assert result["checks"]["apksigner"]["status"] == "not_run"
        assert result["checks"]["aapt_badging"]["status"] == "not_run"

    def test_directory_instead_of_file_fails(self, tmp_path):
        apk_dir = tmp_path / "not-a-file.apk"
        apk_dir.mkdir()
        result, code = verify(tmp_path, apk=apk_dir)
        assert code == EXIT_FAILURE
        assert "apk_not_regular" in [f["code"] for f in result["failures"]]

    @pytest.mark.skipif(os.name == "nt", reason="symlink privileges vary on Windows")
    def test_symlink_apk_fails(self, tmp_path):
        real = make_apk(tmp_path, name="real.apk")
        link = tmp_path / "linked.apk"
        link.symlink_to(real)
        result, code = verify(tmp_path, apk=link)
        assert code == EXIT_FAILURE
        assert "apk_symlink" in [f["code"] for f in result["failures"]]

    def test_unreadable_apk_fails_closed(self, tmp_path, monkeypatch):
        apk = make_apk(tmp_path)
        monkeypatch.setattr(
            "tools.android_release.verify_artifact._read_apk_bytes",
            lambda _p: (_ for _ in ()).throw(OSError(f"boom {PAYLOAD_MARKER}")),
        )
        result, code = verify(tmp_path, apk=apk)
        assert code == EXIT_FAILURE
        assert "apk_unreadable" in [f["code"] for f in result["failures"]]
        assert PAYLOAD_MARKER not in render_json(result)


# ---------------------------------------------------------------------------
# digest checks
# ---------------------------------------------------------------------------


class TestDigest:
    def test_sha256_mismatch_fails(self, tmp_path):
        manifest = make_manifest(tmp_path, sha256="0" * 64)
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_FAILURE
        assert "sha256_mismatch" in [f["code"] for f in result["failures"]]
        digest = result["checks"]["apk_digest"]
        assert digest["sha256_match"] is False
        assert digest["actual_sha256"] == apk_sha256()
        assert digest["expected_sha256"] == "0" * 64

    def test_size_mismatch_fails(self, tmp_path):
        manifest = make_manifest(tmp_path, size_bytes=len(APK_BYTES) + 1)
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_FAILURE
        assert "size_mismatch" in [f["code"] for f in result["failures"]]
        assert result["checks"]["apk_digest"]["size_match"] is False


# ---------------------------------------------------------------------------
# manifest contract
# ---------------------------------------------------------------------------


class TestManifestContract:
    def test_manifest_unreadable_fails(self, tmp_path):
        result, code = verify(tmp_path, manifest=tmp_path / "absent.json")
        assert code == EXIT_FAILURE
        assert "manifest_unreadable" in [f["code"] for f in result["failures"]]
        # identity 期望值不可用 → aapt 比对不运行（prerequisite 缺失）
        assert result["checks"]["aapt_badging"]["status"] == "not_run"

    def test_manifest_invalid_json_fails(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not-json", encoding="utf-8")
        result, code = verify(tmp_path, manifest=bad)
        assert code == EXIT_FAILURE
        assert "manifest_invalid_json" in [f["code"] for f in result["failures"]]

    def test_wrong_schema_fails(self, tmp_path):
        manifest = make_manifest(tmp_path, schema="some-other-schema/9")
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_FAILURE
        assert "manifest_schema_unsupported" in [f["code"] for f in result["failures"]]

    def test_missing_android_channel_fails(self, tmp_path):
        manifest = make_manifest(tmp_path, drop_android_channel=True)
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_FAILURE
        assert "manifest_android_channel_missing" in [
            f["code"] for f in result["failures"]
        ]

    def test_entry_name_drift_fails(self, tmp_path):
        manifest = make_manifest(tmp_path, entry_name="some-other-file.apk")
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_FAILURE
        assert "manifest_entry_not_found" in [f["code"] for f in result["failures"]]
        assert result["checks"]["apk_digest"]["status"] == "not_run"

    def test_unsigned_entry_fails(self, tmp_path):
        manifest = make_manifest(tmp_path, signed=False)
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_FAILURE
        assert "manifest_entry_not_signed" in [f["code"] for f in result["failures"]]

    def test_signature_scheme_drift_fails(self, tmp_path):
        manifest = make_manifest(tmp_path, signature_scheme="v1+v2")
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_FAILURE
        assert "manifest_signature_scheme_mismatch" in [
            f["code"] for f in result["failures"]
        ]

    def test_signature_scheme_missing_fails_closed(self, tmp_path):
        """signature_scheme 是必填契约：缺失必须 fail-closed，而不是跳过比对。"""
        manifest = make_manifest(tmp_path, signature_scheme=None)
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_FAILURE
        codes = [f["code"] for f in result["failures"]]
        assert "manifest_signature_scheme_missing" in codes
        assert "manifest_signature_scheme_mismatch" not in codes
        assert (
            result["checks"]["manifest_contract"]["signature_scheme_accepted"]
            is False
        )

    def test_missing_package_name_fails(self, tmp_path):
        manifest = make_manifest(tmp_path, package_name=None)
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_FAILURE
        assert "manifest_package_name_missing" in [
            f["code"] for f in result["failures"]
        ]

    def test_placeholder_version_code_fails(self, tmp_path):
        """模板占位字符串不是合法 versionCode：fail-closed。"""
        manifest = make_manifest(tmp_path, version_code="<placeholder>")
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_FAILURE
        assert "manifest_version_code_invalid" in [
            f["code"] for f in result["failures"]
        ]

    def test_numeric_string_version_code_accepted(self, tmp_path):
        manifest = make_manifest(tmp_path, version_code=str(VERSION_CODE))
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_OK

    def test_placeholder_sha256_fails_closed(self, tmp_path):
        """模板占位 sha 字符串不是 64 位十六进制：fail-closed。"""
        manifest = make_manifest(tmp_path, sha256="<sha256sum 输出，占位>")
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_FAILURE
        assert "manifest_sha256_invalid" in [f["code"] for f in result["failures"]]

    def test_invalid_size_field_fails_closed(self, tmp_path):
        manifest = make_manifest(tmp_path, size_bytes="not-a-number")
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_FAILURE
        assert "manifest_size_invalid" in [f["code"] for f in result["failures"]]


class TestEntryUrl:
    """下载项 URL 身份：/android/ 相对路径且末段与文件名严格一致。"""

    def test_url_field_missing_fails_closed(self, tmp_path):
        manifest = make_manifest(tmp_path, url=None)
        model = json.loads(manifest.read_text(encoding="utf-8"))
        del model["channels"]["android"]["files"][0]["url"]
        manifest.write_text(json.dumps(model), encoding="utf-8")
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_FAILURE
        assert "manifest_entry_url_missing" in [
            f["code"] for f in result["failures"]
        ]
        assert result["checks"]["manifest_contract"]["entry_url_valid"] is False

    @pytest.mark.parametrize(
        "bad_url",
        [
            f"https://download.example.com/android/{APK_NAME}",  # absolute
            f"//download.example.com/android/{APK_NAME}",  # protocol-relative
            f"android/{APK_NAME}",  # not rooted
            f"/harmony/{APK_NAME}",  # wrong channel directory
            f"/android/{APK_NAME}?x=1",  # query string
            f"/android/{APK_NAME}#frag",  # fragment
            f"/android/{APK_NAME}\\evil",  # backslash
            f"/android/../{APK_NAME}",  # traversal
            f"/android/./{APK_NAME}",  # dot segment
            f"/android//{APK_NAME}",  # empty segment
            f"/android/%2e%2e/{APK_NAME}",  # percent-encoded traversal
            f"/android/{APK_NAME} ",  # trailing space (not HTTP-safe)
        ],
        ids=[
            "absolute", "protocol_relative", "not_rooted", "wrong_dir",
            "query", "fragment", "backslash", "traversal", "dot_segment",
            "empty_segment", "percent_traversal", "space",
        ],
    )
    def test_structurally_invalid_url_fails_closed(self, tmp_path, bad_url):
        manifest = make_manifest(tmp_path, url=bad_url)
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_FAILURE
        assert "manifest_entry_url_invalid" in [
            f["code"] for f in result["failures"]
        ]
        # 失败 detail 保持 value-free：不回显 URL 内容
        rendered = render_json(result)
        assert "download.example.com" not in rendered
        assert "%2e" not in rendered

    def test_url_final_segment_name_mismatch_fails(self, tmp_path):
        manifest = make_manifest(tmp_path, url=f"/android/some-other-file.apk")
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_FAILURE
        assert "manifest_entry_url_name_mismatch" in [
            f["code"] for f in result["failures"]
        ]

    def test_duplicate_matching_entries_fail_closed(self, tmp_path):
        manifest = make_manifest(tmp_path, duplicate_entry=True)
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_FAILURE
        assert "manifest_entry_duplicate" in [
            f["code"] for f in result["failures"]
        ]

    def test_valid_subdirectory_url_passes(self, tmp_path):
        """同频道子目录路径合法：末段一致即可。"""
        manifest = make_manifest(tmp_path, url=f"/android/v2/{APK_NAME}")
        result, code = verify(tmp_path, manifest=manifest)
        assert code == EXIT_OK
        assert result["checks"]["manifest_contract"]["entry_url_valid"] is True


# ---------------------------------------------------------------------------
# apksigner
# ---------------------------------------------------------------------------


def with_apksigner(stdout: str, returncode: int = 0) -> FakeAndroidTools:
    return FakeAndroidTools(apksigner=CommandOutcome(returncode, stdout, "stderr"))


class TestApksigner:
    def test_unsigned_apk_rejected(self, tmp_path):
        tools = FakeAndroidTools(
            apksigner=CommandOutcome(1, "DOES NOT VERIFY\n", f"err {PAYLOAD_MARKER}")
        )
        result, code = verify(tmp_path, tools=tools)
        assert code == EXIT_FAILURE
        assert "apksigner_verify_failed" in [f["code"] for f in result["failures"]]
        assert result["checks"]["apksigner"]["verified"] is False

    def test_v2_missing_fails(self, tmp_path):
        stdout = APKSIGNER_OK.replace(
            "Verified using v2 scheme (APK Signature Scheme v2): true",
            "Verified using v2 scheme (APK Signature Scheme v2): false",
        )
        result, code = verify(tmp_path, tools=with_apksigner(stdout))
        assert code == EXIT_FAILURE
        assert "scheme_v2_missing" in [f["code"] for f in result["failures"]]

    def test_v3_missing_fails(self, tmp_path):
        stdout = APKSIGNER_OK.replace(
            "Verified using v3 scheme (APK Signature Scheme v3): true",
            "Verified using v3 scheme (APK Signature Scheme v3): false",
        )
        result, code = verify(tmp_path, tools=with_apksigner(stdout))
        assert code == EXIT_FAILURE
        assert "scheme_v3_missing" in [f["code"] for f in result["failures"]]

    @pytest.mark.parametrize(
        "verbose", [APKSIGNER_DEBUG_DN, APKSIGNER_ALIAS_DN], ids=["cn", "alias"]
    )
    def test_debug_certificate_rejected(self, tmp_path, verbose):
        result, code = verify(tmp_path, tools=with_apksigner(verbose))
        assert code == EXIT_FAILURE
        failures = [f["code"] for f in result["failures"]]
        assert "debug_certificate" in failures
        assert result["checks"]["apksigner"]["debug_certificate"] is True

    def test_missing_signer_dn_fails_closed(self, tmp_path):
        """签名者证书主体缺失/不可解析时 fail-closed，且不得宣称 debug=false。"""
        stdout = APKSIGNER_OK.replace(
            f"Signer #1 certificate DN: {RELEASE_DN}\n", ""
        )
        result, code = verify(tmp_path, tools=with_apksigner(stdout))
        assert code == EXIT_FAILURE
        codes = [f["code"] for f in result["failures"]]
        assert "signer_certificate_missing" in codes
        assert result["checks"]["apksigner"]["debug_certificate"] is None

    def test_whitespace_signer_dn_fails_closed(self, tmp_path):
        """DN 行只剩空白字符：解析结果为空，同样按证书主体缺失 fail-closed。"""
        stdout = APKSIGNER_OK.replace(
            f"Signer #1 certificate DN: {RELEASE_DN}", "Signer #1 certificate DN: \t"
        )
        result, code = verify(tmp_path, tools=with_apksigner(stdout))
        assert code == EXIT_FAILURE
        assert "signer_certificate_missing" in [
            f["code"] for f in result["failures"]
        ]
        assert result["checks"]["apksigner"]["debug_certificate"] is None

    def test_unparsable_verbose_output_fails_closed(self, tmp_path):
        result, code = verify(tmp_path, tools=with_apksigner("garbage output\n"))
        assert code == EXIT_FAILURE
        assert "apksigner_output_unparsed" in [f["code"] for f in result["failures"]]

    def test_spawn_failure_fails_closed(self, tmp_path):
        tools = FakeAndroidTools(apksigner_spawn_error=True)
        result, code = verify(tmp_path, tools=tools)
        assert code == EXIT_FAILURE
        assert "tool_spawn_failed" in [f["code"] for f in result["failures"]]

    def test_apksigner_runs_even_when_digest_fails(self, tmp_path):
        """签名检查独立于 manifest：digest 失败时仍产出签名证据。"""
        manifest = make_manifest(tmp_path, sha256="0" * 64)
        result, _ = verify(tmp_path, manifest=manifest)
        assert result["checks"]["apksigner"]["status"] == "pass"


# ---------------------------------------------------------------------------
# aapt badging
# ---------------------------------------------------------------------------


def with_badging(stdout: str, returncode: int = 0) -> FakeAndroidTools:
    return FakeAndroidTools(aapt=CommandOutcome(returncode, stdout, ""))


class TestAaptBadging:
    def test_version_code_mismatch_fails(self, tmp_path):
        badging = BADGING_OK.replace(f"versionCode='{VERSION_CODE}'", "versionCode='14'")
        result, code = verify(tmp_path, tools=with_badging(badging))
        assert code == EXIT_FAILURE
        assert "version_code_mismatch" in [f["code"] for f in result["failures"]]

    def test_version_name_mismatch_fails(self, tmp_path):
        badging = BADGING_OK.replace(
            f"versionName='{VERSION_NAME}'", "versionName='0.13.9'"
        )
        result, code = verify(tmp_path, tools=with_badging(badging))
        assert code == EXIT_FAILURE
        assert "version_name_mismatch" in [f["code"] for f in result["failures"]]

    def test_package_name_mismatch_fails(self, tmp_path):
        badging = BADGING_OK.replace(PACKAGE_NAME, "com.evil.impersonator")
        result, code = verify(tmp_path, tools=with_badging(badging))
        assert code == EXIT_FAILURE
        assert "package_name_mismatch" in [f["code"] for f in result["failures"]]

    def test_aapt_failure_fails_closed(self, tmp_path):
        result, code = verify(
            tmp_path, tools=with_badging("", returncode=1)
        )
        assert code == EXIT_FAILURE
        assert "badging_failed" in [f["code"] for f in result["failures"]]

    def test_unparsable_badging_fails_closed(self, tmp_path):
        result, code = verify(tmp_path, tools=with_badging("no package line\n"))
        assert code == EXIT_FAILURE
        assert "badging_failed" in [f["code"] for f in result["failures"]]

    def test_monotonicity_not_evaluated_when_badging_failed(self, tmp_path):
        prev = make_previous_manifest(tmp_path, PREVIOUS_VERSION_CODE)
        result, _ = verify(
            tmp_path,
            previous_manifest=prev,
            tools=with_badging("", returncode=1),
        )
        assert result["checks"]["version_code_monotonic"]["status"] == "not_evaluated"


# ---------------------------------------------------------------------------
# versionCode monotonicity
# ---------------------------------------------------------------------------


class TestMonotonicity:
    def test_not_provided_when_no_previous_input(self, tmp_path):
        result, code = verify(tmp_path)
        assert code == EXIT_OK
        mono = result["checks"]["version_code_monotonic"]
        assert mono["status"] == "not_provided"
        assert mono["strictly_greater"] is None
        assert mono["previous"] is None
        assert "version_code_not_greater" not in [
            f["code"] for f in result["failures"]
        ]

    def test_previous_manifest_higher_actual_fails(self, tmp_path):
        prev = make_previous_manifest(tmp_path, VERSION_CODE + 5)
        result, code = verify(tmp_path, previous_manifest=prev)
        assert code == EXIT_FAILURE
        assert "version_code_not_greater" in [f["code"] for f in result["failures"]]

    def test_previous_version_code_equal_fails(self, tmp_path):
        result, code = verify(
            tmp_path, previous_version_code=VERSION_CODE
        )
        assert code == EXIT_FAILURE
        assert "version_code_not_greater" in [f["code"] for f in result["failures"]]

    def test_previous_version_code_lower_passes(self, tmp_path):
        result, code = verify(
            tmp_path, previous_version_code=PREVIOUS_VERSION_CODE
        )
        assert code == EXIT_OK
        assert result["checks"]["version_code_monotonic"]["strictly_greater"] is True

    def test_conflicting_previous_inputs_fail_closed(self, tmp_path):
        prev = make_previous_manifest(tmp_path, PREVIOUS_VERSION_CODE - 1)
        result, code = verify(
            tmp_path,
            previous_manifest=prev,
            previous_version_code=PREVIOUS_VERSION_CODE,
        )
        assert code == EXIT_FAILURE
        assert "previous_version_code_conflict" in [
            f["code"] for f in result["failures"]
        ]

    def test_agreeing_previous_inputs_pass(self, tmp_path):
        prev = make_previous_manifest(tmp_path, PREVIOUS_VERSION_CODE)
        result, code = verify(
            tmp_path,
            previous_manifest=prev,
            previous_version_code=PREVIOUS_VERSION_CODE,
        )
        assert code == EXIT_OK
        assert result["checks"]["version_code_monotonic"]["source"] == "both"

    def test_invalid_previous_manifest_fails_closed(self, tmp_path):
        bad = tmp_path / "prev-bad.json"
        bad.write_text("{oops", encoding="utf-8")
        result, code = verify(tmp_path, previous_manifest=bad)
        assert code == EXIT_FAILURE
        assert "previous_manifest_invalid" in [f["code"] for f in result["failures"]]

    def test_negative_previous_version_code_fails_closed(self, tmp_path):
        """防御：直接传负数给 run_verify 也必须 fail-closed（与 manifest 契约一致）。"""
        result, code = verify(tmp_path, previous_version_code=-3)
        assert code == EXIT_FAILURE
        codes = [f["code"] for f in result["failures"]]
        assert "previous_version_code_invalid" in codes
        mono = result["checks"]["version_code_monotonic"]
        assert mono["status"] == "not_evaluated"
        assert mono["strictly_greater"] is None


# ---------------------------------------------------------------------------
# tool discovery / availability
# ---------------------------------------------------------------------------


class TestToolAvailability:
    def test_apksigner_missing_fails_closed(self, tmp_path):
        tools = FakeAndroidTools(apksigner_missing=True)
        result, code = verify(tmp_path, tools=tools)
        assert code == EXIT_FAILURE
        failures = [f["code"] for f in result["failures"]]
        assert "tool_missing" in failures
        detail = [
            f["detail"] for f in result["failures"] if f["code"] == "tool_missing"
        ]
        assert {"tool": "apksigner"} in detail
        assert result["checks"]["apksigner"]["resolved"] is False
        assert result["checks"]["apksigner"]["status"] == "fail"

    def test_aapt_missing_fails_closed(self, tmp_path):
        tools = FakeAndroidTools(aapt_missing=True)
        result, code = verify(tmp_path, tools=tools)
        assert code == EXIT_FAILURE
        assert {"tool": "aapt"} in [
            f["detail"]
            for f in result["failures"]
            if f["code"] == "tool_missing"
        ]


class TestDiscovery:
    """resolve_android_tool 的 build-tools 约定发现（不下载、不猜测安装）。"""

    @pytest.mark.parametrize("tool", ["apksigner", "aapt"])
    def test_explicit_path_returned_when_regular_file(self, tmp_path, tool):
        fake = tmp_path / "explicit-tool"
        fake.write_bytes(b"placeholder")
        resolved, name = resolve_android_tool(tool, explicit=str(fake))
        assert resolved == fake
        assert name == "explicit-tool"

    @pytest.mark.parametrize("tool", ["apksigner", "aapt"])
    def test_explicit_path_missing_returns_none(self, tmp_path, tool):
        resolved, name = resolve_android_tool(
            tool, explicit=str(tmp_path / "absent-tool")
        )
        assert resolved is None
        assert name is None

    def test_discovers_newest_build_tools_via_android_home(self, tmp_path, monkeypatch):
        sdk = tmp_path / "sdk"
        for version, tool in (("33.0.2", "apksigner"), ("34.0.0", "apksigner")):
            d = sdk / "build-tools" / version
            d.mkdir(parents=True)
            (d / tool).write_bytes(b"placeholder")
        monkeypatch.setenv("ANDROID_HOME", str(sdk))
        resolved, name = resolve_android_tool("apksigner")
        assert resolved == sdk / "build-tools" / "34.0.0" / "apksigner"
        assert name == "apksigner"

    def test_android_sdk_root_is_second_search_root(self, tmp_path, monkeypatch):
        sdk = tmp_path / "sdk2"
        d = sdk / "build-tools" / "34.0.0"
        d.mkdir(parents=True)
        (d / "aapt2").write_bytes(b"placeholder")
        monkeypatch.setenv("ANDROID_SDK_ROOT", str(sdk))
        resolved, name = resolve_android_tool("aapt")
        assert resolved == d / "aapt2"
        assert name == "aapt2"

    def test_aapt_falls_back_to_legacy_aapt(self, tmp_path, monkeypatch):
        sdk = tmp_path / "sdk3"
        d = sdk / "build-tools" / "30.0.3"
        d.mkdir(parents=True)
        (d / "aapt").write_bytes(b"placeholder")
        monkeypatch.setenv("ANDROID_HOME", str(sdk))
        resolved, name = resolve_android_tool("aapt")
        assert resolved == d / "aapt"
        assert name == "aapt"

    def test_windows_suffixes_probed(self, tmp_path, monkeypatch):
        sdk = tmp_path / "sdk-win"
        d = sdk / "build-tools" / "34.0.0"
        d.mkdir(parents=True)
        (d / "apksigner.bat").write_bytes(b"placeholder")
        monkeypatch.setenv("ANDROID_HOME", str(sdk))
        resolved, name = resolve_android_tool("apksigner")
        if os.name == "nt":
            assert resolved == d / "apksigner.bat"
            assert name == "apksigner.bat"
        else:
            assert resolved is None

    def test_no_sdk_env_returns_none(self, tmp_path):
        resolved, name = resolve_android_tool("apksigner")
        assert resolved is None and name is None

    def test_empty_sdk_dir_returns_none(self, tmp_path, monkeypatch):
        sdk = tmp_path / "empty-sdk"
        (sdk / "build-tools").mkdir(parents=True)
        monkeypatch.setenv("ANDROID_HOME", str(sdk))
        resolved, name = resolve_android_tool("apksigner")
        assert resolved is None and name is None


# ---------------------------------------------------------------------------
# no-leak guarantees
# ---------------------------------------------------------------------------


class TestNoLeaks:
    def test_tool_output_and_dn_never_leak(self, tmp_path):
        noisy = APKSIGNER_OK + f"{PAYLOAD_MARKER}\nstderr:{DN_MARKER}\n"
        tools = FakeAndroidTools(apksigner=CommandOutcome(0, noisy, DN_MARKER))
        result, _ = verify(tmp_path, tools=tools)
        rendered = render_json(result)
        assert PAYLOAD_MARKER not in rendered
        assert DN_MARKER not in rendered
        assert RELEASE_DN not in rendered

    def test_absolute_paths_never_leak_on_missing_apk(self, tmp_path):
        secret_dir = tmp_path / f"secret-dir-{PAYLOAD_MARKER}"
        secret_dir.mkdir()
        result, _ = verify(tmp_path, apk=secret_dir / "nope.apk")
        rendered = render_json(result)
        assert str(tmp_path) not in rendered
        assert "secret-dir" not in rendered
        # filename 只允许 basename
        assert result["checks"]["apk_file"]["filename"] == "nope.apk"

    def test_sdk_env_value_never_leaks_when_tool_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANDROID_HOME", str(tmp_path / f"sdk-{PAYLOAD_MARKER}"))
        tools = FakeAndroidTools(apksigner_missing=True, aapt_missing=True)
        result, _ = verify(tmp_path, tools=tools)
        rendered = render_json(result)
        assert PAYLOAD_MARKER not in rendered
        assert "ANDROID_HOME" not in rendered

    def test_child_env_scrubs_android_signing_inputs(self, tmp_path, monkeypatch):
        for name in SCRUBBED_ENV_VARS:
            monkeypatch.setenv(name, f"{name}={PAYLOAD_MARKER}")
        tools = FakeAndroidTools()
        verify(tmp_path, tools=tools)
        assert tools.calls, "fake runner must have been invoked"
        for call in tools.calls:
            for name in SCRUBBED_ENV_VARS:
                assert name not in call["env"]

    def test_failures_carry_only_category_and_safe_detail(self, tmp_path):
        manifest = make_manifest(tmp_path, sha256="0" * 64)
        result, _ = verify(tmp_path, manifest=manifest)
        for failure in result["failures"]:
            assert set(failure) <= {"code", "detail"}


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_inputs_render_identical_bytes(self, tmp_path):
        first, _ = verify(tmp_path)
        second, _ = verify(tmp_path)
        assert render_json(first).encode("utf-8") == render_json(second).encode(
            "utf-8"
        )

    def test_failures_sorted_for_stable_output(self, tmp_path):
        manifest = make_manifest(
            tmp_path, sha256="0" * 64, size_bytes=1
        )
        result, _ = verify(tmp_path, manifest=manifest)
        codes = [f["code"] for f in result["failures"]]
        assert codes == sorted(codes)

    def test_no_timestamps_in_output(self, tmp_path):
        result, _ = verify(tmp_path)
        rendered = render_json(result)
        assert "generated_at" not in rendered
        assert "timestamp" not in rendered


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCliInProcess:
    """main() 直跑（注入 fake runner/resolver），验证 argparse 与退出码。"""

    def test_cli_success_exit_zero(self, tmp_path, capsys, monkeypatch):
        apk = make_apk(tmp_path)
        manifest = make_manifest(tmp_path)
        tools = FakeAndroidTools()
        monkeypatch.setattr(
            "tools.android_release.verify_artifact.default_runner", tools
        )
        monkeypatch.setattr(
            "tools.android_release.verify_artifact.default_tool_resolver",
            tools.resolver,
        )
        code = main([
            "--apk", str(apk),
            "--manifest", str(manifest),
            "--previous-version-code", str(PREVIOUS_VERSION_CODE),
        ])
        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "verified"

    def test_cli_failure_exit_one(self, tmp_path, capsys, monkeypatch):
        apk = make_apk(tmp_path)
        manifest = make_manifest(tmp_path, sha256="0" * 64)
        tools = FakeAndroidTools()
        monkeypatch.setattr(
            "tools.android_release.verify_artifact.default_runner", tools
        )
        monkeypatch.setattr(
            "tools.android_release.verify_artifact.default_tool_resolver",
            tools.resolver,
        )
        code = main(["--apk", str(apk), "--manifest", str(manifest)])
        assert code == EXIT_FAILURE
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "failed"

    def test_cli_requires_apk_and_manifest(self):
        with pytest.raises(SystemExit) as exc:
            main(["--apk", "x"])
        assert exc.value.code == 2

    def test_cli_negative_previous_version_code_rejected(self):
        """argparse 层拒绝负数 --previous-version-code（SystemExit 2）。"""
        with pytest.raises(SystemExit) as exc:
            main([
                "--apk", "x",
                "--manifest", "y",
                "--previous-version-code", "-1",
            ])
        assert exc.value.code == 2


class TestCliSubprocess:
    def test_subprocess_missing_apk_exits_one_without_sdk(self, tmp_path):
        """真实子进程：请求无效时不探测 SDK、不联网，fail-closed 退出 1。"""
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"ANDROID_HOME", "ANDROID_SDK_ROOT", *SCRUBBED_ENV_VARS}
        }
        manifest = make_manifest(tmp_path)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.android_release.verify_artifact",
                "--apk",
                str(tmp_path / "absent.apk"),
                "--manifest",
                str(manifest),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        assert proc.returncode == 1
        payload = json.loads(proc.stdout)
        assert payload["status"] == "failed"
        assert str(tmp_path) not in proc.stdout
        assert "absent.apk" in payload["checks"]["apk_file"]["filename"]
