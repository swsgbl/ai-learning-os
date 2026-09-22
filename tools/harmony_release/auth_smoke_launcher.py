"""M14-95: task-owned real-auth smoke launcher (smallest testable scope).

Starts the repository's real FastAPI auth backend (``AuthSmokeServer``:
isolated SQLite + synthetic user + OS-assigned loopback port), runs the
M14-89/95 authenticated-session smoke against it with
``expect_auth="on"``, and always stops ONLY its own backend in
``finally``.

Default is plan-only with zero side effects:
- no execution flag -> plan result; the server factory is never
  invoked (nothing starts);
- no mutation confirmation -> the smoke run itself stays a pure plan
  (inherited B1 contract).

Fail-closed: only the task-owned dynamic loopback base flows into the
smoke runner; the synthetic credential pair always matches the fixed
mock credentials (auth_smoke.MOCK_LOGIN_USER / MOCK_LOGIN_PASS).
Reports never carry passwords, tokens, secrets or absolute paths: the
recorded backend block is ``AuthSmokeServer.redacted_status`` minus the
absolute ``db_path`` (shown as "<task-work-dir>").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

try:
    from . import auth_smoke
except ImportError:  # direct script: python tools/harmony_release/auth_smoke_launcher.py
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import auth_smoke  # type: ignore[no-redef]


def _make_services_api_importable(root: Path) -> None:
    """Lazily put <root>/services/api on sys.path (idempotent) so the
    repository's FastAPI app is importable for real CLI execution.

    Called ONLY from the factory inside explicit execute mode; a
    plan-only import/call never mutates sys.path.
    """
    import sys

    api_dir = str(root / "services" / "api")
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)


def _auth_smoke_server_class():
    """Lazy, host-independent resolution of the real backend class."""
    from app.ops.auth_smoke_server import AuthSmokeServer
    return AuthSmokeServer

SCHEMA_VERSION = 1
TOOL_NAME = "harmony_auth_smoke_launcher"

DEFAULT_EVIDENCE_DIR = Path(".verify") / "artifacts" / \
    "m14-95-harmony-real-auth-smoke"

EXIT_OK = 0
EXIT_FAILURE = 1

REASON_SERVER_START_FAILED = "server_start_failed"
REASON_SMOKE_FAILED = "smoke_failed"

SmokeRunner = Callable[..., Tuple[dict, int]]
ServerFactory = Callable[[Path, str], object]
Clock = Callable[[], float]

# Task-owned synthetic AUTH_SECRET (>=32 bytes, M9-04 gate). It is NOT
# a credential or production secret - the mock server's own secret slot
# filled by the task. Kept as a named constant so tests can assert the
# redaction contract against it.
TASK_AUTH_SECRET = "m14-95-synthetic-task-auth-secret-00000"


def default_server_factory(work_dir: Path, db_path: str, repo_root: Path):
    """Real backend: the repository AuthSmokeServer on the task work-dir.

    The seeded username is always ``auth_smoke.MOCK_LOGIN_USER`` (the
    synthetic credential the B1 smoke types) - never the server's own
    default; the seed password is ``auth_smoke.MOCK_LOGIN_PASS``.
    """
    _make_services_api_importable(repo_root)
    server_class = _auth_smoke_server_class()
    return server_class(
        work_dir,
        username=auth_smoke.MOCK_LOGIN_USER,
        db_path=db_path or None,
    )


def redacted_backend_block(server_info: Optional[dict]) -> Dict[str, object]:
    """Redacted, absolute-path-free backend record.

    ``server_info`` is already the redacted dict from
    AuthSmokeServer.start; the only remaining absolute value there is
    ``db_path``, replaced by a location-independent placeholder. Both
    secret slots are re-stamped to placeholders so the contract holds
    even for a raw (non-redacted) info dict.
    """
    if not server_info:
        return {}
    block = {
        key: value for key, value in dict(server_info).items()
        if key != "db_path"
    }
    block["db_path"] = "<task-work-dir>"
    block["auth_secret"] = "<configured>"
    block["seed_password"] = "[REDACTED]"
    return block


def _evidence_name(result: dict) -> str:
    """Plan-only writes are ``*_plan.json``; executed writes use the
    plain name. The launcher result's own status decides: "plan" for a
    plan-only run, "executed" once an explicit execution happened (even
    if the backend start or the smoke then failed)."""
    if result.get("status") == "executed":
        return "auth_smoke_launcher.json"
    return "auth_smoke_launcher_plan.json"


def _write_evidence(
    result: dict, evidence_dir: Path, root: Path
) -> None:
    """Write the JSON evidence; never fails the run, never leaks an
    absolute path into the report (the recorded name is bare)."""
    try:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        name = _evidence_name(result)
        (evidence_dir / name).write_text(
            json.dumps(
                result, indent=2, ensure_ascii=False, sort_keys=True
            ) + "\n",
            encoding="utf-8",
        )
        result["evidence_file"] = name
    except (OSError, TypeError, ValueError):
        result["evidence_error"] = "evidence_write_failed"


def launch_auth_smoke(
    repo_root: Path,
    *,
    hap: Optional[str] = None,
    target: Optional[str] = None,
    work_dir: Optional[Path] = None,
    evidence_dir: Optional[Path] = None,
    expect_auth: str = "on",
    execute: bool = False,
    confirm_mutation: bool = False,
    server_factory: Optional[ServerFactory] = None,
    smoke_runner: Optional[SmokeRunner] = None,
    clock: Optional[Clock] = None,
) -> Tuple[dict, int]:
    """Run the real-auth smoke against the task-owned backend.

    ``execute`` gates backend startup and real execution; when
    executing, ``confirm_mutation`` is forwarded to the smoke runner.
    Without ``execute`` the returned result is a pure plan - the server
    factory is never invoked and the smoke (if a runner was injected)
    runs B1's confirm_mutation=False plan. Returns ``(result,
    exit_code)``; the backend is always stopped in ``finally`` (only
    the task-owned one).
    """
    root = Path(repo_root)
    evidence = Path(evidence_dir) if evidence_dir else None
    if evidence is not None and not evidence.is_absolute():
        evidence = root / evidence

    result: Dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": "plan",
        "execute": bool(execute),
        "expect_auth": expect_auth,
        "hap": hap,
        "target": target,
        "work_dir": "<task-work-dir>" if work_dir else None,
        "evidence_dir": None,
        "backend": {},
        "smoke": None,
        "started": clock() if clock is not None else None,
        "reasons": [],
    }

    if not execute:
        # Plan-only, zero side effects: factory NEVER invoked. If a
        # smoke runner was injected, hand it B1's pure plan (mutation
        # unconfirmed) against the default loopback base.
        if smoke_runner is not None:
            smoke, _ = smoke_runner(
                root,
                target=target,
                hap=hap,
                api_base=auth_smoke.DEFAULT_API_BASE,
                expect_auth=expect_auth,
                confirm_mutation=False,
                evidence_dir=evidence,
            )
            result["smoke"] = smoke
        _write_evidence(
            result, evidence or root / DEFAULT_EVIDENCE_DIR, root)
        return result, EXIT_OK

    # ----- real mode: start the task-owned backend -----------------
    # Launcher status: an explicit execution is "executed" even when the
    # backend start or the smoke fails (B1's smoke result keeps its own
    # status under result["smoke"]).
    result["status"] = "executed"
    factory = server_factory or default_server_factory
    try:
        server = factory(
            Path(work_dir) if work_dir else root,
            "m14-95-auth-smoke.db", root)
    except BaseException as exc:
        # Factory failed: nothing was created by this launcher, so
        # there is nothing to stop.
        result["reasons"].append({
            "code": REASON_SERVER_START_FAILED,
            "detail": {"error_kind": type(exc).__name__},
        })
        _write_evidence(
            result, evidence or root / DEFAULT_EVIDENCE_DIR, root)
        return result, EXIT_FAILURE
    try:
        try:
            info = server.start(TASK_AUTH_SECRET,
                                auth_smoke.MOCK_LOGIN_PASS)
            result["backend"] = redacted_backend_block(info)
        except BaseException as exc:
            # start failed: stop() still attempted below; no smoke.
            result["reasons"].append({
                "code": REASON_SERVER_START_FAILED,
                "detail": {"error_kind": type(exc).__name__},
            })
            _write_evidence(
                result, evidence or root / DEFAULT_EVIDENCE_DIR, root)
            return result, EXIT_FAILURE

        api_base = str(info.get("base_url")) + "/"
        runner = smoke_runner
        if runner is None:
            # Default: the B1 tool (real http/hdc injection is its own
            # concern; the launcher only wires the dynamic base in).
            runner = auth_smoke.run_auth_smoke
        try:
            smoke_result, smoke_exit = runner(
                root,
                target=target,
                hap=hap,
                api_base=api_base,
                expect_auth=expect_auth,
                confirm_mutation=confirm_mutation,
                evidence_dir=evidence,
            )
            result["smoke"] = smoke_result
            if smoke_exit != EXIT_OK:
                result["reasons"].append({
                    "code": REASON_SMOKE_FAILED,
                    "detail": {"exit_code": smoke_exit},
                })
        except BaseException as exc:
            # Honest launcher failure (not propagation): a smoke runner
            # that raises is recorded as a failure with reason +
            # evidence; the backend is still stopped below.
            result["smoke"] = None
            result["reasons"].append({
                "code": "smoke_runner_error",
                "detail": {"error_kind": type(exc).__name__},
            })
            smoke_exit = EXIT_FAILURE
        finally:
            # Always stop ONLY this launcher's own backend, even when
            # the smoke failed or raised (stop-on-smoke-failure).
            try:
                server.stop()
            except BaseException:
                result.setdefault("stop_errors", []).append("stop_failed")
            result["finished"] = clock() if clock is not None else None
        exit_code = EXIT_OK if smoke_exit == EXIT_OK else EXIT_FAILURE
        _write_evidence(
            result, evidence or root / DEFAULT_EVIDENCE_DIR, root)
        return result, exit_code
    except BaseException:
        # Unexpected control-flow error inside the smoke block: still
        # attempt to stop only our own backend, then propagate.
        try:
            server.stop()
        except BaseException:
            result.setdefault("stop_errors", []).append("stop_failed")
        raise


def main(argv: Optional[list] = None) -> int:
    """Minimal CLI: ``--execute`` is the explicit execution flag; without
    it everything is a plan (no backend, no smoke)."""
    import argparse

    parser = argparse.ArgumentParser(
        description="M14-95 real-auth smoke launcher (plan-only default)")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--hap", default=None)
    parser.add_argument("--target", default=None)
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--evidence-dir", default=None)
    parser.add_argument("--expect-auth", choices=["off", "on"],
                        default="on")
    parser.add_argument("--execute", action="store_true",
                        help="start the task-owned backend and run the "
                             "smoke (default: plan only, no side effects)")
    parser.add_argument(auth_smoke.MUTATION_CONFIRMATION_FLAG,
                        action="store_true",
                        help="opt in to device mutation (effective only "
                             "with --execute)")
    ns = parser.parse_args(argv)
    confirm_attr = \
        auth_smoke.MUTATION_CONFIRMATION_FLAG.lstrip("-").replace("-", "_")
    result, exit_code = launch_auth_smoke(
        Path(ns.repo_root),
        hap=ns.hap,
        target=ns.target,
        work_dir=Path(ns.work_dir) if ns.work_dir else None,
        evidence_dir=Path(ns.evidence_dir) if ns.evidence_dir else None,
        expect_auth=ns.expect_auth,
        execute=ns.execute,
        confirm_mutation=bool(getattr(ns, confirm_attr, False)),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    import sys
    sys.exit(main())
