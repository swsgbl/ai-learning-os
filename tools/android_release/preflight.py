"""Android release signing readiness preflight (M14-171A).

Fail-closed static gate that pins the apps/android Gradle configuration to
the honest "unsigned until external inputs arrive" boundary declared in
docs/MOBILE_DISTRIBUTION.md. Mirrors the safety contract of
tools/harmony_release/preflight.py:

- Never prints environment variable values, secrets, or matched text.
- Never reads the contents of keystore files (suffix scan only).
- Deterministic JSON: identical repository state produces identical bytes.

What this gate checks (static contracts over the Gradle wiring only — the
production keystore itself remains an external prerequisite that must never
enter the repository):

1. apps/android/app/build.gradle.kts must reference all five external input
   names (four AIOS_ANDROID_KEYSTORE_* variables plus the optional
   AIOS_ANDROID_SIGNING_PROPERTIES pointer to a repo-external properties
   file). Missing references mean the opt-in wiring drifted.
2. The Gradle text must contain the fail-closed repository-boundary guard
   (M14-171B) that rejects signing inputs resolving inside the repository:
   the guard function (definition plus both call sites — properties file
   and keystore), the repository anchor (nearest .git root with a
   rootProject.rootDir fallback), and real-path resolution. Missing or
   thinned-out guard tokens mean the boundary can be silently deleted.
3. The Gradle text must not contain debug-signing fallbacks, literal
   passwords, or literal in-repo keystore paths.
4. No Android signing material files (.jks/.keystore/.p12/.p7b/.cer/.csr)
   may exist inside the repository (pruned local dirs excepted).

Exit codes: 0 = ok (unsigned-ready honest default), 1 = fail-closed
contract violation. This tool never claims production readiness — an ok
result only means the build wiring stays opt-in, complete-or-fail, and
free of in-repo secrets.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

SCHEMA_VERSION = 2
TOOL_NAME = "android_release_preflight"
GRADLE_FILE_RELPATH = Path("apps/android/app/build.gradle.kts")
DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]

# External input names the Gradle wiring must reference (values never read).
REQUIRED_ENV_REFERENCES = frozenset({
    "AIOS_ANDROID_KEYSTORE_PATH",
    "AIOS_ANDROID_KEYSTORE_STORE_PASSWORD",
    "AIOS_ANDROID_KEYSTORE_KEY_ALIAS",
    "AIOS_ANDROID_KEYSTORE_KEY_PASSWORD",
    "AIOS_ANDROID_SIGNING_PROPERTIES",
})

# M14-171B repository-boundary guard tokens the Gradle wiring must contain,
# mapped to the minimum number of textual occurrences. This locks the
# fail-closed "signing inputs must resolve outside the repository" check so
# it cannot be silently deleted or thinned to a dead declaration. Only token
# names and occurrence counts appear in results — matched text is never echoed.
REQUIRED_BOUNDARY_GUARD: Dict[str, int] = {
    # guard function: 1 definition + 2 call sites (properties + keystore)
    "failClosedOutsideRepo": 3,
    # repository anchor: nearest .git root, rootProject.rootDir fallback
    "rootProject.rootDir": 1,
    # symlink/case-canonical path resolution (real path on both sides)
    "toRealPath": 1,
    # VCS anchor discovery (worktrees carry .git as a file)
    '".git"': 1,
}

# rule -> regexes. Matched text is never echoed into results.
FORBIDDEN_PATTERNS: Dict[str, Tuple[str, ...]] = {
    "debug_signing_fallback": (
        r"signingConfig\s*=\s*signingConfigs\.debug\b",
        r'signingConfig\s*=\s*signingConfigs\.getByName\(\s*["\']debug["\']\s*\)',
    ),
    "password_literal": (
        r'storePassword\s*=\s*["\']',
        r'keyPassword\s*=\s*["\']',
    ),
    "keystore_path_literal": (
        r'storeFile\s*=\s*file\(\s*["\']',
    ),
}

# Signing material suffixes that must never exist inside the repository
# (superset of the .gitignore defense from M13-10 plus Android-specific
# .keystore added by M14-171A).
MATERIAL_SUFFIXES = frozenset({".jks", ".keystore", ".p12", ".p7b", ".cer", ".csr"})

# Pruned during the repo scan: VCS data, local worktrees/evidence, build
# output, caches, vendored/installed trees. Not a security boundary by
# itself; the .gitignore patterns are the commit-side defense.
SCAN_PRUNED_DIRS = frozenset({
    ".git", ".hg", ".svn", ".verify", ".hvigor", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "node_modules", "build", "dist", "coverage", ".next",
    ".gradle", ".kotlin", ".idea", ".claude",
})

EXIT_OK = 0
EXIT_FAILURE = 1


def check_gradle_signing_contract(
    repo_root: Path,
) -> Tuple[dict, List[dict]]:
    """Verify the opt-in release-signing wiring in the Gradle text."""
    path = repo_root / GRADLE_FILE_RELPATH
    result: dict = {
        "path": GRADLE_FILE_RELPATH.as_posix(),
        "exists": path.is_file(),
        "required_env_references": {
            name: False for name in sorted(REQUIRED_ENV_REFERENCES)
        },
        "boundary_guard": {
            token: 0 for token in sorted(REQUIRED_BOUNDARY_GUARD)
        },
        "forbidden_pattern_hits": [],
    }
    failures: List[dict] = []
    if not result["exists"]:
        failures.append({"code": "gradle_file_missing"})
        return result, failures

    text = path.read_text(encoding="utf-8")
    for name in sorted(REQUIRED_ENV_REFERENCES):
        found = name in text
        result["required_env_references"][name] = found
        if not found:
            failures.append({
                "code": "missing_env_reference",
                "detail": {"env": name},
            })

    for token, required in sorted(REQUIRED_BOUNDARY_GUARD.items()):
        found = text.count(token)
        result["boundary_guard"][token] = found
        if found < required:
            failures.append({
                "code": "missing_boundary_guard",
                "detail": {"token": token, "found": found, "required": required},
            })

    for rule, patterns in sorted(FORBIDDEN_PATTERNS.items()):
        for pattern in patterns:
            if re.search(pattern, text):
                result["forbidden_pattern_hits"].append(rule)
                failures.append({
                    "code": "forbidden_pattern",
                    "detail": {"rule": rule},
                })
                break  # one failure per rule is enough to fail closed
    result["forbidden_pattern_hits"] = sorted(set(result["forbidden_pattern_hits"]))
    return result, failures


def scan_repo_materials(repo_root: Path) -> dict:
    """Suffix-only scan for signing materials inside the repository."""
    hits: List[str] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SCAN_PRUNED_DIRS for part in path.relative_to(repo_root).parts[:-1]):
            continue
        if path.suffix.lower() in MATERIAL_SUFFIXES:
            hits.append(path.relative_to(repo_root).as_posix())
    return {
        "count": len(hits),
        "files": sorted(hits),
        "suffixes_scanned": sorted(MATERIAL_SUFFIXES),
    }


def run_preflight(repo_root: Path = DEFAULT_REPO_ROOT) -> Tuple[dict, int]:
    """Run all checks and assemble the deterministic result payload."""
    repo_root = Path(repo_root).resolve()
    gradle_result, failures = check_gradle_signing_contract(repo_root)
    materials = scan_repo_materials(repo_root)
    if materials["count"]:
        failures.append({
            "code": "repo_material_present",
            "detail": {"files": materials["files"]},
        })

    failures = sorted(
        failures,
        key=lambda f: (f["code"], json.dumps(f.get("detail"), sort_keys=True)),
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": "ok" if not failures else "contract_violation",
        "gradle_contract": gradle_result,
        "repo_materials": materials,
        "failures": failures,
    }
    return result, EXIT_FAILURE if failures else EXIT_OK


def render_json(result: dict) -> str:
    """Deterministic JSON rendering (sorted keys, no timestamps)."""
    return json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Android release signing readiness preflight (fail-closed static "
            "contract; never reads secrets, never claims production readiness)."
        )
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help="Repository root to scan (defaults to this checkout).",
    )
    args = parser.parse_args(argv)
    result, code = run_preflight(repo_root=args.repo_root)
    sys.stdout.write(render_json(result) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
