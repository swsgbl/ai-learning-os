"""Fail-closed public Android physical-device smoke chain.

The tool downloads one explicitly supplied public HTTPS download manifest and
the Android APK exposed by that manifest, reuses the full
``verify_artifact`` gate, installs the exact verified APK on one explicit
physical device, starts the app, captures launch/runtime evidence, and probes
two unauthenticated public API endpoints. It performs no destructive device
operation: no factory reset, no uninstall, no settings change, and no package
clear. The downloaded local APK is removed only with an explicit opt-in.

Every boundary is fail-closed. A successful run is evidence for one artifact on
one device at one point in time and never asserts general production
readiness; ``public_ready`` is therefore always false.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
import re
import ssl
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

if __package__ in (None, ""):  # direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.android_release.verify_artifact import (
    ACCEPTED_SIGNATURE_SCHEME,
    parse_manifest_contract,
    run_verify,
)
from tools.android_smoke.adb import AdbClient, AdbError
from tools.android_smoke.logcat_analysis import analyze_logcat

SCHEMA_VERSION = 1
TOOL_NAME = "android_release_public_device_smoke"
MANIFEST_BASENAME = "download-manifest.json"
REPORT_JSON = "report.json"
REPORT_MARKDOWN = "report.md"
OUTPUT_PARENT = ".verify"

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"
EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_BLOCKED = 2

MANIFEST_MAX_BYTES = 1024 * 1024
API_MAX_BYTES = 64 * 1024
DOWNLOAD_CHUNK_BYTES = 64 * 1024
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 90
DEFAULT_API_TIMEOUT_SECONDS = 20
DEFAULT_STABLE_WAIT_SECONDS = 45
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
STABLE_SAMPLE_COUNT = 3

ACTIVITY_NAME = ".MainActivity"
SERIAL_RE = re.compile(r"^[A-Za-z0-9._-]+$")
VERSION_CODE_RE = re.compile(r"(?:^|\s)versionCode=(\d+)(?:\s|$)")
VERSION_NAME_RE = re.compile(r"(?:^|\s)versionName=([^\s]+)(?:\s|$)")
SIGNATURE_RE = re.compile(r"(?:^|\s)signatures=PackageSignatures\{")
# Focus lines may carry leading whitespace (EMUI 10 full window dump prints
# mCurrentFocus indented two spaces, mFocusedWindow four), so both regexes
# tolerate indentation. The ownership filter still requires a Window{...}
# payload, so an indented ``mCurrentFocus=null`` never counts as owned.
FOCUSED_WINDOW_RE = re.compile(
    r"^\s*m(?:CurrentFocus|FocusedWindow)=.*[{\s]", re.MULTILINE
)
# Presence-only check for the focus lines. EMUI 10 omits every
# mCurrentFocus/mFocusedWindow line from ``dumpsys window windows`` while the
# full ``dumpsys window`` still reports them; a present (foreign or null)
# focus line must keep the probe on the windows dump alone.
FOCUS_LINE_RE = re.compile(r"^\s*m(?:CurrentFocus|FocusedWindow)=", re.MULTILINE)


class SmokeFailure(Exception):
    """A fail-closed outcome with a stable stage/category pair."""

    def __init__(self, stage: str, code: str, blocked: bool = False):
        super().__init__(f"{stage}:{code}")
        self.stage = stage
        self.code = code
        self.blocked = blocked


class HttpResponse(Protocol):
    status: int
    headers: Any
    url: str

    def read(self, size: int = -1) -> bytes: ...

    def close(self) -> None: ...


class HttpTransport(Protocol):
    def get(self, url: str, timeout_seconds: int) -> HttpResponse: ...


class UrllibHttpTransport:
    """Default transport: one HTTPS request, no retry loop."""

    USER_AGENT = "ai-learning-os-public-device-smoke/1"

    def get(self, url: str, timeout_seconds: int) -> HttpResponse:
        request = Request(
            url,
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept": "application/json, application/octet-stream",
            },
            method="GET",
        )
        return urlopen(
            request,
            timeout=timeout_seconds,
            context=ssl.create_default_context(),
        )


class Verifier(Protocol):
    def __call__(self, **kwargs: Any) -> tuple[dict, int]: ...


class AdbSmokeDevice(Protocol):
    def get_state(self) -> str: ...

    def getprop(self, name: str) -> str: ...

    def install_apk(self, apk: str) -> None: ...

    def dumpsys_package(self, package: str) -> str: ...

    def clear_logcat(self) -> None: ...

    def start_activity(self, package: str, activity: str) -> None: ...

    def process_id(self, package: str) -> str: ...

    def focused_window(self) -> str: ...

    def dump_ui(self) -> bytes: ...

    def capture_screenshot(self) -> bytes: ...

    def dump_logcat(self) -> bytes: ...


class RealAdbSmokeDevice:
    """Argument-list-only ADB adapter for the narrow smoke surface."""

    def __init__(self, serial: str, adb_path: str = "adb", timeout_seconds: int = 60):
        self.client = AdbClient(
            adb_path=adb_path,
            serial=serial,
            timeout_seconds=timeout_seconds,
        )

    def get_state(self) -> str:
        return self.client.run(["get-state"]).stdout.decode(
            "utf-8", errors="replace"
        ).strip()

    def getprop(self, name: str) -> str:
        return self.client.run_shell(["getprop", name]).stdout.decode(
            "utf-8", errors="replace"
        ).strip()

    def install_apk(self, apk: str) -> None:
        self.client.run(["install", "-r", apk])

    def dumpsys_package(self, package: str) -> str:
        return self.client.run_shell(["dumpsys", "package", package]).stdout.decode(
            "utf-8", errors="replace"
        )

    def clear_logcat(self) -> None:
        self.client.clear_logcat()

    def start_activity(self, package: str, activity: str) -> None:
        self.client.run(
            ["shell", "am", "start", "-W", "-n", f"{package}/{activity}"]
        )

    def process_id(self, package: str) -> str:
        return self.client.run_shell(["pidof", package]).stdout.decode(
            "utf-8", errors="replace"
        ).strip()

    def focused_window(self) -> str:
        windows = self.client.run_shell(
            ["dumpsys", "window", "windows"]
        ).stdout.decode("utf-8", errors="replace")
        if FOCUS_LINE_RE.search(windows) is not None:
            return windows
        # EMUI 10 drops the focus lines from the windows-only dump; fall back
        # to the full window dump only when every focus line is absent so a
        # present (foreign or null) focus line stays fail-closed.
        return self.client.run_shell(["dumpsys", "window"]).stdout.decode(
            "utf-8", errors="replace"
        )

    def dump_ui(self) -> bytes:
        return self.client.dump_ui()

    def capture_screenshot(self) -> bytes:
        return self.client.capture_screenshot()

    def dump_logcat(self) -> bytes:
        return self.client.dump_logcat()


class FileStore(Protocol):
    def mkdir(self, path: Path) -> None: ...

    def exists(self, path: Path) -> bool: ...

    def read_bytes(self, path: Path) -> bytes: ...

    def write_bytes_atomic(self, path: Path, data: bytes) -> None: ...

    def remove_verified(self, path: Path, expected_sha256: str) -> bool: ...


class RealFileStore:
    """Atomic file writes and exact-file cleanup for one evidence directory."""

    def mkdir(self, path: Path) -> None:
        path.mkdir(parents=True)

    def exists(self, path: Path) -> bool:
        return path.exists() or path.is_symlink()

    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()

    def write_bytes_atomic(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
        )
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        except BaseException:
            try:
                temp.unlink()
            except OSError:
                pass
            raise

    def remove_verified(self, path: Path, expected_sha256: str) -> bool:
        try:
            if not path.is_file() or path.is_symlink():
                return False
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
                return False
            path.unlink()
            return True
        except OSError:
            return False


@dataclass
class DownloadedArtifact:
    url: str
    filename: str
    path: Path
    size_bytes: int
    sha256: str
    manifest_path: Path
    manifest_sha256: str
    manifest_size_bytes: int
    package_name: str
    version_code: int
    version_name: str
    signature_scheme: str
    verify_result: dict
    verify_exit: int


def _failure(stage: str, code: str, blocked: bool = False) -> dict:
    item = {"stage": stage, "code": code}
    if blocked:
        item["blocked"] = True
    return item


def _public_host(hostname: str | None, error_code: str) -> str:
    if not hostname:
        raise SmokeFailure("input", error_code, blocked=True)
    host = hostname.rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if (
            not host
            or host == "localhost"
            or host.endswith((".localhost", ".internal", ".local"))
        ):
            raise SmokeFailure("input", error_code, blocked=True) from None
        return host
    if not address.is_global:
        raise SmokeFailure("input", error_code, blocked=True)
    return host


def _public_origin(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise SmokeFailure("input", "manifest_url_invalid", blocked=True)
    if parsed.username is not None or parsed.password is not None:
        raise SmokeFailure("input", "manifest_url_invalid", blocked=True)
    if parsed.query or parsed.fragment or not parsed.hostname:
        raise SmokeFailure("input", "manifest_url_invalid", blocked=True)
    host = _public_host(parsed.hostname, "manifest_url_invalid")
    if parsed.port is not None and parsed.port != 443:
        raise SmokeFailure("input", "manifest_url_invalid", blocked=True)
    path = parsed.path or "/"
    if (
        not path.startswith("/")
        or "//" in path
        or "\\" in path
        or any(part in (".", "..") for part in path.split("/"))
        or "%" in path
    ):
        raise SmokeFailure("input", "manifest_url_invalid", blocked=True)
    display_host = f"[{host}]" if ":" in host else host
    return (f"https://{display_host}", path)


def validate_api_base_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise SmokeFailure("input", "api_base_url_invalid", blocked=True)
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise SmokeFailure("input", "api_base_url_invalid", blocked=True)
    if parsed.port is not None and parsed.port != 443:
        raise SmokeFailure("input", "api_base_url_invalid", blocked=True)
    host = _public_host(parsed.hostname, "api_base_url_invalid")
    path = parsed.path.rstrip("/")
    if not path.startswith("/") or "\\" in path or "%" in path:
        raise SmokeFailure("input", "api_base_url_invalid", blocked=True)
    if any(part in (".", "..") for part in path.split("/")):
        raise SmokeFailure("input", "api_base_url_invalid", blocked=True)
    display_host = f"[{host}]" if ":" in host else host
    return f"https://{display_host}{path}"


def derive_api_base_url(manifest_url: str) -> str:
    origin, path = _public_origin(manifest_url)
    parent = str(PurePosixPath(path).parent)
    if parent == "/":
        return origin
    return origin + parent


def validate_output_path(output: str) -> Path:
    if not isinstance(output, str) or not output.strip():
        raise SmokeFailure("input", "output_invalid", blocked=True)
    candidate = Path(output)
    if candidate.is_absolute() or not candidate.parts:
        raise SmokeFailure("input", "output_invalid", blocked=True)
    parts = candidate.parts
    if (
        parts[0] != OUTPUT_PARENT
        or len(parts) < 2
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise SmokeFailure("input", "output_invalid", blocked=True)
    normalized = Path(os.path.normpath(str(Path.cwd() / candidate)))
    verify_root = Path(os.path.normpath(str(Path.cwd() / OUTPUT_PARENT)))
    try:
        normalized.relative_to(verify_root)
    except ValueError:
        raise SmokeFailure("input", "output_invalid", blocked=True) from None
    for parent in (verify_root, *list(candidate.parents)[: len(parts) - 1]):
        if parent.exists() and parent.is_symlink():
            raise SmokeFailure("input", "output_invalid", blocked=True)
    return candidate


def _read_bounded(
    transport: HttpTransport,
    url: str,
    timeout_seconds: int,
    max_bytes: int,
    expected_size: int | None,
    stage: str,
) -> bytes:
    try:
        response = transport.get(url, timeout_seconds)
    except (OSError, ValueError):
        raise SmokeFailure(stage, f"{stage}_network_unavailable", blocked=True) from None
    try:
        final_url = str(getattr(response, "url", url))
        if final_url != url:
            raise SmokeFailure(stage, f"{stage}_redirect_rejected", blocked=True)
        status = int(getattr(response, "status", 0) or 0)
        if status != 200:
            raise SmokeFailure(stage, f"{stage}_http_error")
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                raise SmokeFailure(stage, f"{stage}_content_length_invalid") from None
            if declared > max_bytes or (
                expected_size is not None and declared != expected_size
            ):
                raise SmokeFailure(stage, f"{stage}_download_size_mismatch")
        chunks: list[bytes] = []
        total = 0
        deadline = time.monotonic() + timeout_seconds
        while True:
            if time.monotonic() >= deadline:
                raise SmokeFailure(
                    stage, f"{stage}_timeout", blocked=True
                )
            chunk = response.read(DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise SmokeFailure(stage, f"{stage}_download_size_exceeded")
            chunks.append(chunk)
        if expected_size is not None and total != expected_size:
            raise SmokeFailure(stage, f"{stage}_download_size_mismatch")
        return b"".join(chunks)
    except SmokeFailure:
        raise
    except (OSError, ValueError):
        raise SmokeFailure(stage, f"{stage}_network_unavailable", blocked=True) from None
    finally:
        try:
            response.close()
        except OSError:
            pass


def _parse_manifest(
    payload: bytes,
    manifest_url: str,
) -> tuple[dict, str, str, dict, dict]:
    try:
        model = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise SmokeFailure("manifest", "manifest_invalid_json", blocked=True) from None
    if not isinstance(model, dict):
        raise SmokeFailure("manifest", "manifest_invalid_json", blocked=True)
    channels = model.get("channels")
    channel = channels.get("android") if isinstance(channels, dict) else None
    entries = channel.get("files") if isinstance(channel, dict) else None
    candidates = [
        item for item in (entries if isinstance(entries, list) else [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]
    if len(candidates) != 1:
        raise SmokeFailure("manifest", "manifest_android_entry_count_invalid", blocked=True)
    contract, contract_failures, entry, identity = parse_manifest_contract(
        model, candidates[0]["name"]
    )
    if contract_failures or entry is None or identity is None:
        raise SmokeFailure("manifest", "manifest_contract_invalid", blocked=True)
    derived_url = urljoin(manifest_url, str(candidates[0].get("url")))
    return (
        contract,
        derived_url,
        str(candidates[0]["name"]),
        identity,
        entry if entry is not None else {},
    )


def validate_apk_url(
    apk_url: str, manifest_url: str, filename: str
) -> str:
    try:
        origin, path = _public_origin(apk_url)
    except SmokeFailure:
        raise SmokeFailure("manifest", "apk_url_invalid", blocked=True) from None
    manifest_origin, _ = _public_origin(manifest_url)
    expected_path = f"/android/{filename}"
    if origin != manifest_origin or path != expected_path:
        raise SmokeFailure("manifest", "apk_url_invalid", blocked=True)
    return apk_url


def _verify_summary(result: dict) -> dict:
    checks = result.get("checks", {})
    apksigner = checks.get("apksigner", {})
    badging = checks.get("aapt_badging", {})
    return {
        "status": result.get("status"),
        "exit_code": result.get("exit_code"),
        "failures": result.get("failures", []),
        "apksigner": {
            key: value
            for key, value in apksigner.items()
            if key in ("status", "v2", "v3", "debug_certificate")
        },
        "aapt_badging": {
            key: badging.get(key)
            for key in ("package_name", "version_code", "version_name", "matches_manifest")
        },
    }


def download_and_verify(
    transport: HttpTransport,
    verifier: Verifier,
    files: FileStore,
    output_dir: Path,
    manifest_url: str,
    apk_url: str | None,
    timeout_seconds: int,
    apksigner: str | None,
    aapt: str | None,
) -> DownloadedArtifact:
    manifest_bytes_ = _read_bounded(
        transport,
        manifest_url,
        timeout_seconds,
        MANIFEST_MAX_BYTES,
        None,
        "manifest",
    )
    manifest_path = output_dir / MANIFEST_BASENAME
    files.write_bytes_atomic(manifest_path, manifest_bytes_)

    _contract, derived_url, filename, identity, entry = _parse_manifest(
        manifest_bytes_, manifest_url
    )
    selected_url = (
        validate_apk_url(apk_url, manifest_url, filename)
        if apk_url is not None
        else derived_url
    )
    entry_size = int(entry["size_bytes"])
    expected_sha256 = str(entry["sha256"]).lower()

    apk_payload = _read_bounded(
        transport,
        selected_url,
        timeout_seconds,
        max(1024 * 1024, entry_size),
        entry_size,
        "apk",
    )
    actual_sha256 = hashlib.sha256(apk_payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise SmokeFailure("artifact", "apk_sha256_mismatch", blocked=True)
    apk_path = output_dir / filename
    files.write_bytes_atomic(apk_path, apk_payload)

    verify_result, verify_exit = verifier(
        apk=str(apk_path),
        manifest=str(manifest_path),
        previous_manifest=None,
        previous_version_code=None,
        apksigner=apksigner,
        aapt=aapt,
    )
    return DownloadedArtifact(
        url=selected_url,
        filename=filename,
        path=apk_path,
        size_bytes=len(apk_payload),
        sha256=actual_sha256,
        manifest_path=manifest_path,
        manifest_sha256=hashlib.sha256(manifest_bytes_).hexdigest(),
        manifest_size_bytes=len(manifest_bytes_),
        package_name=str(identity["package_name"]),
        version_code=int(identity["version_code"]),
        version_name=str(identity["version_name"]),
        signature_scheme=ACCEPTED_SIGNATURE_SCHEME,
        verify_result=_verify_summary(verify_result),
        verify_exit=verify_exit,
    )


def check_physical_device(adb: AdbSmokeDevice, serial: str) -> dict:
    if SERIAL_RE.match(serial) is None or serial.lower().startswith("emulator-"):
        raise SmokeFailure("device", "serial_emulator_rejected", blocked=True)
    state = adb.get_state()
    if state != "device":
        raise SmokeFailure("device", "adb_device_unavailable", blocked=True)
    for prop in ("ro.kernel.qemu", "ro.boot.qemu"):
        if adb.getprop(prop).strip().lower() in ("1", "true", "yes"):
            raise SmokeFailure("device", "adb_device_emulator", blocked=True)
    facts = {
        "state": state,
        "physical": True,
        "manufacturer": adb.getprop("ro.product.manufacturer"),
        "model": adb.getprop("ro.product.model"),
        "android_release": adb.getprop("ro.build.version.release"),
    }
    return facts


def parse_package_facts(package: str, dumpsys: str) -> dict:
    version_code_match = VERSION_CODE_RE.search(dumpsys)
    version_name_match = VERSION_NAME_RE.search(dumpsys)
    if version_code_match is None or version_name_match is None:
        raise SmokeFailure("device", "package_identity_unavailable")
    facts = {
        "package_name": package,
        "version_code": int(version_code_match.group(1)),
        "version_name": version_name_match.group(1),
        "signing_identity_available": SIGNATURE_RE.search(dumpsys) is not None,
    }
    if not facts["signing_identity_available"]:
        raise SmokeFailure("device", "package_identity_unavailable")
    return facts


def wait_for_stable_process_window(
    adb: AdbSmokeDevice,
    package: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    sleep: Callable[[float], None],
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    samples: list[dict] = []
    stable_count = 0
    last_pid = ""
    while time.monotonic() <= deadline:
        pid = adb.process_id(package)
        window = adb.focused_window()
        package_window = any(
            package in line
            for line in window.splitlines()
            if FOCUSED_WINDOW_RE.match(line)
        )
        samples.append(
            {"pid_present": bool(pid), "pid": pid or None, "package_window": package_window}
        )
        if pid and package_window and pid == last_pid:
            stable_count += 1
        else:
            stable_count = 1 if pid and package_window else 0
            last_pid = pid
        if stable_count >= STABLE_SAMPLE_COUNT:
            return {
                "stable_process_window": True,
                "stable_sample_count": STABLE_SAMPLE_COUNT,
                "samples": samples,
            }
        if time.monotonic() + poll_interval_seconds > deadline:
            break
        sleep(poll_interval_seconds)
    raise SmokeFailure("launch", "stable_process_window_timeout")


def probe_public_api(
    transport: HttpTransport,
    api_base_url: str,
    timeout_seconds: int,
) -> dict:
    result: dict[str, dict] = {}
    for name, path, kind in (
        ("health", "/health", "health"),
        ("auth_status", "/api/v1/auth/status", "auth"),
    ):
        url = api_base_url + path
        body = _read_bounded(
            transport, url, timeout_seconds, API_MAX_BYTES, None, name
        )
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise SmokeFailure("public_api", f"{name}_invalid_json") from None
        if not isinstance(payload, dict):
            raise SmokeFailure("public_api", f"{name}_invalid_json")
        record = {
            "status_code": 200,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "body_bytes": len(body),
        }
        if kind == "health":
            status = payload.get("status")
            if status != "ok":
                raise SmokeFailure("public_api", "health_body_not_ok")
            record["body_status"] = status
        else:
            auth_enabled = payload.get("auth_enabled")
            if not isinstance(auth_enabled, bool):
                raise SmokeFailure("public_api", "auth_status_shape_invalid")
            record["auth_enabled"] = auth_enabled
        result[name] = record
    return result


def _artifact_record(artifact: DownloadedArtifact, verify: dict | None) -> dict:
    return {
        "manifest_url": None,
        "manifest_sha256": artifact.manifest_sha256,
        "manifest_size_bytes": artifact.manifest_size_bytes,
        "apk_url": None,
        "apk_filename": artifact.filename,
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
        "manifest_signed": True,
        "signed": artifact.verify_exit == 0,
        "signature_scheme": artifact.signature_scheme,
        "package_name": artifact.package_name,
        "version_code": artifact.version_code,
        "version_name": artifact.version_name,
        "verify_status": verify["status"] if verify else "not_run",
        "verify": verify,
    }


def _evidence_hashes(files: FileStore, output_dir: Path, names: Sequence[str]) -> list[dict]:
    records: list[dict] = []
    for name in names:
        path = output_dir / name
        try:
            payload = files.read_bytes(path)
        except (KeyError, OSError):
            continue
        records.append(
            {
                "name": name,
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return records


def render_json(result: dict) -> str:
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True)


def render_markdown(result: dict) -> str:
    artifact = result.get("artifact", {})
    device = result.get("device", {})
    launch = result.get("launch", {})
    api = result.get("public_api", {})
    logcat = result.get("logcat", {})
    failures = result.get("failures", [])
    lines = [
        "# Android public physical-device smoke",
        "",
        f"- Result: `{result['status']}` (exit `{result['exit_code']}`)",
        "- Boundary: `public_ready=false`; this single smoke does not establish production readiness.",
        f"- Artifact: `{artifact.get('apk_filename')}`, `{artifact.get('size_bytes')}` bytes, SHA-256 `{artifact.get('sha256')}`.",
        f"- Verification: signed={artifact.get('signed')}, scheme `{artifact.get('signature_scheme')}`, verifier `{artifact.get('verify_status')}`.",
        f"- Device: physical={device.get('physical')}, model `{device.get('model')}`, package `{device.get('package_name')}`.",
        f"- Launch: stable_process_window={launch.get('stable_process_window')}.",
        f"- Public API: health={api.get('health', {}).get('body_status')}, auth_status={api.get('auth_status', {}).get('status_code')}.",
        f"- Logcat: captured={logcat.get('captured')}, blocking_issue={logcat.get('has_blocking_issue')}.",
        f"- Downloaded APK cleanup: {result.get('cleanup', {}).get('status')}.",
        f"- Failures: {len(failures)}",
    ]
    for failure in failures:
        lines.append(f"  - `{failure['stage']}` / `{failure['code']}`")
    lines.extend(["", "## Evidence", ""])
    for item in result.get("evidence", {}).get("files", []):
        lines.append(
            f"- `{item['name']}`: {item['size_bytes']} bytes, SHA-256 `{item['sha256']}`"
        )
    return "\n".join(lines) + "\n"


def run_smoke(
    *,
    serial: str,
    manifest_url: str,
    apk_url: str | None,
    output: str,
    remove_apk: bool,
    http: HttpTransport | None = None,
    verifier: Verifier | None = None,
    adb: AdbSmokeDevice | None = None,
    files: FileStore | None = None,
    sleep: Callable[[float], None] = time.sleep,
    api_base_url: str | None = None,
    download_timeout_seconds: int = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    api_timeout_seconds: int = DEFAULT_API_TIMEOUT_SECONDS,
    stable_wait_seconds: float = DEFAULT_STABLE_WAIT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    apksigner: str | None = None,
    aapt: str | None = None,
) -> tuple[dict, int]:
    transport = http or UrllibHttpTransport()
    verify_runner = verifier or run_verify
    file_store = files or RealFileStore()
    failures: list[dict] = []
    artifact: DownloadedArtifact | None = None
    verify_result: dict | None = None
    device_record: dict = {}
    launch_record: dict = {"stable_process_window": False}
    api_record: dict[str, dict] = {}
    logcat_record: dict = {"captured": False}
    cleanup_record: dict[str, Any] = {
        "remove_apk_requested": remove_apk,
        "apk_removed": False,
        "status": "not_downloaded",
    }
    output_dir: Path | None = None
    evidence_names = [
        MANIFEST_BASENAME,
        "adb-device.json",
        "package-dumpsys.txt",
        "process-window.json",
        "public-api.json",
        "window-layout.xml",
        "screenshot.png",
        "logcat.txt",
    ]
    device_started = False

    try:
        numeric_inputs = (
            ("download_timeout_seconds", download_timeout_seconds),
            ("api_timeout_seconds", api_timeout_seconds),
            ("stable_wait_seconds", stable_wait_seconds),
            ("poll_interval_seconds", poll_interval_seconds),
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
            for _, value in numeric_inputs
        ):
            raise SmokeFailure("input", "timeout_invalid", blocked=True)
        if download_timeout_seconds <= 0 or api_timeout_seconds <= 0:
            raise SmokeFailure("input", "timeout_invalid", blocked=True)
        output_dir = validate_output_path(output)
        if file_store.exists(output_dir):
            raise SmokeFailure("input", "output_invalid", blocked=True)
        file_store.mkdir(output_dir)
        _public_origin(manifest_url)
        resolved_api_base = (
            validate_api_base_url(api_base_url)
            if api_base_url is not None
            else derive_api_base_url(manifest_url)
        )
        validate_api_base_url(resolved_api_base)

        device_adapter = adb or RealAdbSmokeDevice(serial)
        # Validate device identity before downloading so a bad target cannot be
        # replaced by an emulator later in the chain.
        precheck = check_physical_device(device_adapter, serial)
        device_record.update(precheck)

        artifact = download_and_verify(
            transport,
            verify_runner,
            file_store,
            output_dir,
            manifest_url,
            apk_url,
            download_timeout_seconds,
            apksigner,
            aapt,
        )
        verify_result = artifact.verify_result
        if artifact.verify_exit != 0:
            raise SmokeFailure("artifact", "artifact_verification_failed", blocked=True)
        device_record.update(
            {
                "serial": serial,
                "package_name": artifact.package_name,
                "expected_version_code": artifact.version_code,
                "expected_version_name": artifact.version_name,
            }
        )

        device_adapter.install_apk(str(artifact.path))
        device_started = True
        dumpsys = device_adapter.dumpsys_package(artifact.package_name)
        file_store.write_bytes_atomic(
            output_dir / "package-dumpsys.txt", dumpsys.encode("utf-8")
        )
        package_record = parse_package_facts(artifact.package_name, dumpsys)
        if (
            package_record["version_code"] != artifact.version_code
            or package_record["version_name"] != artifact.version_name
        ):
            raise SmokeFailure("device", "installed_package_identity_mismatch")
        device_record.update(package_record)

        device_adapter.clear_logcat()
        device_adapter.start_activity(artifact.package_name, ACTIVITY_NAME)
        launch_record = wait_for_stable_process_window(
            device_adapter,
            artifact.package_name,
            stable_wait_seconds,
            poll_interval_seconds,
            sleep,
        )

        api_record = probe_public_api(
            transport, resolved_api_base, api_timeout_seconds
        )
        file_store.write_bytes_atomic(
            output_dir / "public-api.json",
            json.dumps(api_record, indent=2, sort_keys=True).encode("utf-8"),
        )
    except SmokeFailure as exc:
        failures.append(_failure(exc.stage, exc.code, exc.blocked))
    except AdbError:
        failures.append(_failure("device", "adb_command_failed", blocked=True))
    except Exception:  # noqa: BLE001 - raw exceptions can carry paths/secrets
        failures.append(_failure("internal", "internal_error"))

    # Runtime evidence is mandatory on the success path and best-effort after a
    # post-install failure; missing evidence can never turn into a pass.
    if output_dir is not None and artifact is not None and device_started:
        try:
            device_adapter = adb or RealAdbSmokeDevice(serial)
            process = launch_record
            if not process.get("stable_process_window"):
                try:
                    process = wait_for_stable_process_window(
                        device_adapter,
                        artifact.package_name,
                        min(stable_wait_seconds, 5.0),
                        min(poll_interval_seconds, 1.0),
                        sleep,
                    )
                except SmokeFailure:
                    process = {"stable_process_window": False, "samples": []}
            file_store.write_bytes_atomic(
                output_dir / "process-window.json",
                json.dumps(process, indent=2, sort_keys=True).encode("utf-8"),
            )
            file_store.write_bytes_atomic(
                output_dir / "adb-device.json",
                json.dumps(device_record, indent=2, sort_keys=True).encode("utf-8"),
            )
            file_store.write_bytes_atomic(
                output_dir / "window-layout.xml", device_adapter.dump_ui()
            )
            file_store.write_bytes_atomic(
                output_dir / "screenshot.png", device_adapter.capture_screenshot()
            )
            logcat_bytes = device_adapter.dump_logcat()
            logcat_stats = analyze_logcat(
                logcat_bytes.decode("utf-8", errors="replace").splitlines(),
                package_name=artifact.package_name,
            )
            logcat_record = {"captured": True, **logcat_stats}
            file_store.write_bytes_atomic(output_dir / "logcat.txt", logcat_bytes)
            if logcat_stats["has_blocking_issue"]:
                failures.append(_failure("logcat", "logcat_blocking_issue"))
        except Exception:  # noqa: BLE001
            failures.append(_failure("evidence", "runtime_evidence_unavailable"))
    elif output_dir is not None and artifact is not None:
        try:
            file_store.write_bytes_atomic(
                output_dir / "adb-device.json",
                json.dumps(device_record, indent=2, sort_keys=True).encode("utf-8"),
            )
        except Exception:  # noqa: BLE001
            failures.append(_failure("evidence", "device_evidence_unavailable"))

    if output_dir is not None and artifact is not None:
        evidence_names.insert(1, artifact.filename)
    if artifact is not None and remove_apk and output_dir is not None:
        removed = file_store.remove_verified(artifact.path, artifact.sha256)
        cleanup_record["apk_removed"] = removed
        cleanup_record["status"] = "removed" if removed else "failed"
        if not removed:
            failures.append(_failure("cleanup", "apk_cleanup_failed"))
    elif artifact is not None:
        cleanup_record["status"] = "not_requested"

    blocked = any(item.get("blocked") is True for item in failures)
    status = STATUS_BLOCKED if blocked else (
        STATUS_PASSED if not failures else STATUS_FAILED
    )
    exit_code = {
        STATUS_PASSED: EXIT_PASSED,
        STATUS_FAILED: EXIT_FAILED,
        STATUS_BLOCKED: EXIT_BLOCKED,
    }[status]
    failures.sort(key=lambda item: json.dumps(item, sort_keys=True))

    artifact_record = _artifact_record(artifact, verify_result) if artifact else {}
    if artifact is not None:
        artifact_record["manifest_url"] = manifest_url
        artifact_record["apk_url"] = artifact.url
    evidence_files = (
        _evidence_hashes(file_store, output_dir, evidence_names)
        if output_dir is not None
        else []
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": status,
        "exit_code": exit_code,
        "public_ready": False,
        "boundary": (
            "single public artifact physical-device smoke; production "
            "readiness is not asserted"
        ),
        "serial": serial,
        "device": device_record,
        "artifact": artifact_record,
        "launch": launch_record,
        "public_api": api_record,
        "logcat": logcat_record,
        "cleanup": cleanup_record,
        "evidence": {"files": evidence_files},
        "failures": failures,
    }

    if output_dir is not None:
        try:
            file_store.write_bytes_atomic(
                output_dir / REPORT_JSON, render_json(result).encode("utf-8")
            )
            file_store.write_bytes_atomic(
                output_dir / REPORT_MARKDOWN, render_markdown(result).encode("utf-8")
            )
        except Exception:  # noqa: BLE001
            # Reports are mandatory; surface this as a failed result if possible.
            result["status"] = STATUS_FAILED
            result["exit_code"] = EXIT_FAILED
            result["failures"].append(_failure("report", "report_write_failed"))
            result["failures"].sort(key=lambda item: json.dumps(item, sort_keys=True))
    return result, int(result["exit_code"])


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="public_device_smoke",
        description=(
            "Download and verify the public signed Android APK, then run one "
            "explicit physical-device launch smoke (fail-closed, no retries)."
        ),
    )
    parser.add_argument("--serial", required=True, help="Physical adb serial.")
    parser.add_argument(
        "--manifest-url", required=True, help="Public HTTPS download manifest URL."
    )
    parser.add_argument(
        "--apk-url",
        default=None,
        help="Optional explicit APK URL; otherwise derived from the valid manifest.",
    )
    parser.add_argument(
        "--output", required=True, help="New relative directory under .verify/."
    )
    parser.add_argument(
        "--api-base-url",
        default=None,
        help="Public HTTPS API base; defaults from the manifest base path.",
    )
    parser.add_argument(
        "--remove-downloaded-apk",
        action="store_true",
        help="Opt-in cleanup of only the exact verified APK downloaded by this run.",
    )
    parser.add_argument("--download-timeout-seconds", type=int, default=DEFAULT_DOWNLOAD_TIMEOUT_SECONDS)
    parser.add_argument("--api-timeout-seconds", type=int, default=DEFAULT_API_TIMEOUT_SECONDS)
    parser.add_argument("--stable-wait-seconds", type=float, default=DEFAULT_STABLE_WAIT_SECONDS)
    parser.add_argument("--poll-interval-seconds", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS)
    parser.add_argument("--apksigner", default=None)
    parser.add_argument("--aapt", default=None)
    parser.add_argument("--adb-path", default="adb")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    adb = RealAdbSmokeDevice(args.serial, adb_path=args.adb_path)
    result, exit_code = run_smoke(
        serial=args.serial,
        manifest_url=args.manifest_url,
        apk_url=args.apk_url,
        output=args.output,
        remove_apk=args.remove_downloaded_apk,
        adb=adb,
        api_base_url=args.api_base_url,
        download_timeout_seconds=args.download_timeout_seconds,
        api_timeout_seconds=args.api_timeout_seconds,
        stable_wait_seconds=args.stable_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        apksigner=args.apksigner,
        aapt=args.aapt,
    )
    print(render_json(result))
    if not args.quiet:
        codes = ",".join(item["code"] for item in result["failures"])
        print(
            f"{TOOL_NAME}: status={result['status']} exit={result['exit_code']}"
            + (f" failures={codes}" if codes else ""),
            file=sys.stderr,
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
