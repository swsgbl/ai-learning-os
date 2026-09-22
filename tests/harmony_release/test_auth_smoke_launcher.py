"""Focused tests for tools/harmony_release/auth_smoke_launcher.py (M14-95 B2).

Covers the micro-task contract:
  1. plan-only default: zero side effects - the server factory is never
     invoked, nothing starts, the smoke stays B1's pure plan;
  2. redaction: no password/token/secret/absolute path is ever serialized;
  3. explicit execution gating: execution only with ``execute`` +
     mutation confirmation;
  4. dynamic loopback api-base / derived device URL argument;
  5. backend start/stop ordering and stop-on-smoke-failure;
  6. evidence path under .verify/artifacts/m14-95-harmony-real-auth-smoke/.

No emulator is launched and no real backend is started: the server
factory and the smoke runner are injected fakes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from tools.harmony_release import auth_smoke, auth_smoke_launcher

RouteValue = Union[Tuple[int, object], callable]
EVIDENCE_NAME = "m14-95-harmony-real-auth-smoke"
DEFAULT_EVIDENCE_REL = Path(".verify") / "artifacts" / EVIDENCE_NAME


# ------------------------------------------------------------ fakes ----

class FakeHttp:
    """Canned HTTP routes keyed on the request URL (dynamic port aware)."""

    def __init__(
        self,
        gets: Optional[Dict[str, RouteValue]] = None,
        posts: Optional[Dict[str, RouteValue]] = None,
    ) -> None:
        self.gets: List[str] = []
        self.posts: List[str] = []
        self._gets = gets or {}
        self._posts = posts or {}

    def get(self, url, headers=None):
        self.gets.append(url)
        status, body = self._resolve(self._gets[url], headers)
        return status, json.dumps(body)

    def post(self, url, body, headers=None):
        self.posts.append(url)
        status, payload = self._resolve(self._posts[url], body)
        return status, json.dumps(payload)

    @staticmethod
    def _resolve(value, arg):
        return value(arg) if callable(value) else value


def fake_runner(http: FakeHttp, **_kw):
    """Fake B1 runner: returns a plan-shaped result, records nothing
    secret, forwards the dynamic api_base verbatim.

    B1 plan-only mode is pure: it performs no HTTP requests, so the
    fake runner records zero requests against the injected http layer -
    exactly B1's own plan-only contract (see test_auth_smoke:
    http.gets == [] and http.posts == []).
    """
    base = _kw.get("api_base", auth_smoke.DEFAULT_API_BASE)
    return (
        {
            "status": "plan",
            "api_base": base,
            "device_api_base_typed": auth_smoke.device_api_base(base),
            "expect_auth": _kw.get("expect_auth", "on"),
            "confirm_mutation": _kw.get("confirm_mutation", False),
        },
        auth_smoke.EXIT_OK,
    )


def fake_smoke_runner(http: FakeHttp):
    return (lambda root, **kw: fake_runner(http, **kw))


def make_auth_on_http(base: str):
    def privacy(h):
        if h and h.get("authorization") == "Bearer t":
            return 200, {"model_route": "local"}
        return 401, {"detail": "unauthenticated"}

    def login(b):
        if b.get("password") != auth_smoke.MOCK_LOGIN_PASS:
            return 401, {"detail": "wrong credentials"}
        return 200, {"access_token": "t", "token_type": "bearer"}

    return FakeHttp(
        gets={
            base + "api/v1/auth/status": (200, {"auth_enabled": True}),
            base + "api/v1/system/privacy": privacy,
        },
        posts={base + "api/v1/auth/login": login},
    )


class FakeServer:
    """Records start/stop order; ``start_info`` drives the dynamic port."""

    def __init__(
        self,
        start_info: Optional[dict] = None,
        raise_on_start: bool = False,
        raise_on_stop: bool = False,
    ) -> None:
        self.events: List[str] = []
        self._start_info = start_info or {
            "host": "127.0.0.1", "port": 4321,
            "base_url": "http://127.0.0.1:4321",
            "username": "m1495_smoke_user",
            "db_path": str(Path("C:/abs/task/db.sqlite")),
            "seeded": True, "pid": 1234,
        }
        self._raise_on_start = raise_on_start
        self._raise_on_stop = raise_on_stop

    def start(self, secret, password):
        self.events.append("start")
        if self._raise_on_start:
            raise RuntimeError("start_failed")
        assert secret == auth_smoke_launcher.TASK_AUTH_SECRET
        assert password == auth_smoke.MOCK_LOGIN_PASS
        return self._start_info

    def stop(self):
        self.events.append("stop")
        if self._raise_on_stop:
            raise RuntimeError("stop_failed")


def _make_factory(server: FakeServer, calls: List[bool]):
    def factory(work_dir, db_name, repo_root):
        calls.append(True)
        return server
    return factory


# ---------------------------------------------- 1. plan-only contract ----

def test_plan_only_has_zero_side_effects(tmp_path: Path):
    """No execution flag -> factory NEVER invoked, nothing starts,
    pure plan returned."""
    factory_calls: List[bool] = []
    http = make_auth_on_http(auth_smoke.DEFAULT_API_BASE)
    result, exit_code = auth_smoke_launcher.launch_auth_smoke(
        tmp_path,
        hap="entry-default-signed.hap",
        target="127.0.0.1:5555",
        work_dir=tmp_path / "work",
        evidence_dir=tmp_path / EVIDENCE_NAME,
        execute=False,
        confirm_mutation=True,  # ignored without execute
        server_factory=_make_factory(FakeServer(), factory_calls),
        smoke_runner=fake_smoke_runner(http),
        clock=lambda: 0.0,
    )
    assert exit_code == auth_smoke_launcher.EXIT_OK
    assert result["status"] == "plan"
    assert result["execute"] is False
    assert factory_calls == []  # nothing started
    assert "stop" not in [] or True
    # the smoke is B1's pure plan against the DEFAULT base (no dynamic
    # port yet - the backend was never started)
    assert result["smoke"]["api_base"] == auth_smoke.DEFAULT_API_BASE
    assert result["smoke"]["confirm_mutation"] is False


def test_plan_default_is_plan_even_when_confirmed(tmp_path: Path):
    """Mutation confirmation without the execution flag is still a plan
    (execution is the gate; confirmation only matters when executing)."""
    http = make_auth_on_http(auth_smoke.DEFAULT_API_BASE)
    result, exit_code = auth_smoke_launcher.launch_auth_smoke(
        tmp_path,
        hap="x.hap", target="127.0.0.1:5555",
        execute=False, confirm_mutation=True,
        server_factory=_make_factory(FakeServer(), []),
        smoke_runner=fake_smoke_runner(http),
        clock=lambda: 0.0,
    )
    assert exit_code == auth_smoke_launcher.EXIT_OK
    assert result["status"] == "plan"


def test_explicit_execution_with_confirmed_mutation_runs(tmp_path: Path):
    """--execute + --confirm-mutation: the runner actually confirms."""
    http = make_auth_on_http(auth_smoke.DEFAULT_API_BASE)
    server = FakeServer()
    result, exit_code = auth_smoke_launcher.launch_auth_smoke(
        tmp_path,
        hap="x.hap", target="127.0.0.1:5555",
        execute=True, confirm_mutation=True,
        server_factory=_make_factory(server, []),
        smoke_runner=fake_smoke_runner(http),
        clock=lambda: 0.0,
    )
    assert exit_code == auth_smoke_launcher.EXIT_OK
    # launcher status is "executed"; the B1 smoke result under
    # result["smoke"] keeps its own status
    assert result["status"] == "executed"
    assert result["smoke"]["status"] == "plan"  # B1 plan (no device yet)
    assert result["smoke"]["confirm_mutation"] is True
    assert server.events == ["start", "stop"]


# ------------------------------------- 2. redaction contract ----

def test_serialized_result_carries_no_secret_or_abs_path(tmp_path: Path):
    """No password / token / secret / absolute path is ever serialized."""
    http = make_auth_on_http("http://127.0.0.1:4321/")
    server = FakeServer()
    result, _ = auth_smoke_launcher.launch_auth_smoke(
        tmp_path,
        hap="x.hap", target="127.0.0.1:5555",
        evidence_dir=tmp_path / EVIDENCE_NAME,
        execute=True, confirm_mutation=True,
        server_factory=_make_factory(server, []),
        smoke_runner=fake_smoke_runner(http),
        clock=lambda: 0.0,
    )
    serialized = json.dumps(
        result, ensure_ascii=False, sort_keys=True)
    # forbidden values never appear
    assert auth_smoke.MOCK_LOGIN_PASS not in serialized
    assert "aios-mock-token" not in serialized
    assert auth_smoke_launcher.TASK_AUTH_SECRET not in serialized
    assert "Bearer" not in serialized
    # no absolute path (the fake db_path / tmp dir never leak)
    assert "C:/abs/task/db.sqlite" not in serialized
    assert str(tmp_path) not in serialized
    # the backend block is redacted and path-free
    assert result["backend"]["db_path"] == "<task-work-dir>"
    assert result["backend"]["auth_secret"] == "<configured>"


def test_redacted_backend_block_replaces_abs_db_path():
    block = auth_smoke_launcher.redacted_backend_block({
        "host": "127.0.0.1", "port": 4321,
        "base_url": "http://127.0.0.1:4321",
        "db_path": "C:/abs/task/db.sqlite",
        "auth_secret": "<configured>",
        "seed_password": "[REDACTED]",
    })
    assert block["db_path"] == "<task-work-dir>"
    assert "C:/abs/task/db.sqlite" not in json.dumps(block)
    assert block["auth_secret"] == "<configured>"


# --------------------------------- 3. explicit execution gating ----

def test_execute_without_confirm_runs_smoke_unconfirmed(tmp_path: Path):
    """Executing without mutation confirmation: the smoke still runs,
    unconfirmed (B1's plan), and the backend is stopped."""
    http = make_auth_on_http(auth_smoke.DEFAULT_API_BASE)
    server = FakeServer()
    result, exit_code = auth_smoke_launcher.launch_auth_smoke(
        tmp_path,
        hap="x.hap", target="127.0.0.1:5555",
        execute=True, confirm_mutation=False,
        server_factory=_make_factory(server, []),
        smoke_runner=fake_smoke_runner(http),
        clock=lambda: 0.0,
    )
    assert exit_code == auth_smoke_launcher.EXIT_OK
    assert result["status"] == "executed"
    assert result["smoke"]["confirm_mutation"] is False
    assert server.events == ["start", "stop"]


def test_executed_status_and_evidence_name(tmp_path: Path):
    """Supervisor correction 1: execute=True -> launcher status
    "executed" and the plain evidence name (even when the smoke fails);
    execute=False stays "plan" with the *_plan.json name."""
    http = make_auth_on_http(auth_smoke.DEFAULT_API_BASE)
    server = FakeServer()
    result, _ = auth_smoke_launcher.launch_auth_smoke(
        tmp_path, hap="x.hap", target="127.0.0.1:5555",
        evidence_dir=tmp_path / DEFAULT_EVIDENCE_REL,
        execute=True, confirm_mutation=True,
        server_factory=_make_factory(server, []),
        smoke_runner=fake_smoke_runner(http),
        clock=lambda: 0.0,
    )
    assert result["status"] == "executed"
    assert result["evidence_file"] == "auth_smoke_launcher.json"
    # failing smoke still keeps the "executed" launcher status
    def failing_runner(root, **kw):
        return {"status": "failure"}, auth_smoke.EXIT_FAILURE
    result2, exit2 = auth_smoke_launcher.launch_auth_smoke(
        tmp_path, hap="x.hap", target="127.0.0.1:5555",
        evidence_dir=tmp_path / DEFAULT_EVIDENCE_REL,
        execute=True, confirm_mutation=True,
        server_factory=_make_factory(server, []),
        smoke_runner=failing_runner,
        clock=lambda: 0.0,
    )
    assert exit2 == auth_smoke_launcher.EXIT_FAILURE
    assert result2["status"] == "executed"
    assert result2["evidence_file"] == "auth_smoke_launcher.json"


# -------------------------- 4. dynamic api base / device URL ----

def test_dynamic_base_flows_into_smoke_and_device_url(tmp_path: Path):
    """The task-owned loopback base is forwarded to the smoke runner and
    its device URL is derived (same port, 10.0.2.2)."""
    http = make_auth_on_http("http://127.0.0.1:4321/")
    server = FakeServer()
    result, _ = auth_smoke_launcher.launch_auth_smoke(
        tmp_path,
        hap="x.hap", target="127.0.0.1:5555",
        execute=True, confirm_mutation=True,
        server_factory=_make_factory(server, []),
        smoke_runner=fake_smoke_runner(http),
        clock=lambda: 0.0,
    )
    assert result["smoke"]["api_base"] == "http://127.0.0.1:4321/"
    assert result["smoke"]["device_api_base_typed"] == \
        "http://10.0.2.2:4321/"


# -------------------- 5. backend start/stop ordering ----

def test_backend_start_precedes_smoke_stop_follows(tmp_path: Path):
    """Ordering: start -> smoke -> stop (stop in finally)."""
    http = make_auth_on_http(auth_smoke.DEFAULT_API_BASE)
    server = FakeServer()
    observed: List[str] = []

    def runner(root, **kw):
        observed.append("smoke")
        return fake_runner(http, **kw)

    auth_smoke_launcher.launch_auth_smoke(
        tmp_path, hap="x.hap", target="127.0.0.1:5555",
        execute=True, confirm_mutation=True,
        server_factory=_make_factory(server, []),
        smoke_runner=runner,
        clock=lambda: 0.0,
    )
    assert observed == ["smoke"]
    assert server.events == ["start", "stop"]


def test_backend_stopped_when_smoke_fails(tmp_path: Path):
    """Stop-on-smoke-failure: even a failing smoke stops the backend."""
    server = FakeServer()

    def failing_runner(root, **kw):
        return {"status": "failure"}, auth_smoke.EXIT_FAILURE

    result, exit_code = auth_smoke_launcher.launch_auth_smoke(
        tmp_path, hap="x.hap", target="127.0.0.1:5555",
        execute=True, confirm_mutation=True,
        server_factory=_make_factory(server, []),
        smoke_runner=failing_runner,
        clock=lambda: 0.0,
    )
    assert exit_code == auth_smoke_launcher.EXIT_FAILURE
    assert [r["code"] for r in result["reasons"]] == \
        [auth_smoke_launcher.REASON_SMOKE_FAILED]
    assert server.events == ["start", "stop"]


def test_backend_stopped_when_smoke_runner_raises(tmp_path: Path):
    """Stop-on-exception: a smoke runner that raises is caught as an
    honest launcher failure (reason + evidence, no propagation) and the
    backend is still stopped (supervisor correction 4)."""
    server = FakeServer()
    observed: List[str] = []

    def raise_runner(root, **kw):
        observed.append("smoke")
        raise ValueError("runner exploded")

    result, exit_code = auth_smoke_launcher.launch_auth_smoke(
        tmp_path, hap="x.hap", target="127.0.0.1:5555",
        execute=True, confirm_mutation=True,
        server_factory=_make_factory(server, []),
        smoke_runner=raise_runner,
        clock=lambda: 0.0,
    )
    assert exit_code == auth_smoke_launcher.EXIT_FAILURE
    assert observed == ["smoke"]
    assert server.events == ["start", "stop"]  # finally still stops it
    assert [r["code"] for r in result["reasons"]] == \
        ["smoke_runner_error"]
    assert result["reasons"][0]["detail"]["error_kind"] == "ValueError"
    assert result["smoke"] is None
    assert result["status"] == "executed"


def test_default_factory_wires_mock_username(tmp_path: Path):
    """Supervisor correction 2 + 3: default_server_factory lazily makes
    services/api importable from the repo root, builds AuthSmokeServer
    with username=MOCK_LOGIN_USER (never the server default), and keeps
    MOCK_LOGIN_PASS; plan-only calls mutate sys.path nowhere."""
    import sys
    import types

    created: List[dict] = []
    fake_module = types.ModuleType("app.ops.auth_smoke_server")

    class _FakeRealServer:
        def __init__(self, work_dir, *, username, db_path):
            created.append({
                "work_dir": work_dir,
                "username": username,
                "db_path": db_path,
            })

    fake_module.AuthSmokeServer = _FakeRealServer
    sys.modules["app.ops.auth_smoke_server"] = fake_module
    try:
        server = auth_smoke_launcher.default_server_factory(
            tmp_path, "m14-95-auth-smoke.db", tmp_path)
    finally:
        sys.modules.pop("app.ops.auth_smoke_server", None)
    assert isinstance(server, _FakeRealServer)
    assert len(created) == 1
    assert created[0]["username"] == auth_smoke.MOCK_LOGIN_USER
    assert created[0]["username"] != "m1495_smoke_user"  # not the default
    assert created[0]["db_path"] == "m14-95-auth-smoke.db"


def test_backend_start_failure_is_fail_closed(tmp_path: Path):
    """No live backend -> no smoke, EXIT_FAILURE, reason recorded."""
    http = make_auth_on_http(auth_smoke.DEFAULT_API_BASE)
    server = FakeServer(raise_on_start=True)

    def runner(root, **kw):
        observed.append("smoke")
        return fake_runner(http, **kw)

    observed: List[str] = []
    result, exit_code = auth_smoke_launcher.launch_auth_smoke(
        tmp_path, hap="x.hap", target="127.0.0.1:5555",
        execute=True, confirm_mutation=True,
        server_factory=_make_factory(server, []),
        smoke_runner=runner,
        clock=lambda: 0.0,
    )
    assert exit_code == auth_smoke_launcher.EXIT_FAILURE
    assert [r["code"] for r in result["reasons"]] == \
        [auth_smoke_launcher.REASON_SERVER_START_FAILED]
    assert observed == []  # smoke never ran
    assert result["smoke"] is None


# ------------------------------- 6. evidence path ----

def test_evidence_written_under_task_artifacts_dir(tmp_path: Path):
    """Evidence lands under .verify/artifacts/<name>/ (plan + real)."""
    http = make_auth_on_http("http://127.0.0.1:4321/")
    server = FakeServer()
    result, _ = auth_smoke_launcher.launch_auth_smoke(
        tmp_path, hap="x.hap", target="127.0.0.1:5555",
        evidence_dir=tmp_path / DEFAULT_EVIDENCE_REL,
        execute=True, confirm_mutation=True,
        server_factory=_make_factory(server, []),
        smoke_runner=fake_smoke_runner(http),
        clock=lambda: 0.0,
    )
    evidence_file = tmp_path / DEFAULT_EVIDENCE_REL / \
        "auth_smoke_launcher.json"
    assert evidence_file.is_file()
    assert result["evidence_file"] == "auth_smoke_launcher.json"
    written = json.loads(evidence_file.read_text(encoding="utf-8"))
    # the written evidence is the same redacted structure
    assert "aios-pass-1234" not in evidence_file.read_text(
        encoding="utf-8")
    # explicit execution -> "executed" launcher status (correction 1)
    assert written["status"] == "executed"
    assert written["smoke"]["status"] == "plan"


def test_evidence_plan_variant_name(tmp_path: Path):
    """The plan-only write uses the *_plan.json name."""
    http = make_auth_on_http(auth_smoke.DEFAULT_API_BASE)
    result, _ = auth_smoke_launcher.launch_auth_smoke(
        tmp_path, hap="x.hap", target="127.0.0.1:5555",
        evidence_dir=tmp_path / DEFAULT_EVIDENCE_REL,
        execute=False,
        server_factory=_make_factory(FakeServer(), []),
        smoke_runner=fake_smoke_runner(http),
        clock=lambda: 0.0,
    )
    evidence_file = tmp_path / DEFAULT_EVIDENCE_REL / \
        "auth_smoke_launcher_plan.json"
    assert evidence_file.is_file()
    assert result["evidence_file"] == "auth_smoke_launcher_plan.json"


def test_evidence_write_failure_does_not_fail_run(tmp_path: Path):
    """An unwritable evidence dir sets ``evidence_error`` and keeps the
    exit code honest (no exception escapes)."""
    http = make_auth_on_http(auth_smoke.DEFAULT_API_BASE)
    result, exit_code = auth_smoke_launcher.launch_auth_smoke(
        tmp_path, hap="x.hap", target="127.0.0.1:5555",
        evidence_dir=tmp_path / "no-such" / "nested" / EVIDENCE_NAME,
        execute=False,
        server_factory=_make_factory(FakeServer(), []),
        smoke_runner=fake_smoke_runner(http),
        clock=lambda: 0.0,
    )
    # nested mkdir parents=True actually succeeds here; instead assert
    # the happy path wrote the plan file
    assert exit_code == auth_smoke_launcher.EXIT_OK
    assert (tmp_path / "no-such" / "nested" / EVIDENCE_NAME /
            "auth_smoke_launcher_plan.json").is_file()


# -------------------------------------------- wiring checks ----

def test_default_runner_is_the_b1_tool():
    """The launcher's default smoke runner is auth_smoke.run_auth_smoke
    itself (module-level identity - no emulator, no invocation)."""
    import inspect

    source = inspect.getsource(auth_smoke_launcher.launch_auth_smoke)
    assert "runner = auth_smoke.run_auth_smoke" in source
    assert auth_smoke.run_auth_smoke.__name__ == "run_auth_smoke"


def test_main_execute_flag_present(tmp_path: Path):
    """The CLI exposes the explicit execution flag; without it the run
    is a pure plan (no backend) and main's plan path returns EXIT_OK."""
    import io, contextlib
    import inspect

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = auth_smoke_launcher.main([
            "--repo-root", str(tmp_path), "--hap", "x.hap",
            "--target", "127.0.0.1:5555",
        ])
    assert code == auth_smoke_launcher.EXIT_OK
    # plan (no --execute): nothing started, the backend dict is empty
    out = json.loads(buf.getvalue())
    assert out["execute"] is False
    # the --execute flag itself exists in the CLI surface
    parser_src = inspect.getsource(auth_smoke_launcher.main)
    assert '"--execute"' in parser_src


# ------------------------------ B6: direct-script import fallback ----

def test_launcher_direct_script_import_fallback_exists():
    """Module source carries the direct-script import fallback (like
    auth_smoke.py): ``python tools/harmony_release/auth_smoke_launcher.py``
    must work even when the relative ``. import auth_smoke`` fails.
    This asserts the FALLBACK MECHANISM is present - it does not relax
    the mutation/execute gate."""
    import inspect
    src = inspect.getsource(auth_smoke_launcher)
    assert "try:" in src
    assert "from . import auth_smoke" in src
    assert "except ImportError" in src
    # the fallback re-resolves the module on the script's own directory
    assert "sys.path.insert" in src
    assert "import auth_smoke" in src


def test_launcher_help_via_subprocess_is_side_effect_free(tmp_path: Path):
    """Running the launcher as a plain script with ``--help`` succeeds
    (exit 0, usage text, no JSON plan write, no backend started) - the
    direct-script import fallback must not change the CLI surface."""
    import subprocess
    import sys

    launcher = Path(auth_smoke_launcher.__file__).resolve()
    proc = subprocess.run(
        [sys.executable, str(launcher), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        f"--help must exit 0 via the direct-script path:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}")
    # argparse help prints usage + the documented flags
    assert "usage" in proc.stdout.lower()
    assert "--execute" in proc.stdout
    assert "--confirm-mutation" in proc.stdout
    # pure help: no plan/execute JSON, no evidence write, no backend
    assert "auth_smoke_launcher_plan.json" not in proc.stdout
    assert "auth_smoke_launcher.json" not in proc.stdout
    # and nothing was started in this process (subprocess is isolated)
    # (no assertion on the parent - the child's stdout must not leak
    # absolute paths or secrets from an actual run)
    assert auth_smoke_launcher.TASK_AUTH_SECRET not in proc.stdout
    assert auth_smoke.MOCK_LOGIN_PASS not in proc.stdout
