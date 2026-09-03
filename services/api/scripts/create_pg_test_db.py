"""创建隔离 PostgreSQL 测试库（M10-04 返工：PG 测试库隔离）。

事故背景：AIOS_PG_TEST_URL 曾被指向共享主库 5433/ai_learning_os 跑全量
pytest，向主库永久写入 6 批 × 8 张「PG 验证卷」。本脚本是唯一正确的
测试库准备路径：

    cd services/api
    .venv/Scripts/python.exe scripts/create_pg_test_db.py

默认在本机 compose 栈（127.0.0.1:5433）创建隔离库 ai_learning_os_test。

安全约束（fail-closed）：
- 只做两件事：查目标库是否存在；不存在则 CREATE DATABASE。
  绝不 DROP、绝不修改/写入任何既有数据库（含主库 ai_learning_os）；
- 目标库名必须通过 app.db.test_gate 白名单（主/共享库名与其他名字一律拒绝）；
- 维护连接（--admin-url，默认 postgres 维护库）不得指向主/共享数据
  库 ai_learning_os，也不允许缺库名。

创建完成后回连新库验证 current_database()，并打印应设置的
AIOS_PG_TEST_URL（PowerShell / bash 两种形式）。
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # services/api -> sys.path

from app.db.test_gate import (
    ISOLATED_TEST_DATABASES,
    PROTECTED_DATA_DATABASES,
    is_isolated_test_database,
)

DEFAULT_ADMIN_URL = "postgresql+asyncpg://aios:aios@127.0.0.1:5433/postgres"
DEFAULT_NAME = "ai_learning_os_test"
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


async def _database_exists(admin_url: str, name: str) -> bool:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            found = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            return found is not None
    finally:
        await engine.dispose()


async def _create_database(admin_url: str, name: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


async def _verify_current_database(url: object) -> str:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    # 传 URL 对象而非 str(URL)——后者会把密码渲染成字面量 ***（SQLAlchemy 2.x）
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return await conn.scalar(text("SELECT current_database()"))
    finally:
        await engine.dispose()


def _parse_admin_url(raw: str) -> tuple[object, str]:
    from sqlalchemy.engine import make_url

    try:
        parsed = make_url(raw)
    except Exception:  # noqa: BLE001 - fail-closed
        print("拒绝：--admin-url 无法解析为数据库 URL。", file=sys.stderr)
        sys.exit(2)
    database = (parsed.database or "").strip().lower()
    if not database:
        print("拒绝：--admin-url 缺少数据库名（应指向 postgres 维护库）。", file=sys.stderr)
        sys.exit(2)
    if database in PROTECTED_DATA_DATABASES:
        print(
            f"拒绝：--admin-url 指向主/共享库 {database}——维护连接不得落在主库。",
            file=sys.stderr,
        )
        sys.exit(2)
    return parsed, database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="创建隔离 PG 测试库（只 CREATE，绝不触碰既有库）"
    )
    parser.add_argument(
        "--admin-url",
        default=DEFAULT_ADMIN_URL,
        help="维护连接 URL（默认本机 compose 5433 的 postgres 维护库）",
    )
    parser.add_argument(
        "--name",
        default=DEFAULT_NAME,
        help=f"隔离测试库名（默认 {DEFAULT_NAME}；必须通过安全门控白名单）",
    )
    args = parser.parse_args(argv)

    name = args.name.strip().lower()
    if not _IDENTIFIER.match(name) or not is_isolated_test_database(name):
        allowed = " / ".join(sorted(ISOLATED_TEST_DATABASES))
        print(
            f"拒绝：库名 {args.name!r} 不在隔离测试库白名单"
            f"（{allowed} 及其 `<名>_` 前缀；主/共享库名一律拒绝）。",
            file=sys.stderr,
        )
        return 2

    parsed_admin, admin_db = _parse_admin_url(args.admin_url)
    test_url_obj = parsed_admin.set(database=name)
    test_url = test_url_obj.render_as_string(hide_password=False)

    print(f"[1/3] 维护连接 -> {parsed_admin.render_as_string(hide_password=True)}（库 {admin_db}）")
    if asyncio.run(_database_exists(args.admin_url, name)):
        print(f"[2/3] 隔离测试库 {name} 已存在——跳过创建（对任何既有库零写入）")
    else:
        print(f"[2/3] CREATE DATABASE {name}（服务器级新建，不触碰既有库）")
        asyncio.run(_create_database(args.admin_url, name))

    current = asyncio.run(_verify_current_database(test_url_obj))
    print(f"[3/3] 回连验证 current_database() = {current}")
    if current != name:  # pragma: no cover - 防御性复核
        print(f"异常：回连库名 {current!r} != 目标 {name!r}，拒绝继续。", file=sys.stderr)
        return 2

    print()
    print("设置测试门控变量后运行全量 pytest（仓库根）：")
    print(f"  PowerShell: $env:AIOS_PG_TEST_URL='{test_url}'")
    print(f"  bash:       export AIOS_PG_TEST_URL='{test_url}'")
    print("  python -m pytest services/api -q")
    print()
    print("禁止把 5433/ai_learning_os（主/共享库）设为 AIOS_PG_TEST_URL——")
    print("安全门控（app/db/test_gate.py）会自动拒绝并跳过，不建立任何连接。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
