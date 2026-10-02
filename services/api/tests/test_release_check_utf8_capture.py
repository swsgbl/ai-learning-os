"""M14-217 release-check subprocess UTF-8 capture contracts."""
from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

import pytest

from app.ops import release_check_isolated as isolated_runner
from app.ops.release_check import (
    OUTPUT_REPLACEMENT_NOTE,
    CheckResult,
    CommandCheck,
    _execute_command,
    build_release_check_evidence,
    check_migration_current,
    run_captured,
)

SECRET = "UTF8-CAPTURE-SECRET"


def _child(
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
) -> list[str]:
    """Build a Python child that writes exact bytes without console encoding."""
    out_b64 = base64.b64encode(stdout)
    err_b64 = base64.b64encode(stderr)
    code = (
        "import base64, sys; "
        f"sys.stdout.buffer.write(base64.b64decode({out_b64!r})); "
        f"sys.stderr.buffer.write(base64.b64decode({err_b64!r})); "
        "sys.stdout.flush(); sys.stderr.flush(); "
        f"sys.exit({returncode})"
    )
    return [sys.executable, "-c", code]


def test_run_captured_preserves_utf8_and_replaces_invalid_bytes(capfd) -> None:
    """UTF-8 中文保真；GBK/非法字节由 reader 线程替换而不是抛异常。"""
    proc = run_captured(
        _child(
            stdout="中文 UTF-8\n".encode() + "中文 GBK\n".encode("gbk"),
            stderr=b"\xff\xfe invalid\n",
        ),
        timeout=30,
    )
    assert proc.returncode == 0
    assert proc.stdout.startswith("中文 UTF-8\n")
    assert "\ufffd" in proc.stdout and "\ufffd" in proc.stderr
    captured = capfd.readouterr()
    assert "UnicodeDecodeError" not in captured.err


def test_run_captured_empty_long_and_failed_processes() -> None:
    """空输出、长输出与失败退出码均保留原语义。"""
    empty = run_captured(_child(), timeout=30)
    assert empty.returncode == 0
    assert empty.stdout == "" and empty.stderr == ""

    long_child = [
        sys.executable,
        "-c",
        "import sys; sys.stdout.buffer.write(b'x' * 131_072 + b'\\n')",
    ]
    long = run_captured(long_child, timeout=30)
    assert long.stdout == "x" * 131_072 + "\n"

    failed = run_captured(
        _child(stdout=b"before failure\n", stderr=b"failure stderr\n", returncode=7),
        timeout=30,
    )
    assert failed.returncode == 7
    assert failed.stdout == "before failure\n"
    assert failed.stderr == "failure stderr\n"


def test_execute_command_records_replacement_and_evidence_redacts_secret() -> None:
    """非 UTF-8 输出进入 detail；证据保留 replacement note 并抹凭据。"""
    argv = _child(
        stdout=(
            f"ERROR: postgresql://aios:{SECRET}@127.0.0.1:5433/db\n".encode()
            + b"GBK: "
            + "中文".encode("gbk")
            + b"\n"
        ),
        returncode=3,
    )
    check = CommandCheck("utf8-check", "UTF-8 capture", tuple(argv), Path.cwd())
    ok, detail = _execute_command(check)
    assert ok is False
    assert SECRET in detail
    assert OUTPUT_REPLACEMENT_NOTE in detail

    result = next(
        result
        for result in build_release_check_evidence(
            [CheckResult(check.id, check.title, "fail", detail, "command")],
            execution_scope="full",
        )["checks"]
    )
    assert SECRET not in result["detail"]
    assert "***" in result["detail"]
    assert OUTPUT_REPLACEMENT_NOTE in result["detail"]


def test_run_captured_preserves_timeout_env_and_cwd_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """统一入口仍把 timeout/env/cwd 原样交给 subprocess，并保留 TimeoutExpired。"""
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr("app.ops.release_check.subprocess.run", fake_run)
    with pytest.raises(subprocess.TimeoutExpired):
        run_captured(
            ["sample"], cwd=tmp_path, env={"AIOS_TEST": "1"}, timeout=12.5
        )
    assert captured["cwd"] == tmp_path
    assert captured["env"] == {"AIOS_TEST": "1"}
    assert captured["timeout"] == 12.5
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"
    assert captured["check"] is False


def test_isolated_migration_records_replacement_and_redacts_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """isolated 迁移失败：UTF-8 替换留痕、凭据仍被抹除。"""
    stdout = "upgrade failed\npostgresql://aios:" + SECRET + "@host/db\n"
    stderr = "GBK bytes decoded with replacement: \ufffd\n"

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 4, stdout, stderr)

    monkeypatch.setattr(isolated_runner, "run_captured", fake_run)
    ok, detail = isolated_runner.run_alembic_upgrade(
        "sqlite+aiosqlite:///unused.db", timeout=1
    )
    assert ok is False
    assert OUTPUT_REPLACEMENT_NOTE in detail
    assert SECRET not in detail and "***" in detail


def test_isolated_migration_success_and_timeout_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功输出含替换时也留痕；TimeoutExpired 仍转为稳定失败消息。"""
    monkeypatch.setattr(
        isolated_runner,
        "run_captured",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, "\ufffd\n", ""
        ),
    )
    ok, detail = isolated_runner.run_alembic_upgrade(
        "sqlite+aiosqlite:///unused.db"
    )
    assert ok is True
    assert OUTPUT_REPLACEMENT_NOTE in detail

    def timeout_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(isolated_runner, "run_captured", timeout_run)
    ok, detail = isolated_runner.run_alembic_upgrade(
        "sqlite+aiosqlite:///unused.db", timeout=9
    )
    assert ok is False
    assert "alembic upgrade head 超时 (>9s)" in detail


def test_migration_current_records_replacement() -> None:
    """migration current 对账同样把替换写入通过/失败 detail。"""

    class FakeAlembic:
        def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
            command = argv[-1]
            revision = "0027_audit_chain"
            suffix = " (head)" if command == "heads" else ""
            return subprocess.CompletedProcess(
                argv, 0, f"{revision}{suffix} | \ufffd\n", ""
            )

    detail = check_migration_current(Path("."), alembic=FakeAlembic())
    assert "current == head == 0027_audit_chain" in detail
    assert OUTPUT_REPLACEMENT_NOTE in detail


def test_uvicorn_log_tail_records_replacement_and_redacts_secret(
    tmp_path: Path,
) -> None:
    """uvicorn 日志尾部：中文保真、替换留痕、凭据抹除。"""
    log = tmp_path / "uvicorn.log"
    log.write_bytes(
        "INFO: 中文\n".encode()
        + b"\xff invalid\n"
        + f"ERROR: postgresql://aios:{SECRET}@host/db\n".encode()
    )
    tail = isolated_runner.uvicorn_log_tail(log)
    assert "中文" in tail and "\ufffd" in tail
    assert OUTPUT_REPLACEMENT_NOTE in tail
    assert SECRET not in tail and "***" in tail
