"""M14-216 bash executor selection contracts."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.ops.bash_executor import AIOS_BASH_ENV, BashExecutorError, resolve_bash


def _write_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake-executable\n")
    path.chmod(0o755)
    return path


def test_explicit_override_wins_before_windows_path_lookup(tmp_path: Path) -> None:
    """AIOS_BASH 是强制覆盖：优先且不落入 PATH/System32 探测。"""
    explicit = _write_executable(tmp_path / "explicit" / "bash.exe")
    path_bash = _write_executable(tmp_path / "path" / "bash.exe")
    resolved = resolve_bash(
        env={AIOS_BASH_ENV: str(explicit)},
        which=lambda name: str(path_bash) if name == "bash" else None,
        os_name="nt",
    )
    assert Path(resolved) == explicit.resolve()


def test_explicit_override_missing_fails_closed(tmp_path: Path) -> None:
    """显式路径不存在时不回退，直接给出一个 preflight 错误。"""
    def _no_lookup(name: str) -> str | None:
        pytest.fail("显式 AIOS_BASH 无效时不得继续 PATH 探测")

    with pytest.raises(BashExecutorError, match=AIOS_BASH_ENV):
        resolve_bash(
            env={AIOS_BASH_ENV: str(tmp_path / "missing" / "bash.exe")},
            which=_no_lookup,
            os_name="nt",
        )


def test_explicit_windows_wsl_launcher_rejected(tmp_path: Path) -> None:
    """System32 bash 即使存在也不得作为原生执行器。"""
    launcher = _write_executable(tmp_path / "Windows" / "System32" / "bash.exe")
    with pytest.raises(BashExecutorError, match="WSL bash 启动器"):
        resolve_bash(
            env={AIOS_BASH_ENV: str(launcher)},
            which=lambda name: None,
            os_name="nt",
        )


def test_windows_system32_is_degraded_to_derived_git_bash(tmp_path: Path) -> None:
    """PATH 被 System32 WSL 启动器占位时，从 git 布局推导原生 Git Bash。"""
    system32 = _write_executable(tmp_path / "Windows" / "System32" / "bash.exe")
    git = _write_executable(tmp_path / "Git" / "cmd" / "git.exe")
    native = _write_executable(tmp_path / "Git" / "bin" / "bash.exe")
    resolved = resolve_bash(
        env={},
        which=lambda name: str(system32 if name == "bash" else git),
        os_name="nt",
    )
    assert Path(resolved) == native.resolve()


def test_windows_prefers_non_launcher_path_bash_before_git(tmp_path: Path) -> None:
    """PATH 上已有原生 bash 时优先使用，不额外推导 git 安装。"""
    path_bash = _write_executable(tmp_path / "native-path" / "bash.exe")
    git = _write_executable(tmp_path / "Git" / "cmd" / "git.exe")
    git_bash = _write_executable(tmp_path / "Git" / "bin" / "bash.exe")
    resolved = resolve_bash(
        env={},
        which=lambda name: str(path_bash if name == "bash" else git),
        os_name="nt",
    )
    assert Path(resolved) == path_bash.resolve()
    assert Path(resolved) != git_bash.resolve()


def test_posix_path_lookup_does_not_use_git_derivation(tmp_path: Path) -> None:
    """Linux/CI 回归：仍按 PATH 选择 bash，不进入 Windows 推导链。"""
    posix_bash = _write_executable(tmp_path / "usr" / "bin" / "bash")
    resolved = resolve_bash(
        env={},
        which=lambda name: str(posix_bash) if name == "bash" else None,
        os_name="posix",
    )
    assert Path(resolved) == posix_bash.resolve()


def test_resolved_executor_runs_real_command() -> None:
    """解析结果必须能真实执行命令，而不是只返回一个存在的路径。"""
    bash = resolve_bash()
    proc = subprocess.run(
        [bash, "-c", "printf M14-216"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "M14-216"
