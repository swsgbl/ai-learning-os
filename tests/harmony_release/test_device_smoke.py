"""Tests for tools.harmony_release.device_smoke (device smoke wrapper).

Every test injects a fake process runner, a fake tool resolver, a fake target
resolver and/or a fake bundle resolver and uses placeholder files inside
temporary directories: hdc is never executed, no process is spawned, no device
is contacted, nothing is installed, started, stopped or uninstalled, no real
HAP is read and no device log, environment value or secret is touched.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import tools.harmony_release.device_smoke as device_smoke_module
from tools.harmony_release.device_smoke import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    EXIT_BLOCKED,
    EXIT_FAILURE,
    EXIT_OK,
    FORBIDDEN_TARGETS,
    LOCAL_LAYOUT_FILENAME,
    MUTATION_CONFIRMATION_FLAG,
    SCRUBBED_ENV_VARS,
    STEP_BACKGROUND,
    STEP_INSTALL,
    STEP_LAYOUT,
    STEP_STATUS_FAILURE,
    STEP_STATUS_NOT_RUN,
    STEP_STATUS_OK,
    STEP_START,
    STEP_UNINSTALL,
    STATUS_BLOCKED,
    STATUS_FAILURE,
    STATUS_OK,
    STATUS_PLANNED,
    TOOL_NAME,
    CommandResult,
    HdcTool,
    ResolvedTarget,
    build_step_commands,
    child_env,
    inspect_layout,
    main,
    parse_args,
    render_json,
    render_summary,
    resolve_bundle,
    resolve_target,
    run_device_smoke,
)

REPO_SCOPE_RELPATH = Path("apps/harmony/AppScope/app.json5")
HAP_RELPATH = Path(
    "apps/harmony/entry/build/default/outputs/default/entry-default-signed.hap"
)
HAP_BYTES = b"fake-hap-bytes-not-a-real-artifact"
BUNDLE_NAME = "com.example.device-smoke-test"
TARGET = "127.0.0.1:5599"
DEVICE_IDS = ["127.0.0.1:5599", "127.0.0.1:5600"]

# Markers that must never reach the JSON, the summary or an error message.
STDOUT_MARKER = "stdout-marker-must-never-appear"
STDERR_MARKER = "stderr-marker-must-never-appear"
LAYOUT_CONTENT_MARKER = "layout-content-marker-must-never-appear"
TOOL_DIR_MARKER = "secret-tool-dir-must-never-appear"

FAKE_TOOL_PATH = Path(TOOL_DIR_MARKER) / "hdc.exe"

LAYOUT_PAYLOAD = {
    "attributes": {"text": LAYOUT_CONTENT_MARKER},
    "children": [{"attributes": {"text": "child"}}],
}


def _subcommand(argv) -> str:
    """Reconstruct the documented subcommand from a real argv."""
    rest = list(argv)[3:]  # drop program, -t, target
    if not rest:
        return ""
    if rest[0] == "shell":
        rest = rest[1:]
        if rest[0] == "aa":
            return f"shell aa {rest[1]}"
        if rest[0] == "uitest":
            return f"shell uitest {rest[1]}"
        return f"shell {rest[0]}"
    if rest[0] == "file":
        return f"file {rest[1]}"
    return rest[0]


class FakeRunner:
    """Records argv; never spawns a process. stdout/stderr carry markers."""

    def __init__(self, exit_codes=None, exc=None, layout_payload=None):
        self.calls = []
        self.envs = []
        self.exit_codes = dict(exit_codes or {})
        self.exc = dict(exc or {})
        self.layout_payload = layout_payload

    def __call__(self, argv, cwd, env):
        argv = list(argv)
        self.calls.append(argv)
        self.envs.append(dict(env))
        name = _subcommand(argv)
        if name in self.exc:
            raise self.exc[name]
        returncode = int(self.exit_codes.get(name, 0))
        if name == "file recv" and returncode == 0 and self.layout_payload is not None:
            Path(argv[-1]).write_bytes(self.layout_payload)
        return CommandResult(
            argv=tuple(argv),
            cwd=Path(cwd),
            returncode=returncode,
            stdout=STDOUT_MARKER,
            stderr=STDERR_MARKER,
        )

    @property
    def subcommands(self):
        return [_subcommand(argv) for argv in self.calls]


def fake_tool_resolver(path=FAKE_TOOL_PATH, source="path_lookup"):
    """Injectable hdc resolver: never touches PATH, never executes hdc."""

    calls = []

    def resolver(program):
        calls.append(program)
        if path is None:
            return None, [{
                "code": "hdc_not_found",
                "detail": {"source": source, "program_name": "hdc"},
            }]
        return HdcTool(Path(path), source), []

    resolver.calls = calls
    return resolver


def fake_target_resolver(target=None, failures=None):
    calls = []

    def resolver(raw_target, known_targets):
        calls.append((raw_target, known_targets))
        if failures is not None:
            return None, list(failures)
        resolved = target or ResolvedTarget(
            raw=str(raw_target),
            hash="0123456789AB",
            kind="other",
            known_list_size=0,
            validated=False,
            match=None,
        )
        return resolved, []

    resolver.calls = calls
    return resolver


def fake_bundle_resolver(name=BUNDLE_NAME, failures=None):
    calls = []

    def resolver(repo_root, bundle):
        calls.append((Path(repo_root), bundle))
        if failures is not None:
            return None, list(failures)
        return name, []

    resolver.calls = calls
    return resolver


@pytest.fixture
def repo(tmp_path):
    """A placeholder repository: app.json5 + a placeholder HAP."""
    root = tmp_path / "repo"
    scope = root / REPO_SCOPE_RELPATH
    scope.parent.mkdir(parents=True)
    scope.write_text(
        json.dumps({"app": {"bundleName": BUNDLE_NAME}}), encoding="utf-8"
    )
    hap = root / HAP_RELPATH
    hap.parent.mkdir(parents=True)
    hap.write_bytes(HAP_BYTES)
    return root


@pytest.fixture
def layout_dir(tmp_path):
    directory = tmp_path / "layout"
    directory.mkdir()
    return directory


def run_smoke(repo, **kwargs):
    """Run the wrapper and assert the reported exit code matches the tuple."""
    result, exit_code = run_device_smoke(repo, **kwargs)
    assert result["exit_code"] == exit_code
    return result, exit_code


def plan_kwargs(repo, **overrides):
    kwargs = {
        "target": TARGET,
        "hap": HAP_RELPATH.as_posix(),
        "bundle": BUNDLE_NAME,
        "layout_dir": None,
    }
    kwargs.update(overrides)
    return kwargs


def mutation_kwargs(repo, layout_dir, **overrides):
    kwargs = plan_kwargs(repo)
    kwargs.update({
        "confirm_mutation": True,
        "layout_dir": layout_dir,
        "runner": FakeRunner(layout_payload=json.dumps(LAYOUT_PAYLOAD).encode()),
        "tool_resolver": fake_tool_resolver(),
    })
    kwargs.update(overrides)
    return kwargs


def fresh_kwargs(layout_dir, **overrides):
    """Runner/tool seams for a mutation run with no placeholder repo."""
    kwargs = {
        "confirm_mutation": True,
        "layout_dir": layout_dir,
        "runner": FakeRunner(layout_payload=json.dumps(LAYOUT_PAYLOAD).encode()),
        "tool_resolver": fake_tool_resolver(),
    }
    kwargs.update(overrides)
    return kwargs


# --------------------------------------------------------------------------
# 1. Target validation: explicit, single, never discovered
# --------------------------------------------------------------------------


@pytest.mark.parametrize("target", [None, "", "   "])
def test_missing_or_empty_target_is_blocked(repo, target):
    result, exit_code = run_smoke(repo, **plan_kwargs(repo, target=target))
    assert result["status"] == STATUS_BLOCKED
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == ["target_required"]
    assert result["target"] is None
    assert result["not_run"] == [
        "install", "start", "layout", "background", "uninstall"
    ]


@pytest.mark.parametrize(
    "target", sorted(FORBIDDEN_TARGETS)
)
def test_all_devices_spellings_are_forbidden(repo, target):
    result, exit_code = run_smoke(repo, **plan_kwargs(repo, target=target))
    assert exit_code == EXIT_BLOCKED
    assert result["status"] == STATUS_BLOCKED
    assert [item["code"] for item in result["failures"]] == [
        "target_all_devices_forbidden"
    ]


@pytest.mark.parametrize(
    "target", ["127.0.0.1:5599 extra", "-t", " 127.0.0.1:5599 ", "-all"]
)
def test_target_must_be_a_single_bare_token(repo, target):
    result, exit_code = run_smoke(repo, **plan_kwargs(repo, target=target))
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == ["target_invalid"]


def test_empty_supplied_device_list_fails_closed(repo):
    result, exit_code = run_smoke(
        repo, **plan_kwargs(repo, known_targets=[])
    )
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == [
        "target_list_empty"
    ]


def test_unknown_target_fails_closed(repo):
    result, exit_code = run_smoke(
        repo, **plan_kwargs(repo, target="127.0.0.1:9999", known_targets=DEVICE_IDS)
    )
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == ["target_unknown"]
    assert result["failures"][0]["detail"]["known_count"] == 2


def test_ambiguous_target_prefix_fails_closed(repo):
    result, exit_code = run_smoke(
        repo,
        **plan_kwargs(
            repo, target="127.0.0.1:5", known_targets=["127.0.0.1:5599", "127.0.0.1:5600"]
        ),
    )
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == [
        "target_ambiguous"
    ]
    assert result["failures"][0]["detail"]["match_count"] == 2


def test_exact_and_prefix_matches_are_accepted(repo):
    exact, exit_code = run_smoke(
        repo, **plan_kwargs(repo, target=TARGET, known_targets=DEVICE_IDS)
    )
    assert exit_code == EXIT_OK
    assert exact["target"]["match"] == "exact"
    assert exact["target"]["validated_against_device_list"] is True
    assert exact["target"]["known_device_count"] == 2

    prefix, exit_code = run_smoke(
        repo,
        **plan_kwargs(
            repo, target="127.0.0.1:559", known_targets=DEVICE_IDS
        ),
    )
    assert exit_code == EXIT_OK
    assert prefix["target"]["match"] == "prefix"


def test_resolve_target_never_discovers():
    """The resolver is pure: no device list means no cross-check, and no run.

    The raw target is held in memory (it has to reach the child process) but
    the recorded target is a short hash only - see the leak test.
    """
    resolved, failures = resolve_target(TARGET, None)
    assert failures == []
    assert resolved.validated is False
    assert resolved.match is None
    assert resolved.kind == "loopback"
    assert resolved.raw == TARGET
    assert resolved.hash == device_smoke_module._target_hash(TARGET)
    assert len(resolved.hash) == 12
    assert resolved.hash != TARGET


# --------------------------------------------------------------------------
# 2. Dry run (default): plan only, zero device access
# --------------------------------------------------------------------------


def test_dry_run_plans_only_and_touches_nothing(repo, tmp_path):
    runner = FakeRunner()
    tool_resolver = fake_tool_resolver()
    result, exit_code = run_smoke(
        repo,
        **plan_kwargs(repo),
        runner=runner,
        tool_resolver=tool_resolver,
    )
    assert exit_code == EXIT_OK
    assert result["status"] == STATUS_PLANNED
    assert result["dry_run"] is True
    assert result["confirmation"]["confirmed"] is False
    assert result["mutation_performed"] is False
    assert runner.calls == []
    assert tool_resolver.calls == []
    assert result["toolchain"]["probed"] is False
    assert result["device_access"]["hardware_touched"] is False
    assert result["device_access"]["commands_executed"] == 0
    assert result["device_access"]["commands_planned"] == 6
    assert result["device_access"]["plan_only"] is True
    assert result["device_access"]["discovery_used"] is False
    assert result["device_access"]["all_devices_forbidden"] is True
    # Exactly what was not run is reported.
    assert result["not_run"] == [
        STEP_INSTALL, STEP_START, STEP_LAYOUT, STEP_BACKGROUND, STEP_UNINSTALL
    ]
    assert all(
        step["status"] == STEP_STATUS_NOT_RUN
        and step["reason"] == "mutation_not_confirmed"
        and step["commands_planned"] > 0
        and all(not command["executed"] for command in step["commands"])
        for step in result["steps"]
    )
    assert [item["code"] for item in result["warnings"]] == [
        "mutation_not_confirmed", "target_list_not_provided"
    ]
    assert result["cleanup"]["status"] == "not_required"
    assert result["cleanup"]["attempted"] is False
    assert result["layout"] is None
    assert str(tmp_path) not in render_json(result)


def test_dry_run_does_not_touch_hardware_even_without_seams(repo, monkeypatch):
    """No injectable seam is needed: nothing can reach a device in dry run."""

    def boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("a process must never be spawned in dry run")

    monkeypatch.setattr(device_smoke_module.subprocess, "run", boom)
    monkeypatch.setattr(device_smoke_module.shutil, "which", boom)
    result, exit_code = run_smoke(repo, **plan_kwargs(repo))
    assert exit_code == EXIT_OK
    assert result["device_access"]["commands_executed"] == 0


def test_dry_run_json_is_byte_identical_across_runs(repo):
    first, _ = run_smoke(repo, **plan_kwargs(repo))
    second, _ = run_smoke(repo, **plan_kwargs(repo))
    assert render_json(first) == render_json(second)


# --------------------------------------------------------------------------
# 3. Request validation for a mutation run
# --------------------------------------------------------------------------


def test_mutation_requires_a_hap(repo, layout_dir):
    result, exit_code = run_smoke(
        repo, **fresh_kwargs(layout_dir, target=TARGET, bundle=BUNDLE_NAME)
    )
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == ["hap_required"]
    assert result["mutation_performed"] is False


def test_hap_outside_repository_fails_closed(repo, layout_dir, tmp_path):
    outside = tmp_path / "outside.hap"
    outside.write_bytes(HAP_BYTES)
    result, exit_code = run_smoke(
        repo,
        **fresh_kwargs(
            layout_dir, target=TARGET, hap=str(outside), bundle=BUNDLE_NAME
        ),
    )
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == [
        "hap_outside_repository"
    ]


def test_missing_hap_file_fails_closed(repo, layout_dir):
    result, exit_code = run_smoke(
        repo,
        **fresh_kwargs(
            layout_dir, target=TARGET, hap="apps/harmony/nope.hap",
            bundle=BUNDLE_NAME,
        ),
    )
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == ["hap_missing"]


def test_unresolvable_bundle_fails_closed(tmp_path, layout_dir):
    empty_root = tmp_path / "no-scope"
    empty_root.mkdir()
    hap = empty_root / HAP_RELPATH
    hap.parent.mkdir(parents=True)
    hap.write_bytes(HAP_BYTES)
    result, exit_code = run_smoke(
        empty_root,
        **fresh_kwargs(
            layout_dir, target=TARGET, hap=HAP_RELPATH.as_posix()
        ),
    )
    assert exit_code == EXIT_BLOCKED
    codes = [item["code"] for item in result["failures"]]
    assert codes == ["bundle_not_resolved"]
    assert result["failures"][0]["detail"]["error"] == "app_json5_missing"


def test_default_bundle_comes_from_app_scope(repo, layout_dir):
    bundle, failures = resolve_bundle(repo)
    assert failures == []
    assert bundle == BUNDLE_NAME


def test_missing_hdc_tool_fails_closed(repo, layout_dir):
    """Real resolver, bogus program name: no process is spawned."""
    result, exit_code = run_smoke(
        repo,
        **fresh_kwargs(
            layout_dir,
            target=TARGET,
            hap=HAP_RELPATH.as_posix(),
            bundle=BUNDLE_NAME,
            hdc="aios-device-smoke-no-such-hdc",
            tool_resolver=None,
            runner=FakeRunner(),
        ),
    )
    assert exit_code == EXIT_FAILURE
    assert result["status"] == STATUS_FAILURE
    assert [item["code"] for item in result["failures"]] == ["hdc_not_found"]
    assert result["toolchain"]["probed"] is True
    assert result["toolchain"]["resolved"] is False
    assert result["not_run"] == [
        STEP_INSTALL, STEP_START, STEP_LAYOUT, STEP_BACKGROUND, STEP_UNINSTALL
    ]
    assert result["device_access"]["commands_executed"] == 0


def test_blocked_request_never_probes_toolchain(repo):
    tool_resolver = fake_tool_resolver()
    runner = FakeRunner()
    result, exit_code = run_smoke(
        repo,
        **plan_kwargs(repo, target="all"),
        confirm_mutation=True,
        runner=runner,
        tool_resolver=tool_resolver,
    )
    assert exit_code == EXIT_BLOCKED
    assert tool_resolver.calls == []
    assert runner.calls == []
    assert result["toolchain"]["probed"] is False


# --------------------------------------------------------------------------
# 4. Injected seams
# --------------------------------------------------------------------------


def test_injected_target_resolver_is_authoritative(repo, layout_dir):
    runner = FakeRunner()
    resolver = fake_target_resolver(
        failures=[{"code": "target_blocked_by_seam"}]
    )
    result, exit_code = run_smoke(
        repo,
        **fresh_kwargs(
            layout_dir, target=TARGET, hap=HAP_RELPATH.as_posix(),
            bundle=BUNDLE_NAME, runner=runner, target_resolver=resolver,
        ),
    )
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == [
        "target_blocked_by_seam"
    ]
    assert resolver.calls == [(TARGET, None)]
    assert runner.calls == []


def test_injected_bundle_resolver_is_authoritative(tmp_path, layout_dir):
    root = tmp_path / "bare-repo"
    root.mkdir()
    hap = root / HAP_RELPATH
    hap.parent.mkdir(parents=True)
    hap.write_bytes(HAP_BYTES)
    resolver = fake_bundle_resolver()
    result, exit_code = run_smoke(
        root,
        **fresh_kwargs(
            layout_dir, target=TARGET, hap=HAP_RELPATH.as_posix(),
            bundle_resolver=resolver,
        ),
    )
    assert exit_code == EXIT_OK
    assert result["bundle_name"] == BUNDLE_NAME
    assert resolver.calls == [(root, None)]


def test_injected_tool_resolver_source_is_recorded(repo, layout_dir):
    result, exit_code = run_smoke(
        repo,
        **fresh_kwargs(
            layout_dir, target=TARGET, hap=HAP_RELPATH.as_posix(),
            bundle=BUNDLE_NAME,
            tool_resolver=fake_tool_resolver(source="explicit"),
        ),
    )
    assert exit_code == EXIT_OK
    assert result["toolchain"] == {
        "probed": True,
        "program_name": "hdc",
        "resolved": True,
        "source": "explicit",
        "version_recorded": False,
    }


# --------------------------------------------------------------------------
# 5. Successful mutation run
# --------------------------------------------------------------------------


def test_happy_path_runs_every_step_in_documented_order(repo, layout_dir):
    runner = FakeRunner(layout_payload=json.dumps(LAYOUT_PAYLOAD).encode())
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    assert exit_code == EXIT_OK
    assert result["status"] == STATUS_OK
    assert result["dry_run"] is False
    assert result["mutation_performed"] is True
    assert result["device_access"]["hardware_touched"] is True
    assert result["device_access"]["commands_executed"] == 6
    assert result["not_run"] == []
    assert runner.subcommands == [
        "install", "shell aa start", "shell uitest dumpLayout", "file recv",
        "shell aa force-stop", "uninstall",
    ]
    for call in runner.calls:
        assert call[0] == str(FAKE_TOOL_PATH)
        assert call[1:3] == ["-t", TARGET]
    assert [step["name"] for step in result["steps"]] == [
        STEP_INSTALL, STEP_START, STEP_LAYOUT, STEP_BACKGROUND, STEP_UNINSTALL
    ]
    assert all(
        step["status"] == STEP_STATUS_OK for step in result["steps"]
    )
    # Recorded shapes match the argv that was really passed to the runner.
    recorded = [
        shape["subcommand"]
        for step in result["steps"]
        for shape in [command for command in step["commands"]]
    ]
    assert recorded == [
        "install", "shell aa start", "shell uitest dumpLayout", "file recv",
        "shell aa force-stop", "uninstall",
    ]
    assert result["failures"] == []
    assert result["cleanup"] == {
        "required": True, "attempted": True, "status": STEP_STATUS_OK,
        "reason": None,
    }
    assert result["device_side_state"]["bundle_uninstalled"] is True
    assert result["device_side_state"]["layout_dump_removed"] is False
    assert result["device_side_state"]["layout_dump_remote_path_is_constant"] is True


def test_successful_run_records_layout_facts_without_content(repo, layout_dir):
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir))
    assert exit_code == EXIT_OK
    layout = result["layout"]
    assert layout["parseable"] is True
    assert layout["root_type"] == "dict"
    assert layout["content_recorded"] is False
    assert layout["size_bytes"] > 0
    assert len(layout["sha256"]) == 64
    assert layout["node_count"] == 7  # root + attributes(2) + children(4)
    assert LAYOUT_CONTENT_MARKER not in render_json(result)


def test_workdir_is_removed_when_created_by_the_tool(repo, tmp_path, monkeypatch):
    created = tmp_path / "created-workdir"
    created.mkdir()

    def fake_mkdtemp(*args, **kwargs):
        return str(created)

    monkeypatch.setattr(device_smoke_module.tempfile, "mkdtemp", fake_mkdtemp)
    kwargs = mutation_kwargs(repo, None)
    kwargs["layout_dir"] = None
    result, exit_code = run_smoke(repo, **kwargs)
    assert exit_code == EXIT_OK
    assert not created.exists()
    assert str(created) not in render_json(result)


def test_provided_layout_dir_is_never_deleted(repo, layout_dir):
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir))
    assert exit_code == EXIT_OK
    assert layout_dir.is_dir()


# --------------------------------------------------------------------------
# 6. Fail-closed execution paths
# --------------------------------------------------------------------------


def test_install_failure_stops_the_run_and_skips_cleanup(repo, layout_dir):
    runner = FakeRunner(exit_codes={"install": 3})
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    assert exit_code == EXIT_FAILURE
    assert result["status"] == STATUS_FAILURE
    assert result["mutation_performed"] is False
    assert runner.subcommands == ["install"]
    assert [item["code"] for item in result["failures"]] == ["install_failed"]
    assert result["failures"][0]["detail"]["exit_code"] == 3
    by_name = {step["name"]: step for step in result["steps"]}
    assert by_name[STEP_INSTALL]["status"] == STEP_STATUS_FAILURE
    for name in (STEP_START, STEP_LAYOUT, STEP_BACKGROUND):
        assert by_name[name]["status"] == STEP_STATUS_NOT_RUN
        assert by_name[name]["reason"] == "previous_step_failed"
    assert by_name[STEP_UNINSTALL]["status"] == STEP_STATUS_NOT_RUN
    assert by_name[STEP_UNINSTALL]["reason"] == "install_not_successful"
    assert result["cleanup"]["required"] is False
    assert result["cleanup"]["attempted"] is False


def test_later_step_failure_still_attempts_cleanup(repo, layout_dir):
    runner = FakeRunner(
        exit_codes={"shell aa start": 1},
        layout_payload=json.dumps(LAYOUT_PAYLOAD).encode(),
    )
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    assert exit_code == EXIT_FAILURE
    assert result["mutation_performed"] is True
    assert runner.subcommands == ["install", "shell aa start", "uninstall"]
    assert result["cleanup"] == {
        "required": True, "attempted": True, "status": STEP_STATUS_OK,
        "reason": None,
    }
    assert [item["code"] for item in result["failures"]] == ["start_failed"]
    by_name = {step["name"]: step for step in result["steps"]}
    assert by_name[STEP_LAYOUT]["reason"] == "previous_step_failed"
    assert by_name[STEP_BACKGROUND]["reason"] == "previous_step_failed"


def test_cleanup_failure_is_surfaced(repo, layout_dir):
    runner = FakeRunner(
        exit_codes={"uninstall": 7},
        layout_payload=json.dumps(LAYOUT_PAYLOAD).encode(),
    )
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    assert exit_code == EXIT_FAILURE
    assert result["status"] == STATUS_FAILURE
    assert result["cleanup"] == {
        "required": True, "attempted": True, "status": STEP_STATUS_FAILURE,
        "reason": "uninstall_not_successful",
    }
    codes = [item["code"] for item in result["failures"]]
    assert "cleanup_failed" in codes
    assert "uninstall_failed" in codes
    assert result["device_side_state"]["bundle_uninstalled"] is False
    by_name = {step["name"]: step for step in result["steps"]}
    assert by_name[STEP_UNINSTALL]["status"] == STEP_STATUS_FAILURE


def test_spawn_failure_fails_closed(repo, layout_dir):
    runner = FakeRunner(exc={"install": OSError("spawn boom")})
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    assert exit_code == EXIT_FAILURE
    assert [item["code"] for item in result["failures"]] == ["install_failed"]
    assert result["failures"][0]["detail"]["error"] == "spawn_failed"
    command = result["steps"][0]["commands"][0]
    assert command["error"] == "spawn_failed"
    assert command["exit_code"] is None
    assert command["executed"] is False
    assert result["mutation_performed"] is False


def test_timeout_fails_closed(repo, layout_dir):
    runner = FakeRunner(exc={"install": subprocess.TimeoutExpired("hdc", 1)})
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    assert exit_code == EXIT_FAILURE
    assert [item["code"] for item in result["failures"]] == ["install_timeout"]
    assert result["failures"][0]["detail"]["command"] == "install"
    assert result["steps"][0]["commands"][0]["error"] == "timeout"


def test_timeout_counts_attempted_and_touches_hardware(repo, layout_dir):
    """A timeout starts the child (hardware touched) but never completes it.

    attempted counts the killed command; executed stays 0 because no exit
    code ever came back, and later steps are skipped.
    """
    runner = FakeRunner(exc={"install": subprocess.TimeoutExpired("hdc", 1)})
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    assert exit_code == EXIT_FAILURE
    access = result["device_access"]
    assert access["commands_attempted"] == 1
    assert access["commands_executed"] == 0
    assert access["hardware_touched"] is True
    install = result["steps"][0]
    assert install["commands_attempted"] == 1
    assert install["commands_executed"] == 0
    assert install["commands"][0]["executed"] is True  # started, then killed
    assert install["commands"][0]["exit_code"] is None
    assert result["mutation_performed"] is False
    assert result["cleanup"] == {
        "required": False, "attempted": False,
        "status": "not_required", "reason": "install_not_successful",
    }


def test_install_spawn_failure_counts_attempted_without_hardware(
    repo, layout_dir
):
    """A pure OSError spawn failure: the child never started at all.

    attempted counts it, executed does not, and hardware_touched stays false
    because nothing ever reached a device.
    """
    runner = FakeRunner(exc={"install": OSError("spawn boom")})
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    assert exit_code == EXIT_FAILURE
    access = result["device_access"]
    assert access["commands_attempted"] == 1
    assert access["commands_executed"] == 0
    assert access["hardware_touched"] is False
    install = result["steps"][0]
    assert install["commands_attempted"] == 1
    assert install["commands_executed"] == 0
    assert install["commands"][0]["executed"] is False
    assert install["commands"][0]["exit_code"] is None
    assert install["commands"][0]["error"] == "spawn_failed"
    assert result["mutation_performed"] is False


def test_later_spawn_failure_keeps_hardware_touched(repo, layout_dir):
    """Install succeeds, then a later command fails to spawn.

    hardware_touched stays true (the install really reached the device), the
    failed command counts as attempted but not executed, and uninstall
    cleanup is still attempted because device state was mutated.
    """
    runner = FakeRunner(
        exc={"shell aa start": OSError("spawn boom")},
        layout_payload=json.dumps(LAYOUT_PAYLOAD).encode(),
    )
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    assert exit_code == EXIT_FAILURE
    access = result["device_access"]
    assert access["commands_attempted"] == 3  # install + start(spawn) + uninstall
    assert access["commands_executed"] == 2   # install + uninstall
    assert access["hardware_touched"] is True
    by_name = {step["name"]: step for step in result["steps"]}
    start = by_name[STEP_START]
    assert start["commands_attempted"] == 1
    assert start["commands_executed"] == 0
    assert start["commands"][0]["executed"] is False
    assert start["commands"][0]["error"] == "spawn_failed"
    assert result["mutation_performed"] is True
    assert result["cleanup"] == {
        "required": True, "attempted": True, "status": STEP_STATUS_OK,
        "reason": None,
    }
    assert by_name[STEP_UNINSTALL]["status"] == STEP_STATUS_OK


def test_happy_path_counts_attempted_equal_to_executed(repo, layout_dir):
    runner = FakeRunner(layout_payload=json.dumps(LAYOUT_PAYLOAD).encode())
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    assert exit_code == EXIT_OK
    access = result["device_access"]
    assert access["commands_attempted"] == 6
    assert access["commands_executed"] == 6
    assert access["hardware_touched"] is True
    for step in result["steps"]:
        assert step["commands_attempted"] == step["commands_executed"]
        assert step["commands_attempted"] == step["commands_planned"]


def test_not_run_steps_count_zero_attempted(repo, layout_dir):
    runner = FakeRunner(exit_codes={"install": 3})
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    assert exit_code == EXIT_FAILURE
    assert result["device_access"]["commands_attempted"] == 1
    for step in result["steps"]:
        if step["status"] == STEP_STATUS_NOT_RUN:
            assert step["commands_attempted"] == 0
            assert step["commands_executed"] == 0


def test_nonzero_lifecycle_result_fails_closed(repo, layout_dir):
    runner = FakeRunner(
        exit_codes={"shell aa force-stop": 2},
        layout_payload=json.dumps(LAYOUT_PAYLOAD).encode(),
    )
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    assert exit_code == EXIT_FAILURE
    assert [item["code"] for item in result["failures"]] == ["background_failed"]
    by_name = {step["name"]: step for step in result["steps"]}
    assert by_name[STEP_BACKGROUND]["status"] == STEP_STATUS_FAILURE
    assert by_name[STEP_UNINSTALL]["status"] == STEP_STATUS_OK


def test_missing_layout_file_fails_closed(repo, layout_dir):
    runner = FakeRunner()  # successful commands, but no file is written
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    assert exit_code == EXIT_FAILURE
    assert "file recv" in runner.subcommands
    assert [item["code"] for item in result["failures"]] == [
        "layout_file_missing"
    ]
    assert result["layout"] is None


def test_invalid_layout_json_fails_closed(repo, layout_dir):
    runner = FakeRunner(layout_payload=b"{not-json")
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    assert exit_code == EXIT_FAILURE
    assert [item["code"] for item in result["failures"]] == [
        "layout_invalid_json"
    ]
    assert result["layout"] is None


@pytest.mark.parametrize(
    "payload,code",
    [(b"", "layout_file_empty"), (b"[]", None)],
)
def test_layout_file_edge_cases(repo, layout_dir, payload, code):
    runner = FakeRunner(layout_payload=payload)
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    if code is None:
        assert exit_code == EXIT_OK
        assert result["layout"]["root_type"] == "list"
        assert result["layout"]["node_count"] == 1
    else:
        assert exit_code == EXIT_FAILURE
        assert [item["code"] for item in result["failures"]] == [code]


def test_inspect_layout_rejects_unreadable_and_missing(tmp_path):
    missing, failures = inspect_layout(tmp_path / "nope.json")
    assert missing is None
    assert [item["code"] for item in failures] == ["layout_file_missing"]
    a_directory = tmp_path / "a-directory.json"
    a_directory.mkdir()
    record, failures = inspect_layout(a_directory)
    assert record is None
    assert [item["code"] for item in failures] == ["layout_file_unreadable"]


# --------------------------------------------------------------------------
# 7. Leak resistance and record shape
# --------------------------------------------------------------------------


def test_child_env_is_scrubbed_and_only_names_are_recorded(
    repo, layout_dir, monkeypatch
):
    """The child env is os.environ minus every material/credential variable.

    The scrubbed names (and their values) never reach the child; the JSON
    records the stripped *names* only, never a value.
    """
    secret_value = "secret-value-must-never-reach-child-or-json"
    for name in SCRUBBED_ENV_VARS:
        monkeypatch.setenv(name, secret_value)
    monkeypatch.setenv("AIOS_DEVICE_SMOKE_KEPT", "kept-value")
    runner = FakeRunner(layout_payload=json.dumps(LAYOUT_PAYLOAD).encode())
    result, exit_code = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    assert exit_code == EXIT_OK
    assert runner.envs, "the fake runner must have captured at least one env"
    for env in runner.envs:
        for name in SCRUBBED_ENV_VARS:
            assert name not in env
        assert env["AIOS_DEVICE_SMOKE_KEPT"] == "kept-value"
    text = render_json(result) + render_summary(result)
    assert secret_value not in text
    # Only the stripped names are recorded - as names, next to empty env_keys.
    shapes = [
        command for step in result["steps"] for command in step["commands"]
    ]
    assert shapes
    for command in shapes:
        assert command["env_keys"] == []
        assert command["stripped_env_keys"] == list(SCRUBBED_ENV_VARS)
    assert SCRUBBED_ENV_VARS == tuple(sorted(set(SCRUBBED_ENV_VARS)))


def test_scrubbed_env_vars_reuse_the_shared_constants():
    """The scrub list is exactly the shared material+credential constants."""
    from tools.harmony_release.preflight import MATERIAL_ENV_VARS
    from tools.harmony_release.sign_hap import CREDENTIAL_ENV_VARS

    assert set(SCRUBBED_ENV_VARS) == set(MATERIAL_ENV_VARS) | set(
        CREDENTIAL_ENV_VARS
    )


def test_child_env_directly_strips_every_shared_variable(monkeypatch):
    """child_env() is the same construction as verify_signature.child_env."""
    for name in SCRUBBED_ENV_VARS:
        monkeypatch.setenv(name, "x")
    env = child_env()
    for name in SCRUBBED_ENV_VARS:
        assert name not in env
    assert env == {
        key: value for key, value in os.environ.items()
        if key not in SCRUBBED_ENV_VARS
    }


def test_no_secrets_paths_output_or_device_identifiers_leak(repo, layout_dir):
    runner = FakeRunner(layout_payload=json.dumps(LAYOUT_PAYLOAD).encode())
    result, _ = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    text = render_json(result) + render_summary(result)
    for marker in (
        TARGET,
        str(repo),
        str(layout_dir),
        (repo / HAP_RELPATH).as_posix(),
        TOOL_DIR_MARKER,
        STDOUT_MARKER,
        STDERR_MARKER,
        LAYOUT_CONTENT_MARKER,
    ):
        assert marker not in text
    assert result["target"]["raw_recorded"] is False
    assert result["evidence_boundaries"]["command_output_recorded"] is False
    assert result["evidence_boundaries"]["layout_content_recorded"] is False
    assert result["evidence_boundaries"]["device_logs_read"] is False
    assert result["hap"]["signedness_verified"] is False
    assert result["device_access"]["discovery_used"] is False


def test_record_shape_and_schema(repo, layout_dir):
    result, _ = run_smoke(repo, **mutation_kwargs(repo, layout_dir))
    assert result["schema_version"] == 1
    assert result["tool"] == TOOL_NAME
    assert result["command_timeout_seconds"] == DEFAULT_COMMAND_TIMEOUT_SECONDS
    assert result["hap"]["relpath"] == HAP_RELPATH.as_posix()
    assert result["hap"]["filename_has_signed"] is True
    assert result["hap"]["filename_has_unsigned"] is False
    assert result["confirmation"] == {
        "flag": MUTATION_CONFIRMATION_FLAG, "confirmed": True,
    }
    assert set(result["evidence_boundaries"]) == {
        "target_identity_confirmed", "target_recorded",
        "toolchain_version_recorded", "command_output_recorded",
        "layout_content_recorded", "device_logs_read", "signedness_verified",
        "runtime_state_verified", "lifecycle_evidence",
    }
    assert result["evidence_boundaries"]["lifecycle_evidence"] == (
        "exit_codes_only"
    )
    assert result["evidence_boundaries"]["runtime_state_verified"] is False
    for step in result["steps"]:
        assert step["status"] in {
            STEP_STATUS_OK, STEP_STATUS_FAILURE, STEP_STATUS_NOT_RUN
        }
        for command in step["commands"]:
            assert command["target_recorded"] is False
            assert command["target_option"] == "-t"
            assert command["env_keys"] == []


def test_failures_are_sorted_deterministically(repo, layout_dir):
    runner = FakeRunner(
        exit_codes={"shell aa start": 1, "uninstall": 5},
        layout_payload=json.dumps(LAYOUT_PAYLOAD).encode(),
    )
    result, _ = run_smoke(repo, **mutation_kwargs(repo, layout_dir, runner=runner))
    codes = [item["code"] for item in result["failures"]]
    assert codes == sorted(
        codes,
        key=lambda code: json.dumps(
            next(item for item in result["failures"] if item["code"] == code),
            sort_keys=True,
        ),
    )
    assert codes == ["cleanup_failed", "start_failed", "uninstall_failed"]


def test_step_shapes_do_not_carry_values():
    shapes = build_step_commands(
        STEP_UNINSTALL, "hdc", Path("/somewhere/hdc"), TARGET, None,
        BUNDLE_NAME, "EntryAbility", Path("/tmp/layout.json"),
    )
    shape = shapes[0]["shape"]
    assert shape["subcommand"] == "uninstall"
    assert shape["operand_names"] == ["bundle"]
    serialized = json.dumps(shape, sort_keys=True)
    assert TARGET not in serialized
    assert BUNDLE_NAME not in serialized
    assert "/somewhere" not in serialized


# --------------------------------------------------------------------------
# 8. CLI contract
# --------------------------------------------------------------------------


def test_cli_requires_target():
    with pytest.raises(SystemExit) as excinfo:
        parse_args([])
    assert excinfo.value.code == 2


def test_cli_mutation_is_opt_in(repo):
    args = parse_args(["--target", TARGET, "--hap", HAP_RELPATH.as_posix()])
    assert args.confirm_mutation is False
    confirmed = parse_args([
        "--target", TARGET, "--confirm-mutation",
    ])
    assert confirmed.confirm_mutation is True


def test_cli_dry_run_prints_plan_and_exits_zero(repo, capsys):
    exit_code = main([
        "--repo-root", str(repo),
        "--target", TARGET,
        "--hap", HAP_RELPATH.as_posix(),
        "--bundle", BUNDLE_NAME,
        "--quiet",
    ])
    assert exit_code == EXIT_OK
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == STATUS_PLANNED
    assert payload["tool"] == TOOL_NAME
    assert captured.err == ""
    assert TARGET not in captured.out


def test_cli_empty_target_is_blocked(repo, capsys):
    exit_code = main([
        "--repo-root", str(repo),
        "--target", " ",
        "--hap", HAP_RELPATH.as_posix(),
        "--bundle", BUNDLE_NAME,
        "--quiet",
    ])
    assert exit_code == EXIT_BLOCKED
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == STATUS_BLOCKED
    assert payload["failures"][0]["code"] == "target_required"


def test_cli_all_target_is_blocked(repo, capsys):
    exit_code = main([
        "--repo-root", str(repo), "--target", "all", "--quiet",
    ])
    assert exit_code == EXIT_BLOCKED
    payload = json.loads(capsys.readouterr().out)
    assert payload["failures"][0]["code"] == "target_all_devices_forbidden"


def test_cli_device_id_option_cross_checks(repo, capsys):
    exit_code = main([
        "--repo-root", str(repo),
        "--target", "127.0.0.1:9999",
        "--device-id", DEVICE_IDS[0],
        "--device-id", DEVICE_IDS[1],
        "--hap", HAP_RELPATH.as_posix(),
        "--bundle", BUNDLE_NAME,
        "--quiet",
    ])
    assert exit_code == EXIT_BLOCKED
    payload = json.loads(capsys.readouterr().out)
    assert payload["failures"][0]["code"] == "target_unknown"

# --------------------------------------------------------------------------
# 9. Layout directory pre-validation and honest pull verification
#    (correction for the real-emulator runs: recv exits 0 even when the
#    local directory is missing, and a stale file must never count)
# --------------------------------------------------------------------------


def test_supplied_nonexistent_layout_dir_is_created_before_device_commands(
    repo, tmp_path
):
    """Run 02 regression: a missing --layout-dir made recv exit 0 write
    nothing and the layout step fail with layout_file_missing."""
    target_dir = tmp_path / "nested" / "layout"
    assert not target_dir.exists()
    observed = []

    class ProbingRunner(FakeRunner):
        def __call__(self, argv, cwd, env):
            observed.append(("runner", target_dir.is_dir()))
            return super().__call__(argv, cwd, env)

    def probing_tool_resolver(program):
        observed.append(("tool_resolver", target_dir.is_dir()))
        return HdcTool(Path(FAKE_TOOL_PATH), "path_lookup"), []

    runner = ProbingRunner(layout_payload=json.dumps(LAYOUT_PAYLOAD).encode())
    result, exit_code = run_smoke(
        repo,
        **fresh_kwargs(
            target_dir,
            target=TARGET,
            hap=HAP_RELPATH.as_posix(),
            bundle=BUNDLE_NAME,
            runner=runner,
            tool_resolver=probing_tool_resolver,
        ),
    )
    assert exit_code == EXIT_OK
    # Created (parents included) and still there afterwards - never deleted.
    assert target_dir.is_dir()
    assert (target_dir / LOCAL_LAYOUT_FILENAME).is_file()
    # Every device-facing call saw the directory already in place.
    assert observed
    assert all(exists for _kind, exists in observed)


def test_dry_run_does_not_create_a_supplied_layout_dir(repo, tmp_path):
    """A plan touches nothing - not even the local filesystem."""
    target_dir = tmp_path / "nested" / "layout"
    result, exit_code = run_smoke(
        repo, **plan_kwargs(repo, layout_dir=target_dir)
    )
    assert exit_code == EXIT_OK
    assert result["status"] == STATUS_PLANNED
    assert not target_dir.exists()


def _layout_dir_that_is_a_regular_file(base):
    path = base / "not-a-dir"
    path.write_bytes(b"placeholder")
    return path


def _layout_dir_under_a_regular_file(base):
    parent = base / "parent-file"
    parent.write_bytes(b"placeholder")
    return parent / "layout"


@pytest.mark.parametrize(
    "path_factory,code",
    [
        (_layout_dir_that_is_a_regular_file, "layout_dir_not_a_directory"),
        (_layout_dir_under_a_regular_file, "layout_dir_create_failed"),
    ],
)
def test_unusable_layout_dir_blocks_before_any_device_command(
    repo, tmp_path, path_factory, code
):
    """A layout directory that cannot be used or created blocks the run
    before the toolchain is probed and before any device command."""
    bad_dir = path_factory(tmp_path)
    runner = FakeRunner(layout_payload=json.dumps(LAYOUT_PAYLOAD).encode())
    resolver = fake_tool_resolver()
    result, exit_code = run_smoke(
        repo,
        **fresh_kwargs(
            bad_dir,
            target=TARGET,
            hap=HAP_RELPATH.as_posix(),
            bundle=BUNDLE_NAME,
            runner=runner,
            tool_resolver=resolver,
        ),
    )
    assert exit_code == EXIT_BLOCKED
    assert result["status"] == STATUS_BLOCKED
    assert [item["code"] for item in result["failures"]] == [code]
    # Blocked before the toolchain probe and before any device command.
    assert resolver.calls == []
    assert runner.calls == []
    assert result["toolchain"]["probed"] is False
    assert result["device_access"]["commands_attempted"] == 0
    assert result["device_access"]["commands_executed"] == 0
    assert result["device_access"]["hardware_touched"] is False
    assert result["mutation_performed"] is False
    assert result["not_run"] == [
        STEP_INSTALL, STEP_START, STEP_LAYOUT, STEP_BACKGROUND, STEP_UNINSTALL
    ]
    assert all(step["reason"] == "request_invalid" for step in result["steps"])


def test_recv_exit_zero_without_a_written_file_fails_closed(repo, layout_dir):
    """recv exit 0 alone is never layout success evidence."""
    runner = FakeRunner()  # every command exits 0; nothing is ever written
    result, exit_code = run_smoke(
        repo, **mutation_kwargs(repo, layout_dir, runner=runner)
    )
    assert exit_code == EXIT_FAILURE
    by_name = {step["name"]: step for step in result["steps"]}
    recv = by_name[STEP_LAYOUT]["commands"][1]
    assert recv["subcommand"] == "file recv"
    assert recv["executed"] is True
    assert recv["exit_code"] == 0
    assert [item["code"] for item in result["failures"]] == [
        "layout_file_missing"
    ]
    assert result["layout"] is None
    # Install mutated the device, so uninstall cleanup still happened.
    assert result["cleanup"]["attempted"] is True
    assert result["cleanup"]["status"] == STEP_STATUS_OK


def test_stale_layout_file_is_not_accepted_after_a_silent_pull(
    repo, layout_dir
):
    """Run 02 shape: recv exits 0, writes nothing, a stale file exists."""
    stale = layout_dir / LOCAL_LAYOUT_FILENAME
    stale.write_bytes(json.dumps({"stale": LAYOUT_CONTENT_MARKER}).encode())
    runner = FakeRunner()  # recv exits 0 without writing anything
    result, exit_code = run_smoke(
        repo, **mutation_kwargs(repo, layout_dir, runner=runner)
    )
    assert exit_code == EXIT_FAILURE
    assert [item["code"] for item in result["failures"]] == [
        "layout_file_missing"
    ]
    assert result["layout"] is None
    # The stale file was removed before the pull and never re-created.
    assert not stale.exists()
    assert LAYOUT_CONTENT_MARKER not in render_json(result)


def test_stale_layout_file_is_not_accepted_after_a_failed_pull(
    repo, layout_dir
):
    stale = layout_dir / LOCAL_LAYOUT_FILENAME
    stale.write_bytes(b"{}")
    runner = FakeRunner(exit_codes={"file recv": 1})
    result, exit_code = run_smoke(
        repo, **mutation_kwargs(repo, layout_dir, runner=runner)
    )
    assert exit_code == EXIT_FAILURE
    assert [item["code"] for item in result["failures"]] == ["layout_failed"]
    assert result["layout"] is None
    assert not stale.exists()


def test_fresh_pull_replaces_a_stale_layout_file(repo, layout_dir):
    stale = layout_dir / LOCAL_LAYOUT_FILENAME
    stale.write_bytes(b"{}")
    runner = FakeRunner(layout_payload=json.dumps(LAYOUT_PAYLOAD).encode())
    result, exit_code = run_smoke(
        repo, **mutation_kwargs(repo, layout_dir, runner=runner)
    )
    assert exit_code == EXIT_OK
    assert result["layout"]["node_count"] == 7  # fresh payload, not stale {}
    assert (layout_dir / LOCAL_LAYOUT_FILENAME).is_file()


def test_unremovable_stale_layout_file_fails_closed(
    repo, layout_dir, monkeypatch
):
    stale = layout_dir / LOCAL_LAYOUT_FILENAME
    stale.write_bytes(json.dumps(LAYOUT_PAYLOAD).encode())

    def deny(self, missing_ok=False):
        raise OSError("unlink denied")

    monkeypatch.setattr(Path, "unlink", deny)
    runner = FakeRunner(layout_payload=json.dumps(LAYOUT_PAYLOAD).encode())
    result, exit_code = run_smoke(
        repo, **mutation_kwargs(repo, layout_dir, runner=runner)
    )
    assert exit_code == EXIT_FAILURE
    assert [item["code"] for item in result["failures"]] == [
        "layout_stale_file_unremovable"
    ]
    assert stale.exists()  # untouched
    by_name = {step["name"]: step for step in result["steps"]}
    layout = by_name[STEP_LAYOUT]
    assert layout["status"] == STEP_STATUS_FAILURE
    assert layout["commands_attempted"] == 0  # blocked before any command
    assert all(
        command["executed"] is False for command in layout["commands"]
    )
    assert by_name[STEP_BACKGROUND]["reason"] == "previous_step_failed"
    # Install already mutated the device, so cleanup still ran and succeeded.
    assert result["cleanup"]["attempted"] is True
    assert result["cleanup"]["status"] == STEP_STATUS_OK
