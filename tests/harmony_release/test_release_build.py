"""Tests for tools.harmony_release.release_build (release build orchestration).

Every test injects a fake runner and creates fake HAP bytes inside temporary
directories: hvigor/DevEco is never invoked, no real release artifact is ever
produced, and no signing material is read or written.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import tools.harmony_release.release_build as release_build_module
from tools.harmony_release.preflight import MATERIAL_ENV_VARS
from tools.harmony_release.release_build import (
    ASSEMBLE_ARGS,
    CLEAN_ARGS,
    DEFAULT_REPO_ROOT,
    EXPECTED_ARTIFACT_NAME,
    HARMONY_SUBDIR,
    OUTPUT_RELPATH,
    CommandResult,
    discover_unsigned_artifact,
    inspect_artifact,
    render_json,
    resolve_hvigorw,
    run_release_build,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HAP_BYTES = b"fake-unsigned-hap-bytes-not-a-real-artifact"


@pytest.fixture(autouse=True)
def _no_material_env(monkeypatch):
    """Keep host environment from leaking signing-material vars into tests."""
    for name in MATERIAL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class FakeRunner:
    """Records calls and replays canned outcomes (never spawns anything)."""

    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = list(outcomes or [])

    def __call__(self, argv, cwd, env):
        self.calls.append({"argv": tuple(argv), "cwd": Path(cwd), "env": dict(env)})
        returncode, stdout, stderr = (
            self.outcomes.pop(0) if self.outcomes else (0, "BUILD SUCCESSFUL in 1s", "")
        )
        return CommandResult(
            argv=tuple(argv),
            cwd=Path(cwd),
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )


def make_repo(tmp_path: Path) -> Path:
    """Repo skeleton with a dummy hvigor wrapper (never executed)."""
    repo = tmp_path / "repo"
    harmony = repo / HARMONY_SUBDIR
    (harmony / OUTPUT_RELPATH).mkdir(parents=True)
    (harmony / "build-profile.json5").write_text(
        json.dumps({"app": {"signingConfigs": []}}), encoding="utf-8"
    )
    return repo


def write_hap(repo: Path, name: str = EXPECTED_ARTIFACT_NAME, payload: bytes = HAP_BYTES) -> Path:
    path = repo / HARMONY_SUBDIR / OUTPUT_RELPATH / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def fake_program(tmp_path: Path, name: str = "hvigorw.bat") -> Path:
    """Stand-in wrapper: an existing file that is never executed."""
    program = tmp_path / name
    program.write_text("@echo off\n", encoding="utf-8")
    return program


def expected_relpath(name: str = EXPECTED_ARTIFACT_NAME) -> str:
    return (Path(HARMONY_SUBDIR) / OUTPUT_RELPATH / name).as_posix()


class TestSuccess:
    def test_success_records_artifact_and_command_shape(self, tmp_path):
        repo = make_repo(tmp_path)
        write_hap(repo)
        runner = FakeRunner()
        result, code = run_release_build(
            repo, runner=runner, program=str(fake_program(tmp_path))
        )

        assert code == 0
        assert result["status"] == "ok"
        assert result["failures"] == []
        assert result["require_materials"] is False
        assert result["external_materials"] is None

        artifact = result["artifact"]
        assert artifact["relpath"] == expected_relpath()
        assert artifact["size_bytes"] == len(HAP_BYTES)
        assert artifact["sha256"] == hashlib.sha256(HAP_BYTES).hexdigest().upper()
        assert artifact["filename_has_unsigned"] is True
        assert artifact["name_matches_expected"] is True
        assert artifact["inside_repository"] is True

        assert result["steps"] == [
            {"name": "clean", "exit_code": 0, "success_marker": True},
            {"name": "assemble", "exit_code": 0, "success_marker": True},
        ]
        shape = result["command"]
        assert shape["program"] == "hvigorw.bat"
        assert shape["cwd"] == HARMONY_SUBDIR
        assert shape["clean_args"] == list(CLEAN_ARGS)
        assert shape["assemble_args"] == list(ASSEMBLE_ARGS)
        assert shape["assemble_args"][1:5] == [
            "--mode", "module", "-p", "product=default",
        ]
        assert shape["env_keys"] == []

    def test_runner_receives_documented_commands_in_harmony_dir(self, tmp_path):
        repo = make_repo(tmp_path)
        write_hap(repo)
        runner = FakeRunner()
        program = fake_program(tmp_path)
        run_release_build(repo, runner=runner, program=str(program))

        assert [call["argv"][1:] for call in runner.calls] == [
            CLEAN_ARGS, ASSEMBLE_ARGS,
        ]
        assert {call["argv"][0] for call in runner.calls} == {str(program)}
        assert {call["cwd"] for call in runner.calls} == {repo / HARMONY_SUBDIR}

    def test_env_override_keys_recorded_but_not_values(self, tmp_path):
        repo = make_repo(tmp_path)
        write_hap(repo)
        runner = FakeRunner()
        result, code = run_release_build(
            repo,
            runner=runner,
            program=str(fake_program(tmp_path)),
            env_overrides={"DEVECO_SDK_HOME": "D:/secret-sdk-location"},
        )
        assert code == 0
        assert result["command"]["env_keys"] == ["DEVECO_SDK_HOME"]
        # the value reaches the process but never the JSON
        assert runner.calls[0]["env"]["DEVECO_SDK_HOME"] == "D:/secret-sdk-location"
        assert "secret-sdk-location" not in render_json(result)

    def test_non_expected_unsigned_name_is_recorded_not_silently_accepted(self, tmp_path):
        repo = make_repo(tmp_path)
        write_hap(repo, name="entry-default-1.0.0-unsigned.hap")
        result, code = run_release_build(
            repo, runner=FakeRunner(), program=str(fake_program(tmp_path))
        )
        assert code == 0
        assert result["artifact"]["name_matches_expected"] is False
        assert result["artifact"]["filename_has_unsigned"] is True


class TestFailureClosed:
    def test_clean_failure_stops_before_assemble(self, tmp_path):
        repo = make_repo(tmp_path)
        write_hap(repo)  # present but must be ignored: the build failed
        runner = FakeRunner(outcomes=[(1, "", "clean bombed")])
        result, code = run_release_build(
            repo, runner=runner, program=str(fake_program(tmp_path))
        )
        assert code == 1
        assert result["status"] == "failure"
        assert result["artifact"] is None
        assert [f["code"] for f in result["failures"]] == ["clean_failed"]
        assert result["failures"][0]["detail"] == {"exit_code": 1}
        assert len(runner.calls) == 1
        assert result["steps"] == [{"name": "clean", "exit_code": 1, "success_marker": False}]

    def test_assemble_failure_is_failure_without_artifact(self, tmp_path):
        repo = make_repo(tmp_path)
        write_hap(repo)
        runner = FakeRunner(outcomes=[(0, "BUILD SUCCESSFUL", ""), (2, "bundle failed", "")])
        result, code = run_release_build(
            repo, runner=runner, program=str(fake_program(tmp_path))
        )
        assert code == 1
        assert result["status"] == "failure"
        assert result["artifact"] is None
        assert [f["code"] for f in result["failures"]] == ["assemble_failed"]
        assert len(runner.calls) == 2
        assert [step["name"] for step in result["steps"]] == ["clean", "assemble"]

    def test_build_failure_never_leaks_log_text(self, tmp_path):
        repo = make_repo(tmp_path)
        secret_log = f"ERROR at {tmp_path}\\node_modules\\fatal boom"
        runner = FakeRunner(outcomes=[(0, "BUILD SUCCESSFUL", ""), (1, secret_log, secret_log)])
        result, code = run_release_build(
            repo, runner=runner, program=str(fake_program(tmp_path))
        )
        assert code == 1
        text = render_json(result)
        assert "fatal boom" not in text
        assert str(tmp_path) not in text
        assert "ERROR at" not in text

    def test_missing_artifact_fails_closed(self, tmp_path):
        repo = make_repo(tmp_path)
        result, code = run_release_build(
            repo, runner=FakeRunner(), program=str(fake_program(tmp_path))
        )
        assert code == 1
        assert result["status"] == "failure"
        assert result["artifact"] is None
        assert [f["code"] for f in result["failures"]] == ["artifact_missing"]
        assert result["failures"][0]["detail"]["searched"] == (
            Path(HARMONY_SUBDIR) / OUTPUT_RELPATH
        ).as_posix()

    def test_no_output_directory_at_all_fails_closed(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / HARMONY_SUBDIR).mkdir(parents=True)
        result, code = run_release_build(
            repo, runner=FakeRunner(), program=str(fake_program(tmp_path))
        )
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["artifact_missing"]

    def test_ambiguous_artifacts_fail_closed_with_relative_paths(self, tmp_path):
        repo = make_repo(tmp_path)
        write_hap(repo)
        write_hap(repo, name="entry-default-1.0.0-unsigned.hap")
        result, code = run_release_build(
            repo, runner=FakeRunner(), program=str(fake_program(tmp_path))
        )
        assert code == 1
        assert result["artifact"] is None
        assert [f["code"] for f in result["failures"]] == ["artifact_ambiguous"]
        detail = result["failures"][0]["detail"]
        assert detail["count"] == 2
        assert detail["paths"] == sorted([
            expected_relpath(),
            expected_relpath("entry-default-1.0.0-unsigned.hap"),
        ])
        assert str(tmp_path) not in render_json(result)

    def test_signed_hap_is_not_discovered(self, tmp_path):
        repo = make_repo(tmp_path)
        write_hap(repo, name="entry-default-signed.hap")
        result, code = run_release_build(
            repo, runner=FakeRunner(), program=str(fake_program(tmp_path))
        )
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["artifact_missing"]

    def test_artifact_outside_repository_fails_closed(self, tmp_path):
        repo = make_repo(tmp_path)
        outside = tmp_path / "outside" / EXPECTED_ARTIFACT_NAME
        outside.parent.mkdir(parents=True)
        outside.write_bytes(HAP_BYTES)
        record, failures = inspect_artifact(outside, repo)
        assert record is None
        assert [f["code"] for f in failures] == ["artifact_outside_repository"]
        assert str(tmp_path) not in json.dumps(failures)

    def test_unreadable_artifact_fails_closed(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        write_hap(repo)

        def boom(_path):
            raise OSError("cannot read")

        monkeypatch.setattr(release_build_module, "_sha256_upper", boom)
        result, code = run_release_build(
            repo, runner=FakeRunner(), program=str(fake_program(tmp_path))
        )
        assert code == 1
        assert result["artifact"] is None
        assert [f["code"] for f in result["failures"]] == ["artifact_unreadable"]
        assert result["failures"][0]["detail"] == {"relpath": expected_relpath()}
        assert "cannot read" not in render_json(result)

    def test_hvigorw_missing_fails_closed_without_spawning(self, tmp_path):
        repo = make_repo(tmp_path)
        write_hap(repo)
        runner = FakeRunner()
        result, code = run_release_build(
            repo, runner=runner, program="definitely-missing-hvigorw-xyz"
        )
        assert code == 1
        assert result["steps"] == []
        assert runner.calls == []
        assert [f["code"] for f in result["failures"]] == ["hvigorw_not_found"]
        assert result["failures"][0]["detail"] == {"program": "definitely-missing-hvigorw-xyz"}

    def test_spawn_failure_is_recorded_as_failure(self, tmp_path):
        repo = make_repo(tmp_path)
        write_hap(repo)

        def raising_runner(argv, cwd, env):
            raise OSError("spawn exploded")

        result, code = run_release_build(
            repo, runner=raising_runner, program=str(fake_program(tmp_path))
        )
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["clean_failed"]
        assert result["failures"][0]["detail"] == {"error": "spawn_failed"}
        assert "spawn exploded" not in render_json(result)

    def test_resolve_hvigorw_explicit_missing_path_is_none(self, tmp_path):
        assert resolve_hvigorw(str(tmp_path / "nope.bat")) is None
        assert resolve_hvigorw(str(fake_program(tmp_path))) is not None


class TestRequireMaterials:
    def test_absent_materials_exit_2_without_running_hvigor(self, tmp_path):
        repo = make_repo(tmp_path)
        write_hap(repo)
        runner = FakeRunner()
        result, code = run_release_build(
            repo, runner=runner, program=str(fake_program(tmp_path)),
            require_materials=True,
        )
        assert code == 2
        assert result["status"] == "blocked_by_external_materials"
        assert result["require_materials"] is True
        assert result["failures"] == []
        assert result["steps"] == []
        assert result["artifact"] is None
        assert runner.calls == []
        assert result["external_materials"]["all_present"] is False
        assert result["external_materials"]["all_valid"] is None

    def test_present_materials_allow_the_build(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        write_hap(repo)
        materials = tmp_path / "outside_materials"
        materials.mkdir()
        for name, suffix in (
            ("AIOS_HARMONY_CERT_PATH", ".cer"),
            ("AIOS_HARMONY_PROFILE_PATH", ".p7b"),
            ("AIOS_HARMONY_KEYSTORE_PATH", ".p12"),
        ):
            path = materials / f"release{suffix}"
            path.write_bytes(b"placeholder-material")
            monkeypatch.setenv(name, str(path))
        runner = FakeRunner()
        result, code = run_release_build(
            repo, runner=runner, program=str(fake_program(tmp_path)),
            require_materials=True,
        )
        assert code == 0
        assert result["status"] == "ok"
        assert result["external_materials"]["all_present"] is True
        assert result["external_materials"]["all_valid"] is True
        assert len(runner.calls) == 2

    def test_invalid_material_is_failure_exit_1_without_running_hvigor(
        self, tmp_path, monkeypatch
    ):
        repo = make_repo(tmp_path)
        write_hap(repo)
        inside = repo / "keystore.p12"  # inside the repository: invalid
        inside.write_bytes(b"placeholder-material")
        monkeypatch.setenv("AIOS_HARMONY_KEYSTORE_PATH", str(inside))
        runner = FakeRunner()
        result, code = run_release_build(
            repo, runner=runner, program=str(fake_program(tmp_path)),
            require_materials=True,
        )
        assert code == 1
        assert result["status"] == "failure"
        assert runner.calls == []
        assert [f["code"] for f in result["failures"]] == ["external_material_invalid"]
        assert result["failures"][0]["detail"] == {
            "variable": "AIOS_HARMONY_KEYSTORE_PATH", "error": "inside_repository",
        }
        assert result["external_materials"]["all_present"] is False
        assert str(tmp_path) not in render_json(result)

    def test_default_mode_ignores_materials_entirely(self, tmp_path):
        repo = make_repo(tmp_path)
        write_hap(repo)
        result, code = run_release_build(
            repo, runner=FakeRunner(), program=str(fake_program(tmp_path))
        )
        assert code == 0
        assert result["external_materials"] is None
        assert result["require_materials"] is False


class TestJsonSafetyAndDeterminism:
    def test_json_has_no_absolute_paths_or_env_values(self, tmp_path):
        repo = make_repo(tmp_path)
        write_hap(repo)
        runner = FakeRunner()
        result, _ = run_release_build(
            repo,
            runner=runner,
            program=str(fake_program(tmp_path)),
            env_overrides={"DEVECO_SDK_HOME": str(tmp_path / "sdk")},
        )
        text = render_json(result)
        assert str(tmp_path) not in text
        assert str(tmp_path).replace("\\", "/") not in text
        # every path-ish string in the JSON is repo-relative
        assert result["artifact"]["relpath"].startswith(f"{HARMONY_SUBDIR}/")
        assert result["command"]["cwd"] == HARMONY_SUBDIR
        assert not Path(result["artifact"]["relpath"]).is_absolute()

    def test_json_contains_no_log_fields(self, tmp_path):
        repo = make_repo(tmp_path)
        write_hap(repo)
        runner = FakeRunner(outcomes=[(0, "BUILD SUCCESSFUL in 1s\nmore log", "warning text")])
        result, _ = run_release_build(
            repo, runner=runner, program=str(fake_program(tmp_path))
        )
        text = render_json(result)
        assert "more log" not in text
        assert "warning text" not in text
        for forbidden in ("stdout", "stderr", "log", "log_tail"):
            assert f'"{forbidden}"' not in text

    def test_output_is_deterministic(self, tmp_path):
        repo = make_repo(tmp_path)
        write_hap(repo)
        program = str(fake_program(tmp_path))
        first, first_code = run_release_build(repo, runner=FakeRunner(), program=program)
        second, second_code = run_release_build(repo, runner=FakeRunner(), program=program)
        assert first_code == second_code == 0
        assert render_json(first) == render_json(second)

    def test_failure_output_is_deterministic(self, tmp_path):
        repo = make_repo(tmp_path)
        write_hap(repo)
        write_hap(repo, name="entry-default-1.0.0-unsigned.hap")
        program = str(fake_program(tmp_path))
        first, _ = run_release_build(repo, runner=FakeRunner(), program=program)
        second, _ = run_release_build(repo, runner=FakeRunner(), program=program)
        assert render_json(first) == render_json(second)

    def test_discover_returns_missing_without_touching_disk(self, tmp_path):
        repo = make_repo(tmp_path)
        record, failures = discover_unsigned_artifact(repo)
        assert record is None
        assert [f["code"] for f in failures] == ["artifact_missing"]


class TestCli:
    """CLI paths that never start a build process (no DevEco, no artifacts)."""

    def _run_cli(self, repo: Path, *extra: str) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in MATERIAL_ENV_VARS}
        return subprocess.run(
            [sys.executable,
             str(REPO_ROOT / "tools/harmony_release/release_build.py"),
             "--repo-root", str(repo), *extra],
            capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
        )

    def test_cli_require_materials_exit_2_with_json_and_summary(self, tmp_path):
        repo = make_repo(tmp_path)
        proc = self._run_cli(repo, "--require-materials")
        assert proc.returncode == 2
        payload = json.loads(proc.stdout)
        assert payload["status"] == "blocked_by_external_materials"
        assert payload["exit_code"] == 2
        assert payload["steps"] == []
        assert "status=blocked_by_external_materials" in proc.stderr
        assert str(tmp_path) not in proc.stdout
        assert str(tmp_path) not in proc.stderr

    def test_cli_hvigorw_missing_exit_1(self, tmp_path):
        repo = make_repo(tmp_path)
        proc = self._run_cli(repo, "--hvigorw", "definitely-missing-hvigorw-xyz")
        assert proc.returncode == 1
        payload = json.loads(proc.stdout)
        assert payload["failures"][0]["code"] == "hvigorw_not_found"
        assert "failures=hvigorw_not_found" in proc.stderr

    def test_cli_quiet_suppresses_summary_only(self, tmp_path):
        repo = make_repo(tmp_path)
        proc = self._run_cli(repo, "--require-materials", "--quiet")
        assert proc.returncode == 2
        assert proc.stderr == ""
        assert json.loads(proc.stdout)["exit_code"] == 2

    def test_cli_default_repo_root_is_this_checkout_but_runs_no_build(self):
        # --require-materials on the real repo: materials are absent, so the
        # gate stops before any process would be spawned.
        env = {k: v for k, v in os.environ.items() if k not in MATERIAL_ENV_VARS}
        proc = subprocess.run(
            [sys.executable,
             str(REPO_ROOT / "tools/harmony_release/release_build.py"),
             "--require-materials"],
            capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
        )
        assert proc.returncode == 2
        payload = json.loads(proc.stdout)
        assert payload["steps"] == []
        assert DEFAULT_REPO_ROOT.name in str(REPO_ROOT)
