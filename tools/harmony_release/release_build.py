"""HarmonyOS release build orchestration (M13-16a slice 2).

Runs the documented release build from ``apps/harmony``:

    hvigorw clean --no-daemon
    hvigorw assembleHap --mode module -p product=default -p buildMode=release --no-daemon

and records the honest facts of that run as deterministic JSON: the command
shape, per-step exit codes, and (on success) exactly one unsigned HAP artifact
with its repo-relative path, size, SHA-256 and unsigned-name flag.

Safety contract:
- Process execution is injectable (``runner``), so unit tests never invoke
  DevEco/hvigor and never create real release artifacts.
- The JSON carries no absolute paths, no environment values, no log text and
  no secret material; the executable is recorded by basename only and env
  overrides by variable name only.
- Fail closed (exit 1) on build failure, missing artifact, ambiguous artifact,
  unreadable artifact, or an artifact that resolves outside the repository.
- ``--require-materials`` reuses the preflight material classification: absent
  materials block the run *before* any process is spawned (exit 2); present but
  invalid materials are a contract violation (exit 1). Nothing is built in
  either case.
- This module never signs anything and never infers signedness: a successful
  release-mode build is not a signed/releasable artifact.

Exit codes: 0 = ok, 1 = failure (fail-closed), 2 = blocked and materials are
required (``--require-materials`` with materials absent).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

try:  # package import (pytest, python -m)
    from tools.harmony_release.preflight import check_external_materials
except ImportError:  # direct script: python tools/harmony_release/release_build.py
    from preflight import check_external_materials

SCHEMA_VERSION = 1
TOOL_NAME = "harmony_release_build"
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]

# HarmonyOS app subdirectory inside the repository (recorded as a relative
# path only - never the absolute repository root).
HARMONY_SUBDIR = "apps/harmony"

DEFAULT_HVIGORW = "hvigorw.bat"
HVIGORW_CANDIDATES = ("hvigorw.bat", "hvigorw", "hvigorw.sh")

# Documented release build (docs/DEVELOPMENT.md, docs/evidence/m13-10-*).
CLEAN_ARGS = ("clean", "--no-daemon")
ASSEMBLE_ARGS = (
    "assembleHap",
    "--mode",
    "module",
    "-p",
    "product=default",
    "-p",
    "buildMode=release",
    "--no-daemon",
)

# Artifact discovery root, relative to the harmony app directory.
OUTPUT_RELPATH = Path("entry/build/default/outputs/default")
UNSIGNED_HAP_GLOB = "*-unsigned.hap"
EXPECTED_ARTIFACT_NAME = "entry-default-unsigned.hap"
SUCCESS_MARKER = "BUILD SUCCESSFUL"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_BLOCKED_MATERIALS = 2

STATUS_OK = "ok"
STATUS_FAILURE = "failure"
STATUS_BLOCKED = "blocked_by_external_materials"


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one injected command. Never serialized into the JSON."""

    argv: Tuple[str, ...]
    cwd: Path
    returncode: int
    stdout: str = ""
    stderr: str = ""


CommandRunner = Callable[[Sequence[str], Path, Dict[str, str]], CommandResult]


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


def resolve_hvigorw(program: str = DEFAULT_HVIGORW) -> Optional[Path]:
    """Resolve the hvigor wrapper without ever executing it.

    An explicit path (any separator) must exist. A bare name is looked up on
    PATH; the alternative spellings are only tried for the default name, so a
    typo can never silently resolve to an unrelated wrapper.
    """
    candidate = Path(program)
    if candidate.parent != Path("."):
        return candidate if candidate.is_file() else None
    found = shutil.which(program)
    if found:
        return Path(found)
    if program != DEFAULT_HVIGORW:
        return None
    for name in HVIGORW_CANDIDATES:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def command_shape(program: Path, env_keys: Sequence[str]) -> dict:
    """Command shape with basenames/relative paths only (no absolute paths)."""
    return {
        "program": program.name,
        "cwd": HARMONY_SUBDIR,
        "clean_args": list(CLEAN_ARGS),
        "assemble_args": list(ASSEMBLE_ARGS),
        "env_keys": sorted(env_keys),
    }


def _sha256_upper(path: Path) -> str:
    """Streaming SHA-256, uppercase hex (same format as preflight)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def inspect_artifact(
    candidate: Path, repo_root: Path
) -> Tuple[Optional[dict], List[dict]]:
    """Record one artifact's facts, fail closed on path/read problems."""
    try:
        resolved = candidate.resolve()
        root = Path(repo_root).resolve()
    except OSError:
        return None, [{"code": "artifact_unreadable", "detail": {"error": "unresolvable_path"}}]
    if resolved != root and not resolved.is_relative_to(root):
        return None, [{"code": "artifact_outside_repository"}]
    relpath = resolved.relative_to(root).as_posix()
    try:
        size_bytes = resolved.stat().st_size
        sha256 = _sha256_upper(resolved)
    except OSError:
        return None, [{"code": "artifact_unreadable", "detail": {"relpath": relpath}}]
    record = {
        "relpath": relpath,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "filename_has_unsigned": "unsigned" in resolved.name.lower(),
        "name_matches_expected": resolved.name == EXPECTED_ARTIFACT_NAME,
        "inside_repository": True,
    }
    return record, []


def discover_unsigned_artifact(
    repo_root: Path, output_relpath: Path = OUTPUT_RELPATH
) -> Tuple[Optional[dict], List[dict]]:
    """Find exactly one unsigned HAP under the module output directory."""
    searched_rel = (Path(HARMONY_SUBDIR) / output_relpath).as_posix()
    root = Path(repo_root) / HARMONY_SUBDIR / output_relpath
    candidates = sorted(
        (path for path in root.rglob(UNSIGNED_HAP_GLOB) if path.is_file()),
        key=lambda path: path.as_posix(),
    )
    if not candidates:
        return None, [{"code": "artifact_missing", "detail": {"searched": searched_rel}}]
    if len(candidates) > 1:
        repo_resolved = Path(repo_root).resolve()
        names = sorted(
            path.resolve().relative_to(repo_resolved).as_posix()
            if path.resolve().is_relative_to(repo_resolved)
            else path.name
            for path in candidates
        )
        return None, [{
            "code": "artifact_ambiguous",
            "detail": {"count": len(candidates), "paths": names},
        }]
    return inspect_artifact(candidates[0], repo_root)


def run_release_build(
    repo_root: Path,
    *,
    runner: Optional[CommandRunner] = None,
    program: str = DEFAULT_HVIGORW,
    require_materials: bool = False,
    env_overrides: Optional[Dict[str, str]] = None,
) -> Tuple[dict, int]:
    """Orchestrate the release build; return (deterministic result, exit code).

    ``runner`` defaults to the real subprocess runner; tests inject a fake.
    ``env_overrides`` values are passed to the process but only the variable
    *names* are ever recorded.
    """
    repo_root = Path(repo_root)
    overrides = dict(env_overrides or {})
    failures: List[dict] = []
    steps: List[dict] = []
    external_materials: Optional[dict] = None
    artifact: Optional[dict] = None
    status: Optional[str] = None

    # 1) Material gate - strictly before any process execution.
    if require_materials:
        external_materials, material_failures = check_external_materials(repo_root)
        if material_failures:
            # Present-but-invalid materials are a contract violation.
            failures += material_failures
            status = STATUS_FAILURE
        elif not external_materials["all_present"]:
            status = STATUS_BLOCKED

    if status is None:
        resolved_program = resolve_hvigorw(program)
        if resolved_program is None:
            failures.append({
                "code": "hvigorw_not_found",
                "detail": {"program": Path(program).name},
            })
            status = STATUS_FAILURE
        else:
            run = runner or subprocess_runner
            harmony_dir = repo_root / HARMONY_SUBDIR
            env = dict(os.environ)
            env.update(overrides)
            for name, args in (("clean", CLEAN_ARGS), ("assemble", ASSEMBLE_ARGS)):
                argv = [str(resolved_program), *args]
                try:
                    outcome = run(argv, harmony_dir, env)
                except OSError:
                    failures.append({"code": f"{name}_failed", "detail": {"error": "spawn_failed"}})
                    break
                steps.append({
                    "name": name,
                    "exit_code": int(outcome.returncode),
                    "success_marker": SUCCESS_MARKER in (outcome.stdout or ""),
                })
                if outcome.returncode != 0:
                    failures.append({
                        "code": f"{name}_failed",
                        "detail": {"exit_code": int(outcome.returncode)},
                    })
                    break
            if not failures:
                artifact, artifact_failures = discover_unsigned_artifact(repo_root)
                failures += artifact_failures
            if not failures:
                status = STATUS_OK
            else:
                status = STATUS_FAILURE

    failures.sort(key=lambda item: json.dumps(item, sort_keys=True))
    shape = command_shape(Path(program), list(overrides))
    exit_code = EXIT_FAILURE if failures else EXIT_OK
    if exit_code == EXIT_OK and status == STATUS_BLOCKED:
        exit_code = EXIT_BLOCKED_MATERIALS

    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": status,
        "exit_code": exit_code,
        "require_materials": require_materials,
        "command": shape,
        "steps": steps,
        "artifact": artifact,
        "external_materials": external_materials,
        "failures": failures,
    }
    return result, exit_code


def render_json(result: dict) -> str:
    """Deterministic serialization (sorted keys, ASCII-safe)."""
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True)


def render_summary(result: dict) -> str:
    """One-line human summary: repo-relative facts only, never log content."""
    parts = [f"{TOOL_NAME}: status={result['status']}", f"exit={result['exit_code']}"]
    artifact = result.get("artifact")
    if artifact:
        parts.append(f"artifact={artifact['relpath']}")
        parts.append(f"size_bytes={artifact['size_bytes']}")
        parts.append(f"sha256={artifact['sha256']}")
    codes = ",".join(item["code"] for item in result["failures"])
    if codes:
        parts.append(f"failures={codes}")
    return " ".join(parts)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="release_build",
        description="HarmonyOS release build orchestration (fail-closed).",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=DEFAULT_REPO_ROOT,
        help="Repository root (default: this checkout).",
    )
    parser.add_argument(
        "--hvigorw", default=DEFAULT_HVIGORW,
        help="hvigor wrapper name or path (default: hvigorw.bat from PATH).",
    )
    parser.add_argument(
        "--require-materials", action="store_true",
        help="Exit 2 without building when AIOS_HARMONY_* materials are absent.",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress the one-line stderr summary.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    result, exit_code = run_release_build(
        repo_root=args.repo_root.resolve(),
        program=args.hvigorw,
        require_materials=args.require_materials,
    )
    sys.stdout.write(render_json(result) + "\n")
    if not args.quiet:
        sys.stderr.write(render_summary(result) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
