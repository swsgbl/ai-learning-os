"""M7-06 Versioning：运行时版本端点。"""
from __future__ import annotations

from fastapi import APIRouter

from app.ops.version import build_version_info

router = APIRouter(prefix="/api/v1/version", tags=["version"])


@router.get("")
async def get_version() -> dict:
    """版本 + git commit + alembic 状态；git/alembic 不可用如实 not_available。

    端点不查 alembic（不引入 DB 依赖），固定 not_available；真实迁移
    状态用 `python -m app.ops.cli version` 查询（经 alembic 子进程）。
    """
    git = None
    try:
        from app.ops.version import git_commit

        git = git_commit()
    except Exception:  # noqa: BLE001 - 任何异常如实降级 not_available
        git = None
    return build_version_info(git=git)
