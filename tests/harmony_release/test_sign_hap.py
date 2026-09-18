"""Tests for tools.harmony_release.sign_hap (HAP signing wrapper).

Every test injects a fake runner (and, where useful, a fake toolchain
resolver) and uses placeholder files inside temporary directories: java and
hap-sign-tool.jar are never executed, no process is ever spawned, no real
signature is produced, and no real signing material is read or written.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import tools.harmony_release.sign_hap as sign_hap_module
from tools.harmony_release.preflight import MATERIAL_ENV_VARS
from tools.harmony_release.sign_hap import (
    CLAIM_BASIS,
    CREDENTIAL_ENV_VARS,
    DEFAULT_REPO_ROOT,
    JAR_CANDIDATES,
    SDK_HOME_ENV_VAR,
    SIGN_ALGORITHM,
    SIGN_MODE,
    SIGN_OPTION_NAMES,
    STEP_NAME,
    CommandResult,
    Toolchain,
    render_json,
    render_summary,
    resolve_toolchain,
    run_sign_hap,
    validate_request,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HAP_RELPATH = Path(
    "apps/harmony/entry/build/default/outputs/default/entry-default-unsigned.hap"
)
UNSIGNED_BYTES = b"fake-unsigned-hap-bytes-not-a-real-artifact"
SIGNED_BYTES = b"fake-signed-hap-bytes-not-a-real-signature"
PLACEHOLDER = b"placeholder-not-a-real-certificate"

# Markers that must never reach the JSON, a summary or an error message.
ALIAS = "alias-marker-must-never-appear"
KEY_PASSWORD = "key-password-marker-must-never-appear"
KEYSTORE_PASSWORD = "keystore-password-marker-must-never-appear"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Keep host materials/credentials/SDK from leaking into tests."""
    for name in (*MATERIAL_ENV_VARS, *CREDENTIAL_ENV_VARS, SDK_HOME_ENV_VAR):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------


def make_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    (repo / HAP_RELPATH.parent).mkdir(parents=True)
    (repo / HAP_RELPATH).write_bytes(UNSIGNED_BYTES)
    return repo


def hap_path(repo: Path) -> Path:
    return repo / HAP_RELPATH


def default_output_relpath() -> str:
    return HAP_RELPATH.with_name("entry-default-signed.hap").as_posix()


def make_materials(tmp_path: Path) -> dict:
    outside = tmp_path / "outside_materials"
    outside.mkdir(exist_ok=True)
    paths = {
        "AIOS_HARMONY_CERT_PATH": outside / "release.cer",
        "AIOS_HARMONY_PROFILE_PATH": outside / "release.p7b",
        "AIOS_HARMONY_KEYSTORE_PATH": outside / "release.p12",
    }
    for path in paths.values():
        path.write_bytes(PLACEHOLDER)
    return paths


def set_env(monkeypatch, mapping: dict) -> None:
    for name, value in mapping.items():
        monkeypatch.setenv(name, str(value))


def set_full_materials(monkeypatch, tmp_path: Path) -> dict:
    paths = make_materials(tmp_path)
    set_env(monkeypatch, paths)
    set_env(monkeypatch, {
        "AIOS_HARMONY_KEY_ALIAS": ALIAS,
        "AIOS_HARMONY_KEY_PASSWORD": KEY_PASSWORD,
        "AIOS_HARMONY_KEYSTORE_PASSWORD": KEYSTORE_PASSWORD,
    })
    return paths


def make_toolchain_files(tmp_path: Path) -> tuple:
    """Stand-in java + jar: existing placeholder files, never executed."""
    tools_dir = tmp_path / "fake_tools"
    tools_dir.mkdir(exist_ok=True)
    java = tools_dir / "java"
    jar = tools_dir / "hap-sign-tool.jar"
    java.write_bytes(b"not-a-real-java")
    jar.write_bytes(b"not-a-real-jar")
    return java, jar


class FakeSignTool:
    """Records calls and replays a canned outcome (never spawns anything)."""

    def __init__(self, returncode: int = 0, output_bytes: bytes = SIGNED_BYTES,
                 write_output: bool = True):
        self.calls = []
        self.returncode = returncode
        self.output_bytes = output_bytes
        self.write_output = write_output

    def __call__(self, argv, cwd, env):
        argv = tuple(argv)
        self.calls.append({"argv": argv, "cwd": Path(cwd), "env": dict(env)})
        if self.returncode == 0 and self.write_output:
            Path(argv[argv.index("-outFile") + 1]).write_bytes(self.output_bytes)
        return CommandResult(
            argv=argv,
            cwd=Path(cwd),
            returncode=self.returncode,
            stdout="tool log noise that must never be recorded",
            stderr="tool error noise that must never be recorded",
        )


class Fixture:
    """A ready-to-run success fixture with everything wired."""

    def __init__(self, tmp_path, monkeypatch, **overrides):
        self.repo = make_repo(tmp_path)
        self.java, self.jar = make_toolchain_files(tmp_path)
        self.materials = set_full_materials(monkeypatch, tmp_path)
        self.runner = overrides.pop("runner", FakeSignTool())
        self.kwargs = dict(
            hap=str(hap_path(self.repo)),
            java=str(self.java),
            jar=str(self.jar),
            runner=self.runner,
        )
        self.kwargs.update(overrides)

    def run(self, **overrides):
        kwargs = dict(self.kwargs)
        kwargs.update(overrides)
        return run_sign_hap(self.repo, **kwargs)


def make_sdk_tree(tmp_path: Path, candidates=JAR_CANDIDATES[:1]) -> Path:
    """Fake DEVECO_SDK_HOME containing the given jar candidates."""
    sdk = tmp_path / "fake_sdk"
    for rel in candidates:
        target = sdk / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"not-a-real-jar")
    return sdk


# --------------------------------------------------------------------------
# success path
# --------------------------------------------------------------------------


class TestSuccess:
    def test_signs_and_records_honest_facts(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run()

        assert code == 0
        assert result["status"] == "ok"
        assert result["failures"] == []
        assert result["allow_overwrite"] is False

        assert result["input"] == {
            "relpath": HAP_RELPATH.as_posix(),
            "size_bytes": len(UNSIGNED_BYTES),
            "sha256": hashlib.sha256(UNSIGNED_BYTES).hexdigest().upper(),
            "filename_has_unsigned": True,
            "inside_repository": True,
        }
        assert result["output"] == {
            "relpath": default_output_relpath(),
            "path_explicit": False,
            "existed_before": False,
            "filename_has_signed": True,
            "inside_repository": True,
        }
        assert result["artifact"] == {
            "relpath": default_output_relpath(),
            "size_bytes": len(SIGNED_BYTES),
            "sha256": hashlib.sha256(SIGNED_BYTES).hexdigest().upper(),
            "filename_has_signed": True,
            "bytes_changed_from_input": True,
        }
        assert result["steps"] == [{"name": STEP_NAME, "exit_code": 0}]

    def test_signed_flag_is_always_false_and_claim_is_labelled(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run()
        assert code == 0
        assert result["signed"] is False
        assert result["signedness_verified"] is False
        assert result["claimed_signed"] is True
        assert result["claimed_signed_basis"] == CLAIM_BASIS

    def test_documented_argv_shape_and_secret_delivery(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run()
        assert code == 0

        output = hap_path(fixture.repo).with_name("entry-default-signed.hap")
        expected = [
            str(fixture.java), "-jar", str(fixture.jar), "sign-app",
            "-keyAlias", ALIAS,
            "-signAlg", SIGN_ALGORITHM,
            "-mode", SIGN_MODE,
            "-appCertFile", str(fixture.materials["AIOS_HARMONY_CERT_PATH"]),
            "-profileFile", str(fixture.materials["AIOS_HARMONY_PROFILE_PATH"]),
            "-inFile", str(hap_path(fixture.repo).resolve()),
            "-keystoreFile", str(fixture.materials["AIOS_HARMONY_KEYSTORE_PATH"]),
            "-outFile", str(output.resolve()),
            "-keyPwd", KEY_PASSWORD,
            "-keystorePwd", KEYSTORE_PASSWORD,
        ]
        assert len(fixture.runner.calls) == 1
        assert list(fixture.runner.calls[0]["argv"]) == expected
        assert fixture.runner.calls[0]["cwd"] == fixture.repo
        # values (including secrets) reach the child process only
        env = fixture.runner.calls[0]["env"]
        assert env["AIOS_HARMONY_KEY_PASSWORD"] == KEY_PASSWORD
        assert env["AIOS_HARMONY_KEY_ALIAS"] == ALIAS

        shape = result["command"]
        assert shape["program"] == "java"
        assert shape["jar_name"] == "hap-sign-tool.jar"
        assert shape["subcommand"] == "sign-app"
        assert shape["cwd"] == "."
        assert shape["mode"] == SIGN_MODE
        assert shape["sign_alg"] == SIGN_ALGORITHM
        assert shape["option_names"] == list(SIGN_OPTION_NAMES)
        assert shape["secret_option_names"] == ["-keyAlias", "-keyPwd", "-keystorePwd"]
        assert shape["env_keys"] == []

    def test_toolchain_recorded_by_name_only(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run()
        assert code == 0
        assert result["toolchain"] == {
            "java_name": "java",
            "java_source": "explicit",
            "jar_name": "hap-sign-tool.jar",
            "jar_source": "explicit",
            "jar_candidate": None,
        }

    def test_materials_and_credentials_recorded_value_free(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run()
        assert code == 0
        assert result["external_materials"]["all_present"] is True
        assert result["external_materials"]["all_valid"] is True
        assert result["credentials"]["all_present"] is True
        assert result["credentials"]["variables"] == {
            name: {"present": True, "valid": True, "error": None}
            for name in CREDENTIAL_ENV_VARS
        }

    def test_relative_hap_and_out_resolve_against_repo_root(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch, hap=HAP_RELPATH.as_posix())
        result, code = fixture.run(out="apps/harmony/entry/build/default/outputs/"
                                       "default/entry-default-signed.hap")
        assert code == 0
        assert result["input"]["relpath"] == HAP_RELPATH.as_posix()
        assert result["output"]["path_explicit"] is True
        assert result["artifact"]["relpath"] == default_output_relpath()

    def test_in_repo_traversal_that_stays_inside_is_allowed(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        dotted = (
            "apps/harmony/entry/build/default/outputs/default/../default/"
            "entry-default-signed.hap"
        )
        result, code = fixture.run(out=dotted)
        assert code == 0
        assert result["artifact"]["relpath"] == default_output_relpath()

    def test_in_repo_backslash_traversal_that_stays_inside_is_allowed(
        self, tmp_path, monkeypatch
    ):
        """Windows-style separators must not break documented in-repo ``..``."""
        fixture = Fixture(tmp_path, monkeypatch)
        dotted = (
            "apps\\harmony\\entry\\build\\default\\outputs\\default\\..\\default\\"
            "entry-default-signed.hap"
        )
        result, code = fixture.run(out=dotted)
        assert code == 0
        assert result["output"]["path_explicit"] is True
        assert result["artifact"]["relpath"] == default_output_relpath()

    def test_unchanged_output_bytes_are_recorded_not_hidden(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(runner=FakeSignTool(output_bytes=UNSIGNED_BYTES))
        assert code == 0
        assert result["artifact"]["bytes_changed_from_input"] is False
        # the claim still rests on tool success, never on this wrapper's word
        assert result["claimed_signed"] is True
        assert result["signed"] is False

    def test_overwrite_allowed_with_explicit_opt_in(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        output = hap_path(fixture.repo).with_name("entry-default-signed.hap")
        output.write_bytes(b"previous-signed-bytes")
        result, code = fixture.run(allow_overwrite=True)
        assert code == 0
        assert result["allow_overwrite"] is True
        assert result["output"]["existed_before"] is True
        assert result["artifact"]["sha256"] == hashlib.sha256(
            SIGNED_BYTES
        ).hexdigest().upper()

    def test_sdk_home_discovery_is_used_when_jar_not_given(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch, jar=None)
        sdk = make_sdk_tree(tmp_path)
        set_env(monkeypatch, {SDK_HOME_ENV_VAR: sdk})
        result, code = fixture.run()
        assert code == 0
        assert result["toolchain"]["jar_source"] == "deveco_sdk_home"
        assert result["toolchain"]["jar_candidate"] == JAR_CANDIDATES[0]
        assert fixture.runner.calls[0]["argv"][2] == str(sdk / JAR_CANDIDATES[0])

    def test_toolchain_resolver_is_injectable(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        seen = {}

        def fake_resolver(java_program, jar, sdk_home):
            seen["args"] = (java_program, jar, sdk_home)
            return Toolchain(
                java=fixture.java, jar=fixture.jar,
                java_source="explicit", jar_source="explicit", jar_candidate=None,
            ), []

        result, code = fixture.run(toolchain_resolver=fake_resolver)
        assert code == 0
        assert seen["args"] == (str(fixture.java), str(fixture.jar), None)
        assert fixture.runner.calls[0]["argv"][1] == "-jar"


# --------------------------------------------------------------------------
# external materials + credentials gate
# --------------------------------------------------------------------------


class TestExternalGate:
    def test_missing_materials_blocked_exit_2_without_spawning(self, tmp_path):
        repo = make_repo(tmp_path)
        java, jar = make_toolchain_files(tmp_path)
        runner = FakeSignTool()
        result, code = run_sign_hap(
            repo, hap=str(hap_path(repo)), java=str(java), jar=str(jar), runner=runner
        )
        assert code == 2
        assert result["status"] == "blocked_by_external_materials"
        assert result["exit_code"] == 2
        assert result["failures"] == []
        assert result["steps"] == []
        assert result["artifact"] is None
        assert result["toolchain"] is None  # blocked before toolchain probing
        assert result["claimed_signed"] is False
        assert result["claimed_signed_basis"] is None
        assert result["signed"] is False
        assert result["external_materials"]["all_present"] is False
        assert result["credentials"]["all_present"] is False
        assert runner.calls == []

    def test_blocked_run_never_probes_a_missing_jar(self, tmp_path):
        """Absent materials are reported as the first blocker, not masked."""
        repo = make_repo(tmp_path)
        result, code = run_sign_hap(
            repo, hap=str(hap_path(repo)),
            java="definitely-missing-java-xyz",
            sdk_home=str(tmp_path / "empty_sdk"),
        )
        assert code == 2
        assert result["status"] == "blocked_by_external_materials"
        assert result["failures"] == []
        assert result["toolchain"] is None

    def test_partial_materials_blocked(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        java, jar = make_toolchain_files(tmp_path)
        paths = make_materials(tmp_path)
        set_env(monkeypatch, {"AIOS_HARMONY_CERT_PATH": paths["AIOS_HARMONY_CERT_PATH"]})
        set_env(monkeypatch, {
            "AIOS_HARMONY_KEY_ALIAS": ALIAS,
            "AIOS_HARMONY_KEY_PASSWORD": KEY_PASSWORD,
            "AIOS_HARMONY_KEYSTORE_PASSWORD": KEYSTORE_PASSWORD,
        })
        runner = FakeSignTool()
        result, code = run_sign_hap(
            repo, hap=str(hap_path(repo)), java=str(java), jar=str(jar), runner=runner
        )
        assert code == 2
        assert result["failures"] == []
        assert result["external_materials"]["all_present"] is False
        assert runner.calls == []

    @pytest.mark.parametrize("missing", CREDENTIAL_ENV_VARS)
    def test_missing_credential_blocks_exit_2(self, tmp_path, monkeypatch, missing):
        repo = make_repo(tmp_path)
        java, jar = make_toolchain_files(tmp_path)
        paths = make_materials(tmp_path)
        set_env(monkeypatch, paths)
        credentials = {
            "AIOS_HARMONY_KEY_ALIAS": ALIAS,
            "AIOS_HARMONY_KEY_PASSWORD": KEY_PASSWORD,
            "AIOS_HARMONY_KEYSTORE_PASSWORD": KEYSTORE_PASSWORD,
        }
        credentials.pop(missing)
        set_env(monkeypatch, credentials)
        runner = FakeSignTool()
        result, code = run_sign_hap(
            repo, hap=str(hap_path(repo)), java=str(java), jar=str(jar), runner=runner
        )
        assert code == 2
        assert result["status"] == "blocked_by_external_materials"
        assert result["failures"] == []
        assert result["credentials"]["variables"][missing] == {
            "present": False, "valid": None, "error": "not_set",
        }
        assert runner.calls == []

    def test_empty_password_is_blocked_not_failure(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        java, jar = make_toolchain_files(tmp_path)
        set_full_materials(monkeypatch, tmp_path)
        monkeypatch.setenv("AIOS_HARMONY_KEY_PASSWORD", "")
        runner = FakeSignTool()
        result, code = run_sign_hap(
            repo, hap=str(hap_path(repo)), java=str(java), jar=str(jar), runner=runner
        )
        assert code == 2
        assert result["failures"] == []
        assert result["credentials"]["variables"]["AIOS_HARMONY_KEY_PASSWORD"] == {
            "present": False, "valid": None, "error": "empty_value",
        }
        assert runner.calls == []

    def test_wrong_extension_is_failure_exit_1_without_spawning(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        java, jar = make_toolchain_files(tmp_path)
        paths = set_full_materials(monkeypatch, tmp_path)
        bad = paths["AIOS_HARMONY_CERT_PATH"].with_suffix(".pem")
        bad.write_bytes(PLACEHOLDER)
        monkeypatch.setenv("AIOS_HARMONY_CERT_PATH", str(bad))
        runner = FakeSignTool()
        result, code = run_sign_hap(
            repo, hap=str(hap_path(repo)), java=str(java), jar=str(jar), runner=runner
        )
        assert code == 1
        assert result["status"] == "failure"
        assert [f["code"] for f in result["failures"]] == ["external_material_invalid"]
        assert result["failures"][0]["detail"] == {
            "variable": "AIOS_HARMONY_CERT_PATH", "error": "wrong_extension",
        }
        assert result["toolchain"] is None
        assert runner.calls == []
        assert str(tmp_path) not in render_json(result)

    def test_repo_local_material_is_failure_exit_1(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        java, jar = make_toolchain_files(tmp_path)
        set_full_materials(monkeypatch, tmp_path)
        inside = repo / "stray-keystore.p12"
        inside.write_bytes(PLACEHOLDER)
        monkeypatch.setenv("AIOS_HARMONY_KEYSTORE_PATH", str(inside))
        runner = FakeSignTool()
        result, code = run_sign_hap(
            repo, hap=str(hap_path(repo)), java=str(java), jar=str(jar), runner=runner
        )
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["external_material_invalid"]
        assert result["failures"][0]["detail"] == {
            "variable": "AIOS_HARMONY_KEYSTORE_PATH", "error": "inside_repository",
        }
        assert runner.calls == []
        assert "stray-keystore" not in render_json(result)

    def test_material_path_not_found_is_failure_not_blocker(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        java, jar = make_toolchain_files(tmp_path)
        set_full_materials(monkeypatch, tmp_path)
        missing = tmp_path / "outside_materials" / "absent.cer"
        monkeypatch.setenv("AIOS_HARMONY_CERT_PATH", str(missing))
        runner = FakeSignTool()
        result, code = run_sign_hap(
            repo, hap=str(hap_path(repo)), java=str(java), jar=str(jar), runner=runner
        )
        assert code == 1
        assert result["failures"][0]["detail"] == {
            "variable": "AIOS_HARMONY_CERT_PATH", "error": "path_not_found",
        }
        assert runner.calls == []
        assert "absent" not in render_json(result)


# --------------------------------------------------------------------------
# request validation (fail closed before anything is spawned)
# --------------------------------------------------------------------------


class TestRequestValidation:
    def test_missing_hap_argument(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(hap=None)
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["input_hap_missing"]
        assert result["failures"][0]["detail"] == {"argument": "--hap"}
        assert result["input"] is None
        assert fixture.runner.calls == []

    def test_missing_hap_file(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        hap_path(fixture.repo).unlink()
        result, code = fixture.run()
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["input_hap_missing"]
        assert fixture.runner.calls == []

    def test_non_unsigned_input_name(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        target = fixture.repo / "entry-default.hap"
        target.write_bytes(UNSIGNED_BYTES)
        result, code = fixture.run(hap=str(target))
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["input_hap_not_unsigned"]
        assert result["output"] is None
        assert fixture.runner.calls == []

    def test_input_outside_repository(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        outside = tmp_path / "outside-unsigned.hap"
        outside.write_bytes(UNSIGNED_BYTES)
        result, code = fixture.run(hap=str(outside))
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["input_hap_outside_repository"]
        assert fixture.runner.calls == []
        assert str(tmp_path) not in render_json(result)

    @pytest.mark.parametrize("out", ["../outside-signed.hap", "..\\outside-signed.hap"])
    def test_output_traversal_outside_repository(self, tmp_path, monkeypatch, out):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(out=out)
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["output_outside_repository"]
        assert fixture.runner.calls == []

    @pytest.mark.parametrize("hap", ["../outside-unsigned.hap", "..\\outside-unsigned.hap"])
    def test_input_traversal_outside_repository(self, tmp_path, monkeypatch, hap):
        """Both separator styles must be judged as traversal everywhere."""
        fixture = Fixture(tmp_path, monkeypatch)
        outside = tmp_path / "outside-unsigned.hap"
        outside.write_bytes(UNSIGNED_BYTES)
        result, code = fixture.run(hap=hap)
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["input_hap_outside_repository"]
        assert fixture.runner.calls == []
        assert str(tmp_path) not in render_json(result)

    def test_absolute_output_outside_repository(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(out=str(tmp_path / "evil-signed.hap"))
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["output_outside_repository"]
        assert fixture.runner.calls == []
        assert str(tmp_path) not in render_json(result)

    @pytest.mark.parametrize(
        "out",
        [
            "C:\\outside-signed.hap",
            "C:/outside-signed.hap",
            "\\\\fileserver\\release\\outside-signed.hap",
            "//fileserver/release/outside-signed.hap",
        ],
    )
    def test_windows_drive_or_unc_output_is_outside_everywhere(
        self, tmp_path, monkeypatch, out
    ):
        """Drive and UNC forms must fail containment on Linux and Windows.

        On POSIX ``Path("C:/x").is_absolute()`` is false; joining it onto
        the repo root used to forge containment for drive-absolute paths.
        ``PureWindowsPath`` now catches the drive/UNC form before any
        boundary decision.
        """
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(out=out)
        assert code == 1
        assert [f["code"] for f in result["failures"]] == [
            "output_outside_repository"
        ]
        assert fixture.runner.calls == []
        text = render_json(result)
        assert str(tmp_path) not in text
        assert "outside-signed" not in text

    @pytest.mark.parametrize(
        "hap",
        [
            "C:\\outside-unsigned.hap",
            "C:/outside-unsigned.hap",
            "\\\\fileserver\\release\\outside-unsigned.hap",
            "//fileserver/release/outside-unsigned.hap",
        ],
    )
    def test_windows_drive_or_unc_input_is_outside_everywhere(
        self, tmp_path, monkeypatch, hap
    ):
        """Same containment rule for the input HAP, on both platforms."""
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(hap=hap)
        assert code == 1
        assert [f["code"] for f in result["failures"]] == [
            "input_hap_outside_repository"
        ]
        assert fixture.runner.calls == []
        text = render_json(result)
        assert str(tmp_path) not in text
        assert "outside-unsigned" not in text

    def test_output_equals_input_is_refused_even_with_opt_in(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        for allow in (False, True):
            result, code = fixture.run(out=str(hap_path(fixture.repo)),
                                       allow_overwrite=allow)
            assert code == 1
            assert [f["code"] for f in result["failures"]] == ["output_equals_input"]
        assert fixture.runner.calls == []
        assert hap_path(fixture.repo).read_bytes() == UNSIGNED_BYTES

    def test_existing_output_refused_without_opt_in(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        output = hap_path(fixture.repo).with_name("entry-default-signed.hap")
        output.write_bytes(b"previous-signed-bytes")
        result, code = fixture.run()
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["output_exists"]
        assert result["failures"][0]["detail"] == {"relpath": default_output_relpath()}
        assert result["artifact"] is None
        assert fixture.runner.calls == []
        assert output.read_bytes() == b"previous-signed-bytes"

    def test_missing_output_directory(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(out="apps/harmony/entry/build/default/outputs/"
                                       "nope/entry-default-signed.hap")
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["output_directory_missing"]
        assert result["failures"][0]["detail"] == {
            "relpath": "apps/harmony/entry/build/default/outputs/nope"
        }
        assert fixture.runner.calls == []

    def test_validation_failures_never_spawn_and_are_deterministic(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        bad_requests = [
            {"hap": None},
            {"hap": str(tmp_path / "outside-unsigned.hap")},
            {"out": "../outside-signed.hap"},
            {"out": "..\\outside-signed.hap"},
            {"out": "C:/outside-signed.hap"},
            {"out": "\\\\fileserver\\release\\outside-signed.hap"},
            {"hap": HAP_RELPATH.as_posix(), "out": HAP_RELPATH.as_posix()},
        ]
        for request in bad_requests:
            first, first_code = fixture.run(**request)
            second, second_code = fixture.run(**request)
            assert first_code == second_code == 1
            assert render_json(first) == render_json(second)
            assert first["steps"] == []
        assert fixture.runner.calls == []

    def test_validate_request_returns_paths_for_a_valid_request(self, tmp_path):
        repo = make_repo(tmp_path)
        request, failures = validate_request(repo, HAP_RELPATH.as_posix(), None, False)
        assert failures == []
        assert request.input_path == hap_path(repo).resolve()
        assert request.output_path.name == "entry-default-signed.hap"
        assert request.input_record["filename_has_unsigned"] is True
        assert request.output_record["path_explicit"] is False


# --------------------------------------------------------------------------
# toolchain discovery
# --------------------------------------------------------------------------


class TestToolchain:
    def test_explicit_paths_are_preferred(self, tmp_path, monkeypatch):
        java, jar = make_toolchain_files(tmp_path)
        toolchain, failures = resolve_toolchain(str(java), str(jar), "")
        assert failures == []
        assert toolchain.jar_source == "explicit"
        assert toolchain.java_source == "explicit"
        assert toolchain.jar == jar

    def test_java_is_looked_up_on_path_when_not_a_path(self, tmp_path, monkeypatch):
        java, jar = make_toolchain_files(tmp_path)
        monkeypatch.setattr(
            sign_hap_module.shutil, "which",
            lambda name: str(java) if name == "java" else None,
        )
        toolchain, failures = resolve_toolchain("java", str(jar), "")
        assert failures == []
        assert toolchain.java_source == "path_lookup"
        assert toolchain.java == java

    def test_java_absent_fails_closed(self, tmp_path, monkeypatch):
        java, jar = make_toolchain_files(tmp_path)
        monkeypatch.setattr(sign_hap_module.shutil, "which", lambda name: None)
        toolchain, failures = resolve_toolchain("java", str(jar), "")
        assert toolchain is None
        assert [f["code"] for f in failures] == ["java_not_found"]
        assert failures[0]["detail"] == {"program": "java"}

    def test_sdk_home_candidate_order_is_deterministic(self, tmp_path, monkeypatch):
        java, _ = make_toolchain_files(tmp_path)
        sdk = make_sdk_tree(tmp_path, [JAR_CANDIDATES[1], JAR_CANDIDATES[0]])
        toolchain, failures = resolve_toolchain(str(java), None, str(sdk))
        assert failures == []
        assert toolchain.jar_source == "deveco_sdk_home"
        assert toolchain.jar_candidate == JAR_CANDIDATES[0]
        assert toolchain.jar == sdk / JAR_CANDIDATES[0]

    def test_sdk_home_env_var_is_used_when_not_passed(self, tmp_path, monkeypatch):
        java, _ = make_toolchain_files(tmp_path)
        sdk = make_sdk_tree(tmp_path)
        monkeypatch.setenv(SDK_HOME_ENV_VAR, str(sdk))
        toolchain, failures = resolve_toolchain(str(java))
        assert failures == []
        assert toolchain.jar_candidate == JAR_CANDIDATES[0]

    def test_sdk_home_not_set_fails_closed_without_values(self, tmp_path, monkeypatch):
        java, _ = make_toolchain_files(tmp_path)
        toolchain, failures = resolve_toolchain(str(java))
        assert toolchain is None
        assert [f["code"] for f in failures] == ["sign_tool_jar_not_found"]
        detail = failures[0]["detail"]
        assert detail["env_var"] == SDK_HOME_ENV_VAR
        assert detail["jar_name"] == "hap-sign-tool.jar"
        assert detail["searched"] == list(JAR_CANDIDATES)
        assert detail["error"] == "sdk_home_not_set"

    def test_sdk_home_without_the_jar_fails_closed(self, tmp_path, monkeypatch):
        java, _ = make_toolchain_files(tmp_path)
        empty = tmp_path / "empty_sdk"
        empty.mkdir()
        toolchain, failures = resolve_toolchain(str(java), None, str(empty))
        assert toolchain is None
        assert failures[0]["detail"]["error"] == "no_candidate_found"
        assert "searched" in failures[0]["detail"]

    def test_explicit_missing_jar_fails_closed(self, tmp_path, monkeypatch):
        java, _ = make_toolchain_files(tmp_path)
        toolchain, failures = resolve_toolchain(str(java), str(tmp_path / "nope.jar"), "")
        assert toolchain is None
        assert failures[0]["detail"] == {
            "source": "explicit", "jar_name": "nope.jar",
        }

    def test_missing_jar_is_failure_exit_1_without_spawning(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        java, _ = make_toolchain_files(tmp_path)
        set_full_materials(monkeypatch, tmp_path)
        empty = tmp_path / "empty_sdk"
        empty.mkdir()
        runner = FakeSignTool()
        result, code = run_sign_hap(
            repo, hap=str(hap_path(repo)), java=str(java),
            sdk_home=str(empty), runner=runner,
        )
        assert code == 1
        assert result["status"] == "failure"
        assert [f["code"] for f in result["failures"]] == ["sign_tool_jar_not_found"]
        assert result["toolchain"] is None
        assert result["steps"] == []
        assert runner.calls == []

    def test_missing_java_is_failure_exit_1_without_spawning(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        _, jar = make_toolchain_files(tmp_path)
        set_full_materials(monkeypatch, tmp_path)
        monkeypatch.setattr(sign_hap_module.shutil, "which", lambda name: None)
        runner = FakeSignTool()
        result, code = run_sign_hap(
            repo, hap=str(hap_path(repo)), java="java", jar=str(jar), runner=runner
        )
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["java_not_found"]
        assert result["toolchain"] is None
        assert runner.calls == []

    def test_toolchain_paths_never_reach_the_json(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run()
        assert code == 0
        text = render_json(result)
        assert str(tmp_path) not in text
        assert "fake_tools" not in text
        assert "not-a-real-jar" not in text


# --------------------------------------------------------------------------
# child process outcomes
# --------------------------------------------------------------------------


class TestChildOutcome:
    def test_nonzero_tool_exit_is_failure_without_artifact(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(runner=FakeSignTool(returncode=3))
        assert code == 1
        assert result["status"] == "failure"
        assert [f["code"] for f in result["failures"]] == ["sign_tool_failed"]
        assert result["failures"][0]["detail"] == {"exit_code": 3}
        assert result["steps"] == [{"name": STEP_NAME, "exit_code": 3}]
        assert result["artifact"] is None
        assert result["claimed_signed"] is False
        assert result["claimed_signed_basis"] is None
        assert result["signed"] is False
        assert render_json(result).count("tool log noise") == 0

    def test_spawn_failure_is_recorded_without_error_text(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)

        def raising_runner(argv, cwd, env):
            raise OSError("spawn exploded at an absolute path")

        result, code = fixture.run(runner=raising_runner)
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["sign_tool_spawn_failed"]
        assert result["failures"][0]["detail"] == {"error": "spawn_failed"}
        assert result["steps"] == []
        assert result["artifact"] is None
        text = render_json(result)
        assert "spawn exploded" not in text
        assert str(tmp_path) not in text

    def test_missing_output_after_success_is_failure(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(runner=FakeSignTool(write_output=False))
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["output_missing"]
        assert result["failures"][0]["detail"] == {"relpath": default_output_relpath()}
        assert result["artifact"] is None
        assert result["claimed_signed"] is False
        assert result["steps"] == [{"name": STEP_NAME, "exit_code": 0}]

    def test_unreadable_output_is_failure_without_error_text(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        calls = {"count": 0}

        def boom(path):
            calls["count"] += 1
            if calls["count"] > 1:  # 1st call hashes the input
                raise OSError("cannot read the output")
            return hashlib.sha256(UNSIGNED_BYTES).hexdigest().upper()

        monkeypatch.setattr(sign_hap_module, "_sha256_upper", boom)
        result, code = fixture.run()
        assert code == 1
        assert [f["code"] for f in result["failures"]] == ["output_unreadable"]
        assert result["artifact"] is None
        assert "cannot read the output" not in render_json(result)

    def test_tool_stdout_and_stderr_are_never_read(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run()
        assert code == 0
        text = render_json(result)
        for forbidden in ("tool log noise", "tool error noise", "stdout", "stderr"):
            assert forbidden not in text


# --------------------------------------------------------------------------
# JSON safety and determinism
# --------------------------------------------------------------------------


class TestJsonSafety:
    def test_no_absolute_paths_env_values_secrets_or_argv(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run()
        assert code == 0
        text = render_json(result)
        assert str(tmp_path) not in text
        assert str(tmp_path).replace("\\", "/") not in text
        assert str(fixture.jar) not in text
        assert str(fixture.java) not in text
        assert "outside_materials" not in text
        assert "release.cer" not in text
        assert "release.p7b" not in text
        assert "release.p12" not in text
        for secret in (ALIAS, KEY_PASSWORD, KEYSTORE_PASSWORD):
            assert secret not in text
        assert '"argv"' not in text
        assert "-keyPwd" in text  # the option *name* is part of the shape
        assert not Path(result["input"]["relpath"]).is_absolute()
        assert result["input"]["relpath"].startswith("apps/harmony/")

    def test_failure_json_leaks_no_values_either(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        output = hap_path(fixture.repo).with_name("entry-default-signed.hap")
        output.write_bytes(b"previous-signed-bytes")
        result, code = fixture.run()
        assert code == 1
        text = render_json(result)
        assert str(tmp_path) not in text
        for secret in (ALIAS, KEY_PASSWORD, KEYSTORE_PASSWORD):
            assert secret not in text
        assert result["failures"][0]["detail"] == {"relpath": default_output_relpath()}

    def test_traversal_attempts_put_no_path_in_the_json(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        for request in (
            {"hap": str(tmp_path / "outside-unsigned.hap")},
            {"out": str(tmp_path / "evil-signed.hap")},
            {"out": "../evil-signed.hap"},
            {"out": "..\\evil-signed.hap"},
        ):
            result, code = fixture.run(**request)
            assert code == 1
            text = render_json(result)
            assert str(tmp_path) not in text
            assert "evil" not in text
            assert "outside-unsigned" not in text

    def test_summary_has_no_values_and_is_honest(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run()
        assert code == 0
        summary = render_summary(result)
        assert "status=ok" in summary
        assert "exit=0" in summary
        assert "signed=false" in summary
        assert "claimed_signed=true" in summary
        assert default_output_relpath() in summary
        assert str(tmp_path) not in summary
        for secret in (ALIAS, KEY_PASSWORD, KEYSTORE_PASSWORD):
            assert secret not in summary

    def test_blocked_summary_reports_the_blocked_status(self, tmp_path):
        repo = make_repo(tmp_path)
        result, code = run_sign_hap(repo, hap=str(hap_path(repo)))
        assert code == 2
        summary = render_summary(result)
        assert "status=blocked_by_external_materials" in summary
        assert "exit=2" in summary
        assert "claimed_signed=false" in summary

    def test_success_json_is_deterministic(self, tmp_path, monkeypatch):
        # Two independent checkouts: the JSON carries repo-relative facts only,
        # so identical inputs must produce byte-identical output.
        first, first_code = Fixture(tmp_path / "a", monkeypatch).run()
        second, second_code = Fixture(tmp_path / "b", monkeypatch).run()
        assert first_code == second_code == 0
        assert render_json(first) == render_json(second)

    def test_failure_json_is_deterministic(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        first, first_code = fixture.run(runner=FakeSignTool(returncode=1))
        second, second_code = fixture.run(runner=FakeSignTool(returncode=1))
        assert first_code == second_code == 1
        assert render_json(first) == render_json(second)

    def test_default_repo_root_is_this_checkout(self):
        assert DEFAULT_REPO_ROOT == REPO_ROOT


# --------------------------------------------------------------------------
# CLI (paths that never start a signing process)
# --------------------------------------------------------------------------


class TestCli:
    SIGN_HAP = REPO_ROOT / "tools/harmony_release/sign_hap.py"

    def _clean_env(self) -> dict:
        excluded = {*MATERIAL_ENV_VARS, *CREDENTIAL_ENV_VARS, SDK_HOME_ENV_VAR}
        return {k: v for k, v in os.environ.items() if k not in excluded}

    def _run_cli(self, repo: Path, *extra: str):
        proc = subprocess.run(
            [sys.executable, str(self.SIGN_HAP), "--repo-root", str(repo), *extra],
            capture_output=True, text=True, env=self._clean_env(), cwd=str(REPO_ROOT),
        )
        return proc

    def test_cli_blocked_exit_2_with_json_and_summary(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        java, jar = make_toolchain_files(tmp_path)
        proc = self._run_cli(
            repo, "--hap", HAP_RELPATH.as_posix(), "--java", str(java), "--jar", str(jar)
        )
        assert proc.returncode == 2
        payload = json.loads(proc.stdout)
        assert payload["status"] == "blocked_by_external_materials"
        assert payload["exit_code"] == 2
        assert payload["steps"] == []
        assert payload["toolchain"] is None
        assert payload["signed"] is False
        assert "status=blocked_by_external_materials" in proc.stderr
        for text in (proc.stdout, proc.stderr):
            assert str(tmp_path) not in text
            assert "fake_tools" not in text

    def test_cli_missing_hap_argument_exit_1(self, tmp_path):
        repo = make_repo(tmp_path)
        proc = self._run_cli(repo)
        assert proc.returncode == 1
        payload = json.loads(proc.stdout)
        assert [f["code"] for f in payload["failures"]] == ["input_hap_missing"]
        assert "failures=input_hap_missing" in proc.stderr

    def test_cli_quiet_suppresses_summary_only(self, tmp_path):
        repo = make_repo(tmp_path)
        proc = self._run_cli(repo, "--quiet")
        assert proc.returncode == 1
        assert proc.stderr == ""
        assert json.loads(proc.stdout)["exit_code"] == 1

    def test_cli_documented_option_set_is_stable(self, tmp_path):
        repo = make_repo(tmp_path)
        proc = self._run_cli(repo)
        payload = json.loads(proc.stdout)
        assert payload["command"]["option_names"] == list(SIGN_OPTION_NAMES)
        assert payload["command"]["program"] == "java"
        assert payload["command"]["jar_name"] == "hap-sign-tool.jar"
