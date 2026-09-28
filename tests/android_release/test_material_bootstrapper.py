"""Tests for tools.android_release.material_bootstrapper (M14-174 readiness).

Every test injects a fake keytool runner, a fake password entropy source, a
fake permission applier, a fake clock, and a fake repo-root finder: keytool /
icacls are never executed for real, no OS randomness is consumed, no network
is touched, and no real keystore or secret is ever created or read. Target
paths live inside pytest's tmp_path, outside the injected fake repository
root, mirroring the real outside-repo contract.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tools.android_release.material_bootstrapper import (
    CONFIRM_PHRASE,
    DEFAULT_ALIAS,
    FORBIDDEN_ALIASES,
    KEYSTORE_TYPE,
    KEYTOOL_DNAME,
    KEYTOOL_JAVA_TOOL_OPTIONS,
    MIN_KEY_SIZE,
    MIN_VALIDITY_DAYS,
    PASSWORD_LENGTH,
    PROPERTIES_KEYS,
    REQUIRED_KEY_ALG,
    SCHEMA_VERSION,
    TOOL_NAME,
    keytool_env,
    main,
    parse_keytool_list,
    run_execute,
    run_plan,
    run_verify_material,
)
from tools.android_release.verify_artifact import CommandOutcome

# Marker that must never reach any rendered JSON, whatever happens.
PASSWORD_MARKER = "GENERATED-PASSWORD-VALUE-MUST-NEVER-APPEAR"
ABS_PATH_MARKER = "abs-path-marker-must-never-appear"
CHILD_STDOUT_MARKER = "CHILD-STDOUT-MARKER-MUST-NEVER-APPEAR"
CHILD_STDERR_MARKER = "CHILD-STDERR-MARKER-MUST-NEVER-APPEAR"

KEYSTORE_NAME = "aios-release.keystore"
PROPERTIES_NAME = "aios-android-signing.properties"

FAKE_FINGERPRINT_COLONS = ":".join(["A1"] * 32)
FAKE_FINGERPRINT_HEX = "a1" * 32

VALID_FROM = "Mon Sep 28 21:00:00 CST 2026"
VALID_UNTIL = "Sun Feb 14 21:00:00 CST 2054"
NOW_IN_WINDOW = datetime(2030, 6, 1, 12, 0, 0)
NOW_BEFORE = datetime(2020, 6, 1, 12, 0, 0)
NOW_AFTER = datetime(2060, 6, 1, 12, 0, 0)

FIXED_WINDOW_LIST = (
    "Keystore type: PKCS12\n"
    "Your keystore contains 1 entry\n"
    "Alias name: aios-release\n"
    f"Owner: {KEYTOOL_DNAME}\n"
    f"SHA256: {FAKE_FINGERPRINT_COLONS}\n"
    f"Valid from: {VALID_FROM} until: {VALID_UNTIL}\n"
)

# Real Windows JDK 17 ``keytool -list -v`` shape (English, forced via
# JAVA_TOOL_OPTIONS): labels at column 0, fingerprint lines indented by a
# leading tab, SHA1 printed before SHA256.
JDK17_ENGLISH_LIST = (
    "Keystore type: PKCS12\n"
    "Keystore provider: SUN\n"
    "\n"
    "Your keystore contains 1 entry\n"
    "\n"
    "Alias name: aios-release\n"
    "Creation date: Sep 28, 2026\n"
    "Entry type: PrivateKeyEntry\n"
    "Certificate chain length: 1\n"
    "Certificate[1]:\n"
    f"Owner: {KEYTOOL_DNAME}\n"
    f"Issuer: {KEYTOOL_DNAME}\n"
    "Serial number: 6a3f9c1e\n"
    f"Valid from: {VALID_FROM} until: {VALID_UNTIL}\n"
    "\n"
    "Certificate fingerprints:\n"
    "\tSHA1: " + ":".join(["BB"] * 20) + "\n"
    f"\tSHA256: {FAKE_FINGERPRINT_COLONS}\n"
)


def _java_date(moment: datetime) -> str:
    """keytool-style date text (fixed CST label, format %a %b %d %H:%M:%S)."""
    return moment.strftime("%a %b %d %H:%M:%S") + " CST " + moment.strftime(
        "%Y"
    )


def dynamic_window_list() -> str:
    """A -list output whose window always brackets "now" (CI-proof)."""
    now = datetime.now()
    start = now - timedelta(days=1)
    end = now + timedelta(days=10001)
    return (
        "Alias name: aios-release\n"
        f"SHA256: {FAKE_FINGERPRINT_COLONS}\n"
        f"Valid from: {_java_date(start)} until: {_java_date(end)}\n"
    )


def fake_repo(tmp_path: Path) -> Path:
    """A plausible repository root (has .git) outside the target paths."""
    repo = tmp_path / "repo-root"
    repo.mkdir(exist_ok=True)
    (repo / ".git").write_text("gitdir: elsewhere\n", encoding="ascii")
    return repo


def make_targets(tmp_path: Path) -> tuple[Path, Path]:
    material_dir = tmp_path / "material"
    material_dir.mkdir(exist_ok=True)
    return material_dir / KEYSTORE_NAME, material_dir / PROPERTIES_NAME


class FakeKeytool:
    """Records every call; -genkeypair "creates" the keystore file."""

    def __init__(self, keystore: Path, gen_returncode: int = 0,
                 list_returncode: int = 0, list_stdout: str | None = None):
        self.keystore = keystore
        self.gen_returncode = gen_returncode
        self.list_returncode = list_returncode
        # default: a window that always brackets the real current time, so
        # execute/verify pass paths never rot as the wall clock advances
        self.list_stdout = (
            dynamic_window_list() if list_stdout is None else list_stdout
        )
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []

    def __call__(self, argv, env):
        self.calls.append(list(argv))
        self.envs.append(dict(env))
        if "-genkeypair" in argv:
            if self.gen_returncode == 0:
                self.keystore.write_bytes(b"fake-keystore-bytes")
            return CommandOutcome(self.gen_returncode, "", "")
        if "-list" in argv:
            return CommandOutcome(self.list_returncode, self.list_stdout, "")
        raise AssertionError(f"unexpected keytool argv: {argv}")


class FakeEntropy:
    """Deterministic password source; values must stay out of all output."""

    def __init__(self):
        self.lengths: list[int] = []

    def __call__(self, length: int) -> str:
        self.lengths.append(length)
        return f"{PASSWORD_MARKER}-{length}-{len(self.lengths)}"


class FakePermissions:
    """Records applied paths; simulates success or failure per path."""

    def __init__(self, fail_on: str | None = None):
        self.applied: list[Path] = []
        self.fail_on = fail_on

    def __call__(self, path: Path):
        if self.fail_on is not None and self.fail_on in str(path):
            return False, "permission_apply_failed"
        self.applied.append(path)
        return True, None


# ---------------------------------------------------------------------------
# shared argument helpers
# ---------------------------------------------------------------------------


def plan_args(keystore: Path, properties: Path) -> list[str]:
    return [
        "plan",
        "--keystore", str(keystore),
        "--properties", str(properties),
    ]


def execute_args(keystore: Path, properties: Path,
                 confirm: str | None = CONFIRM_PHRASE) -> list[str]:
    # a keytool stub file: explicit tool paths must name a real file
    stub = keystore.parent / "keytool-stub"
    stub.touch(exist_ok=True)
    args = [
        "execute",
        "--keystore", str(keystore),
        "--properties", str(properties),
        "--keytool", str(stub),
    ]
    if confirm is not None:
        args += ["--confirm", confirm]
    return args


def verify_args(keystore: Path, properties: Path) -> list[str]:
    stub = keystore.parent / "keytool-stub"
    stub.touch(exist_ok=True)
    return [
        "verify",
        "--keystore", str(keystore),
        "--properties", str(properties),
        "--keytool", str(stub),
    ]


def inject_common(monkeypatch, tmp_path, *, gen_rc=0, list_rc=0,
                  list_stdout=None, fail_permissions_on=None):
    keystore, properties = make_targets(tmp_path)
    repo = fake_repo(tmp_path)
    runner = FakeKeytool(keystore, gen_returncode=gen_rc,
                         list_returncode=list_rc, list_stdout=list_stdout)
    entropy = FakeEntropy()
    permissions = FakePermissions(fail_on=fail_permissions_on)
    monkeypatch.setattr(
        "tools.android_release.material_bootstrapper.default_runner", runner
    )
    monkeypatch.setattr(
        "tools.android_release.material_bootstrapper.default_entropy", entropy
    )
    monkeypatch.setattr(
        "tools.android_release.material_bootstrapper.default_permission_applier",
        permissions,
    )
    monkeypatch.setattr(
        "tools.android_release.material_bootstrapper.default_permission_checker",
        lambda _path: ("ok", None),
    )
    monkeypatch.setattr(
        "tools.android_release.material_bootstrapper.default_repo_root",
        lambda: repo,
    )
    return keystore, properties, runner, entropy, permissions


def keytool_stub(keystore: Path) -> Path:
    """A real file the explicit --keytool check accepts (never executed)."""
    stub = keystore.parent / "keytool-stub"
    stub.touch(exist_ok=True)
    return stub


def make_junction(link: Path, target: Path) -> bool:
    """Create a real Windows directory junction; False when unavailable.

    Output is captured as bytes (localized cmd output breaks utf-8 decode).
    No network, no elevation: ``mklink /J`` needs no symlink privilege.
    """
    if os.name != "nt" or link.exists():
        return False
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
    )
    return proc.returncode == 0 and link.exists()


def write_valid_material(keystore: Path, properties: Path,
                         keystore_path_value: str | None = None,
                         store_password: str = "stored-value",
                         key_password: str = "stored-value") -> None:
    keystore.write_bytes(b"fake-keystore-bytes")
    properties.write_text(
        "keystore.path="
        + (keystore_path_value or str(keystore).replace("\\", "\\\\"))
        + f"\nkeystore.storePassword={store_password}\n"
        + "keystore.keyAlias=aios-release\n"
        + f"keystore.keyPassword={key_password}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


class TestPlan:
    def test_plan_reports_ready_without_touching_fs_or_keytool(
        self, tmp_path, capsys, monkeypatch
    ):
        keystore, properties = make_targets(tmp_path)
        repo = fake_repo(tmp_path)
        monkeypatch.setattr(
            "tools.android_release.material_bootstrapper.default_repo_root",
            lambda: repo,
        )
        code = main(plan_args(keystore, properties))
        result = json.loads(capsys.readouterr().out)
        assert code == 0
        assert result["status"] == "ready"
        assert result["mode"] == "plan"
        assert result["tool"] == TOOL_NAME
        assert result["schema_version"] == SCHEMA_VERSION
        plan = result["plan"]
        assert plan["keystore_type"] == KEYSTORE_TYPE
        assert plan["keyalg"] == REQUIRED_KEY_ALG
        assert plan["keysize"] == MIN_KEY_SIZE
        assert plan["validity_days"] == MIN_VALIDITY_DAYS
        assert plan["alias"] == DEFAULT_ALIAS
        assert plan["dname"] == KEYTOOL_DNAME
        assert plan["keystore_basename"] == KEYSTORE_NAME
        assert plan["properties_basename"] == PROPERTIES_NAME
        assert result["checks"]["keystore_outside_repo"] is True
        assert result["checks"]["properties_outside_repo"] is True
        assert result["checks"]["keystore_symlink_free"] is True
        assert result["checks"]["properties_symlink_free"] is True
        assert result["checks"]["targets_absent"] is True
        # plan is read-only: nothing generated, no keytool run
        assert not keystore.exists()
        assert not properties.exists()

    def test_plan_blocked_when_target_already_exists(self, tmp_path, capsys):
        keystore, properties = make_targets(tmp_path)
        repo = fake_repo(tmp_path)
        keystore.write_bytes(b"pre-existing")
        result, code = run_plan(
            str(keystore), str(properties),
            repo_root_finder=lambda: repo,
        )
        assert code == 1
        assert result["status"] == "blocked"
        codes = [f["code"] for f in result["failures"]]
        assert "target_exists" in codes
        # pre-existing file untouched
        assert keystore.read_bytes() == b"pre-existing"

    def test_plan_blocked_inside_repo(self, tmp_path):
        _keystore, properties = make_targets(tmp_path)
        repo = fake_repo(tmp_path)
        inside = repo / "secrets" / KEYSTORE_NAME
        inside.parent.mkdir()
        result, code = run_plan(
            str(inside), str(properties), repo_root_finder=lambda: repo
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "target_inside_repo" in codes

    def test_plan_blocked_when_repo_root_unknown(self, tmp_path):
        keystore, properties = make_targets(tmp_path)
        result, code = run_plan(
            str(keystore), str(properties), repo_root_finder=lambda: None
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "repo_root_not_found" in codes


# ---------------------------------------------------------------------------
# execute: confirmation gate and target validation
# ---------------------------------------------------------------------------


class TestExecuteGates:
    def test_execute_rejects_missing_confirmation(self, tmp_path, monkeypatch):
        keystore, properties, runner, _entropy, _permissions = inject_common(
            monkeypatch, tmp_path
        )
        result, code = run_execute(
            str(keystore), str(properties), confirm=None,
        )
        assert code == 1
        assert result["status"] == "failed"
        codes = [f["code"] for f in result["failures"]]
        assert "confirmation_missing" in codes
        assert runner.calls == []          # keytool never invoked
        assert not keystore.exists()
        assert not properties.exists()

    def test_execute_rejects_wrong_confirmation(self, tmp_path, monkeypatch):
        keystore, properties, runner, _, _ = inject_common(monkeypatch, tmp_path)
        result, code = run_execute(
            str(keystore), str(properties), confirm="yes"
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "confirmation_mismatch" in codes
        assert runner.calls == []

    def test_execute_rejects_debug_alias(self, tmp_path, monkeypatch):
        keystore, properties, runner, _, _ = inject_common(monkeypatch, tmp_path)
        for alias in sorted(FORBIDDEN_ALIASES):
            result, code = run_execute(
                str(keystore), str(properties),
                confirm=CONFIRM_PHRASE, alias=alias,
            )
            assert code == 1
            codes = [f["code"] for f in result["failures"]]
            assert "alias_forbidden" in codes
            assert runner.calls == []
            assert not keystore.exists()

    def test_execute_rejects_short_validity(self, tmp_path, monkeypatch):
        keystore, properties, runner, _, _ = inject_common(monkeypatch, tmp_path)
        result, code = run_execute(
            str(keystore), str(properties),
            confirm=CONFIRM_PHRASE, validity_days=MIN_VALIDITY_DAYS - 1,
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "validity_below_minimum" in codes
        assert runner.calls == []

    def test_execute_rejects_existing_target_without_keytool(
        self, tmp_path, monkeypatch
    ):
        keystore, properties, runner, _, _ = inject_common(monkeypatch, tmp_path)
        keystore.write_bytes(b"pre-existing")
        result, code = run_execute(
            str(keystore), str(properties), confirm=CONFIRM_PHRASE
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "target_exists" in codes
        assert runner.calls == []
        assert keystore.read_bytes() == b"pre-existing"

    def test_execute_rejects_target_inside_repo(self, tmp_path, monkeypatch):
        _keystore, properties, runner, _, _ = inject_common(monkeypatch, tmp_path)
        repo = fake_repo(tmp_path)
        inside = repo / KEYSTORE_NAME
        result, code = run_execute(
            str(inside), str(properties), confirm=CONFIRM_PHRASE
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "target_inside_repo" in codes
        assert runner.calls == []
        assert not inside.exists()

    @pytest.mark.skipif(
        os.name == "nt", reason="symlink privileges vary on Windows"
    )
    def test_execute_rejects_symlinked_parent(self, tmp_path, monkeypatch):
        _keystore, properties, runner, _, _ = inject_common(monkeypatch, tmp_path)
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link_dir = tmp_path / "link"
        os.symlink(real_dir, link_dir, target_is_directory=True)
        target = link_dir / KEYSTORE_NAME
        result, code = run_execute(
            str(target), str(properties), confirm=CONFIRM_PHRASE
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "target_symlink" in codes
        assert runner.calls == []

    def test_execute_rejects_junctioned_parent_windows(self, tmp_path,
                                                       monkeypatch):
        """Real directory junction: is_symlink() is False for junctions on
        Python 3.11 — the reparse-aware detector must still fail closed."""
        _keystore, properties, runner, _, _ = inject_common(monkeypatch, tmp_path)
        real_dir = tmp_path / "junction-real"
        real_dir.mkdir()
        link_dir = tmp_path / "junction-link"
        if not make_junction(link_dir, real_dir):
            pytest.skip("junction creation unavailable on this host")
        target = link_dir / KEYSTORE_NAME
        result, code = run_execute(
            str(target), str(properties), confirm=CONFIRM_PHRASE
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "target_symlink" in codes
        assert runner.calls == []          # keytool never invoked
        assert not (real_dir / KEYSTORE_NAME).exists()

    def test_plan_blocked_on_junctioned_parent_windows(self, tmp_path):
        _keystore, properties = make_targets(tmp_path)
        repo = fake_repo(tmp_path)
        real_dir = tmp_path / "jr"
        real_dir.mkdir()
        link_dir = tmp_path / "jl"
        if not make_junction(link_dir, real_dir):
            pytest.skip("junction creation unavailable on this host")
        result, code = run_plan(
            str(link_dir / KEYSTORE_NAME), str(properties),
            repo_root_finder=lambda: repo,
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "target_symlink" in codes
        assert result["checks"]["keystore_symlink_free"] is False

    def test_execute_rejects_component_flagged_by_detector_seam(
        self, tmp_path, monkeypatch
    ):
        """Platform-independent wiring proof: any existing component the
        detector flags (junction or otherwise) fails closed before keytool."""
        _keystore, properties, runner, _, _ = inject_common(monkeypatch, tmp_path)
        keystore, _props = make_targets(tmp_path)

        def flag_material_dir(path):
            return Path(path).name == "material"

        monkeypatch.setattr(
            "tools.android_release.material_bootstrapper._link_detector",
            flag_material_dir,
        )
        result, code = run_execute(
            str(keystore), str(properties), confirm=CONFIRM_PHRASE
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "target_symlink" in codes
        assert runner.calls == []


# ---------------------------------------------------------------------------
# execute: success path
# ---------------------------------------------------------------------------


class TestExecuteSuccess:
    def test_execute_generates_material_and_reports_metadata(
        self, tmp_path, monkeypatch
    ):
        keystore, properties, runner, _entropy, permissions = inject_common(
            monkeypatch, tmp_path
        )
        result, code = run_execute(
            str(keystore), str(properties), confirm=CONFIRM_PHRASE
        )
        assert code == 0
        assert result["status"] == "generated"
        assert keystore.is_file()
        assert properties.is_file()
        # keytool called once to generate, once to list
        assert len(runner.calls) == 2
        gen_argv = runner.calls[0]
        assert "-genkeypair" in gen_argv
        assert "-keyalg" in gen_argv
        idx = gen_argv.index("-keyalg")
        assert gen_argv[idx + 1] == REQUIRED_KEY_ALG
        idx = gen_argv.index("-keysize")
        assert int(gen_argv[idx + 1]) >= MIN_KEY_SIZE
        idx = gen_argv.index("-validity")
        assert int(gen_argv[idx + 1]) >= MIN_VALIDITY_DAYS
        idx = gen_argv.index("-dname")
        assert gen_argv[idx + 1] == KEYTOOL_DNAME
        # properties carry exactly the four consumable keys
        content = properties.read_text(encoding="utf-8")
        assert "keystore.path=" in content
        assert "keystore.storePassword=" in content
        assert "keystore.keyAlias=" in content
        assert "keystore.keyPassword=" in content
        assert content.count("\n") == 4
        # permissions tightened on both files
        assert permissions.applied == [keystore, properties]
        # certificate metadata reported, value-free
        cert = result["checks"]["certificate"]
        assert cert["sha256_fingerprint"] == FAKE_FINGERPRINT_HEX
        assert cert["alias_present"] is True

    def test_execute_passwords_come_from_entropy_not_echoed(
        self, tmp_path, monkeypatch, capsys
    ):
        keystore, properties, _runner, entropy, _ = inject_common(
            monkeypatch, tmp_path
        )
        code = main(execute_args(keystore, properties))
        assert code == 0
        rendered = capsys.readouterr().out
        assert PASSWORD_MARKER not in rendered
        assert ABS_PATH_MARKER not in rendered
        result = json.loads(rendered)
        rendered_again = json.dumps(result, sort_keys=True)
        assert PASSWORD_MARKER not in rendered_again
        # ONE password requested per run (store == key, PKCS12 contract)
        assert entropy.lengths == [PASSWORD_LENGTH]

    def test_execute_output_has_no_absolute_paths(self, tmp_path, monkeypatch,
                                                  capsys):
        keystore, properties, *_ = inject_common(monkeypatch, tmp_path)
        code = main(execute_args(keystore, properties))
        assert code == 0
        rendered = capsys.readouterr().out
        assert str(tmp_path) not in rendered
        assert str(keystore.parent) not in rendered
        assert keystore.name in rendered

    @pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits")
    def test_execute_posix_permissions_0600(self, tmp_path, monkeypatch):
        from tools.android_release.material_bootstrapper import (
            posix_permission_applier,
        )
        keystore, properties, *_ = inject_common(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "tools.android_release.material_bootstrapper"
            ".default_permission_applier",
            posix_permission_applier,
        )
        _result, code = run_execute(
            str(keystore), str(properties), confirm=CONFIRM_PHRASE
        )
        assert code == 0
        for path in (keystore, properties):
            mode = stat.S_IMODE(path.stat().st_mode)
            assert mode == 0o600


# ---------------------------------------------------------------------------
# execute: failure cleanup
# ---------------------------------------------------------------------------


class TestWindowsPermissionAccount:
    """icacls account selection is fail-closed (supervisor correction #2)."""

    def _apply(self, monkeypatch, env):
        from tools.android_release.material_bootstrapper import (
            windows_permission_applier,
        )

        calls: list[list[str]] = []

        def fake_run(argv, **_kwargs):
            calls.append(list(argv))

            class FakeProc:
                returncode = 0
                stdout = ""
                stderr = ""

            return FakeProc()

        monkeypatch.setattr(
            "tools.android_release.material_bootstrapper.subprocess.run",
            fake_run,
        )
        for name in ("USERNAME", "USERDOMAIN"):
            if name in env:
                monkeypatch.setenv(name, env[name])
            else:
                monkeypatch.delenv(name, raising=False)
        return windows_permission_applier(Path("target.bin")), calls

    def test_prefers_domain_backslash_username(self, tmp_path, monkeypatch):
        outcome, calls = self._apply(
            monkeypatch,
            {"USERDOMAIN": "CORP", "USERNAME": "ops"},
        )
        assert outcome == (True, None)
        assert len(calls) == 1
        grant = calls[0][calls[0].index("/grant:r") + 1]
        assert grant == "CORP\\ops:F"

    def test_bare_username_without_domain(self, tmp_path, monkeypatch):
        outcome, calls = self._apply(monkeypatch, {"USERNAME": "ops"})
        assert outcome == (True, None)
        grant = calls[0][calls[0].index("/grant:r") + 1]
        assert grant == "ops:F"

    def test_userdomain_alone_fails_closed_no_icacls_call(
        self, tmp_path, monkeypatch
    ):
        outcome, calls = self._apply(monkeypatch, {"USERDOMAIN": "CORP"})
        assert outcome == (False, "permission_owner_unknown")
        assert calls == []          # a domain is not an account; grant nothing

    def test_neither_variable_fails_closed(self, tmp_path, monkeypatch):
        outcome, calls = self._apply(monkeypatch, {})
        assert outcome == (False, "permission_owner_unknown")
        assert calls == []


class TestExecuteFailureCleanup:
    def test_keytool_failure_cleans_new_keystore_no_properties(
        self, tmp_path, monkeypatch
    ):
        keystore, properties, runner, _, _permissions = inject_common(
            monkeypatch, tmp_path, gen_rc=1
        )
        # keytool "creates" the file then reports failure, as real keytool
        # can leave a partial keystore behind.
        original_call = runner.__call__

        def gen_creates_then_fails(argv, env):
            outcome = original_call(argv, env)
            if "-genkeypair" in argv and outcome.returncode != 0:
                keystore.write_bytes(b"partial")
            return outcome

        monkeypatch.setattr(
            "tools.android_release.material_bootstrapper.default_runner",
            gen_creates_then_fails,
        )
        result, code = run_execute(
            str(keystore), str(properties), confirm=CONFIRM_PHRASE
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "keytool_genkeypair_failed" in codes
        assert not keystore.exists()
        assert not properties.exists()

    def test_permission_failure_cleans_new_files(self, tmp_path, monkeypatch):
        keystore, properties, _, _, _permissions = inject_common(
            monkeypatch, tmp_path, fail_permissions_on=KEYSTORE_NAME
        )
        result, code = run_execute(
            str(keystore), str(properties), confirm=CONFIRM_PHRASE
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "permission_apply_failed" in codes
        assert not keystore.exists()
        assert not properties.exists()

    def test_list_failure_after_generation_cleans_up(
        self, tmp_path, monkeypatch
    ):
        keystore, properties, _, _, _ = inject_common(
            monkeypatch, tmp_path, list_rc=1
        )
        result, code = run_execute(
            str(keystore), str(properties), confirm=CONFIRM_PHRASE
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "keytool_list_failed" in codes
        assert not keystore.exists()
        assert not properties.exists()

    def test_cleanup_never_deletes_preexisting_files(self, tmp_path,
                                                     monkeypatch):
        keystore, _properties, _, _, _ = inject_common(monkeypatch, tmp_path)
        # an unrelated pre-existing file next to the targets survives cleanup
        bystander = keystore.parent / "unrelated.txt"
        bystander.write_text("keep me", encoding="ascii")
        failing = inject_common(
            monkeypatch, tmp_path, fail_permissions_on=KEYSTORE_NAME
        )
        _result, code = run_execute(
            str(failing[0]), str(failing[1]), confirm=CONFIRM_PHRASE
        )
        assert code == 1
        assert bystander.read_text(encoding="ascii") == "keep me"


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


class TestVerifyMaterial:
    def test_verify_passes_on_valid_material(self, tmp_path, monkeypatch):
        keystore, properties, runner, *_ = inject_common(monkeypatch, tmp_path)
        write_valid_material(keystore, properties)
        result, code = run_verify_material(
            str(keystore), str(properties),
            keytool=str(keytool_stub(keystore)),
            runner=runner,
            clock=lambda: NOW_IN_WINDOW,
            repo_root_finder=lambda: fake_repo(tmp_path),
        )
        assert code == 0
        assert result["status"] == "verified"
        cert = result["checks"]["certificate"]
        assert cert["sha256_fingerprint"] == FAKE_FINGERPRINT_HEX
        assert cert["alias_present"] is True
        assert cert["validity"]["state"] == "valid"

    def test_verify_reports_missing_keystore(self, tmp_path, monkeypatch):
        keystore, properties, runner, *_ = inject_common(monkeypatch, tmp_path)
        result, code = run_verify_material(
            str(keystore), str(properties),
            keytool=str(keytool_stub(keystore)),
            runner=runner,
            repo_root_finder=lambda: fake_repo(tmp_path),
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "keystore_missing" in codes

    def test_verify_reports_incomplete_properties(self, tmp_path,
                                                  monkeypatch):
        keystore, properties, runner, *_ = inject_common(monkeypatch, tmp_path)
        keystore.write_bytes(b"fake")
        properties.write_text(
            "keystore.path=elsewhere\nkeystore.keyAlias=aios-release\n",
            encoding="utf-8",
        )
        result, code = run_verify_material(
            str(keystore), str(properties),
            keytool=str(keytool_stub(keystore)),
            runner=runner,
            repo_root_finder=lambda: fake_repo(tmp_path),
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "properties_incomplete" in codes

    def test_verify_reports_inside_repo_material(self, tmp_path, monkeypatch):
        keystore, properties, runner, *_ = inject_common(monkeypatch, tmp_path)
        write_valid_material(keystore, properties)
        repo = fake_repo(tmp_path)
        result, code = run_verify_material(
            str(repo / KEYSTORE_NAME), str(properties),
            keytool=str(keytool_stub(keystore)),
            runner=runner,
            repo_root_finder=lambda: repo,
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "target_inside_repo" in codes

    def test_verify_reports_expired_certificate(self, tmp_path, monkeypatch):
        keystore, properties, runner, *_ = inject_common(
            monkeypatch, tmp_path, list_stdout=FIXED_WINDOW_LIST
        )
        write_valid_material(keystore, properties)
        result, code = run_verify_material(
            str(keystore), str(properties),
            runner=runner,
            keytool=str(keytool_stub(keystore)),
            clock=lambda: NOW_AFTER,
            repo_root_finder=lambda: fake_repo(tmp_path),
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "certificate_expired" in codes

    def test_verify_reports_not_yet_valid_certificate(self, tmp_path,
                                                      monkeypatch):
        keystore, properties, runner, *_ = inject_common(
            monkeypatch, tmp_path, list_stdout=FIXED_WINDOW_LIST
        )
        write_valid_material(keystore, properties)
        result, code = run_verify_material(
            str(keystore), str(properties),
            keytool=str(keytool_stub(keystore)),
            runner=runner,
            clock=lambda: NOW_BEFORE,
            repo_root_finder=lambda: fake_repo(tmp_path),
        )
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "certificate_not_yet_valid" in codes

    def test_verify_reports_unparsable_validity_honestly(
        self, tmp_path, monkeypatch
    ):
        keystore, properties, runner, *_ = inject_common(
            monkeypatch, tmp_path,
            list_stdout="Alias name: aios-release\nSHA256: "
            + FAKE_FINGERPRINT_COLONS
            + "\nValid from: nicht englisch until: egal\n",
        )
        write_valid_material(keystore, properties)
        result, _code = run_verify_material(
            str(keystore), str(properties),
            keytool=str(keytool_stub(keystore)),
            runner=runner,
            clock=lambda: NOW_IN_WINDOW,
            repo_root_finder=lambda: fake_repo(tmp_path),
        )
        cert = result["checks"]["certificate"]
        assert cert["validity"]["state"] == "not_evaluated"
        assert "validity_unparsed" not in [f["code"] for f in result["failures"]]

    def test_verify_never_echos_properties_values(self, tmp_path, monkeypatch,
                                                  capsys):
        keystore, properties, _runner, *_ = inject_common(monkeypatch, tmp_path)
        write_valid_material(keystore, properties)
        code = main(verify_args(keystore, properties))
        rendered = capsys.readouterr().out
        assert code == 0
        assert "stored-value" not in rendered
        assert str(tmp_path) not in rendered


# ---------------------------------------------------------------------------
# keytool -list parsing
# ---------------------------------------------------------------------------


class TestParseKeytoolList:
    def test_parses_fingerprint_alias_and_window(self):
        facts = parse_keytool_list(FIXED_WINDOW_LIST)
        assert facts["sha256_fingerprint"] == FAKE_FINGERPRINT_HEX
        assert facts["alias"] == "aios-release"
        assert facts["valid_from"] == datetime(2026, 9, 28, 21, 0, 0)
        assert facts["valid_until"] == datetime(2054, 2, 14, 21, 0, 0)

    def test_parses_without_validity_line(self):
        facts = parse_keytool_list(
            "Alias name: aios-release\nSHA256: " + FAKE_FINGERPRINT_COLONS
        )
        assert facts["sha256_fingerprint"] == FAKE_FINGERPRINT_HEX
        assert facts["valid_from"] is None
        assert facts["valid_until"] is None

    def test_fingerprint_case_normalized(self):
        lowered = ":".join(["a1"] * 32)
        facts = parse_keytool_list(f"SHA256: {lowered}\n")
        assert facts["sha256_fingerprint"] == FAKE_FINGERPRINT_HEX

    def test_missing_fingerprint_is_none(self):
        assert parse_keytool_list("Alias name: x\n")["sha256_fingerprint"] is None


# ---------------------------------------------------------------------------
# deterministic keytool environment + JDK 17 output compatibility (M14-175)
# ---------------------------------------------------------------------------


class TestKeytoolEnglishEnv:
    """keytool children run under a forced English JVM locale."""

    def test_env_forces_english_overriding_inherited_value(self, monkeypatch):
        # an inherited JAVA_TOOL_OPTIONS (heap flag, foreign locale) must
        # never win over the deterministic English override
        monkeypatch.setenv("JAVA_TOOL_OPTIONS", "-Xmx1g -Duser.language=zh")
        assert keytool_env()["JAVA_TOOL_OPTIONS"] == KEYTOOL_JAVA_TOOL_OPTIONS
        assert KEYTOOL_JAVA_TOOL_OPTIONS == "-Duser.language=en -Duser.country=US"

    def test_env_is_child_env_based_android_inputs_scrubbed(self, monkeypatch):
        monkeypatch.setenv("AIOS_ANDROID_KEYSTORE_STORE_PASSWORD", "leak")
        assert "AIOS_ANDROID_KEYSTORE_STORE_PASSWORD" not in keytool_env()

    def test_env_does_not_mutate_process_environment(self, monkeypatch):
        monkeypatch.setenv("JAVA_TOOL_OPTIONS", "-Xmx1g")
        keytool_env()
        assert os.environ["JAVA_TOOL_OPTIONS"] == "-Xmx1g"

    def test_both_keytool_children_receive_forced_env(self, tmp_path,
                                                      monkeypatch):
        keystore, properties, runner, *_ = inject_common(monkeypatch, tmp_path)
        _result, code = run_execute(
            str(keystore), str(properties), confirm=CONFIRM_PHRASE
        )
        assert code == 0
        assert len(runner.envs) == 2          # -genkeypair and -list
        for env in runner.envs:
            assert env["JAVA_TOOL_OPTIONS"] == KEYTOOL_JAVA_TOOL_OPTIONS


class TestJdk17LeadingTabOutput:
    """Windows JDK 17 ``-list -v`` prints fingerprints with a leading tab."""

    def test_parse_accepts_leading_tab_sha256(self):
        facts = parse_keytool_list("\tSHA256: " + FAKE_FINGERPRINT_COLONS + "\n")
        assert facts["sha256_fingerprint"] == FAKE_FINGERPRINT_HEX

    def test_parse_accepts_leading_tab_alias(self):
        facts = parse_keytool_list("\tAlias name: aios-release\n")
        assert facts["alias"] == "aios-release"

    def test_tab_indented_sha1_line_is_not_mistaken_for_sha256(self):
        text = "\tSHA1: " + ":".join(["BB"] * 20) + "\n"
        assert parse_keytool_list(text)["sha256_fingerprint"] is None

    def test_value_shape_stays_exact_32_pairs(self):
        too_short = ":".join(["A1"] * 31)
        too_long = ":".join(["A1"] * 33)
        assert parse_keytool_list(f"SHA256: {too_short}\n")["sha256_fingerprint"] is None
        assert parse_keytool_list(f"SHA256: {too_long}\n")["sha256_fingerprint"] is None

    def test_fingerprint_never_matches_across_lines(self):
        split = "SHA256: " + ":".join(["A1"] * 31) + "\n:A1\n"
        assert parse_keytool_list(split)["sha256_fingerprint"] is None

    def test_execute_succeeds_on_jdk17_tab_indented_english_output(
        self, tmp_path, monkeypatch
    ):
        """The incident shape: without the fix this failed fingerprint_missing
        and (correctly) cleaned the generated files; now it succeeds."""
        keystore, properties, _runner, *_ = inject_common(
            monkeypatch, tmp_path, list_stdout=JDK17_ENGLISH_LIST
        )
        result, code = run_execute(
            str(keystore), str(properties), confirm=CONFIRM_PHRASE
        )
        assert code == 0
        assert result["status"] == "generated"
        cert = result["checks"]["certificate"]
        assert cert["sha256_fingerprint"] == FAKE_FINGERPRINT_HEX
        assert cert["alias_present"] is True
        assert keystore.is_file()
        assert properties.is_file()

    def test_verify_succeeds_on_jdk17_tab_indented_english_output(
        self, tmp_path, monkeypatch
    ):
        keystore, properties, runner, *_ = inject_common(
            monkeypatch, tmp_path, list_stdout=JDK17_ENGLISH_LIST
        )
        write_valid_material(keystore, properties)
        result, code = run_verify_material(
            str(keystore), str(properties),
            keytool=str(keytool_stub(keystore)),
            runner=runner,
            clock=lambda: NOW_IN_WINDOW,
            repo_root_finder=lambda: fake_repo(tmp_path),
        )
        assert code == 0
        cert = result["checks"]["certificate"]
        assert cert["sha256_fingerprint"] == FAKE_FINGERPRINT_HEX
        assert cert["alias_present"] is True


class TestOutputHygiene:
    """Child stdout/stderr and passwords never reach any rendered JSON."""

    @staticmethod
    def _with_stderr_marker(monkeypatch, runner):
        original = runner.__call__

        def adds_stderr(argv, env):
            outcome = original(argv, env)
            if "-list" in argv:
                return CommandOutcome(
                    outcome.returncode, outcome.stdout, CHILD_STDERR_MARKER
                )
            return outcome

        monkeypatch.setattr(
            "tools.android_release.material_bootstrapper.default_runner",
            adds_stderr,
        )

    def test_execute_json_excludes_child_stdout_stderr_passwords(
        self, tmp_path, monkeypatch, capsys
    ):
        keystore, properties, runner, *_ = inject_common(
            monkeypatch, tmp_path,
            list_stdout=dynamic_window_list() + CHILD_STDOUT_MARKER + "\n",
        )
        self._with_stderr_marker(monkeypatch, runner)
        code = main(execute_args(keystore, properties))
        assert code == 0
        rendered = capsys.readouterr().out
        result = json.loads(rendered)
        serialized = json.dumps(result, sort_keys=True)
        for blob in (rendered, serialized):
            for marker in (
                CHILD_STDOUT_MARKER, CHILD_STDERR_MARKER, PASSWORD_MARKER,
            ):
                assert marker not in blob

    def test_failure_json_excludes_child_stdout_stderr(self, tmp_path,
                                                       monkeypatch, capsys):
        """The incident path: -list fails after generation; the failure JSON
        stays value-free and the generated files are still cleaned up."""
        keystore, properties, runner, *_ = inject_common(
            monkeypatch, tmp_path, list_rc=1,
            list_stdout=CHILD_STDOUT_MARKER + "\n",
        )
        self._with_stderr_marker(monkeypatch, runner)
        code = main(execute_args(keystore, properties))
        assert code == 1
        rendered = capsys.readouterr().out
        assert CHILD_STDOUT_MARKER not in rendered
        assert CHILD_STDERR_MARKER not in rendered
        assert PASSWORD_MARKER not in rendered
        assert not keystore.exists()
        assert not properties.exists()


# ---------------------------------------------------------------------------
# single-password PKCS12 contract (M14-175 round 2)
# ---------------------------------------------------------------------------


def _property_values(text: str) -> dict[str, str]:
    """Escape-free key=value pairs (fake passwords contain no backslashes)."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, raw = line.partition("=")
            values[key.strip()] = raw.strip()
    return values


class TestSinglePasswordPkcs12Contract:
    """PKCS12 keys are read with the store password (operator-confirmed:
    diverging passwords fail packageRelease with "Given final block not
    properly padded"); generation therefore uses ONE password for both."""

    def test_entropy_requested_exactly_once(self, tmp_path, monkeypatch):
        keystore, properties, _runner, entropy, _ = inject_common(
            monkeypatch, tmp_path
        )
        _result, code = run_execute(
            str(keystore), str(properties), confirm=CONFIRM_PHRASE
        )
        assert code == 0
        assert entropy.lengths == [PASSWORD_LENGTH]  # ONE draw, store == key

    def test_genkeypair_pins_pkcs12_and_one_password(self, tmp_path,
                                                     monkeypatch):
        keystore, properties, runner, _entropy, _ = inject_common(
            monkeypatch, tmp_path
        )
        _result, code = run_execute(
            str(keystore), str(properties), confirm=CONFIRM_PHRASE
        )
        assert code == 0
        gen_argv = runner.calls[0]
        assert gen_argv[gen_argv.index("-storetype") + 1] == KEYSTORE_TYPE
        store_pw = gen_argv[gen_argv.index("-storepass") + 1]
        key_pw = gen_argv[gen_argv.index("-keypass") + 1]
        assert store_pw == key_pw
        assert store_pw                # one non-empty OS-random value

    def test_properties_carry_identical_password_values(self, tmp_path,
                                                        monkeypatch):
        keystore, properties, _runner, _entropy, _ = inject_common(
            monkeypatch, tmp_path
        )
        _result, code = run_execute(
            str(keystore), str(properties), confirm=CONFIRM_PHRASE
        )
        assert code == 0
        values = _property_values(properties.read_text(encoding="utf-8"))
        assert set(values) == set(PROPERTIES_KEYS)
        assert values["keystore.storePassword"] == values["keystore.keyPassword"]

    def test_verify_rejects_diverging_passwords_without_echoing(
        self, tmp_path, monkeypatch, capsys
    ):
        keystore, properties, _runner, *_ = inject_common(monkeypatch, tmp_path)
        write_valid_material(
            keystore, properties,
            store_password="store-pw-must-not-appear",
            key_password="key-pw-must-not-appear",
        )
        code = main(verify_args(keystore, properties))
        assert code == 1
        rendered = capsys.readouterr().out
        result = json.loads(rendered)
        codes = [f["code"] for f in result["failures"]]
        assert "store_key_password_mismatch" in codes
        assert (
            result["checks"]["properties_keys"]["store_key_passwords_equal"]
            is False
        )
        assert "store-pw-must-not-appear" not in rendered
        assert "key-pw-must-not-appear" not in rendered

    def test_verify_passes_and_records_equal_passwords(self, tmp_path,
                                                       monkeypatch):
        keystore, properties, runner, *_ = inject_common(monkeypatch, tmp_path)
        write_valid_material(keystore, properties)
        result, code = run_verify_material(
            str(keystore), str(properties),
            keytool=str(keytool_stub(keystore)),
            runner=runner,
            clock=lambda: NOW_IN_WINDOW,
            repo_root_finder=lambda: fake_repo(tmp_path),
        )
        assert code == 0
        keys_check = result["checks"]["properties_keys"]
        assert keys_check["all_present"] is True
        assert keys_check["store_key_passwords_equal"] is True


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestCli:
    def test_cli_plan_json_deterministic(self, tmp_path, monkeypatch, capsys):
        keystore, properties = make_targets(tmp_path)
        repo = fake_repo(tmp_path)
        monkeypatch.setattr(
            "tools.android_release.material_bootstrapper.default_repo_root",
            lambda: repo,
        )
        main(plan_args(keystore, properties))
        first = capsys.readouterr().out
        main(plan_args(keystore, properties))
        second = capsys.readouterr().out
        assert first == second

    def test_cli_rejects_validity_below_minimum_at_argparse(self, tmp_path):
        keystore, properties = make_targets(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main([
                *execute_args(keystore, properties),
                "--validity-days", str(MIN_VALIDITY_DAYS - 1),
            ])
        assert exc.value.code == 2

    def test_cli_execute_without_keytool_fails_closed(self, tmp_path,
                                                      monkeypatch, capsys):
        keystore, properties, *_ = inject_common(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "tools.android_release.material_bootstrapper.shutil.which",
            lambda _name: None,
        )
        args = [
            "execute", "--keystore", str(keystore),
            "--properties", str(properties),
            "--confirm", CONFIRM_PHRASE,
        ]
        code = main(args)
        rendered = capsys.readouterr().out
        result = json.loads(rendered)
        assert code == 1
        codes = [f["code"] for f in result["failures"]]
        assert "tool_missing" in codes
        assert not keystore.exists()
        assert not properties.exists()
