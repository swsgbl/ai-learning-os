"""Repeat-cycle wrapper around the backend-smoke CLI (M14-142 Stage 1).

Runs the repository's existing backend-smoke tool (``backend_smoke.py``,
M14-84) as a **subprocess**, N times (1..5), and aggregates the cycles into
one JSON + one Markdown report. It contains no UI-driver logic of its own:
the child CLI stays the single owner of the hdc/uitest/HTTP logic, and this
wrapper only sequences, times, sanitizes and aggregates.

Contract (Stage 1; real emulator execution is Stage 2):

- Structural whitelist: the only spawnable program is the repository's own
  ``tools/harmony_release/backend_smoke.py`` executed through the current
  Python interpreter with ``-B -m`` style semantics resolved to a real file
  path *inside the repository*. No shell (``shell=False`` by construction -
  argv list), no ``cwd`` guessing, nothing else may ever be spawned.
- Plan mode (no ``--confirm-mutation`` forwarded) spawns **zero**
  subprocesses; the aggregate is a pure plan.
- Mutation mode runs cycles sequentially and **fail-stops** at the first
  cycle whose child exits nonzero: later cycles are planned, not run.
- Every cycle gets its own evidence subdirectory; the aggregate JSON and
  Markdown reports are written **atomically** (temp file + ``os.replace``)
  and a write error fails the run closed.
- Recorded per cycle: sequence number, sanitized argv (option names and
  operand *names* only - never values, never the target, never the HAP
  path), return code, wall-clock seconds, and relative evidence references.
- Fail-closed on a malformed child payload (unparseable/non-dict child
  stdout JSON): the cycle is a failure, never silently dropped.
- Never recorded: secrets, environment values, absolute host paths (the
  repo root appears only as ``<repo-root>``).

Exit codes: 0 = all executed cycles ok (or plan), 1 = any cycle failed or
the report could not be written, 2 = blocked (bad cycles/whitelist/evidence
dir - nothing spawned).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

try:  # package import (pytest, python -m)
    from tools.harmony_release.device_smoke import (
        DEFAULT_REPO_ROOT,
        EXIT_BLOCKED,
        EXIT_FAILURE,
        EXIT_OK,
        MUTATION_CONFIRMATION_FLAG,
    )
except ImportError:  # direct script: python tools/harmony_release/backend_smoke_repeat.py
    from device_smoke import (  # type: ignore[no-redef]
        DEFAULT_REPO_ROOT,
        EXIT_BLOCKED,
        EXIT_FAILURE,
        EXIT_OK,
        MUTATION_CONFIRMATION_FLAG,
    )

SCHEMA_VERSION = 1
TOOL_NAME = "harmony_backend_smoke_repeat"

# Repeat-cycle bounds: at least one cycle, at most five.
MIN_CYCLES = 1
MAX_CYCLES = 5
DEFAULT_CYCLES = 1

# The only spawnable child: the repository backend_smoke CLI, resolved
# structurally (repo-root-relative constant). No shell, no arbitrary path.
CHILD_RELPATH = Path("tools/harmony_release/backend_smoke.py")
CHILD_TOOL_NAME = "harmony_backend_smoke"

# Subprocess budget per child (plan or execute alike): generous for a full
# install->UI->home->uninstall cycle on a slow emulator.
DEFAULT_CHILD_TIMEOUT_SECONDS = 1800.0

REASON_REQUEST_INVALID = "request_invalid"
REASON_MUTATION_NOT_CONFIRMED = "mutation_not_confirmed"
REASON_CYCLES_OUT_OF_RANGE = "cycles_out_of_range"
REASON_CHILD_NOT_WHITELISTED = "child_not_whitelisted"
REASON_EVIDENCE_DIR_UNUSABLE = "evidence_dir_unusable"
REASON_REPORT_WRITE_FAILED = "report_write_failed"
REASON_CHILD_MALFORMED = "child_result_malformed"
REASON_CYCLE_FAILED = "cycle_failed"
REASON_FAIL_STOP = "fail_stopped_after_failure"

STATUS_PLANNED = "planned"
STATUS_OK = "ok"
STATUS_FAILURE = "failure"
STATUS_BLOCKED = "blocked"

REPO_ROOT_PLACEHOLDER = "<repo-root>"

# Cycle evidence subdirectory name pattern: cycle_1 .. cycle_5.
CYCLE_DIR_TEMPLATE = "cycle_{index}"

# Sanitized child argv: only option names and operand *names* are recorded.
# Values (target string, HAP path, api base, bundle, ability) are dropped.
SANITIZED_ARGV = (
    "--repo-root",
    "--target",
    "--hap",
    "--bundle",
    "--ability",
    "--hdc",
    "--api-base",
    "--device-id",
    "--evidence-dir",
    "--timeout-seconds",
    "--quiet",
    MUTATION_CONFIRMATION_FLAG,
)

Runner = Callable[
    [Sequence[str], Path, Dict[str, str], float],
    "ChildOutcome",
]


class ChildOutcome:
    """Outcome of one child subprocess (never carries child stdout)."""

    __slots__ = ("returncode", "stdout_text", "timed_out", "spawn_error")

    def __init__(
        self,
        returncode: Optional[int],
        stdout_text: str = "",
        timed_out: bool = False,
        spawn_error: Optional[str] = None,
    ) -> None:
        self.returncode = returncode
        self.stdout_text = stdout_text
        self.timed_out = timed_out
        self.spawn_error = spawn_error


def real_runner(argv: Sequence[str], cwd: Path, env: Dict[str, str],
                timeout_seconds: float) -> ChildOutcome:
    """Real subprocess runner: argv list, no shell, captured output."""
    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return ChildOutcome(None, timed_out=True)
    except OSError as exc:
        return ChildOutcome(None, spawn_error=type(exc).__name__)
    return ChildOutcome(
        completed.returncode,
        stdout_text=completed.stdout or "",
    )


def _child_env() -> Dict[str, str]:
    """Child environment: the host env is passed through untouched.

    Secrets are never *recorded* by this wrapper; the child CLI owns its
    own env scrubbing for its grandchildren (hdc). Only PYTHONIOENCODING
    is pinned so child stdout always parses as utf-8 JSON.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def resolve_child(repo_root: Path) -> Tuple[Optional[Path], List[dict]]:
    """Resolve the whitelisted child CLI path inside the repository."""
    root = Path(repo_root).resolve()
    candidate = root / CHILD_RELPATH
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None, [{
            "code": REASON_CHILD_NOT_WHITELISTED,
            "detail": {
                "child": CHILD_RELPATH.as_posix(),
                "reason": "unresolvable",
            },
        }]
    root_resolved = root
    if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
        return None, [{
            "code": REASON_CHILD_NOT_WHITELISTED,
            "detail": {
                "child": CHILD_RELPATH.as_posix(),
                "reason": "outside_repository",
            },
        }]
    if not resolved.is_file():
        return None, [{
            "code": REASON_CHILD_NOT_WHITELISTED,
            "detail": {
                "child": CHILD_RELPATH.as_posix(),
                "reason": "not_a_file",
            },
        }]
    return resolved, []


def sanitize_path(value: str) -> str:
    """Replace any absolute host path prefix with a placeholder."""
    text = str(value)
    # Absolute windows/unix paths -> placeholder stem (keep the basename).
    text = re.sub(
        r"(?:[A-Za-z]:)?(?:[\\/][^\s\\/]*)+", REPO_ROOT_PLACEHOLDER, text
    )
    return text


def sanitized_argv(argv: Sequence[str]) -> List[str]:
    """Option names only: every value token is dropped.

    ``--flag value`` and ``--flag=value`` both collapse to the flag name;
    unknown tokens are replaced with ``<value>`` (fail-closed recording:
    never echo an unclassified token that could carry a secret).
    """
    out: List[str] = []
    known = set(SANITIZED_ARGV)
    skip_next = False
    for token in argv[1:]:  # argv[0] is the interpreter/program: dropped
        if skip_next:
            skip_next = False
            continue
        if token in known:
            out.append(token)
            if token not in (MUTATION_CONFIRMATION_FLAG, "--quiet"):
                head, sep, _tail = token.partition("=")
                if not sep:
                    skip_next = True
            continue
        head, sep, _tail = token.partition("=")
        if head in known and sep:
            out.append(head)
            continue
        out.append("<value>")
    return out


def validate_cycles(raw: object) -> Tuple[Optional[int], List[dict]]:
    """Cycles must be an int within 1..5 (bool excluded)."""
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None, [{
            "code": REASON_CYCLES_OUT_OF_RANGE,
            "detail": {"argument": "--cycles", "value_type":
                       type(raw).__name__},
        }]
    if not (MIN_CYCLES <= raw <= MAX_CYCLES):
        return None, [{
            "code": REASON_CYCLES_OUT_OF_RANGE,
            "detail": {
                "argument": "--cycles",
                "min": MIN_CYCLES,
                "max": MAX_CYCLES,
                "actual": raw,
            },
        }]
    return raw, []


def build_child_argv(
    child_path: Path,
    repo_root: Path,
    *,
    target: str,
    hap: Optional[str],
    bundle: Optional[str],
    ability: str,
    hdc: str,
    api_base: str,
    evidence_dir: Path,
    timeout_seconds: float,
    confirm_mutation: bool,
) -> List[str]:
    """The exact child argv (values live here; only names are recorded)."""
    argv: List[str] = [sys.executable, "-B", str(child_path)]
    argv += ["--repo-root", str(repo_root)]
    argv += ["--target", target]
    if hap is not None:
        argv += ["--hap", hap]
    if bundle is not None:
        argv += ["--bundle", bundle]
    argv += ["--ability", ability]
    argv += ["--hdc", hdc]
    argv += ["--api-base", api_base]
    argv += ["--evidence-dir", str(evidence_dir)]
    argv += ["--timeout-seconds", str(timeout_seconds)]
    argv += ["--quiet"]
    if confirm_mutation:
        argv.append(MUTATION_CONFIRMATION_FLAG)
    return argv


def _parse_child_json(stdout_text: str) -> Tuple[Optional[dict], List[dict]]:
    """Parse the child's stdout JSON (its own last line is the record)."""
    text = str(stdout_text or "").strip()
    if not text:
        return None, [{"code": REASON_CHILD_MALFORMED,
                       "detail": {"reason": "empty_stdout"}}]
    # The child writes exactly one JSON document; be honest about trailing
    # whitespace noise by taking the outermost JSON object only.
    try:
        parsed = json.loads(text)
    except ValueError:
        # Tolerate trailing non-JSON lines: parse the first {...} block.
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None, [{"code": REASON_CHILD_MALFORMED,
                           "detail": {"reason": "not_json"}}]
        try:
            parsed = json.loads(text[start:end + 1])
        except ValueError:
            return None, [{"code": REASON_CHILD_MALFORMED,
                           "detail": {"reason": "not_json"}}]
    if not isinstance(parsed, dict):
        return None, [{"code": REASON_CHILD_MALFORMED,
                   "detail": {"reason": "not_an_object"}}]
    return parsed, []


def atomic_write_text(path: Path, text: str) -> None:
    """Atomic text write: same-directory temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def render_markdown(result: dict) -> str:
    """Aggregate Markdown report (relative names only, ASCII-safe pathless)."""
    lines: List[str] = []
    lines.append(f"# {TOOL_NAME} aggregate report")
    lines.append("")
    lines.append(f"- status: {result['status']}")
    lines.append(f"- exit_code: {result['exit_code']}")
    lines.append(f"- cycles_requested: {result['cycles_requested']}")
    lines.append(f"- cycles_executed: {result['cycles_executed']}")
    lines.append(f"- confirm_mutation: "
                 f"{str(result['confirmation']['confirmed']).lower()}")
    if result.get("failures"):
        for item in result["failures"]:
            lines.append(f"- failure: {item['code']}")
    lines.append("")
    lines.append("| cycle | child_status | exit_code | seconds | evidence |")
    lines.append("|-------|--------------|-----------|---------|----------|")
    for cycle in result["cycles"]:
        lines.append(
            f"| {cycle['index']} | {cycle.get('child_status') if cycle.get('child_status') is not None else '-'} "
            f"| {cycle['returncode'] if cycle['returncode'] is not None else '-'} "
            f"| {cycle['seconds']:.2f} "
            f"| {cycle['evidence_dir']} |"
        )
    lines.append("")
    return "\n".join(lines)


def run_backend_smoke_repeat(
    repo_root: Path,
    *,
    target: str,
    cycles: int = DEFAULT_CYCLES,
    hap: Optional[str] = None,
    bundle: Optional[str] = None,
    ability: str = "EntryAbility",
    hdc: str = "hdc",
    api_base: str = "http://127.0.0.1:8000/",
    confirm_mutation: bool = False,
    evidence_dir: Optional[Path] = None,
    child_timeout_seconds: float = DEFAULT_CHILD_TIMEOUT_SECONDS,
    timeout_seconds: float = 120.0,
    runner: Optional[Runner] = None,
    child_resolver: Optional[Callable[[Path], Tuple[Optional[Path],
                                                       List[dict]]]] = None,
    writer: Optional[Callable[[Path, str], None]] = None,
) -> Tuple[dict, int]:
    """Plan (default) or execute N sequential backend-smoke cycles."""
    root = Path(repo_root).resolve()
    request_failures: List[dict] = []
    cycles_records: List[dict] = []
    run_cycles = 0
    child_executed = 0
    fail_stopped_at: Optional[int] = None

    # 1) Request validation - cycles, evidence dir, whitelist. Nothing is
    #    spawned when any of these fails closed.
    cycles_ok, cycle_failures = validate_cycles(cycles)
    request_failures += cycle_failures
    child_path, child_failures = (child_resolver or resolve_child)(root)
    request_failures += child_failures

    evidence = Path(evidence_dir) if evidence_dir is not None else (
        root / ".verify" / "artifacts" / "m14-142-backend-smoke-repeat"
    )
    evidence_ready = True
    try:
        evidence.mkdir(parents=True, exist_ok=True)
        if not evidence.is_dir():
            evidence_ready = False
    except OSError:
        evidence_ready = False
    if not evidence_ready:
        request_failures.append({
            "code": REASON_EVIDENCE_DIR_UNUSABLE,
            "detail": {"argument": "--evidence-dir"},
        })

    if request_failures:
        result = {
            "schema_version": SCHEMA_VERSION,
            "tool": TOOL_NAME,
            "status": STATUS_BLOCKED,
            "exit_code": EXIT_BLOCKED,
            "cycles_requested": cycles if cycles_ok is not None else None,
            "cycles_executed": 0,
            "confirmation": {
                "flag": MUTATION_CONFIRMATION_FLAG,
                "confirmed": bool(confirm_mutation),
            },
            "subprocesses_spawned": 0,
            "fail_stopped_at": None,
            "cycles": [],
            "failures": sorted(
                request_failures, key=lambda f: json.dumps(f, sort_keys=True)
            ),
        }
        return result, EXIT_BLOCKED

    assert cycles_ok is not None and child_path is not None
    run = runner or real_runner
    write = writer or atomic_write_text
    cycle_failures: List[dict] = []

    for index in range(1, cycles_ok + 1):
        cycle_dir = evidence / CYCLE_DIR_TEMPLATE.format(index=index)
        argv = build_child_argv(
            child_path, root,
            target=target, hap=hap, bundle=bundle, ability=ability,
            hdc=hdc, api_base=api_base,
            evidence_dir=cycle_dir,
            timeout_seconds=timeout_seconds,
            confirm_mutation=confirm_mutation,
        )
        if not confirm_mutation or fail_stopped_at is not None:
            planned_record = {
                "index": index,
                "status": "planned",
                "reason": (REASON_FAIL_STOP if fail_stopped_at is not None
                           else REASON_MUTATION_NOT_CONFIRMED),
                "argv": sanitized_argv(argv),
                "returncode": None,
                "seconds": 0.0,
                "evidence_dir": CYCLE_DIR_TEMPLATE.format(index=index),
                "child_status": None,
            }
            cycles_records.append(planned_record)
            continue
        run_cycles += 1
        started = time.monotonic()
        try:
            outcome = run(argv, root, _child_env(), child_timeout_seconds)
        except BaseException as exc:  # honest wrapper failure
            cycle_failures.append({
                "code": "child_runner_error",
                "detail": {"index": index,
                           "error_kind": type(exc).__name__},
            })
            fail_stopped_at = index
            cycles_records.append({
                "index": index,
                "status": "failure",
                "reason": "child_runner_error",
                "argv": sanitized_argv(argv),
                "returncode": None,
                "seconds": round(time.monotonic() - started, 3),
                "evidence_dir": CYCLE_DIR_TEMPLATE.format(index=index),
                "child_status": None,
            })
            # Fail-stop like any other cycle failure: the remaining
            # cycles are recorded as planned by the loop head. The
            # spawn attempt this cycle consumed still counts as
            # spawned (cycles_executed was already incremented).
            child_executed += 1
            continue
        elapsed = round(time.monotonic() - started, 3)
        child_executed += 1
        parsed, malformed = _parse_child_json(outcome.stdout_text)
        record: Dict[str, object] = {
            "index": index,
            "argv": sanitized_argv(argv),
            "returncode": outcome.returncode,
            "seconds": elapsed,
            "evidence_dir": CYCLE_DIR_TEMPLATE.format(index=index),
            "child_status": (parsed.get("status") if isinstance(parsed, dict)
                             else None),
            "child_exit_code": (parsed.get("exit_code")
                                if isinstance(parsed, dict) else None),
            "child_mutation_performed": (
                parsed.get("mutation_performed")
                if isinstance(parsed, dict) else None),
            "child_failures": (
                [f.get("code") for f in parsed.get("failures", [])
                 if isinstance(f, dict)]
                if isinstance(parsed, dict) else None),
        }
        failed = False
        if malformed:
            record["status"] = "failure"
            record["failures"] = malformed
            cycle_failures += [dict(f, detail={**f.get("detail", {}),
                                               "index": index})
                               for f in malformed]
            failed = True
        elif outcome.timed_out:
            record["status"] = "failure"
            record["failures"] = [{"code": "child_timeout",
                                   "detail": {"index": index}}]
            cycle_failures.append({"code": "child_timeout",
                                   "detail": {"index": index}})
            failed = True
        elif outcome.spawn_error:
            record["status"] = "failure"
            record["failures"] = [{"code": "child_spawn_failed",
                                   "detail": {"index": index,
                                              "error_kind":
                                              outcome.spawn_error}}]
            cycle_failures.append({"code": "child_spawn_failed",
                                   "detail": {"index": index,
                                              "error_kind":
                                              outcome.spawn_error}})
            failed = True
        elif outcome.returncode != 0:
            record["status"] = "failure"
            record["failures"] = [{"code": REASON_CYCLE_FAILED,
                                   "detail": {"index": index,
                                              "exit_code":
                                              outcome.returncode}}]
            cycle_failures.append({"code": REASON_CYCLE_FAILED,
                                   "detail": {"index": index,
                                              "exit_code":
                                              outcome.returncode}})
            failed = True
        else:
            record["status"] = "ok"
            record.pop("failures", None)
        cycles_records.append(record)
        if failed:
            # Fail-stop: later cycles are recorded as planned, not run
            # (the loop's fail_stopped_at branch appends them).
            fail_stopped_at = index

    # 2) Aggregate status: a partial run can never be a success.
    if fail_stopped_at is not None:
        status = STATUS_FAILURE
        exit_code = EXIT_FAILURE
    elif cycle_failures:
        status = STATUS_FAILURE
        exit_code = EXIT_FAILURE
    elif confirm_mutation:
        status = STATUS_OK
        exit_code = EXIT_OK
    else:
        status = STATUS_PLANNED
        exit_code = EXIT_OK

    result: Dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": status,
        "exit_code": exit_code,
        "cycles_requested": cycles_ok,
        "cycles_executed": run_cycles,
        "confirmation": {
            "flag": MUTATION_CONFIRMATION_FLAG,
            "confirmed": bool(confirm_mutation),
        },
        "subprocesses_spawned": child_executed,
        "fail_stopped_at": fail_stopped_at,
        "cycles": cycles_records,
        "failures": sorted(
            cycle_failures, key=lambda f: json.dumps(f, sort_keys=True)
        ),
    }

    # 3) Reports (atomic); a write error fails the run closed.
    try:
        write(evidence / "backend_smoke_repeat.json",
              json.dumps(result, indent=2, sort_keys=True,
                         ensure_ascii=True) + "\n")
        write(evidence / "backend_smoke_repeat.md", render_markdown(result))
    except OSError:
        result = dict(result)
        result["status"] = STATUS_FAILURE
        result["exit_code"] = EXIT_FAILURE
        result["failures"] = sorted(
            [*cycle_failures, {"code": REASON_REPORT_WRITE_FAILED,
                               "detail": {}}],
            key=lambda f: json.dumps(f, sort_keys=True),
        )
        return result, EXIT_FAILURE
    return result, exit_code


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backend_smoke_repeat",
        description="Repeat-cycle wrapper around the backend-smoke CLI "
        "(fail-closed, plan by default).",
    )
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--target", required=True,
                        help="One explicit device target (forwarded).")
    parser.add_argument("--cycles", type=int, default=DEFAULT_CYCLES,
                        help=f"Repeat cycles {MIN_CYCLES}..{MAX_CYCLES} "
                        f"(default {DEFAULT_CYCLES}).")
    parser.add_argument("--hap", default=None)
    parser.add_argument("--bundle", default=None)
    parser.add_argument("--ability", default="EntryAbility")
    parser.add_argument("--hdc", default="hdc")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000/")
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument("--child-timeout-seconds", type=float,
                        default=DEFAULT_CHILD_TIMEOUT_SECONDS)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument(MUTATION_CONFIRMATION_FLAG, action="store_true",
                        help="Opt in to device mutation; without it the run "
                        "is a plan that spawns zero subprocesses.")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    result, exit_code = run_backend_smoke_repeat(
        args.repo_root.resolve(),
        target=args.target,
        cycles=args.cycles,
        hap=args.hap,
        bundle=args.bundle,
        ability=args.ability,
        hdc=args.hdc,
        api_base=args.api_base,
        confirm_mutation=bool(args.confirm_mutation),
        evidence_dir=args.evidence_dir,
        child_timeout_seconds=args.child_timeout_seconds,
        timeout_seconds=args.timeout_seconds,
    )
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True,
                                ensure_ascii=True) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
