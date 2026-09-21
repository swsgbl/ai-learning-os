"""Tests for tools.harmony_release.agc_closure_manifest (AGC closure).

All evidence files are small synthetic JSON documents written into
temporary directories: no device, no build, no signing, no real artifact
and no environment variable is ever touched by these tests.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import tools.harmony_release.agc_closure_manifest as manifest_module
from tools.harmony_release.agc_closure_manifest import (
    EXIT_BLOCKED,
    EXIT_FAILURE,
    EXIT_OK,
    PRODUCTION_READY,
    REQUIRED_GATES,
    TOOL_NAME,
    main,
    render_json,
    render_markdown,
    run_agc_closure_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

PREFLIGHT_TOOL = "harmony_release_preflight"
BUILD_TOOL = "harmony_release_build"
SIGN_TOOL = "harmony_sign_hap"
VERIFY_TOOL = "harmony_release_verify_signature"
DEVICE_PREFLIGHT_TOOL = "harmony_device_preflight"
DEVICE_SMOKE_TOOL = "harmony_device_smoke"

HAP_RELPATH = (
    "apps/harmony/entry/build/default/outputs/default/"
    "entry-default-unsigned.hap"
)
SHA_A = "A" * 64
SHA_B = "B" * 64

# A secret marker that must never reach the manifest or the markdown.
SECRET = "secret-value-must-never-appear"


def _write_report(tmp_path: Path, name: str, report: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def preflight_ok() -> dict:
    return {"tool": PREFLIGHT_TOOL, "status": "ok", "failures": []}


def build_ok() -> dict:
    return {
        "tool": BUILD_TOOL,
        "status": "ok",
        "failures": [],
        "artifact": {
            "relpath": HAP_RELPATH,
            "size_bytes": 128,
            "sha256": SHA_A,
            "filename_has_unsigned": True,
            "name_matches_expected": True,
            "inside_repository": True,
        },
    }


def sign_ok() -> dict:
    return {
        "tool": SIGN_TOOL,
        "status": "ok",
        "failures": [],
        "claimed_signed": True,
        "signedness_verified": False,
    }


def verify_signed() -> dict:
    return {
        "tool": VERIFY_TOOL,
        "status": "signed_and_valid",
        "failures": [],
        "signature": {"signed": True, "verified": True},
    }


def device_ok(tool: str) -> dict:
    return {"tool": tool, "status": "ok", "failures": []}


def all_ok_reports() -> dict:
    return {
        "preflight.json": preflight_ok(),
        "build.json": build_ok(),
        "sign.json": sign_ok(),
        "verify.json": verify_signed(),
        "device_preflight.json": device_ok(DEVICE_PREFLIGHT_TOOL),
        "device_smoke.json": device_ok(DEVICE_SMOKE_TOOL),
    }


def _write_all(tmp_path: Path, reports: dict) -> list:
    paths = []
    for name, report in reports.items():
        paths.append(_write_report(tmp_path, name, report))
    return [str(path) for path in paths]


# ---------------------------------------------------------------------------
# Normal blocked aggregation.
# ---------------------------------------------------------------------------

class TestBlockedAggregation:

    def test_all_ok_is_not_blocked(self, tmp_path):
        inputs = _write_all(tmp_path, all_ok_reports())
        result, code = run_agc_closure_manifest(inputs=inputs)
        assert result["status"] == "ok"
        assert result["blockers"] == []
        assert result["next_actions"] == []
        assert code == EXIT_OK

    def test_preflight_blocked_by_materials_blocks(self, tmp_path):
        reports = all_ok_reports()
        reports["preflight.json"] = {
            "tool": PREFLIGHT_TOOL,
            "status": "blocked_by_external_materials",
            "failures": [],
        }
        result, code = run_agc_closure_manifest(
            inputs=_write_all(tmp_path, reports)
        )
        assert result["status"] == "blocked"
        assert result["production_ready"] is False
        assert code == EXIT_BLOCKED
        gate = {g["name"]: g for g in result["gates"]}["preflight"]
        assert gate["pass"] is False
        assert gate["source_status"] == "blocked_by_external_materials"
        assert "provide_external_signing_materials_out_of_band" in (
            result["next_actions"]
        )

    def test_failure_status_blocks_with_codes(self, tmp_path):
        reports = all_ok_reports()
        reports["device_smoke.json"] = {
            "tool": DEVICE_SMOKE_TOOL,
            "status": "failure",
            "failures": [
                {"code": "install_failed", "detail": {"secret": SECRET}},
                {"code": "launch_failed"},
            ],
        }
        result, _ = run_agc_closure_manifest(
            inputs=_write_all(tmp_path, reports)
        )
        gate = {g["name"]: g for g in result["gates"]}["device_smoke"]
        assert gate["pass"] is False
        assert gate["failure_codes"] == ["install_failed", "launch_failed"]
        blocker = next(
            b for b in result["blockers"] if b["gate"] == "device_smoke"
        )
        assert blocker["detail"]["source_status"] == "failure"
        # Detail payloads (secret values) never travel into the manifest.
        assert json.dumps(result).find(SECRET) == -1

    def test_planned_is_not_evidence(self, tmp_path):
        reports = all_ok_reports()
        reports["device_preflight.json"] = {
            "tool": DEVICE_PREFLIGHT_TOOL,
            "status": "planned",
            "failures": [],
        }
        result, _ = run_agc_closure_manifest(
            inputs=_write_all(tmp_path, reports)
        )
        gate = {g["name"]: g for g in result["gates"]}["device_preflight"]
        assert gate["pass"] is False
        assert result["status"] == "blocked"

    def test_hap_comes_from_release_build_artifact_only(self, tmp_path):
        reports = all_ok_reports()
        inputs = _write_all(tmp_path, reports)
        result, _ = run_agc_closure_manifest(inputs=inputs)
        assert result["hap"]["relpath"] == HAP_RELPATH
        assert result["hap"]["sha256"] == SHA_A
        assert result["hap"]["size_bytes"] == 128

    def test_missing_artifact_record_blocks_build_gate(self, tmp_path):
        reports = all_ok_reports()
        reports["build.json"] = {
            "tool": BUILD_TOOL,
            "status": "ok",
            "failures": [],
            "artifact": None,
        }
        result, _ = run_agc_closure_manifest(
            inputs=_write_all(tmp_path, reports)
        )
        gate = {g["name"]: g for g in result["gates"]}["release_build"]
        assert gate["pass"] is False
        assert result["hap"] is None

    def test_sign_claim_without_output_claim_blocks(self, tmp_path):
        reports = all_ok_reports()
        reports["sign.json"] = {
            "tool": SIGN_TOOL,
            "status": "ok",
            "failures": [],
            "claimed_signed": False,
        }
        result, _ = run_agc_closure_manifest(
            inputs=_write_all(tmp_path, reports)
        )
        gate = {g["name"]: g for g in result["gates"]}["sign_hap"]
        assert gate["pass"] is False
        assert gate["facts"]["claimed_signed"] is False


# ---------------------------------------------------------------------------
# Missing inputs.
# ---------------------------------------------------------------------------

class TestMissingInputs:

    def test_no_inputs_blocks_with_every_gate_missing(self):
        result, code = run_agc_closure_manifest(inputs=[])
        assert result["status"] == "blocked"
        assert code == EXIT_BLOCKED
        assert {b["code"] for b in result["blockers"]} == {"missing_report"}
        assert len(result["blockers"]) == len(REQUIRED_GATES)
        for gate in result["gates"]:
            assert gate["pass"] is False
            assert gate["source_status"] is None

    def test_absent_sign_and_verify_reports_block(self, tmp_path):
        reports = all_ok_reports()
        del reports["sign.json"]
        del reports["verify.json"]
        result, code = run_agc_closure_manifest(
            inputs=_write_all(tmp_path, reports)
        )
        assert result["status"] == "blocked"
        assert code == EXIT_BLOCKED
        gates = {g["name"]: g for g in result["gates"]}
        assert gates["sign_hap"]["source_status"] is None
        assert gates["verify_signature"]["source_status"] is None
        codes = {b["gate"]: b["code"] for b in result["blockers"]}
        assert codes["sign_hap"] == "missing_report"
        assert codes["verify_signature"] == "missing_report"
        assert "run_sign_hap_with_external_materials" in result["next_actions"]
        assert "run_signature_verification" in result["next_actions"]

    def test_nonexistent_input_is_failure_not_manifest(self, tmp_path):
        missing = str(tmp_path / "absent.json")
        result, code = run_agc_closure_manifest(inputs=[missing])
        assert code == EXIT_FAILURE
        assert result["status"] == "failure"
        assert result["failures"][0]["code"] == "input_rejected"
        assert result["failures"][0]["detail"]["error"] == "path_not_found"

    def test_invalid_json_is_failure(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        result, code = run_agc_closure_manifest(inputs=[str(bad)])
        assert code == EXIT_FAILURE
        assert result["failures"][0]["detail"]["error"] == "not_json"

    def test_report_without_tool_field_is_failure(self, tmp_path):
        path = _write_report(tmp_path, "no_tool.json", {"status": "ok"})
        result, code = run_agc_closure_manifest(inputs=[str(path)])
        assert code == EXIT_FAILURE
        assert result["failures"][0]["detail"]["error"] == (
            "tool_or_status_missing"
        )


# ---------------------------------------------------------------------------
# Duplicate tool handling.
# ---------------------------------------------------------------------------

class TestDuplicateTools:

    def test_duplicate_gate_report_blocks(self, tmp_path):
        reports = all_ok_reports()
        reports["verify_copy.json"] = verify_signed()
        result, code = run_agc_closure_manifest(
            inputs=_write_all(tmp_path, reports)
        )
        assert result["status"] == "blocked"
        assert code == EXIT_BLOCKED
        duplicate = next(
            b for b in result["blockers"] if b["code"] == "duplicate_report"
        )
        assert duplicate["gate"] == "verify_signature"
        assert duplicate["detail"]["count"] == 2
        assert duplicate["detail"]["sources"] == [
            "verify.json",
            "verify_copy.json",
        ]
        assert "deduplicate_evidence_reports_per_gate" in result["next_actions"]

    def test_duplicate_choice_is_order_independent(self, tmp_path):
        ok = preflight_ok()
        blocked = {
            "tool": PREFLIGHT_TOOL,
            "status": "blocked_by_external_materials",
            "failures": [],
        }
        first = [
            _write_report(tmp_path, "a.json", ok),
            _write_report(tmp_path, "b.json", blocked),
        ]
        second = [
            _write_report(tmp_path, "b.json", blocked),
            _write_report(tmp_path, "a.json", ok),
        ]
        one, _ = run_agc_closure_manifest(
            inputs=[str(p) for p in first]
        )
        two, _ = run_agc_closure_manifest(
            inputs=[str(p) for p in second]
        )
        # The same evidence set must produce byte-identical manifests.
        assert render_json(one) == render_json(two)
        gate_one = {g["name"]: g for g in one["gates"]}["preflight"]
        gate_two = {g["name"]: g for g in two["gates"]}["preflight"]
        assert gate_one["source_name"] == gate_two["source_name"]
        # blocked_by_external_materials sorts before ok: deterministic pick.
        assert gate_one["source_status"] == "blocked_by_external_materials"

    def test_unknown_tool_report_blocks(self, tmp_path):
        reports = all_ok_reports()
        reports["mystery.json"] = {
            "tool": "some_other_tool",
            "status": "ok",
            "failures": [],
        }
        result, _ = run_agc_closure_manifest(
            inputs=_write_all(tmp_path, reports)
        )
        unclassified = next(
            b for b in result["blockers"] if b["code"] == "unclassified_report"
        )
        assert unclassified["detail"]["tools"] == ["some_other_tool"]
        assert "supply_reports_from_known_release_tools" in result["next_actions"]


# ---------------------------------------------------------------------------
# Unsigned verify and filename signedness.
# ---------------------------------------------------------------------------

class TestSignedness:

    def test_verify_unsigned_blocks(self, tmp_path):
        reports = all_ok_reports()
        reports["verify.json"] = {
            "tool": VERIFY_TOOL,
            "status": "unsigned",
            "failures": [{"code": "hap_unsigned", "detail": {"exit_code": 1}}],
            "signature": {"signed": False, "verified": False},
        }
        result, code = run_agc_closure_manifest(
            inputs=_write_all(tmp_path, reports)
        )
        assert result["status"] == "blocked"
        assert code == EXIT_BLOCKED
        gate = {g["name"]: g for g in result["gates"]}["verify_signature"]
        assert gate["pass"] is False
        assert gate["source_status"] == "unsigned"
        assert "sign_the_unsigned_release_hap_then_verify" in (
            result["next_actions"]
        )

    def test_verify_invalid_signature_blocks(self, tmp_path):
        reports = all_ok_reports()
        reports["verify.json"] = {
            "tool": VERIFY_TOOL,
            "status": "invalid_signature",
            "failures": [],
            "signature": {"signed": True, "verified": False},
        }
        result, _ = run_agc_closure_manifest(
            inputs=_write_all(tmp_path, reports)
        )
        assert result["status"] == "blocked"

    def test_signed_filename_never_implies_signed(self, tmp_path):
        reports = all_ok_reports()
        # An artifact record whose *name* says signed, while the verify
        # report honestly says unsigned: the filename must not win.
        reports["build.json"]["artifact"]["relpath"] = HAP_RELPATH.replace(
            "-unsigned.hap", "-signed.hap"
        )
        reports["build.json"]["artifact"]["sha256"] = SHA_B
        reports["verify.json"] = {
            "tool": VERIFY_TOOL,
            "status": "unsigned",
            "failures": [{"code": "hap_unsigned"}],
            "signature": {"signed": False, "verified": False},
        }
        result, _ = run_agc_closure_manifest(
            inputs=_write_all(tmp_path, reports)
        )
        assert result["status"] == "blocked"
        gate = {g["name"]: g for g in result["gates"]}["verify_signature"]
        assert gate["pass"] is False
        assert gate["facts"]["signature_signed"] is False

    def test_sign_ok_alone_never_satisfies_signedness(self, tmp_path):
        reports = all_ok_reports()
        del reports["verify.json"]
        # sign_hap claims success, but no verification exists at all.
        inputs = _write_all(tmp_path, reports)
        result, code = run_agc_closure_manifest(inputs=inputs)
        assert code == EXIT_BLOCKED
        gates = {g["name"]: g for g in result["gates"]}
        assert gates["sign_hap"]["pass"] is True
        assert gates["verify_signature"]["source_status"] is None


# ---------------------------------------------------------------------------
# Cross-report HAP chain consistency (M14-79).
# ---------------------------------------------------------------------------

class TestEvidenceChainConsistency:

    def test_stale_sign_input_hash_blocks_manifest(self, tmp_path):
        reports = all_ok_reports()
        # sign_hap still points at the previous build's unsigned HAP:
        # every per-gate status passes, but the chain disagrees.
        reports["sign.json"]["input"] = {
            "relpath": HAP_RELPATH,
            "size_bytes": 128,
            "sha256": SHA_B,
            "filename_has_unsigned": True,
            "inside_repository": True,
        }
        result, code = run_agc_closure_manifest(
            inputs=_write_all(tmp_path, reports)
        )
        assert result["status"] == "blocked"
        assert code == EXIT_BLOCKED
        gates = {g["name"]: g for g in result["gates"]}
        # Every per-gate status still passes ...
        assert all(g["pass"] for g in gates.values())
        # ... but the cross-report mismatch blocks the closure.
        blocker = next(
            b for b in result["blockers"]
            if b["code"] == "evidence_hap_mismatch"
        )
        assert blocker["gate"] == "evidence_chain"
        assert blocker["detail"]["link"] == "build_artifact_vs_sign_input"
        assert "rerun_release_chain_so_evidence_shares_one_hap" in (
            result["next_actions"]
        )

    def test_mixed_verify_input_hash_blocks_manifest(self, tmp_path):
        reports = all_ok_reports()
        reports["sign.json"]["artifact"] = {
            "relpath": HAP_RELPATH.replace("-unsigned.hap", "-signed.hap"),
            "size_bytes": 256,
            "sha256": SHA_A,
            "filename_has_signed": True,
            "bytes_changed_from_input": False,
        }
        reports["verify.json"]["input"] = {
            "relpath": HAP_RELPATH.replace("-unsigned.hap", "-signed.hap"),
            "size_bytes": 256,
            "sha256": SHA_B,
            "filename_has_signed": True,
            "filename_has_unsigned": False,
            "inside_repository": True,
            "filename_used_for_signedness": False,
        }
        result, code = run_agc_closure_manifest(
            inputs=_write_all(tmp_path, reports)
        )
        assert result["status"] == "blocked"
        assert code == EXIT_BLOCKED
        blocker = next(
            b for b in result["blockers"]
            if b["code"] == "evidence_hap_mismatch"
        )
        assert blocker["detail"]["link"] == "sign_artifact_vs_verify_input"

    def test_matching_chain_still_ok(self, tmp_path):
        reports = all_ok_reports()
        signed_relpath = HAP_RELPATH.replace("-unsigned.hap", "-signed.hap")
        reports["sign.json"]["input"] = {
            "relpath": HAP_RELPATH,
            "size_bytes": 128,
            "sha256": SHA_A,
            "filename_has_unsigned": True,
            "inside_repository": True,
        }
        reports["sign.json"]["artifact"] = {
            "relpath": signed_relpath,
            "size_bytes": 256,
            "sha256": SHA_B,
            "filename_has_signed": True,
            "bytes_changed_from_input": True,
        }
        reports["verify.json"]["input"] = {
            "relpath": signed_relpath,
            "size_bytes": 256,
            "sha256": SHA_B,
            "filename_has_signed": True,
            "filename_has_unsigned": False,
            "inside_repository": True,
            "filename_used_for_signedness": False,
        }
        result, code = run_agc_closure_manifest(
            inputs=_write_all(tmp_path, reports)
        )
        assert result["status"] == "ok"
        assert result["blockers"] == []
        assert code == EXIT_OK

    def test_missing_records_never_trigger_chain_blocker(self, tmp_path):
        # sign/verify reports without HAP records: chain check stays
        # silent; the missing-evidence handling keeps its old shape.
        reports = all_ok_reports()
        result, code = run_agc_closure_manifest(
            inputs=_write_all(tmp_path, reports)
        )
        assert result["status"] == "ok"
        assert code == EXIT_OK
        assert not any(
            b["code"] == "evidence_hap_mismatch"
            for b in result["blockers"]
        )


# ---------------------------------------------------------------------------
# Path safety: symlinks and non-regular files.
# ---------------------------------------------------------------------------

class TestPathRejection:

    def test_symlinked_input_file_rejected(self, tmp_path):
        real = tmp_path / "real.json"
        real.write_text(json.dumps(preflight_ok()), encoding="utf-8")
        link = tmp_path / "link.json"
        try:
            os.symlink(str(real), str(link))
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable on this host")
        result, code = run_agc_closure_manifest(inputs=[str(link)])
        assert code == EXIT_FAILURE
        assert result["failures"][0]["detail"]["error"] == (
            "symlink_or_reparse_point"
        )

    def test_symlinked_parent_directory_rejected(self, tmp_path):
        real_dir = tmp_path / "real_dir"
        real_dir.mkdir()
        real = real_dir / "report.json"
        real.write_text(json.dumps(preflight_ok()), encoding="utf-8")
        link_dir = tmp_path / "link_dir"
        try:
            os.symlink(str(real_dir), str(link_dir))
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable on this host")
        result, code = run_agc_closure_manifest(
            inputs=[str(link_dir / "report.json")]
        )
        assert code == EXIT_FAILURE
        assert result["failures"][0]["detail"]["error"] == "symlink_component"

    def test_directory_input_rejected(self, tmp_path):
        directory = tmp_path / "adir"
        directory.mkdir()
        result, code = run_agc_closure_manifest(inputs=[str(directory)])
        assert code == EXIT_FAILURE
        assert result["failures"][0]["detail"]["error"] == "not_a_regular_file"

    def test_fifo_input_rejected(self, tmp_path):
        if os.name == "nt":
            pytest.skip("mkfifo unavailable on Windows")
        fifo = tmp_path / "fifo.json"
        os.mkfifo(str(fifo))
        result, code = run_agc_closure_manifest(inputs=[str(fifo)])
        assert code == EXIT_FAILURE
        assert result["failures"][0]["detail"]["error"] == "not_a_regular_file"

    def test_symlinked_output_rejected_and_manifest_still_reported(
        self, tmp_path
    ):
        inputs = _write_all(tmp_path, all_ok_reports())
        real_out = tmp_path / "real_out.json"
        real_out.write_text("prior", encoding="utf-8")
        link_out = tmp_path / "link_out.json"
        try:
            os.symlink(str(real_out), str(link_out))
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable on this host")
        result, code = run_agc_closure_manifest(
            inputs=inputs, json_output=str(link_out)
        )
        assert code == EXIT_FAILURE
        assert result["failures"][0]["code"] == "output_rejected"
        # The symlink target was never touched.
        assert real_out.read_text(encoding="utf-8") == "prior"

    def test_output_overlapping_input_rejected(self, tmp_path):
        path = _write_report(tmp_path, "preflight.json", preflight_ok())
        result, code = run_agc_closure_manifest(
            inputs=[str(path)], json_output=str(path)
        )
        assert code == EXIT_FAILURE
        assert result["failures"][0]["code"] == "output_overlaps_input"
        # The input evidence file is preserved untouched.
        assert json.loads(path.read_text(encoding="utf-8")) == preflight_ok()

    def test_duplicate_output_paths_rejected(self, tmp_path):
        inputs = _write_all(tmp_path, all_ok_reports())
        out = str(tmp_path / "same.json")
        result, code = run_agc_closure_manifest(
            inputs=inputs, json_output=out, markdown_output=out
        )
        assert code == EXIT_FAILURE
        assert result["failures"][0]["code"] == "conflicting_output_paths"


# ---------------------------------------------------------------------------
# Atomic output writing.
# ---------------------------------------------------------------------------

class TestAtomicWrite:

    def test_outputs_written_and_prior_replaced_atomically(self, tmp_path):
        inputs = _write_all(tmp_path, all_ok_reports())
        json_out = tmp_path / "manifest.json"
        markdown_out = tmp_path / "manifest.md"
        json_out.write_text("old-json", encoding="utf-8")
        markdown_out.write_text("old-md", encoding="utf-8")
        _result, code = run_agc_closure_manifest(
            inputs=inputs,
            json_output=str(json_out),
            markdown_output=str(markdown_out),
        )
        assert code == EXIT_OK
        written = json.loads(json_out.read_text(encoding="utf-8"))
        assert written["tool"] == TOOL_NAME
        assert markdown_out.read_text(encoding="utf-8").startswith(
            "# AGC Closure Manifest"
        )
        # No temp files are left behind in the output directory.
        leftovers = [
            name
            for name in os.listdir(tmp_path)
            if name.endswith((".tmp", ".restore"))
        ]
        assert leftovers == []

    def test_failed_second_write_restores_first_output(self, tmp_path):
        inputs = _write_all(tmp_path, all_ok_reports())
        json_out = tmp_path / "manifest.json"
        markdown_out = tmp_path / "manifest.md"
        json_out.write_text("prior-json", encoding="utf-8")
        markdown_out.write_text("prior-md", encoding="utf-8")
        real_replace = os.replace
        calls = {"count": 0}

        def flaky_replace(src, dst):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("simulated replace failure")
            return real_replace(src, dst)

        saved = manifest_module._os_replace
        manifest_module._os_replace = flaky_replace
        try:
            result, code = run_agc_closure_manifest(
                inputs=inputs,
                json_output=str(json_out),
                markdown_output=str(markdown_out),
            )
        finally:
            manifest_module._os_replace = saved
        assert code == EXIT_FAILURE
        assert result["failures"][0]["code"] == "output_write_failed"
        # The first replaced output is restored byte-for-byte.
        assert json_out.read_text(encoding="utf-8") == "prior-json"
        assert markdown_out.read_text(encoding="utf-8") == "prior-md"
        leftovers = [
            name
            for name in os.listdir(tmp_path)
            if name.endswith((".tmp", ".restore"))
        ]
        assert leftovers == []

    def test_prior_absent_output_stays_absent_on_failure(self, tmp_path):
        inputs = _write_all(tmp_path, all_ok_reports())
        json_out = tmp_path / "fresh.json"
        real_replace = os.replace
        calls = {"count": 0}

        def flaky_replace(src, dst):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("simulated replace failure")
            return real_replace(src, dst)

        saved = manifest_module._os_replace
        manifest_module._os_replace = flaky_replace
        try:
            _result, code = run_agc_closure_manifest(
                inputs=inputs,
                json_output=str(json_out),
                markdown_output=str(tmp_path / "fresh.md"),
            )
        finally:
            manifest_module._os_replace = saved
        assert code == EXIT_FAILURE
        assert not json_out.exists()

    def test_no_output_arguments_writes_nothing(self, tmp_path):
        inputs = _write_all(tmp_path, all_ok_reports())
        before = sorted(os.listdir(tmp_path))
        _result, code = run_agc_closure_manifest(inputs=inputs)
        assert code == EXIT_OK
        assert sorted(os.listdir(tmp_path)) == before


# ---------------------------------------------------------------------------
# Determinism and fixed contract fields.
# ---------------------------------------------------------------------------

class TestDeterminism:

    def test_identical_inputs_produce_identical_json(self, tmp_path):
        one_dir = tmp_path / "one"
        two_dir = tmp_path / "two"
        one_dir.mkdir()
        two_dir.mkdir()
        one, code_one = run_agc_closure_manifest(
            inputs=_write_all(one_dir, all_ok_reports())
        )
        two, code_two = run_agc_closure_manifest(
            inputs=_write_all(two_dir, all_ok_reports())
        )
        assert code_one == code_two == EXIT_OK
        # Same content but different absolute input directories: the
        # basenames match, so the manifests must be byte-identical.
        assert render_json(one) == render_json(two)

    def test_manifest_schema_fields(self, tmp_path):
        inputs = _write_all(tmp_path, all_ok_reports())
        result, _ = run_agc_closure_manifest(inputs=inputs)
        assert result["schema_version"] == 1
        assert result["tool"] == TOOL_NAME
        assert result["production_ready"] is PRODUCTION_READY is False
        assert result["production_ready_reason"] == "fixed_false_in_this_slice"
        assert [g["name"] for g in result["gates"]] == list(REQUIRED_GATES)

    def test_json_is_sorted_and_ascii(self, tmp_path):
        inputs = _write_all(tmp_path, all_ok_reports())
        result, _ = run_agc_closure_manifest(inputs=inputs)
        text = render_json(result)
        assert text == json.dumps(
            result, indent=2, sort_keys=True, ensure_ascii=True
        )
        text.encode("ascii")

    def test_input_order_does_not_change_manifest(self, tmp_path):
        reports = all_ok_reports()
        reports["verify.json"] = {
            "tool": VERIFY_TOOL,
            "status": "unsigned",
            "failures": [],
            "signature": {"signed": False, "verified": False},
        }
        forward = _write_all(tmp_path, reports)
        reverse = list(reversed(forward))
        one, _ = run_agc_closure_manifest(inputs=forward)
        two, _ = run_agc_closure_manifest(inputs=reverse)
        assert render_json(one) == render_json(two)

    def test_markdown_renders_safe_fields_only(self, tmp_path):
        reports = all_ok_reports()
        reports["device_smoke.json"] = {
            "tool": DEVICE_SMOKE_TOOL,
            "status": "failure",
            "failures": [{"code": "install_failed", "detail": {"v": SECRET}}],
        }
        inputs = _write_all(tmp_path, reports)
        result, _ = run_agc_closure_manifest(inputs=inputs)
        markdown = render_markdown(result)
        assert SECRET not in markdown
        assert "production_ready: false" in markdown
        assert "| verify_signature |" in markdown
        assert "install_failed" not in markdown  # codes stay out of md
        assert "sign_the_unsigned_release_hap_then_verify" not in markdown


# ---------------------------------------------------------------------------
# CLI surface.
# ---------------------------------------------------------------------------

class TestCli:

    def test_repeated_input_flag(self, tmp_path, capsys):
        inputs = _write_all(tmp_path, all_ok_reports())
        argv = []
        for raw in inputs:
            argv += ["--input", raw]
        out = tmp_path / "manifest.json"
        argv += ["--json-output", str(out)]
        code = main(argv)
        assert code == EXIT_OK
        stdout = capsys.readouterr().out
        assert json.loads(stdout)["tool"] == TOOL_NAME
        assert json.loads(out.read_text(encoding="utf-8"))["status"] == "ok"

    def test_cli_blocked_exit_code(self, tmp_path, capsys):
        path = _write_report(tmp_path, "only.json", preflight_ok())
        code = main(["--input", str(path)])
        assert code == EXIT_BLOCKED
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "blocked"

    def test_cli_failure_exit_code(self, tmp_path, capsys):
        code = main(["--input", str(tmp_path / "absent.json")])
        assert code == EXIT_FAILURE
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "failure"
