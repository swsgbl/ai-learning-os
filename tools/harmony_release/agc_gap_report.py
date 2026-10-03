"""Read-only, machine-readable Harmony AGC gap report.

This tool aggregates the known distance between the current checkout and an
AGC-backed public release without pretending any material exists. It never
spawns a subprocess, never reads signing-material or HAP bytes, never touches
a device, and never contacts AGC or any network endpoint.

Evidence sources (all local and read-only):

* ``agc_signing_preflight`` (imported, not spawned) supplies the signing
  structure dimension (declaration + build-profile product binding + app
  manifest) and the external material/credential absence dimension.
* A filesystem probe (``lstat`` only) reports whether a signed HAP artifact
  from the canonical release output layout exists. No HAP bytes are read and
  no signing is performed.
* The AGC upload / release-manifest / distribution and public-channel
  dimensions are fixed boundaries: this tool has no AGC access, so those
  gaps are reported as constants from a closed vocabulary.

All status, blocker-code, and next-action values come from closed
vocabularies. ``production_ready`` is always ``false``: a gap report is not a
release-readiness claim, and even a fully-green report would only describe a
checkout state, not an executed release.

Exit codes:
    0 = no gaps reported (structurally unreachable in this slice because the
        AGC distribution dimension is a fixed boundary)
    1 = signing-structure failure (malformed or drifted inputs)
    2 = valid structure, but one or more gaps are reported
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

try:  # package import (pytest and python -m)
    from tools.harmony_release import agc_signing_preflight
    from tools.harmony_release.release_build import (
        EXPECTED_ARTIFACT_NAME,
        HARMONY_SUBDIR,
        OUTPUT_RELPATH,
    )
except ImportError:  # direct script execution
    import agc_signing_preflight  # type: ignore[no-redef]
    from release_build import (  # type: ignore[no-redef]
        EXPECTED_ARTIFACT_NAME,
        HARMONY_SUBDIR,
        OUTPUT_RELPATH,
    )


SCHEMA_VERSION = 1
TOOL_NAME = "harmony_agc_gap_report"
MODE = "read_only_report"
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]

STATUS_OK = "ok"
STATUS_FAILURE = "failure"
STATUS_BLOCKED = "blocked"

DIMENSION_OK = "ok"
DIMENSION_FAILURE = "failure"
DIMENSION_BLOCKED = "blocked"
DIMENSION_NOT_EVALUATED = "not_evaluated"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_BLOCKED = 2

PRODUCTION_READY = False
PRODUCTION_READY_REASON = "gap_report_is_not_release_readiness"

UNSIGNED_MARKER = "unsigned"
SIGNED_MARKER = "signed"
SIGNED_HAP_NAME = EXPECTED_ARTIFACT_NAME.replace(
    UNSIGNED_MARKER, SIGNED_MARKER, 1
)

DIMENSION_SIGNING_STRUCTURE = "signing_structure"
DIMENSION_EXTERNAL_INPUTS = "external_inputs"
DIMENSION_SIGNATURE_EVIDENCE = "signature_evidence"
DIMENSION_AGC_DISTRIBUTION = "agc_distribution"
DIMENSION_PUBLIC_CHANNEL = "public_channel"
DIMENSIONS = (
    DIMENSION_SIGNING_STRUCTURE,
    DIMENSION_EXTERNAL_INPUTS,
    DIMENSION_SIGNATURE_EVIDENCE,
    DIMENSION_AGC_DISTRIBUTION,
    DIMENSION_PUBLIC_CHANNEL,
)

STATUS_VALUES = (STATUS_OK, STATUS_FAILURE, STATUS_BLOCKED)
DIMENSION_STATUS_VALUES = (
    DIMENSION_OK,
    DIMENSION_FAILURE,
    DIMENSION_BLOCKED,
    DIMENSION_NOT_EVALUATED,
)

# Failure codes from agc_signing_preflight that belong to the signing
# structure dimension (declaration, build profile, app manifest).
STRUCTURE_FAILURE_CODES = frozenset(
    {
        "declaration_rejected",
        "declaration_contract_drift",
        "build_profile_missing",
        "build_profile_rejected",
        "build_profile_unparseable",
        "build_profile_app_invalid",
        "signing_configs_invalid",
        "signing_configs_empty",
        "signing_config_ambiguous",
        "products_invalid",
        "product_ambiguous",
        "product_binding_missing",
        "product_binding_mismatch",
        "app_manifest_missing",
        "app_manifest_rejected",
        "app_manifest_unparseable",
        "app_manifest_app_invalid",
        "bundle_name_missing",
        "bundle_name_mismatch",
    }
)

NEXT_FIX_STRUCTURE = "fix_signing_structure_failures"
NEXT_CONFIGURE_SIGNING = "configure_release_signing_in_build_profile"
NEXT_PROVIDE_MATERIALS = "provide_external_signing_materials"
NEXT_PROVIDE_CREDENTIALS = "provide_signing_credentials"
NEXT_RUN_SIGN_HAP = "run_sign_hap_with_external_materials"
NEXT_RUN_VERIFY = "run_signature_verification"
NEXT_OPERATOR_UPLOAD = "operator_run_agc_upload_with_real_credentials"
NEXT_OPERATOR_MANIFEST = "operator_download_agc_release_manifest"
NEXT_EXERCISE_CHANNEL = "exercise_agc_public_distribution_channel"
NEXT_ACTIONS = (
    NEXT_FIX_STRUCTURE,
    NEXT_CONFIGURE_SIGNING,
    NEXT_PROVIDE_MATERIALS,
    NEXT_PROVIDE_CREDENTIALS,
    NEXT_RUN_SIGN_HAP,
    NEXT_RUN_VERIFY,
    NEXT_OPERATOR_UPLOAD,
    NEXT_OPERATOR_MANIFEST,
    NEXT_EXERCISE_CHANNEL,
)

BLOCKER_CODES = (
    "signing_structure_failure",
    "external_material_absent",
    "external_material_invalid",
    "external_credential_absent",
    "signed_hap_absent",
    "signature_verification_evidence_absent",
    "agc_upload_not_attempted",
    "agc_release_manifest_not_downloaded",
    "agc_distribution_channel_not_exercised",
    "public_channel_requires_signed_release",
)
BLOCKER_CODES_SET = frozenset(BLOCKER_CODES)

FIX_UPSTREAM_CODES = frozenset({"declaration_rejected", "declaration_contract_drift"})


def _regular_file_exists(path: Path) -> bool:
    """True only when path is an existing regular file (lstat, no reads)."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_point", False):
        return False
    return stat.S_ISREG(info.st_mode)


def _blocker(
    dimension: str, code: str, next_action: str, detail: dict[str, Any] | None = None
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "dimension": dimension,
        "code": code,
        "next_action": next_action,
    }
    if detail:
        record["detail"] = detail
    return record


def _signing_structure_dimension(
    preflight_result: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Project the structure failures of the preflight result."""
    structure_codes = [
        failure["code"]
        for failure in preflight_result["failures"]
        if failure["code"] in STRUCTURE_FAILURE_CODES
    ]
    blockers = [
        _blocker(
            DIMENSION_SIGNING_STRUCTURE,
            "signing_structure_failure",
            NEXT_CONFIGURE_SIGNING
            if code not in FIX_UPSTREAM_CODES
            else NEXT_FIX_STRUCTURE,
            detail={"failure_code": code},
        )
        for code in structure_codes
    ]
    if structure_codes:
        status = DIMENSION_FAILURE
    elif preflight_result["declaration"]["checked"]:
        status = DIMENSION_OK
    else:
        status = DIMENSION_NOT_EVALUATED
    record = {
        "basis": "agc_signing_preflight_report",
        "status": status,
        "failure_codes": structure_codes,
        "blockers": blockers,
    }
    return record, blockers


def _external_inputs_dimension(
    preflight_result: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Report material/credential absence without ever reading values."""
    inputs = preflight_result["external_inputs"]
    blockers: list[dict[str, Any]] = []
    material_view: dict[str, Any] = {}
    credential_view: dict[str, Any] = {}
    if not inputs["checked"]:
        record = {
            "basis": "agc_signing_preflight_report",
            "status": DIMENSION_NOT_EVALUATED,
            "material_variables": material_view,
            "credential_variables": credential_view,
            "blockers": blockers,
        }
        return record, blockers
    for name in sorted(inputs["materials"]["variables"]):
        entry = inputs["materials"]["variables"][name]
        material_view[name] = {
            "present": entry["present"],
            "valid": entry["valid"],
        }
        if not entry["present"]:
            blockers.append(
                _blocker(
                    DIMENSION_EXTERNAL_INPUTS,
                    "external_material_absent",
                    NEXT_PROVIDE_MATERIALS,
                    detail={"variable": name},
                )
            )
        elif entry["valid"] is not True:
            blockers.append(
                _blocker(
                    DIMENSION_EXTERNAL_INPUTS,
                    "external_material_invalid",
                    NEXT_PROVIDE_MATERIALS,
                    detail={"variable": name},
                )
            )
    for name in sorted(inputs["credentials"]["variables"]):
        entry = inputs["credentials"]["variables"][name]
        credential_view[name] = {"present": entry["present"]}
        if not entry["present"]:
            blockers.append(
                _blocker(
                    DIMENSION_EXTERNAL_INPUTS,
                    "external_credential_absent",
                    NEXT_PROVIDE_CREDENTIALS,
                    detail={"variable": name},
                )
            )
    status = DIMENSION_OK if not blockers else DIMENSION_BLOCKED
    record = {
        "basis": "agc_signing_preflight_report",
        "status": status,
        "material_variables": material_view,
        "credential_variables": credential_view,
        "blockers": blockers,
    }
    return record, blockers


def _signature_evidence_dimension(
    repo_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Probe the canonical signed-HAP location by lstat only."""
    output_dir = repo_root / HARMONY_SUBDIR / OUTPUT_RELPATH
    signed_present = _regular_file_exists(output_dir / SIGNED_HAP_NAME)
    unsigned_present = _regular_file_exists(output_dir / EXPECTED_ARTIFACT_NAME)
    blockers: list[dict[str, Any]] = []
    if not signed_present:
        blockers.append(
            _blocker(
                DIMENSION_SIGNATURE_EVIDENCE,
                "signed_hap_absent",
                NEXT_RUN_SIGN_HAP,
                detail={"artifact_name": SIGNED_HAP_NAME},
            )
        )
    blockers.append(
        _blocker(
            DIMENSION_SIGNATURE_EVIDENCE,
            "signature_verification_evidence_absent",
            NEXT_RUN_VERIFY,
        )
    )
    record = {
        "basis": "filesystem_probe_no_hap_bytes_read",
        "status": DIMENSION_BLOCKED,
        "signed_hap_name": SIGNED_HAP_NAME,
        "signed_hap_present": signed_present,
        "unsigned_hap_present": unsigned_present,
        "verification_evidence_present": False,
        "blockers": blockers,
    }
    return record, blockers


def _agc_distribution_dimension() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fixed boundary: this tool never contacts AGC, so the gaps stand."""
    blockers = [
        _blocker(
            DIMENSION_AGC_DISTRIBUTION,
            "agc_upload_not_attempted",
            NEXT_OPERATOR_UPLOAD,
        ),
        _blocker(
            DIMENSION_AGC_DISTRIBUTION,
            "agc_release_manifest_not_downloaded",
            NEXT_OPERATOR_MANIFEST,
        ),
        _blocker(
            DIMENSION_AGC_DISTRIBUTION,
            "agc_distribution_channel_not_exercised",
            NEXT_EXERCISE_CHANNEL,
        ),
    ]
    record = {
        "basis": "fixed_boundary_no_agc_access",
        "status": DIMENSION_BLOCKED,
        "agc_access": False,
        "blockers": blockers,
    }
    return record, blockers


def _public_channel_dimension() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fixed boundary: public readiness needs a signed release first."""
    blockers = [
        _blocker(
            DIMENSION_PUBLIC_CHANNEL,
            "public_channel_requires_signed_release",
            NEXT_EXERCISE_CHANNEL,
        )
    ]
    record = {
        "basis": "fixed_boundary_requires_signed_hap",
        "status": DIMENSION_BLOCKED,
        "blockers": blockers,
    }
    return record, blockers


def run_gap_report(
    repo_root: Path, declaration_path: Path | None = None
) -> tuple[dict[str, Any], int]:
    """Aggregate all dimensions and return deterministic JSON plus exit code."""
    repo_root = Path(repo_root).resolve()
    preflight_result, _preflight_exit = agc_signing_preflight.run_preflight(
        repo_root, declaration_path
    )

    structure_record, structure_blockers = _signing_structure_dimension(
        preflight_result
    )
    inputs_record, inputs_blockers = _external_inputs_dimension(preflight_result)
    signature_record, signature_blockers = _signature_evidence_dimension(repo_root)
    distribution_record, distribution_blockers = _agc_distribution_dimension()
    public_record, public_blockers = _public_channel_dimension()

    dimensions = {
        DIMENSION_SIGNING_STRUCTURE: structure_record,
        DIMENSION_EXTERNAL_INPUTS: inputs_record,
        DIMENSION_SIGNATURE_EVIDENCE: signature_record,
        DIMENSION_AGC_DISTRIBUTION: distribution_record,
        DIMENSION_PUBLIC_CHANNEL: public_record,
    }
    blockers = sorted(
        structure_blockers
        + inputs_blockers
        + signature_blockers
        + distribution_blockers
        + public_blockers,
        key=lambda item: json.dumps(item, sort_keys=True),
    )
    next_actions: list[str] = []
    for blocker in blockers:
        if blocker["next_action"] not in next_actions:
            next_actions.append(blocker["next_action"])

    if structure_record["status"] == DIMENSION_FAILURE:
        status = STATUS_FAILURE
        exit_code = EXIT_FAILURE
    elif blockers:
        status = STATUS_BLOCKED
        exit_code = EXIT_BLOCKED
    else:
        status = STATUS_OK
        exit_code = EXIT_OK

    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "mode": MODE,
        "status": status,
        "exit_code": exit_code,
        "dimensions": dimensions,
        "blockers": blockers,
        "next_actions": next_actions,
        "agc_access": False,
        "signing_performed": False,
        "signed_hap_generated": False,
        "production_ready": PRODUCTION_READY,
        "production_ready_reason": PRODUCTION_READY_REASON,
    }
    return result, exit_code


def render_json(result: dict[str, Any]) -> str:
    """Deterministic, ASCII-safe machine output."""
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agc_gap_report",
        description=(
            "Aggregate the read-only AGC signing/distribution gap report "
            "without AGC access, signing, or HAP reads."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help="Repository root (default: this checkout).",
    )
    parser.add_argument(
        "--declaration",
        type=Path,
        default=None,
        help=(
            "Value-free declaration JSON "
            "(default: tools/harmony_release/agc_signing_inputs.json)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    declaration = (
        args.repo_root.resolve() / agc_signing_preflight.DEFAULT_DECLARATION_RELPATH
        if args.declaration is None
        else args.declaration
    )
    result, exit_code = run_gap_report(
        repo_root=args.repo_root,
        declaration_path=declaration,
    )
    sys.stdout.write(render_json(result) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
