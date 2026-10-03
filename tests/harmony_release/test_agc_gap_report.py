"""Focused offline tests for the read-only AGC gap report.

Every fixture is structural and local: a fake repo tree (build-profile +
app.json5 + declaration copy), placeholder bytes outside the repo, and
synthetic signed/unsigned HAP placeholder files. No real AGC credential,
material, tool, device, build, signing operation, subprocess, or network
access is used. No HAP bytes are ever read.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tools.harmony_release import agc_gap_report as gap
from tools.harmony_release.preflight import MATERIAL_ENV_VARS
from tools.harmony_release.sign_hap import CREDENTIAL_ENV_VARS

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_DECLARATION_PATH = (
    REPO_ROOT / "tools" / "harmony_release" / "agc_signing_inputs.json"
)

PLACEHOLDER_BYTES = b"placeholder-not-a-real-signing-material"


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch):
    """No material or credential environment variable may leak into tests."""
    for name in (*MATERIAL_ENV_VARS, *CREDENTIAL_ENV_VARS):
        monkeypatch.delenv(name, raising=False)
    yield
    # set_external_inputs() writes os.environ directly; scrub again so no
    # fixture value leaks into later tests or the operator's session.
    for name in (*MATERIAL_ENV_VARS, *CREDENTIAL_ENV_VARS):
        os.environ.pop(name, None)


def canonical_declaration() -> dict[str, Any]:
    return json.loads(CANONICAL_DECLARATION_PATH.read_text(encoding="utf-8"))


def write_repo(
    tmp_path: Path,
    profile: dict[str, Any] | None = None,
    signed_hap: bool = False,
    unsigned_hap: bool = False,
) -> Path:
    repo = tmp_path / "repo"
    harmony = repo / "apps" / "harmony"
    harmony.mkdir(parents=True)
    if profile is None:
        profile = {
            "app": {
                "signingConfigs": [{"name": "release", "type": "HarmonyOS"}],
                "products": [{"name": "default", "signingConfig": "release"}],
            }
        }
    (harmony / "build-profile.json5").write_text(
        json.dumps(profile, indent=2), encoding="utf-8"
    )
    app_scope = harmony / "AppScope"
    app_scope.mkdir()
    (app_scope / "app.json5").write_text(
        json.dumps({"app": {"bundleName": "com.ailearningos.app"}}, indent=2),
        encoding="utf-8",
    )
    output_dir = harmony / Path("entry/build/default/outputs/default")
    output_dir.mkdir(parents=True)
    if unsigned_hap:
        (output_dir / "entry-default-unsigned.hap").write_bytes(PLACEHOLDER_BYTES)
    if signed_hap:
        (output_dir / "entry-default-signed.hap").write_bytes(PLACEHOLDER_BYTES)
    return repo


def set_external_inputs(tmp_path: Path, materials: bool, credentials: bool) -> None:
    if materials:
        outside = tmp_path / "external-fixture"
        outside.mkdir(exist_ok=True)
        for env, suffix in sorted(MATERIAL_ENV_VARS.items()):
            path = outside / ("material" + suffix)
            path.write_bytes(PLACEHOLDER_BYTES)
            os.environ[env] = str(path)
    else:
        for env in MATERIAL_ENV_VARS:
            os.environ.pop(env, None)
    for env in CREDENTIAL_ENV_VARS:
        if credentials:
            os.environ[env] = "fake-credential-not-real"
        else:
            os.environ.pop(env, None)


def run_report(repo: Path) -> tuple[dict[str, Any], int]:
    declaration = repo.parent / "agc-signing-inputs.json"
    declaration.write_text(
        CANONICAL_DECLARATION_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return gap.run_gap_report(repo, declaration)


# ---------------------------------------------------------------- structure


def test_structure_ok_when_fake_repo_is_canonical(tmp_path):
    repo = write_repo(tmp_path)
    set_external_inputs(tmp_path, materials=True, credentials=True)
    result, _ = run_report(repo)
    structure = result["dimensions"]["signing_structure"]
    assert structure["status"] == "ok"
    assert structure["failure_codes"] == []
    assert structure["blockers"] == []


def test_structure_failure_current_checkout_shape(tmp_path):
    # signingConfigs: [] and no product binding, i.e. the real checkout shape.
    profile = {
        "app": {
            "signingConfigs": [],
            "products": [{"name": "default"}],
        }
    }
    repo = write_repo(tmp_path, profile=profile)
    set_external_inputs(tmp_path, materials=True, credentials=True)
    result, exit_code = run_report(repo)
    assert exit_code == 1
    assert result["status"] == "failure"
    structure = result["dimensions"]["signing_structure"]
    assert structure["status"] == "failure"
    codes = set(structure["failure_codes"])
    assert "signing_configs_empty" in codes
    assert "product_binding_missing" in codes
    blocker_codes = {b["code"] for b in structure["blockers"]}
    assert blocker_codes == {"signing_structure_failure"}
    for blocker in structure["blockers"]:
        assert blocker["next_action"] == "configure_release_signing_in_build_profile"


def test_structure_not_evaluated_when_declaration_missing(tmp_path):
    repo = write_repo(tmp_path)
    result, exit_code = gap.run_gap_report(repo, repo / "no-such-declaration.json")
    assert exit_code == 1
    structure = result["dimensions"]["signing_structure"]
    assert structure["status"] == "failure"
    assert "declaration_rejected" in structure["failure_codes"]
    blocker = structure["blockers"][0]
    assert blocker["next_action"] == "fix_signing_structure_failures"


# ------------------------------------------------------------ external inputs


def test_external_inputs_absent_reported_without_values(tmp_path):
    repo = write_repo(tmp_path)
    set_external_inputs(tmp_path, materials=False, credentials=True)
    result, exit_code = run_report(repo)
    assert exit_code == 2
    inputs = result["dimensions"]["external_inputs"]
    assert inputs["status"] == "blocked"
    material_names = set(inputs["material_variables"])
    assert material_names == set(MATERIAL_ENV_VARS)
    for entry in inputs["material_variables"].values():
        assert entry["present"] is False
    blocker_codes = {b["code"] for b in inputs["blockers"]}
    assert blocker_codes == {"external_material_absent"}
    for blocker in inputs["blockers"]:
        assert blocker["next_action"] == "provide_external_signing_materials"
    # No material or credential value may ever appear in the report.
    serialized = gap.render_json(result)
    assert "fake-credential-not-real" not in serialized
    assert "placeholder-not-a-real-signing-material" not in serialized


def test_external_credentials_absent_reported(tmp_path):
    repo = write_repo(tmp_path)
    set_external_inputs(tmp_path, materials=True, credentials=False)
    result, exit_code = run_report(repo)
    assert exit_code == 2
    inputs = result["dimensions"]["external_inputs"]
    assert inputs["status"] == "blocked"
    blocker_codes = {b["code"] for b in inputs["blockers"]}
    assert blocker_codes == {"external_credential_absent"}
    assert len(inputs["blockers"]) == len(CREDENTIAL_ENV_VARS)


def test_external_inputs_ok_when_all_present(tmp_path):
    repo = write_repo(tmp_path)
    set_external_inputs(tmp_path, materials=True, credentials=True)
    result, _ = run_report(repo)
    inputs = result["dimensions"]["external_inputs"]
    assert inputs["status"] == "ok"
    assert inputs["blockers"] == []


def test_external_inputs_not_evaluated_without_declaration(tmp_path):
    repo = write_repo(tmp_path)
    result, _ = gap.run_gap_report(repo, repo / "no-such-declaration.json")
    inputs = result["dimensions"]["external_inputs"]
    assert inputs["status"] == "not_evaluated"
    assert inputs["blockers"] == []


def test_present_but_invalid_material_is_blocked(tmp_path):
    repo = write_repo(tmp_path)
    set_external_inputs(tmp_path, materials=True, credentials=True)
    os.environ["AIOS_HARMONY_CERT_PATH"] = str(
        repo / "inside-repo-material.cer"
    )  # inside the repository -> invalid
    (repo / "inside-repo-material.cer").write_bytes(PLACEHOLDER_BYTES)
    result, exit_code = run_report(repo)
    assert exit_code == 2
    inputs = result["dimensions"]["external_inputs"]
    assert inputs["status"] == "blocked"
    codes = {b["code"] for b in inputs["blockers"]}
    assert codes == {"external_material_invalid"}


# -------------------------------------------------------- signature evidence


def test_signature_evidence_absent_when_no_signed_hap(tmp_path):
    repo = write_repo(tmp_path)
    set_external_inputs(tmp_path, materials=True, credentials=True)
    result, _ = run_report(repo)
    signature = result["dimensions"]["signature_evidence"]
    assert signature["status"] == "blocked"
    assert signature["signed_hap_present"] is False
    assert signature["verification_evidence_present"] is False
    codes = {b["code"] for b in signature["blockers"]}
    assert codes == {
        "signed_hap_absent",
        "signature_verification_evidence_absent",
    }


def test_signature_evidence_reports_signed_hap_presence_only(tmp_path):
    repo = write_repo(tmp_path, signed_hap=True, unsigned_hap=True)
    set_external_inputs(tmp_path, materials=True, credentials=True)
    result, _ = run_report(repo)
    signature = result["dimensions"]["signature_evidence"]
    # Even with a placeholder signed HAP present, verification evidence is
    # still absent: presence is not a signature verdict.
    assert signature["signed_hap_present"] is True
    assert signature["unsigned_hap_present"] is True
    codes = {b["code"] for b in signature["blockers"]}
    assert codes == {"signature_verification_evidence_absent"}


def test_signed_hap_symlink_is_not_presence(tmp_path):
    repo = write_repo(tmp_path, unsigned_hap=True)
    set_external_inputs(tmp_path, materials=True, credentials=True)
    output_dir = repo / "apps/harmony/entry/build/default/outputs/default"
    try:
        os.symlink(
            output_dir / "entry-default-unsigned.hap",
            output_dir / "entry-default-signed.hap",
        )
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    result, _ = run_report(repo)
    signature = result["dimensions"]["signature_evidence"]
    assert signature["signed_hap_present"] is False
    assert "signed_hap_absent" in {b["code"] for b in signature["blockers"]}


# ------------------------------------------------------------ AGC boundaries


def test_agc_distribution_fixed_boundary(tmp_path):
    repo = write_repo(tmp_path)
    result, _ = run_report(repo)
    distribution = result["dimensions"]["agc_distribution"]
    assert distribution["status"] == "blocked"
    assert distribution["basis"] == "fixed_boundary_no_agc_access"
    assert distribution["agc_access"] is False
    codes = sorted(b["code"] for b in distribution["blockers"])
    assert codes == [
        "agc_distribution_channel_not_exercised",
        "agc_release_manifest_not_downloaded",
        "agc_upload_not_attempted",
    ]
    for blocker in distribution["blockers"]:
        assert blocker["next_action"] in {
            "operator_run_agc_upload_with_real_credentials",
            "operator_download_agc_release_manifest",
            "exercise_agc_public_distribution_channel",
        }


def test_public_channel_fixed_boundary(tmp_path):
    repo = write_repo(tmp_path)
    result, _ = run_report(repo)
    public = result["dimensions"]["public_channel"]
    assert public["status"] == "blocked"
    assert public["basis"] == "fixed_boundary_requires_signed_hap"
    blocker = public["blockers"][0]
    assert blocker["code"] == "public_channel_requires_signed_release"
    assert blocker["next_action"] == "exercise_agc_public_distribution_channel"


# ------------------------------------------------------------- global shape


def test_production_ready_always_false_and_signed_flags(tmp_path):
    repo = write_repo(tmp_path, signed_hap=True)
    set_external_inputs(tmp_path, materials=True, credentials=True)
    result, _ = run_report(repo)
    assert result["production_ready"] is False
    assert result["production_ready_reason"] == (
        "gap_report_is_not_release_readiness"
    )
    assert result["signing_performed"] is False
    assert result["signed_hap_generated"] is False
    assert result["agc_access"] is False


def test_dimensions_closed_vocabulary_and_order(tmp_path):
    repo = write_repo(tmp_path)
    result, _ = run_report(repo)
    assert set(result["dimensions"]) == set(gap.DIMENSIONS)
    for name, record in result["dimensions"].items():
        assert record["status"] in gap.DIMENSION_STATUS_VALUES
        for blocker in record["blockers"]:
            assert blocker["dimension"] == name
            assert blocker["code"] in gap.BLOCKER_CODES_SET
            assert blocker["next_action"] in gap.NEXT_ACTIONS


def test_blockers_sorted_and_next_actions_deduplicated(tmp_path):
    repo = write_repo(tmp_path)  # no external inputs at all
    result, _ = run_report(repo)
    keys = [json.dumps(b, sort_keys=True) for b in result["blockers"]]
    assert keys == sorted(keys)
    actions = result["next_actions"]
    assert actions == list(dict.fromkeys(actions))
    assert set(actions) <= set(gap.NEXT_ACTIONS)


def test_status_blocked_when_only_gaps(tmp_path):
    repo = write_repo(tmp_path)
    set_external_inputs(tmp_path, materials=True, credentials=True)
    result, exit_code = run_report(repo)
    # Structure ok + inputs present, but signature/AGC/public gaps remain.
    assert result["status"] == "blocked"
    assert exit_code == 2
    assert result["blockers"]


def test_result_is_deterministic(tmp_path):
    repo = write_repo(tmp_path)
    first, first_exit = run_report(repo)
    second, second_exit = run_report(repo)
    assert first_exit == second_exit
    assert gap.render_json(first) == gap.render_json(second)


def test_render_json_is_ascii_and_stable(tmp_path):
    repo = write_repo(tmp_path)
    result, _ = run_report(repo)
    rendered = gap.render_json(result)
    rendered.encode("ascii")
    assert json.loads(rendered) == result


def test_no_local_paths_in_output(tmp_path):
    repo = write_repo(tmp_path)
    result, _ = run_report(repo)
    rendered = gap.render_json(result)
    assert str(tmp_path) not in rendered
    assert "D:" not in rendered
    assert "\\\\\\" not in rendered


# --------------------------------------------------------------- constants


def test_closed_vocabularies_are_closed():
    assert set(gap.STATUS_VALUES) == {"ok", "failure", "blocked"}
    assert set(gap.DIMENSION_STATUS_VALUES) == {
        "ok",
        "failure",
        "blocked",
        "not_evaluated",
    }
    assert len(gap.BLOCKER_CODES) == len(set(gap.BLOCKER_CODES))
    assert len(gap.NEXT_ACTIONS) == len(set(gap.NEXT_ACTIONS))


def test_signed_hap_name_derived_from_unsigned_canonical():
    assert gap.SIGNED_HAP_NAME == "entry-default-signed.hap"
    assert gap.EXPECTED_ARTIFACT_NAME == "entry-default-unsigned.hap"


# ------------------------------------------------------------- current repo


def test_current_checkout_reports_gaps_not_failure_pretense():
    # Real checkout: signingConfigs is empty and no materials exist. The
    # report must be blocked (exit 2), not a structural failure pretense,
    # and must never claim a signed HAP exists.
    result, exit_code = gap.run_gap_report(REPO_ROOT)
    assert exit_code in (1, 2)
    assert result["production_ready"] is False
    signature = result["dimensions"]["signature_evidence"]
    assert signature["signed_hap_present"] is False
    assert result["signed_hap_generated"] is False


def test_current_checkout_never_claims_agc_access():
    result, _ = gap.run_gap_report(REPO_ROOT)
    assert result["agc_access"] is False
    assert result["dimensions"]["agc_distribution"]["agc_access"] is False


# --------------------------------------------------------------------- CLI


def test_cli_stdout_json_and_exit_code(tmp_path, capsys, monkeypatch):
    repo = write_repo(tmp_path)
    declaration = tmp_path / "agc-signing-inputs.json"
    declaration.write_text(
        CANONICAL_DECLARATION_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    code = gap.main(
        [
            "--repo-root",
            str(repo),
            "--declaration",
            str(declaration),
        ]
    )
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "harmony_agc_gap_report"
    assert payload["status"] == "blocked"
