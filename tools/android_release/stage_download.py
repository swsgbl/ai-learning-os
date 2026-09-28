"""Android download staging publisher (M14-174 readiness tool).

Atomically stages a **verified** signed release APK and its download
manifest into a staging download directory (the shape of
``download.example.com``: ``manifest.json`` at the root, APKs under
``android/``). Readiness only: this tool writes to a local staging root the
operator supplies; uploading to the public edge host remains an explicit
operator (Codex) step outside this repository.

Pipeline, fail-closed at every step — nothing is written unless every earlier
gate passes:

1. staging-root shape: an existing, non-symlink directory whose ``android``
   child is either absent (created) or an existing non-symlink directory;
2. the full M14-173 ``verify_artifact`` gate is **reused as-is** (same
   module, same seams): APK regular-file/SHA256/size vs manifest, signed
   entry with ``signature_scheme: v2+v3``, relative ``/android/`` URL
   identity, apksigner v2+v3 with a non-debug certificate, aapt badging
   identity, and — when a previous manifest is supplied — a strictly
   increasing versionCode over a schema-authoritative previous manifest;
3. target safety: the APK lands exactly at ``<staging>/android/<entry.name>``
   (single path segment, no dot segments — independently re-validated), a
   pre-existing target APK is refused (never overwritten), and the manifest
   lands at ``<staging>/manifest.json``;
4. atomic writes: same-directory temp file + fsync + ``os.replace`` for both
   payloads; on any failure the previous ``manifest.json`` is restored
   byte-for-byte, artifacts written by this run are removed, and pre-existing
   APKs (the previous release) are never deleted;
5. independent recheck: the staged APK bytes are re-read and re-hashed
   against the manifest entry, and the staged manifest is re-read and
   re-parsed against the ``aios-download-manifest/1`` schema; a tampered or
   half-written state fails closed and triggers the same rollback.

Safety contract (mirrors tools/android_release/verify_artifact.py): output is
deterministic value-free JSON — no secret, no environment variable value, no
certificate subject, no local absolute path, no child stdout/stderr, no temp
file names. The runner and tool resolver are injectable so the test suite
runs with zero real apksigner/aapt invocations and zero network.

Exit codes: 0 = staged, 1 = failed (fail-closed for every rejected input);
argparse usage errors exit 2 per the interpreter's own convention.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

if __package__ in (None, ""):  # direct script execution: put repo on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.android_release.verify_artifact import (
    MANIFEST_SCHEMA,
    load_manifest,
    parse_manifest_contract,
    run_verify,
)

SCHEMA_VERSION = 1
TOOL_NAME = "android_release_stage_download"
MODE = "stage"
MANIFEST_BASENAME = "manifest.json"
ANDROID_SUBDIR = "android"

STATUS_STAGED = "staged"
STATUS_FAILED = "failed"
EXIT_OK = 0
EXIT_FAILURE = 1

# Single path segment over the manifest URL allowlist charset, no dot
# segments — this re-derives, independently of verify_artifact, the shape
# that may ever become a file name under <staging>/android/.
STAGE_NAME_RE = re.compile(r"^[A-Za-z0-9._~+-]+$")

Runner = Callable
ToolResolver = Callable

# Indirections so tests can simulate failing os.replace and tampered reads.
_os_replace = os.replace
_reread_file = Path.read_bytes


# ---------------------------------------------------------------------------
# staging-root shape and target validation
# ---------------------------------------------------------------------------


def _is_symlink_dir(path: Path) -> bool:
    return path.is_symlink()


def validate_staging_root(
    staging_root: Path,
) -> Tuple[Dict[str, object], List[dict]]:
    """Staging root exists, is a directory, and is not a symlink."""
    record: Dict[str, object] = {
        "exists": staging_root.exists(),
        "is_directory": staging_root.is_dir(),
        "symlink": staging_root.is_symlink(),
    }
    failures: List[dict] = []
    if not staging_root.exists() or not staging_root.is_dir():
        failures.append({"code": "staging_root_invalid"})
        return record, failures
    if staging_root.is_symlink():
        failures.append({"code": "staging_root_symlink"})
        return record, failures
    android = staging_root / ANDROID_SUBDIR
    if android.exists():
        record["android_exists"] = True
        record["android_is_directory"] = android.is_dir()
        record["android_symlink"] = android.is_symlink()
        if not android.is_dir() or android.is_symlink():
            failures.append({"code": "staging_android_invalid"})
    else:
        record["android_exists"] = False
    return record, failures


def validate_stage_target(entry_name: str) -> Optional[str]:
    """A single safe path segment, or the failure category."""
    if not entry_name or STAGE_NAME_RE.match(entry_name) is None:
        return "staging_target_invalid"
    if entry_name in (".", ".."):
        return "staging_target_invalid"
    return None


def _symlink_free_child(path: Path) -> bool:
    return not path.is_symlink()


# ---------------------------------------------------------------------------
# atomic write helpers (temp + fsync + os.replace; rollback on failure)
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, payload: bytes) -> Optional[str]:
    """Same-directory temp + fsync + os.replace; None on success."""
    try:
        handle, temp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
        )
    except OSError:
        return "staging_write_failed"
    temp = Path(temp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _os_replace(temp, path)
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass
        return "staging_write_failed"
    return None


def _restore_manifest(target: Path, prior: Optional[bytes]) -> None:
    """Best-effort byte-for-byte rollback of the staged manifest."""
    try:
        if prior is None:
            target.unlink(missing_ok=True)
            return
        handle, temp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=target.name + ".", suffix=".restore"
        )
        with os.fdopen(handle, "wb") as stream:
            stream.write(prior)
            stream.flush()
            os.fsync(stream.fileno())
        _os_replace(Path(temp_name), target)
    except OSError:
        pass


def _cleanup_new(path: Path) -> None:
    """Remove a file written by this run only (targets are pre-validated
    absent, so anything present here was created by us)."""
    try:
        if path.is_file() and not path.is_symlink():
            path.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def _verify_summary(verify_result: dict) -> dict:
    checks = verify_result.get("checks", {})
    return {
        "status": verify_result["status"],
        "exit_code": verify_result["exit_code"],
        "failures": verify_result["failures"],
        "version_code_monotonic": checks.get("version_code_monotonic"),
        "apk_digest": checks.get("apk_digest"),
    }


def run_stage(
    apk: str,
    manifest: str,
    staging_root: str,
    previous_manifest: Optional[str] = None,
    apksigner: Optional[str] = None,
    aapt: Optional[str] = None,
    runner: Optional[Runner] = None,
    tool_resolver: Optional[ToolResolver] = None,
) -> Tuple[dict, int]:
    """Stage a verified APK + manifest; every rejected input fails closed."""
    failures: List[dict] = []
    checks: Dict[str, dict] = {}

    root_path = Path(staging_root).expanduser()
    apk_path = Path(apk).expanduser() if apk else None

    root_record, root_failures = validate_staging_root(root_path)
    checks["staging_root"] = root_record  # type: ignore[assignment]
    failures += root_failures
    if root_failures:
        return _stage_result(checks, failures, None)

    # --- M14-173 verify gate: nothing is staged unless the artifact passes
    verify_result, verify_exit = run_verify(
        apk=apk,
        manifest=manifest,
        previous_manifest=previous_manifest,
        apksigner=apksigner,
        aapt=aapt,
        runner=runner,
        tool_resolver=tool_resolver,
    )
    checks["verify"] = _verify_summary(verify_result)  # type: ignore[assignment]
    if verify_exit != EXIT_OK:
        failures += verify_result["failures"]
        return _stage_result(checks, failures, None)

    # --- expected manifest entry / identity for the landing zone
    model, error = load_manifest(manifest)
    if error is not None or model is None:
        failures.append({"code": error or "manifest_invalid_json"})
        return _stage_result(checks, failures, None)
    apk_name = apk_path.name if apk_path is not None else ""
    contract_record, contract_failures, entry, _identity = (
        parse_manifest_contract(model, apk_name)
    )
    if contract_failures or entry is None:
        # verify already passed, so a contract failure here is a drift:
        # fail closed rather than stage anything.
        failures += contract_failures or [{"code": "manifest_entry_not_found"}]
        return _stage_result(checks, failures, None)

    target_error = validate_stage_target(apk_name)
    if target_error is not None:
        failures.append({"code": target_error})
        return _stage_result(checks, failures, None)

    android_dir = root_path / ANDROID_SUBDIR
    try:
        if not android_dir.exists():
            android_dir.mkdir(parents=True)
    except OSError:
        failures.append({"code": "staging_android_create_failed"})
        return _stage_result(checks, failures, None)
    if android_dir.is_symlink() or not android_dir.is_dir():
        failures.append({"code": "staging_android_invalid"})
        return _stage_result(checks, failures, None)

    apk_target = android_dir / apk_name
    manifest_target = root_path / MANIFEST_BASENAME
    if apk_target.exists() or apk_target.is_symlink():
        failures.append({"code": "staging_target_exists"})
        return _stage_result(checks, failures, None)

    # defense in depth: resolve must stay inside <staging>/android
    resolved_target = Path(os.path.realpath(apk_target))
    resolved_root = Path(os.path.realpath(android_dir))
    if resolved_root not in resolved_target.parents:
        failures.append({"code": "staging_target_invalid"})
        return _stage_result(checks, failures, None)

    # --- land the APK, then the manifest, atomically
    try:
        apk_bytes = apk_path.read_bytes() if apk_path is not None else b""
    except OSError:
        failures.append({"code": "apk_unreadable"})
        return _stage_result(checks, failures, None)
    if hashlib.sha256(apk_bytes).hexdigest() != entry["sha256"] or len(
        apk_bytes
    ) != entry["size_bytes"]:
        # verify already compared these; drift since then fails closed.
        failures.append({"code": "sha256_mismatch"})
        return _stage_result(checks, failures, None)

    try:
        manifest_bytes = Path(manifest).expanduser().read_bytes()
    except OSError:
        failures.append({"code": "manifest_unreadable"})
        return _stage_result(checks, failures, None)

    prior_manifest: Optional[bytes] = None
    manifest_had_prior = manifest_target.exists()
    if manifest_had_prior:
        try:
            prior_manifest = _reread_file(manifest_target)  # type: ignore[misc]
        except OSError:
            failures.append({"code": "staging_write_failed"})
            return _stage_result(checks, failures, None)

    error = _atomic_write(apk_target, apk_bytes)
    if error is not None:
        failures.append({"code": error})
        return _stage_result(checks, failures, None)

    error = _atomic_write(manifest_target, manifest_bytes)
    if error is not None:
        failures.append({"code": error})
        _cleanup_new(apk_target)
        _restore_manifest(manifest_target, prior_manifest)
        return _stage_result(checks, failures, None)

    # --- independent recheck of what is actually on disk now
    recheck_record, recheck_failures = _recheck(
        apk_target, manifest_target, entry, manifest_bytes
    )
    checks["recheck"] = recheck_record  # type: ignore[assignment]
    if recheck_failures:
        failures += recheck_failures
        _cleanup_new(apk_target)
        _restore_manifest(manifest_target, prior_manifest)
        return _stage_result(checks, failures, None)

    staged = {
        "apk_basename": apk_target.name,
        "sha256": entry["sha256"],
        "size_bytes": entry["size_bytes"],
        "manifest_basename": MANIFEST_BASENAME,
        "version_code": contract_record.get("version_code"),
        "version_name": contract_record.get("version_name"),
    }
    return _stage_result(checks, failures, staged)


def _recheck(
    apk_target: Path,
    manifest_target: Path,
    entry: dict,
    expected_manifest_bytes: bytes,
) -> Tuple[dict, List[dict]]:
    """Re-read what landed on disk; mismatch or tamper fails closed."""
    record: Dict[str, object] = {
        "apk_digest": {"status": "fail", "sha256_match": None,
                       "size_match": None},
        "manifest_reparsed": {"status": "fail", "schema_recognized": False,
                              "bytes_match": None},
    }
    failures: List[dict] = []
    try:
        staged_bytes = _reread_file(apk_target)  # type: ignore[misc]
    except OSError:
        failures.append({"code": "recheck_apk_unreadable"})
        return record, failures
    staged_sha = hashlib.sha256(staged_bytes).hexdigest()
    record["apk_digest"]["sha256_match"] = staged_sha == entry["sha256"]
    record["apk_digest"]["size_match"] = len(staged_bytes) == entry["size_bytes"]
    if not record["apk_digest"]["sha256_match"]:
        failures.append({"code": "recheck_sha256_mismatch"})
    if not record["apk_digest"]["size_match"]:
        failures.append({"code": "recheck_size_mismatch"})
    if not failures:
        record["apk_digest"]["status"] = "pass"

    try:
        staged_manifest_bytes = _reread_file(manifest_target)  # type: ignore[misc]
    except OSError:
        failures.append({"code": "recheck_manifest_unreadable"})
        return record, failures
    record["manifest_reparsed"]["bytes_match"] = (
        staged_manifest_bytes == expected_manifest_bytes
    )
    if not record["manifest_reparsed"]["bytes_match"]:
        failures.append({"code": "recheck_manifest_bytes_mismatch"})
    try:
        staged_model = json.loads(staged_manifest_bytes.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        staged_model = None
    record["manifest_reparsed"]["schema_recognized"] = (
        isinstance(staged_model, dict)
        and staged_model.get("schema") == MANIFEST_SCHEMA
    )
    if not record["manifest_reparsed"]["schema_recognized"]:
        failures.append({"code": "recheck_manifest_schema_unrecognized"})
    if not any(
        code.startswith("recheck_manifest") for code in (f["code"] for f in failures)
    ):
        record["manifest_reparsed"]["status"] = "pass"
    return record, failures


def _stage_result(
    checks: Dict[str, dict], failures: List[dict], staged: Optional[dict]
) -> Tuple[dict, int]:
    failures_sorted = sorted(
        failures, key=lambda item: json.dumps(item, sort_keys=True)
    )
    status = STATUS_STAGED if not failures and staged is not None else STATUS_FAILED
    exit_code = EXIT_OK if status == STATUS_STAGED else EXIT_FAILURE
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "mode": MODE,
        "status": status,
        "exit_code": exit_code,
        "staged": staged,
        "checks": checks,
        "failures": failures_sorted,
    }
    return result, exit_code


def render_json(result: dict) -> str:
    """Deterministic serialization (sorted keys, ASCII-safe, no timestamps)."""
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True)


def render_summary(result: dict) -> str:
    """One-line human summary: statuses and failure codes only."""
    parts = [
        f"{TOOL_NAME}: status={result['status']}",
        f"exit={result['exit_code']}",
    ]
    codes = ",".join(item["code"] for item in result["failures"])
    if codes:
        parts.append(f"failures={codes}")
    return " ".join(parts)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="stage_download",
        description=(
            "Stage a verified signed Android APK + download manifest into a "
            "local staging download directory (atomic, fail-closed; upload "
            "to the public host stays an operator step)."
        ),
    )
    parser.add_argument("--apk", required=True, help="Verified signed APK.")
    parser.add_argument(
        "--manifest", required=True, help="Expected download manifest (JSON)."
    )
    parser.add_argument(
        "--staging-root", required=True,
        help="Staging download root (manifest.json + android/).",
    )
    parser.add_argument(
        "--previous-manifest", default=None,
        help="Previous download manifest (strictly smaller versionCode).",
    )
    parser.add_argument(
        "--apksigner", default=None,
        help="Explicit apksigner path (default: SDK build-tools discovery).",
    )
    parser.add_argument(
        "--aapt", default=None,
        help="Explicit aapt/aapt2 path (default: build-tools discovery).",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress the stderr summary."
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    result, exit_code = run_stage(
        apk=args.apk,
        manifest=args.manifest,
        staging_root=args.staging_root,
        previous_manifest=args.previous_manifest,
        apksigner=args.apksigner,
        aapt=args.aapt,
    )
    print(render_json(result))
    if not args.quiet:
        print(render_summary(result), file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
