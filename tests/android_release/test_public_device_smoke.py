"""Offline contract tests for the public Android physical-device smoke tool.

Every transport, verifier, ADB device, clock, and file-writing dependency is
injected. No test contacts the network, runs apksigner/aapt, or uses a device.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.android_release import public_device_smoke as smoke
from tools.android_release.verify_artifact import (
    ACCEPTED_SIGNATURE_SCHEME,
    MANIFEST_SCHEMA,
)
from tools.android_smoke.adb import AdbError

MANIFEST_URL = "https://public.example/aios/download-manifest.json"
APK_URL = "https://public.example/android/app-release-signed.apk"
API_BASE_URL = "https://public.example/aios"
SERIAL = "EYFBB22923201473"
PACKAGE = "com.ailearningos.app"
APK_NAME = "app-release-signed.apk"
APK_BYTES = b"placeholder-signed-apk"
VERSION_CODE = 2
VERSION_NAME = "0.14.0"
SECRET_MARKER = "secret-value-must-never-appear"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_bytes(
    *,
    url: str = "/android/app-release-signed.apk",
    schema: str = MANIFEST_SCHEMA,
    signed: bool = True,
) -> bytes:
    model = {
        "schema": schema,
        "version": VERSION_NAME,
        "channels": {
            "android": {
                "package_name": PACKAGE,
                "versionCode": VERSION_CODE,
                "versionName": VERSION_NAME,
                "files": [
                    {
                        "name": APK_NAME,
                        "url": url,
                        "sha256": digest(APK_BYTES),
                        "size_bytes": len(APK_BYTES),
                        "signed": signed,
                        "signature_scheme": ACCEPTED_SIGNATURE_SCHEME,
                    }
                ],
            }
        },
    }
    return json.dumps(model).encode("utf-8")


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None):
        self.status = status
        self.headers = headers or {}
        self.body = body
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk, self.body = self.body, b""
        else:
            chunk, self.body = self.body[:size], self.body[size:]
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeHttp:
    def __init__(self, responses: dict[str, FakeResponse | Exception] | None = None):
        self.responses = responses or {}
        self.urls: list[str] = []

    def get(self, url: str, timeout_seconds: int) -> FakeResponse:
        self.urls.append(url)
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        return value


class FakeVerifier:
    def __init__(self, exit_code: int = 0):
        self.exit_code = exit_code
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        failures = [] if self.exit_code == 0 else [{"code": "apksigner_failed"}]
        return {
            "status": "verified" if self.exit_code == 0 else "failed",
            "exit_code": self.exit_code,
            "failures": failures,
            "checks": {
                "apksigner": {
                    "status": "pass" if self.exit_code == 0 else "fail",
                    "v2": self.exit_code == 0,
                    "v3": self.exit_code == 0,
                    "debug_certificate": False,
                },
                "aapt_badging": {
                    "package_name": PACKAGE,
                    "version_code": VERSION_CODE,
                    "version_name": VERSION_NAME,
                    "matches_manifest": self.exit_code == 0,
                },
            },
        }, self.exit_code


class FakeAdb:
    def __init__(
        self,
        *,
        qemu: bool = False,
        window_polls: int = 3,
        window_forever: bool = False,
        fatal_logcat: bool = False,
        signing_identity: bool = True,
        state_error: bool = False,
    ):
        self.qemu = qemu
        self.window_polls = window_polls
        self.window_forever = window_forever
        self.fatal_logcat = fatal_logcat
        self.signing_identity = signing_identity
        self.state_error = state_error
        self.calls: list[str] = []

    def get_state(self) -> str:
        self.calls.append("get-state")
        if self.state_error:
            raise AdbError("device offline")
        return "device"

    def getprop(self, name: str) -> str:
        self.calls.append("getprop:" + name)
        if name == "ro.kernel.qemu":
            return "1" if self.qemu else ""
        if name == "ro.product.manufacturer":
            return "Huawei"
        if name == "ro.product.model":
            return "MGA-AL00"
        if name == "ro.build.version.release":
            return "12"
        return ""

    def install_apk(self, apk: str) -> None:
        self.calls.append("install:" + Path(apk).name)

    def dumpsys_package(self, package: str) -> str:
        self.calls.append("dumpsys-package")
        dump = (
            f"Package [{package}] ...\n"
            "versionCode=2 minSdk=26 targetSdk=36\n"
            "versionName=0.14.0\n"
        )
        if self.signing_identity:
            dump += (
                "signatures=PackageSignatures{"
                "version:3, signatures:[ABC123]}\n"
            )
        return dump

    def clear_logcat(self) -> None:
        self.calls.append("clear-logcat")

    def start_activity(self, package: str, activity: str) -> None:
        self.calls.append(f"start:{package}/{activity}")

    def process_id(self, package: str) -> str:
        self.calls.append("pidof")
        return "4242"

    def focused_window(self) -> str:
        self.calls.append("window")
        if self.window_forever or len(
            [c for c in self.calls if c == "window"]
        ) < self.window_polls:
            return "mCurrentFocus=Window{other}"
        return f"mCurrentFocus=Window{{123 u0 {PACKAGE}/.MainActivity}}"

    def dump_ui(self) -> bytes:
        self.calls.append("dump-ui")
        return b'<?xml version="1.0"?><hierarchy><node package="com.ailearningos.app"/></hierarchy>'

    def capture_screenshot(self) -> bytes:
        self.calls.append("screenshot")
        return b"\x89PNG\r\n\x1a\nfake"

    def dump_logcat(self) -> bytes:
        self.calls.append("logcat")
        if self.fatal_logcat:
            return b"--------- beginning of main\nFATAL EXCEPTION: main\n"
        return b"--------- beginning of main\n"


class FakeFiles:
    def __init__(self):
        self.files: dict[Path, bytes] = {}
        self.removed: list[Path] = []
        self.unlink_error: Path | None = None
        self.write_error: Path | None = None

    def mkdir(self, path: Path) -> None:
        self.files.setdefault(path, b"")

    def exists(self, path: Path) -> bool:
        return path in self.files

    def read_bytes(self, path: Path) -> bytes:
        return self.files[path]

    def write_bytes_atomic(self, path: Path, data: bytes) -> None:
        if self.write_error == path:
            raise OSError("write failed")
        self.files[path] = data

    def remove_verified(self, path: Path, expected_sha256: str) -> bool:
        if self.unlink_error == path:
            return False
        data = self.files.get(path)
        if data is None or digest(data) != expected_sha256:
            return False
        del self.files[path]
        self.removed.append(path)
        return True


class NoSleep:
    def __init__(self):
        self.seconds: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


def run_smoke(
    tmp_path: Path,
    *,
    http: FakeHttp | None = None,
    verifier: FakeVerifier | None = None,
    adb: FakeAdb | None = None,
    files: FakeFiles | None = None,
    apk_url: str | None = None,
    remove_apk: bool = False,
    output: str = ".verify/public-device-smoke",
):
    return smoke.run_smoke(
        serial=SERIAL,
        manifest_url=MANIFEST_URL,
        apk_url=apk_url,
        output=output,
        remove_apk=remove_apk,
        http=http or FakeHttp(
            {
                MANIFEST_URL: FakeResponse(manifest_bytes()),
                APK_URL: FakeResponse(
                    APK_BYTES, headers={"Content-Length": str(len(APK_BYTES))}
                ),
                API_BASE_URL + "/health": FakeResponse(
                    b'{"status":"ok","service":"ai-learning-os-api"}'
                ),
                API_BASE_URL + "/api/v1/auth/status": FakeResponse(
                    b'{"auth_enabled":false}'
                ),
            }
        ),
        verifier=verifier or FakeVerifier(),
        adb=adb or FakeAdb(),
        files=files or FakeFiles(),
        sleep=NoSleep(),
        poll_interval_seconds=0,
    )


def codes(result: dict) -> list[str]:
    return [failure["code"] for failure in result["failures"]]


def test_happy_path_orders_and_reports_all_evidence(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    files = FakeFiles()
    adb = FakeAdb()
    result, code = run_smoke(tmp_path, files=files, adb=adb, remove_apk=True)

    assert code == 0
    assert result["status"] == "passed"
    assert result["public_ready"] is False
    assert result["artifact"]["sha256"] == digest(APK_BYTES)
    assert result["artifact"]["verify_status"] == "verified"
    assert result["artifact"]["manifest_signed"] is True
    assert result["artifact"]["signed"] is True
    assert result["artifact"]["verify"]["apksigner"]["v2"] is True
    assert result["artifact"]["verify"]["apksigner"]["v3"] is True
    assert result["artifact"]["verify"]["aapt_badging"]["matches_manifest"] is True
    assert result["device"]["physical"] is True
    assert result["device"]["package_name"] == PACKAGE
    assert result["device"]["version_code"] == 2
    assert result["device"]["version_name"] == VERSION_NAME
    assert result["device"]["signing_identity_available"] is True
    assert result["launch"]["stable_process_window"] is True
    assert result["public_api"]["health"]["status_code"] == 200
    assert result["public_api"]["health"]["body_status"] == "ok"
    assert result["public_api"]["auth_status"]["status_code"] == 200
    assert result["public_api"]["auth_status"]["auth_enabled"] is False
    assert result["logcat"]["captured"] is True
    assert result["logcat"]["has_blocking_issue"] is False
    assert result["cleanup"]["apk_removed"] is True
    assert [item["name"] for item in result["evidence"]["files"]] == [
        "download-manifest.json",
        "adb-device.json",
        "package-dumpsys.txt",
        "process-window.json",
        "public-api.json",
        "window-layout.xml",
        "screenshot.png",
        "logcat.txt",
    ]
    assert "install:app-release-signed.apk" in adb.calls
    assert "dump-ui" in adb.calls
    assert "screenshot" in adb.calls
    assert "logcat" in adb.calls
    clear = adb.calls.index("clear-logcat")
    start = adb.calls.index(f"start:{PACKAGE}/.MainActivity")
    assert clear < start
    rendered = json.dumps(result, sort_keys=True)
    assert str(tmp_path).replace("\\", "/") not in rendered
    assert SECRET_MARKER not in rendered
    monkeypatch.undo()


def test_emulator_serial_is_rejected_before_any_network_or_device_call(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    http = FakeHttp()
    adb = FakeAdb()
    result, code = smoke.run_smoke(
        serial="emulator-5554",
        manifest_url=MANIFEST_URL,
        apk_url=None,
        output=".verify/smoke",
        remove_apk=False,
        http=http,
        verifier=FakeVerifier(),
        adb=adb,
        files=FakeFiles(),
        sleep=NoSleep(),
    )
    assert code == 2
    assert result["status"] == "blocked"
    assert "serial_emulator_rejected" in codes(result)
    assert http.urls == []
    assert adb.calls == []
    monkeypatch.undo()


def test_output_must_be_new_relative_path_under_verify(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    for bad in ("evidence", ".verify/../outside", str(tmp_path / ".verify" / "out")):
        result, code = smoke.run_smoke(
            serial=SERIAL,
            manifest_url=MANIFEST_URL,
            apk_url=None,
            output=bad,
            remove_apk=False,
            http=FakeHttp(),
            verifier=FakeVerifier(),
            adb=FakeAdb(),
            files=FakeFiles(),
            sleep=NoSleep(),
        )
        assert code == 2
        assert "output_invalid" in codes(result)
    monkeypatch.undo()


@pytest.mark.parametrize(
    "url",
    [
        "http://public.example/manifest.json",
        "https://127.0.0.1/manifest.json",
        "https://user:pass@public.example/manifest.json",
        "https://public.example/manifest.json?x=1",
        "https://public.example/manifest.json#fragment",
        "file:///tmp/manifest.json",
    ],
)
def test_manifest_url_contract_is_fail_closed(tmp_path, url):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    http = FakeHttp()
    result, code = smoke.run_smoke(
        serial=SERIAL,
        manifest_url=url,
        apk_url=None,
        output=".verify/smoke",
        remove_apk=False,
        http=http,
        verifier=FakeVerifier(),
        adb=FakeAdb(),
        files=FakeFiles(),
        sleep=NoSleep(),
    )
    assert code == 2
    assert result["status"] == "blocked"
    assert "manifest_url_invalid" in codes(result)
    assert http.urls == []
    monkeypatch.undo()


def test_single_bounded_download_and_no_retries_by_default(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    response = FakeResponse(manifest_bytes() + b"x" * smoke.MANIFEST_MAX_BYTES)
    http = FakeHttp({MANIFEST_URL: response})
    result, code = run_smoke(tmp_path, http=http)
    assert code == 1
    assert result["status"] == "failed"
    assert "manifest_download_size_exceeded" in codes(result)
    assert http.urls == [MANIFEST_URL]
    assert response.closed is True
    monkeypatch.undo()


def test_invalid_manifest_never_derives_or_downloads_apk(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    http = FakeHttp({MANIFEST_URL: FakeResponse(manifest_bytes(schema="other/1"))})
    result, code = run_smoke(tmp_path, http=http)
    assert code == 2
    assert result["status"] == "blocked"
    assert "manifest_contract_invalid" in codes(result)
    assert http.urls == [MANIFEST_URL]
    monkeypatch.undo()


def test_unsigned_manifest_entry_is_never_downloaded(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    http = FakeHttp({MANIFEST_URL: FakeResponse(manifest_bytes(signed=False))})
    result, code = run_smoke(tmp_path, http=http)
    assert code == 2
    assert result["status"] == "blocked"
    assert "manifest_contract_invalid" in codes(result)
    assert http.urls == [MANIFEST_URL]
    monkeypatch.undo()


def test_explicit_apk_url_must_match_public_manifest_origin_and_name(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    for url in (
        "https://other.example/android/app-release-signed.apk",
        "https://public.example/android/other.apk",
        "https://public.example/not-android/app-release-signed.apk",
    ):
        result, code = run_smoke(tmp_path, apk_url=url)
        assert code == 2
        assert result["status"] == "blocked"
        assert "apk_url_invalid" in codes(result)
    monkeypatch.undo()


def test_apk_size_and_hash_must_match_manifest(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    http = FakeHttp(
        {
            MANIFEST_URL: FakeResponse(manifest_bytes()),
            APK_URL: FakeResponse(
                APK_BYTES + b"tamper",
                headers={"Content-Length": str(len(APK_BYTES) + 6)},
            ),
        }
    )
    result, code = run_smoke(tmp_path, http=http)
    assert code == 1
    assert result["status"] == "failed"
    assert "apk_download_size_mismatch" in codes(result)
    assert "apk_unverified_not_installed" not in codes(result)
    monkeypatch.undo()


def test_verifier_failure_blocks_device_mutation(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    adb = FakeAdb()
    result, code = run_smoke(tmp_path, verifier=FakeVerifier(exit_code=1), adb=adb)
    assert code == 2
    assert result["status"] == "blocked"
    assert "artifact_verification_failed" in codes(result)
    assert result["artifact"]["verify_status"] == "failed"
    assert result["artifact"]["verify"]["failures"] == [{"code": "apksigner_failed"}]
    assert result["artifact"]["manifest_signed"] is True
    assert result["artifact"]["signed"] is False
    assert "install:app-release-signed.apk" not in adb.calls
    assert "dumpsys-package" not in adb.calls
    monkeypatch.undo()


def test_adb_reported_emulator_property_fails_closed_before_install(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    adb = FakeAdb(qemu=True)
    result, code = run_smoke(tmp_path, adb=adb)
    assert code == 2
    assert result["status"] == "blocked"
    assert "adb_device_emulator" in codes(result)
    assert "install:app-release-signed.apk" not in adb.calls
    monkeypatch.undo()


def test_offline_adb_device_is_blocked_not_internal(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    adb = FakeAdb(state_error=True)
    result, code = run_smoke(tmp_path, adb=adb)
    assert code == 2
    assert result["status"] == "blocked"
    assert "adb_command_failed" in codes(result)
    monkeypatch.undo()


def test_launch_requires_repeated_equal_pid_and_package_window(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    adb = FakeAdb(window_forever=True)
    sleeper = NoSleep()
    clock = {"now": 0.0}

    def controlled_monotonic() -> float:
        return clock["now"]

    def sleep_without_waiting(seconds: float) -> None:
        sleeper(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(smoke.time, "monotonic", controlled_monotonic)
    result, code = smoke.run_smoke(
        serial=SERIAL,
        manifest_url=MANIFEST_URL,
        apk_url=None,
        output=".verify/public-device-smoke",
        remove_apk=False,
        http=FakeHttp(
            {
                MANIFEST_URL: FakeResponse(manifest_bytes()),
                APK_URL: FakeResponse(APK_BYTES),
                API_BASE_URL + "/health": FakeResponse(b'{"status":"ok"}'),
                API_BASE_URL + "/api/v1/auth/status": FakeResponse(
                    b'{"auth_enabled":false}'
                ),
            }
        ),
        verifier=FakeVerifier(),
        adb=adb,
        files=FakeFiles(),
        sleep=sleep_without_waiting,
        stable_wait_seconds=1,
        poll_interval_seconds=0.25,
    )
    assert code == 1
    assert result["status"] == "failed"
    assert "stable_process_window_timeout" in codes(result)
    assert len([call for call in adb.calls if call == "window"]) >= 3
    monkeypatch.undo()


def test_public_api_requires_both_health_and_auth_status(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    http = FakeHttp(
        {
            MANIFEST_URL: FakeResponse(manifest_bytes()),
            APK_URL: FakeResponse(APK_BYTES),
            API_BASE_URL + "/health": FakeResponse(b'{"status":"ok"}'),
            API_BASE_URL + "/api/v1/auth/status": FakeResponse(
                b'{"auth_enabled":false}', status=503
            ),
        }
    )
    result, code = run_smoke(tmp_path, http=http)
    assert code == 1
    assert result["status"] == "failed"
    assert "auth_status_http_error" in codes(result)
    monkeypatch.undo()


def test_missing_adb_signing_identity_fails_closed(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    adb = FakeAdb(signing_identity=False)
    result, code = run_smoke(tmp_path, adb=adb)
    assert code == 1
    assert result["status"] == "failed"
    assert "package_identity_unavailable" in codes(result)
    monkeypatch.undo()


def test_explicit_api_base_must_be_public_https(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    for url in ("http://public.example", "https://127.0.0.1", "https://localhost"):
        result, code = smoke.run_smoke(
            serial=SERIAL,
            manifest_url=MANIFEST_URL,
            apk_url=None,
            output=".verify/smoke",
            remove_apk=False,
            http=FakeHttp(),
            verifier=FakeVerifier(),
            adb=FakeAdb(),
            files=FakeFiles(),
            sleep=NoSleep(),
            api_base_url=url,
        )
        assert code == 2
        assert result["status"] == "blocked"
        assert "api_base_url_invalid" in codes(result)
    monkeypatch.undo()


def test_nonpositive_network_timeout_is_rejected_before_work(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    http = FakeHttp()
    result, code = smoke.run_smoke(
        serial=SERIAL,
        manifest_url=MANIFEST_URL,
        apk_url=None,
        output=".verify/smoke",
        remove_apk=False,
        http=http,
        verifier=FakeVerifier(),
        adb=FakeAdb(),
        files=FakeFiles(),
        sleep=NoSleep(),
        download_timeout_seconds=0,
    )
    assert code == 2
    assert result["status"] == "blocked"
    assert "timeout_invalid" in codes(result)
    assert http.urls == []
    monkeypatch.undo()


def test_blocking_logcat_issue_fails_while_preserving_evidence(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    files = FakeFiles()
    adb = FakeAdb(fatal_logcat=True)
    result, code = run_smoke(tmp_path, files=files, adb=adb)
    assert code == 1
    assert result["status"] == "failed"
    assert "logcat_blocking_issue" in codes(result)
    assert result["logcat"]["fatal_exception_count"] == 1
    evidence = {item["name"]: item for item in result["evidence"]["files"]}
    assert evidence["logcat.txt"]["sha256"] == digest(
        b"--------- beginning of main\nFATAL EXCEPTION: main\n"
    )
    monkeypatch.undo()


def test_failed_report_write_returns_failed_exit(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    files = FakeFiles()
    files.write_error = Path(".verify/public-device-smoke/report.md")
    result, code = run_smoke(tmp_path, files=files)
    assert code == 1
    assert result["status"] == "failed"
    assert "report_write_failed" in codes(result)
    monkeypatch.undo()


def test_real_file_store_leaves_no_temporary_files(tmp_path):
    path = tmp_path / "nested" / "artifact.txt"
    smoke.RealFileStore().write_bytes_atomic(path, b"atomic")
    assert path.read_bytes() == b"atomic"
    assert [item.name for item in path.parent.iterdir()] == ["artifact.txt"]


def test_default_cleanup_keeps_apk_and_failed_cleanup_is_reported(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    files = FakeFiles()
    result, code = run_smoke(tmp_path, files=files)
    assert code == 0
    assert result["cleanup"] == {
        "remove_apk_requested": False,
        "apk_removed": False,
        "status": "not_requested",
    }
    assert files.files[Path(".verify/public-device-smoke") / APK_NAME] == APK_BYTES

    files = FakeFiles()
    output_apk = Path(".verify/public-device-smoke") / APK_NAME
    files.unlink_error = output_apk
    result, code = run_smoke(tmp_path, files=files, remove_apk=True)
    assert code == 1
    assert result["status"] == "failed"
    assert "apk_cleanup_failed" in codes(result)
    monkeypatch.undo()


def test_network_failure_is_honest_blocked_without_retry(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    http = FakeHttp({MANIFEST_URL: OSError("network unavailable")})
    result, code = run_smoke(tmp_path, http=http)
    assert code == 2
    assert result["status"] == "blocked"
    assert "manifest_network_unavailable" in codes(result)
    assert http.urls == [MANIFEST_URL]
    monkeypatch.undo()


def test_markdown_and_json_are_atomic_value_free_and_deterministic(tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.chdir(tmp_path)
    files = FakeFiles()
    result, _ = run_smoke(tmp_path, files=files)
    output = Path(".verify/public-device-smoke")
    json_bytes = files.files[output / "report.json"]
    md_bytes = files.files[output / "report.md"]
    assert json.loads(json_bytes) == result
    assert b"# Android public physical-device smoke" in md_bytes
    assert b"`public_ready=false`" in md_bytes
    assert b"- Logcat: captured=True" in md_bytes
    assert str(tmp_path).encode() not in json_bytes
    assert str(tmp_path).encode() not in md_bytes
    assert SECRET_MARKER.encode() not in json_bytes + md_bytes
    monkeypatch.undo()
