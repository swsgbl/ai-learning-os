"""M7-06 Versioning：单一版本真相源与运行时版本信息。

VERSION 文件（仓库根）是唯一版本真相源；web package.json 的 version 与
CHANGELOG 顶部条目由测试守卫与之同步。git commit 与 alembic revision
由调用方按需查询（端点不 spawn 子进程，CLI 查真实状态）。
"""
from __future__ import annotations

import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


def _locate_version_file() -> Path:
    """向上查找 VERSION：源码仓库布局（repo 根）与容器布局（/app/VERSION）都兼容。

    M8-00：固定 parents[4] 在容器 /app 下越界（IndexError 导致 API 容器
    启动失败）；找不到 VERSION 时抛明确 RuntimeError，不允许含混失败。
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "VERSION"
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "VERSION 文件未找到：期望位于源码仓库根或容器 /app 目录（version 打包缺失）"
    )


REPO_ROOT = _locate_version_file().parent
VERSION_FILE = REPO_ROOT / "VERSION"

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


@lru_cache(maxsize=1)
def read_version() -> str:
    """读 VERSION 文件（X.Y.Z）；缺失或格式错即抛错——不虚报版本。"""
    text = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not _SEMVER_RE.match(text):
        raise ValueError(f"VERSION 文件格式非法: {text!r}（期望 X.Y.Z）")
    return text


def git_commit(repo_root: Path | None = None) -> str | None:
    """当前 git commit 短 hash；非 git 环境（如容器内无 .git）如实 None。"""
    root = repo_root or REPO_ROOT
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root), capture_output=True, text=True, check=False, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _alembic_dir() -> Path:
    """alembic.ini 所在目录：源码布局在 services/api，容器布局就在 /app。"""
    for candidate in (REPO_ROOT / "services" / "api", REPO_ROOT):
        if (candidate / "alembic.ini").is_file():
            return candidate
    return REPO_ROOT / "services" / "api"  # 兜底：subprocess 如实报错


def alembic_state(api_dir: Path | None = None) -> dict:
    """{current, head}；两值相等即迁移已就位。可注入 alembic 可执行。"""
    root = api_dir or _alembic_dir()

    def _run(cmd: str) -> str:
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", cmd],
            cwd=str(root), capture_output=True, text=True, check=False, timeout=60,
        )
        return proc.stdout

    def _first_rev(out: str) -> str | None:
        for ln in out.splitlines():
            if ln and ln[0].isalnum():
                return ln.split()[0]
        return None

    return {
        "current": _first_rev(_run("current")),
        "head": _first_rev(_run("heads")),
    }


def build_version_info(*, git: str | None = None, alembic: dict | None = None) -> dict:
    """端点/CLI 共用的版本信息结构。git/alembic 缺席如实 not_available。"""
    return {
        "version": read_version(),
        "git_commit": git if git is not None else "not_available",
        "alembic_current": (alembic or {}).get("current") or "not_available",
        "alembic_head": (alembic or {}).get("head") or "not_available",
    }
