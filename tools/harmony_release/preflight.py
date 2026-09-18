"""HarmonyOS release/AGC signing preflight.

Fail-closed gate that pins the repository to its honest "unsigned"
boundary until AGC release signing materials arrive out-of-band.

Safety contract:
- Never prints environment variable values, secrets, or absolute paths.
- Never reads the contents of signing material files (stat/suffix only).
- Deterministic JSON: identical repository state produces identical bytes.

Expectation modes:
- default / --expect-unsigned: signingConfigs must still be the empty array
  (the honest unsigned boundary); non-empty configs fail closed.
- --expect-signed: the repository claims the signed contract, so
  signingConfigs must be non-empty AND the three external materials
  referenced by AIOS_HARMONY_CERT_PATH / AIOS_HARMONY_PROFILE_PATH /
  AIOS_HARMONY_KEYSTORE_PATH must be present and valid. Absent materials are
  an external blocker (status blocked_by_external_materials, exit 2 - the
  same signal --require-materials uses); present-but-invalid materials and
  empty signingConfigs are contract violations, so they fail closed (exit 1).
- The signed-mode keys (expect_signed, signed_contract) are additive and are
  emitted only in --expect-signed mode, so default-mode output stays
  byte-identical to the pre-existing unsigned contract.

Exit codes: 0 = ok or (by default) blocked-by-missing-materials,
1 = failure (fail-closed), 2 = blocked and materials are required
(--require-materials, or always in --expect-signed mode).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

try:  # package import (pytest, python -m)
    from tools.harmony_release.json5lite import load_json5_relaxed
except ImportError:  # direct script: python tools/harmony_release/preflight.py
    from json5lite import load_json5_relaxed

SCHEMA_VERSION = 1
TOOL_NAME = "harmony_release_preflight"
BUILD_PROFILE_RELPATH = Path("apps/harmony/build-profile.json5")
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]

# Env var name -> required file extension. Values are never printed.
MATERIAL_ENV_VARS = {
    "AIOS_HARMONY_CERT_PATH": ".cer",
    "AIOS_HARMONY_PROFILE_PATH": ".p7b",
    "AIOS_HARMONY_KEYSTORE_PATH": ".p12",
}

# Signing material suffixes that must never exist inside the repository.
MATERIAL_SUFFIXES = frozenset({".p12", ".p7b", ".cer", ".csr", ".jks"})

# Pruned during the repo scan: VCS data, local evidence, build output,
# caches, vendored/installed trees. Not a security boundary by itself;
# the .gitignore patterns are the commit-side defense.
SCAN_PRUNED_DIRS = frozenset({
    ".git", ".hg", ".svn", ".verify", ".hvigor", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "node_modules", "build", "dist", "coverage", ".next",
})

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_MISSING_MATERIALS = 2


def check_build_profile(
    repo_root: Path, expect_signed: bool = False
) -> Tuple[dict, List[dict]]:
    """Verify signingConfigs against the expected signing boundary.

    expect_signed=False (default): signingConfigs must be the empty array.
    expect_signed=True: signingConfigs must be non-empty; an empty array is a
    contract violation because the caller declared the signed state.
    """
    path = repo_root / BUILD_PROFILE_RELPATH
    result = {
        "path": BUILD_PROFILE_RELPATH.as_posix(),
        "exists": path.is_file(),
        "parseable": False,
        "signing_configs_count": None,
        "unsigned_boundary": None,
    }
    failures: List[dict] = []
    if not result["exists"]:
        failures.append({"code": "build_profile_missing"})
        return result, failures
    data = load_json5_relaxed(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        failures.append({"code": "build_profile_unparseable"})
        return result, failures
    result["parseable"] = True
    app = data.get("app")
    configs = app.get("signingConfigs") if isinstance(app, dict) else None
    if configs is None:
        failures.append({"code": "signing_configs_missing"})
        return result, failures
    if not isinstance(configs, list):
        failures.append({"code": "signing_configs_invalid"})
        return result, failures
    result["signing_configs_count"] = len(configs)
    result["unsigned_boundary"] = len(configs) == 0
    if expect_signed:
        if not configs:
            failures.append({"code": "signing_configs_empty"})
    elif configs:
        failures.append({
            "code": "signing_configs_not_empty",
            "detail": {"signing_configs_count": len(configs)},
        })
    return result, failures


def scan_repo_materials(repo_root: Path) -> Tuple[dict, List[dict]]:
    """Scan the repo for signing material files; presence fails closed.

    Names/extensions only — file contents are never opened.
    """
    findings: List[dict] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(d for d in dirnames if d not in SCAN_PRUNED_DIRS)
        for name in sorted(filenames):
            ext = Path(name).suffix.lower()
            if ext in MATERIAL_SUFFIXES:
                rel = Path(dirpath, name).relative_to(repo_root).as_posix()
                findings.append({"extension": ext, "path": rel})
    findings.sort(key=lambda item: (item["extension"], item["path"]))
    result = {"scanned": True, "count": len(findings), "findings": findings}
    if findings:
        return result, [{
            "code": "repo_signing_material_present",
            "detail": {"count": len(findings)},
        }]
    return result, []


def check_external_materials(repo_root: Path) -> Tuple[dict, List[dict]]:
    """Validate the three material env vars. Values are never emitted.

    Per variable the JSON only carries: name, present, valid, expected
    extension, and an error category. Missing variables are not failures;
    they produce the blocked status at the run level.
    """
    resolved_root = repo_root.resolve()
    variables: dict = {}
    failures: List[dict] = []
    for name in sorted(MATERIAL_ENV_VARS):
        entry = {
            "expected_extension": MATERIAL_ENV_VARS[name],
            "present": False,
            "valid": None,
            "error": "not_set",
        }
        raw = os.environ.get(name)
        if raw == "":
            entry["error"] = "empty_value"
        elif raw is not None:
            entry["present"] = True
            error = _classify_material_path(raw, entry["expected_extension"], resolved_root)
            entry["error"] = error
            entry["valid"] = error is None
            if error is not None:
                failures.append({
                    "code": "external_material_invalid",
                    "detail": {"variable": name, "error": error},
                })
        variables[name] = entry
    result = {
        "variables": variables,
        "all_present": all(v["present"] for v in variables.values()),
        "all_valid": None,
    }
    if result["all_present"]:
        result["all_valid"] = all(v["valid"] for v in variables.values())
    return result, failures


def _classify_material_path(
    raw: str, expected_ext: str, resolved_root: Path
) -> Optional[str]:
    """Return an error category for a material path, or None when valid."""
    try:
        resolved = Path(raw).expanduser().resolve()
    except OSError:
        return "unresolvable_path"
    if resolved == resolved_root or resolved.is_relative_to(resolved_root):
        return "inside_repository"
    if not resolved.exists():
        return "path_not_found"
    if not resolved.is_file():
        return "not_a_regular_file"
    if resolved.suffix.lower() != expected_ext:
        return "wrong_extension"
    return None


def signed_contract_summary(build_profile: dict, external_materials: dict) -> dict:
    """Additive signed-mode view of the contract (never carries values).

    Derived purely from already-emitted facts, so it can never leak an
    environment value or a path.
    """
    count = build_profile.get("signing_configs_count")
    configs_non_empty = count > 0 if isinstance(count, int) else None
    materials_present = bool(external_materials.get("all_present"))
    materials_valid = external_materials.get("all_valid")
    return {
        "expect_signed": True,
        "signing_configs_required_non_empty": True,
        "signing_configs_non_empty": configs_non_empty,
        "materials_required": True,
        "materials_present": materials_present,
        "materials_valid": materials_valid,
        "satisfied": bool(configs_non_empty) and materials_present
        and materials_valid is True,
    }


def record_hap(hap_arg: Optional[str]) -> Tuple[Optional[dict], List[dict]]:
    """Record artifact facts only; signedness is never inferred."""
    if not hap_arg:
        return None, []
    path = Path(hap_arg)
    record = {
        "exists": path.is_file(),
        "size_bytes": None,
        "sha256": None,
        "filename_has_unsigned": "unsigned" in path.name.lower(),
    }
    warnings: List[dict] = []
    if record["exists"]:
        record["size_bytes"] = path.stat().st_size
        record["sha256"] = _sha256_upper(path)
    else:
        warnings.append({"code": "hap_path_missing"})
    if not record["filename_has_unsigned"]:
        warnings.append({"code": "hap_filename_not_unsigned"})
    return record, warnings


def _sha256_upper(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def run_preflight(
    repo_root: Path,
    strict: bool = False,
    require_materials: bool = False,
    hap: Optional[str] = None,
    expect_signed: bool = False,
) -> Tuple[dict, int]:
    """Run all checks and build the deterministic result + exit code.

    expect_signed selects the expectation mode (see the module docstring).
    It only adds result keys; no existing key changes meaning.
    """
    failures: List[dict] = []
    warnings: List[dict] = []
    build_profile, more = check_build_profile(repo_root, expect_signed=expect_signed)
    failures += more
    repo_materials, more = scan_repo_materials(repo_root)
    failures += more
    external_materials, more = check_external_materials(repo_root)
    failures += more
    hap_record, more = record_hap(hap)
    warnings += more
    if strict:
        failures += warnings
        warnings = []
    failures.sort(key=lambda item: json.dumps(item, sort_keys=True))

    if failures:
        status = "failure"
    elif external_materials["all_present"]:
        status = "ok"
    else:
        status = "blocked_by_external_materials"

    # --expect-signed always requires the external materials, exactly as
    # --require-materials does in the unsigned mode.
    materials_required = require_materials or expect_signed
    exit_code = EXIT_FAILURE if failures else EXIT_OK
    if exit_code == EXIT_OK and materials_required and status == "blocked_by_external_materials":
        exit_code = EXIT_MISSING_MATERIALS
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": status,
        "exit_code": exit_code,
        "strict": strict,
        "require_materials": require_materials,
        "build_profile": build_profile,
        "repo_materials": repo_materials,
        "external_materials": external_materials,
        "hap": hap_record,
        "warnings": warnings,
        "failures": failures,
    }
    if expect_signed:
        result["expect_signed"] = True
        result["signed_contract"] = signed_contract_summary(
            build_profile, external_materials
        )
    return result, exit_code


def render_json(result: dict) -> str:
    """Deterministic serialization (sorted keys, ASCII-safe)."""
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="preflight",
        description="Harmony release/AGC signing preflight (fail-closed).",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=DEFAULT_REPO_ROOT,
        help="Repository root to check (default: this checkout).",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Upgrade warnings to failures.",
    )
    parser.add_argument(
        "--require-materials", action="store_true",
        help="Exit 2 when external signing materials are absent.",
    )
    parser.add_argument(
        "--hap", default=None,
        help="Optional HAP path: record size/SHA256/unsigned-name flag only.",
    )
    expectations = parser.add_mutually_exclusive_group()
    expectations.add_argument(
        "--expect-signed", dest="expect_signed", action="store_true",
        help="Require non-empty signingConfigs and valid AIOS_HARMONY_* materials.",
    )
    expectations.add_argument(
        "--expect-unsigned", dest="expect_signed", action="store_false",
        help="Require the empty signingConfigs unsigned boundary (default).",
    )
    parser.set_defaults(expect_signed=False)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    result, exit_code = run_preflight(
        repo_root=args.repo_root.resolve(),
        strict=args.strict,
        require_materials=args.require_materials,
        hap=args.hap,
        expect_signed=args.expect_signed,
    )
    sys.stdout.write(render_json(result) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
