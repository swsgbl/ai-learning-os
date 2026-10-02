"""Fail-closed bash executor resolution for release tooling.

Windows may resolve ``bash`` to ``System32\\bash.exe``. That executable is a
WSL relay rather than a native shell; when its distro runtime is unavailable it
turns one orchestration problem into many downstream test failures. Release
tools therefore resolve the executor once and reject unsafe launchers before
starting work.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path, PureWindowsPath

AIOS_BASH_ENV = "AIOS_BASH"


class BashExecutorError(RuntimeError):
    """No usable bash executor is available (caller must fail closed)."""


def _is_windows_wsl_launcher(path: str | os.PathLike[str]) -> bool:
    """Return true for Windows launchers whose semantics depend on WSL."""
    parts = tuple(part.lower() for part in PureWindowsPath(os.fspath(path)).parts)
    return parts[-1:] == ("bash.exe",) and bool(
        {"system32", "sysnative", "windowsapps"} & set(parts[:-1])
    )


def _is_usable_executor(path: str | os.PathLike[str]) -> bool:
    candidate = Path(path)
    return candidate.is_file() and os.access(candidate, os.X_OK)


def _git_bash_candidates(git_path: str | os.PathLike[str]) -> tuple[Path, ...]:
    """Derive native Git Bash paths from a located ``git.exe``.

    This covers Git's root, ``bin/``, and standard ``cmd/`` installation
    layouts without embedding a machine-specific absolute path.
    """
    git_dir = Path(git_path).parent
    candidates = (
        git_dir / "bash.exe",
        git_dir / "bin" / "bash.exe",
        git_dir / "usr" / "bin" / "bash.exe",
        git_dir.parent / "bin" / "bash.exe",
        git_dir.parent / "usr" / "bin" / "bash.exe",
    )
    return tuple(dict.fromkeys(candidates))


def _program_file_candidates(env: Mapping[str, str]) -> tuple[Path, ...]:
    """Conventional Git for Windows install roots (optional final fallback)."""
    roots = (
        env.get("ProgramFiles"),
        env.get("ProgramFiles(x86)"),
        env.get("ProgramW6432"),
    )
    candidates: list[Path] = []
    for root in roots:
        if not root:
            continue
        base = Path(root) / "Git"
        candidates.extend(
            (base / "bin" / "bash.exe", base / "usr" / "bin" / "bash.exe")
        )
    return tuple(dict.fromkeys(candidates))


def _path_candidates(name: str, env: Mapping[str, str]) -> tuple[Path, ...]:
    """Return every matching executable visible in PATH order.

    ``shutil.which`` intentionally returns only the first match. On this
    Windows failure mode that first match is the WSL relay while a native Git
    Bash appears later in PATH, so the resolver must inspect the full order.
    """
    candidates: list[Path] = []
    for directory in env.get("PATH", "").split(os.pathsep):
        if directory:
            candidates.append(Path(directory) / name)
    return tuple(dict.fromkeys(candidates))


def resolve_bash(
    *,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    os_name: str = os.name,
) -> str:
    """Resolve one executable bash path or raise :class:`BashExecutorError`.

    ``AIOS_BASH`` is an explicit override, not a hint: an invalid or WSL
    launcher path fails immediately. On POSIX, PATH lookup preserves CI
    behavior. On Windows, a non-relay PATH bash wins first, followed by bash
    derived from Git's installation layout and conventional Program Files
    locations; ``System32``/``Sysnative``/``WindowsApps`` launchers are never
    selected automatically.
    """
    environment = os.environ if env is None else env

    explicit = environment.get(AIOS_BASH_ENV)
    if explicit:
        if not _is_usable_executor(explicit):
            raise BashExecutorError(
                f"{AIOS_BASH_ENV} 指定的 bash 不可用: {explicit} "
                "(路径必须存在且可执行)"
            )
        if os_name == "nt" and _is_windows_wsl_launcher(explicit):
            raise BashExecutorError(
                f"{AIOS_BASH_ENV} 指定的是 WSL bash 启动器: {explicit} "
                "(请改用 Git Bash 等原生 Windows bash)"
            )
        return str(Path(explicit).resolve())

    path_bash = which("bash")
    if path_bash and _is_usable_executor(path_bash) and (
        os_name != "nt" or not _is_windows_wsl_launcher(path_bash)
    ):
        return str(Path(path_bash).resolve())

    if os_name != "nt":
        raise BashExecutorError("bash preflight failed: PATH 上找不到可执行的 bash")

    if os_name == "nt":
        for candidate in _path_candidates("bash.exe", environment):
            if _is_usable_executor(candidate) and not _is_windows_wsl_launcher(
                candidate
            ):
                return str(candidate.resolve())

    git_path = which("git")
    candidates: tuple[Path, ...] = ()
    if git_path:
        candidates = _git_bash_candidates(git_path)
    if os_name == "nt":
        for git_candidate in _path_candidates("git.exe", environment):
            candidates += _git_bash_candidates(git_candidate)
    candidates += _program_file_candidates(environment)

    for candidate in candidates:
        candidate_text = str(candidate)
        if _is_usable_executor(candidate) and not _is_windows_wsl_launcher(
            candidate_text
        ):
            return str(candidate.resolve())

    raise BashExecutorError(
        "bash preflight failed: 未找到可用的原生 Windows bash。"
        f"可设置 {AIOS_BASH_ENV} 指向已存在且可执行的 Git Bash；"
        "System32/Sysnative/WindowsApps 的 WSL 启动器会被排除。"
    )
