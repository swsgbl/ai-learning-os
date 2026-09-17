"""Verifies one signed HAP with the DevEco ``hap-sign-tool.jar`` verifier.

Documented child command (the same tool the signing wrapper uses; values are
passed to the child only)::

    java -jar hap-sign-tool.jar verify-app \
         -inFile <hap> -outCertChain <cert-chain> -outProfile <profile> \
         -inForm zip

``verify-app`` / ``-inForm`` / ``-outCertChain`` / ``-outProfile`` are the
option names documented for the SDK's hap-sign-tool. The two dump paths are
created inside a fresh temporary directory *outside* the repository and removed
before the run returns; their paths and contents are never recorded. This tool
never signs anything, never reads a credential (every release material and
credential variable is stripped from the child environment), never inspects or
records the child's stdout/stderr, and never touches a device.

Signedness evidence (never the filename)
========================================
Exactly two value-free evidence items decide the verdict:

* the **verifier result** - the exit code of the documented command above;
* the **parsed profile facts** - the artifact's own profile as dumped by
  ``-outProfile``, and, when the caller supplied ``--profile``, that material
  too. *Every* available source is checked; only the *presence* of a bundle
  name and whether it matches the expected bundle name are recorded - an
  extracted bundle name is only ever compared, never echoed.

The input file name is only recorded as an observation (``filename_has_signed``,
``filename_has_unsigned``); ``filename_used_for_signedness`` is always ``false``.

Profile material safety
=======================
``--profile`` material must live *outside* the repository, exist, be a regular
file and keep a documented suffix (``.p7b`` or ``.json``); it is validated by
the very same classifier the release preflight uses for external materials, so
the two tools cannot drift. Paths, payload bytes and extracted values never
reach the result JSON, the summary or a failure detail.

Verdict precedence (documented, not hidden)
===========================================

    request_invalid -> toolchain_unavailable -> tool_failure
      -> profile_bundle_mismatch -> invalid_signature -> unsigned
      -> signed_and_valid

* ``unsigned``: the verifier rejected the artifact and no signature material
  (profile payload) could be extracted from it.
* ``invalid_signature``: the verifier rejected an artifact that *did* carry
  signature material.
* ``profile_bundle_mismatch``: profile facts are available but a bundle name is
  missing or differs from the expected bundle name - fail closed. A mismatch
  outranks a missing verdict, so a contradiction is never hidden behind
  ``unsigned``.
* ``tool_failure``: the verifier could not be spawned, so no verdict exists.
* ``request_invalid`` also covers a requested profile assertion whose facts
  cannot be read at all: the request cannot be answered, so nothing is claimed.

``signature`` talks only about the artifact's signature (``signed`` = does it
carry verifiable signature material, ``verified`` = did the verifier accept
it); it stays ``true`` for a mismatch that verified, which is why the release
verdict lives in ``status``/``exit_code``. ``signature.signed`` is ``null``
whenever no verifier verdict exists.

Exit codes: 0 = ``signed_and_valid``, 1 = a negative verdict or a tool failure,
2 = the request/toolchain cannot produce a verdict at all. argparse usage errors
exit 2 as well; that is the interpreter's own convention and this tool never
reports its own exit 2 through argparse.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

try:  # package import (pytest, python -m)
    from tools.harmony_release.preflight import (
        MATERIAL_ENV_VARS,
        _classify_material_path as classify_material_path,
    )
    from tools.harmony_release.sign_hap import (
        CREDENTIAL_ENV_VARS,
        DEFAULT_JAR_NAME,
        DEFAULT_JAVA,
        SDK_HOME_ENV_VAR,
        CommandResult,
        Toolchain,
        _resolve_in_repo,
        _sha256_upper,
        resolve_toolchain,
        subprocess_runner,
        toolchain_record,
    )
except ImportError:  # direct script: python tools/harmony_release/verify_signature.py
    from preflight import (  # type: ignore[no-redef]
        MATERIAL_ENV_VARS,
        _classify_material_path as classify_material_path,
    )
    from sign_hap import (  # type: ignore[no-redef]
        CREDENTIAL_ENV_VARS,
        DEFAULT_JAR_NAME,
        DEFAULT_JAVA,
        SDK_HOME_ENV_VAR,
        CommandResult,
        Toolchain,
        _resolve_in_repo,
        _sha256_upper,
        resolve_toolchain,
        subprocess_runner,
        toolchain_record,
    )

SCHEMA_VERSION = 1
TOOL_NAME = "harmony_release_verify_signature"
MODE = "verify_only"
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_BUNDLE_NAME = "com.ailearningos.app"
BUNDLE_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$"
BUNDLE_NAME_RE = re.compile(BUNDLE_NAME_PATTERN)
BUNDLE_NAME_MAX_LENGTH = 128

VERIFY_SUBCOMMAND = "verify-app"
VERIFY_IN_FORM = "zip"
VERIFY_OPTION_NAMES = ("-inFile", "-outCertChain", "-outProfile", "-inForm")
VERIFY_DUMP_OPTION_NAMES = ("-outCertChain", "-outProfile")
VERIFY_DUMP_NAMES = ("signature-cert-chain.cer", "hap-profile.p7b")
TEMP_DIR_PREFIX = "hmh-verify-signature-"
STEP_NAME = "verify_app"

PROFILE_SUFFIXES = (".p7b", ".json")
PROFILE_BUNDLE_INFO_KEYS = ("bundle-info", "bundleInfo")
PROFILE_BUNDLE_NAME_KEYS = ("bundle-name", "bundleName")
PROFILE_MAX_BYTES = 1 << 22  # 4 MiB: a provisioning profile is a few KiB
PROFILE_NOT_EXTRACTED = "profile_not_extracted"
SOURCE_HAP_PROFILE = "hap_profile"
SOURCE_PROFILE_OPTION = "profile_option"

SIGNED_MARKER = "signed"
UNSIGNED_MARKER = "unsigned"

EXIT_VALID = 0
EXIT_FAILURE = 1
EXIT_UNAVAILABLE = 2

STATUS_SIGNED_AND_VALID = "signed_and_valid"
STATUS_UNSIGNED = "unsigned"
STATUS_INVALID_SIGNATURE = "invalid_signature"
STATUS_PROFILE_BUNDLE_MISMATCH = "profile_bundle_mismatch"
STATUS_TOOL_FAILURE = "tool_failure"
STATUS_REQUEST_INVALID = "request_invalid"
STATUS_TOOLCHAIN_UNAVAILABLE = "toolchain_unavailable"

# UNAVAILABLE_STATUSES are the statuses that mean "no verdict was produced".
UNAVAILABLE_STATUSES = (STATUS_REQUEST_INVALID, STATUS_TOOLCHAIN_UNAVAILABLE)

BASIS_VERIFIER_OK = "verifier_reported_valid"
BASIS_REJECTED_WITH_MATERIAL = "verifier_rejected_and_signature_material_present"
BASIS_REJECTED_WITHOUT_MATERIAL = "verifier_rejected_and_no_signature_material"
BASIS_NO_VERDICT = "verifier_did_not_produce_a_verdict"
BASIS_NOT_RUN = "verifier_did_not_run"

VERIFIER_STAGE_NOT_RUN = "not_run"
VERIFIER_STAGE_SPAWN_FAILED = "spawn_failed"
VERIFIER_STAGE_COMPLETED = "completed"

# The verifier needs no credential: every release material/credential variable
# is stripped from the child environment, and only their *names* are recorded.
SCRUBBED_ENV_VARS = tuple(sorted((*MATERIAL_ENV_VARS, *CREDENTIAL_ENV_VARS)))


@dataclass(frozen=True)
class VerifyRequest:
    input_path: Path
    input_record: dict
    profile_path: Optional[Path]
    profile_record: dict
    expected_bundle_name: str


# A profile reader returns (facts, error category); facts carry the extracted
# bundle name in memory only (never in the result JSON).
ProfileReader = Callable[[Path], Tuple[Optional[dict], Optional[str]]]


def child_env() -> Dict[str, str]:
    """Host environment minus every release material/credential variable."""
    env = dict(os.environ)
    for name in SCRUBBED_ENV_VARS:
        env.pop(name, None)
    return env


def build_verify_argv(
    toolchain: Toolchain, hap_path: Path, cert_chain_out: Path, profile_out: Path
) -> List[str]:
    """The documented ``verify-app`` invocation, values included for the child.

    Checked by tests against the documented option names; the returned argv is
    handed to the runner and is never serialized.
    """
    return [
        str(toolchain.java),
        "-jar",
        str(toolchain.jar),
        VERIFY_SUBCOMMAND,
        "-inFile", str(hap_path),
        "-outCertChain", str(cert_chain_out),
        "-outProfile", str(profile_out),
        "-inForm", VERIFY_IN_FORM,
    ]


def command_shape(java_name: str) -> dict:
    """Command shape: option/variable *names* and constants only.

    ``java_name`` is a basename so an absolute java path can never leak.
    """
    return {
        "program": java_name,
        "jar_name": DEFAULT_JAR_NAME,
        "subcommand": VERIFY_SUBCOMMAND,
        "in_form": VERIFY_IN_FORM,
        "cwd": ".",
        "option_names": list(VERIFY_OPTION_NAMES),
        "dump_option_names": list(VERIFY_DUMP_OPTION_NAMES),
        "secret_option_names": [],
        "env_keys": [],
        "stripped_env_keys": list(SCRUBBED_ENV_VARS),
    }


def _extract_bundle_name(data: dict) -> Optional[str]:
    """Read ``bundle-info.bundle-name`` from a parsed profile payload."""
    info = None
    for key in PROFILE_BUNDLE_INFO_KEYS:
        candidate = data.get(key)
        if isinstance(candidate, dict):
            info = candidate
            break
    if info is None:
        return None
    for key in PROFILE_BUNDLE_NAME_KEYS:
        value = info.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _parse_json_object(raw: bytes) -> Optional[dict]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _embedded_json_slice(raw: bytes) -> Optional[bytes]:
    """Slice a JSON object out of a container (a p7b payload is contiguous).

    The provisioning profile payload inside a PKCS#7 container is stored as one
    contiguous byte range, so the outermost ``{...}`` slice is the payload.
    """
    start = raw.find(b"{")
    end = raw.rfind(b"}")
    if start < 0 or end <= start:
        return None
    return raw[start:end + 1]


def read_profile_facts(path: Path) -> Tuple[Optional[dict], Optional[str]]:
    """Extract value-free profile facts; the payload is never recorded.

    Returns ``(facts, error)``; facts are ``None`` when nothing could be
    parsed, together with an error *category* (never a value).
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None, "profile_unreadable"
    if not raw:
        return None, "profile_empty"
    if len(raw) > PROFILE_MAX_BYTES:
        return None, "profile_too_large"
    data = _parse_json_object(raw)
    fmt = "json"
    if data is None:
        sliced = _embedded_json_slice(raw)
        data = _parse_json_object(sliced) if sliced is not None else None
        fmt = "embedded_json"
    if data is None:
        return None, "profile_payload_not_json"
    bundle_name = _extract_bundle_name(data)
    return {
        "format": fmt,
        "bundle_name": bundle_name,
        "bundle_name_present": bundle_name is not None,
    }, None


def _classify_profile_argument(
    raw: str, resolved_root: Path
) -> Tuple[Optional[str], Optional[str]]:
    """Apply the shared external-material rules to ``--profile`` material.

    Returns ``(error_category, suffix)``; the caller never records the value.
    """
    suffix = Path(raw).suffix.lower()
    if suffix not in PROFILE_SUFFIXES:
        return "wrong_extension", suffix or None
    return classify_material_path(raw, suffix, resolved_root), suffix


def validate_request(
    repo_root: Path,
    hap: Optional[str],
    profile: Optional[str] = None,
    bundle_name: Optional[str] = None,
) -> Tuple[Optional[VerifyRequest], List[dict]]:
    """Validate paths/names before anything is probed or spawned.

    The HAP must live inside the repository; ``--profile`` material must pass
    the external-material safety rules. No path value ever reaches a failure
    detail.
    """
    root = Path(repo_root).resolve()
    failures: List[dict] = []

    expected_bundle_name = DEFAULT_BUNDLE_NAME
    if bundle_name is not None:
        candidate = bundle_name.strip()
        if (
            not candidate
            or len(candidate) > BUNDLE_NAME_MAX_LENGTH
            or not BUNDLE_NAME_RE.match(candidate)
        ):
            failures.append({
                "code": "bundle_name_invalid",
                "detail": {"argument": "--bundle-name"},
            })
        else:
            expected_bundle_name = candidate

    profile_record = {
        "requested": profile is not None,
        "material": {
            "checked": False,
            "valid": None,
            "error": None,
            "allowed_suffixes": list(PROFILE_SUFFIXES),
        },
    }
    profile_path: Optional[Path] = None
    if profile is not None:
        profile_record["material"]["checked"] = True
        error, suffix = _classify_profile_argument(profile, root)
        profile_record["material"]["error"] = error
        profile_record["material"]["valid"] = error is None
        if error is not None:
            failures.append({
                "code": "profile_material_invalid",
                "detail": {"option": "--profile", "error": error, "suffix": suffix},
            })
        else:
            profile_path = Path(profile).expanduser().resolve()

    input_record: Optional[dict] = None
    input_path: Optional[Path] = None
    if hap is None:
        if not failures:
            failures.append({
                "code": "input_hap_missing",
                "detail": {"argument": "--hap"},
            })
        return None, failures
    input_path = _resolve_in_repo(root, hap)
    relpath: Optional[str] = None
    try:
        relpath = input_path.relative_to(root).as_posix()
    except ValueError:
        relpath = None
    if relpath is None:
        failures.append({"code": "input_hap_outside_repository"})
    elif not input_path.is_file():
        failures.append({"code": "input_hap_missing", "detail": {"relpath": relpath}})
    else:
        try:
            size_bytes = input_path.stat().st_size
            sha256 = _sha256_upper(input_path)
        except OSError:
            failures.append({
                "code": "input_hap_unreadable", "detail": {"relpath": relpath},
            })
        else:
            lower = input_path.name.lower()
            input_record = {
                "relpath": relpath,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "filename_has_signed": SIGNED_MARKER in lower,
                "filename_has_unsigned": UNSIGNED_MARKER in lower,
                "inside_repository": True,
                "filename_used_for_signedness": False,
            }
    if failures:
        return None, failures
    return VerifyRequest(
        input_path=input_path,
        input_record=input_record,
        profile_path=profile_path,
        profile_record=profile_record,
        expected_bundle_name=expected_bundle_name,
    ), []


def _profile_record(
    requested: bool,
    material: dict,
    sources: List[Tuple[str, dict]],
    source_errors: Dict[str, str],
    expected_bundle_name: str,
) -> dict:
    """Profile view: presence and match verdicts, never a material value.

    Every available source is reported by *name*; a bundle name is only ever
    compared against the expected one, never echoed.
    """
    names = [(source, facts["bundle_name"]) for source, facts in sources]
    available = bool(sources)
    mismatching = sorted(
        source for source, name in names
        if name is not None and name != expected_bundle_name
    )
    unnamed = sorted(source for source, name in names if name is None)
    matches: Optional[bool] = None
    bundle_name_present: Optional[bool] = None
    if available:
        matches = not mismatching and not unnamed
        bundle_name_present = not unnamed
    return {
        "requested": requested,
        "material": material,
        "facts": {
            "available": available,
            "sources": [source for source, _ in sources],
            "primary_source": sources[0][0] if sources else None,
            "format": sources[0][1]["format"] if sources else None,
            "bundle_name_present": bundle_name_present,
            "matches_expected": matches,
            "errors": dict(sorted(source_errors.items())),
        },
    }


def _signature_record(
    verifier_stage: str,
    verifier_exit_code: Optional[int],
    hap_material_present: bool,
) -> dict:
    """Signedness: verifier result plus (where applicable) profile facts.

    ``signed`` answers "does this artifact carry verifiable signature
    material?"; ``verified`` answers "did the verifier accept it?". Both derive
    from the two evidence items only - never from the file name.
    """
    record = {
        "signed": None,
        "verified": None,
        "basis": BASIS_NOT_RUN,
        "filename_used_for_signedness": False,
    }
    if verifier_stage == VERIFIER_STAGE_NOT_RUN:
        return record
    if verifier_stage == VERIFIER_STAGE_SPAWN_FAILED:
        record["basis"] = BASIS_NO_VERDICT
        return record
    if verifier_exit_code == 0:
        record["signed"] = True
        record["verified"] = True
        record["basis"] = BASIS_VERIFIER_OK
        return record
    record["signed"] = hap_material_present
    record["verified"] = False
    record["basis"] = (
        BASIS_REJECTED_WITH_MATERIAL if hap_material_present
        else BASIS_REJECTED_WITHOUT_MATERIAL
    )
    return record


def _exit_code_for(status: str) -> int:
    if status == STATUS_SIGNED_AND_VALID:
        return EXIT_VALID
    if status in UNAVAILABLE_STATUSES:
        return EXIT_UNAVAILABLE
    return EXIT_FAILURE


def run_verify_signature(
    repo_root: Path,
    *,
    hap: Optional[str] = None,
    profile: Optional[str] = None,
    bundle_name: Optional[str] = None,
    java: str = DEFAULT_JAVA,
    jar: Optional[str] = None,
    sdk_home: Optional[str] = None,
    runner: Optional[
        Callable[[Sequence[str], Path, Dict[str, str]], CommandResult]
    ] = None,
    toolchain_resolver: Optional[
        Callable[
            [str, Optional[str], Optional[str]],
            Tuple[Optional[Toolchain], List[dict]],
        ]
    ] = None,
    profile_reader: Optional[ProfileReader] = None,
) -> Tuple[dict, int]:
    """Verify one signed HAP; return (deterministic value-free result, exit).

    ``runner``, ``toolchain_resolver`` and ``profile_reader`` default to the
    real implementations; tests inject fakes so no java/hap-sign-tool.jar is
    ever executed and no device is involved. Nothing is spawned unless the
    request and the toolchain are valid.
    """
    root = Path(repo_root)
    failures: List[dict] = []
    warnings: List[dict] = []
    steps: List[dict] = []
    input_record: Optional[dict] = None
    toolchain: Optional[Toolchain] = None
    expected_bundle_name = DEFAULT_BUNDLE_NAME
    verifier_stage = VERIFIER_STAGE_NOT_RUN
    verifier_exit_code: Optional[int] = None
    sources: List[Tuple[str, dict]] = []
    source_errors: Dict[str, str] = {}
    hap_facts_available = False
    spawn_failed = False
    toolchain_failures: List[dict] = []
    material_record: dict = {
        "checked": False,
        "valid": None,
        "error": None,
        "allowed_suffixes": list(PROFILE_SUFFIXES),
    }

    # 1) Request: paths, suffixes, expected bundle name.
    request, request_failures = validate_request(root, hap, profile, bundle_name)
    if request_failures:
        failures += request_failures
        if profile is not None:
            material_error = "not_checked"
            material_valid: Optional[bool] = None
            for item in request_failures:
                if item["code"] == "profile_material_invalid":
                    material_error = item["detail"]["error"]
                    material_valid = False
            material_record = {
                "checked": True,
                "valid": material_valid,
                "error": material_error,
                "allowed_suffixes": list(PROFILE_SUFFIXES),
            }
    else:
        input_record = request.input_record
        expected_bundle_name = request.expected_bundle_name
        material_record = dict(request.profile_record["material"])

    # 2) Toolchain: never probed while the request is already invalid.
    if not failures and request is not None:
        resolve = toolchain_resolver or resolve_toolchain
        toolchain, toolchain_failures = resolve(java, jar, sdk_home)
        failures += toolchain_failures

    # 3) Execute the documented command. The dump paths live in a fresh
    #    temporary directory outside the repository and are removed below.
    if not failures and request is not None and toolchain is not None:
        run = runner or subprocess_runner
        reader = profile_reader or read_profile_facts
        workdir = Path(tempfile.mkdtemp(prefix=TEMP_DIR_PREFIX))
        cert_chain_out = workdir / VERIFY_DUMP_NAMES[0]
        profile_out = workdir / VERIFY_DUMP_NAMES[1]
        try:
            argv = build_verify_argv(
                toolchain, request.input_path, cert_chain_out, profile_out
            )
            # No credential is added; the material/credential variables are
            # stripped; stdout/stderr are never inspected or recorded.
            env = child_env()
            try:
                outcome = run(argv, root, env)
            except OSError:
                spawn_failed = True
                verifier_stage = VERIFIER_STAGE_SPAWN_FAILED
            else:
                verifier_stage = VERIFIER_STAGE_COMPLETED
                verifier_exit_code = int(outcome.returncode)
                steps.append({"name": STEP_NAME, "exit_code": verifier_exit_code})
                if profile_out.is_file():
                    hap_facts, hap_error = reader(profile_out)
                    if hap_facts is not None:
                        sources.append((SOURCE_HAP_PROFILE, hap_facts))
                        hap_facts_available = True
                    else:
                        source_errors[SOURCE_HAP_PROFILE] = hap_error or "unknown"
                else:
                    source_errors[SOURCE_HAP_PROFILE] = PROFILE_NOT_EXTRACTED
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    # 4) Profile facts: every available source is checked - the artifact's own
    #    profile and, when given, the explicit --profile material.
    profile_requested = profile is not None
    if request is not None and request.profile_path is not None:
        reader = profile_reader or read_profile_facts
        option_facts, option_error = reader(request.profile_path)
        if option_facts is not None:
            sources.append((SOURCE_PROFILE_OPTION, option_facts))
        else:
            source_errors[SOURCE_PROFILE_OPTION] = option_error or "unknown"

    # 5) Verdict: documented precedence, fail closed.
    verifier_ok = verifier_exit_code == 0
    verifier_rejected = verifier_exit_code is not None and verifier_exit_code != 0
    facts_sources = [source for source, _ in sources]
    mismatching_sources = sorted(
        source for source, facts in sources
        if facts["bundle_name"] is not None
        and facts["bundle_name"] != expected_bundle_name
    )
    unnamed_sources = sorted(
        source for source, facts in sources if facts["bundle_name"] is None
    )
    if failures:
        status = (
            STATUS_TOOLCHAIN_UNAVAILABLE
            if toolchain_failures
            else STATUS_REQUEST_INVALID
        )
    elif spawn_failed:
        status = STATUS_TOOL_FAILURE
        failures.append({
            "code": "verify_tool_spawn_failed", "detail": {"error": "spawn_failed"},
        })
    elif profile_requested and SOURCE_PROFILE_OPTION not in facts_sources:
        status = STATUS_REQUEST_INVALID
        failures.append({
            "code": "profile_facts_unavailable",
            "detail": {
                "source": SOURCE_PROFILE_OPTION,
                "error": source_errors.get(SOURCE_PROFILE_OPTION),
            },
        })
    elif mismatching_sources:
        status = STATUS_PROFILE_BUNDLE_MISMATCH
        failures.append({
            "code": "profile_bundle_mismatch",
            "detail": {
                "expected_bundle_name": expected_bundle_name,
                "mismatching_sources": mismatching_sources,
            },
        })
    elif unnamed_sources:
        status = STATUS_PROFILE_BUNDLE_MISMATCH
        failures.append({
            "code": "profile_bundle_name_missing",
            "detail": {"sources": unnamed_sources},
        })
    elif verifier_ok:
        status = STATUS_SIGNED_AND_VALID
    elif verifier_rejected:
        status = STATUS_INVALID_SIGNATURE if hap_facts_available else STATUS_UNSIGNED
        failures.append({
            "code": (
                "hap_signature_invalid" if hap_facts_available else "hap_unsigned"
            ),
            "detail": {"exit_code": verifier_exit_code},
        })
    else:  # pragma: no cover - defensive: no verifier result at all
        status = STATUS_TOOL_FAILURE
        failures.append({"code": "verify_tool_no_verdict"})

    hap_facts_error = source_errors.get(SOURCE_HAP_PROFILE)
    if hap_facts_error not in (None, PROFILE_NOT_EXTRACTED):
        warnings.append({
            "code": "profile_facts_unavailable",
            "detail": {"source": SOURCE_HAP_PROFILE, "error": hap_facts_error},
        })
    elif hap_facts_error == PROFILE_NOT_EXTRACTED and verifier_ok:
        warnings.append({
            "code": PROFILE_NOT_EXTRACTED,
            "detail": {"subcommand": VERIFY_SUBCOMMAND},
        })

    failures.sort(key=lambda item: json.dumps(item, sort_keys=True))
    warnings.sort(key=lambda item: json.dumps(item, sort_keys=True))
    profile_record = _profile_record(
        profile_requested, material_record, sources, source_errors,
        expected_bundle_name,
    )

    exit_code = _exit_code_for(status)
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "mode": MODE,
        "status": status,
        "exit_code": exit_code,
        "signing_performed": False,
        "device_access": False,
        "expected_bundle_name": expected_bundle_name,
        "input": input_record,
        "profile": profile_record,
        "signature": _signature_record(
            verifier_stage, verifier_exit_code, hap_facts_available
        ),
        "command": command_shape(Path(java).name),
        "verifier": {
            "stage": verifier_stage,
            "ran": verifier_stage == VERIFIER_STAGE_COMPLETED,
            "exit_code": verifier_exit_code,
        },
        "toolchain": toolchain_record(toolchain),
        "steps": steps,
        "warnings": warnings,
        "failures": failures,
    }
    return result, exit_code


def render_json(result: dict) -> str:
    """Deterministic serialization (sorted keys, ASCII-safe)."""
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True)


def render_summary(result: dict) -> str:
    """One-line human summary: names, categories and repo-relative paths."""
    signature = result["signature"]
    facts = result["profile"]["facts"]
    parts = [
        f"{TOOL_NAME}: status={result['status']}",
        f"exit={result['exit_code']}",
        f"signed={str(signature['signed']).lower()}",
        f"verified={str(signature['verified']).lower()}",
    ]
    input_record = result.get("input")
    if input_record:
        parts.append(f"input={input_record['relpath']}")
    sources = facts["sources"]
    parts.append(f"profile_facts={','.join(sources) if sources else 'none'}")
    parts.append(f"bundle_name_matches={str(facts['matches_expected']).lower()}")
    codes = ",".join(item["code"] for item in result["failures"])
    if codes:
        parts.append(f"failures={codes}")
    return " ".join(parts)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="verify_signature",
        description="HarmonyOS HAP signature verification wrapper (fail-closed).",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=DEFAULT_REPO_ROOT,
        help="Repository root (default: this checkout).",
    )
    parser.add_argument(
        "--hap", default=None,
        help="Signed HAP to verify; relative paths resolve against the repo root.",
    )
    parser.add_argument(
        "--profile", default=None,
        help="Optional provisioning profile carrying the expected bundle name "
             "(must live outside the repository; .p7b or .json).",
    )
    parser.add_argument(
        "--bundle-name", default=None,
        help=f"Expected bundle name (default: {DEFAULT_BUNDLE_NAME}).",
    )
    parser.add_argument(
        "--java", default=DEFAULT_JAVA,
        help="java executable name or path (default: java from PATH).",
    )
    parser.add_argument(
        "--jar", default=None,
        help="hap-sign-tool.jar path (default: discovered under "
             f"${SDK_HOME_ENV_VAR}).",
    )
    parser.add_argument(
        "--sdk-home", default=None,
        help=f"SDK home to search for the jar (default: ${SDK_HOME_ENV_VAR}).",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress the one-line stderr summary.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    result, exit_code = run_verify_signature(
        repo_root=args.repo_root.resolve(),
        hap=args.hap,
        profile=args.profile,
        bundle_name=args.bundle_name,
        java=args.java,
        jar=args.jar,
        sdk_home=args.sdk_home,
    )
    print(render_json(result))
    if not args.quiet:
        print(render_summary(result), file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
