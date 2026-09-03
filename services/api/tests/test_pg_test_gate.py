"""M10-04 返工：AIOS_PG_TEST_URL 安全门控回归。

事故：AIOS_PG_TEST_URL 指向共享主库 5433/ai_learning_os 跑全量 pytest，
每轮写入 8 张「PG 验证卷」（6 批共 48 张）永久污染主库。

锁定语义：
- 主/共享库名在任意主机/端口/驱动/大小写下都被拒绝，且拒绝时拿不到 URL
  （物理上无法连接）；
- 隔离测试库名（ai_learning_os_test / ai_learning_os_drill 及 <名>_ 前缀）放行；
- 同名前缀边界（ai_learning_os_testx 等）不误放行；
- 缺库名 / 空 / 坏 URL / 非 PG 驱动 fail-closed；
- 端到端：子进程 pytest 在主库名下模块全 skip（零连接），隔离库名下真实执行。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from app.db.test_gate import (
    ENV_VAR,
    evaluate_pg_test_url,
    is_isolated_test_database,
    pg_test_gate_from_env,
)

API_DIR = Path(__file__).resolve().parents[1]
PG_PREFIX = "postgresql+asyncpg://aios:aios@127.0.0.1:5433/"


def test_main_database_rejected_on_any_host_port_and_case() -> None:
    """主/共享库名无论怎么写都被拒，且 url=None（拿不到连接串）。"""
    urls = [
        PG_PREFIX + "ai_learning_os",  # 本机主库（事故现场）
        "postgresql+asyncpg://aios:aios@localhost:5432/ai_learning_os",
        "postgresql+asyncpg://aios:aios@localhost:5432/ai_learning_os?sslmode=disable",
        "postgresql://aios:aios@db.internal.example:6000/ai_learning_os",
        PG_PREFIX + "AI_Learning_OS",  # 大小写变体
        PG_PREFIX + " ai_learning_os ",  # 空白变体
        PG_PREFIX + "postgres",  # 维护库同样禁止作测试目标
    ]
    for url in urls:
        gate = evaluate_pg_test_url(url)
        assert not gate.enabled, f"主库 URL 不应放行: {url}"
        assert gate.url is None, f"被拒 URL 不得透出连接串: {url}"
        assert gate.database is None
        assert "已跳过" in gate.reason


def test_isolated_test_databases_allowed() -> None:
    for name in (
        "ai_learning_os_test",
        "ai_learning_os_drill",  # backup drill 自管库
        "ai_learning_os_test_2",
        "ai_learning_os_test_run42",
    ):
        gate = evaluate_pg_test_url(PG_PREFIX + name)
        assert gate.enabled, f"隔离测试库应放行: {name}"
        assert gate.url == PG_PREFIX + name
        assert gate.database == name
        assert name in gate.reason


def test_same_prefix_boundary_not_confused() -> None:
    """同名前缀的近亲库名不误放行（白名单是精确名 + `<名>_` 前缀）。"""
    for name in (
        "ai_learning_os",  # 主库本体
        "ai_learning_os_testx",
        "ai_learning_os_tests",
        "ai_learning_os_testing",
        "ai_learning_os_prod",
        "ai_learning_os_drills",
        "mydb",
    ):
        assert not is_isolated_test_database(name), name
        gate = evaluate_pg_test_url(PG_PREFIX + name)
        assert not gate.enabled, name
        if name != "ai_learning_os":
            assert "白名单" in gate.reason


def test_missing_or_invalid_url_fail_closed() -> None:
    for url in (None, "", "   "):
        gate = evaluate_pg_test_url(url)
        assert not gate.enabled and gate.url is None
    # 缺库名：裸 host 与尾斜杠都解析不出 database
    for url in ("postgresql+asyncpg://aios:aios@127.0.0.1:5433",
                "postgresql+asyncpg://aios:aios@127.0.0.1:5433/",
                "postgresql+asyncpg://"):
        gate = evaluate_pg_test_url(url)
        assert not gate.enabled, url
        assert gate.url is None
    # 坏 URL 不抛异常，直接拒绝
    for url in ("not a url", ":://x", "::::"):
        gate = evaluate_pg_test_url(url)
        assert not gate.enabled and gate.url is None
        assert "fail-closed" in gate.reason or "跳过" in gate.reason


def test_non_postgres_driver_rejected() -> None:
    for url in ("sqlite:///foo.db", "sqlite+aiosqlite:///:memory:",
                "mysql+pymysql://u:p@h/db_test"):
        gate = evaluate_pg_test_url(url)
        assert not gate.enabled, url
        assert "PostgreSQL" in gate.reason


def test_env_helper_reads_gate_variable(monkeypatch) -> None:
    monkeypatch.setenv(ENV_VAR, PG_PREFIX + "ai_learning_os")
    blocked = pg_test_gate_from_env()
    assert not blocked.enabled and "主/共享库" in blocked.reason

    monkeypatch.setenv(ENV_VAR, PG_PREFIX + "ai_learning_os_test")
    allowed = pg_test_gate_from_env()
    assert allowed.enabled and allowed.database == "ai_learning_os_test"

    monkeypatch.delenv(ENV_VAR, raising=False)
    missing = pg_test_gate_from_env()
    assert not missing.enabled and "未设置" in missing.reason


def test_reason_never_echoes_credentials() -> None:
    """拒绝原因不得回显完整 URL（防凭据进日志）。"""
    secret_url = "postgresql+asyncpg://aios:super-secret@127.0.0.1:5433/ai_learning_os"
    gate = evaluate_pg_test_url(secret_url)
    assert not gate.enabled
    assert "super-secret" not in gate.reason
    assert gate.url is None


def _run_pytest_module(env_url: str) -> subprocess.CompletedProcess[str]:
    """子进程跑 PG 集成模块；端口故意用不可达的 59999——门控若失效，
    结果是连接失败（错误），绝不会静默写到任何真实库。"""
    env = {**os.environ, ENV_VAR: env_url}
    env.pop("DATABASE_URL", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_pg_integration.py", "-q",
         "--no-header", "-p", "no:cacheprovider"],
        cwd=str(API_DIR), env=env, capture_output=True, text=True, timeout=300,
        check=False,
    )


def test_gated_module_skips_without_connecting_when_url_is_main_database() -> None:
    """端到端：AIOS_PG_TEST_URL 指向主库名 -> 模块全部 skip、退出码 0。

    主库名挂在不可达端口上：若门控失效，测试会因连接失败而报错退出，
    而不是静默写入——双重保险下这个断言不可能掩盖污染路径。
    """
    result = _run_pytest_module(
        "postgresql+asyncpg://aios:aios@127.0.0.1:59999/ai_learning_os"
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    summary = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    skipped = re.search(r"(\d+) skipped", summary)
    passed = re.search(r"(\d+) passed", summary)
    assert skipped and int(skipped.group(1)) >= 10, f"应整模块跳过: {summary!r}"
    assert not passed, f"主库名下不应有测试真实执行: {summary!r}"


def test_gated_module_runs_when_url_is_isolated_test_database() -> None:
    """端到端：隔离库名放行 -> 测试真实执行（不可达端口表现为连接失败，
    证明放行后确实按配置 URL 连接，而不是被静默吞掉）。"""
    result = _run_pytest_module(
        "postgresql+asyncpg://aios:aios@127.0.0.1:59999/ai_learning_os_test"
    )
    assert result.returncode != 0, "隔离库放行后测试应真实执行（此处应连接失败）"
    combined = (result.stdout + result.stderr).lower()
    assert "refused" in combined or "connect" in combined or "error" in combined
