"""Android release material bootstrapper (M14-174 readiness tool).

Prepares the **externally managed** release signing material for the Android
channel: a keystore plus a properties file consumable by the M14-171A
opt-in signing config (``AIOS_ANDROID_SIGNING_PROPERTIES``, keys
``keystore.path`` / ``keystore.storePassword`` / ``keystore.keyAlias`` /
``keystore.keyPassword``). Readiness only: running ``execute`` for real is
an operator (Codex) step outside this repository; this slice never generates
a keystore into the repo, never prints a password, and never touches the
network, Docker, or any remote.

Subcommands:

- ``plan`` — read-only preview: reports the exact keytool key parameters,
  target basenames and pre-flight target checks. Creates nothing.
- ``execute`` — generates the keystore via ``keytool -genkeypair`` (PKCS12
  store, RSA 2048, validity >= 10000 days, non-debug alias/dname) and the
  properties file. ONE OS-random password protects both the store and the
  key — the PKCS12 contract Android signing reads under — and is written to
  both password keys, never printed. Requires the explicit confirmation
  phrase. Both
  targets must live **outside the repository**, contain no symlink/junction/
  reparse-point path component (every existing component is checked without
  following the link), and not exist beforehand. POSIX targets are tightened
  to 0600; Windows targets have inheritance removed and access restricted to
  the current user (``USERDOMAIN\\USERNAME`` when both exist) via ``icacls``.
  Any failure cleans up files created by this run and never deletes
  pre-existing files.
- ``verify`` — re-checks existing material: presence, symlink-free paths,
  outside-repo placement, the four properties keys (values never echoed),
  store/key password equality (value-free boolean; unequal fails closed —
  the PKCS12 key is read with the store password), the keystore.path target
  identity, tightened permissions (POSIX mode
  check; Windows ACL read-back is honestly ``not_evaluated``), and the
  certificate via ``keytool -list`` (SHA256 fingerprint, alias, validity
  window judged against an injectable clock). Every keytool child runs
  under a forced English JVM locale (``JAVA_TOOL_OPTIONS`` override, no
  unsupported ``-J`` flags) and the parser tolerates JDK 17's leading-tab
  fingerprint indentation, so inspection is deterministic on any host.

Safety contract (mirrors tools/android_release/verify_artifact.py): output
is deterministic value-free JSON — no password, no keystore content, no
local absolute path, no child stdout/stderr is ever recorded; failures carry
a category plus safe fields only. The run password comes from ``secrets``
(OS random source) at runtime — one value per run protecting both the store
and the key — and is replaceable by an injectable entropy seam for
tests; keytool, the filesystem permission applier, the clock, and the
repository-root finder are all injectable so the test suite runs with zero
real keytool invocations and zero network.

Exit codes: 0 = success (plan ready / execute generated / verify verified),
1 = failed (every rejected input fails closed); argparse usage errors exit 2
per the interpreter's own convention.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

if __package__ in (None, ""):  # direct script execution: put repo on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.android_release.path_safety import is_link_or_reparse
from tools.android_release.verify_artifact import (
    CommandOutcome,
    child_env,
)

SCHEMA_VERSION = 1
TOOL_NAME = "android_release_material_bootstrapper"

REQUIRED_KEY_ALG = "RSA"
MIN_KEY_SIZE = 2048
MIN_VALIDITY_DAYS = 10000
# Pinning the store type matters for the password contract below: PKCS12
# protects the private key with the *store* password in practice (that is
# how Android Gradle Plugin / apksigner read it), so the key password must
# equal the store password or packageRelease fails with
# "Given final block not properly padded".
KEYSTORE_TYPE = "PKCS12"
DEFAULT_ALIAS = "aios-release"
# Whole-token, lower-cased comparison: debug-convention alias values that
# must never name a release key.
FORBIDDEN_ALIASES = frozenset({"androiddebugkey", "debug", "testkey"})
KEYTOOL_DNAME = "CN=AI Learning OS Release, O=AI Learning OS, C=CN"
CONFIRM_PHRASE = "GENERATE-RELEASE-MATERIAL-OUTSIDE-REPO"
PASSWORD_LENGTH = 32
PASSWORD_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
PROPERTIES_KEYS = (
    "keystore.path",
    "keystore.storePassword",
    "keystore.keyAlias",
    "keystore.keyPassword",
)
POSIX_PRIVATE_MODE = 0o600

STATUS_READY = "ready"
STATUS_BLOCKED = "blocked"
STATUS_GENERATED = "generated"
STATUS_FAILED = "failed"
STATUS_VERIFIED = "verified"
EXIT_OK = 0
EXIT_FAILURE = 1

# keytool output is locale-sensitive ("Alias name:" is localized) and JDK 17
# ``-list -v`` indents the fingerprint lines with a leading tab. Force a
# deterministic English JVM locale through JAVA_TOOL_OPTIONS — the only
# supported lever, since current keytool accepts no ``-J`` passthrough. The
# value *overrides* any inherited JAVA_TOOL_OPTIONS (never appended: an
# inherited locale or heap flag must not win). The "Picked up
# JAVA_TOOL_OPTIONS" banner the JVM then prints on stderr is harmless:
# child stdout/stderr are never recorded in any JSON.
KEYTOOL_JAVA_TOOL_OPTIONS = "-Duser.language=en -Duser.country=US"

# Leading [ \t]* only (never \s, which would cross newlines in MULTILINE
# mode): real JDK 17 Windows output prefixes SHA256 with a tab, while the
# captured value keeps its exact shape (32 hex pairs, colon-separated) and
# every match stays on a single line.
FINGERPRINT_RE = re.compile(
    r"^[ \t]*SHA256:[ \t]*([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){31})[ \t]*$",
    re.MULTILINE,
)
ALIAS_RE = re.compile(r"^[ \t]*Alias name:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
VALIDITY_RE = re.compile(r"^Valid from:[ \t]*(.+?)[ \t]*until:[ \t]*(.+?)[ \t]*$", re.MULTILINE)
JAVA_DATE_FORMATS = ("%a %b %d %H:%M:%S %Z %Y", "%a %b %d %H:%M:%S %z %Y")

Entropy = Callable[[int], str]
PermissionApplier = Callable[[Path], Tuple[bool, Optional[str]]]
PermissionChecker = Callable[[Path], Tuple[str, Optional[str]]]
RepoRootFinder = Callable[[], Optional[Path]]
Clock = Callable[[], datetime.datetime]


def subprocess_runner(argv: Sequence[str], env: Dict[str, str]) -> CommandOutcome:
    """Run one child tool; OSError propagates to the caller (fail-closed)."""
    proc = subprocess.run(
        list(argv), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env,
    )
    return CommandOutcome(proc.returncode, proc.stdout or "", proc.stderr or "")


def keytool_env() -> Dict[str, str]:
    """``child_env()`` plus the forced English JVM locale for keytool.

    Both the ``-genkeypair`` and ``-list`` children run with this
    environment so inspection is deterministic regardless of the host
    locale (a Chinese Windows host localizes "Alias name:"). The value
    overrides any inherited JAVA_TOOL_OPTIONS; see
    KEYTOOL_JAVA_TOOL_OPTIONS above for why this is not a ``-J`` option.
    """
    env = child_env()
    env["JAVA_TOOL_OPTIONS"] = KEYTOOL_JAVA_TOOL_OPTIONS
    return env


def secrets_entropy(length: int) -> str:
    """OS random source (secrets); never printed, never logged."""
    import secrets

    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def posix_permission_applier(path: Path) -> Tuple[bool, Optional[str]]:
    """Tighten to owner-only 0600 (POSIX)."""
    try:
        os.chmod(path, POSIX_PRIVATE_MODE)
    except OSError:
        return False, "permission_apply_failed"
    return True, None


def windows_permission_applier(path: Path) -> Tuple[bool, Optional[str]]:
    """Remove inheritance and grant full control to the current user only.

    Account selection (fail-closed): ``USERDOMAIN\\USERNAME`` when both are
    present, bare ``USERNAME`` when the domain is absent, and a hard failure
    when neither qualifies — ``USERDOMAIN`` alone names a domain, not an
    account, and must never be granted anything.
    """
    username = (os.environ.get("USERNAME") or "").strip()
    domain = (os.environ.get("USERDOMAIN") or "").strip()
    if username and domain:
        account = f"{domain}\\{username}"
    elif username:
        account = username
    else:
        return False, "permission_owner_unknown"
    argv = ["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:F"]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except OSError:
        return False, "permission_apply_failed"
    if proc.returncode != 0:
        return False, "permission_apply_failed"
    return True, None


def posix_permission_checker(path: Path) -> Tuple[str, Optional[str]]:
    """Report whether the mode is still owner-only 0600 (POSIX)."""
    import stat as stat_module

    try:
        mode = stat_module.S_IMODE(path.stat().st_mode)
    except OSError:
        return "not_evaluated", "permission_stat_failed"
    if mode == POSIX_PRIVATE_MODE:
        return "ok", None
    return "loose", "permissions_loose"


def windows_permission_checker(path: Path) -> Tuple[str, Optional[str]]:
    """ACL read-back cannot be summarized value-free; report honestly."""
    return "not_evaluated", None


def default_repo_root() -> Optional[Path]:
    """Nearest ancestor of this file containing .git (repo anchor)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


def default_permission_applier(path: Path) -> Tuple[bool, Optional[str]]:
    if os.name == "nt":
        return windows_permission_applier(path)
    return posix_permission_applier(path)


def default_permission_checker(path: Path) -> Tuple[str, Optional[str]]:
    if os.name == "nt":
        return windows_permission_checker(path)
    return posix_permission_checker(path)


def default_clock() -> datetime.datetime:
    return datetime.datetime.now()


# Module-level seams so tests (and main()) can inject fakes without spawning
# keytool/icacls or consuming OS randomness.
default_runner: Callable[[Sequence[str], Dict[str, str]], CommandOutcome] = subprocess_runner
default_entropy: Entropy = secrets_entropy


# ---------------------------------------------------------------------------
# keytool -list parsing (to value-free facts only)
# ---------------------------------------------------------------------------


def _parse_java_date(text: str) -> Optional[datetime.datetime]:
    """Parse a keytool date; timezone abbreviations are dropped, not trusted.

    ``%Z`` rejects most locale timezone names (CST, CEST, ...), so after the
    direct formats fail the timezone token is removed and only the calendar
    fields are parsed — a ±few-hours skew against a >=10000-day window.
    """
    cleaned = text.strip()
    for fmt in JAVA_DATE_FORMATS:
        try:
            return datetime.datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    match = re.match(
        r"^\w{3} (\w{3}) +(\d{1,2}) (\d{2}:\d{2}:\d{2}) \S+ (\d{4})$", cleaned
    )
    if match:
        try:
            return datetime.datetime.strptime(
                f"{match.group(1)} {match.group(2)} {match.group(3)} {match.group(4)}",
                "%b %d %H:%M:%S %Y",
            )
        except ValueError:
            return None
    return None


def parse_keytool_list(text: str) -> Dict[str, object]:
    """Extract fingerprint / alias / validity window (never raw stdout)."""
    facts: Dict[str, object] = {
        "sha256_fingerprint": None,
        "alias": None,
        "valid_from": None,
        "valid_until": None,
    }
    fp = FINGERPRINT_RE.search(text)
    if fp:
        facts["sha256_fingerprint"] = fp.group(1).replace(":", "").lower()
    alias = ALIAS_RE.search(text)
    if alias:
        facts["alias"] = alias.group(1).strip()
    window = VALIDITY_RE.search(text)
    if window:
        facts["valid_from"] = _parse_java_date(window.group(1))
        facts["valid_until"] = _parse_java_date(window.group(2))
    return facts


def validity_state(
    facts: Dict[str, object], now: datetime.datetime
) -> Dict[str, object]:
    """Judge the parsed window against ``now``; unparsable → not_evaluated."""
    valid_from = facts.get("valid_from")
    valid_until = facts.get("valid_until")
    if not isinstance(valid_from, datetime.datetime) or not isinstance(
        valid_until, datetime.datetime
    ):
        return {"state": "not_evaluated", "failure": None}
    if now < valid_from:
        return {"state": "not_yet_valid", "failure": "certificate_not_yet_valid"}
    if now > valid_until:
        return {"state": "expired", "failure": "certificate_expired"}
    return {"state": "valid", "failure": None}


# ---------------------------------------------------------------------------
# target validation (outside repo, link/reparse-free, absent)
# ---------------------------------------------------------------------------

# Detection seam: symlink **and** Windows junction/reparse points
# (Path.is_symlink alone misses junctions on Python 3.11 — see
# tools/android_release/path_safety.py). Injectable for tests.
_link_detector = is_link_or_reparse


def _symlink_free(path: Path) -> bool:
    """No symlink/junction/reparse point anywhere in the path's prefix.

    Every existing path component is checked without following the link.
    """
    current = path
    while True:
        if _link_detector(current):
            return False
        parent = current.parent
        if parent == current:
            return True
        current = parent


def _inside_repo(target: Path, repo_root: Path) -> bool:
    resolved = Path(os.path.realpath(target))
    repo = Path(os.path.realpath(repo_root))
    return resolved == repo or repo in resolved.parents


def validate_targets(
    keystore: Path,
    properties: Path,
    repo_root: Optional[Path],
    *,
    require_absent: bool,
) -> Tuple[Dict[str, object], List[dict]]:
    """Shared pre-flight checks; returns (check record, failures)."""
    record: Dict[str, object] = {
        "repo_root_found": repo_root is not None,
        "keystore_symlink_free": None,
        "properties_symlink_free": None,
        "keystore_outside_repo": None,
        "properties_outside_repo": None,
        "targets_absent": None,
    }
    failures: List[dict] = []
    if repo_root is None:
        failures.append({"code": "repo_root_not_found"})
        return record, failures
    for label, target in (("keystore", keystore), ("properties", properties)):
        if not _symlink_free(target):
            record[f"{label}_symlink_free"] = False
            failures.append({"code": "target_symlink", "detail": {"target": label}})
        else:
            record[f"{label}_symlink_free"] = True
        if _inside_repo(target, repo_root):
            record[f"{label}_outside_repo"] = False
            failures.append({"code": "target_inside_repo", "detail": {"target": label}})
        else:
            record[f"{label}_outside_repo"] = True
    if require_absent:
        exists = [label for label, t in (("keystore", keystore), ("properties", properties)) if t.exists()]
        record["targets_absent"] = not exists
        for label in exists:
            failures.append({"code": "target_exists", "detail": {"target": label}})
    else:
        record["targets_absent"] = None
    return record, failures


def _plan_block(keystore: Path, properties: Path, alias: str, validity_days: int) -> dict:
    return {
        "keystore_type": KEYSTORE_TYPE,
        "keyalg": REQUIRED_KEY_ALG,
        "keysize": MIN_KEY_SIZE,
        "validity_days": validity_days,
        "alias": alias,
        "dname": KEYTOOL_DNAME,
        "keystore_basename": keystore.name,
        "properties_basename": properties.name,
        "permission_scheme": "windows_acl_icacls" if os.name == "nt" else "posix_0600",
    }


def alias_forbidden(alias: str) -> bool:
    lowered = alias.strip().lower()
    tokens = [token for token in re.split(r"[^a-z0-9]+", lowered) if token]
    return any(token in FORBIDDEN_ALIASES for token in tokens)


# ---------------------------------------------------------------------------
# subcommand: plan
# ---------------------------------------------------------------------------


def run_plan(
    keystore: str,
    properties: str,
    alias: str = DEFAULT_ALIAS,
    validity_days: int = MIN_VALIDITY_DAYS,
    repo_root_finder: Optional[RepoRootFinder] = None,
) -> Tuple[dict, int]:
    """Read-only preview; creates nothing, runs no keytool."""
    finder = repo_root_finder if repo_root_finder is not None else default_repo_root
    ks_path = Path(keystore).expanduser()
    props_path = Path(properties).expanduser()
    failures: List[dict] = []
    if alias_forbidden(alias):
        failures.append({"code": "alias_forbidden"})
    if validity_days < MIN_VALIDITY_DAYS:
        failures.append({"code": "validity_below_minimum"})
    targets_record, target_failures = validate_targets(
        ks_path, props_path, finder(), require_absent=True
    )
    failures += target_failures
    status = STATUS_READY if not failures else STATUS_BLOCKED
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "mode": "plan",
        "status": status,
        "exit_code": EXIT_OK if not failures else EXIT_FAILURE,
        "plan": _plan_block(ks_path, props_path, alias, validity_days),
        "checks": targets_record,
        "failures": _sorted_failures(failures),
    }
    return result, result["exit_code"]


# ---------------------------------------------------------------------------
# subcommand: execute
# ---------------------------------------------------------------------------


def _resolve_keytool(explicit: Optional[str]) -> Optional[Path]:
    if explicit is not None:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_file() else None
    found = shutil.which("keytool")
    return Path(found) if found else None


def _escape_properties_value(value: str) -> str:
    """java.util.Properties escaping for the bytes we write."""
    return (
        value.replace("\\", "\\\\").replace(":", "\\:").replace("=", "\\=")
    )


def _properties_bytes(keystore: Path, password: str, alias: str) -> bytes:
    """One password value fills both password keys (PKCS12 contract)."""
    lines = [
        f"keystore.path={_escape_properties_value(str(keystore.resolve()))}",
        f"keystore.storePassword={_escape_properties_value(password)}",
        f"keystore.keyAlias={_escape_properties_value(alias)}",
        f"keystore.keyPassword={_escape_properties_value(password)}",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _plain_regular_file(path: Path) -> bool:
    """A regular file that is not itself a link/junction/reparse point."""
    return path.is_file() and not _link_detector(path)


def _cleanup_new_files(paths: Sequence[Path]) -> None:
    """Remove only files created by this run; never touch pre-existing ones."""
    for path in paths:
        try:
            if _plain_regular_file(path):
                path.unlink()
        except OSError:
            pass


def run_execute(
    keystore: str,
    properties: str,
    confirm: Optional[str],
    alias: str = DEFAULT_ALIAS,
    validity_days: int = MIN_VALIDITY_DAYS,
    keytool: Optional[str] = None,
    runner: Optional[Callable] = None,
    entropy: Optional[Entropy] = None,
    permission_applier: Optional[PermissionApplier] = None,
    repo_root_finder: Optional[RepoRootFinder] = None,
    clock: Optional[Clock] = None,
) -> Tuple[dict, int]:
    """Generate the signing material; every rejected input fails closed."""
    run = runner if runner is not None else default_runner
    gen = entropy if entropy is not None else default_entropy
    apply_perm = (
        permission_applier
        if permission_applier is not None
        else default_permission_applier
    )
    finder = repo_root_finder if repo_root_finder is not None else default_repo_root
    now = clock if clock is not None else default_clock

    ks_path = Path(keystore).expanduser()
    props_path = Path(properties).expanduser()
    failures: List[dict] = []
    checks: Dict[str, dict] = {}
    created: List[Path] = []

    if confirm is None:
        failures.append({"code": "confirmation_missing"})
    elif confirm != CONFIRM_PHRASE:
        failures.append({"code": "confirmation_mismatch"})
    if alias_forbidden(alias):
        failures.append({"code": "alias_forbidden"})
    if validity_days < MIN_VALIDITY_DAYS:
        failures.append({"code": "validity_below_minimum"})
    targets_record, target_failures = validate_targets(
        ks_path, props_path, finder(), require_absent=True
    )
    checks["targets"] = targets_record  # type: ignore[assignment]
    failures += target_failures
    if failures:
        return _execute_result(checks, failures, ks_path, props_path)

    tool_path = _resolve_keytool(keytool)
    if tool_path is None:
        failures.append({"code": "tool_missing", "detail": {"tool": "keytool"}})
        return _execute_result(checks, failures, ks_path, props_path)

    # ONE OS-random password for the store and the key: PKCS12 keys are
    # read with the store password by Android tooling, so a diverging key
    # password breaks packageRelease (operator-confirmed M14-175 round 2).
    password = gen(PASSWORD_LENGTH)
    gen_record: Dict[str, object] = {"exit_code": None, "ok": False}
    argv = [
        str(tool_path),
        "-genkeypair",
        "-keystore", str(ks_path),
        "-storetype", KEYSTORE_TYPE,
        "-alias", alias,
        "-keyalg", REQUIRED_KEY_ALG,
        "-keysize", str(MIN_KEY_SIZE),
        "-validity", str(validity_days),
        "-storepass", password,
        "-keypass", password,
        "-dname", KEYTOOL_DNAME,
    ]
    try:
        outcome = run(argv, keytool_env())
    except OSError:
        failures.append({"code": "tool_spawn_failed", "detail": {"tool": "keytool"}})
        return _execute_result(checks, failures, ks_path, props_path)
    gen_record["exit_code"] = outcome.returncode
    if (
        outcome.returncode != EXIT_OK
        or not _plain_regular_file(ks_path)
    ):
        failures.append({"code": "keytool_genkeypair_failed"})
        gen_record["ok"] = False
        checks["keytool_genpair"] = gen_record  # type: ignore[assignment]
        _cleanup_new_files([ks_path])
        return _execute_result(checks, failures, ks_path, props_path)
    gen_record["ok"] = True
    checks["keytool_genpair"] = gen_record  # type: ignore[assignment]
    created.append(ks_path)

    perm_record = _apply_and_record(apply_perm, ks_path, "keystore")
    checks["permissions"] = perm_record  # type: ignore[assignment]
    if not perm_record["keystore_applied"]:
        failures.append({"code": perm_record["keystore_error"] or "permission_apply_failed"})
        _cleanup_new_files(created)
        return _execute_result(checks, failures, ks_path, props_path)

    try:
        props_bytes = _properties_bytes(ks_path, password, alias)
        with open(props_path, "xb") as stream:
            stream.write(props_bytes)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        failures.append({"code": "properties_write_failed"})
        _cleanup_new_files(created)
        return _execute_result(checks, failures, ks_path, props_path)
    created.append(props_path)

    props_perm = _apply_and_record(apply_perm, props_path, "properties")
    checks["permissions"]["properties_applied"] = props_perm["properties_applied"]
    checks["permissions"]["properties_error"] = props_perm["properties_error"]
    if not props_perm["properties_applied"]:
        failures.append({"code": props_perm["properties_error"] or "permission_apply_failed"})
        _cleanup_new_files(created)
        return _execute_result(checks, failures, ks_path, props_path)

    cert_record, cert_failures = _inspect_certificate(
        tool_path, ks_path, password, alias, run, now()
    )
    checks["certificate"] = cert_record  # type: ignore[assignment]
    if cert_failures:
        failures += cert_failures
        _cleanup_new_files(created)
        return _execute_result(checks, failures, ks_path, props_path)

    return _execute_result(checks, failures, ks_path, props_path)


def _apply_and_record(
    apply_perm: PermissionApplier, path: Path, label: str
) -> Dict[str, object]:
    applied, error = apply_perm(path)
    return {f"{label}_applied": applied, f"{label}_error": error}


def _inspect_certificate(
    tool_path: Path,
    ks_path: Path,
    store_pw: str,
    alias: str,
    run: Callable,
    now: datetime.datetime,
) -> Tuple[Dict[str, object], List[dict]]:
    """keytool -list inspection; returns (record, failures) value-free."""
    record: Dict[str, object] = {
        "sha256_fingerprint": None,
        "alias_present": False,
        "validity": {"state": "not_evaluated", "failure": None},
    }
    failures: List[dict] = []
    argv = [
        str(tool_path), "-list", "-v",
        "-keystore", str(ks_path), "-storepass", store_pw,
    ]
    try:
        outcome = run(argv, keytool_env())
    except OSError:
        failures.append({"code": "tool_spawn_failed", "detail": {"tool": "keytool"}})
        return record, failures
    if outcome.returncode != EXIT_OK:
        failures.append({"code": "keytool_list_failed"})
        return record, failures
    facts = parse_keytool_list(outcome.stdout)
    if facts["sha256_fingerprint"] is None:
        failures.append({"code": "fingerprint_missing"})
        return record, failures
    record["sha256_fingerprint"] = facts["sha256_fingerprint"]
    record["alias_present"] = facts["alias"] == alias
    if not record["alias_present"]:
        failures.append({"code": "alias_mismatch"})
    window = validity_state(facts, now)
    record["validity"] = window
    if window["failure"]:
        failures.append({"code": window["failure"]})
    return record, failures


def _execute_result(
    checks: Dict[str, dict],
    failures: List[dict],
    ks_path: Path,
    props_path: Path,
) -> Tuple[dict, int]:
    status = STATUS_GENERATED if not failures else STATUS_FAILED
    exit_code = EXIT_OK if not failures else EXIT_FAILURE
    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "mode": "execute",
        "status": status,
        "exit_code": exit_code,
        "artifacts": (
            {
                "keystore_basename": ks_path.name,
                "properties_basename": props_path.name,
            }
            if status == STATUS_GENERATED
            else None
        ),
        "checks": checks,
        "failures": _sorted_failures(failures),
    }
    return result, exit_code


# ---------------------------------------------------------------------------
# subcommand: verify
# ---------------------------------------------------------------------------


def _parse_properties_keys(path: Path) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
    """Line-wise java.util.Properties read (keys + raw values, in memory)."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None, "properties_unreadable"
    values: Dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        if "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        values[key.strip()] = _unescape_properties_value(raw.strip())
    return values, None


def _unescape_properties_value(value: str) -> str:
    out: List[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            out.append(value[index + 1])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def run_verify_material(
    keystore: str,
    properties: str,
    alias: str = DEFAULT_ALIAS,
    keytool: Optional[str] = None,
    runner: Optional[Callable] = None,
    permission_checker: Optional[PermissionChecker] = None,
    repo_root_finder: Optional[RepoRootFinder] = None,
    clock: Optional[Clock] = None,
) -> Tuple[dict, int]:
    """Re-check existing material without modifying anything."""
    run = runner if runner is not None else default_runner
    check_perm = (
        permission_checker
        if permission_checker is not None
        else default_permission_checker
    )
    finder = repo_root_finder if repo_root_finder is not None else default_repo_root
    now = clock if clock is not None else default_clock

    ks_path = Path(keystore).expanduser()
    props_path = Path(properties).expanduser()
    failures: List[dict] = []
    checks: Dict[str, dict] = {}

    targets_record, target_failures = validate_targets(
        ks_path, props_path, finder(), require_absent=False
    )
    checks["targets"] = targets_record  # type: ignore[assignment]
    failures += target_failures

    keystore_state = "ok"
    if not ks_path.exists():
        keystore_state = "missing"
        failures.append({"code": "keystore_missing"})
    elif not _plain_regular_file(ks_path):
        keystore_state = "not_regular"
        failures.append({"code": "keystore_not_regular"})
    props_state = "ok"
    if not props_path.exists():
        props_state = "missing"
        failures.append({"code": "properties_missing"})
    elif not _plain_regular_file(props_path):
        props_state = "not_regular"
        failures.append({"code": "properties_not_regular"})

    values: Dict[str, str] = {}
    if props_state == "ok":
        parsed, error = _parse_properties_keys(props_path)
        if error is not None:
            props_state = "unreadable"
            failures.append({"code": error})
        else:
            assert parsed is not None
            values = parsed
            missing = [key for key in PROPERTIES_KEYS if not values.get(key)]
            checks["properties_keys"] = {"all_present": not missing}  # type: ignore[assignment]
            if missing:
                props_state = "incomplete"
                failures.append({"code": "properties_incomplete"})
            else:
                # PKCS12 contract: the key is read with the store password,
                # so diverging passwords fail closed (value-free report).
                equal = (
                    values["keystore.storePassword"]
                    == values["keystore.keyPassword"]
                )
                checks["properties_keys"]["store_key_passwords_equal"] = equal  # type: ignore[index]
                if not equal:
                    failures.append({"code": "store_key_password_mismatch"})
                declared = Path(values["keystore.path"])
                if Path(os.path.realpath(declared)) != Path(os.path.realpath(ks_path)):
                    props_state = "keystore_path_mismatch"
                    failures.append({"code": "keystore_path_mismatch"})

    perm_record: Dict[str, object] = {}
    if keystore_state == "ok":
        state, code = check_perm(ks_path)
        perm_record["keystore"] = state
        if code:
            failures.append({"code": code, "detail": {"target": "keystore"}})
    if props_state == "ok":
        state, code = check_perm(props_path)
        perm_record["properties"] = state
        if code:
            failures.append({"code": code, "detail": {"target": "properties"}})
    checks["permissions"] = perm_record  # type: ignore[assignment]

    if keystore_state == "ok" and props_state == "ok" and values.get(
        "keystore.storePassword"
    ):
        tool_path = _resolve_keytool(keytool)
        if tool_path is None:
            failures.append({"code": "tool_missing", "detail": {"tool": "keytool"}})
        else:
            cert_record, cert_failures = _inspect_certificate(
                tool_path,
                ks_path,
                values["keystore.storePassword"],
                values.get("keystore.keyAlias", alias),
                run,
                now(),
            )
            checks["certificate"] = cert_record  # type: ignore[assignment]
            failures += cert_failures
    else:
        checks["certificate"] = {  # type: ignore[assignment]
            "sha256_fingerprint": None,
            "alias_present": None,
            "validity": {"state": "not_evaluated", "failure": None},
        }

    result = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "mode": "verify",
        "status": STATUS_VERIFIED if not failures else STATUS_FAILED,
        "exit_code": EXIT_OK if not failures else EXIT_FAILURE,
        "checks": checks,
        "failures": _sorted_failures(failures),
    }
    return result, result["exit_code"]


# ---------------------------------------------------------------------------
# shared rendering / CLI
# ---------------------------------------------------------------------------


def _sorted_failures(failures: List[dict]) -> List[dict]:
    return sorted(failures, key=lambda item: json.dumps(item, sort_keys=True))


def render_json(result: dict) -> str:
    """Deterministic serialization (sorted keys, ASCII-safe, no timestamps)."""
    return json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True)


def render_summary(result: dict) -> str:
    """One-line human summary: statuses and failure codes only."""
    parts = [
        f"{TOOL_NAME}: mode={result['mode']}",
        f"status={result['status']}",
        f"exit={result['exit_code']}",
    ]
    codes = ",".join(item["code"] for item in result["failures"])
    if codes:
        parts.append(f"failures={codes}")
    return " ".join(parts)


def _validity_days(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if parsed < MIN_VALIDITY_DAYS:
        raise argparse.ArgumentTypeError(
            f"must be at least {MIN_VALIDITY_DAYS} days"
        )
    return parsed


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="material_bootstrapper",
        description=(
            "Android release material bootstrapper (readiness tool): plan, "
            "generate (outside the repo, OS-random passwords, never echoed), "
            "and verify the externally managed release keystore + signing "
            "properties."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan_p = sub.add_parser("plan", help="Read-only preview of the generation plan.")
    _add_target_args(plan_p)
    _add_key_args(plan_p)

    exec_p = sub.add_parser(
        "execute", help="Generate keystore + properties (explicit confirmation)."
    )
    _add_target_args(exec_p)
    _add_key_args(exec_p)
    exec_p.add_argument(
        "--confirm", default=None,
        help=f"Required confirmation phrase: {CONFIRM_PHRASE}",
    )
    exec_p.add_argument(
        "--keytool", default=None,
        help="Explicit keytool path (default: PATH lookup).",
    )

    ver_p = sub.add_parser("verify", help="Re-check existing material.")
    _add_target_args(ver_p)
    ver_p.add_argument("--alias", default=DEFAULT_ALIAS, help="Expected alias.")
    ver_p.add_argument(
        "--keytool", default=None,
        help="Explicit keytool path (default: PATH lookup).",
    )
    return parser.parse_args(argv)


def _add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--keystore", required=True,
        help="Keystore output path (must be outside the repository, absent).",
    )
    parser.add_argument(
        "--properties", required=True,
        help="Signing properties output path (outside the repository, absent).",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress stderr summary.")


def _add_key_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--alias", default=DEFAULT_ALIAS, help="Key alias.")
    parser.add_argument(
        "--validity-days", type=_validity_days, default=MIN_VALIDITY_DAYS,
        help=f"Validity in days (minimum {MIN_VALIDITY_DAYS}).",
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.command == "plan":
        result, exit_code = run_plan(
            keystore=args.keystore,
            properties=args.properties,
            alias=args.alias,
            validity_days=args.validity_days,
        )
    elif args.command == "execute":
        result, exit_code = run_execute(
            keystore=args.keystore,
            properties=args.properties,
            confirm=args.confirm,
            alias=args.alias,
            validity_days=args.validity_days,
            keytool=args.keytool,
        )
    else:
        result, exit_code = run_verify_material(
            keystore=args.keystore,
            properties=args.properties,
            alias=args.alias,
            keytool=args.keytool,
        )
    print(render_json(result))
    if not getattr(args, "quiet", False):
        print(render_summary(result), file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
