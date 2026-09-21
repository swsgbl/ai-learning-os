"""AGC closure manifest builder (M14-68H2).

Aggregates the JSON evidence reports produced by the release-chain tools
(``preflight``, ``release_build``, ``sign_hap``, ``verify_signature``,
``device_preflight``, ``device_smoke``) into one deterministic closure
manifest that answers a single question honestly: is every gate required
for an AGC release genuinely pass?

Safety contract:
- Reads only the given JSON evidence files. Never reads the environment,
  never spawns a process, never touches a device, never builds or signs.
- Cross-report chain consistency (M14-79): the release_build output
  hash must equal the sign_hap input hash, and the sign_hap
  signed-output hash must equal the verify_signature input hash. A
  mismatch (stale or mixed evidence from different builds) blocks the
  manifest even when every per-gate status passes; a missing side
  never triggers it.
- Signedness is never inferred from a file name: only a
  ``verify_signature`` report with status ``signed_and_valid`` counts as
  signedness evidence; a signing claim (``claimed_signed``) alone never
  passes the closure, and an absent sign/verify report blocks it.
- Fail closed: a missing, duplicated, non-passing or unclassifiable
  report blocks the manifest (``status: blocked``), and
  ``production_ready`` stays ``false`` in this slice by construction.
- Input paths must be regular files with no symlink/reparse-point
  component anywhere in the path; output paths must be regular-or-absent
  files inside real directories, must not overlap any input, and must not
  conflict with each other. Anything else is rejected before a single
  byte is written.
- Outputs are written atomically (temp file + ``os.replace``); when any
  write fails, every prior output is restored byte-for-byte (or removed
  again when it did not exist before).
- Only human-safe labels reach the manifest, the markdown and the
  errors: statuses, failure codes, evidence basenames, repo-relative
  artifact paths and hashes - never secret values, never absolute paths.

Exit codes: 0 = every required gate genuinely pass, 1 = failure (an
input or output was rejected/unreadable - no manifest written, prior
outputs preserved), 2 = manifest produced but blocked.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

SCHEMA_VERSION = 1
TOOL_NAME = "agc-closure-manifest"

STATUS_OK = "ok"
STATUS_BLOCKED = "blocked"
STATUS_FAILURE = "failure"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_BLOCKED = 2

# Fixed for this slice: the manifest never claims production readiness.
PRODUCTION_READY = False
PRODUCTION_READY_REASON = "fixed_false_in_this_slice"

# Evidence tool field -> gate name (the classification contract).
TOOL_GATE_NAMES = {
    "harmony_release_preflight": "preflight",
    "harmony_release_build": "release_build",
    "harmony_sign_hap": "sign_hap",
    "harmony_release_verify_signature": "verify_signature",
    "harmony_device_preflight": "device_preflight",
    "harmony_device_smoke": "device_smoke",
}
GATE_TOOL_NAMES = {gate: tool for tool, gate in TOOL_GATE_NAMES.items()}
REQUIRED_GATES = tuple(sorted(TOOL_GATE_NAMES.values()))

# The only statuses that genuinely pass each gate. Anything else -
# including ``blocked_by_external_materials``, ``planned`` (a plan is not
# evidence) and every verify_signature negative verdict - blocks.
PASS_STATUSES = {
    "preflight": ("ok",),
    "release_build": ("ok",),
    "sign_hap": ("ok",),
    "verify_signature": ("signed_and_valid",),
    "device_preflight": ("ok",),
    "device_smoke": ("ok",),
}

MAX_INPUT_BYTES = 8 << 20  # evidence reports are a few KiB
MAX_UNCLASSIFIED_LABELS = 8

# Human-safe text shapes. Anything that does not match is replaced by a
# fixed fallback label, so a forged report can never smuggle a value.
LABEL_RE = re.compile(r"^[a-z0-9_.-]{1,64}$")
PATH_TEXT_RE = re.compile(r"^[A-Za-z0-9._/@+-]{1,256}$")
SHA256_RE = re.compile(r"^[0-9A-Fa-f]{64}$")

# Indirection so tests can simulate a failing os.replace atomically.
_os_replace = os.replace


def _safe_label(value: object, fallback: str = "invalid_label") -> str:
    """Return ``value`` only when it is a short printable label."""
    if isinstance(value, str) and LABEL_RE.match(value):
        return value
    return fallback


def _safe_basename(name: str) -> str:
    if isinstance(name, str) and PATH_TEXT_RE.match(name):
        return name
    return "invalid_name"


# ---------------------------------------------------------------------------
# Path validation: no symlinks, no reparse points, regular files only.
# ---------------------------------------------------------------------------

def _lstat(path: Path) -> tuple[os.stat_result | None, str | None]:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None, "path_not_found"
    except OSError:
        return None, "path_inaccessible"
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_point", False):
        return None, "symlink_or_reparse_point"
    return info, None


def _components(path: Path) -> list[Path]:
    """Every component of an absolute path, anchor first."""
    parts = path.parts
    return [Path(*parts[:index]) for index in range(1, len(parts) + 1)]


def _validate_input_file(path: Path) -> str | None:
    """Reject symlink components and non-regular input files."""
    chain = _components(path)
    for component in chain[:-1]:
        info, error = _lstat(component)
        if error == "symlink_or_reparse_point":
            return "symlink_component"
        if error is not None:
            return error
        if not stat.S_ISDIR(info.st_mode):
            return "component_not_a_directory"
    info, error = _lstat(path)
    if error == "symlink_or_reparse_point":
        return "symlink_or_reparse_point"
    if error is not None:
        return error
    if not stat.S_ISREG(info.st_mode):
        return "not_a_regular_file"
    return None


def _validate_output_target(path: Path) -> str | None:
    """Output must be absent or a regular file, inside real directories."""
    chain = _components(path)
    for component in chain[:-1]:
        info, error = _lstat(component)
        if error == "symlink_or_reparse_point":
            return "symlink_component"
        if error is not None:
            return error
        if not stat.S_ISDIR(info.st_mode):
            return "component_not_a_directory"
    info, error = _lstat(path)
    if error == "symlink_or_reparse_point":
        return "symlink_or_reparse_point"
    if error == "path_not_found":
        return None  # a fresh output file is fine
    if error is not None:
        return error
    if not stat.S_ISREG(info.st_mode):
        return "not_a_regular_file"
    return None


# ---------------------------------------------------------------------------
# Evidence loading and classification.
# ---------------------------------------------------------------------------

def _load_report(path: Path) -> tuple[dict | None, str | None]:
    """Parse one evidence file; return (report, error category)."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None, "unreadable"
    if len(raw) > MAX_INPUT_BYTES:
        return None, "too_large"
    try:
        parsed = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None, "not_json"
    if not isinstance(parsed, dict):
        return None, "not_an_object"
    if not isinstance(parsed.get("tool"), str) or not isinstance(
        parsed.get("status"), str
    ):
        return None, "tool_or_status_missing"
    return parsed, None


def _failure_codes(report: dict) -> list[str]:
    """Sorted unique failure codes; details never travel."""
    codes = set()
    failures = report.get("failures")
    if isinstance(failures, list):
        for item in failures:
            if isinstance(item, dict) and isinstance(item.get("code"), str):
                label = _safe_label(item["code"])
                if label != "invalid_label":
                    codes.add(label)
    return sorted(codes)


def _hap_record(report: dict | None) -> dict | None:
    """The HAP facts, taken from a release_build artifact record only.

    Every field is type-checked and sanitized; a partial or forged
    artifact record yields ``None`` instead of half-truths.
    """
    if not isinstance(report, dict):
        return None
    artifact = report.get("artifact")
    if not isinstance(artifact, dict):
        return None
    relpath = artifact.get("relpath")
    size_bytes = artifact.get("size_bytes")
    sha256 = artifact.get("sha256")
    if not isinstance(relpath, str) or not PATH_TEXT_RE.match(relpath):
        return None
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool):
        return None
    if size_bytes < 0:
        return None
    if not isinstance(sha256, str) or not SHA256_RE.match(sha256):
        return None
    name_matches = artifact.get("name_matches_expected")
    return {
        "relpath": relpath,
        "size_bytes": size_bytes,
        "sha256": sha256.upper(),
        "filename_has_unsigned": artifact.get("filename_has_unsigned") is True,
        "name_matches_expected": (
            name_matches if isinstance(name_matches, bool) else None
        ),
        "inside_repository": artifact.get("inside_repository") is True,
    }


def _hap_sha256_from(report: dict | None, section: str) -> str | None:
    """Sanitized SHA-256 from a report HAP record, if present and valid.

    "section" selects the record: "artifact" (the release_build output
    or the sign_hap signed output) or "input" (the HAP a tool was
    pointed at). A missing, partial or forged record yields None so
    the cross-report chain check never fires on absent evidence -
    missing evidence stays the job of the per-gate checks.
    """
    if not isinstance(report, dict):
        return None
    record = report.get(section)
    if not isinstance(record, dict):
        return None
    sha256 = record.get("sha256")
    if not isinstance(sha256, str) or not SHA256_RE.match(sha256):
        return None
    return sha256.upper()


def _chain_consistency(
    chosen: dict[str, tuple[str, dict]],
) -> list[dict]:
    """Cross-report HAP chain: the release_build output hash must equal
    the sign_hap input hash, and the sign_hap signed-output hash must
    equal the verify_signature input hash (fail-closed on a mismatch
    when both sides are present).

    Per-gate status checks cannot see stale or mixed evidence: a
    release_build report for build N plus a sign_hap report still
    pointing at build N-1 passes every per-gate test. This check
    compares only sanitized hashes, never paths or values, and only
    fires when both sides of a link exist - a missing report stays
    the missing_report blocker job.
    """
    blockers: list[dict] = []

    def chosen_report(gate: str) -> dict | None:
        entry = chosen.get(gate)
        return entry[1] if entry else None

    links = (
        (
            "release_build",
            "artifact",
            "sign_hap",
            "input",
            "build_artifact_vs_sign_input",
        ),
        (
            "sign_hap",
            "artifact",
            "verify_signature",
            "input",
            "sign_artifact_vs_verify_input",
        ),
    )
    for left_gate, left_key, right_gate, right_key, link in links:
        left = _hap_sha256_from(chosen_report(left_gate), left_key)
        right = _hap_sha256_from(chosen_report(right_gate), right_key)
        if left is not None and right is not None and left != right:
            blockers.append({
                "code": "evidence_hap_mismatch",
                "gate": "evidence_chain",
                "detail": {"link": link},
            })
    return blockers


def _gate_facts(gate: str, report: dict) -> dict:
    """Value-free, tool-specific facts for the gate table."""
    if gate == "release_build":
        return {"artifact_recorded": _hap_record(report) is not None}
    if gate == "sign_hap":
        return {
            "claimed_signed": report.get("claimed_signed") is True,
            # The signing wrapper never verifies: a claim stays a claim.
            "signedness_verified": report.get("signedness_verified") is True,
        }
    if gate == "verify_signature":
        signature = report.get("signature")
        signed = signature.get("signed") if isinstance(signature, dict) else None
        verified = signature.get("verified") if isinstance(signature, dict) else None
        return {
            "signature_signed": (
                signed if isinstance(signed, bool) else None
            ),
            "signature_verified": (
                verified if isinstance(verified, bool) else None
            ),
        }
    return {}


def _gate_pass(gate: str, report: dict) -> tuple[bool, dict]:
    """Genuinely-pass test plus extra human-safe blocker detail."""
    status = report.get("status")
    passed = status in PASS_STATUSES[gate]
    extra: dict[str, object] = {}
    # A signing run is only half the evidence: it must also claim the
    # output was written. Verification is the verify gate's job.
    if gate == "sign_hap" and passed and report.get("claimed_signed") is not True:
        passed = False
        extra["claimed_signed"] = False
    if gate == "release_build" and passed and _hap_record(report) is None:
        passed = False
        extra["artifact_record"] = "missing_or_invalid"
    return passed, extra


def _gate_record(
    gate: str,
    source: str | None,
    report: dict | None,
    passed: bool = False,
) -> dict:
    if report is None:
        return {
            "name": gate,
            "tool": GATE_TOOL_NAMES[gate],
            "source_name": None,
            "source_status": None,
            "pass": False,
            "failure_codes": [],
            "facts": {},
        }
    return {
        "name": gate,
        "tool": GATE_TOOL_NAMES[gate],
        "source_name": _safe_basename(source or ""),
        "source_status": _safe_label(report.get("status"), "invalid_status"),
        "pass": bool(passed),
        "failure_codes": _failure_codes(report),
        "facts": _gate_facts(gate, report),
    }


def _next_action(blocker: dict) -> str:
    """One human-safe next-action label per blocker (no values)."""
    code = blocker["code"]
    gate = blocker["gate"]
    if code == "missing_report":
        return {
            "preflight": "run_release_preflight",
            "release_build": "run_release_build",
            "sign_hap": "run_sign_hap_with_external_materials",
            "verify_signature": "run_signature_verification",
            "device_preflight": "run_device_preflight",
            "device_smoke": "run_device_smoke",
        }.get(gate, f"supply_{gate}_report")
    if code == "unclassified_report":
        return "supply_reports_from_known_release_tools"
    if code == "duplicate_report":
        return "deduplicate_evidence_reports_per_gate"
    if code == "evidence_hap_mismatch":
        return "rerun_release_chain_so_evidence_shares_one_hap"
    detail = blocker.get("detail") if isinstance(blocker.get("detail"), dict) else {}
    source_status = detail.get("source_status")
    if code == "gate_not_pass":
        if source_status == "blocked_by_external_materials":
            return "provide_external_signing_materials_out_of_band"
        if gate == "verify_signature" and source_status == "unsigned":
            return "sign_the_unsigned_release_hap_then_verify"
        if gate == "verify_signature":
            return "rerun_signature_verification_until_signed_and_valid"
        return f"review_and_rerun_{gate}"
    return f"review_{code}"


def build_manifest(reports: Sequence[tuple[str, dict]]) -> dict:
    """Aggregate classified evidence into the deterministic manifest."""
    by_gate: dict[str, list[tuple[str, dict]]] = {
        gate: [] for gate in REQUIRED_GATES
    }
    unclassified: list[str] = []
    for source, report in reports:
        raw_tool = report.get("tool")
        gate = (
            TOOL_GATE_NAMES.get(raw_tool) if isinstance(raw_tool, str) else None
        )
        if gate is None:
            unclassified.append(
                _safe_label(raw_tool, "non_printable_tool_label")
            )
        else:
            by_gate[gate].append((source, report))

    gates: list[dict] = []
    blockers: list[dict] = []
    hap: dict | None = None
    chosen: dict[str, tuple[str, dict]] = {}
    for gate in REQUIRED_GATES:
        entries = by_gate[gate]
        if not entries:
            gates.append(_gate_record(gate, None, None))
            blockers.append({
                "code": "missing_report",
                "gate": gate,
                "detail": {"tool": GATE_TOOL_NAMES[gate]},
            })
            continue
        if len(entries) > 1:
            blockers.append({
                "code": "duplicate_report",
                "gate": gate,
                "detail": {
                    "count": len(entries),
                    "sources": sorted(source for source, _ in entries),
                },
            })
        # Deterministic choice among duplicates: smallest sanitized status,
        # then smallest evidence basename - never the CLI order.
        source, report = min(
            entries,
            key=lambda entry: (
                _safe_label(entry[1].get("status"), "invalid_status"),
                entry[0],
            ),
        )
        chosen[gate] = (source, report)
        passed, extra = _gate_pass(gate, report)
        gates.append(_gate_record(gate, source, report, passed))
        if not passed:
            detail: dict[str, object] = {
                "source_status": _safe_label(
                    report.get("status"), "invalid_status"
                )
            }
            detail.update(extra)
            blockers.append({
                "code": "gate_not_pass",
                "gate": gate,
                "detail": detail,
            })
        if gate == "release_build":
            hap = _hap_record(report)

    blockers += _chain_consistency(chosen)

    if unclassified:
        blockers.append({
            "code": "unclassified_report",
            "gate": "unclassified",
            "detail": {
                "count": len(unclassified),
                "tools": sorted(set(unclassified))[:MAX_UNCLASSIFIED_LABELS],
            },
        })

    blockers.sort(key=lambda item: json.dumps(item, sort_keys=True))
    status = STATUS_BLOCKED if blockers else STATUS_OK
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": status,
        "production_ready": PRODUCTION_READY,
        "production_ready_reason": PRODUCTION_READY_REASON,
        "inputs": {
            "count": len(reports),
            "sources": sorted(source for source, _ in reports),
        },
        "gates": gates,
        "hap": hap,
        "blockers": blockers,
        "next_actions": sorted(
            {_next_action(blocker) for blocker in blockers}
        ),
    }


# ---------------------------------------------------------------------------
# Atomic output writing with byte-for-byte rollback.
# ---------------------------------------------------------------------------

def _read_prior_bytes(path: Path) -> bytes | None:
    info, error = _lstat(path)
    if error == "path_not_found":
        return None
    if error is not None:
        raise OSError(error)
    if not stat.S_ISREG(info.st_mode):
        raise OSError("not_a_regular_file")
    return path.read_bytes()


def _restore_prior(path: Path, prior: bytes | None) -> None:
    """Best-effort rollback of one already-replaced output."""
    try:
        if prior is None:
            path.unlink(missing_ok=True)
            return
        handle, temp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name + ".", suffix=".restore"
        )
        with os.fdopen(handle, "wb") as stream:
            stream.write(prior)
            stream.flush()
            os.fsync(stream.fileno())
        _os_replace(temp_name, path)
    except OSError:
        pass


def _write_outputs(outputs: list[tuple[Path, bytes]]) -> list[dict]:
    """Write all outputs atomically; restore every prior file on failure."""
    backups: dict[Path, bytes | None] = {}
    temps: list[Path] = []
    replaced: list[Path] = []
    try:
        for path, payload in outputs:
            backups[path] = _read_prior_bytes(path)
            handle, temp_name = tempfile.mkstemp(
                dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
            )
            temps.append(Path(temp_name))
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        for (path, _payload), temp in zip(outputs, temps):
            _os_replace(temp, path)
            replaced.append(path)
    except OSError:
        for path in replaced:
            _restore_prior(path, backups.get(path))
        return [{"code": "output_write_failed"}]
    finally:
        for temp in temps:
            try:
                temp.unlink()
            except OSError:
                pass
    return []


def _plan_outputs(
    json_output: str | None,
    markdown_output: str | None,
    manifest: dict,
    input_paths: Sequence[Path],
    failures: list[dict],
) -> list[tuple[Path, bytes]]:
    """Validate output targets and build the (path, payload) plan."""
    plan: list[tuple[Path, bytes]] = []
    raw_plan: list[tuple[str, bytes]] = []
    if json_output:
        raw_plan.append((json_output, (render_json(manifest) + "\n").encode()))
    if markdown_output:
        raw_plan.append(
            (markdown_output, render_markdown(manifest).encode())
        )
    seen: list[Path] = []
    for raw, payload in raw_plan:
        path = Path(os.path.abspath(raw))
        error = _validate_output_target(path)
        if error is not None:
            failures.append({
                "code": "output_rejected",
                "detail": {"source": _safe_basename(path.name), "error": error},
            })
            continue
        if any(path == other for other in seen):
            failures.append({
                "code": "conflicting_output_paths",
                "detail": {"source": _safe_basename(path.name)},
            })
            continue
        if any(path == input_path for input_path in input_paths):
            failures.append({
                "code": "output_overlaps_input",
                "detail": {"source": _safe_basename(path.name)},
            })
            continue
        seen.append(path)
        plan.append((path, payload))
    return plan


def _failure_result(failures: list[dict]) -> dict:
    failures.sort(key=lambda item: json.dumps(item, sort_keys=True))
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": STATUS_FAILURE,
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# Rendering (deterministic; markdown carries safe fields only).
# ---------------------------------------------------------------------------

def render_json(result: dict) -> str:
    """Deterministic serialization (sorted keys, ASCII-safe)."""
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True)


def render_markdown(manifest: dict) -> str:
    """Markdown over safe fields only: no details, no values, no paths."""
    lines = [
        "# AGC Closure Manifest",
        "",
        f"- status: {manifest['status']}",
        f"- production_ready: {str(manifest['production_ready']).lower()}",
        f"- production_ready_reason: {manifest['production_ready_reason']}",
        f"- inputs: {manifest['inputs']['count']}",
        "",
        "## Gates",
        "",
        "| gate | tool | source_status | pass |",
        "| --- | --- | --- | --- |",
    ]
    for gate in manifest["gates"]:
        source_status = gate["source_status"] or "absent"
        lines.append(
            f"| {gate['name']} | {gate['tool']} | {source_status} "
            f"| {str(gate['pass']).lower()} |"
        )
    hap = manifest["hap"]
    lines += ["", "## HAP", ""]
    if hap is None:
        lines.append("- hap: none (no valid release_build artifact record)")
    else:
        lines += [
            f"- relpath: {hap['relpath']}",
            f"- size_bytes: {hap['size_bytes']}",
            f"- sha256: {hap['sha256']}",
            f"- filename_has_unsigned: {str(hap['filename_has_unsigned']).lower()}",
        ]
    lines += ["", "## Blockers", ""]
    if manifest["blockers"]:
        for blocker in manifest["blockers"]:
            line = f"- {blocker['code']}: {blocker['gate']}"
            detail = blocker.get("detail")
            if isinstance(detail, dict) and "source_status" in detail:
                line += f" (source_status={detail['source_status']})"
            lines.append(line)
    else:
        lines.append("- none")
    lines += ["", "## Next actions", ""]
    if manifest["next_actions"]:
        lines.extend(f"- {action}" for action in manifest["next_actions"])
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------

def run_agc_closure_manifest(
    inputs: Sequence[str] | None = None,
    json_output: str | None = None,
    markdown_output: str | None = None,
) -> tuple[dict, int]:
    """Aggregate evidence reports into the closure manifest."""
    failures: list[dict] = []
    reports: list[tuple[str, dict]] = []
    input_paths: list[Path] = []
    for raw in inputs or []:
        path = Path(os.path.abspath(raw))
        error = _validate_input_file(path)
        category = error
        if category is None:
            report, load_error = _load_report(path)
            if load_error is not None:
                category = load_error
        if category is not None:
            failures.append({
                "code": "input_rejected",
                "detail": {
                    "source": _safe_basename(path.name),
                    "error": category,
                },
            })
            continue
        reports.append((_safe_basename(path.name), report))
        input_paths.append(path)

    if failures:
        return _failure_result(failures), EXIT_FAILURE

    manifest = build_manifest(reports)
    exit_code = EXIT_OK if manifest["status"] == STATUS_OK else EXIT_BLOCKED

    outputs = _plan_outputs(
        json_output, markdown_output, manifest, input_paths, failures
    )
    if failures:
        return _failure_result(failures), EXIT_FAILURE
    if outputs:
        write_failures = _write_outputs(outputs)
        if write_failures:
            return _failure_result(write_failures), EXIT_FAILURE
    return manifest, exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agc_closure_manifest",
        description="Aggregate release-chain evidence into the AGC "
                    "closure manifest (fail-closed).",
    )
    parser.add_argument(
        "--input", action="append", default=[], required=False,
        metavar="JSON_EVIDENCE",
        help="Evidence report JSON (repeat once per report; the tool "
             "field classifies it).",
    )
    parser.add_argument(
        "--json-output", default=None,
        help="Optional path for the deterministic manifest JSON.",
    )
    parser.add_argument(
        "--markdown-output", default=None,
        help="Optional path for the human-safe markdown rendering.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result, exit_code = run_agc_closure_manifest(
        inputs=args.input,
        json_output=args.json_output,
        markdown_output=args.markdown_output,
    )
    sys.stdout.write(render_json(result) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
