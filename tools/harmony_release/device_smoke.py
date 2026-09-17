"""HarmonyOS device smoke wrapper (M13-16a slice 5).

Plans - and, only with an explicit confirmation flag, executes - one device
smoke run of an already built HAP against **one explicit device target**, and
records the honest facts of that run as deterministic JSON.

Documented child commands (values go to the child only; the JSON records
command *shapes* - program/subcommand/option and operand *names* - never an
argv, never the target string, never an absolute path)::

    hdc -t <TARGET> install <HAP>
    hdc -t <TARGET> shell aa start -a <ABILITY> -b <BUNDLE>
    hdc -t <TARGET> shell uitest dumpLayout -p <REMOTE_LAYOUT_PATH>
    hdc -t <TARGET> file recv <REMOTE_LAYOUT_PATH> <LOCAL_LAYOUT_PATH>
    hdc -t <TARGET> shell aa force-stop <BUNDLE>
    hdc -t <TARGET> uninstall <BUNDLE>

Steps and their phases (executed in this order)::

    install    mutation      installs the HAP
    start      foreground    starts the ability (aa start)
    layout     read_only     dumps the UI tree (uitest dumpLayout) and pulls it
    background background    leaves the foreground (aa force-stop)
    uninstall  cleanup       removes the bundle again

``background`` is implemented as the documented ``aa force-stop``: the ability
process stops, so the app is no longer in the foreground. This wrapper does
**not** claim any particular background-task state beyond that command's exit
code, and the recorded command shape shows ``force-stop`` verbatim.

Safety contract:
- ``--target`` is required and must be **one explicit** device target. Target
  discovery is never performed and the "all devices" spellings (``all``,
  ``any``, ``*``, ``list``, ``devices``) are rejected before anything runs, so
  a bare ``hdc install`` against every attached device is impossible by
  construction.
- Mutation is opt-in: without ``--confirm-mutation`` the run is a pure plan -
  nothing is resolved on the device, nothing is spawned, and every step is
  recorded as ``not_run`` with the exact ``not_run`` list at the top level.
- Process execution (``runner``), hdc discovery (``tool_resolver``), target
  validation (``target_resolver``) and bundle resolution (``bundle_resolver``)
  are all injectable, so unit tests never invoke hdc, never touch a device and
  never install anything.
- Never serialized: environment values, secrets, raw argv, absolute paths,
  command stdout/stderr, layout content, the raw target string and unrelated
  device identifiers. The target is recorded only as a 12-hex-character
  SHA-256 prefix plus a loopback/other kind; the toolchain only by program
  name/source. Device logs are never read at all.
- Fail closed (exit 2, status ``blocked``) on: missing/empty target, forbidden
  all-devices target, a target that is not a single token, an empty supplied
  device list, a target absent from the supplied list, an ambiguous target
  prefix, a missing/outside-repository/unreadable HAP, and an unresolvable
  bundle name. Fail closed (exit 1, status ``failure``) on: missing hdc, spawn
  failure, timeout, a nonzero step exit code, a missing/empty/unreadable layout
  file, invalid layout JSON, and cleanup (uninstall) failure.
- Cleanup semantics: once the run has mutated device state (a successful
  ``install``), uninstall cleanup is attempted even when a later step failed,
  and a cleanup failure is surfaced as its own ``cleanup_failed`` failure - it
  is never hidden behind a successful step list.
- Honest boundaries (recorded, never implied): exit codes are the only
  lifecycle evidence; no screenshot, logcat, hilog or device-version probe is
  read; the HAP filename is recorded as a fact but is never used to infer
  signedness; and the device-side layout dump is left on the device (only the
  installed bundle is cleaned up).

Exit codes: 0 = ok or planned (dry run), 1 = failure (fail-closed), 2 = blocked
(fail-closed request/target validation, nothing executed). (argparse usage
errors also exit 2 - the interpreter's own convention, e.g. a missing
``--target``.)
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
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

try:  # package import (pytest, python -m)
    from tools.harmony_release.json5lite import load_json5_relaxed
    from tools.harmony_release.preflight import MATERIAL_ENV_VARS
    from tools.harmony_release.sign_hap import CREDENTIAL_ENV_VARS
except ImportError:  # direct script: python tools/harmony_release/device_smoke.py
    from json5lite import load_json5_relaxed
    from preflight import MATERIAL_ENV_VARS  # type: ignore[no-redef]
    from sign_hap import CREDENTIAL_ENV_VARS  # type: ignore[no-redef]

SCHEMA_VERSION = 1
TOOL_NAME = "harmony_device_smoke"
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]

HARMONY_SUBDIR = "apps/harmony"
APP_SCOPE_RELPATH = Path("apps/harmony/AppScope/app.json5")

DEFAULT_HDC = "hdc"
DEFAULT_ABILITY = "EntryAbility"
DEFAULT_COMMAND_TIMEOUT_SECONDS = 120.0

# Constant device-side and local layout file coordinates. Neither value is
# ever serialized: the JSON records the *names* of the slots only.
REMOTE_LAYOUT_PATH = "/data/local/tmp/aios_device_smoke_layout.json"
LOCAL_LAYOUT_FILENAME = "device_smoke_layout.json"

# Spellings that would mean "discover" or "every device". Rejected outright.
FORBIDDEN_TARGETS = frozenset({
    "all", "any", "*", "list", "devices", "device", "-", "全部",
})

TARGET_HASH_LENGTH = 12
LOOPBACK_TARGET_RE = re.compile(
    r"^(?:127\.0\.0\.1|localhost|\[::1\]):\d{1,5}$", re.IGNORECASE
)

MUTATION_CONFIRMATION_FLAG = "--confirm-mutation"

# hdc needs no signing material and no credential: every release
# material/credential variable is stripped from the child environment, and
# only their *names* are recorded (same shared constants as
# verify_signature.py, so the tools cannot drift).
SCRUBBED_ENV_VARS = tuple(sorted({*MATERIAL_ENV_VARS, *CREDENTIAL_ENV_VARS}))

STEP_INSTALL = "install"
STEP_START = "start"
STEP_LAYOUT = "layout"
STEP_BACKGROUND = "background"
STEP_UNINSTALL = "uninstall"

# (step name, phase) in documented execution order.
STEP_ORDER = (
    (STEP_INSTALL, "mutation"),
    (STEP_START, "foreground"),
    (STEP_LAYOUT, "read_only"),
    (STEP_BACKGROUND, "background"),
    (STEP_UNINSTALL, "cleanup"),
)

REASON_MUTATION_NOT_CONFIRMED = "mutation_not_confirmed"
REASON_REQUEST_INVALID = "request_invalid"
REASON_TOOLCHAIN_UNAVAILABLE = "toolchain_unavailable"
REASON_PREVIOUS_STEP_FAILED = "previous_step_failed"
REASON_INSTALL_NOT_SUCCESSFUL = "install_not_successful"

STATUS_PLANNED = "planned"
STATUS_OK = "ok"
STATUS_FAILURE = "failure"
STATUS_BLOCKED = "blocked"

STEP_STATUS_OK = "ok"
STEP_STATUS_FAILURE = "failure"
STEP_STATUS_NOT_RUN = "not_run"

CLEANUP_NOT_REQUIRED = "not_required"
TARGET_RECORDED_AS = "sha256_prefix"
TARGET_KIND_LOOPBACK = "loopback"
TARGET_KIND_OTHER = "other"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_BLOCKED = 2


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
class HdcTool:
    """Resolved hdc executable. The path is never serialized."""

    path: Path
    source: str  # "explicit" | "path_lookup"


@dataclass(frozen=True)
class ResolvedTarget:
    """Validated single device target. The raw string is never serialized."""

    raw: str
    hash: str
    kind: str
    known_list_size: int
    validated: bool
    match: Optional[str]  # "exact" | "prefix" | None when no list was supplied


CommandRunner = Callable[[Sequence[str], Path, Dict[str, str]], CommandResult]
ToolResolver = Callable[[str], Tuple[Optional[HdcTool], List[dict]]]
TargetResolver = Callable[
    [Optional[str], Optional[Sequence[str]]],
    Tuple[Optional[ResolvedTarget], List[dict]],
]
BundleResolver = Callable[[Path, Optional[str]], Tuple[Optional[str], List[dict]]]


def make_subprocess_runner(timeout_seconds: float) -> CommandRunner:
    """Build the real runner; the timeout is enforced by the child call.

    A timeout propagates as ``subprocess.TimeoutExpired`` (and a spawn problem
    as ``OSError``) so the caller can fail closed on both.
    """

    def runner(
        argv: Sequence[str], cwd: Path, env: Dict[str, str]
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


# Default real runner (used when no runner is injected).
subprocess_runner: CommandRunner = make_subprocess_runner(
    DEFAULT_COMMAND_TIMEOUT_SECONDS
)


def _sha256_upper(path: Path) -> str:
    """Streaming SHA-256, uppercase hex (same format as preflight)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def child_env() -> Dict[str, str]:
    """Host environment minus every release material/credential variable."""
    env = dict(os.environ)
    for name in SCRUBBED_ENV_VARS:
        env.pop(name, None)
    return env


def _target_hash(target: str) -> str:
    """Short, stable, non-reversible alias for a device target."""
    digest = hashlib.sha256(target.encode("utf-8")).hexdigest()
    return digest[:TARGET_HASH_LENGTH].upper()


def _target_kind(target: str) -> str:
    return (
        TARGET_KIND_LOOPBACK
        if LOOPBACK_TARGET_RE.match(target)
        else TARGET_KIND_OTHER
    )


def _resolve_in_repo(repo_root: Path, raw: str) -> Path:
    """Resolve a path against the repository root (absolute paths allowed)."""
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = Path(repo_root) / candidate
    try:
        return candidate.resolve()
    except OSError:
        return candidate


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def resolve_hdc_tool(program: str = DEFAULT_HDC) -> Tuple[Optional[HdcTool], List[dict]]:
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


def resolve_target(
    target: Optional[str],
    known_targets: Optional[Sequence[str]] = None,
) -> Tuple[Optional[ResolvedTarget], List[dict]]:
    """Validate the explicit target. Never discovers anything.

    ``known_targets`` is an operator-supplied list of device identifiers (the
    ``--device-id`` option), used only to cross-check the target the operator
    already named - never to pick one. Defining an empty list is a failure, and
    a target that is absent from the list or matches several entries by prefix
    fails closed as well.
    """
    list_provided = known_targets is not None
    known = [
        item.strip()
        for item in (known_targets or [])
        if isinstance(item, str) and item.strip()
    ]

    if target is None or not str(target).strip():
        return None, [{
            "code": "target_required",
            "detail": {
                "argument": "--target",
                "hint": "one explicit device target is required; "
                        "discovery and 'all devices' are forbidden",
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

    match: Optional[str] = None
    if list_provided:
        if not known:
            return None, [{
                "code": "target_list_empty",
                "detail": {"argument": "--device-id"},
            }]
        if token in known:
            match = "exact"
        else:
            prefixed = sorted(item for item in known if item.startswith(token))
            if not prefixed:
                return None, [{
                    "code": "target_unknown",
                    "detail": {"argument": "--target", "known_count": len(known)},
                }]
            if len(prefixed) > 1:
                return None, [{
                    "code": "target_ambiguous",
                    "detail": {
                        "argument": "--target",
                        "match_count": len(prefixed),
                    },
                }]
            match = "prefix"

    return ResolvedTarget(
        raw=token,
        hash=_target_hash(token),
        kind=_target_kind(token),
        known_list_size=len(known),
        validated=match is not None,
        match=match,
    ), []


def resolve_bundle(
    repo_root: Path, bundle: Optional[str] = None
) -> Tuple[Optional[str], List[dict]]:
    """Resolve the bundle name: explicit argument, else AppScope/app.json5."""
    if bundle is not None:
        token = bundle.strip()
        if token:
            return token, []
        return None, [{
            "code": "bundle_not_resolved",
            "detail": {"argument": "--bundle", "error": "empty_value"},
        }]
    path = Path(repo_root) / APP_SCOPE_RELPATH
    if not path.is_file():
        return None, [{
            "code": "bundle_not_resolved",
            "detail": {
                "app_relpath": APP_SCOPE_RELPATH.as_posix(),
                "error": "app_json5_missing",
            },
        }]
    data = load_json5_relaxed(path.read_text(encoding="utf-8-sig"))
    app = data.get("app") if isinstance(data, dict) else None
    name = app.get("bundleName") if isinstance(app, dict) else None
    if not isinstance(name, str) or not name.strip():
        return None, [{
            "code": "bundle_not_resolved",
            "detail": {
                "app_relpath": APP_SCOPE_RELPATH.as_posix(),
                "error": "bundle_name_missing",
            },
        }]
    return name.strip(), []


def inspect_hap(repo_root: Path, hap: Optional[str]) -> Tuple[Optional[dict], List[dict]]:
    """Record HAP facts. The filename is never used to infer signedness."""
    if not hap:
        return None, [{"code": "hap_required", "detail": {"argument": "--hap"}}]
    root = Path(repo_root).resolve()
    path = _resolve_in_repo(root, hap)
    if not _inside(path, root):
        return None, [{"code": "hap_outside_repository"}]
    if not path.is_file():
        return None, [{"code": "hap_missing", "detail": {"argument": "--hap"}}]
    try:
        size_bytes = path.stat().st_size
        sha256 = _sha256_upper(path)
    except OSError:
        return None, [{"code": "hap_unreadable"}]
    name = path.name.lower()
    filename_has_unsigned = "unsigned" in name
    return {
        "relpath": path.relative_to(root).as_posix(),
        "size_bytes": size_bytes,
        "sha256": sha256,
        "inside_repository": True,
        "filename_has_unsigned": filename_has_unsigned,
        "filename_has_signed": (not filename_has_unsigned) and "signed" in name,
        "signedness_verified": False,
    }, []


def _count_nodes(value: object) -> int:
    """Generic node count of a parsed layout tree (content is never kept)."""
    if isinstance(value, dict):
        return 1 + sum(_count_nodes(item) for item in value.values())
    if isinstance(value, list):
        return 1 + sum(_count_nodes(item) for item in value)
    return 1


def inspect_layout(path: Path) -> Tuple[Optional[dict], List[dict]]:
    """Validate the pulled layout file; content is inspected, never recorded."""
    if not path.exists():
        return None, [{
            "code": "layout_file_missing",
            "detail": {"filename": path.name},
        }]
    if not path.is_file():
        return None, [{
            "code": "layout_file_unreadable",
            "detail": {"filename": path.name},
        }]
    try:
        data = path.read_bytes()
    except OSError:
        return None, [{
            "code": "layout_file_unreadable",
            "detail": {"filename": path.name},
        }]
    if not data:
        return None, [{
            "code": "layout_file_empty",
            "detail": {"filename": path.name},
        }]
    try:
        parsed = json.loads(data.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeError):
        return None, [{
            "code": "layout_invalid_json",
            "detail": {"filename": path.name, "size_bytes": len(data)},
        }]
    root_type = (
        "dict" if isinstance(parsed, dict)
        else "list" if isinstance(parsed, list)
        else "scalar"
    )
    return {
        "filename": path.name,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest().upper(),
        "parseable": True,
        "root_type": root_type,
        "node_count": _count_nodes(parsed),
        "content_recorded": False,
    }, []


def _command_shape(
    program_name: str,
    subcommand: str,
    option_names: Sequence[str],
    operand_names: Sequence[str],
) -> dict:
    """Command shape: names only. Values and paths stay out of the record."""
    return {
        "program": program_name,
        "subcommand": subcommand,
        "target_option": "-t",
        "target_recorded": False,
        "option_names": list(option_names),
        "operand_names": list(operand_names),
        "env_keys": [],
        "stripped_env_keys": list(SCRUBBED_ENV_VARS),
    }


def build_step_commands(
    step: str,
    program_name: str,
    program_path: Path,
    target: str,
    hap_path: Optional[Path],
    bundle: Optional[str],
    ability: str,
    layout_local: Path,
) -> List[dict]:
    """Planned commands for one step: (argv for the child, shape for the JSON).

    The argv carries the real values (target, HAP path, bundle, absolute local
    layout path) and is handed to the process runner only - it is never
    serialized, printed or embedded in an error message.
    """
    program = str(program_path)
    target_args = ["-t", target]

    if step == STEP_INSTALL:
        argv = [program, *target_args, "install", str(hap_path)]
        shape = _command_shape(program_name, "install", [], ["hap"])
    elif step == STEP_START:
        argv = [
            program, *target_args, "shell", "aa", "start", "-a", ability,
            "-b", str(bundle),
        ]
        shape = _command_shape(
            program_name, "shell aa start", ["-a", "-b"], ["ability", "bundle"]
        )
    elif step == STEP_LAYOUT:
        return [
            {
                "argv": [
                    program, *target_args, "shell", "uitest", "dumpLayout",
                    "-p", REMOTE_LAYOUT_PATH,
                ],
                "shape": _command_shape(
                    program_name, "shell uitest dumpLayout", ["-p"],
                    ["remote_layout_path"],
                ),
            },
            {
                "argv": [
                    program, *target_args, "file", "recv", REMOTE_LAYOUT_PATH,
                    str(layout_local),
                ],
                "shape": _command_shape(
                    program_name, "file recv", [],
                    ["remote_layout_path", "local_layout_path"],
                ),
            },
        ]
    elif step == STEP_BACKGROUND:
        argv = [program, *target_args, "shell", "aa", "force-stop", str(bundle)]
        shape = _command_shape(program_name, "shell aa force-stop", [], ["bundle"])
    elif step == STEP_UNINSTALL:
        argv = [program, *target_args, "uninstall", str(bundle)]
        shape = _command_shape(program_name, "uninstall", [], ["bundle"])
    else:  # pragma: no cover - STEP_ORDER is the only source of step names
        raise ValueError(f"unknown step: {step}")

    return [{"argv": argv, "shape": shape}]


def _not_run_step(
    name: str, phase: str, reason: Optional[str], shapes: Sequence[dict] = ()
) -> dict:
    """A step that was planned but not executed (reason recorded verbatim)."""
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
        "failures": [],
        "warnings": [],
    }


def _planned_step(name: str, phase: str, shapes: Sequence[dict]) -> dict:
    return _not_run_step(name, phase, None, shapes)


def _step_shapes(planned: Dict[str, List[dict]], name: str) -> List[dict]:
    return [item["shape"] for item in planned[name]]


def run_device_smoke(
    repo_root: Path,
    *,
    target: Optional[str] = None,
    hap: Optional[str] = None,
    bundle: Optional[str] = None,
    ability: str = DEFAULT_ABILITY,
    hdc: str = DEFAULT_HDC,
    confirm_mutation: bool = False,
    known_targets: Optional[Sequence[str]] = None,
    layout_dir: Optional[Path] = None,
    timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    runner: Optional[CommandRunner] = None,
    tool_resolver: Optional[ToolResolver] = None,
    target_resolver: Optional[TargetResolver] = None,
    bundle_resolver: Optional[BundleResolver] = None,
) -> Tuple[dict, int]:
    """Plan (default) or execute (``confirm_mutation=True``) one device smoke.

    Returns ``(deterministic result, exit code)``. ``runner``,
    ``tool_resolver``, ``target_resolver`` and ``bundle_resolver`` default to
    the real implementations; tests inject all of them, so no hdc process is
    ever spawned and no device is ever touched by the test suite.
    """
    root = Path(repo_root)
    request_failures: List[dict] = []
    toolchain_failures: List[dict] = []
    execution_failures: List[dict] = []
    warnings: List[dict] = []
    steps: List[dict] = []
    layout_record: Optional[dict] = None
    tool: Optional[HdcTool] = None
    resolved_target: Optional[ResolvedTarget] = None
    hap_record: Optional[dict] = None
    bundle_name: Optional[str] = None
    commands_executed = 0
    commands_attempted = 0
    hardware_touched = False
    mutation_performed = False
    cleanup_attempted = False
    created_workdir: Optional[Path] = None

    # 1) Request validation - target, HAP, bundle. Nothing runs when any of
    #    these fails closed.
    resolved_target, target_failures = (target_resolver or resolve_target)(
        target, known_targets
    )
    request_failures += target_failures
    if not target_failures:
        hap_record, hap_failures = inspect_hap(root, hap)
        request_failures += hap_failures
    if not request_failures:
        bundle_name, bundle_failures = (bundle_resolver or resolve_bundle)(
            root, bundle
        )
        request_failures += bundle_failures

    # 2) Plan shapes (names only) - recorded for every outcome, executed never.
    program_name = Path(hdc).name or DEFAULT_HDC
    program_path = Path(hdc)
    layout_local = (
        Path(layout_dir) / LOCAL_LAYOUT_FILENAME
        if layout_dir is not None
        else Path(tempfile.gettempdir()) / LOCAL_LAYOUT_FILENAME
    )
    hap_path = (
        _resolve_in_repo(Path(root).resolve(), hap) if hap_record else None
    )
    planned: Dict[str, List[dict]] = {
        name: build_step_commands(
            name, program_name, program_path,
            str(target).strip() if target is not None else "",
            hap_path, bundle_name, ability, layout_local,
        )
        for name, _phase in STEP_ORDER
    }

    toolchain_record = {
        "probed": False,
        "program_name": program_name,
        "resolved": False,
        "source": None,
        "version_recorded": False,
    }

    if request_failures:
        steps = [
            _not_run_step(name, phase, REASON_REQUEST_INVALID)
            for name, phase in STEP_ORDER
        ]
    elif not confirm_mutation:
        # Dry run: nothing is resolved on the device, nothing is spawned.
        warnings.append({
            "code": REASON_MUTATION_NOT_CONFIRMED,
            "detail": {
                "confirmation_flag": MUTATION_CONFIRMATION_FLAG,
                "not_run": [name for name, _phase in STEP_ORDER],
            },
        })
        steps = [
            {
                **_planned_step(name, phase, planned[name]["shapes"]
                                if isinstance(planned[name], dict) else
                                [item["shape"] for item in planned[name]]),
                "reason": REASON_MUTATION_NOT_CONFIRMED,
            }
            for name, phase in STEP_ORDER
        ]
    else:
        # 3) Toolchain: only probed for a confirmed mutation run.
        tool, toolchain_failures = (tool_resolver or resolve_hdc_tool)(hdc)
        toolchain_record = {
            "probed": True,
            "program_name": program_name,
            "resolved": tool is not None,
            "source": tool.source if tool is not None else None,
            "version_recorded": False,
        }
        if toolchain_failures:
            steps = [
                {
                    **_planned_step(
                        name, phase, [item["shape"] for item in planned[name]]
                    ),
                    "reason": REASON_TOOLCHAIN_UNAVAILABLE,
                }
                for name, phase in STEP_ORDER
            ]

    # 4) Execution: sequential, fail-closed, uninstall cleanup always attempted
    #    once device state was mutated.
    if confirm_mutation and not request_failures and not toolchain_failures:
        run = runner or make_subprocess_runner(timeout_seconds)
        # hdc needs no signing material and no credential: the material and
        # credential variables are stripped from the child environment and
        # only their names are recorded in the command shapes.
        env = child_env()
        if layout_dir is None:
            created_workdir = Path(
                tempfile.mkdtemp(prefix="aios-device-smoke-")
            )
            layout_local = created_workdir / LOCAL_LAYOUT_FILENAME

        try:
            previous_failed = False
            for name, phase in STEP_ORDER:
                if name == STEP_UNINSTALL:
                    if not mutation_performed:
                        steps.append(_not_run_step(
                            name, phase, REASON_INSTALL_NOT_SUCCESSFUL,
                            _step_shapes(planned, name),
                        ))
                        continue
                elif previous_failed:
                    steps.append(_not_run_step(
                        name, phase, REASON_PREVIOUS_STEP_FAILED,
                        _step_shapes(planned, name),
                    ))
                    continue

                commands = build_step_commands(
                    name, program_name, tool.path, resolved_target.raw,
                    hap_path, bundle_name, ability, layout_local,
                )

                if name == STEP_UNINSTALL:
                    cleanup_attempted = True

                step_failures: List[dict] = []
                records: List[dict] = []
                step_executed = 0
                step_attempted = 0
                for item in commands:
                    shape = item["shape"]
                    commands_attempted += 1
                    step_attempted += 1
                    try:
                        outcome = run(item["argv"], root, env)
                    except subprocess.TimeoutExpired:
                        # The child started and was killed at the deadline:
                        # attempted, and it did touch hardware, but it never
                        # completed - so it is not counted as executed.
                        hardware_touched = True
                        records.append({
                            **shape, "executed": True, "exit_code": None,
                            "error": "timeout",
                        })
                        step_failures.append({
                            "code": f"{name}_timeout",
                            "detail": {
                                "command": shape["subcommand"],
                                "timeout_seconds": timeout_seconds,
                            },
                        })
                        break
                    except OSError:
                        # Pure spawn failure: the child never started, so the
                        # command is attempted but not executed and does not
                        # by itself count as touching hardware.
                        records.append({
                            **shape, "executed": False, "exit_code": None,
                            "error": "spawn_failed",
                        })
                        step_failures.append({
                            "code": f"{name}_failed",
                            "detail": {
                                "command": shape["subcommand"],
                                "error": "spawn_failed",
                            },
                        })
                        break
                    step_executed += 1
                    commands_executed += 1
                    hardware_touched = True
                    # stdout/stderr are deliberately never inspected/recorded.
                    records.append({
                        **shape, "executed": True,
                        "exit_code": int(outcome.returncode), "error": None,
                    })
                    if outcome.returncode != 0:
                        step_failures.append({
                            "code": f"{name}_failed",
                            "detail": {
                                "command": shape["subcommand"],
                                "exit_code": int(outcome.returncode),
                            },
                        })
                        break

                if name == STEP_INSTALL and not step_failures:
                    mutation_performed = True

                if name == STEP_LAYOUT and not step_failures:
                    layout_record, layout_failures = inspect_layout(layout_local)
                    step_failures += layout_failures

                step_status = (
                    STEP_STATUS_FAILURE if step_failures else STEP_STATUS_OK
                )
                steps.append({
                    "name": name,
                    "phase": phase,
                    "status": step_status,
                    "reason": None,
                    "commands": records,
                    "commands_planned": len(commands),
                    "commands_attempted": step_attempted,
                    "commands_executed": step_executed,
                    "failures": step_failures,
                    "warnings": [],
                })
                execution_failures += step_failures
                if step_failures:
                    previous_failed = True
        finally:
            if created_workdir is not None:
                shutil.rmtree(created_workdir, ignore_errors=True)

    # 5) Cleanup summary - a cleanup failure is its own surfaced failure.
    cleanup_failures: List[dict] = []
    if mutation_performed:
        uninstall_step = next(
            (item for item in steps if item["name"] == STEP_UNINSTALL), None
        )
        uninstall_ok = bool(
            uninstall_step
            and uninstall_step["status"] == STEP_STATUS_OK
        )
        cleanup = {
            "required": True,
            "attempted": cleanup_attempted,
            "status": STEP_STATUS_OK if uninstall_ok else STEP_STATUS_FAILURE,
            "reason": None if uninstall_ok else "uninstall_not_successful",
        }
        if not uninstall_ok:
            cleanup_failures = [{
                "code": "cleanup_failed",
                "detail": {"step": STEP_UNINSTALL},
            }]
    else:
        cleanup = {
            "required": False,
            "attempted": cleanup_attempted,
            "status": CLEANUP_NOT_REQUIRED,
            "reason": REASON_INSTALL_NOT_SUCCESSFUL,
        }
    execution_failures += cleanup_failures

    failures = sorted(
        request_failures + toolchain_failures + execution_failures,
        key=lambda item: json.dumps(item, sort_keys=True),
    )

    if request_failures and not toolchain_failures and not execution_failures:
        status = STATUS_BLOCKED
    elif failures:
        status = STATUS_FAILURE
    elif confirm_mutation:
        status = STATUS_OK
    else:
        status = STATUS_PLANNED

    exit_code = EXIT_OK
    if status == STATUS_BLOCKED:
        exit_code = EXIT_BLOCKED
    elif status == STATUS_FAILURE:
        exit_code = EXIT_FAILURE

    target_record = None
    if resolved_target is not None:
        target_record = {
            "recorded_as": TARGET_RECORDED_AS,
            "hash": resolved_target.hash,
            "kind": resolved_target.kind,
            "validated_against_device_list": resolved_target.validated,
            "match": resolved_target.match,
            "known_device_count": resolved_target.known_list_size,
            "raw_recorded": False,
        }
    if target_record is not None and known_targets is None:
        warnings.append({"code": "target_list_not_provided"})

    commands_planned = sum(len(commands) for commands in planned.values())
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": status,
        "exit_code": exit_code,
        "dry_run": not confirm_mutation,
        "confirmation": {
            "flag": MUTATION_CONFIRMATION_FLAG,
            "confirmed": bool(confirm_mutation),
        },
        "target": target_record,
        "hap": hap_record,
        "bundle_name": bundle_name,
        "ability": ability,
        "toolchain": toolchain_record,
        "command_timeout_seconds": timeout_seconds,
        "device_access": {
            "target_explicit": bool(target is not None and str(target).strip()),
            "discovery_used": False,
            "all_devices_forbidden": True,
            "device_list_cross_checked": bool(
                resolved_target is not None and resolved_target.validated
            ),
            "commands_planned": commands_planned,
            "commands_attempted": commands_attempted,
            "commands_executed": commands_executed,
            "hardware_touched": hardware_touched or commands_executed > 0,
            "plan_only": not confirm_mutation,
        },
        "mutation_performed": mutation_performed,
        "steps": steps,
        "layout": layout_record,
        "cleanup": cleanup,
        "not_run": [
            item["name"] for item in steps
            if item["status"] == STEP_STATUS_NOT_RUN
        ],
        "device_side_state": {
            "bundle_uninstalled": bool(
                mutation_performed
                and cleanup["attempted"]
                and cleanup["status"] == STEP_STATUS_OK
            ),
            "layout_dump_removed": False,
            "layout_dump_remote_path_is_constant": True,
        },
        "evidence_boundaries": {
            "target_identity_confirmed": bool(
                resolved_target is not None and resolved_target.validated
            ),
            "target_recorded": TARGET_RECORDED_AS,
            "toolchain_version_recorded": False,
            "command_output_recorded": False,
            "layout_content_recorded": False,
            "device_logs_read": False,
            "signedness_verified": False,
            "runtime_state_verified": False,
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
    """One-line human summary: names, hashes and relative paths only."""
    parts = [
        f"{TOOL_NAME}: status={result['status']}",
        f"exit={result['exit_code']}",
        f"dry_run={str(result['dry_run']).lower()}",
        f"mutation_performed={str(result['mutation_performed']).lower()}",
    ]
    target_record = result.get("target")
    if target_record:
        parts.append(f"target_hash={target_record['hash']}")
        parts.append(f"target_kind={target_record['kind']}")
    hap_record = result.get("hap")
    if hap_record:
        parts.append(f"hap={hap_record['relpath']}")
        parts.append(f"size_bytes={hap_record['size_bytes']}")
        parts.append(f"sha256={hap_record['sha256']}")
    parts.append(
        f"commands_attempted={result['device_access']['commands_attempted']}"
    )
    parts.append(
        f"commands_executed={result['device_access']['commands_executed']}"
    )
    not_run = result.get("not_run") or []
    if not_run:
        parts.append(f"not_run={','.join(not_run)}")
    codes = ",".join(item["code"] for item in result["failures"])
    if codes:
        parts.append(f"failures={codes}")
    return " ".join(parts)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="device_smoke",
        description="HarmonyOS device smoke wrapper (fail-closed, "
        "plan by default).",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=DEFAULT_REPO_ROOT,
        help="Repository root (default: this checkout).",
    )
    parser.add_argument(
        "--target", required=True, default=None,
        help="Required: one explicit device target (e.g. 127.0.0.1:5555). "
        "Target discovery is never performed.",
    )
    parser.add_argument(
        "--hap", default=None,
        help="HAP to install; relative paths resolve against the repo root.",
    )
    parser.add_argument(
        "--bundle", default=None,
        help="Bundle name (default: AppScope/app.json5 bundleName).",
    )
    parser.add_argument(
        "--ability", default=DEFAULT_ABILITY,
        help=f"Ability to start (default: {DEFAULT_ABILITY}).",
    )
    parser.add_argument(
        "--hdc", default=DEFAULT_HDC,
        help="hdc executable name or path (default: hdc from PATH).",
    )
    parser.add_argument(
        "--device-id", action="append", default=None,
        help="Operator-known device identifier (repeatable). Supplied only to "
        "cross-check --target; no discovery is performed.",
    )
    parser.add_argument(
        MUTATION_CONFIRMATION_FLAG, action="store_true",
        help="Opt in to device mutation (install/start/background/uninstall). "
        "Without it the run is a plan and touches nothing.",
    )
    parser.add_argument(
        "--layout-dir", type=Path, default=None,
        help="Directory for the pulled layout dump (default: a temporary "
        "directory that is removed afterwards).",
    )
    parser.add_argument(
        "--timeout-seconds", type=float,
        default=DEFAULT_COMMAND_TIMEOUT_SECONDS,
        help="Per-command timeout (default: 120).",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress the one-line stderr summary.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    result, exit_code = run_device_smoke(
        repo_root=args.repo_root.resolve(),
        target=args.target,
        hap=args.hap,
        bundle=args.bundle,
        ability=args.ability,
        hdc=args.hdc,
        confirm_mutation=args.confirm_mutation,
        known_targets=args.device_id,
        layout_dir=args.layout_dir,
        timeout_seconds=args.timeout_seconds,
    )
    sys.stdout.write(render_json(result) + "\n")
    if not args.quiet:
        sys.stderr.write(render_summary(result) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
