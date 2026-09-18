"""HarmonyOS real-device read-only preflight gate (M13-17).

Plans - and, only with an exact confirmation flag, executes - a strictly
read-only probe of **one explicit real device target**, gated behind an
existing signature verification report produced by
``tools/harmony_release/verify_signature.py``.

Two modes:
- ``plan``: a pure plan. No tool is resolved, no target existence check is
  made, no process is spawned, **no file is read** - without an explicit
  ``--bundle`` the bundle name is never resolved (``bundle_resolver`` would
  read ``AppScope/app.json5``) and the bundle-query step stays ``not_run``
  with the deterministic reason ``bundle_not_supplied`` - and every step is
  recorded ``not_run``.
- ``check``: read-only execution. Requires the exact flag
  ``--confirm-read-only``, one explicit non-loopback target, and a passing
  signature report (with a matching ``.sha256`` sidecar) **before** any hdc
  probe runs.

Documented child commands (values go to the child only; the JSON records
command *shapes* - program/subcommand/operand names - never an argv, never
the target string, never an absolute path, never any command output)::

    hdc -t <TARGET> shell param get const.ohos.apiversion
    hdc -t <TARGET> shell param get const.product.model
    hdc -t <TARGET> shell bm dump -n <BUNDLE>

Nothing else is ever built: there is no code path that could provision,
launch, stop, remove or wipe anything on the device, and no log-reading
command exists in this module at all.

Signature gate
==============
Before the first hdc probe, the tool consumes an **existing** report JSON
from ``verify_signature.py``. The sidecar ``<report>.sha256`` must exist and
match the report bytes; the report must be strict JSON (NaN/Infinity
rejected), name ``harmony_release_verify_signature``, carry
``status == "signed_and_valid"``, ``exit_code == 0``,
``signature.signed == true`` and ``signature.verified == true`` (a real
verifier verdict - never a filename inference), and carry a concrete HAP
``input.sha256`` (64 hex chars) and ``input.size_bytes`` (> 0). Any
deviation blocks the run before the toolchain is even probed.

Safety contract
- One explicit target only; ``all``/``any``/``*``/``list``/``devices`` and
  multi-token targets are rejected. In ``check`` (true-device) mode,
  loopback/localhost targets are rejected with **no bypass** - this gate
  exists to protect real hardware.
- Read-only only: the probe table is a fixed allowlist; a test pins the
  module source against every mutating verb.
- Never serialized: raw argv, absolute paths, the raw target, secrets,
  command stdout/stderr, unrelated device identifiers, and an unvalidated
  report ``input.relpath`` (absolute/drive/UNC/empty/``..`` forms are
  rejected at load time and never echoed). The target is
  recorded as a 12-hex SHA-256 prefix plus a loopback/other kind only
  (device_smoke conventions). Every release material/credential variable is
  stripped from the child environment; only the names are recorded.
- Plan mode is zero-file-read: without an explicit ``--bundle`` no bundle
  resolution happens at all (``AppScope/app.json5`` is never opened) and
  the bundle-query step stays ``not_run`` with reason
  ``bundle_not_supplied``. ``check`` mode keeps the
  ``AppScope/app.json5`` fallback.
- Fail closed (exit 2, ``blocked``) on malformed requests, forbidden or
  loopback targets, a missing confirmation flag, and every signature-gate
  deviation. Fail closed (exit 1, ``failure``) on a missing hdc tool,
  spawn failure, timeout, nonzero probe exit, or invalid probe output.

Honest boundaries: this tool verifies **nothing** about the device beyond
the three probes above; no AGC access, no credentials, no real signing, no
device logs, and no app-state claims. Exit codes are the only lifecycle
evidence. The test suite is mock-only: no hdc process is ever spawned and
no device is ever contacted by the tests.

Exit codes: 0 = ok or planned, 1 = failure (fail-closed), 2 = blocked
(fail-closed; nothing executed). argparse usage errors also exit 2.
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:  # package import (pytest, python -m)
    from tools.harmony_release.device_smoke import (
        TARGET_HASH_LENGTH,
        CommandResult,
        resolve_bundle,
    )
    from tools.harmony_release.preflight import MATERIAL_ENV_VARS
    from tools.harmony_release.sign_hap import CREDENTIAL_ENV_VARS
except ImportError:  # direct script
    from device_smoke import (  # type: ignore[no-redef]
        TARGET_HASH_LENGTH,
        CommandResult,
        resolve_bundle,
    )
    from preflight import MATERIAL_ENV_VARS  # type: ignore[no-redef]
    from sign_hap import CREDENTIAL_ENV_VARS  # type: ignore[no-redef]

SCHEMA_VERSION = 1
TOOL_NAME = "harmony_device_preflight"
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HDC = "hdc"
DEFAULT_COMMAND_TIMEOUT_SECONDS = 30.0

MODE_PLAN = "plan"
MODE_CHECK = "check"
MODES = (MODE_PLAN, MODE_CHECK)
READ_ONLY_CONFIRMATION_FLAG = "--confirm-read-only"

FORBIDDEN_TARGETS = frozenset({
    "all", "any", "*", "list", "devices", "device", "-", "全部",
})

# True-device mode: every loopback spelling is refused, no bypass exists.
LOOPBACK_HOSTS = frozenset({"localhost", "[::1]", "::1"})

REPORT_SIDECAR_SUFFIX = ".sha256"
REPORT_TOOL_NAME = "harmony_release_verify_signature"
REPORT_STATUS_PASS = "signed_and_valid"
SHA256_HEX_RE = re.compile(r"^[0-9A-Fa-f]{64}$")
SIDECAR_TOKEN_RE = re.compile(r"^[0-9A-Fa-f]{64}$")
# A report input.relpath may only ever be a safe POSIX relative path: no
# leading slash (absolute), no drive/UNC form, no backslash separator,
# and no ".." traversal component anywhere (checked separately).
RELPATH_SAFE_RE = re.compile(r"^[^\\/]+(?:/[^\\/]+)*$")
REPORT_MAX_BYTES = 1 << 22  # 4 MiB; a report is a few KiB

# hdc needs no signing material and no credential: every release
# material/credential variable is stripped from the child environment and
# only their *names* are recorded (shared constants - no drift).
SCRUBBED_ENV_VARS = tuple(sorted({*MATERIAL_ENV_VARS, *CREDENTIAL_ENV_VARS}))

# --- read-only probe table (the complete allowlist) -----------------------
PROBE_API_VERSION = "api_version"
PROBE_DEVICE_MODEL = "device_model"
PROBE_BUNDLE_QUERY = "bundle_query"

PARAM_API_KEY = "const.ohos.apiversion"
PARAM_MODEL_KEY = "const.product.model"

# (probe name, phase) in execution order; the signature gate runs first.
PROBE_ORDER = (
    (PROBE_API_VERSION, "read_only"),
    (PROBE_DEVICE_MODEL, "read_only"),
    (PROBE_BUNDLE_QUERY, "read_only"),
)
STEP_GATE = "signature_report"

REASON_PLAN_MODE = "plan_mode"
REASON_REQUEST_INVALID = "request_invalid"
REASON_TOOLCHAIN_UNAVAILABLE = "toolchain_unavailable"
REASON_PREVIOUS_STEP_FAILED = "previous_step_failed"
REASON_BUNDLE_NOT_SUPPLIED = "bundle_not_supplied"
REASON_READ_ONLY_NOT_CONFIRMED = "read_only_not_confirmed"

STATUS_PLANNED = "planned"
STATUS_OK = "ok"
STATUS_FAILURE = "failure"
STATUS_BLOCKED = "blocked"

STEP_STATUS_OK = "ok"
STEP_STATUS_FAILURE = "failure"
STEP_STATUS_NOT_RUN = "not_run"

TARGET_RECORDED_AS = "sha256_prefix"
TARGET_KIND_LOOPBACK = "loopback"
TARGET_KIND_OTHER = "other"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_BLOCKED = 2

CommandRunner = Callable[[Sequence[str], Path, dict[str, str]], CommandResult]
ToolResolver = Callable[[str], tuple[Optional["HdcTool"], list[dict]]]


@dataclass(frozen=True)
class HdcTool:
    """Resolved hdc executable. The path is never serialized."""

    path: Path
    source: str  # "explicit" | "path_lookup"


@dataclass(frozen=True)
class ResolvedTarget:
    """Validated single target. The raw string is never serialized."""

    raw: str
    hash: str
    kind: str


@dataclass(frozen=True)
class SignatureGate:
    """Outcome of the signature-report gate. Paths never serialized."""

    report_sha256: str | None
    hap: dict | None


def make_subprocess_runner(timeout_seconds: float) -> CommandRunner:
    """Real runner; timeout propagates as TimeoutExpired, spawn errors as OSError."""

    def runner(
        argv: Sequence[str], cwd: Path, env: dict[str, str]
    ) -> CommandResult:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
        return CommandResult(
            argv=tuple(argv),
            cwd=Path(cwd),
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    return runner


subprocess_runner: CommandRunner = make_subprocess_runner(
    DEFAULT_COMMAND_TIMEOUT_SECONDS
)


def child_env() -> dict[str, str]:
    """Host environment minus every release material/credential variable."""
    env = dict(os.environ)
    for name in SCRUBBED_ENV_VARS:
        env.pop(name, None)
    return env


def _target_hash(target: str) -> str:
    digest = hashlib.sha256(target.encode("utf-8")).hexdigest()
    return digest[:TARGET_HASH_LENGTH].upper()


def _host_of(token: str) -> str:
    if token.startswith("["):  # bracketed IPv6 host, e.g. [::1]:5555
        end = token.find("]")
        return token[1:end] if end != -1 else token
    if token.count(":") == 1:  # host:port
        return token.rsplit(":", 1)[0]
    return token


def is_loopback_target(token: str) -> bool:
    host = _host_of(token.strip())
    return host.lower() in LOOPBACK_HOSTS or host.startswith("127.")


def _target_kind(token: str) -> str:
    return (
        TARGET_KIND_LOOPBACK if is_loopback_target(token) else TARGET_KIND_OTHER
    )


def resolve_target(
    target: str | None,
    *,
    true_device_mode: bool,
) -> tuple[ResolvedTarget | None, list[dict]]:
    """Validate one explicit target. Never discovers, never probes."""
    if target is None or not str(target).strip():
        return None, [{
            "code": "target_required",
            "detail": {
                "argument": "--target",
                "hint": "one explicit real device target is required",
            },
        }]
    raw = str(target)
    token = raw.strip()
    if token.lower() in FORBIDDEN_TARGETS:
        return None, [{
            "code": "target_all_devices_forbidden",
            "detail": {"argument": "--target"},
        }]
    if token != raw or len(raw.split()) != 1 or token.startswith("-"):
        return None, [{
            "code": "target_invalid",
            "detail": {"argument": "--target", "error": "not_a_single_token"},
        }]
    if true_device_mode and is_loopback_target(token):
        return None, [{
            "code": "target_loopback_forbidden",
            "detail": {
                "argument": "--target",
                "bypass": "none",
                "reason": "true-device preflight refuses loopback targets",
            },
        }]
    return ResolvedTarget(
        raw=token, hash=_target_hash(token), kind=_target_kind(token)
    ), []


def resolve_hdc_tool(program: str = DEFAULT_HDC) -> tuple[HdcTool | None, list[dict]]:
    """Resolve the hdc executable without ever executing it."""
    candidate = Path(program)
    if candidate.parent != Path("."):
        if candidate.is_file():
            return HdcTool(candidate, "explicit"), []
        return None, [{
            "code": "hdc_not_found",
            "detail": {"source": "explicit", "program_name": candidate.name},
        }]
    found = shutil.which(program)
    if found:
        return HdcTool(Path(found), "path_lookup"), []
    return None, [{
        "code": "hdc_not_found",
        "detail": {"source": "path_lookup", "program_name": Path(program).name},
    }]


def validate_relpath(value: object) -> str | None:
    """Return the value only if it is a safe POSIX relative path.

    Rejects non-strings, empty/whitespace-only values, absolute paths
    (leading ``/``), any colon (Windows drive forms ``C:`` and ``C:/x``,
    NTFS stream names), UNC forms (leading
    ``\\\\``), any backslash separator, and any ``..`` traversal
    component. The offending value is never echoed by the caller.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    token = value.strip()
    # A colon means a Windows drive form (``C:`` / ``C:/x``) or an NTFS
    # stream name; neither is a safe POSIX relative path.
    if ":" in token:
        return None
    if token != value or not RELPATH_SAFE_RE.match(token):
        return None
    if any(part == ".." for part in token.split("/")):
        return None
    return token


def _reject_constant(value: str):
    raise ValueError(f"non-strict JSON constant: {value}")


def load_signature_gate(
    report: str | None,
) -> tuple[SignatureGate | None, list[dict]]:
    """Consume an existing verify_signature report; fail closed on any doubt.

    The sidecar ``<report>.sha256`` must exist and its first token must equal
    the SHA-256 of the report bytes. The report must be strict JSON naming the
    verifier tool, a passing verdict with a real verifier decision, and a
    concrete HAP hash/size. A present ``input.relpath`` must validate as a
    safe POSIX relative path (absolute/drive/UNC/empty/``..`` forms block
    the run) before anything is serialized; the offending value is never
    echoed. Nothing about the file path is ever returned.
    """
    if report is None or not str(report).strip():
        return None, [{"code": "signature_report_required"}]
    path = Path(str(report)).expanduser()
    try:
        raw = path.read_bytes()
    except OSError:
        return None, [{"code": "signature_report_unreadable"}]
    if not raw:
        return None, [{"code": "signature_report_empty"}]
    if len(raw) > REPORT_MAX_BYTES:
        return None, [{"code": "signature_report_too_large"}]
    report_sha = hashlib.sha256(raw).hexdigest()
    try:
        sidecar_text = (
            path.with_name(path.name + REPORT_SIDECAR_SUFFIX).read_text(
                encoding="utf-8", errors="strict"
            )
        )
    except OSError:
        return None, [{"code": "signature_report_sidecar_missing"}]
    sidecar_token = sidecar_text.strip().split()[0] if sidecar_text.strip() else ""
    if not SIDECAR_TOKEN_RE.match(sidecar_token) or sidecar_token.lower() != report_sha:
        return None, [{"code": "signature_report_sidecar_mismatch"}]
    try:
        data = json.loads(
            raw.decode("utf-8", errors="strict"), parse_constant=_reject_constant
        )
    except (ValueError, UnicodeError):
        return None, [{"code": "signature_report_invalid_json"}]
    if not isinstance(data, dict):
        return None, [{"code": "signature_report_invalid_json"}]
    if data.get("tool") != REPORT_TOOL_NAME:
        return None, [{"code": "signature_report_wrong_tool"}]
    signature = data.get("signature")
    signature_ok = (
        isinstance(signature, dict)
        and signature.get("signed") is True
        and signature.get("verified") is True
    )
    if (
        data.get("status") != REPORT_STATUS_PASS
        or data.get("exit_code") != 0
        or not signature_ok
    ):
        return None, [{"code": "signature_report_not_passing"}]
    hap = data.get("input")
    sha = hap.get("sha256") if isinstance(hap, dict) else None
    size = hap.get("size_bytes") if isinstance(hap, dict) else None
    if (
        not isinstance(sha, str)
        or not SHA256_HEX_RE.match(sha)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
    ):
        return None, [{"code": "signature_report_hap_facts_invalid"}]
    relpath = hap.get("relpath")
    if relpath is not None:
        relpath = validate_relpath(relpath)
        if relpath is None:
            return None, [{"code": "signature_report_hap_relpath_invalid"}]
    return SignatureGate(
        report_sha256=report_sha.upper(),
        hap={
            "sha256": sha.upper(),
            "size_bytes": size,
            "relpath": relpath,
            "signedness_source": "verifier_report_not_filename",
        },
    ), []


def _command_shape(
    program_name: str, subcommand: str, operand_names: Sequence[str]
) -> dict:
    return {
        "program": program_name,
        "subcommand": subcommand,
        "target_option": "-t",
        "target_recorded": False,
        "option_names": [],
        "operand_names": list(operand_names),
        "read_only": True,
        "env_keys": [],
        "stripped_env_keys": list(SCRUBBED_ENV_VARS),
    }


def build_probe_commands(
    probe: str,
    program_name: str,
    program_path: Path,
    target: str,
    bundle: str | None,
) -> list[dict]:
    """Build one probe: (argv for the child, shape for the JSON)."""
    program = str(program_path)
    target_args = ["-t", target]
    if probe == PROBE_API_VERSION:
        argv = [program, *target_args, "shell", "param", "get", PARAM_API_KEY]
        shape = _command_shape(program_name, "shell param get", ["param_key"])
    elif probe == PROBE_DEVICE_MODEL:
        argv = [program, *target_args, "shell", "param", "get", PARAM_MODEL_KEY]
        shape = _command_shape(program_name, "shell param get", ["param_key"])
    elif probe == PROBE_BUNDLE_QUERY:
        argv = [
            program, *target_args, "shell", "bm", "dump", "-n", str(bundle)
        ]
        shape = _command_shape(program_name, "shell bm dump", ["bundle"])
    else:  # pragma: no cover - PROBE_ORDER is the only source of names
        raise ValueError(f"unknown probe: {probe}")
    return [{"argv": argv, "shape": shape}]


def parse_probe_output(probe: str, stdout: str) -> tuple[dict | None, str | None]:
    """Validate probe output. Parsed values are facts only; never echoed."""
    text = (stdout or "").strip()
    if probe == PROBE_API_VERSION:
        if not text or not text.isdigit():
            return None, "probe_output_invalid"
        return {"api_version": int(text)}, None
    if probe == PROBE_DEVICE_MODEL:
        if not text or not text.isprintable():
            return None, "probe_output_invalid"
        return {"value_present": True, "value_recorded": False}, None
    if probe == PROBE_BUNDLE_QUERY:
        if not text:
            return None, "probe_output_invalid"
        return {"bundle_present": "bundleName" in text}, None
    return None, "probe_output_invalid"  # pragma: no cover


def _not_run_step(
    name: str, phase: str, reason: str | None, shapes: Sequence[dict] = ()
) -> dict:
    return {
        "name": name,
        "phase": phase,
        "status": STEP_STATUS_NOT_RUN,
        "reason": reason,
        "commands": [
            {**shape, "executed": False, "exit_code": None, "error": None}
            for shape in shapes
        ],
        "commands_planned": len(shapes),
        "commands_attempted": 0,
        "commands_executed": 0,
        "facts": None,
        "failures": [],
        "warnings": [],
    }


def run_device_preflight(
    repo_root: Path,
    *,
    mode: str = MODE_PLAN,
    target: str | None = None,
    signature_report: str | None = None,
    bundle: str | None = None,
    hdc: str = DEFAULT_HDC,
    confirm_read_only: bool = False,
    timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    runner: CommandRunner | None = None,
    tool_resolver: ToolResolver | None = None,
    bundle_resolver=resolve_bundle,
) -> tuple[dict, int]:
    """Plan or execute the read-only preflight; returns (result, exit code)."""
    root = Path(repo_root)
    if mode not in MODES:  # pragma: no cover - CLI restricts mode already
        mode = MODE_PLAN
    true_device_mode = mode == MODE_CHECK

    request_failures: list[dict] = []
    toolchain_failures: list[dict] = []
    execution_failures: list[dict] = []
    warnings: list[dict] = []
    steps: list[dict] = []
    probes_record: dict[str, dict | None] = {}
    tool: HdcTool | None = None
    resolved_target: ResolvedTarget | None = None
    gate: SignatureGate | None = None
    bundle_name: str | None = None
    commands_executed = 0
    commands_attempted = 0
    hardware_touched = False

    program_name = Path(hdc).name or DEFAULT_HDC
    program_path = Path(hdc)

    # 1) Request validation (never touches a device or a tool).
    resolved_target, target_failures = resolve_target(
        target, true_device_mode=true_device_mode
    )
    request_failures += target_failures
    if true_device_mode and not confirm_read_only:
        request_failures.append({
            "code": REASON_READ_ONLY_NOT_CONFIRMED,
            "detail": {"confirmation_flag": READ_ONLY_CONFIRMATION_FLAG},
        })
    if bundle is not None and not bundle.strip():
        request_failures.append({
            "code": "bundle_invalid",
            "detail": {"argument": "--bundle", "error": "empty_value"},
        })
    # Zero-file-read rule: without an explicit --bundle, plan mode NEVER
    # resolves the bundle name - bundle_resolver(root, None) would read
    # AppScope/app.json5 - so the bundle-query step stays not_run with
    # the deterministic reason bundle_not_supplied. check mode keeps the
    # AppScope/app.json5 fallback (and passes failures through).
    if bundle is not None:
        if not request_failures and bundle.strip():
            bundle_name = bundle.strip()
    elif true_device_mode and not request_failures:
        try:
            resolved_bundle, resolve_failures = bundle_resolver(root, None)
        except Exception:  # pragma: no cover - defensive  # noqa: BLE001
            resolved_bundle = None
            resolve_failures = []
        if resolved_bundle:
            bundle_name = resolved_bundle
        elif resolve_failures:
            request_failures.extend(resolve_failures)

    # 2) Signature gate: checked mode only, always before the toolchain probe.
    gate_record: dict = {
        "mode": mode,
        "requested": signature_report is not None,
        "checked": False,
        "sidecar_verified": None,
        "report_sha256": None,
        "hap": None,
    }
    if true_device_mode:
        gate, gate_failures = load_signature_gate(signature_report)
        request_failures += gate_failures
        gate_record["checked"] = True
        if gate is not None:
            gate_record["sidecar_verified"] = True
            gate_record["report_sha256"] = gate.report_sha256
            gate_record["hap"] = gate.hap

    gate_shapes: list[dict] = []
    if request_failures:
        steps.append(_not_run_step(
            STEP_GATE, "gate", REASON_REQUEST_INVALID, gate_shapes
        ))
        steps.extend(
            _not_run_step(name, phase, REASON_REQUEST_INVALID)
            for name, phase in PROBE_ORDER
        )
        if true_device_mode and any(
            item["code"] == REASON_READ_ONLY_NOT_CONFIRMED for item in request_failures
        ):
            warnings.append({
                "code": REASON_READ_ONLY_NOT_CONFIRMED,
                "detail": {"confirmation_flag": READ_ONLY_CONFIRMATION_FLAG},
            })
    elif mode == MODE_PLAN:
        steps.append(_not_run_step(STEP_GATE, "gate", REASON_PLAN_MODE, gate_shapes))
        for name, phase in PROBE_ORDER:
            reason = (
                REASON_BUNDLE_NOT_SUPPLIED
                if name == PROBE_BUNDLE_QUERY and bundle_name is None
                else REASON_PLAN_MODE
            )
            steps.append(_not_run_step(
                name, phase, reason,
                [item["shape"] for item in build_probe_commands(
                    name, program_name, program_path,
                    str(target).strip() if target is not None else "",
                    bundle_name,
                )],
            ))
    else:
        # 3) Toolchain (checked mode only, after the gate passed).
        tool, toolchain_failures = (tool_resolver or resolve_hdc_tool)(hdc)
        if toolchain_failures:
            steps.append(_not_run_step(STEP_GATE, "gate", None, gate_shapes))
            steps.extend(
                _not_run_step(name, phase, REASON_TOOLCHAIN_UNAVAILABLE)
                for name, phase in PROBE_ORDER
            )
        else:
            steps.append({
                "name": STEP_GATE, "phase": "gate",
                "status": STEP_STATUS_OK, "reason": None,
                "commands": [], "commands_planned": 0,
                "commands_attempted": 0, "commands_executed": 0,
                "facts": {
                    "sidecar_verified": True,
                    "report_sha256": gate.report_sha256 if gate else None,
                    "hap": gate.hap if gate else None,
                },
                "failures": [], "warnings": [],
            })
            # 4) Read-only probes: sequential, fail closed.
            run = runner or make_subprocess_runner(timeout_seconds)
            env = child_env()
            previous_failed = False
            for name, phase in PROBE_ORDER:
                if name == PROBE_BUNDLE_QUERY and bundle_name is None:
                    steps.append(_not_run_step(
                        name, phase, REASON_BUNDLE_NOT_SUPPLIED
                    ))
                    continue
                if previous_failed:
                    steps.append(_not_run_step(
                        name, phase, REASON_PREVIOUS_STEP_FAILED
                    ))
                    continue
                commands = build_probe_commands(
                    name, program_name, tool.path, resolved_target.raw, bundle_name
                )
                item = commands[0]
                shape = item["shape"]
                commands_attempted += 1
                try:
                    outcome = run(item["argv"], root, env)
                except subprocess.TimeoutExpired:
                    hardware_touched = True
                    execution_failures.append({
                        "code": f"{name}_timeout",
                        "detail": {
                            "command": shape["subcommand"],
                            "timeout_seconds": timeout_seconds,
                        },
                    })
                    steps.append({
                        "name": name, "phase": phase,
                        "status": STEP_STATUS_FAILURE,
                        "reason": None,
                        "commands": [{**shape, "executed": True,
                                      "exit_code": None, "error": "timeout"}],
                        "commands_planned": 1, "commands_attempted": 1,
                        "commands_executed": 0, "facts": None,
                        "failures": [{"code": f"{name}_timeout"}],
                        "warnings": [],
                    })
                    previous_failed = True
                    continue
                except OSError:
                    execution_failures.append({
                        "code": f"{name}_failed",
                        "detail": {
                            "command": shape["subcommand"],
                            "error": "spawn_failed",
                        },
                    })
                    steps.append({
                        "name": name, "phase": phase,
                        "status": STEP_STATUS_FAILURE,
                        "reason": None,
                        "commands": [{**shape, "executed": False,
                                      "exit_code": None,
                                      "error": "spawn_failed"}],
                        "commands_planned": 1, "commands_attempted": 1,
                        "commands_executed": 0, "facts": None,
                        "failures": [{"code": f"{name}_failed",
                                      "detail": {"error": "spawn_failed"}}],
                        "warnings": [],
                    })
                    previous_failed = True
                    continue
                commands_executed += 1
                hardware_touched = True
                record = {**shape, "executed": True,
                          "exit_code": int(outcome.returncode), "error": None}
                facts: dict | None = None
                failures: list[dict] = []
                if outcome.returncode != 0:
                    failures.append({
                        "code": f"{name}_failed",
                        "detail": {
                            "command": shape["subcommand"],
                            "exit_code": int(outcome.returncode),
                        },
                    })
                else:
                    facts, parse_error = parse_probe_output(name, outcome.stdout)
                    if parse_error is not None:
                        facts = None
                        failures.append({
                            "code": f"{name}_output_invalid",
                            "detail": {"command": shape["subcommand"]},
                        })
                probes_record[name] = facts
                execution_failures += failures
                steps.append({
                    "name": name, "phase": phase,
                    "status": STEP_STATUS_FAILURE if failures else STEP_STATUS_OK,
                    "reason": None,
                    "commands": [record],
                    "commands_planned": 1, "commands_attempted": 1,
                    "commands_executed": 1, "facts": facts,
                    "failures": failures, "warnings": [],
                })
                if failures:
                    previous_failed = True

    failures = sorted(
        request_failures + toolchain_failures + execution_failures,
        key=lambda item: json.dumps(item, sort_keys=True),
    )
    if request_failures and not toolchain_failures and not execution_failures:
        status = STATUS_BLOCKED
    elif failures:
        status = STATUS_FAILURE
    elif mode == MODE_CHECK:
        status = STATUS_OK
    else:
        status = STATUS_PLANNED

    exit_code = EXIT_OK
    if status == STATUS_BLOCKED:
        exit_code = EXIT_BLOCKED
    elif status == STATUS_FAILURE:
        exit_code = EXIT_FAILURE

    commands_planned = 3 - (
        1 if bundle_name is None else 0
    )
    target_record = None
    if resolved_target is not None:
        target_record = {
            "recorded_as": TARGET_RECORDED_AS,
            "hash": resolved_target.hash,
            "kind": resolved_target.kind,
            "raw_recorded": False,
        }
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "mode": mode,
        "status": status,
        "exit_code": exit_code,
        "dry_run": mode == MODE_PLAN,
        "confirmation": {
            "flag": READ_ONLY_CONFIRMATION_FLAG,
            "confirmed": bool(confirm_read_only and mode == MODE_CHECK),
        },
        "signature_gate": gate_record,
        "target": target_record,
        "bundle_name": bundle_name,
        "toolchain": {
            "probed": mode == MODE_CHECK and not request_failures,
            "program_name": program_name,
            "resolved": tool is not None,
            "source": tool.source if tool is not None else None,
            "version_recorded": False,
        },
        "command_timeout_seconds": timeout_seconds,
        "device_access": {
            "target_explicit": bool(target is not None and str(target).strip()),
            "discovery_used": False,
            "all_devices_forbidden": True,
            "read_only_only": True,
            "loopback_rejected_in_true_device_mode": True,
            "commands_planned": commands_planned,
            "commands_attempted": commands_attempted,
            "commands_executed": commands_executed,
            "hardware_touched": hardware_touched or commands_executed > 0,
            "mutation_performed": False,
            "plan_only": mode == MODE_PLAN,
        },
        "probes": probes_record or None,
        "steps": steps,
        "not_run": [
            item["name"] for item in steps if item["status"] == STEP_STATUS_NOT_RUN
        ],
        "boundaries": {
            "agc_access": False,
            "credentials_used": False,
            "signing_performed": False,
            "device_logs_read": False,
            "command_output_recorded": False,
            "device_identity_recorded": False,
            "signedness_source": "verifier_report_not_filename",
            "lifecycle_evidence": "exit_codes_only",
        },
        "warnings": warnings,
        "failures": failures,
    }
    return result, exit_code


def render_json(result: dict) -> str:
    """Deterministic serialization (sorted keys, ASCII-safe)."""
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True)


def render_summary(result: dict) -> str:
    """One-line human summary: names, hashes and statuses only."""
    parts = [
        f"{TOOL_NAME}: mode={result['mode']}",
        f"status={result['status']}",
        f"exit={result['exit_code']}",
    ]
    target_record = result.get("target")
    if target_record:
        parts.append(f"target_hash={target_record['hash']}")
        parts.append(f"target_kind={target_record['kind']}")
    gate = result.get("signature_gate") or {}
    if gate.get("sidecar_verified"):
        parts.append("signature_gate=verified")
    access = result["device_access"]
    parts.append(f"commands_executed={access['commands_executed']}")
    not_run = result.get("not_run") or []
    if not_run:
        parts.append(f"not_run={','.join(not_run)}")
    codes = ",".join(item["code"] for item in result["failures"])
    if codes:
        parts.append(f"failures={codes}")
    return " ".join(parts)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="device_preflight",
        description="HarmonyOS real-device read-only preflight gate "
        "(fail-closed; plan by default).",
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    def common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--repo-root", type=Path, default=DEFAULT_REPO_ROOT,
            help="Repository root (default: this checkout).",
        )
        sub.add_argument(
            "--target", required=True, default=None,
            help="One explicit real device target. Discovery is never "
            "performed; loopback is refused in check mode.",
        )
        sub.add_argument(
            "--bundle", default=None,
            help="Bundle name to query. Without it, check mode falls back "
            "to AppScope/app.json5 (plan mode never reads any file: the "
            "bundle-query step stays not_run with reason "
            "bundle_not_supplied).",
        )
        sub.add_argument(
            "--hdc", default=DEFAULT_HDC,
            help="hdc executable name or path (default: hdc from PATH).",
        )
        sub.add_argument(
            "--timeout-seconds", type=float,
            default=DEFAULT_COMMAND_TIMEOUT_SECONDS,
            help="Per-command timeout (default: 30).",
        )
        sub.add_argument(
            "--quiet", action="store_true",
            help="Suppress the one-line stderr summary.",
        )

    plan = subparsers.add_parser(
        MODE_PLAN, help="Plan only: nothing is resolved, read or spawned."
    )
    common(plan)
    plan.add_argument(
        "--signature-report", default=None,
        help="Signature report to record as planned input (not read in plan).",
    )

    check = subparsers.add_parser(
        MODE_CHECK, help="Read-only execution (requires the exact flag)."
    )
    common(check)
    check.add_argument(
        "--signature-report", required=True, default=None,
        help="Existing verify_signature.py report JSON (needs a matching "
        ".sha256 sidecar and a passing verdict).",
    )
    check.add_argument(
        READ_ONLY_CONFIRMATION_FLAG, action="store_true",
        help="Exact opt-in to read-only device probing. There is no bypass "
        "for the loopback refusal.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result, exit_code = run_device_preflight(
        repo_root=args.repo_root.resolve(),
        mode=args.mode,
        target=args.target,
        signature_report=getattr(args, "signature_report", None),
        bundle=args.bundle,
        hdc=args.hdc,
        confirm_read_only=getattr(args, "confirm_read_only", False),
        timeout_seconds=args.timeout_seconds,
    )
    sys.stdout.write(render_json(result) + "\n")
    if not args.quiet:
        sys.stderr.write(render_summary(result) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
