"""Tests for tools.harmony_release.verify_signature (HAP verification wrapper).

Every test injects a fake runner, a fake toolchain resolver and/or a fake
profile reader and uses placeholder files inside temporary directories: java
and hap-sign-tool.jar are never executed, no process is spawned, no real
signature is produced or verified, no credential is read and no device or
signing material is touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import tools.harmony_release.verify_signature as verify_module
from tools.harmony_release.preflight import MATERIAL_ENV_VARS
from tools.harmony_release.sign_hap import CREDENTIAL_ENV_VARS, SDK_HOME_ENV_VAR
from tools.harmony_release.verify_signature import (
    BASIS_NO_VERDICT,
    BASIS_NOT_RUN,
    BASIS_REJECTED_WITH_MATERIAL,
    BASIS_REJECTED_WITHOUT_MATERIAL,
    BASIS_VERIFIER_OK,
    DEFAULT_BUNDLE_NAME,
    EXIT_FAILURE,
    EXIT_UNAVAILABLE,
    EXIT_VALID,
    PROFILE_MAX_BYTES,
    PROFILE_SUFFIXES,
    SCRUBBED_ENV_VARS,
    STATUS_INVALID_SIGNATURE,
    STATUS_PROFILE_BUNDLE_MISMATCH,
    STATUS_REQUEST_INVALID,
    STATUS_SIGNED_AND_VALID,
    STATUS_TOOLCHAIN_UNAVAILABLE,
    STATUS_TOOL_FAILURE,
    STATUS_UNSIGNED,
    STEP_NAME,
    VERIFY_IN_FORM,
    VERIFY_OPTION_NAMES,
    VERIFY_SUBCOMMAND,
    CommandResult,
    Toolchain,
    _exit_code_for,
    command_shape,
    main,
    read_profile_facts,
    render_json,
    render_summary,
    resolve_toolchain,
    run_verify_signature,
    validate_request,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HAP_RELPATH = Path(
    "apps/harmony/entry/build/default/outputs/default/entry-default-signed.hap"
)
HAP_BYTES = b"fake-signed-hap-bytes-not-a-real-artifact"

# Markers that must never reach the JSON, a summary or an error message.
BUNDLE_NAME_MARKER = "com.leaky-bundle-must-never-appear"
PAYLOAD_MARKER = "payload-marker-must-never-appear"
PROXY_MARKER = "http://user:pass@proxy-marker-must-never-appear.invalid"


def profile_payload(bundle_name: str = DEFAULT_BUNDLE_NAME, extra: dict | None = None) -> bytes:
    """A placeholder provisioning profile payload (never a real profile)."""
    data = {
        "version-name": "2.0.0",
        "type": "release",
        "bundle-info": {
            "developer-id": PAYLOAD_MARKER,
            "distribution-certificate": PAYLOAD_MARKER,
            "bundle-name": bundle_name,
        },
    }
    if extra:
        data.update(extra)
    return json.dumps(data).encode("utf-8")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Keep host materials, credentials, SDK paths and proxies out of tests."""
    for name in (
        *MATERIAL_ENV_VARS, *CREDENTIAL_ENV_VARS, SDK_HOME_ENV_VAR,
        "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
    ):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------


def make_repo(tmp_path: Path, name: str = "repo") -> Path:
    repo = tmp_path / name
    (repo / HAP_RELPATH.parent).mkdir(parents=True)
    (repo / HAP_RELPATH).write_bytes(HAP_BYTES)
    return repo


def make_toolchain_files(tmp_path: Path) -> tuple:
    """Stand-in java + jar: existing placeholder files, never executed."""
    tools_dir = tmp_path / "fake_tools"
    tools_dir.mkdir(exist_ok=True)
    java = tools_dir / "java"
    jar = tools_dir / "hap-sign-tool.jar"
    java.write_bytes(b"not-a-real-java")
    jar.write_bytes(b"not-a-real-jar")
    return java, jar


class FakeVerifyTool:
    """Records calls and replays a canned verifier outcome.

    Never spawns anything. ``dump`` is the profile payload the verifier would
    write to ``-outProfile`` (``None`` = write nothing, like an unsigned HAP).
    """

    def __init__(self, returncode: int = 0, dump: bytes | None = None,
                 write_cert_chain: bool = True, raise_oserror: bool = False):
        self.calls = []
        self.returncode = returncode
        self.dump = dump
        self.write_cert_chain = write_cert_chain
        self.raise_oserror = raise_oserror

    def __call__(self, argv, cwd, env):
        argv = tuple(argv)
        self.calls.append({"argv": argv, "cwd": Path(cwd), "env": dict(env)})
        if self.raise_oserror:
            raise OSError("spawn failed marker that must never be recorded")
        if self.dump is not None:
            Path(argv[argv.index("-outProfile") + 1]).write_bytes(self.dump)
        if self.write_cert_chain:
            Path(argv[argv.index("-outCertChain") + 1]).write_bytes(
                b"placeholder-not-a-real-cert-chain"
            )
        return CommandResult(
            argv=argv,
            cwd=Path(cwd),
            returncode=self.returncode,
            stdout=f"tool log noise {PAYLOAD_MARKER}",
            stderr=f"tool error noise {PAYLOAD_MARKER}",
        )


class FakeResolver:
    """Records toolchain probes; returns a placeholder toolchain or failures."""

    def __init__(self, java: Path | None, jar: Path | None,
                 failures: list | None = None):
        self.java = java
        self.jar = jar
        self.failures = failures or []
        self.calls = []

    def __call__(self, java_program, jar, sdk_home):
        self.calls.append({
            "java_program": java_program, "jar": jar, "sdk_home": sdk_home,
        })
        if self.failures:
            return None, list(self.failures)
        return Toolchain(
            java=self.java, jar=self.jar, java_source="explicit",
            jar_source="explicit", jar_candidate=None,
        ), []


class Fixture:
    """A ready-to-run success fixture with everything wired."""

    def __init__(self, tmp_path, monkeypatch, **overrides):
        self.repo = make_repo(tmp_path)
        self.java, self.jar = make_toolchain_files(tmp_path)
        self.runner = overrides.pop("runner", FakeVerifyTool(dump=profile_payload()))
        self.resolver = overrides.pop("resolver", FakeResolver(self.java, self.jar))
        self.kwargs = dict(
            hap=str(HAP_RELPATH),
            java=str(self.java),
            jar=str(self.jar),
            runner=self.runner,
            toolchain_resolver=self.resolver,
        )
        self.kwargs.update(overrides)

    def run(self, **overrides):
        kwargs = dict(self.kwargs)
        kwargs.update(overrides)
        return run_verify_signature(self.repo, **kwargs)

    def argv(self):
        return list(self.runner.calls[0]["argv"])

    def dump_dir(self):
        argv = self.argv()
        return Path(argv[argv.index("-outProfile") + 1]).parent


def outside_material(tmp_path: Path, name: str = "release.json",
                     bundle_name: str = DEFAULT_BUNDLE_NAME) -> Path:
    """Profile material that lives outside the repo (safety rules allow it)."""
    outside = tmp_path / "outside_materials"
    outside.mkdir(exist_ok=True)
    path = outside / name
    path.write_bytes(profile_payload(bundle_name))
    return path


def failure_codes(result: dict) -> list:
    return [item["code"] for item in result["failures"]]


def warning_codes(result: dict) -> list:
    return [item["code"] for item in result["warnings"]]


# --------------------------------------------------------------------------
# profile payload reader
# --------------------------------------------------------------------------


class TestProfileReader:
    def test_reads_plain_json_payload(self, tmp_path):
        path = tmp_path / "profile.json"
        path.write_bytes(profile_payload())
        facts, error = read_profile_facts(path)
        assert error is None
        assert facts == {
            "format": "json",
            "bundle_name": DEFAULT_BUNDLE_NAME,
            "bundle_name_present": True,
        }

    def test_reads_payload_embedded_in_a_container(self, tmp_path):
        """A p7b payload is stored contiguously inside the container."""
        path = tmp_path / "profile.p7b"
        path.write_bytes(b"\x30\x82\x0d\x0aDER-BYTES" + profile_payload() + b"\x00\x01")
        facts, error = read_profile_facts(path)
        assert error is None
        assert facts["format"] == "embedded_json"
        assert facts["bundle_name"] == DEFAULT_BUNDLE_NAME

    def test_opaque_container_is_reported_as_a_category(self, tmp_path):
        path = tmp_path / "profile.p7b"
        path.write_bytes(os.urandom(256))
        facts, error = read_profile_facts(path)
        assert facts is None
        assert error == "profile_payload_not_json"

    def test_missing_bundle_name_is_presence_not_a_value(self, tmp_path):
        path = tmp_path / "profile.json"
        path.write_bytes(json.dumps({"type": "release"}).encode("utf-8"))
        facts, error = read_profile_facts(path)
        assert error is None
        assert facts["bundle_name"] is None
        assert facts["bundle_name_present"] is False

    def test_json_that_is_not_an_object_is_rejected(self, tmp_path):
        path = tmp_path / "profile.json"
        path.write_bytes(b"[1, 2, 3]")
        facts, error = read_profile_facts(path)
        assert facts is None
        assert error == "profile_payload_not_json"

    def test_empty_file_is_reported(self, tmp_path):
        path = tmp_path / "profile.json"
        path.write_bytes(b"")
        facts, error = read_profile_facts(path)
        assert facts is None
        assert error == "profile_empty"

    def test_oversized_payload_is_reported_without_reading_it_into_json(self, tmp_path):
        path = tmp_path / "profile.json"
        path.write_bytes(b"{" + b" " * PROFILE_MAX_BYTES + b"}")
        facts, error = read_profile_facts(path)
        assert facts is None
        assert error == "profile_too_large"

    def test_unreadable_path_is_reported(self, tmp_path):
        facts, error = read_profile_facts(tmp_path / "absent.json")
        assert facts is None
        assert error == "profile_unreadable"


# --------------------------------------------------------------------------
# success path
# --------------------------------------------------------------------------


class TestSuccess:
    def test_signed_and_valid_records_honest_facts(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run()

        assert code == EXIT_VALID
        assert result["status"] == STATUS_SIGNED_AND_VALID
        assert result["failures"] == []
        assert result["warnings"] == []
        assert result["mode"] == "verify_only"
        assert result["signing_performed"] is False
        assert result["device_access"] is False
        assert result["expected_bundle_name"] == DEFAULT_BUNDLE_NAME

        assert result["input"] == {
            "relpath": HAP_RELPATH.as_posix(),
            "size_bytes": len(HAP_BYTES),
            "sha256": hashlib.sha256(HAP_BYTES).hexdigest().upper(),
            "filename_has_signed": True,
            "filename_has_unsigned": False,
            "inside_repository": True,
            "filename_used_for_signedness": False,
        }
        assert result["signature"] == {
            "signed": True,
            "verified": True,
            "basis": BASIS_VERIFIER_OK,
            "filename_used_for_signedness": False,
        }
        assert result["profile"]["facts"] == {
            "available": True,
            "sources": ["hap_profile"],
            "primary_source": "hap_profile",
            "format": "json",
            "bundle_name_present": True,
            "matches_expected": True,
            "errors": {},
        }
        assert result["profile"]["material"]["checked"] is False
        assert result["verifier"] == {
            "stage": "completed", "ran": True, "exit_code": 0,
        }
        assert result["steps"] == [{"name": STEP_NAME, "exit_code": 0}]

    def test_wraps_the_documented_verify_app_command(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        _, code = fixture.run()
        assert code == EXIT_VALID

        argv = fixture.argv()
        profile_out = Path(argv[argv.index("-outProfile") + 1])
        cert_chain_out = Path(argv[argv.index("-outCertChain") + 1])
        assert argv[:4] == [str(fixture.java), "-jar", str(fixture.jar), VERIFY_SUBCOMMAND]
        assert argv[4:6] == ["-inFile", str(fixture.repo / HAP_RELPATH)]
        assert argv[argv.index("-inForm") + 1] == VERIFY_IN_FORM
        # Exactly the documented option names, nothing else ("-jar" is the
        # launcher flag, not a verify-app option).
        assert [
            item for item in argv if item.startswith("-") and item != "-jar"
        ] == list(VERIFY_OPTION_NAMES)

        result, _ = fixture.run()
        assert result["command"] == {
            "program": fixture.java.name,
            "jar_name": fixture.jar.name,
            "subcommand": VERIFY_SUBCOMMAND,
            "in_form": VERIFY_IN_FORM,
            "cwd": ".",
            "option_names": list(VERIFY_OPTION_NAMES),
            "dump_option_names": ["-outCertChain", "-outProfile"],
            "secret_option_names": [],
            "env_keys": [],
            "stripped_env_keys": list(SCRUBBED_ENV_VARS),
        }
        assert command_shape("java")["subcommand"] == "verify-app"
        # The dump paths exist while the child runs, and live outside the repo.
        assert profile_out.parent == cert_chain_out.parent
        assert not profile_out.parent.is_relative_to(fixture.repo.resolve())

    def test_no_credential_is_read_or_added(self, tmp_path, monkeypatch):
        for name in CREDENTIAL_ENV_VARS:
            monkeypatch.setenv(name, PAYLOAD_MARKER)
        monkeypatch.setenv("AIOS_HARMONY_PROFILE_PATH", str(tmp_path / "x.p7b"))
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run()

        assert code == EXIT_VALID
        assert result["command"]["env_keys"] == []
        assert result["command"]["secret_option_names"] == []
        assert result["command"]["stripped_env_keys"] == list(SCRUBBED_ENV_VARS)
        child_env = fixture.runner.calls[0]["env"]
        for name in (*CREDENTIAL_ENV_VARS, "AIOS_HARMONY_PROFILE_PATH"):
            assert name not in child_env
        assert "-keyPwd" not in fixture.argv()

    def test_output_dump_directory_is_removed_after_the_run(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        _, code = fixture.run()
        assert code == EXIT_VALID
        assert not fixture.dump_dir().exists()

    def test_no_signing_material_is_left_inside_the_repository(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        fixture.run()
        leftovers = sorted(
            path.relative_to(fixture.repo).as_posix()
            for path in fixture.repo.rglob("*")
            if path.is_file() and path.suffix.lower() in {".cer", ".p7b", ".p12", ".jks"}
        )
        assert leftovers == []

    def test_resolver_is_the_default_injectable_seam(self, tmp_path, monkeypatch):
        """Without an injected resolver the real one is used - and fails closed."""
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(toolchain_resolver=None, java="no-such-java-binary")
        assert code == EXIT_UNAVAILABLE
        assert result["status"] == STATUS_TOOLCHAIN_UNAVAILABLE
        assert "java_not_found" in failure_codes(result)
        assert fixture.runner.calls == []
        assert resolve_toolchain is verify_module.resolve_toolchain


# --------------------------------------------------------------------------
# unsigned / invalid signature
# --------------------------------------------------------------------------


class TestUnsigned:
    def test_rejected_artifact_without_signature_material(self, tmp_path, monkeypatch):
        runner = FakeVerifyTool(returncode=1, dump=None, write_cert_chain=False)
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        result, code = fixture.run()

        assert code == EXIT_FAILURE
        assert result["status"] == STATUS_UNSIGNED
        assert result["signature"]["signed"] is False
        assert result["signature"]["verified"] is False
        assert result["signature"]["basis"] == BASIS_REJECTED_WITHOUT_MATERIAL
        assert failure_codes(result) == ["hap_unsigned"]
        assert result["failures"][0]["detail"] == {"exit_code": 1}
        assert result["profile"]["facts"]["available"] is False
        assert result["profile"]["facts"]["errors"] == {
            "hap_profile": "profile_not_extracted",
        }
        assert result["verifier"]["exit_code"] == 1

    def test_tool_failure_uses_its_own_basis(self, tmp_path, monkeypatch):
        runner = FakeVerifyTool(returncode=2, dump=None, write_cert_chain=False)
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        result, code = fixture.run()
        assert code == EXIT_FAILURE
        assert result["status"] == STATUS_UNSIGNED
        assert result["failures"][0]["detail"] == {"exit_code": 2}


class TestInvalidSignature:
    def test_verifier_rejects_an_artifact_that_carries_signature_material(
        self, tmp_path, monkeypatch
    ):
        runner = FakeVerifyTool(returncode=1, dump=profile_payload())
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        result, code = fixture.run()

        assert code == EXIT_FAILURE
        assert result["status"] == STATUS_INVALID_SIGNATURE
        assert result["signature"]["signed"] is True
        assert result["signature"]["verified"] is False
        assert result["signature"]["basis"] == BASIS_REJECTED_WITH_MATERIAL
        assert failure_codes(result) == ["hap_signature_invalid"]
        assert result["profile"]["facts"]["available"] is True
        assert result["profile"]["facts"]["matches_expected"] is True


# --------------------------------------------------------------------------
# profile bundle-name
# --------------------------------------------------------------------------


class TestProfileBundleName:
    def test_mismatching_artifact_profile_fails_closed(self, tmp_path, monkeypatch):
        runner = FakeVerifyTool(dump=profile_payload(BUNDLE_NAME_MARKER))
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        result, code = fixture.run()

        assert code == EXIT_FAILURE
        assert result["status"] == STATUS_PROFILE_BUNDLE_MISMATCH
        assert failure_codes(result) == ["profile_bundle_mismatch"]
        assert result["failures"][0]["detail"] == {
            "expected_bundle_name": DEFAULT_BUNDLE_NAME,
            "mismatching_sources": ["hap_profile"],
        }
        assert result["profile"]["facts"]["matches_expected"] is False
        # The signature itself verified; the identity did not.
        assert result["signature"] == {
            "signed": True,
            "verified": True,
            "basis": BASIS_VERIFIER_OK,
            "filename_used_for_signedness": False,
        }

    def test_mismatch_outranks_unsigned(self, tmp_path, monkeypatch):
        """Documented precedence: a contradiction wins over a missing verdict."""
        runner = FakeVerifyTool(
            returncode=1, dump=profile_payload(BUNDLE_NAME_MARKER)
        )
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        result, code = fixture.run()
        assert code == EXIT_FAILURE
        assert result["status"] == STATUS_PROFILE_BUNDLE_MISMATCH
        assert failure_codes(result) == ["profile_bundle_mismatch"]
        assert result["verifier"]["exit_code"] == 1

    def test_wrong_expected_bundle_name_also_fails_closed(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(bundle_name="com.example.other")
        assert code == EXIT_FAILURE
        assert result["status"] == STATUS_PROFILE_BUNDLE_MISMATCH
        assert result["expected_bundle_name"] == "com.example.other"
        assert result["profile"]["facts"]["matches_expected"] is False

    def test_explicit_bundle_name_can_be_satisfied(self, tmp_path, monkeypatch):
        runner = FakeVerifyTool(dump=profile_payload("com.example.other"))
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        result, code = fixture.run(bundle_name="com.example.other")
        assert code == EXIT_VALID
        assert result["status"] == STATUS_SIGNED_AND_VALID

    def test_profile_without_a_bundle_name_fails_closed(self, tmp_path, monkeypatch):
        payload = json.dumps({"type": "release"}).encode("utf-8")
        runner = FakeVerifyTool(dump=payload)
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        result, code = fixture.run()
        assert code == EXIT_FAILURE
        assert result["status"] == STATUS_PROFILE_BUNDLE_MISMATCH
        assert failure_codes(result) == ["profile_bundle_name_missing"]
        assert result["failures"][0]["detail"] == {"sources": ["hap_profile"]}
        assert result["profile"]["facts"]["bundle_name_present"] is False
        assert result["profile"]["facts"]["matches_expected"] is False

    def test_explicit_profile_material_is_checked_when_available(
        self, tmp_path, monkeypatch
    ):
        profile = outside_material(tmp_path, bundle_name="com.example.other")
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(profile=str(profile))

        assert code == EXIT_FAILURE
        assert result["status"] == STATUS_PROFILE_BUNDLE_MISMATCH
        assert result["profile"]["material"] == {
            "checked": True,
            "valid": True,
            "error": None,
            "allowed_suffixes": list(PROFILE_SUFFIXES),
        }
        assert result["profile"]["facts"]["primary_source"] == "hap_profile"
        assert result["profile"]["facts"]["sources"] == [
            "hap_profile", "profile_option",
        ]
        assert result["failures"][0]["detail"] == {
            "expected_bundle_name": DEFAULT_BUNDLE_NAME,
            "mismatching_sources": ["profile_option"],
        }

    def test_every_available_source_is_checked(self, tmp_path, monkeypatch):
        """A matching artifact profile never excuses mismatching material."""
        profile = outside_material(tmp_path, bundle_name=DEFAULT_BUNDLE_NAME)
        runner = FakeVerifyTool(dump=profile_payload(BUNDLE_NAME_MARKER))
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        result, code = fixture.run(profile=str(profile))

        assert code == EXIT_FAILURE
        assert result["status"] == STATUS_PROFILE_BUNDLE_MISMATCH
        assert result["profile"]["facts"]["sources"] == [
            "hap_profile", "profile_option",
        ]
        assert result["failures"][0]["detail"]["mismatching_sources"] == ["hap_profile"]

    def test_explicit_profile_is_the_documented_fallback(self, tmp_path, monkeypatch):
        """No dump from the artifact: the explicit profile material is used."""
        profile = outside_material(tmp_path, bundle_name=BUNDLE_NAME_MARKER)
        runner = FakeVerifyTool(returncode=1, dump=None, write_cert_chain=False)
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        result, code = fixture.run(profile=str(profile))

        assert code == EXIT_FAILURE
        assert result["status"] == STATUS_PROFILE_BUNDLE_MISMATCH
        assert result["profile"]["facts"]["sources"] == ["profile_option"]
        assert result["profile"]["facts"]["primary_source"] == "profile_option"
        assert result["profile"]["facts"]["errors"] == {
            "hap_profile": "profile_not_extracted",
        }
        # External material never proves the artifact is signed.
        assert result["signature"]["signed"] is False

    def test_explicit_profile_that_matches_still_reports_unsigned(
        self, tmp_path, monkeypatch
    ):
        profile = outside_material(tmp_path, bundle_name=DEFAULT_BUNDLE_NAME)
        runner = FakeVerifyTool(returncode=1, dump=None, write_cert_chain=False)
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        result, code = fixture.run(profile=str(profile))
        assert code == EXIT_FAILURE
        assert result["status"] == STATUS_UNSIGNED
        assert result["profile"]["facts"]["sources"] == ["profile_option"]
        assert result["profile"]["facts"]["matches_expected"] is True

    def test_requested_profile_whose_facts_are_unavailable_is_unanswerable(
        self, tmp_path, monkeypatch
    ):
        outside = tmp_path / "outside_materials"
        outside.mkdir()
        opaque = outside / "release.p7b"
        opaque.write_bytes(os.urandom(128))
        runner = FakeVerifyTool(returncode=1, dump=None, write_cert_chain=False)
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        result, code = fixture.run(profile=str(opaque))

        assert code == EXIT_UNAVAILABLE
        assert result["status"] == STATUS_REQUEST_INVALID
        assert failure_codes(result) == ["profile_facts_unavailable"]
        assert result["failures"][0]["detail"] == {
            "source": "profile_option", "error": "profile_payload_not_json",
        }
        assert result["profile"]["facts"]["errors"] == {
            "hap_profile": "profile_not_extracted",
            "profile_option": "profile_payload_not_json",
        }
        # A rejected artifact is still reported honestly (no signature claim).
        assert result["signature"]["signed"] is False
        assert result["signature"]["verified"] is False

    def test_unanswerable_request_keeps_the_signature_verdict_separate(
        self, tmp_path, monkeypatch
    ):
        """The signature verdict survives an unanswerable profile request."""
        outside = tmp_path / "outside_materials"
        outside.mkdir()
        opaque = outside / "release.p7b"
        opaque.write_bytes(os.urandom(128))
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(profile=str(opaque))

        assert code == EXIT_UNAVAILABLE
        assert result["status"] == STATUS_REQUEST_INVALID
        assert result["signature"]["signed"] is True
        assert result["signature"]["verified"] is True

    def test_unparseable_artifact_profile_is_only_a_warning(self, tmp_path, monkeypatch):
        runner = FakeVerifyTool(dump=os.urandom(128))
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        result, code = fixture.run()
        assert code == EXIT_VALID
        assert result["status"] == STATUS_SIGNED_AND_VALID
        assert warning_codes(result) == ["profile_facts_unavailable"]
        assert result["warnings"][0]["detail"] == {
            "source": "hap_profile", "error": "profile_payload_not_json",
        }

    def test_valid_artifact_without_a_profile_dump_warns(self, tmp_path, monkeypatch):
        runner = FakeVerifyTool(returncode=0, dump=None)
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        result, code = fixture.run()
        assert code == EXIT_VALID
        assert warning_codes(result) == ["profile_not_extracted"]


# --------------------------------------------------------------------------
# tool failure
# --------------------------------------------------------------------------


class TestToolFailure:
    def test_spawn_failure_is_a_tool_failure(self, tmp_path, monkeypatch):
        runner = FakeVerifyTool(raise_oserror=True)
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        result, code = fixture.run()

        assert code == EXIT_FAILURE
        assert result["status"] == STATUS_TOOL_FAILURE
        assert failure_codes(result) == ["verify_tool_spawn_failed"]
        assert result["failures"][0]["detail"] == {"error": "spawn_failed"}
        assert result["verifier"] == {
            "stage": "spawn_failed", "ran": False, "exit_code": None,
        }
        assert result["steps"] == []
        assert result["signature"]["signed"] is None
        assert result["signature"]["verified"] is None
        assert result["signature"]["basis"] == BASIS_NO_VERDICT

    def test_child_output_is_never_recorded(self, tmp_path, monkeypatch):
        runner = FakeVerifyTool(returncode=1, dump=None, write_cert_chain=False)
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        result, code = fixture.run()
        blob = render_json(result) + render_summary(result)
        assert code == EXIT_FAILURE
        assert "tool log noise" not in blob
        assert "tool error noise" not in blob
        assert PAYLOAD_MARKER not in blob
        assert "stdout" not in blob
        assert "stderr" not in blob


# --------------------------------------------------------------------------
# toolchain
# --------------------------------------------------------------------------


class TestToolchain:
    def test_missing_toolchain_blocks_before_any_spawn(self, tmp_path, monkeypatch):
        failures = [{"code": "sign_tool_jar_not_found",
                     "detail": {"source": "deveco_sdk_home", "jar_name": "hap-sign-tool.jar"}}]
        resolver = FakeResolver(None, None, failures=failures)
        fixture = Fixture(tmp_path, monkeypatch, resolver=resolver)
        result, code = fixture.run()

        assert code == EXIT_UNAVAILABLE
        assert result["status"] == STATUS_TOOLCHAIN_UNAVAILABLE
        assert failure_codes(result) == ["sign_tool_jar_not_found"]
        assert result["toolchain"] is None
        assert result["steps"] == []
        assert fixture.runner.calls == []
        assert result["signature"]["signed"] is None
        assert result["signature"]["basis"] == BASIS_NOT_RUN

    def test_missing_java_blocks_with_the_resolver_default(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(
            toolchain_resolver=None, java="no-such-java-binary", jar=None,
            sdk_home=str(tmp_path / "no-such-sdk"),
        )
        assert code == EXIT_UNAVAILABLE
        assert result["status"] == STATUS_TOOLCHAIN_UNAVAILABLE
        assert "sign_tool_jar_not_found" in failure_codes(result)
        assert fixture.runner.calls == []

    def test_resolved_toolchain_is_recorded_by_name_only(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run()
        assert code == EXIT_VALID
        assert result["toolchain"] == {
            "java_name": fixture.java.name,
            "java_source": "explicit",
            "jar_name": fixture.jar.name,
            "jar_source": "explicit",
            "jar_candidate": None,
        }


# --------------------------------------------------------------------------
# request validation
# --------------------------------------------------------------------------


class TestRequestValidation:
    def test_missing_hap_argument(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(hap=None)
        assert code == EXIT_UNAVAILABLE
        assert result["status"] == STATUS_REQUEST_INVALID
        assert failure_codes(result) == ["input_hap_missing"]
        assert result["failures"][0]["detail"] == {"argument": "--hap"}
        assert fixture.runner.calls == []
        assert fixture.resolver.calls == []
        assert result["input"] is None

    def test_hap_outside_the_repository_is_refused(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        outside = tmp_path / "outside" / "entry-default-signed.hap"
        outside.parent.mkdir()
        outside.write_bytes(HAP_BYTES)
        result, code = fixture.run(hap=str(outside))
        assert code == EXIT_UNAVAILABLE
        assert failure_codes(result) == ["input_hap_outside_repository"]
        assert fixture.runner.calls == []

    def test_path_traversal_out_of_the_repository_is_refused(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        (tmp_path / "escape.hap").write_bytes(HAP_BYTES)
        result, code = fixture.run(hap="../../escape.hap")
        assert code == EXIT_UNAVAILABLE
        assert failure_codes(result) == ["input_hap_outside_repository"]
        assert str(tmp_path) not in render_json(result)
        assert fixture.runner.calls == []

    def test_traversal_that_stays_inside_the_repository_is_allowed(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(hap=f"entry/../{HAP_RELPATH.as_posix()}")
        assert code == EXIT_VALID
        assert result["input"]["relpath"] == HAP_RELPATH.as_posix()

    def test_missing_hap_file(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(hap="apps/harmony/nope.hap")
        assert code == EXIT_UNAVAILABLE
        assert failure_codes(result) == ["input_hap_missing"]
        assert result["failures"][0]["detail"] == {"relpath": "apps/harmony/nope.hap"}

    def test_hap_that_is_a_directory(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(hap=HAP_RELPATH.parent.as_posix())
        assert code == EXIT_UNAVAILABLE
        assert failure_codes(result) == ["input_hap_missing"]

    def test_empty_hap_path_is_refused(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(hap="")
        assert code == EXIT_UNAVAILABLE
        assert failure_codes(result) == ["input_hap_missing"]

    def test_profile_material_inside_the_repository_is_refused(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        inside = fixture.repo / "release.p7b"
        inside.write_bytes(b"placeholder-not-a-real-profile")
        result, code = fixture.run(profile=str(inside))
        assert code == EXIT_UNAVAILABLE
        assert failure_codes(result) == ["profile_material_invalid"]
        assert result["failures"][0]["detail"]["error"] == "inside_repository"
        assert result["profile"]["material"]["valid"] is False
        assert fixture.runner.calls == []
        assert fixture.resolver.calls == []

    def test_profile_material_with_a_wrong_suffix_is_refused(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        wrong = outside_material(tmp_path, name="release.pem")
        result, code = fixture.run(profile=str(wrong))
        assert code == EXIT_UNAVAILABLE
        assert failure_codes(result) == ["profile_material_invalid"]
        assert result["failures"][0]["detail"] == {
            "option": "--profile", "error": "wrong_extension", "suffix": ".pem",
        }

    def test_profile_material_that_does_not_exist_is_refused(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        absent = tmp_path / "outside_materials" / "absent.p7b"
        result, code = fixture.run(profile=str(absent))
        assert code == EXIT_UNAVAILABLE
        assert result["failures"][0]["detail"]["error"] == "path_not_found"

    def test_profile_material_traversal_is_refused(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run(profile="../escape.p7b")
        assert code == EXIT_UNAVAILABLE
        assert failure_codes(result) == ["profile_material_invalid"]
        assert fixture.runner.calls == []

    def test_invalid_expected_bundle_name_is_refused(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        for bad in ("", "   ", "nodots", "1.com.example", "com..example", "a" * 200):
            result, code = fixture.run(bundle_name=bad)
            assert code == EXIT_UNAVAILABLE
            assert failure_codes(result) == ["bundle_name_invalid"]
            if bad.strip():
                assert bad.strip() not in render_json(result)
            assert result["expected_bundle_name"] == DEFAULT_BUNDLE_NAME

    def test_request_failures_never_carry_a_path_value(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        inside = fixture.repo / "release.p12"
        inside.write_bytes(b"placeholder")
        result, code = fixture.run(hap=str(tmp_path / "outside.hap"), profile=str(inside))
        assert code == EXIT_UNAVAILABLE
        blob = render_json(result) + render_summary(result)
        assert str(tmp_path) not in blob
        assert str(fixture.java) not in blob
        assert "outside.hap" not in blob
        assert failure_codes(result) == [
            "input_hap_outside_repository", "profile_material_invalid",
        ]

    def test_validate_request_returns_a_request_only_when_clean(self, tmp_path):
        repo = make_repo(tmp_path)
        request, failures = validate_request(repo, HAP_RELPATH.as_posix())
        assert failures == []
        assert request.input_path == repo / HAP_RELPATH
        assert request.expected_bundle_name == DEFAULT_BUNDLE_NAME
        assert request.profile_path is None
        assert request.profile_record == {
            "requested": False,
            "material": {
                "checked": False,
                "valid": None,
                "error": None,
                "allowed_suffixes": list(PROFILE_SUFFIXES),
            },
        }


# --------------------------------------------------------------------------
# determinism / leakage
# --------------------------------------------------------------------------


class TestDeterminism:
    def test_success_json_is_byte_identical_across_runs(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        first, code = fixture.run()
        second, code2 = fixture.run()
        assert code == code2 == EXIT_VALID
        assert render_json(first) == render_json(second)

    def test_failure_json_is_byte_identical_across_runs(self, tmp_path, monkeypatch):
        runner = FakeVerifyTool(returncode=1, dump=None, write_cert_chain=False)
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        first, _ = fixture.run()
        second, _ = fixture.run()
        assert render_json(first) == render_json(second)

    def test_failure_and_warning_lists_are_sorted(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, _ = fixture.run()
        assert result["failures"] == sorted(
            result["failures"], key=lambda item: json.dumps(item, sort_keys=True)
        )
        assert result["warnings"] == sorted(
            result["warnings"], key=lambda item: json.dumps(item, sort_keys=True)
        )

    def test_render_json_is_ascii_safe(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, _ = fixture.run()
        blob = render_json(result)
        blob.encode("ascii")

    def test_exit_code_contract(self):
        assert _exit_code_for(STATUS_SIGNED_AND_VALID) == EXIT_VALID
        for status in (STATUS_UNSIGNED, STATUS_INVALID_SIGNATURE,
                       STATUS_PROFILE_BUNDLE_MISMATCH, STATUS_TOOL_FAILURE):
            assert _exit_code_for(status) == EXIT_FAILURE
        for status in (STATUS_REQUEST_INVALID, STATUS_TOOLCHAIN_UNAVAILABLE):
            assert _exit_code_for(status) == EXIT_UNAVAILABLE


class TestLeakage:
    def test_no_absolute_path_argv_or_material_value_leaks(self, tmp_path, monkeypatch):
        profile = outside_material(tmp_path, bundle_name=BUNDLE_NAME_MARKER)
        runner = FakeVerifyTool(returncode=1, dump=None, write_cert_chain=False)
        fixture = Fixture(tmp_path, monkeypatch, runner=runner)
        result, _ = fixture.run(profile=str(profile))
        blob = render_json(result) + render_summary(result)

        for leaked in (
            str(tmp_path), str(fixture.repo), str(fixture.java), str(fixture.jar),
            str(profile), "fake_tools", "outside_materials", "release.json",
            BUNDLE_NAME_MARKER, PAYLOAD_MARKER, PROXY_MARKER,
        ):
            assert leaked not in blob
        assert '"argv"' not in blob
        assert result["command"]["program"] == fixture.java.name

    def test_dump_paths_never_reach_the_report(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, _ = fixture.run()
        blob = render_json(result)
        argv = fixture.argv()
        for flag in ("-outProfile", "-outCertChain"):
            assert argv[argv.index(flag) + 1] not in blob
        assert "hmh-verify-signature" not in blob
        # Option *names* are recorded; their values are not.
        assert "-outProfile" in blob
        assert VERIFY_SUBCOMMAND in blob

    def test_environment_values_never_reach_the_report(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", PROXY_MARKER)
        monkeypatch.setenv("AIOS_HARMONY_KEY_PASSWORD", PAYLOAD_MARKER)
        fixture = Fixture(tmp_path, monkeypatch)
        result, _ = fixture.run()
        blob = render_json(result) + render_summary(result)
        assert PAYLOAD_MARKER not in blob
        assert PROXY_MARKER not in blob

    def test_summary_is_value_free(self, tmp_path, monkeypatch):
        fixture = Fixture(tmp_path, monkeypatch)
        result, code = fixture.run()
        assert code == EXIT_VALID
        summary = render_summary(result)
        assert summary.startswith("harmony_release_verify_signature: status=signed_and_valid")
        assert "exit=0" in summary
        assert "signed=true" in summary
        assert "verified=true" in summary
        assert f"input={HAP_RELPATH.as_posix()}" in summary
        assert "profile_facts=hap_profile" in summary
        assert "bundle_name_matches=true" in summary
        assert str(tmp_path) not in summary


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


class TestCli:
    def test_cli_reports_request_invalid_without_absolute_paths(
        self, tmp_path, monkeypatch, capsys
    ):
        repo = make_repo(tmp_path)
        code = main(["--repo-root", str(repo), "--hap", "apps/harmony/nope.hap"])
        captured = capsys.readouterr()
        assert code == EXIT_UNAVAILABLE
        payload = json.loads(captured.out)
        assert payload["status"] == STATUS_REQUEST_INVALID
        assert payload["exit_code"] == EXIT_UNAVAILABLE
        assert "harmony_release_verify_signature" in captured.err
        assert str(repo) not in captured.out

    def test_cli_blocks_when_the_toolchain_is_unavailable(
        self, tmp_path, monkeypatch, capsys
    ):
        repo = make_repo(tmp_path)
        code = main([
            "--repo-root", str(repo),
            "--hap", HAP_RELPATH.as_posix(),
            "--java", "no-such-java-binary",
            "--sdk-home", str(tmp_path / "no-such-sdk"),
        ])
        captured = capsys.readouterr()
        assert code == EXIT_UNAVAILABLE
        payload = json.loads(captured.out)
        assert payload["status"] == STATUS_TOOLCHAIN_UNAVAILABLE
        assert payload["signing_performed"] is False
        assert payload["device_access"] is False
        assert str(repo) not in captured.out
        assert str(tmp_path) not in captured.out

    def test_cli_quiet_suppresses_the_summary(self, tmp_path, capsys):
        repo = make_repo(tmp_path)
        code = main(["--repo-root", str(repo), "--hap", "nope.hap", "--quiet"])
        captured = capsys.readouterr()
        assert code == EXIT_UNAVAILABLE
        assert captured.err == ""
        json.loads(captured.out)

    def test_cli_defaults_the_expected_bundle_name(self, tmp_path, capsys):
        repo = make_repo(tmp_path)
        code = main(["--repo-root", str(repo), "--hap", "nope.hap"])
        captured = capsys.readouterr()
        assert code == EXIT_UNAVAILABLE
        assert json.loads(captured.out)["expected_bundle_name"] == DEFAULT_BUNDLE_NAME

    def test_module_entry_point_uses_the_same_contract(self, tmp_path):
        """Running the file as a script stays fail-closed and value-free."""
        script = REPO_ROOT / "tools" / "harmony_release" / "verify_signature.py"
        completed = subprocess.run(
            [sys.executable, str(script), "--hap", "nope.hap"],
            capture_output=True, text=True, check=False,
            cwd=str(REPO_ROOT),
        )
        assert completed.returncode == EXIT_UNAVAILABLE
        payload = json.loads(completed.stdout)
        assert payload["status"] == STATUS_REQUEST_INVALID
        assert str(REPO_ROOT) not in completed.stdout + completed.stderr
