"""Focused contract tests for the M14-142 repeat-cycle wrapper.

All tests use a fake runner and a temporary repo tree: no subprocess is
spawned (except the whitelist probes, which are pure filesystem checks),
no device is touched, no HTTP request is made. The fake runner replays
canned child CLI stdout JSON so the aggregation logic is exercised for
real without any emulator or backend.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.harmony_release.backend_smoke_repeat import (
    DEFAULT_CYCLES,
    EXIT_BLOCKED,
    EXIT_FAILURE,
    EXIT_OK,
    MAX_CYCLES,
    MIN_CYCLES,
    TOOL_NAME,
    atomic_write_text,
    render_markdown,
    run_backend_smoke_repeat,
    sanitize_path,
    sanitized_argv,
    validate_cycles,
)

CHILD_TOOL = "harmony_backend_smoke"


# ------------------------------------------------------------------ fakes ---

class FakeOutcome:
    def __init__(self, returncode=0, stdout=None):
        self.returncode = returncode
        # Default stdout is one honest ok child record (a "lie" must be
        # constructed explicitly).
        self.stdout_text = ok_stdout() if stdout is None else stdout
        self.timed_out = False
        self.spawn_error = None


def ok_stdout(status="ok", **extra):
    payload = {
        "schema_version": 1,
        "tool": CHILD_TOOL,
        "status": status,
        "exit_code": 0 if status == "ok" else 1,
        "mutation_performed": True,
        "failures": [],
    }
    payload.update(extra)
    return json.dumps(payload)


def make_fake_runner(results, *, calls=None):
    """Results: list of FakeOutcome (or int returncode) per spawn."""

    def runner(argv, cwd, env, timeout):
        if calls is not None:
            calls.append(list(argv))
        if not results:
            raise AssertionError("unexpected extra subprocess spawn")
        head = results.pop(0)
        if isinstance(head, int):
            return FakeOutcome(returncode=head, stdout=ok_stdout())
        return head

    return runner


def make_repo(tmp_path: Path) -> Path:
    """Minimal temporary repo containing the whitelisted child CLI file."""
    child = tmp_path / "tools" / "harmony_release" / "backend_smoke.py"
    child.parent.mkdir(parents=True, exist_ok=True)
    child.write_text("# fake child CLI\n", encoding="utf-8")
    return tmp_path


def recording_writer(writes=None, *, fail_on=None):
    def writer(path: Path, text: str):
        if writes is not None:
            writes.append(Path(path).name)
        if fail_on is not None and Path(path).name == fail_on:
            raise OSError("disk full (test)")
        atomic_write_text(path, text)

    return writer


# ------------------------------------------------------------ cycle bounds --

class TestCycleBounds:
    @pytest.mark.parametrize("bad", [0, -1, MAX_CYCLES + 1, 100])
    def test_out_of_range_blocks_with_zero_subprocesses(self, tmp_path,
                                                        bad):
        repo = make_repo(tmp_path)
        calls: list = []
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=bad,
            runner=make_fake_runner([], calls=calls),
            writer=recording_writer(),
        )
        assert code == EXIT_BLOCKED
        assert result["status"] == "blocked"
        assert result["cycles_executed"] == 0
        assert calls == []
        codes = {f["code"] for f in result["failures"]}
        assert "cycles_out_of_range" in codes

    def test_non_int_rejected(self, tmp_path):
        repo = make_repo(tmp_path)
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles="3",
            runner=make_fake_runner([]), writer=recording_writer(),
        )
        assert code == EXIT_BLOCKED
        assert "cycles_out_of_range" in {f["code"]
                                         for f in result["failures"]}

    def test_bool_rejected_even_if_in_range(self, tmp_path):
        repo = make_repo(tmp_path)
        result, code = validate_and_run(repo, True)
        assert code == EXIT_BLOCKED

    def test_bounds_constants(self):
        assert MIN_CYCLES == 1
        assert MAX_CYCLES == 5
        assert DEFAULT_CYCLES == 1

    def test_exact_bounds_accepted(self, tmp_path):
        repo = make_repo(tmp_path)
        for good in (MIN_CYCLES, MAX_CYCLES):
            result, code = run_backend_smoke_repeat(
                repo, target="127.0.0.1:5555", cycles=good,
                confirm_mutation=True,
                runner=make_fake_runner([FakeOutcome()
                                         for _ in range(good)]),
                writer=recording_writer(),
            )
            assert code == EXIT_OK
            assert result["cycles_executed"] == good


def validate_and_run(repo, cycles):
    return run_backend_smoke_repeat(
        repo, target="127.0.0.1:5555", cycles=cycles,
        runner=make_fake_runner([]), writer=recording_writer(),
    )


# ------------------------------------------------------------ plan mode -----

class TestPlanMode:
    def test_plan_spawns_zero_subprocesses(self, tmp_path):
        repo = make_repo(tmp_path)
        calls: list = []
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=3,
            runner=make_fake_runner([], calls=calls),
            writer=recording_writer(),
        )
        assert code == EXIT_OK
        assert result["status"] == "planned"
        assert result["subprocesses_spawned"] == 0
        assert calls == []
        assert len(result["cycles"]) == 3
        assert all(c["status"] == "planned" for c in result["cycles"])
        assert result["confirmation"]["confirmed"] is False

    def test_plan_records_sanitized_argv_only(self, tmp_path):
        repo = make_repo(tmp_path)
        result, _ = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=1, hap="some.hap",
            runner=make_fake_runner([]), writer=recording_writer(),
        )
        argv = result["cycles"][0]["argv"]
        assert "--target" in argv
        assert "127.0.0.1:5555" not in argv
        assert "some.hap" not in argv


# --------------------------------------------------- successful cycles ------

class TestSuccessfulCycles:
    def test_three_ok_cycles(self, tmp_path):
        repo = make_repo(tmp_path)
        calls: list = []
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=3,
            confirm_mutation=True,
            runner=make_fake_runner([FakeOutcome() for _ in range(3)],
                                    calls=calls),
            writer=recording_writer(),
        )
        assert code == EXIT_OK
        assert result["status"] == "ok"
        assert result["cycles_executed"] == 3
        assert result["subprocesses_spawned"] == 3
        assert result["fail_stopped_at"] is None
        assert result["failures"] == []
        # sequential order + per-cycle evidence subdirectories
        assert [c["index"] for c in result["cycles"]] == [1, 2, 3]
        assert [c["evidence_dir"] for c in result["cycles"]] == [
            "cycle_1", "cycle_2", "cycle_3"]
        # each child got its own cycle dir and the repo root as cwd
        assert len(calls) == 3
        for index, argv in enumerate(calls, start=1):
            evidence_arg = argv[argv.index("--evidence-dir") + 1]
            assert evidence_arg.replace("\\", "/").endswith(
                f"cycle_{index}")
        # child facts propagated
        assert all(c["child_status"] == "ok" for c in result["cycles"])
        assert all(c["returncode"] == 0 for c in result["cycles"])
        assert all(c["child_mutation_performed"] is True
                   for c in result["cycles"])
        # timing recorded and plausible
        assert all(c["seconds"] >= 0 for c in result["cycles"])


# ---------------------------------------------------------- fail-stop -------

class TestFailStop:
    def test_second_cycle_nonzero_stops_the_run(self, tmp_path):
        repo = make_repo(tmp_path)
        runner = make_fake_runner([
            FakeOutcome(),
            FakeOutcome(returncode=1,
                        stdout=ok_stdout(status="failure",
                                         exit_code=1,
                                         failures=[{"code": "x"}])),
            FakeOutcome(),  # must never be consumed
        ])
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=3,
            confirm_mutation=True, runner=runner,
            writer=recording_writer(),
        )
        assert code == EXIT_FAILURE
        assert result["status"] == "failure"
        assert result["fail_stopped_at"] == 2
        assert result["cycles_executed"] == 2
        assert result["subprocesses_spawned"] == 2
        statuses = [c["status"] for c in result["cycles"]]
        assert statuses[:2] == ["ok", "failure"]
        assert statuses[2:] == ["planned"] * (len(statuses) - 2)
        # later cycles are planned, not run: exactly one "planned" entry
        # closes the sequence after the fail-stop.
        assert result["cycles"][2]["status"] == "planned"
        codes = {f["code"] for f in result["failures"]}
        assert "cycle_failed" in codes
        # a partial run is never a success
        assert result["exit_code"] == EXIT_FAILURE

    def test_runner_exception_fail_stops_with_planned_tail(self, tmp_path):
        """Regression (supervisor R1): a runner exception on cycle 1
        must fail-stop the run instead of falling through to outcome
        parsing (which used to raise UnboundLocalError on ``outcome``).
        The call itself not raising is the "no wrapper exception" assert.
        """
        repo = make_repo(tmp_path)

        def raising_runner(argv, cwd, env, timeout):
            raise RuntimeError("runner exploded (test)")

        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=3,
            confirm_mutation=True, runner=raising_runner,
            writer=recording_writer(),
        )
        assert code == EXIT_FAILURE
        assert result["status"] == "failure"
        assert result["exit_code"] == EXIT_FAILURE
        assert result["cycles_executed"] == 1
        assert result["subprocesses_spawned"] == 1
        assert result["fail_stopped_at"] == 1
        assert [c["status"] for c in result["cycles"]] == [
            "failure", "planned", "planned"]
        codes = {f["code"] for f in result["failures"]}
        assert "child_runner_error" in codes
        assert result["cycles"][0]["reason"] == "child_runner_error"


# ------------------------------------------------- malformed child payload --

class TestMalformedChild:
    @pytest.mark.parametrize("stdout", [
        "",
        "not json at all",
        "[1, 2, 3]",
        "null",
    ])
    def test_malformed_child_is_failure(self, tmp_path, stdout):
        repo = make_repo(tmp_path)
        runner = make_fake_runner([FakeOutcome(stdout=stdout)])
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=2,
            confirm_mutation=True, runner=runner,
            writer=recording_writer(),
        )
        assert code == EXIT_FAILURE
        assert result["fail_stopped_at"] == 1
        assert result["cycles_executed"] == 1
        codes = {f["code"] for f in result["failures"]}
        assert "child_result_malformed" in codes
        assert result["cycles"][1]["status"] == "planned"

    def test_json_with_trailing_noise_still_parses(self, tmp_path):
        repo = make_repo(tmp_path)
        runner = make_fake_runner([
            FakeOutcome(stdout=ok_stdout() + "\ntrailing noise")])
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=1,
            confirm_mutation=True, runner=runner,
            writer=recording_writer(),
        )
        assert code == EXIT_OK
        assert result["cycles"][0]["status"] == "ok"

    def test_timeout_is_failure_fail_stop(self, tmp_path):
        repo = make_repo(tmp_path)
        outcome = FakeOutcome()
        outcome.timed_out = True
        runner = make_fake_runner([outcome])
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=2,
            confirm_mutation=True, runner=runner,
            writer=recording_writer(),
        )
        assert code == EXIT_FAILURE
        codes = {f["code"] for f in result["failures"]}
        assert "child_timeout" in codes
        assert result["fail_stopped_at"] == 1


# ------------------------------------------------------------ whitelist -----

class TestWhitelist:
    def test_missing_child_blocks_before_any_spawn(self, tmp_path):
        repo = tmp_path  # no tools/harmony_release/backend_smoke.py
        calls: list = []
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=1,
            confirm_mutation=True,
            runner=make_fake_runner([], calls=calls),
            writer=recording_writer(),
        )
        assert code == EXIT_BLOCKED
        assert result["subprocesses_spawned"] == 0
        assert calls == []
        codes = {f["code"] for f in result["failures"]}
        assert "child_not_whitelisted" in codes

    def test_injected_resolver_rejection_blocks(self, tmp_path):
        repo = make_repo(tmp_path)
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=1,
            confirm_mutation=True,
            child_resolver=lambda root: (None, [{
                "code": "child_not_whitelisted", "detail": {}}]),
            runner=make_fake_runner([]), writer=recording_writer(),
        )
        assert code == EXIT_BLOCKED

    def test_child_argv_never_uses_a_shell(self, tmp_path):
        repo = make_repo(tmp_path)
        calls: list = []
        run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=1,
            confirm_mutation=True,
            runner=make_fake_runner([FakeOutcome()], calls=calls),
            writer=recording_writer(),
        )
        assert len(calls) == 1
        argv = calls[0]
        assert argv[0].lower().endswith(("python.exe", "python",
                                         "python3", "py"))
        # structural: the child path is the repo's own backend_smoke.py
        child = argv[argv.index("-B") + 1].replace("\\", "/")
        assert child.endswith("tools/harmony_release/backend_smoke.py")
        assert "-c" not in argv and "/c" not in argv
        assert not any("cmd" in part.lower() and part.lower().endswith(
            (".exe",)) for part in argv)


# -------------------------------------------------- reports + atomicity ----

class TestReports:
    def test_reports_written_atomically_json_then_md(self, tmp_path):
        repo = make_repo(tmp_path)
        evidence = tmp_path / "ev"
        writes: list = []
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=1,
            confirm_mutation=True,
            runner=make_fake_runner([FakeOutcome()]),
            writer=recording_writer(writes),
            evidence_dir=evidence,
        )
        assert code == EXIT_OK
        assert writes == ["backend_smoke_repeat.json",
                          "backend_smoke_repeat.md"]
        payload = json.loads(
            (evidence / "backend_smoke_repeat.json").read_text("utf-8"))
        assert payload["tool"] == TOOL_NAME
        md = (evidence / "backend_smoke_repeat.md").read_text("utf-8")
        assert "cycle" in md and "cycle_1" in md

    def test_write_failure_fails_run_closed(self, tmp_path):
        repo = make_repo(tmp_path)
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=1,
            confirm_mutation=True,
            runner=make_fake_runner([FakeOutcome()]),
            writer=recording_writer(fail_on="backend_smoke_repeat.md"),
            evidence_dir=tmp_path / "ev",
        )
        assert code == EXIT_FAILURE
        assert result["status"] == "failure"
        codes = {f["code"] for f in result["failures"]}
        assert "report_write_failed" in codes
        # the *aggregated* exit code may not be ok even though the only
        # executed cycle succeeded: report failure is a run failure.
        assert result["exit_code"] == EXIT_FAILURE

    def test_no_temp_files_left_behind(self, tmp_path):
        repo = make_repo(tmp_path)
        evidence = tmp_path / "ev"
        run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=1,
            confirm_mutation=True,
            runner=make_fake_runner([FakeOutcome()]),
            evidence_dir=evidence,
        )
        leftovers = [p.name for p in evidence.iterdir()
                     if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_evidence_dir_created_when_missing(self, tmp_path):
        repo = make_repo(tmp_path)
        evidence = tmp_path / "deep" / "nested" / "ev"
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=1,
            runner=make_fake_runner([]),
            writer=recording_writer(), evidence_dir=evidence,
        )
        assert code == EXIT_OK
        assert evidence.is_dir()


# ----------------------------------------------------- sanitized output -----

class TestSanitization:
    def test_absolute_paths_placeholdered(self):
        text = r"D:\some\host\path\file.json"
        assert "D:" not in sanitize_path(text)
        assert r"\some" not in sanitize_path(text)

    def test_relative_names_survive(self):
        assert sanitize_path("cycle_1") == "cycle_1"

    def test_sanitized_argv_drops_values_and_unknown_tokens(self):
        argv = [sys_exec(), "--repo-root", r"C:\repo",
                "--target", "127.0.0.1:5555", "--cycles", "3",
                "--confirm-mutation", "--quiet", "--evil=1", "positional"]
        out = sanitized_argv(argv)
        assert "--target" in out and "--confirm-mutation" in out
        assert "127.0.0.1:5555" not in out
        assert "--evil" not in out
        assert "<value>" in out  # unknown tokens replaced, never echoed

    def test_result_blob_has_no_abs_paths_or_secrets(self, tmp_path):
        repo = make_repo(tmp_path)
        result, _ = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=1,
            confirm_mutation=True,
            runner=make_fake_runner([FakeOutcome()]),
            writer=recording_writer(), evidence_dir=tmp_path / "ev",
        )
        blob = json.dumps(result)
        assert str(tmp_path).replace("\\", "/") not in blob.replace("\\", "/")
        assert "127.0.0.1:5555" not in blob
        for secret_name in ("TOKEN", "SECRET", "PASSWORD", "KEY"):
            for match in (f'"{secret_name}', f"{secret_name}="):
                assert match not in blob.upper() or secret_name in (
                    "KEY",)  # only structural keys allowed

    def test_render_markdown_has_no_abs_paths(self, tmp_path):
        repo = make_repo(tmp_path)
        result, _ = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=1,
            confirm_mutation=True,
            runner=make_fake_runner([FakeOutcome()]),
            writer=recording_writer(), evidence_dir=tmp_path / "ev",
        )
        md = render_markdown(result)
        assert str(tmp_path) not in md
        assert "cycle_1" in md


def sys_exec():
    return "C:/Python/python.exe"


# ------------------------------------------------------- aggregate exit ----

class TestAggregateExitCodes:
    def test_plan_exit_zero(self, tmp_path):
        repo = make_repo(tmp_path)
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=2,
            runner=make_fake_runner([]), writer=recording_writer(),
        )
        assert (code, result["status"]) == (EXIT_OK, "planned")

    def test_all_ok_exit_zero(self, tmp_path):
        repo = make_repo(tmp_path)
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=2,
            confirm_mutation=True,
            runner=make_fake_runner([FakeOutcome(), FakeOutcome()]),
            writer=recording_writer(),
        )
        assert (code, result["status"]) == (EXIT_OK, "ok")

    def test_any_failure_exit_one(self, tmp_path):
        repo = make_repo(tmp_path)
        runner = make_fake_runner([FakeOutcome(), 1])
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=2,
            confirm_mutation=True, runner=runner,
            writer=recording_writer(),
        )
        assert (code, result["status"]) == (EXIT_FAILURE, "failure")

    def test_blocked_exit_two(self, tmp_path):
        repo = tmp_path  # child CLI missing -> whitelist rejection
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=1,
            runner=make_fake_runner([]), writer=recording_writer(),
        )
        assert (code, result["status"]) == (EXIT_BLOCKED, "blocked")

    def test_unusable_evidence_dir_blocks(self, tmp_path):
        repo = make_repo(tmp_path)
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        result, code = run_backend_smoke_repeat(
            repo, target="127.0.0.1:5555", cycles=1,
            evidence_dir=blocker,
            runner=make_fake_runner([]), writer=recording_writer(),
        )
        assert code == EXIT_BLOCKED
        assert "evidence_dir_unusable" in {
            f["code"] for f in result["failures"]}


# --------------------------------------------------------- unit helpers ----

class TestUnits:
    def test_validate_cycles(self):
        assert validate_cycles(1) == (1, [])
        assert validate_cycles(5) == (5, [])
        assert validate_cycles(0)[0] is None
        assert validate_cycles(6)[0] is None
        assert validate_cycles(True)[0] is None
        assert validate_cycles("3")[0] is None

    def test_atomic_write_replaces_not_appends(self, tmp_path):
        target = tmp_path / "x.json"
        atomic_write_text(target, "first")
        atomic_write_text(target, "second")
        assert target.read_text("utf-8") == "second"
