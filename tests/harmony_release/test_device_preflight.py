"""Tests for tools.harmony_release.device_preflight (M13-17).

Every test fakes the process runner, the tool resolver and/or the signature
report inputs: hdc is never executed, no process is spawned, no device is
contacted, no real HAP or signing material is read, no AGC endpoint is
reached and no environment secret is touched.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

import tools.harmony_release.device_preflight as dp
from tools.harmony_release.device_preflight import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    EXIT_BLOCKED,
    EXIT_FAILURE,
    EXIT_OK,
    FORBIDDEN_TARGETS,
    MODE_CHECK,
    MODE_PLAN,
    PROBE_API_VERSION,
    PROBE_BUNDLE_QUERY,
    PROBE_DEVICE_MODEL,
    READ_ONLY_CONFIRMATION_FLAG,
    SCRUBBED_ENV_VARS,
    STATUS_BLOCKED,
    STATUS_OK,
    STATUS_PLANNED,
    STEP_GATE,
    STEP_STATUS_NOT_RUN,
    STEP_STATUS_OK,
    TOOL_NAME,
    CommandResult,
    HdcTool,
    build_probe_commands,
    is_loopback_target,
    load_signature_gate,
    main,
    parse_args,
    parse_probe_output,
    render_json,
    render_summary,
    resolve_target,
    run_device_preflight,
)

REPO_SCOPE_RELPATH = Path("apps/harmony/AppScope/app.json5")
BUNDLE_NAME = "com.example.preflight-test"
TARGET = "192.168.1.42:5555"
LOOPBACK_TARGET = "127.0.0.1:5555"

# FakeRunner dispatch keys: the full shell operand string of each probe, so
# the two `param get` probes never collide on one shared key.
SUB_API = "param get const.ohos.apiversion"
SUB_MODEL = "param get const.product.model"
SUB_BUNDLE = "bm dump -n " + BUNDLE_NAME

REPORT_TOOL = "harmony_release_verify_signature"
HAP_SHA = "A" * 64
HAP_SIZE = 21976

STDOUT_MARKER = "stdout-marker-must-never-appear"
STDERR_MARKER = "stderr-marker-must-never-appear"
TOOL_DIR_MARKER = "secret-tool-dir-must-never-appear"
FAKE_TOOL_PATH = Path(TOOL_DIR_MARKER) / "hdc.exe"
REPORT_PATH_MARKER = "secret-reports-dir-must-never-appear"


def _subcommand(argv) -> str:
    rest = list(argv)[3:]  # drop program, -t, target
    if not rest:
        return ""
    if rest[0] == "shell":
        return " ".join(rest[1:])
    return rest[0]


class FakeRunner:
    """Records argv; never spawns a process. stdout/stderr carry markers."""

    def __init__(self, exit_codes=None, exc=None, outputs=None):
        self.calls = []
        self.envs = []
        self.exit_codes = dict(exit_codes or {})
        self.exc = dict(exc or {})
        self.outputs = dict(outputs or {})

    def __call__(self, argv, cwd, env):
        argv = list(argv)
        self.calls.append(argv)
        self.envs.append(dict(env))
        name = _subcommand(argv)
        if name in self.exc:
            raise self.exc[name]
        returncode = int(self.exit_codes.get(name, 0))
        stdout = self.outputs.get(name, "")
        return CommandResult(
            argv=tuple(argv),
            cwd=Path(cwd),
            returncode=returncode,
            stdout=stdout,
            stderr=STDERR_MARKER,
        )

    @property
    def subcommands(self):
        return [_subcommand(argv) for argv in self.calls]


def fake_tool_resolver(path=FAKE_TOOL_PATH, source="path_lookup"):
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


def make_report(tmp_path, **overrides) -> Path:
    """A passing verify_signature report plus a matching .sha256 sidecar."""
    payload = {
        "tool": REPORT_TOOL,
        "status": "signed_and_valid",
        "exit_code": 0,
        "signature": {"signed": True, "verified": True},
        "input": {
            "relpath": "apps/harmony/entry/build/default/outputs/default/"
                       "entry-default-signed.hap",
            "sha256": HAP_SHA,
            "size_bytes": HAP_SIZE,
        },
    }
    payload.update(overrides)
    report = tmp_path / "verify.json"
    raw = json.dumps(payload, indent=2, sort_keys=True).encode()
    report.write_bytes(raw)
    (tmp_path / "verify.json.sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "\n", encoding="utf-8"
    )
    return report


def fake_bundle_resolver(name=BUNDLE_NAME):
    calls = []

    def resolver(repo_root, bundle):
        calls.append((Path(repo_root), bundle))
        return name, []

    resolver.calls = calls
    return resolver


def ok_outputs():
    """Passing outputs for all three probes (values never echoed)."""
    return {
        SUB_API: "12",
        SUB_MODEL: "Pura 90 Pro",
        SUB_BUNDLE: json.dumps({"bundleName": BUNDLE_NAME}),
    }


def _with_invalid(key, value):
    """All probes passing except one - so the run reaches that probe."""
    outputs = ok_outputs()
    outputs[key] = value
    return outputs


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    scope = root / REPO_SCOPE_RELPATH
    scope.parent.mkdir(parents=True)
    scope.write_text(
        json.dumps({"app": {"bundleName": BUNDLE_NAME}}), encoding="utf-8"
    )
    return root


def run(repo, **kwargs):
    result, exit_code = run_device_preflight(repo, **kwargs)
    assert result["exit_code"] == exit_code
    return result, exit_code


def check_kwargs(report, **overrides):
    """Seams for a checked run; every hdc interaction is faked."""
    kwargs = {
        "mode": MODE_CHECK,
        "target": TARGET,
        "signature_report": str(report),
        "bundle": BUNDLE_NAME,
        "confirm_read_only": True,
        "runner": FakeRunner(outputs=ok_outputs()),
        "tool_resolver": fake_tool_resolver(),
    }
    kwargs.update(overrides)
    return kwargs


# --------------------------------------------------------------------------
# 1. plan mode: pure plan, zero device access, zero filesystem reads
# --------------------------------------------------------------------------


def test_plan_never_resolves_or_spawns_or_reads(repo, tmp_path, monkeypatch):
    report = tmp_path / "not-read.json"
    report.write_text("this is never read in plan mode", encoding="utf-8")

    def boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("plan mode must never touch the real world")

    monkeypatch.setattr(dp.subprocess, "run", boom)
    monkeypatch.setattr(dp.shutil, "which", boom)
    runner = FakeRunner()
    resolver = fake_tool_resolver()
    bundle_probe = fake_bundle_resolver()

    result, exit_code = run(
        repo,
        mode=MODE_PLAN,
        target=TARGET,
        signature_report=str(report),
        runner=runner,
        tool_resolver=resolver,
        bundle_resolver=bundle_probe,
    )
    assert exit_code == EXIT_OK
    assert result["status"] == STATUS_PLANNED
    assert runner.calls == []
    assert resolver.calls == []
    assert result["toolchain"]["probed"] is False
    assert result["device_access"]["plan_only"] is True
    assert result["device_access"]["hardware_touched"] is False
    assert result["signature_gate"]["checked"] is False
    assert bundle_probe.calls == []  # AppScope/app.json5 never opened
    assert result["not_run"] == [
        STEP_GATE, PROBE_API_VERSION, PROBE_DEVICE_MODEL, PROBE_BUNDLE_QUERY
    ]
    reasons = {step["name"]: step["reason"] for step in result["steps"]}
    assert reasons == {
        STEP_GATE: "plan_mode",
        PROBE_API_VERSION: "plan_mode",
        PROBE_DEVICE_MODEL: "plan_mode",
        PROBE_BUNDLE_QUERY: "bundle_not_supplied",
    }


def test_plan_does_not_require_target_existence_or_signature_report(repo):
    result, exit_code = run(repo, mode=MODE_PLAN, target=TARGET)
    assert exit_code == EXIT_OK
    assert result["signature_gate"]["requested"] is False


def test_plan_json_is_byte_identical_across_runs(repo):
    first, _ = run(repo, mode=MODE_PLAN, target=TARGET)
    second, _ = run(repo, mode=MODE_PLAN, target=TARGET)
    assert render_json(first) == render_json(second)


def test_plan_without_bundle_never_resolves_bundle_or_reads_app_json5(repo):
    """Zero-file-read: without --bundle, plan mode never calls
    bundle_resolver (it would read AppScope/app.json5)."""
    resolver = fake_bundle_resolver()

    result, exit_code = run(
        repo, mode=MODE_PLAN, target=TARGET, bundle_resolver=resolver
    )
    assert exit_code == EXIT_OK
    assert resolver.calls == []
    assert result["bundle_name"] is None
    bundle_step = next(
        s for s in result["steps"] if s["name"] == PROBE_BUNDLE_QUERY
    )
    assert bundle_step["status"] == STEP_STATUS_NOT_RUN
    assert bundle_step["reason"] == "bundle_not_supplied"


def test_plan_with_explicit_bundle_needs_no_file_read(repo):
    """Explicit --bundle stays available in plan mode, still zero reads."""
    (repo / REPO_SCOPE_RELPATH).unlink()  # provably not needed
    resolver = fake_bundle_resolver()

    result, exit_code = run(
        repo, mode=MODE_PLAN, target=TARGET, bundle=BUNDLE_NAME,
        bundle_resolver=resolver,
    )
    assert exit_code == EXIT_OK
    assert resolver.calls == []
    assert result["bundle_name"] == BUNDLE_NAME
    bundle_step = next(
        s for s in result["steps"] if s["name"] == PROBE_BUNDLE_QUERY
    )
    assert bundle_step["reason"] == "plan_mode"
    assert bundle_step["commands"][0]["operand_names"] == ["bundle"]


# --------------------------------------------------------------------------
# 2. Target validation: explicit, single, no discovery, no loopback
# --------------------------------------------------------------------------


@pytest.mark.parametrize("target", [None, "", "   "])
def test_missing_or_empty_target_is_blocked(repo, target):
    result, exit_code = run(repo, mode=MODE_PLAN, target=target)
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == ["target_required"]


@pytest.mark.parametrize("target", sorted(FORBIDDEN_TARGETS))
def test_all_devices_spellings_are_forbidden(repo, target):
    result, exit_code = run(repo, mode=MODE_PLAN, target=target)
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == [
        "target_all_devices_forbidden"
    ]


@pytest.mark.parametrize(
    "target", ["a b", "-t", " 192.168.1.42:5555 ", "x\ty"]
)
def test_target_must_be_a_single_bare_token(repo, target):
    result, exit_code = run(repo, mode=MODE_PLAN, target=target)
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == ["target_invalid"]


@pytest.mark.parametrize(
    "target",
    [
        "127.0.0.1:5555",
        "localhost:5555",
        "LOCALHOST:5555",
        "[::1]:5555",
        "127.5.4.3:5555",
    ],
)
def test_check_mode_rejects_loopback_with_no_bypass(repo, tmp_path, target):
    report = make_report(tmp_path)
    result, exit_code = run(
        repo,
        **check_kwargs(
            report,
            target=target,
            confirm_read_only=True,  # the flag does NOT bypass loopback
        ),
    )
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == [
        "target_loopback_forbidden"
    ]
    assert result["failures"][0]["detail"]["bypass"] == "none"
    assert result["device_access"]["commands_attempted"] == 0


def test_loopback_target_is_recorded_as_loopback_kind(repo):
    result, _ = run(repo, mode=MODE_PLAN, target=LOOPBACK_TARGET)
    assert result["target"]["kind"] == "loopback"


def test_plan_mode_allows_loopback_kind_but_check_does_not(repo, tmp_path):
    report = make_report(tmp_path)
    plan, _ = run(repo, mode=MODE_PLAN, target=LOOPBACK_TARGET)
    assert plan["status"] == STATUS_PLANNED  # a plan touches nothing
    check, _ = run(
        repo, **check_kwargs(report, target=LOOPBACK_TARGET)
    )
    assert check["status"] == STATUS_BLOCKED


def test_is_loopback_target_classifies_hosts():
    for token in (
        "127.0.0.1:1", "localhost:22", "localhost", "[::1]:3", "::1",
        "127.9.9.9:9",
    ):
        assert is_loopback_target(token) is True
    for token in ("192.168.1.2:5555", "usb-serial-id"):
        assert is_loopback_target(token) is False


# --------------------------------------------------------------------------
# 3. Signature-report gate
# --------------------------------------------------------------------------


def test_check_requires_the_exact_confirmation_flag(repo, tmp_path):
    report = make_report(tmp_path)
    result, exit_code = run(
        repo,
        **check_kwargs(report, confirm_read_only=False),
    )
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == [
        "read_only_not_confirmed"
    ]
    assert result["device_access"]["commands_attempted"] == 0


def test_check_requires_a_signature_report(repo):
    result, exit_code = run(
        repo,
        **{**check_kwargs(None), "signature_report": None},
    )
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == [
        "signature_report_required"
    ]


def test_sidecar_mismatch_blocks_before_any_probe(repo, tmp_path):
    report = make_report(tmp_path)
    (tmp_path / "verify.json.sha256").write_text(
        "0" * 64 + "\n", encoding="utf-8"
    )
    result, exit_code = run(repo, **check_kwargs(report))
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == [
        "signature_report_sidecar_mismatch"
    ]
    assert result["signature_gate"]["sidecar_verified"] is None
    assert result["toolchain"]["probed"] is False


def test_missing_sidecar_blocks(repo, tmp_path):
    report = make_report(tmp_path)
    (tmp_path / "verify.json.sha256").unlink()
    result, exit_code = run(repo, **check_kwargs(report))
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == [
        "signature_report_sidecar_missing"
    ]


@pytest.mark.parametrize(
    "override,code",
    [
        ({"status": "unsigned"}, "signature_report_not_passing"),
        ({"exit_code": 1}, "signature_report_not_passing"),
        ({"signature": {"signed": True, "verified": False}},
         "signature_report_not_passing"),
        ({"signature": None}, "signature_report_not_passing"),
        ({"tool": "something_else"}, "signature_report_wrong_tool"),
        ({"input": {"sha256": "short", "size_bytes": 10}},
         "signature_report_hap_facts_invalid"),
        ({"input": {"sha256": HAP_SHA, "size_bytes": 0}},
         "signature_report_hap_facts_invalid"),
        ({"input": None}, "signature_report_hap_facts_invalid"),
    ],
)
def test_non_passing_report_blocks(repo, tmp_path, override, code):
    report = make_report(tmp_path, **override)
    result, exit_code = run(repo, **check_kwargs(report))
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == [code]


def test_non_strict_json_report_blocks(repo, tmp_path):
    raw = (
        f'{{"tool": "{REPORT_TOOL}", "status": "signed_and_valid", '
        '"exit_code": 0, "signature": {"signed": true, "verified": true}, '
        f'"input": {{"sha256": "{HAP_SHA}", "size_bytes": {HAP_SIZE}}}, '
        '"extra": NaN}'
    ).encode()
    report = tmp_path / "verify.json"
    report.write_bytes(raw)
    (tmp_path / "verify.json.sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "\n", encoding="utf-8"
    )
    result, exit_code = run(repo, **check_kwargs(report))
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == [
        "signature_report_invalid_json"
    ]


def test_uppercase_hex_digest_is_accepted(repo, tmp_path):
    """The sidecar digest may be upper- or lowercase hex."""
    report = make_report(tmp_path)
    (tmp_path / "verify.json.sha256").write_text(
        (tmp_path / "verify.json.sha256").read_text().upper(),
        encoding="utf-8",
    )
    _, exit_code = run(repo, **check_kwargs(report))
    assert exit_code == EXIT_OK


def test_gate_holds_a_concrete_hap_hash_and_size(repo, tmp_path):
    report = make_report(tmp_path)
    result, _ = run(repo, **check_kwargs(report))
    hap = result["signature_gate"]["hap"]
    assert hap["sha256"] == HAP_SHA
    assert hap["size_bytes"] == HAP_SIZE
    assert result["signature_gate"]["sidecar_verified"] is True
    assert len(result["signature_gate"]["report_sha256"]) == 64


def test_gate_failure_precedes_toolchain_probe(repo, tmp_path):
    report = make_report(tmp_path, status="unsigned")
    resolver = fake_tool_resolver()
    runner = FakeRunner()
    result, _ = run(
        repo, **check_kwargs(report, tool_resolver=resolver, runner=runner)
    )
    assert resolver.calls == []
    assert runner.calls == []
    assert result["toolchain"]["probed"] is False


# --------------------------------------------------------------------------
# 4. Command-shape allowlist and the no-mutation source contract
# --------------------------------------------------------------------------


def test_probe_commands_are_exactly_the_allowlisted_shapes():
    commands = [
        item
        for name, _phase in dp.PROBE_ORDER
        for item in build_probe_commands(
            name, "hdc", Path("/somewhere/hdc"), TARGET, BUNDLE_NAME
        )
    ]
    assert len(commands) == 3
    serialized = json.dumps([item["shape"] for item in commands])
    for forbidden in (TARGET, BUNDLE_NAME, "/somewhere"):
        assert forbidden not in serialized
    for item in commands:
        assert item["shape"]["read_only"] is True
        assert item["shape"]["target_option"] == "-t"
        assert item["shape"]["target_recorded"] is False
    argv_subcommands = [" ".join(item["argv"][3:5]) for item in commands]
    assert argv_subcommands == [
        "shell param", "shell param", "shell bm"
    ]
    # The only shell operands are param get / bm dump (full tail).
    assert commands[0]["argv"][3:] == [
        "shell", "param", "get", "const.ohos.apiversion"
    ]
    assert commands[1]["argv"][3:] == [
        "shell", "param", "get", "const.product.model"
    ]
    assert commands[2]["argv"][3:] == [
        "shell", "bm", "dump", "-n", BUNDLE_NAME
    ]


def test_source_contains_no_mutating_or_discovery_commands():
    """The module cannot build any mutating hdc command: no code path."""
    source = Path(dp.__file__).read_text(encoding="utf-8")
    for forbidden in (
        '"install"', '"uninstall"', '"aa start"', '"force-stop"',
        '"hilog"', '"shell aa ', '"wipe"', '"reboot"', '"list targets"',
    ):
        assert forbidden not in source, forbidden
    # The runner's stdout is captured but never recorded or echoed.
    assert '"stdout"' not in source


def test_checked_run_uses_only_allowlisted_subcommands(repo, tmp_path):
    report = make_report(tmp_path)
    runner = FakeRunner(outputs=ok_outputs())
    result, exit_code = run(repo, **check_kwargs(report, runner=runner))
    assert exit_code == EXIT_OK
    assert runner.subcommands == [SUB_API, SUB_MODEL, SUB_BUNDLE]
    for call in runner.calls:
        assert call[0] == str(FAKE_TOOL_PATH)
        assert call[1:3] == ["-t", TARGET]
    assert result["device_access"]["commands_executed"] == 3
    assert result["device_access"]["mutation_performed"] is False
    assert result["probes"][PROBE_API_VERSION] == {"api_version": 12}


# --------------------------------------------------------------------------
# 5. Fail-closed execution paths
# --------------------------------------------------------------------------


def test_missing_hdc_tool_fails_closed(repo, tmp_path):
    report = make_report(tmp_path)
    result, exit_code = run(
        repo,
        **check_kwargs(
            report,
            hdc="aios-no-such-hdc",
            tool_resolver=None,
            runner=FakeRunner(),
        ),
    )
    assert exit_code == EXIT_FAILURE
    assert [item["code"] for item in result["failures"]] == ["hdc_not_found"]
    assert result["toolchain"]["probed"] is True
    assert result["device_access"]["commands_executed"] == 0


def test_spawn_failure_fails_closed(repo, tmp_path):
    report = make_report(tmp_path)
    runner = FakeRunner(exc={SUB_API: OSError("spawn boom")})
    result, exit_code = run(repo, **check_kwargs(report, runner=runner))
    assert exit_code == EXIT_FAILURE
    assert result["failures"][0]["code"] == "api_version_failed"
    assert result["failures"][0]["detail"]["error"] == "spawn_failed"
    assert [s["name"] for s in result["steps"] if s["status"] == STEP_STATUS_OK] == [
        STEP_GATE
    ]


def test_timeout_fails_closed(repo, tmp_path):
    report = make_report(tmp_path)
    runner = FakeRunner(
        exc={SUB_API: subprocess.TimeoutExpired("hdc", 1)}
    )
    result, exit_code = run(repo, **check_kwargs(report, runner=runner))
    assert exit_code == EXIT_FAILURE
    assert result["failures"][0]["code"] == "api_version_timeout"
    command = result["steps"][1]["commands"][0]
    assert command["error"] == "timeout"
    assert command["exit_code"] is None
    assert result["device_access"]["commands_attempted"] == 1
    assert result["device_access"]["commands_executed"] == 0
    assert result["device_access"]["hardware_touched"] is True


def test_nonzero_probe_exit_fails_closed(repo, tmp_path):
    report = make_report(tmp_path)
    runner = FakeRunner(exit_codes={SUB_API: 4})
    result, exit_code = run(repo, **check_kwargs(report, runner=runner))
    assert exit_code == EXIT_FAILURE
    assert result["failures"][0]["code"] == "api_version_failed"
    assert result["failures"][0]["detail"]["exit_code"] == 4
    later = next(
        s for s in result["steps"] if s["name"] == PROBE_DEVICE_MODEL
    )
    assert later["status"] == STEP_STATUS_NOT_RUN
    assert later["reason"] == "previous_step_failed"


@pytest.mark.parametrize(
    "outputs,code",
    [
        (_with_invalid(SUB_API, ""), "api_version_output_invalid"),
        (_with_invalid(SUB_API, "not-a-number"), "api_version_output_invalid"),
        (_with_invalid(SUB_MODEL, ""), "device_model_output_invalid"),
        (_with_invalid(SUB_BUNDLE, ""), "bundle_query_output_invalid"),
    ],
)
def test_invalid_probe_output_fails_closed(repo, tmp_path, outputs, code):
    report = make_report(tmp_path)
    runner = FakeRunner(outputs=outputs)
    result, exit_code = run(repo, **check_kwargs(report, runner=runner))
    assert exit_code == EXIT_FAILURE
    assert code in [item["code"] for item in result["failures"]]


def test_bundle_query_reports_presence_without_echoing_dump(
    repo, tmp_path
):
    report = make_report(tmp_path)
    runner = FakeRunner(outputs=ok_outputs())
    result, exit_code = run(repo, **check_kwargs(report, runner=runner))
    assert exit_code == EXIT_OK
    assert result["probes"][PROBE_BUNDLE_QUERY] == {"bundle_present": True}
    assert BUNDLE_NAME not in render_json(result["probes"])


def test_no_bundle_skips_bundle_query_in_checked_mode(repo, tmp_path):
    report = make_report(tmp_path)
    result, exit_code = run(
        repo,
        **check_kwargs(
            report,
            bundle=" ",
            runner=FakeRunner(outputs=ok_outputs()),
        ),
    )
    assert exit_code == EXIT_BLOCKED  # empty --bundle is a blocked request
    assert [item["code"] for item in result["failures"]] == ["bundle_invalid"]


def test_bundle_defaults_to_app_scope(repo, tmp_path):
    report = make_report(tmp_path)
    resolver = fake_bundle_resolver()
    result, exit_code = run(
        repo,
        **check_kwargs(
            report,
            bundle=None,
            bundle_resolver=resolver,
            runner=FakeRunner(outputs=ok_outputs()),
        ),
    )
    assert exit_code == EXIT_OK
    assert resolver.calls == [(repo, None)]
    assert result["bundle_name"] == BUNDLE_NAME


def test_check_bundle_resolution_failure_fails_closed(repo, tmp_path):
    report = make_report(tmp_path)

    def failing(repo_root, bundle):
        return None, [{"code": "bundle_not_resolved"}]

    result, exit_code = run(
        repo,
        **check_kwargs(
            report,
            bundle=None,
            bundle_resolver=failing,
            runner=FakeRunner(),
        ),
    )
    assert exit_code == EXIT_BLOCKED
    assert [item["code"] for item in result["failures"]] == [
        "bundle_not_resolved"
    ]


# --------------------------------------------------------------------------
# 6. Redaction and deterministic serialization
# --------------------------------------------------------------------------


def test_no_target_paths_outputs_or_secrets_leak(repo, tmp_path):
    reports_dir = tmp_path / REPORT_PATH_MARKER
    reports_dir.mkdir()
    report = make_report(reports_dir)
    runner = FakeRunner(outputs=ok_outputs())
    result, _ = run(
        repo,
        **check_kwargs(
            report,
            runner=runner,
            tool_resolver=fake_tool_resolver(source="explicit"),
        ),
    )
    text = render_json(result) + render_summary(result)
    for marker in (
        TARGET,
        LOOPBACK_TARGET,
        str(tmp_path),
        REPORT_PATH_MARKER,
        TOOL_DIR_MARKER,
        STDOUT_MARKER,
        STDERR_MARKER,
    ):
        assert marker not in text
    assert result["target"]["raw_recorded"] is False
    assert result["target"]["recorded_as"] == "sha256_prefix"
    assert len(result["target"]["hash"]) == 12
    assert result["boundaries"]["command_output_recorded"] is False
    assert result["boundaries"]["device_identity_recorded"] is False


def test_target_hash_is_stable_and_non_reversible():
    resolved, failures = resolve_target(TARGET, true_device_mode=True)
    assert failures == []
    assert resolved.raw == TARGET
    assert resolved.hash == dp._target_hash(TARGET)
    assert resolved.hash != TARGET
    assert len(resolved.hash) == 12
    assert resolved.kind == "other"


def test_child_env_is_scrubbed(repo, tmp_path, monkeypatch):
    report = make_report(tmp_path)
    secret_value = "secret-value-must-never-reach-child-or-json"
    for name in SCRUBBED_ENV_VARS:
        monkeypatch.setenv(name, secret_value)
    runner = FakeRunner(outputs=ok_outputs())
    result, _ = run(repo, **check_kwargs(report, runner=runner))
    for env in runner.envs:
        for name in SCRUBBED_ENV_VARS:
            assert name not in env
    text = render_json(result) + render_summary(result)
    assert secret_value not in text
    commands = [
        c for step in result["steps"] for c in step["commands"]
    ]
    for command in commands:
        assert command["env_keys"] == []
        assert command["stripped_env_keys"] == list(SCRUBBED_ENV_VARS)


def test_scrubbed_env_vars_reuse_the_shared_constants():
    from tools.harmony_release.preflight import MATERIAL_ENV_VARS
    from tools.harmony_release.sign_hap import CREDENTIAL_ENV_VARS

    assert set(SCRUBBED_ENV_VARS) == set(MATERIAL_ENV_VARS) | set(
        CREDENTIAL_ENV_VARS
    )


def test_failures_are_sorted_deterministically(repo, tmp_path):
    report = make_report(tmp_path)
    result, _ = run(repo, **check_kwargs(report, target="all"))
    dumped = [
        json.dumps(item, sort_keys=True) for item in result["failures"]
    ]
    assert dumped == sorted(dumped)


def test_parse_probe_output_shapes():
    assert parse_probe_output(PROBE_API_VERSION, "12\n") == (
        {"api_version": 12}, None
    )
    assert parse_probe_output(PROBE_API_VERSION, "abc")[1] == (
        "probe_output_invalid"
    )
    assert parse_probe_output(PROBE_DEVICE_MODEL, "Pura 90 Pro")[0] == {
        "value_present": True, "value_recorded": False,
    }
    assert parse_probe_output(PROBE_DEVICE_MODEL, " ")[1] == (
        "probe_output_invalid"
    )
    assert parse_probe_output(PROBE_BUNDLE_QUERY, '{"bundleName": "x"}') == (
        {"bundle_present": True}, None
    )


def test_load_signature_gate_requires_strict_json(tmp_path):
    report = tmp_path / "r.json"
    report.write_text("{oops", encoding="utf-8")
    (tmp_path / "r.json.sha256").write_text(
        hashlib.sha256(b"{oops").hexdigest(), encoding="utf-8"
    )
    gate, failures = load_signature_gate(str(report))
    assert gate is None
    assert [item["code"] for item in failures] == [
        "signature_report_invalid_json"
    ]


def test_load_signature_gate_missing_report(tmp_path):
    gate, failures = load_signature_gate(str(tmp_path / "nope.json"))
    assert gate is None
    assert [item["code"] for item in failures] == [
        "signature_report_unreadable"
    ]


def test_load_signature_gate_none():
    gate, failures = load_signature_gate(None)
    assert gate is None
    assert failures == [{"code": "signature_report_required"}]


def test_validate_relpath_classifies_paths():
    """Only safe POSIX relative paths validate; everything else is None."""
    for safe in (
        "entry-default-signed.hap",
        "apps/harmony/entry-default-signed.hap",
        "build/default/outputs/x.hap",
        "a/b/c/d/e.hap",
        ".hidden.hap",
    ):
        assert dp.validate_relpath(safe) == safe
    for unsafe in (
        "/etc/passwd",
        "C:\\Secret\\entry-signed.hap",
        "C:/Secret/entry-signed.hap",
        "\\\\server\\share\\entry.hap",
        "apps\\harmony\\x.hap",
        "..",
        "../entry.hap",
        "apps/../secret.hap",
        "apps/harmony/../../etc/x.hap",
        "",
        "   ",
        " entry.hap",
        42,
        None,
    ):
        assert dp.validate_relpath(unsafe) is None


@pytest.mark.parametrize(
    "relpath",
    [
        "/absolute/build/entry-signed.hap",
        "C:\\Secret\\build\\entry-signed.hap",
        "\\\\server\\share\\entry-signed.hap",
        "apps\\harmony\\entry-signed.hap",
        "../outside/entry-signed.hap",
        "apps/../outside/entry-signed.hap",
        "",
        42,
    ],
)
def test_unsafe_relpath_blocks_the_gate(tmp_path, relpath):
    report = make_report(
        tmp_path,
        input={"relpath": relpath, "sha256": HAP_SHA,
               "size_bytes": HAP_SIZE},
    )
    gate, failures = load_signature_gate(str(report))
    assert gate is None
    assert [item["code"] for item in failures] == [
        "signature_report_hap_relpath_invalid"
    ]
    assert '"relpath"' not in json.dumps(failures)


def test_absolute_relpath_report_cannot_leak_into_output(repo, tmp_path):
    """End to end: a passing report carrying an absolute relpath is
    rejected at the gate and the path never reaches any output byte."""
    secret_relpath = "C:\\Secret\\build\\entry-signed.hap"
    report = make_report(
        tmp_path,
        input={"relpath": secret_relpath, "sha256": HAP_SHA,
               "size_bytes": HAP_SIZE},
    )
    runner = FakeRunner(outputs=ok_outputs())
    result, exit_code = run(repo, **check_kwargs(report, runner=runner))
    assert exit_code == EXIT_BLOCKED
    assert result["status"] == STATUS_BLOCKED
    assert [item["code"] for item in result["failures"]] == [
        "signature_report_hap_relpath_invalid"
    ]
    assert result["signature_gate"]["hap"] is None
    assert runner.calls == []  # blocked before any toolchain probe
    text = render_json(result) + render_summary(result)
    assert secret_relpath not in text
    assert "Secret" not in text
    # not even the relpath KEY is serialized (the failure *code* may
    # legitimately contain the substring - it is a fixed constant).
    assert '"relpath"' not in text


# --------------------------------------------------------------------------
# 7. CLI contract
# --------------------------------------------------------------------------


def test_cli_requires_a_mode():
    with pytest.raises(SystemExit) as excinfo:
        parse_args([])
    assert excinfo.value.code == 2


def test_cli_check_requires_signature_report():
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["check", "--target", TARGET])
    assert excinfo.value.code == 2


def test_cli_check_requires_target():
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["check", "--signature-report", "x.json"])
    assert excinfo.value.code == 2


def test_cli_plan_prints_plan_and_exits_zero(repo, capsys):
    exit_code = main([
        "plan", "--repo-root", str(repo), "--target", TARGET, "--quiet",
    ])
    assert exit_code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == STATUS_PLANNED
    assert payload["tool"] == TOOL_NAME
    assert TARGET not in json.dumps(payload)


def test_cli_check_without_flag_is_blocked(repo, tmp_path, capsys):
    report = make_report(tmp_path)
    exit_code = main([
        "check",
        "--repo-root", str(repo),
        "--target", TARGET,
        "--signature-report", str(report),
        "--quiet",
    ])
    assert exit_code == EXIT_BLOCKED
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == STATUS_BLOCKED
    assert payload["failures"][0]["code"] == "read_only_not_confirmed"


def test_cli_check_loopback_is_blocked(repo, tmp_path, capsys):
    report = make_report(tmp_path)
    exit_code = main([
        "check",
        "--repo-root", str(repo),
        "--target", "localhost:5555",
        "--signature-report", str(report),
        READ_ONLY_CONFIRMATION_FLAG,
        "--quiet",
    ])
    assert exit_code == EXIT_BLOCKED
    payload = json.loads(capsys.readouterr().out)
    assert payload["failures"][0]["code"] == "target_loopback_forbidden"


def test_cli_checked_run_via_subparsers(repo, tmp_path, capsys, monkeypatch):
    report = make_report(tmp_path)

    def fake_run(repo_root, **kwargs):  # never spawns anything
        return {"schema_version": 1, "tool": TOOL_NAME, "mode": MODE_CHECK,
                "status": STATUS_OK, "exit_code": EXIT_OK,
                "dry_run": False,
                "confirmation": {"flag": READ_ONLY_CONFIRMATION_FLAG,
                                 "confirmed": True},
                "signature_gate": {"sidecar_verified": True},
                "target": {"hash": "ABCDEF123456", "kind": "other"},
                "device_access": {"commands_executed": 3},
                "failures": [], "not_run": [],
                "warnings": [], "steps": []}, EXIT_OK

    monkeypatch.setattr(dp, "run_device_preflight", fake_run)
    exit_code = main([
        "check",
        "--repo-root", str(repo),
        "--target", TARGET,
        "--signature-report", str(report),
        "--bundle", BUNDLE_NAME,
        READ_ONLY_CONFIRMATION_FLAG,
        "--quiet",
    ])
    assert exit_code == EXIT_OK
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == STATUS_OK


def test_record_shape(repo, tmp_path):
    report = make_report(tmp_path)
    result, _ = run(repo, **check_kwargs(report))
    assert result["schema_version"] == 1
    assert result["tool"] == TOOL_NAME
    assert result["command_timeout_seconds"] == DEFAULT_COMMAND_TIMEOUT_SECONDS
    assert result["confirmation"] == {
        "flag": READ_ONLY_CONFIRMATION_FLAG, "confirmed": True,
    }
    assert result["boundaries"]["agc_access"] is False
    assert result["boundaries"]["credentials_used"] is False
    assert result["boundaries"]["device_logs_read"] is False
    assert result["device_access"]["read_only_only"] is True
    assert result["device_access"]["discovery_used"] is False
    assert result["signature_gate"]["hap"]["signedness_source"] == (
        "verifier_report_not_filename"
    )
