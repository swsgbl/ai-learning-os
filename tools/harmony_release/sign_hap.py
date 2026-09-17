"""HarmonyOS HAP signing wrapper (M13-16a slice 3).

Signs one *unsigned* release HAP with the DevEco ``hap-sign-tool.jar``
``sign-app`` command and records the honest facts of that run as
deterministic JSON.

Documented child command (values are passed to the child only)::

    java -jar hap-sign-tool.jar sign-app \
      -keyAlias <alias> -signAlg SHA256withECDSA -mode localSign \
      -appCertFile <cert.cer> -profileFile <profile.p7b> \
      -inFile <...-unsigned.hap> -keystoreFile <keystore.p12> \
      -outFile <...-signed.hap> -keyPwd <key password> -keystorePwd <keystore password>

Value-free external contract (names only; *values* are never read into the
JSON, never printed, never logged, and never re-used in an error message):

===============================  ==========  ==================================
environment variable             extension   meaning
===============================  ==========  ==================================
AIOS_HARMONY_CERT_PATH           .cer        release app certificate
AIOS_HARMONY_PROFILE_PATH        .p7b        release provisioning profile
AIOS_HARMONY_KEYSTORE_PATH       .p12        release keystore
AIOS_HARMONY_KEY_ALIAS           -           key alias inside the keystore
AIOS_HARMONY_KEYSTORE_PASSWORD   -           keystore password
AIOS_HARMONY_KEY_PASSWORD        -           key password
===============================  ==========  ==================================

The three path materials must live *outside* the repository and keep the
documented extension; they are validated by the same
``preflight.check_external_materials`` gate used by the release build (so the
two tools cannot drift). File contents are never opened or parsed.

Safety contract:
- ``runner`` (process execution) and ``toolchain_resolver`` (java/jar
  discovery) are injectable, so unit tests never invoke java/hap-sign-tool and
  never create a real signed HAP.
- Nothing derived from a secret, an environment value, an absolute path, a
  raw argv or a child's stdout/stderr is ever serialized: the JSON carries
  repo-relative paths, sizes, SHA-256, option *names*, and variable *names*
  with present/valid/error categories only. Child logs are never read at all.
- Fail closed on: missing/unnamed input HAP, an input whose filename does not
  contain ``unsigned``, an input or output that resolves outside the
  repository, an output that would overwrite an existing file without
  ``allow_overwrite``, an output equal to the input, a missing output
  directory, a missing/unreadable jar or java, a spawn failure, a nonzero tool
  exit, or a missing/unreadable produced file.
- ``signed`` is always ``false``: this wrapper does **not** cryptographically
  verify signedness. ``claimed_signed`` is true only when the tool exited 0
  and the output file was written and read back; that is a *claim* backed by
  tool success (``claimed_signed_basis``), not a verification
  (``signedness_verified`` stays false). Verifying the signature is a
  separate, later tool.

Blocking precedence (documented, not hidden): request validation (exit 1) ->
external materials (exit 2 when absent, exit 1 when present but invalid) ->
toolchain discovery (exit 1) -> child execution. A run blocked by missing
materials never probes the toolchain and never spawns anything.

Exit codes: 0 = ok, 1 = failure (fail-closed), 2 = blocked because external
materials/credentials are absent. (argparse usage errors still exit 2, which
is the interpreter's own convention - this tool never reports its own
"blocked" status with a usage error.)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

try:  # package import (pytest, python -m)
    from tools.harmony_release.preflight import (
        MATERIAL_ENV_VARS,
        check_external_materials,
    )
except ImportError:  # direct script: python tools/harmony_release/sign_hap.py
    from preflight import MATERIAL_ENV_VARS, check_external_materials

SCHEMA_VERSION = 1
TOOL_NAME = "harmony_sign_hap"
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_JAVA = "java"
DEFAULT_JAR_NAME = "hap-sign-tool.jar"
SDK_HOME_ENV_VAR = "DEVECO_SDK_HOME"

# Deterministic jar candidates, relative to DEVECO_SDK_HOME (the API 12+ SDK
# layout first). The DEVECO_SDK_HOME *value* is never recorded.
JAR_CANDIDATES = (
    "default/openharmony/toolchains/lib/hap-sign-tool.jar",
    "default/hms/toolchains/lib/hap-sign-tool.jar",
    "openharmony/toolchains/lib/hap-sign-tool.jar",
    "hms/toolchains/lib/hap-sign-tool.jar",
    "toolchains/lib/hap-sign-tool.jar",
)

# Credential variables: names are part of the contract, values never are.
CREDENTIAL_ENV_VARS = (
    "AIOS_HARMONY_KEY_ALIAS",
    "AIOS_HARMONY_KEYSTORE_PASSWORD",
    "AIOS_HARMONY_KEY_PASSWORD",
)

# Documented sign-app option set, in the documented order (names only).
SIGN_SUBCOMMAND = "sign-app"
SIGN_MODE = "localSign"
SIGN_ALGORITHM = "SHA256withECDSA"
SIGN_OPTION_NAMES = (
    "-keyAlias",
    "-signAlg",
    "-mode",
    "-appCertFile",
    "-profileFile",
    "-inFile",
    "-keystoreFile",
    "-outFile",
    "-keyPwd",
    "-keystorePwd",
)
SECRET_OPTION_NAMES = ("-keyAlias", "-keyPwd", "-keystorePwd")

UNSIGNED_MARKER = "unsigned"
SIGNED_MARKER = "signed"
STEP_NAME = "sign_hap"
CLAIM_BASIS = "hap_sign_tool_exit_0_and_output_written"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_BLOCKED_MATERIALS = 2

STATUS_OK = "ok"
STATUS_FAILURE = "failure"
STATUS_BLOCKED = "blocked_by_external_materials"


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one injected command. Never serialized into the JSON.

    Same shape as ``release_build.CommandResult`` so runners are
    interchangeable; ``stdout``/``stderr`` are deliberately never read.
    """

    argv: Tuple[str, ...]
    cwd: Path
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class Toolchain:
    """Resolved signing toolchain. Paths are never serialized."""

    java: Path
    jar: Path
    java_source: str  # "explicit" | "path_lookup"
    jar_source: str  # "explicit" | "deveco_sdk_home"
    jar_candidate: Optional[str]  # relative candidate inside the SDK home


@dataclass(frozen=True)
class SignRequest:
    """Validated request paths (absolute) plus their value-free records."""

    input_path: Path
    output_path: Path
    input_record: dict
    output_record: dict


CommandRunner = Callable[[Sequence[str], Path, Dict[str, str]], CommandResult]
ToolchainResolver = Callable[
    [str, Optional[str], Optional[str]],
    Tuple[Optional[Toolchain], List[dict]],
]


def subprocess_runner(
    argv: Sequence[str], cwd: Path, env: Dict[str, str]
) -> CommandResult:
    """Real runner: spawn the command, capture output, never raise on exit."""
    completed = subprocess.run(
        list(argv),
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return CommandResult(
        argv=tuple(argv),
        cwd=Path(cwd),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def _sha256_upper(path: Path) -> str:
    """Streaming SHA-256, uppercase hex (same format as preflight)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _resolve_java(program: str) -> Tuple[Optional[Path], str]:
    """Resolve the java executable without ever executing it."""
    candidate = Path(program)
    if candidate.parent != Path("."):
        return (candidate if candidate.is_file() else None), "explicit"
    found = shutil.which(program)
    return (Path(found) if found else None), "path_lookup"


def _resolve_jar(
    jar: Optional[str], sdk_home: Optional[str]
) -> Tuple[Optional[Path], str, Optional[str], List[dict]]:
    """Resolve hap-sign-tool.jar: explicit path, else DEVECO_SDK_HOME layout."""
    if jar is not None:
        candidate = Path(jar)
        if candidate.is_file():
            return candidate, "explicit", None, []
        return None, "explicit", None, [{
            "code": "sign_tool_jar_not_found",
            "detail": {"source": "explicit", "jar_name": candidate.name},
        }]
    root_raw = sdk_home if sdk_home is not None else os.environ.get(SDK_HOME_ENV_VAR)
    detail = {
        "source": "deveco_sdk_home",
        "env_var": SDK_HOME_ENV_VAR,
        "jar_name": DEFAULT_JAR_NAME,
        "searched": list(JAR_CANDIDATES),
    }
    if not root_raw:
        return None, "deveco_sdk_home", None, [{
            "code": "sign_tool_jar_not_found",
            "detail": {**detail, "error": "sdk_home_not_set"},
        }]
    root = Path(root_raw).expanduser()
    for rel in JAR_CANDIDATES:
        candidate = root / rel
        if candidate.is_file():
            return candidate, "deveco_sdk_home", rel, []
    return None, "deveco_sdk_home", None, [{
        "code": "sign_tool_jar_not_found",
        "detail": {**detail, "error": "no_candidate_found"},
    }]


def resolve_toolchain(
    java_program: str = DEFAULT_JAVA,
    jar: Optional[str] = None,
    sdk_home: Optional[str] = None,
) -> Tuple[Optional[Toolchain], List[dict]]:
    """Resolve java + hap-sign-tool.jar; fail closed when either is absent.

    Injectable (``toolchain_resolver``) so tests never touch a real SDK.
    """
    failures: List[dict] = []
    java_path, java_source = _resolve_java(java_program)
    if java_path is None:
        failures.append({
            "code": "java_not_found",
            "detail": {"program": Path(java_program).name},
        })
    jar_path, jar_source, jar_candidate, jar_failures = _resolve_jar(jar, sdk_home)
    failures += jar_failures
    if failures:
        return None, failures
    return Toolchain(
        java=java_path,
        jar=jar_path,
        java_source=java_source,
        jar_source=jar_source,
        jar_candidate=jar_candidate,
    ), []


def _resolve_in_repo(repo_root: Path, raw: str) -> Path:
    """Resolve a path against the repository root (absolute paths allowed).

    Relative paths are interpreted relative to ``repo_root`` (not the caller's
    cwd) so the same invocation means the same thing anywhere.
    """
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = Path(repo_root) / candidate
    try:
        return candidate.resolve()
    except OSError:
        return candidate


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _inspect_input(path: Path, root: Path) -> Tuple[Optional[dict], List[dict]]:
    rel = path.relative_to(root).as_posix()
    try:
        size_bytes = path.stat().st_size
        sha256 = _sha256_upper(path)
    except OSError:
        return None, [{"code": "input_hap_unreadable", "detail": {"relpath": rel}}]
    return {
        "relpath": rel,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "filename_has_unsigned": UNSIGNED_MARKER in path.name.lower(),
        "inside_repository": True,
    }, []


def _derived_output_name(name: str) -> str:
    """``entry-default-unsigned.hap`` -> ``entry-default-signed.hap``."""
    return re.sub(
        UNSIGNED_MARKER, SIGNED_MARKER, name, count=1, flags=re.IGNORECASE
    )


def validate_request(
    repo_root: Path,
    hap: Optional[str],
    out: Optional[str],
    allow_overwrite: bool,
) -> Tuple[Optional[SignRequest], List[dict]]:
    """Validate input/output paths, names and overwrite safety (fail closed)."""
    root = Path(repo_root).resolve()

    if not hap:
        return None, [{"code": "input_hap_missing", "detail": {"argument": "--hap"}}]

    input_path = _resolve_in_repo(root, hap)
    if not _inside(input_path, root):
        return None, [{"code": "input_hap_outside_repository"}]
    if not input_path.is_file():
        return None, [{"code": "input_hap_missing", "detail": {"argument": "--hap"}}]
    if UNSIGNED_MARKER not in input_path.name.lower():
        return None, [{"code": "input_hap_not_unsigned"}]

    path_explicit = bool(out)
    if path_explicit:
        output_path = _resolve_in_repo(root, out)
        if not _inside(output_path, root):
            return None, [{"code": "output_outside_repository"}]
    else:
        output_path = input_path.with_name(_derived_output_name(input_path.name))

    if output_path == input_path:
        return None, [{"code": "output_equals_input"}]
    if not output_path.parent.is_dir():
        rel_parent = output_path.parent.relative_to(root).as_posix()
        return None, [{
            "code": "output_directory_missing",
            "detail": {"relpath": rel_parent},
        }]
    output_rel = output_path.relative_to(root).as_posix()
    existed_before = output_path.exists()
    if existed_before and not allow_overwrite:
        return None, [{"code": "output_exists", "detail": {"relpath": output_rel}}]

    input_record, input_failures = _inspect_input(input_path, root)
    if input_failures:
        return None, input_failures
    output_record = {
        "relpath": output_rel,
        "path_explicit": path_explicit,
        "existed_before": existed_before,
        "filename_has_signed": SIGNED_MARKER in output_path.name.lower(),
        "inside_repository": True,
    }
    return SignRequest(
        input_path=input_path,
        output_path=output_path,
        input_record=input_record,
        output_record=output_record,
    ), []


def collect_credentials() -> Tuple[dict, Dict[str, str]]:
    """Read the credential variables: value-free record + raw values.

    The returned value mapping is handed to the child process only; it is
    never serialized, printed, or embedded in an error.
    """
    record: dict = {"variables": {}, "all_present": True}
    values: Dict[str, str] = {}
    for name in CREDENTIAL_ENV_VARS:
        raw = os.environ.get(name)
        entry = {"present": False, "valid": None, "error": "not_set"}
        if raw == "":
            entry["error"] = "empty_value"
        elif raw is not None:
            entry["present"] = True
            entry["valid"] = True
            entry["error"] = None
            values[name] = raw
        record["variables"][name] = entry
    record["all_present"] = all(
        entry["present"] for entry in record["variables"].values()
    )
    return record, values


def material_values() -> Dict[str, Path]:
    """Material paths as configured (never serialized)."""
    values: Dict[str, Path] = {}
    for name in MATERIAL_ENV_VARS:
        raw = os.environ.get(name)
        if raw:
            values[name] = Path(raw).expanduser()
    return values


def build_sign_argv(
    toolchain: Toolchain,
    materials: Dict[str, Path],
    input_path: Path,
    output_path: Path,
    credentials: Dict[str, str],
) -> List[str]:
    """The documented sign-app argv. Contains secrets: never serialize it."""
    return [
        str(toolchain.java),
        "-jar",
        str(toolchain.jar),
        SIGN_SUBCOMMAND,
        "-keyAlias",
        credentials["AIOS_HARMONY_KEY_ALIAS"],
        "-signAlg",
        SIGN_ALGORITHM,
        "-mode",
        SIGN_MODE,
        "-appCertFile",
        str(materials["AIOS_HARMONY_CERT_PATH"]),
        "-profileFile",
        str(materials["AIOS_HARMONY_PROFILE_PATH"]),
        "-inFile",
        str(input_path),
        "-keystoreFile",
        str(materials["AIOS_HARMONY_KEYSTORE_PATH"]),
        "-outFile",
        str(output_path),
        "-keyPwd",
        credentials["AIOS_HARMONY_KEY_PASSWORD"],
        "-keystorePwd",
        credentials["AIOS_HARMONY_KEYSTORE_PASSWORD"],
    ]


def command_shape(java_name: str) -> dict:
    """Command shape: option/variable *names* and constants only.

    ``java_name`` is a basename so an absolute java path can never leak.
    """
    return {
        "program": java_name,
        "jar_name": DEFAULT_JAR_NAME,
        "subcommand": SIGN_SUBCOMMAND,
        "cwd": ".",
        "mode": SIGN_MODE,
        "sign_alg": SIGN_ALGORITHM,
        "option_names": list(SIGN_OPTION_NAMES),
        "secret_option_names": list(SECRET_OPTION_NAMES),
        "env_keys": [],
    }


def toolchain_record(toolchain: Optional[Toolchain]) -> Optional[dict]:
    """Toolchain facts by name/candidate only - never a path."""
    if toolchain is None:
        return None
    return {
        "java_name": toolchain.java.name,
        "java_source": toolchain.java_source,
        "jar_name": toolchain.jar.name,
        "jar_source": toolchain.jar_source,
        "jar_candidate": toolchain.jar_candidate,
    }


def _inspect_artifact(
    path: Path, root: Path, input_record: dict
) -> Tuple[Optional[dict], List[dict]]:
    """Record the produced file; fail closed when it is absent/unreadable."""
    try:
        rel = path.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return None, [{"code": "output_outside_repository"}]
    if not path.is_file():
        return None, [{"code": "output_missing", "detail": {"relpath": rel}}]
    try:
        size_bytes = path.stat().st_size
        sha256 = _sha256_upper(path)
    except OSError:
        return None, [{"code": "output_unreadable", "detail": {"relpath": rel}}]
    return {
        "relpath": rel,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "filename_has_signed": SIGNED_MARKER in path.name.lower(),
        "bytes_changed_from_input": sha256 != input_record["sha256"],
    }, []


def run_sign_hap(
    repo_root: Path,
    *,
    hap: Optional[str] = None,
    out: Optional[str] = None,
    allow_overwrite: bool = False,
    java: str = DEFAULT_JAVA,
    jar: Optional[str] = None,
    sdk_home: Optional[str] = None,
    runner: Optional[CommandRunner] = None,
    toolchain_resolver: Optional[ToolchainResolver] = None,
) -> Tuple[dict, int]:
    """Sign one unsigned HAP; return (deterministic result, exit code).

    ``runner`` defaults to the real subprocess runner and
    ``toolchain_resolver`` to :func:`resolve_toolchain`; tests inject both.
    Nothing is spawned unless the request, the materials and the toolchain are
    all valid.
    """
    root = Path(repo_root)
    failures: List[dict] = []
    steps: List[dict] = []
    input_record: Optional[dict] = None
    output_record: Optional[dict] = None
    artifact: Optional[dict] = None
    toolchain: Optional[Toolchain] = None
    external_materials: Optional[dict] = None
    credentials: Optional[dict] = None
    status: Optional[str] = None
    credential_values: Dict[str, str] = {}
    claimed_signed = False
    claimed_signed_basis: Optional[str] = None

    # 1) Request: paths, names, overwrite safety.
    request, request_failures = validate_request(root, hap, out, allow_overwrite)
    if request_failures:
        failures += request_failures
    else:
        input_record = request.input_record
        output_record = request.output_record

    # 2) External materials: absent blocks (exit 2), invalid fails (exit 1).
    if not failures:
        external_materials, material_failures = check_external_materials(root)
        credentials, credential_values = collect_credentials()
        failures += material_failures
        if not failures:
            materials_absent = not (
                external_materials["all_present"] and credentials["all_present"]
            )
            if materials_absent:
                status = STATUS_BLOCKED

    # 3) Toolchain: never probed while blocked or already failing.
    if not failures and status is None:
        resolve = toolchain_resolver or resolve_toolchain
        toolchain, toolchain_failures = resolve(java, jar, sdk_home)
        failures += toolchain_failures

    # 4) Execute the documented command (secrets go to the child only).
    if not failures and status is None:
        run = runner or subprocess_runner
        argv = build_sign_argv(
            toolchain,
            material_values(),
            request.input_path,
            request.output_path,
            credential_values,
        )
        env = dict(os.environ)
        try:
            outcome = run(argv, root, env)
        except OSError:
            failures.append({
                "code": "sign_tool_spawn_failed",
                "detail": {"error": "spawn_failed"},
            })
        else:
            # stdout/stderr are deliberately never inspected or recorded.
            steps.append({"name": STEP_NAME, "exit_code": int(outcome.returncode)})
            if outcome.returncode != 0:
                failures.append({
                    "code": "sign_tool_failed",
                    "detail": {"exit_code": int(outcome.returncode)},
                })
            else:
                artifact, artifact_failures = _inspect_artifact(
                    request.output_path, Path(root).resolve(), input_record
                )
                failures += artifact_failures
                if not artifact_failures:
                    claimed_signed = True
                    claimed_signed_basis = CLAIM_BASIS

    if failures:
        status = STATUS_FAILURE
    elif status is None:
        status = STATUS_OK

    failures.sort(key=lambda item: json.dumps(item, sort_keys=True))
    exit_code = EXIT_FAILURE if failures else EXIT_OK
    if exit_code == EXIT_OK and status == STATUS_BLOCKED:
        exit_code = EXIT_BLOCKED_MATERIALS

    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": status,
        "exit_code": exit_code,
        "allow_overwrite": allow_overwrite,
        "input": input_record,
        "output": output_record,
        "artifact": artifact,
        "command": command_shape(Path(java).name),
        "toolchain": toolchain_record(toolchain),
        "external_materials": external_materials,
        "credentials": credentials,
        "steps": steps,
        "signed": False,
        "claimed_signed": claimed_signed,
        "claimed_signed_basis": claimed_signed_basis,
        "signedness_verified": False,
        "failures": failures,
    }
    return result, exit_code


def render_json(result: dict) -> str:
    """Deterministic serialization (sorted keys, ASCII-safe)."""
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True)


def render_summary(result: dict) -> str:
    """One-line human summary: names, repo-relative paths and hashes only."""
    parts = [
        f"{TOOL_NAME}: status={result['status']}",
        f"exit={result['exit_code']}",
        f"signed={str(result['signed']).lower()}",
        f"claimed_signed={str(result['claimed_signed']).lower()}",
    ]
    input_record = result.get("input")
    if input_record:
        parts.append(f"input={input_record['relpath']}")
    artifact = result.get("artifact")
    if artifact:
        parts.append(f"output={artifact['relpath']}")
        parts.append(f"size_bytes={artifact['size_bytes']}")
        parts.append(f"sha256={artifact['sha256']}")
    codes = ",".join(item["code"] for item in result["failures"])
    if codes:
        parts.append(f"failures={codes}")
    return " ".join(parts)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sign_hap",
        description="HarmonyOS HAP signing wrapper (fail-closed).",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=DEFAULT_REPO_ROOT,
        help="Repository root (default: this checkout).",
    )
    parser.add_argument(
        "--hap", default=None,
        help="Unsigned HAP to sign; relative paths resolve against the repo root.",
    )
    parser.add_argument(
        "--out", default=None,
        help="Output HAP (default: beside the input, 'unsigned' -> 'signed').",
    )
    parser.add_argument(
        "--java", default=DEFAULT_JAVA,
        help="java executable name or path (default: java from PATH).",
    )
    parser.add_argument(
        "--jar", default=None,
        help="hap-sign-tool.jar path (default: discovered under "
        "$DEVECO_SDK_HOME).",
    )
    parser.add_argument(
        "--sdk-home", default=None,
        help="SDK home to search for the jar (default: $DEVECO_SDK_HOME).",
    )
    parser.add_argument(
        "--allow-overwrite", action="store_true",
        help="Permit writing over an existing output HAP.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress the one-line stderr summary.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    result, exit_code = run_sign_hap(
        repo_root=args.repo_root.resolve(),
        hap=args.hap,
        out=args.out,
        allow_overwrite=args.allow_overwrite,
        java=args.java,
        jar=args.jar,
        sdk_home=args.sdk_home,
    )
    sys.stdout.write(render_json(result) + "\n")
    if not args.quiet:
        sys.stderr.write(render_summary(result) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
