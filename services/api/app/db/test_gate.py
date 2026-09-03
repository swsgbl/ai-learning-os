"""AIOS_PG_TEST_URL 安全门控：PG 集成测试只允许连隔离测试库。

事故背景（M10-04 返工）：全量 pytest 在
``AIOS_PG_TEST_URL=postgresql+asyncpg://aios:aios@127.0.0.1:5433/ai_learning_os``
（共享主库）下运行，每轮向主库写入 8 张「PG 验证卷」，6 批共 48 张，
主库被永久污染（只读分类报告 744/1397 -> 792/1487）。

门控策略（fail-closed 白名单，全仓库唯一实现，测试与脚本一律复用）：

- 只做 URL 解析，**绝不连接数据库**——被拒绝的测试在建立任何连接前跳过；
- 数据库名必须命中隔离测试库白名单（``ai_learning_os_test`` /
  ``ai_learning_os_drill`` 及其 ``<名>_`` 前缀变体）；
- 主/共享数据库名（``ai_learning_os``）、维护库（``postgres``）、
  缺库名、非 PostgreSQL 驱动、无法解析的 URL 一律拒绝并给出原因；
- 拒绝原因不回显完整 URL（可能内嵌凭据），只回显库名。
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.engine import make_url

ENV_VAR = "AIOS_PG_TEST_URL"

#: 项目主/共享数据库名（compose DATABASE_URL 的目标库）——测试永远禁止。
PROTECTED_DATA_DATABASES: frozenset[str] = frozenset({"ai_learning_os"})
#: PG 维护库——不是测试目标，同样禁止。
MAINTENANCE_DATABASES: frozenset[str] = frozenset({"postgres"})
#: 门控合并拒绝名单（测试 URL 视角）。
MAIN_DATABASES: frozenset[str] = PROTECTED_DATA_DATABASES | MAINTENANCE_DATABASES
#: 明确隔离的测试库名白名单（backup drill 自管 ai_learning_os_drill）。
ISOLATED_TEST_DATABASES: frozenset[str] = frozenset(
    {"ai_learning_os_test", "ai_learning_os_drill"}
)


@dataclass(frozen=True)
class PgTestGate:
    """门控判定结果。

    enabled=False 时 url 恒为 None——调用方拿不到可用连接串，
    物理上无法连到被拒绝的库。
    """

    enabled: bool
    reason: str
    url: str | None = None
    database: str | None = None


def is_isolated_test_database(name: str) -> bool:
    """库名是否属于允许的隔离测试库（白名单成员或其 ``<名>_`` 前缀变体）。

    前缀必须带下划线边界：``ai_learning_os_test_2`` 放行，
    ``ai_learning_os_testx`` / ``ai_learning_os_tests`` 拒绝（防前缀误放行）。
    """
    normalized = name.strip().lower()
    if not normalized or normalized in MAIN_DATABASES:
        return False
    if normalized in ISOLATED_TEST_DATABASES:
        return True
    return normalized.startswith(
        tuple(database + "_" for database in sorted(ISOLATED_TEST_DATABASES))
    )


def evaluate_pg_test_url(url: str | None) -> PgTestGate:
    """判定 AIOS_PG_TEST_URL 是否可用于集成测试（纯解析，零连接）。"""
    if url is None or not url.strip():
        return PgTestGate(
            False,
            f"{ENV_VAR} 未设置——真实 PG 集成测试跳过（单元测试用 SQLite 替身）",
        )
    try:
        parsed = make_url(url.strip())
    except Exception:  # noqa: BLE001 - 任何解析失败都按不安全处理
        return PgTestGate(
            False, f"{ENV_VAR} 无法解析为数据库 URL（fail-closed）——已跳过"
        )
    driver = (parsed.drivername or "").lower()
    if not driver.startswith("postgresql"):
        return PgTestGate(
            False, f"{ENV_VAR} 驱动 {driver or '(空)'} 不是 PostgreSQL——已跳过"
        )
    database = (parsed.database or "").strip()
    if not database:
        return PgTestGate(
            False,
            f"{ENV_VAR} 缺少数据库名（fail-closed）——已跳过；"
            f"隔离测试库应形如 {min(ISOLATED_TEST_DATABASES)}",
        )
    normalized = database.lower()
    if normalized in MAIN_DATABASES:
        return PgTestGate(
            False,
            f"{ENV_VAR} 指向主/共享库 {normalized}——禁止向主库写入测试数据，已跳过；"
            "请创建并使用隔离测试库 ai_learning_os_test"
            "（services/api/scripts/create_pg_test_db.py，见 docs/DEVELOPMENT.md）",
        )
    if not is_isolated_test_database(database):
        allowed = " / ".join(sorted(ISOLATED_TEST_DATABASES))
        return PgTestGate(
            False,
            f"{ENV_VAR} 库名 {normalized} 不在隔离测试库白名单"
            f"（{allowed} 及其 `<名>_` 前缀）——已跳过",
        )
    return PgTestGate(
        True, f"{ENV_VAR} -> 隔离测试库 {normalized}", url=url.strip(), database=normalized
    )


def pg_test_gate_from_env(env: Mapping[str, str] | None = None) -> PgTestGate:
    """从环境变量读取并判定（默认进程环境）。"""
    source = os.environ if env is None else env
    return evaluate_pg_test_url(source.get(ENV_VAR))
