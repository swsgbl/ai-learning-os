"""Android signed artifact verifier (M14-173 readiness gate).

Fail-closed acceptance tool for a future ``assembleRelease`` APK signed by the
externally managed release keystore (M14-171A wiring). This is a readiness
tool only: no release keystore exists today, no signed release APK has been
produced, and the ``/download`` channel state stays pending
(docs/MOBILE_DISTRIBUTION.md §0). The tool never generates, reads, or moves
keystore material, never touches a secret, never contacts the network, and
never marks a debug/unsigned artifact as distributable.

What it verifies, against an expected manifest aligned with
``infra/edge/download-manifest.example.json`` (schema
``aios-download-manifest/1``; minimal additive extension: the android channel
may declare ``package_name``, which this verifier requires for an exact
identity match):

1. the APK exists, is a regular file and is not a symlink;
2. size and SHA256 match the manifest entry matched by file name;
3. the manifest platform is android (``channels.android``), the entry is
   ``signed: true``, and its declared ``signature_scheme`` is the accepted
   ``v2+v3``;
4. ``apksigner verify --verbose --print-certs`` succeeds with both the v2 and
   v3 schemes true (v1 may be false), and the signer certificate subject is
   not a debug certificate (``androiddebugkey`` / ``CN=Android Debug`` →
   fail-closed);
5. ``aapt dump badging`` (aapt2 preferred, legacy aapt fallback) yields the
   actual package name / versionCode / versionName, matched exactly against
   the manifest;
6. when a previous manifest or previous versionCode is supplied, the actual
   versionCode is strictly greater; without previous input the monotonicity
   check honestly reports ``not_provided`` and never claims a pass.

Tool discovery follows the Android SDK build-tools convention
(``$ANDROID_HOME`` / ``$ANDROID_SDK_ROOT`` → ``build-tools/<version>/``)
with explicit ``--apksigner`` / ``--aapt`` overrides. Missing tools fail
closed; this tool never downloads or installs anything.

Safety contract (mirrors tools/harmony_release/verify_signature.py): results
are deterministic value-free JSON — no secret, no environment variable value,
no certificate subject or public key text, no local absolute path, no child
stdout/stderr is ever recorded; failures carry a category plus safe fields
only. Every ``AIOS_ANDROID_*`` signing input is stripped from the child
environment before any tool runs.

Exit codes: 0 = verified, 1 = failed (fail-closed for every rejected input);
argparse usage errors exit 2 per the interpreter's own convention.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

SCHEMA_VERSION = 1
TOOL_NAME = "android_release_verify_artifact"
MODE = "verify_only"

MANIFEST_SCHEMA = "aios-download-manifest/1"
ACCEPTED_SIGNATURE_SCHEME = "v2+v3"
REQUIRED_SCHEMES = ("v2", "v3")

# The verifier itself needs no credential: every Android signing input is
# stripped from the child environment; only their *names* are known here.
SCRUBBED_ENV_VARS = (
    "AIOS_ANDROID_KEYSTORE_PATH",
    "AIOS_ANDROID_KEYSTORE_STORE_PASSWORD",
    "AIOS_ANDROID_KEYSTORE_KEY_ALIAS",
    "AIOS_ANDROID_KEYSTORE_KEY_PASSWORD",
    "AIOS_ANDROID_SIGNING_PROPERTIES",
)
SDK_ENV_VARS = ("ANDROID_HOME", "ANDROID_SDK_ROOT")

APKSIGNER_ARGV_TAIL = ("verify", "--verbose", "--print-certs")
AAPT_ARGV_TAIL = ("dump", "badging")

DEBUG_DN_MARKERS = (
    ("androiddebugkey", "androiddebugkey"),
    ("cn=android debug", "cn_android_debug"),
)
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
SCHEME_RES = {
    "v1": re.compile(r"Verified using v1 scheme \(JAR signing\):\s*(true|false)"),
    "v2": re.compile(r"Verified using v2 scheme \(APK Signature Scheme v2\):\s*(true|false)"),
    "v3": re.compile(r"Verified using v3 scheme \(APK Signature Scheme v3\):\s*(true|false)"),
}
SIGNER_DN_RE = re.compile(r"Signer #1 certificate DN:\s*(.+)")
BADGING_PACKAGE_RE = re.compile(
    r"^package:\s+name='([^']*)'\s+versionCode='(\d+)'\s+versionName='([^']*)'",
    re.MULTILINE,
)

EXIT_OK = 0
EXIT_FAILURE = 1
STATUS_VERIFIED = "verified"
STATUS_FAILED = "failed"
CHECK_NOT_RUN = "not_run"

ToolResolver = Callable[[str, Optional[str]], Tuple[Optional[Path], Optional[str]]]
Runner = Callable[[Sequence[str], Dict[str, str]], "CommandOutcome"]


class CommandOutcome(NamedTuple):
    """Value extracted from a child tool run (stdout/stderr stay in memory)."""

    returncode: int
    stdout: str
    stderr: str


def subprocess_runner(argv: Sequence[str], env: Dict[str, str]) -> CommandOutcome:
    """Run one child tool; OSError propagates to the caller (fail-closed)."""
    proc = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return CommandOutcome(proc.returncode, proc.stdout or "", proc.stderr or "")


# Module-level defaults so tests (and main()) can inject fakes without
# spawning apksigner/aapt or requiring an Android SDK.
default_runner: Runner = subprocess_runner
default_tool_resolver: ToolResolver = None  # assigned after resolve_android_tool


def _read_apk_bytes(path: Path) -> bytes:
    """Read the APK bytes (seam for tests; OSError fails closed upstream)."""
    return path.read_bytes()


def child_env() -> Dict[str, str]:
    """Host environment minus every Android signing input (names only here)."""
    env = dict(os.environ)
    for name in SCRUBBED_ENV_VARS:
        env.pop(name, None)
    return env


# ---------------------------------------------------------------------------
# tool discovery (build-tools convention; never downloads anything)
# ---------------------------------------------------------------------------


def _version_dir_key(path: object) -> Tuple[int, ...]:
    name = path.name if isinstance(path, Path) else str(path)
    return tuple(int(part) for part in re.findall(r"\d+", name))


def _candidate_names(kind: str) -> List[str]:
    bases = ["apksigner"] if kind == "apksigner" else ["aapt2", "aapt"]
    if os.name != "nt":
        return bases
    names: List[str] = []
    for base in bases:
        names.extend([base, base + ".bat", base + ".exe"])
    return names


def _sdk_roots() -> List[Path]:
    roots: List[Path] = []
    for var in SDK_ENV_VARS:
        value = os.environ.get(var)
        if value:
            root = Path(value).expanduser()
            if root not in roots:
                roots.append(root)
    return roots


def resolve_android_tool(
    kind: str, explicit: Optional[str] = None
) -> Tuple[Optional[Path], Optional[str]]:
    """Resolve apksigner/aapt; return (path, program name) or (None, None).

    Explicit paths are used verbatim when they name a regular file. Otherwise
    the newest ``build-tools/<version>/`` under the SDK env roots is probed
    with the platform-appropriate file names. Discovery never installs,
    downloads, or guesses beyond this convention.
    """
    if explicit is not None:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return candidate, candidate.name
        return None, None
    for root in _sdk_roots():
        build_tools = root / "build-tools"
        if not build_tools.is_dir():
            continue
        version_dirs = sorted(build_tools.iterdir(), key=_version_dir_key, reverse=True)
        for version_dir in version_dirs:
            for name in _candidate_names(kind):
                candidate = version_dir / name
                if candidate.is_file():
                    return candidate, candidate.name
    return None, None


default_tool_resolver = resolve_android_tool


# ---------------------------------------------------------------------------
# child output parsing (to value-free facts only)
# ---------------------------------------------------------------------------


def parse_apksigner_verbose(text: str) -> Dict[str, object]:
    """Extract scheme booleans and the signer DN (in-memory, never echoed)."""
    facts: Dict[str, object] = {"v1": None, "v2": None, "v3": None, "signer_dn": None}
    for scheme, pattern in SCHEME_RES.items():
        match = pattern.search(text)
        if match:
            facts[scheme] = match.group(1) == "true"
    dn = SIGNER_DN_RE.search(text)
    if dn:
        facts["signer_dn"] = dn.group(1).strip()
    facts["parsed"] = facts["v2"] is not None and facts["v3"] is not None
    return facts


def parse_badging_package(text: str) -> Optional[Dict[str, object]]:
    """Extract name / versionCode / versionName from ``dump badging``."""
    match = BADGING_PACKAGE_RE.search(text)
    if match is None:
        return None
    return {
        "package_name": match.group(1),
        "version_code": int(match.group(2)),
        "version_name": match.group(3),
    }


def debug_dn_marker(dn: Optional[str]) -> Optional[str]:
    """Category of the matched debug-certificate marker (never the DN)."""
    if not dn:
        return None
    lowered = dn.lower()
    for needle, category in DEBUG_DN_MARKERS:
        if needle in lowered:
            return category
    return None


def _parse_nonnegative_int(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


# ---------------------------------------------------------------------------
# manifest loading and contract
# ---------------------------------------------------------------------------


def load_manifest(manifest: str) -> Tuple[Optional[dict], Optional[str]]:
    """Load the expected manifest; return (model, error category)."""
    path = Path(manifest).expanduser()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None, "manifest_unreadable"
    try:
        model = json.loads(text)
    except ValueError:
        return None, "manifest_invalid_json"
    if not isinstance(model, dict):
        return None, "manifest_invalid_json"
    return model, None


def parse_manifest_contract(
    model: dict, apk_filename: str
) -> Tuple[dict, List[dict], Optional[dict], Optional[dict]]:
    """Check the manifest contract; return (record, failures, entry, identity).

    ``entry`` carries the validated sha256/size fields (or None); ``identity``
    carries package_name/version_code/version_name for the badging comparison
    (or None when the channel identity is incomplete).
    """
    record: dict = {
        "status": "fail",
        "schema_recognized": False,
        "android_channel": False,
        "entry_name": None,
        "entry_matched": False,
        "entry_signed": None,
        "signature_scheme_accepted": None,
        "package_name": None,
        "version_code": None,
        "version_name": None,
    }
    failures: List[dict] = []
    if model.get("schema") != MANIFEST_SCHEMA:
        failures.append({"code": "manifest_schema_unsupported"})
        return record, failures, None, None
    record["schema_recognized"] = True

    channels = model.get("channels")
    channel = channels.get("android") if isinstance(channels, dict) else None
    if not isinstance(channel, dict):
        failures.append({"code": "manifest_android_channel_missing"})
        return record, failures, None, None
    record["android_channel"] = True

    package_name = channel.get("package_name")
    if isinstance(package_name, str) and package_name:
        record["package_name"] = package_name
    else:
        failures.append({"code": "manifest_package_name_missing"})
    version_code = _parse_nonnegative_int(channel.get("versionCode"))
    if version_code is None:
        failures.append({"code": "manifest_version_code_invalid"})
    else:
        record["version_code"] = version_code
    version_name = channel.get("versionName")
    if isinstance(version_name, str) and version_name:
        record["version_name"] = version_name
    else:
        failures.append({"code": "manifest_version_name_invalid"})
    identity = (
        {"package_name": record["package_name"],
         "version_code": record["version_code"],
         "version_name": record["version_name"]}
        if record["package_name"] is not None
        and record["version_code"] is not None
        and record["version_name"] is not None
        else None
    )

    entry = _match_manifest_entry(record, channel, apk_filename, failures)
    record["status"] = "pass" if not failures else "fail"
    return record, failures, entry, identity


def _match_manifest_entry(
    record: dict, channel: dict, apk_filename: str, failures: List[dict]
) -> Optional[dict]:
    """Match the APK file name against ``files``; validate entry fields."""
    files = channel.get("files")
    entries = [
        item
        for item in (files if isinstance(files, list) else [])
        if isinstance(item, dict) and item.get("name") == apk_filename
    ]
    if len(entries) != 1:
        failures.append({"code": "manifest_entry_not_found"})
        return None
    entry = entries[0]
    record["entry_matched"] = True
    record["entry_name"] = entry.get("name") if isinstance(entry.get("name"), str) else None
    if entry.get("signed") is not True:
        failures.append({"code": "manifest_entry_not_signed"})
    else:
        record["entry_signed"] = True
    scheme = entry.get("signature_scheme")
    if scheme is not None:
        accepted = scheme == ACCEPTED_SIGNATURE_SCHEME
        record["signature_scheme_accepted"] = accepted
        if not accepted:
            failures.append({"code": "manifest_signature_scheme_mismatch"})
    return _validate_entry_fields(entry, failures)


def _validate_entry_fields(entry: dict, failures: List[dict]) -> Optional[dict]:
    sha256 = entry.get("sha256")
    normalized = sha256.strip().lower() if isinstance(sha256, str) else None
    sha_valid = normalized is not None and SHA256_HEX_RE.match(normalized) is not None
    if not sha_valid:
        failures.append({"code": "manifest_sha256_invalid"})
    size = _parse_nonnegative_int(entry.get("size_bytes"))
    if size is None:
        failures.append({"code": "manifest_size_invalid"})
    if not sha_valid or size is None:
        return None
    return {"sha256": normalized, "size_bytes": size}


# ---------------------------------------------------------------------------
# individual checks
# ---------------------------------------------------------------------------


def check_apk_file(apk_path: Optional[Path]) -> Tuple[dict, List[dict]]:
    """The APK must exist as a regular file and not be a symlink."""
    exists = apk_path is not None and apk_path.exists()
    regular = apk_path is not None and apk_path.is_file()
    symlink = apk_path is not None and apk_path.is_symlink()
    record: dict = {
        "status": "fail",
        "filename": apk_path.name if apk_path is not None else None,
        "exists": exists,
        "regular_file": regular,
        "symlink": symlink,
    }
    failures: List[dict] = []
    if not exists:
        failures.append({"code": "apk_missing"})
    elif not regular:
        failures.append({"code": "apk_not_regular"})
    elif symlink:
        failures.append({"code": "apk_symlink"})
    else:
        record["status"] = "pass"
    return record, failures


def check_digest(
    entry: Optional[dict], actual_sha256: Optional[str], actual_size: Optional[int]
) -> Tuple[dict, List[dict]]:
    """Compare size and SHA256 against the matched manifest entry."""
    record: dict = {
        "status": "not_run",
        "expected_sha256": entry["sha256"] if entry else None,
        "actual_sha256": actual_sha256,
        "expected_size_bytes": entry["size_bytes"] if entry else None,
        "actual_size_bytes": actual_size,
        "sha256_match": None,
        "size_match": None,
    }
    failures: List[dict] = []
    if entry is None or actual_sha256 is None or actual_size is None:
        return record, failures
    record["sha256_match"] = entry["sha256"] == actual_sha256
    record["size_match"] = entry["size_bytes"] == actual_size
    if not record["sha256_match"]:
        failures.append({"code": "sha256_mismatch"})
    if not record["size_match"]:
        failures.append({"code": "size_mismatch"})
    record["status"] = "pass" if not failures else "fail"
    return record, failures


def check_apksigner(
    apk_path: Path, resolve: ToolResolver, run: Runner, explicit: Optional[str]
) -> Tuple[dict, List[dict]]:
    """apksigner verify --verbose --print-certs: v2+v3 true, non-debug cert."""
    record: dict = {
        "status": "fail",
        "tool": None,
        "resolved": False,
        "exit_code": None,
        "verified": None,
        "schemes": {"v1": None, "v2": None, "v3": None},
        "required_schemes": list(REQUIRED_SCHEMES),
        "debug_certificate": None,
    }
    failures: List[dict] = []
    tool_path, tool_name = resolve("apksigner", explicit)
    if tool_path is None:
        failures.append({"code": "tool_missing", "detail": {"tool": "apksigner"}})
        return record, failures
    record["resolved"] = True
    record["tool"] = tool_name
    argv = [str(tool_path), *APKSIGNER_ARGV_TAIL, str(apk_path)]
    try:
        outcome = run(argv, child_env())
    except OSError:
        failures.append({"code": "tool_spawn_failed", "detail": {"tool": "apksigner"}})
        return record, failures
    record["exit_code"] = outcome.returncode
    if outcome.returncode != EXIT_OK:
        record["verified"] = False
        failures.append(
            {"code": "apksigner_verify_failed", "detail": {"exit_code": outcome.returncode}}
        )
        return record, failures
    record["verified"] = True
    return _evaluate_apksigner_facts(record, outcome.stdout, failures)


def _evaluate_apksigner_facts(
    record: dict, stdout: str, failures: List[dict]
) -> Tuple[dict, List[dict]]:
    facts = parse_apksigner_verbose(stdout)
    for scheme in ("v1", "v2", "v3"):
        record["schemes"][scheme] = facts[scheme]
    if not facts["parsed"]:
        failures.append({"code": "apksigner_output_unparsed"})
        return record, failures
    marker = debug_dn_marker(facts["signer_dn"])
    record["debug_certificate"] = marker is not None
    if marker is not None:
        failures.append({"code": "debug_certificate", "detail": {"matched": marker}})
        return record, failures
    if facts["v2"] is not True:
        failures.append({"code": "scheme_v2_missing"})
    if facts["v3"] is not True:
        failures.append({"code": "scheme_v3_missing"})
    record["status"] = "pass" if not failures else "fail"
    return record, failures


def check_aapt_badging(
    apk_path: Path,
    identity: Optional[dict],
    resolve: ToolResolver,
    run: Runner,
    explicit: Optional[str],
) -> Tuple[dict, List[dict]]:
    """aapt/aapt2 dump badging: exact package/version identity match."""
    record: dict = {
        "status": "fail",
        "tool": None,
        "resolved": False,
        "exit_code": None,
        "package_name": None,
        "version_code": None,
        "version_name": None,
        "matches_manifest": None,
    }
    failures: List[dict] = []
    if identity is None:  # manifest identity incomplete → comparison impossible
        record["status"] = CHECK_NOT_RUN
        return record, failures
    tool_path, tool_name = resolve("aapt", explicit)
    if tool_path is None:
        failures.append({"code": "tool_missing", "detail": {"tool": "aapt"}})
        return record, failures
    record["resolved"] = True
    record["tool"] = tool_name
    argv = [str(tool_path), *AAPT_ARGV_TAIL, str(apk_path)]
    try:
        outcome = run(argv, child_env())
    except OSError:
        failures.append({"code": "tool_spawn_failed", "detail": {"tool": "aapt"}})
        return record, failures
    record["exit_code"] = outcome.returncode
    facts = parse_badging_package(outcome.stdout) if outcome.returncode == EXIT_OK else None
    if facts is None:
        failures.append(
            {"code": "badging_failed", "detail": {"exit_code": outcome.returncode}}
        )
        return record, failures
    return _compare_badging_identity(record, facts, identity, failures)


def _compare_badging_identity(
    record: dict, facts: Dict[str, object], identity: dict, failures: List[dict]
) -> Tuple[dict, List[dict]]:
    record["package_name"] = facts["package_name"]
    record["version_code"] = facts["version_code"]
    record["version_name"] = facts["version_name"]
    if facts["package_name"] != identity["package_name"]:
        failures.append({"code": "package_name_mismatch"})
    if facts["version_code"] != identity["version_code"]:
        failures.append({"code": "version_code_mismatch"})
    if facts["version_name"] != identity["version_name"]:
        failures.append({"code": "version_name_mismatch"})
    record["matches_manifest"] = not failures
    record["status"] = "pass" if not failures else "fail"
    return record, failures


def _previous_version_code_from_manifest(
    previous_manifest: Optional[str],
) -> Tuple[Optional[int], List[dict]]:
    if previous_manifest is None:
        return None, []
    model, error = load_manifest(previous_manifest)
    if error is not None:
        return None, [{"code": "previous_manifest_invalid"}]
    channels = model.get("channels")
    channel = channels.get("android") if isinstance(channels, dict) else None
    version_code = (
        _parse_nonnegative_int(channel.get("versionCode"))
        if isinstance(channel, dict)
        else None
    )
    if version_code is None:
        return None, [{"code": "previous_manifest_invalid"}]
    return version_code, []


def check_version_code_monotonic(
    previous_manifest: Optional[str],
    previous_version_code: Optional[int],
    actual_version_code: Optional[int],
) -> Tuple[dict, List[dict]]:
    """versionCode must strictly increase; no previous input → not_provided."""
    record: dict = {
        "status": "not_provided",
        "source": None,
        "previous": None,
        "actual": actual_version_code,
        "strictly_greater": None,
    }
    failures: List[dict] = []
    from_manifest, parse_failures = _previous_version_code_from_manifest(
        previous_manifest
    )
    failures += parse_failures
    if parse_failures:
        record["status"] = "not_evaluated"
        return record, failures
    if previous_version_code is not None and from_manifest is not None:
        if previous_version_code != from_manifest:
            failures.append({"code": "previous_version_code_conflict"})
            record["status"] = "not_evaluated"
            return record, failures
        previous, source = from_manifest, "both"
    elif previous_version_code is not None:
        previous, source = previous_version_code, "previous_version_code"
    elif from_manifest is not None:
        previous, source = from_manifest, "previous_manifest"
    else:
        return record, failures  # not_provided, honestly
    record["previous"] = previous
    record["source"] = source
    if actual_version_code is None:
        record["status"] = "not_evaluated"
        return record, failures
    record["strictly_greater"] = actual_version_code > previous
    if not record["strictly_greater"]:
        failures.append({"code": "version_code_not_greater"})
        record["status"] = "fail"
    else:
        record["status"] = "pass"
    return record, failures


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def _not_run_check(name: str) -> dict:
    skeletons: Dict[str, dict] = {
        "manifest_contract": {"status": CHECK_NOT_RUN, "schema_recognized": False,
                              "android_channel": False, "entry_name": None,
                              "entry_matched": False, "entry_signed": None,
                              "signature_scheme_accepted": None, "package_name": None,
                              "version_code": None, "version_name": None},
        "apk_digest": {"status": CHECK_NOT_RUN, "expected_sha256": None,
                       "actual_sha256": None, "expected_size_bytes": None,
                       "actual_size_bytes": None, "sha256_match": None,
                       "size_match": None},
        "apksigner": {"status": CHECK_NOT_RUN, "tool": None, "resolved": False,
                      "exit_code": None, "verified": None,
                      "schemes": {"v1": None, "v2": None, "v3": None},
                      "required_schemes": list(REQUIRED_SCHEMES),
                      "debug_certificate": None},
        "aapt_badging": {"status": CHECK_NOT_RUN, "tool": None, "resolved": False,
                         "exit_code": None, "package_name": None, "version_code": None,
                         "version_name": None, "matches_manifest": None},
    }
    return dict(skeletons[name])


def _load_contract(
    manifest: Optional[str], apk_filename: str
) -> Tuple[dict, List[dict], Optional[dict], Optional[dict]]:
    """Load and contract-check the manifest; (record, failures, entry, identity)."""
    if manifest is None:
        return _not_run_check("manifest_contract"), [{"code": "manifest_unreadable"}], None, None
    model, error = load_manifest(manifest)
    if error is not None:
        return _not_run_check("manifest_contract"), [{"code": error}], None, None
    return parse_manifest_contract(model, apk_filename)


def run_verify(
    apk: str,
    manifest: str,
    previous_manifest: Optional[str] = None,
    previous_version_code: Optional[int] = None,
    apksigner: Optional[str] = None,
    aapt: Optional[str] = None,
    runner: Optional[Runner] = None,
    tool_resolver: Optional[ToolResolver] = None,
) -> Tuple[dict, int]:
    """Run every check; return (deterministic value-free result, exit code)."""
    run = runner if runner is not None else default_runner
    resolve = tool_resolver if tool_resolver is not None else default_tool_resolver
    failures: List[dict] = []
    checks: Dict[str, dict] = {}

    apk_path = Path(apk).expanduser() if apk else None
    apk_record, apk_failures = check_apk_file(apk_path)
    checks["apk_file"] = apk_record
    failures += apk_failures
    apk_ok = apk_record["status"] == "pass"

    actual_sha256: Optional[str] = None
    actual_size: Optional[int] = None
    if apk_ok and apk_path is not None:
        try:
            data = _read_apk_bytes(apk_path)
        except OSError:
            failures.append({"code": "apk_unreadable"})
        else:
            actual_sha256 = hashlib.sha256(data).hexdigest()
            actual_size = len(data)

    contract_record, contract_failures, entry, identity = _load_contract(
        manifest, apk_path.name if apk_path is not None else ""
    )
    checks["manifest_contract"] = contract_record
    failures += contract_failures

    digest_record, digest_failures = check_digest(entry, actual_sha256, actual_size)
    checks["apk_digest"] = digest_record
    failures += digest_failures

    if apk_ok and apk_path is not None:
        signer_record, signer_failures = check_apksigner(
            apk_path, resolve, run, apksigner
        )
        checks["apksigner"] = signer_record
        failures += signer_failures
        badging_record, badging_failures = check_aapt_badging(
            apk_path, identity, resolve, run, aapt
        )
        checks["aapt_badging"] = badging_record
        failures += badging_failures
        actual_version_code = badging_record["version_code"]
    else:
        checks["apksigner"] = _not_run_check("apksigner")
        checks["aapt_badging"] = _not_run_check("aapt_badging")
        actual_version_code = None

    mono_record, mono_failures = check_version_code_monotonic(
        previous_manifest, previous_version_code, actual_version_code
    )
    checks["version_code_monotonic"] = mono_record
    failures += mono_failures

    return _assemble_result(checks, failures)


def _assemble_result(checks: Dict[str, dict], failures: List[dict]) -> Tuple[dict, int]:
    failures.sort(key=lambda item: json.dumps(item, sort_keys=True))
    status = STATUS_VERIFIED if not failures else STATUS_FAILED
    exit_code = EXIT_OK if not failures else EXIT_FAILURE
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "mode": MODE,
        "status": status,
        "exit_code": exit_code,
        "checks": checks,
        "failures": failures,
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
        prog="verify_artifact",
        description=(
            "Android signed artifact verifier (fail-closed readiness gate; "
            "never reads keystores or secrets, never claims the unsigned "
            "boundary away)."
        ),
    )
    parser.add_argument("--apk", required=True, help="APK to verify.")
    parser.add_argument(
        "--manifest", required=True, help="Expected download manifest (JSON)."
    )
    parser.add_argument(
        "--previous-manifest", default=None,
        help="Previous download manifest supplying the previous versionCode.",
    )
    parser.add_argument(
        "--previous-version-code", type=int, default=None,
        help="Previous versionCode (must agree with --previous-manifest if both given).",
    )
    parser.add_argument(
        "--apksigner", default=None,
        help="Explicit apksigner path (default: Android SDK build-tools discovery).",
    )
    parser.add_argument(
        "--aapt", default=None,
        help="Explicit aapt/aapt2 path (default: build-tools discovery, aapt2 first).",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress the one-line stderr summary."
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    result, exit_code = run_verify(
        apk=args.apk,
        manifest=args.manifest,
        previous_manifest=args.previous_manifest,
        previous_version_code=args.previous_version_code,
        apksigner=args.apksigner,
        aapt=args.aapt,
    )
    print(render_json(result))
    if not args.quiet:
        print(render_summary(result), file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
