"""Read-only Harmony AGC signing-input preflight.

This tool validates the declared structure of the AGC signing contract. It
does not discover or contact AGC, invoke java or hap-sign-tool, build a HAP,
sign a HAP, read a device, or parse signing-material bytes.

The explicit declaration is value-free: it names the release bundle/product/
signing-config and the public environment-variable names. Material paths are
validated with the same classifier as the existing release gate (regular file,
documented suffix, outside the repository). Credential values are only checked
for presence; their values are never read into the result.

Exit codes:
    0 = all declared structures and inputs pass validation
    1 = malformed, ambiguous, drifted, or present-but-invalid input
    2 = valid local structure, but one or more external inputs are absent

An ok result is never a production-readiness claim: production_ready is always
false and no signedness conclusion is produced.
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
    from tools.harmony_release.json5lite import load_json5_relaxed
    from tools.harmony_release.preflight import _classify_material_path
except ImportError:  # direct script execution
    from json5lite import load_json5_relaxed  # type: ignore[no-redef]
    from preflight import _classify_material_path  # type: ignore[no-redef]


SCHEMA_VERSION = 1
TOOL_NAME = "harmony_agc_signing_preflight"
MODE = "validate_only"
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DECLARATION_RELPATH = Path("tools/harmony_release/agc_signing_inputs.json")
BUILD_PROFILE_RELPATH = Path("apps/harmony/build-profile.json5")
APP_MANIFEST_RELPATH = Path("apps/harmony/AppScope/app.json5")
MAX_DECLARATION_BYTES = 1 << 20

STATUS_OK = "ok"
STATUS_FAILURE = "failure"
STATUS_BLOCKED = "blocked_by_external_inputs"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_BLOCKED = 2

PRODUCTION_READY = False
PRODUCTION_READY_REASON = "signing_input_structure_is_not_release_readiness"

CANONICAL_DECLARATION: dict[str, Any] = {
    "schema_version": 1,
    "bundle_name": "com.ailearningos.app",
    "product": "default",
    "signing_config": "release",
    "material_inputs": [
        {
            "name": "release_certificate",
            "env": "AIOS_HARMONY_CERT_PATH",
            "expected_suffix": ".cer",
        },
        {
            "name": "release_profile",
            "env": "AIOS_HARMONY_PROFILE_PATH",
            "expected_suffix": ".p7b",
        },
        {
            "name": "release_keystore",
            "env": "AIOS_HARMONY_KEYSTORE_PATH",
            "expected_suffix": ".p12",
        },
    ],
    "credential_inputs": [
        {"name": "key_alias", "env": "AIOS_HARMONY_KEY_ALIAS"},
        {"name": "keystore_password", "env": "AIOS_HARMONY_KEYSTORE_PASSWORD"},
        {"name": "key_password", "env": "AIOS_HARMONY_KEY_PASSWORD"},
    ],
}


class DuplicateJsonKey(ValueError):
    """Raised by strict JSON parsing when an object repeats a key."""


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKey(key)
        result[key] = value
    return result


def _regular_file_error(path: Path) -> str | None:
    """Classify an input path without serializing the path."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "path_not_found"
    except OSError:
        return "path_inaccessible"
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_point", False):
        return "symlink_or_reparse_point"
    if not stat.S_ISREG(info.st_mode):
        return "not_a_regular_file"
    return None


def load_declaration(path: Path) -> tuple[dict[str, Any] | None, list[dict]]:
    """Strictly load and validate the value-free declaration contract."""
    path = Path(path)
    error = _regular_file_error(path)
    if error is not None:
        return None, [{"code": "declaration_rejected", "detail": {"error": error}}]

    try:
        raw = path.read_bytes()
    except OSError:
        return None, [
            {"code": "declaration_rejected", "detail": {"error": "unreadable"}}
        ]
    if len(raw) > MAX_DECLARATION_BYTES:
        return None, [
            {"code": "declaration_rejected", "detail": {"error": "too_large"}}
        ]
    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError:
        return None, [
            {"code": "declaration_rejected", "detail": {"error": "not_utf8"}}
        ]
    except DuplicateJsonKey:
        return None, [
            {
                "code": "declaration_rejected",
                "detail": {"error": "duplicate_json_key"},
            }
        ]
    except ValueError:
        return None, [
            {"code": "declaration_rejected", "detail": {"error": "invalid_json"}}
        ]
    if not isinstance(parsed, dict):
        return None, [
            {"code": "declaration_rejected", "detail": {"error": "not_an_object"}}
        ]

    failures = _validate_declaration_shape(parsed)
    if failures:
        return None, failures
    return parsed, []


def _validate_declaration_shape(declaration: dict[str, Any]) -> list[dict]:
    """Reject unknown shape and any drift from the fixed release contract."""
    if declaration != CANONICAL_DECLARATION:
        return [{"code": "declaration_contract_drift"}]
    return []


def _declaration_record(
    declaration: dict[str, Any] | None, failures: list[dict]
) -> dict[str, Any]:
    error: str | None = None
    if declaration is None:
        for failure in failures:
            if failure["code"] in {"declaration_rejected", "invalid_declaration"}:
                detail = failure.get("detail", {})
                if isinstance(detail, dict):
                    error = detail.get("error")
                break
        if error is None:
            error = "contract_drift"
    return {
        "provided": True,
        "checked": True,
        "valid": declaration is not None,
        "canonical": declaration == CANONICAL_DECLARATION,
        "error": error,
        "bundle_name": (
            declaration["bundle_name"] if declaration is not None else None
        ),
        "product": declaration["product"] if declaration is not None else None,
        "signing_config": (
            declaration["signing_config"] if declaration is not None else None
        ),
        "material_variables": (
            [item["env"] for item in declaration["material_inputs"]]
            if declaration is not None
            else []
        ),
        "credential_variables": (
            [item["env"] for item in declaration["credential_inputs"]]
            if declaration is not None
            else []
        ),
    }


def _check_build_profile(
    repo_root: Path, declaration: dict[str, Any]
) -> tuple[dict[str, Any], list[dict]]:
    """Require one unambiguous config and one matching product binding."""
    path = repo_root / BUILD_PROFILE_RELPATH
    path_error = _regular_file_error(path)
    record: dict[str, Any] = {
        "path": BUILD_PROFILE_RELPATH.as_posix(),
        "exists": path_error not in {"path_not_found", "path_inaccessible"},
        "parseable": False,
        "signing_configs_count": None,
        "matching_product_count": None,
        "signing_config_found": False,
        "product_binding_matches": None,
        "structure_valid": False,
    }
    failures: list[dict] = []
    if path_error == "path_not_found":
        failures.append({"code": "build_profile_missing"})
        return record, failures
    if path_error is not None:
        failures.append(
            {
                "code": "build_profile_rejected",
                "detail": {"error": path_error},
            }
        )
        return record, failures
    try:
        parsed = load_json5_relaxed(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        failures.append({"code": "build_profile_unparseable"})
        return record, failures
    if not isinstance(parsed, dict):
        failures.append({"code": "build_profile_unparseable"})
        return record, failures
    record["parseable"] = True

    app = parsed.get("app")
    if not isinstance(app, dict):
        failures.append({"code": "build_profile_app_invalid"})
        return record, failures
    configs = app.get("signingConfigs")
    if not isinstance(configs, list):
        failures.append({"code": "signing_configs_invalid"})
        return record, failures
    record["signing_configs_count"] = len(configs)
    matching_configs = [
        item
        for item in configs
        if isinstance(item, dict)
        and item.get("name") == declaration["signing_config"]
    ]
    if not configs:
        failures.append({"code": "signing_configs_empty"})
    elif len(configs) != 1 or len(matching_configs) != 1:
        failures.append({"code": "signing_config_ambiguous"})
    else:
        record["signing_config_found"] = True

    products = app.get("products")
    if not isinstance(products, list):
        failures.append({"code": "products_invalid"})
        return record, failures
    matching_products = [
        item
        for item in products
        if isinstance(item, dict) and item.get("name") == declaration["product"]
    ]
    record["matching_product_count"] = len(matching_products)
    if len(matching_products) != 1:
        failures.append({"code": "product_ambiguous"})
        return record, failures
    product = matching_products[0]
    binding = product.get("signingConfig")
    if not isinstance(binding, str):
        failures.append({"code": "product_binding_missing"})
    else:
        record["product_binding_matches"] = binding == declaration["signing_config"]
        if not record["product_binding_matches"]:
            failures.append({"code": "product_binding_mismatch"})

    record["structure_valid"] = not failures
    return record, failures


def _check_app_manifest(
    repo_root: Path, declaration: dict[str, Any]
) -> tuple[dict[str, Any], list[dict]]:
    """Pin the declared bundle name to the authoritative app manifest."""
    path = repo_root / APP_MANIFEST_RELPATH
    path_error = _regular_file_error(path)
    record: dict[str, Any] = {
        "path": APP_MANIFEST_RELPATH.as_posix(),
        "exists": path_error not in {"path_not_found", "path_inaccessible"},
        "parseable": False,
        "bundle_name_present": None,
        "bundle_name_matches": None,
    }
    failures: list[dict] = []
    if path_error == "path_not_found":
        failures.append({"code": "app_manifest_missing"})
        return record, failures
    if path_error is not None:
        failures.append(
            {
                "code": "app_manifest_rejected",
                "detail": {"error": path_error},
            }
        )
        return record, failures
    try:
        parsed = load_json5_relaxed(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        failures.append({"code": "app_manifest_unparseable"})
        return record, failures
    if not isinstance(parsed, dict):
        failures.append({"code": "app_manifest_unparseable"})
        return record, failures
    record["parseable"] = True
    app = parsed.get("app")
    if not isinstance(app, dict):
        failures.append({"code": "app_manifest_app_invalid"})
        return record, failures
    bundle_name = app.get("bundleName")
    record["bundle_name_present"] = isinstance(bundle_name, str) and bool(bundle_name)
    if not record["bundle_name_present"]:
        failures.append({"code": "bundle_name_missing"})
        return record, failures
    record["bundle_name_matches"] = bundle_name == declaration["bundle_name"]
    if not record["bundle_name_matches"]:
        failures.append({"code": "bundle_name_mismatch"})
    return record, failures


def _not_run_inputs(declaration: dict[str, Any] | None) -> dict[str, Any]:
    material_variables = (
        [item["env"] for item in declaration["material_inputs"]]
        if declaration is not None
        else []
    )
    credential_variables = (
        [item["env"] for item in declaration["credential_inputs"]]
        if declaration is not None
        else []
    )
    return {
        "checked": False,
        "materials": {
            "variables": {name: None for name in material_variables},
            "all_present": None,
            "all_valid": None,
        },
        "credentials": {
            "variables": {name: None for name in credential_variables},
            "all_present": None,
        },
    }


def _check_material_inputs(
    repo_root: Path, declaration: dict[str, Any]
) -> tuple[dict[str, Any], list[dict]]:
    variables: dict[str, Any] = {}
    failures: list[dict] = []
    resolved_root = repo_root.resolve()
    for item in sorted(declaration["material_inputs"], key=lambda value: value["env"]):
        name = item["env"]
        expected_suffix = item["expected_suffix"]
        raw = os.environ.get(name)
        entry: dict[str, Any] = {
            "expected_suffix": expected_suffix,
            "present": False,
            "valid": None,
            "error": "not_set",
        }
        if raw == "":
            entry["error"] = "empty_value"
        elif raw is not None:
            entry["present"] = True
            error = _classify_material_path(raw, expected_suffix, resolved_root)
            entry["error"] = error
            entry["valid"] = error is None
            if error is not None:
                failures.append(
                    {
                        "code": "external_material_invalid",
                        "detail": {"variable": name, "error": error},
                    }
                )
        variables[name] = entry
    all_present = bool(variables) and all(
        entry["present"] for entry in variables.values()
    )
    all_valid = (
        all(entry["valid"] for entry in variables.values())
        if all_present
        else None
    )
    return {
        "variables": variables,
        "all_present": all_present,
        "all_valid": all_valid,
    }, failures


def _check_credential_inputs(
    declaration: dict[str, Any],
) -> tuple[dict[str, Any], list[dict]]:
    variables: dict[str, Any] = {}
    for item in sorted(declaration["credential_inputs"], key=lambda value: value["env"]):
        name = item["env"]
        raw = os.environ.get(name)
        entry = {"present": False, "error": "not_set"}
        if raw == "":
            entry["error"] = "empty_value"
        elif raw is not None:
            entry["present"] = True
            entry["error"] = None
        variables[name] = entry
    all_present = bool(variables) and all(
        entry["present"] for entry in variables.values()
    )
    return {"variables": variables, "all_present": all_present}, []


def run_preflight(
    repo_root: Path, declaration_path: Path | None = None
) -> tuple[dict[str, Any], int]:
    """Run all read-only checks and return deterministic JSON plus exit code."""
    repo_root = Path(repo_root).resolve()
    declaration_path = (
        Path(declaration_path)
        if declaration_path is not None
        else repo_root / DEFAULT_DECLARATION_RELPATH
    )
    declaration, declaration_failures = load_declaration(declaration_path)
    failures = list(declaration_failures)
    declaration_record = _declaration_record(declaration, declaration_failures)

    build_record: dict[str, Any] = {
        "checked": False,
        "exists": None,
        "parseable": None,
        "signing_configs_count": None,
        "matching_product_count": None,
        "signing_config_found": None,
        "product_binding_matches": None,
        "structure_valid": None,
    }
    app_manifest_record: dict[str, Any] = {
        "checked": False,
        "exists": None,
        "parseable": None,
        "bundle_name_present": None,
        "bundle_name_matches": None,
    }
    external_inputs = _not_run_inputs(declaration)

    if declaration is not None:
        build_record, build_failures = _check_build_profile(repo_root, declaration)
        failures += build_failures
        app_manifest_record, manifest_failures = _check_app_manifest(
            repo_root, declaration
        )
        failures += manifest_failures
        materials, material_failures = _check_material_inputs(repo_root, declaration)
        credentials, credential_failures = _check_credential_inputs(declaration)
        failures += material_failures + credential_failures
        external_inputs = {
            "checked": True,
            "materials": materials,
            "credentials": credentials,
        }

    failures.sort(key=lambda item: json.dumps(item, sort_keys=True))
    if failures:
        status = STATUS_FAILURE
        exit_code = EXIT_FAILURE
    elif (
        external_inputs["materials"]["all_present"]
        and external_inputs["credentials"]["all_present"]
    ):
        status = STATUS_OK
        exit_code = EXIT_OK
    else:
        status = STATUS_BLOCKED
        exit_code = EXIT_BLOCKED

    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "mode": MODE,
        "status": status,
        "exit_code": exit_code,
        "declaration": declaration_record,
        "build_profile": build_record,
        "app_manifest": app_manifest_record,
        "external_inputs": external_inputs,
        "signing_performed": False,
        "signed_hap_generated": False,
        "agc_access": False,
        "production_ready": PRODUCTION_READY,
        "production_ready_reason": PRODUCTION_READY_REASON,
        "failures": failures,
    }
    return result, exit_code


def render_json(result: dict[str, Any]) -> str:
    """Deterministic, ASCII-safe machine output."""
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agc_signing_preflight",
        description=(
            "Validate Harmony AGC signing inputs without signing or AGC access."
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
        args.repo_root.resolve() / DEFAULT_DECLARATION_RELPATH
        if args.declaration is None
        else args.declaration
    )
    result, exit_code = run_preflight(
        repo_root=args.repo_root,
        declaration_path=declaration,
    )
    sys.stdout.write(render_json(result) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
