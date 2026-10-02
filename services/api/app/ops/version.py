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


def locate_repository_root(
    start: Path | None = None, *, require_infra: bool = False
) -> Path:
    """向上查找仓库根：源码 checkout（services/api/app/ops → 仓库根）与
    生产镜像 /app 布局（/app/app/ops → /app）同一解析——两种布局的仓库根
    都具备 VERSION 文件，祖先目录都不具备。

    M14-211：固定 ``parents[N]`` 偏移只对单一布局成立（源码 checkout 的
    ``parents[4]`` 在容器 /app 布局下越界 IndexError；反之亦然）。本函数按
    VERSION 标记向上查找，找不到时抛明确 RuntimeError，不允许含混失败——
    供 ``is_safe_artifact_path`` 等容器内必须可用的护栏复用（此前
    ``legacy_papers.is_safe_artifact_path`` 的 ``parents[4]`` 在容器内
    IndexError 导致 provider-smoke 导出失败）。

    ``start`` 可注入起点路径（测试容器布局用，缺省本模块真实位置）。
    ``require_infra=True`` 额外要求仓库根含 ``infra`` 目录（provider-smoke
    编排冒烟脚本的更强契约——容器内 ``infra/smoke_*.sh`` 必须可见才允许
    解析为根；弱契约只找 VERSION，供 version 自身等不需要 infra 的场景）。
    """
    current = (start or Path(__file__)).resolve()
    for parent in current.parents:
        if not (parent / "VERSION").is_file():
            continue
        if require_infra and not (parent / "infra").is_dir():
            continue
        return parent
    hint = (
        "VERSION 文件 + infra 目录" if require_infra else "VERSION 文件"
    )
    suffix = " 与 infra/smoke_*.sh" if require_infra else ""
    raise RuntimeError(
        f"仓库根未找到：期望 {hint} 位于源码仓库根或容器 /app"
        f"（services/api Dockerfile 需打包 VERSION{suffix}）"
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
