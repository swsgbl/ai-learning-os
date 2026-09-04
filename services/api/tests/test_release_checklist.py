"""M7-05 Release checklist: executable release gate summary.

Acceptance (backlog M7-05): lint / typecheck / test / build / E2E /
migration / backup / voice / license all green.

Three guard layers:
1. coverage guard - the nine acceptance words map to real check ids
   (no missing item, no phantom item);
2. command assembly - local checks invoke the same commands as CI
   (ruff / npm lint / tsc / next build / pytest / alembic / backup CLI);
3. orchestration + live checks - run_release_check aggregates pass/fail,
   live items (E2E walkthrough / voice / license) execute against a real
   TestClient app; a missing client is an honest fail, never a fake green.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.ops.release_check import (
    ACCEPTANCE_COVERAGE,
    DEFAULT_LIVE_CHECKS,
    CommandCheck,
    check_e2e,
    check_license,
    check_migration_current,
    check_voice,
    default_command_checks,
    run_release_check,
    summarize,
)

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
#: 隔离测试库 URL——release_check 门控唯一放行注入 api-test 的形态。
ISOLATED_TEST_DB_URL = "postgresql+asyncpg://aios:aios@127.0.0.1:5433/ai_learning_os_test"


def _all_check_ids() -> set[str]:
    cmds, lives = (
        default_command_checks(),
        DEFAULT_LIVE_CHECKS,
    )
    return {c.id for c in cmds} | {lc.id for lc in lives}


def test_acceptance_nine_words_covered() -> None:
    """九个验收字面每一项都映射到真实检查项（无漏项、无虚设项）。"""
    ids = _all_check_ids()
    covered: set[str] = set()
    for word, check_ids in ACCEPTANCE_COVERAGE.items():
        assert check_ids, f"acceptance word {word} maps to no check"
        unknown = set(check_ids) - ids
        assert not unknown, f"acceptance word {word} maps to phantom checks: {unknown}"
        covered.add(word)
    assert covered == set(ACCEPTANCE_COVERAGE), "coverage map drifted"


def test_command_checks_match_ci_commands() -> None:
    """本地命令与 CI 同构：ruff/eslint/tsc/next build/pytest/alembic/backup。"""
    checks = {c.id: c for c in default_command_checks()}
    assert checks["api-lint"].argv[-2:] == ("check", ".")
    assert "ruff" in checks["api-lint"].argv
    assert checks["web-lint"].argv == ("npm", "run", "lint")
    assert checks["web-typecheck"].argv == ("npm", "run", "typecheck")
    assert checks["web-build"].argv == ("npm", "run", "build")
    assert checks["api-test"].argv[-1] == "-q"
    assert "pytest" in checks["api-test"].argv
    assert checks["migration"].argv[-2:] == ("upgrade", "head")
    assert "alembic" in checks["migration"].argv
    b = checks["backup"]
    assert "backup" in b.argv and "--out" in b.argv
    # cwd 真实存在（services/api 与 apps/web）
    for c in checks.values():
        assert c.cwd.exists(), f"cwd missing: {c.cwd}"
    # --db-url: only a gate-approved isolated test DB reaches api-test's env;
    # backup keeps forwarding the raw value as its --db-url argument
    gated = {c.id: c for c in default_command_checks(db_url=ISOLATED_TEST_DB_URL)}
    assert gated["api-test"].env.get("AIOS_PG_TEST_URL") == ISOLATED_TEST_DB_URL
    assert ISOLATED_TEST_DB_URL in gated["backup"].argv


def test_live_checks_present() -> None:
    ids = {lc.id for lc in DEFAULT_LIVE_CHECKS}
    assert {"e2e", "voice", "license"} <= ids


def test_run_release_check_orchestration_all_green() -> None:
    """假 execute 全 ok -> 10 项全 pass，live 用真实 TestClient app 全过。"""
    cmd_checks = [
        CommandCheck("a", "A", ("true",), Path(".")),
        CommandCheck("b", "B", ("false",), Path(".")),
    ]

    def fake_execute(check: CommandCheck) -> tuple[bool, str]:
        return True, "ok"

    with TestClient(create_app(SQLITE_URL)) as client:
        results = run_release_check(
            cmd_checks, DEFAULT_LIVE_CHECKS, execute=fake_execute, client=client
        )
    by_id = {r.id: r for r in results}
    assert {r.status for r in results} == {"pass"}
    assert by_id["e2e"].kind == "live"
    assert "walkthrough" in by_id["e2e"].detail


def test_run_release_check_failure_is_honest() -> None:
    """一项命令 fail -> 该项透出且整体不绿；live 缺 client -> fail 不虚报。"""
    cmd_checks = [
        CommandCheck("good", "G", ("true",), Path(".")),
        CommandCheck("bad", "B", ("true",), Path(".")),
    ]

    def fake_execute(check: CommandCheck) -> tuple[bool, str]:
        if check.id == "bad":
            return False, "exit 1 boom"
        return True, "ok"

    results = run_release_check(cmd_checks, DEFAULT_LIVE_CHECKS, execute=fake_execute, client=None)
    by_id = {r.id: r for r in results}
    assert by_id["good"].status == "pass"
    assert by_id["bad"].status == "fail"
    assert "exit 1 boom" in by_id["bad"].detail
    for lid in ("voice", "license", "e2e"):
        assert by_id[lid].status == "fail"
        assert "需要运行中的 API" in by_id[lid].detail
    all_green, report = summarize(results)
    assert not all_green
    assert "FAILED" in report and "bad" in report


def test_summarize_all_green_format() -> None:
    results = run_release_check([], None)
    all_green, report = summarize(results)
    assert all_green and results == []
    assert "ALL GREEN" in report


def test_check_migration_current_alembic_injectable() -> None:
    """migration 第二步 current 对账：alembic 可注入，不一致如实 fail。"""

    class _FakeAlembic:
        def __init__(self, heads: str, current: str) -> None:
            self._heads, self._current = heads, current

        def __call__(self, argv, **kw):
            out = self._heads if argv[3] == "heads" else self._current

            class P:
                stdout = out
                stderr = ""
                returncode = 0

            return P()

    ok_case = _FakeAlembic("0046_merge (head)\n", "0046_merge\n")
    assert "==" in check_migration_current(Path("."), alembic=ok_case)
    bad_case = _FakeAlembic("0046_merge (head)\n", "\n")
    try:
        check_migration_current(Path("."), alembic=bad_case)
    except AssertionError as exc:
        assert "current=(none)" in str(exc)
    else:
        raise AssertionError("stale current must fail")


def test_check_voice_live() -> None:
    """voice 项真实执行：providers 视图 + synthesize 音频头。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        detail = check_voice(client)
    assert "voice_mode=" in detail and "audio/wav" in detail


def test_check_license_live() -> None:
    """license 项真实执行：report 200 + 四区段。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        detail = check_license(client)
    assert "api deps" in detail


def test_check_license_missing_section_is_fail() -> None:
    """缺区段的 report 如实 fail（不虚报四区段齐全）。"""

    class _BrokenClient:
        def get(self, path: str):
            class R:
                status_code = 200

                def json(self):
                    return {"dependencies": {}, "models": []}

            return R()

    try:
        check_license(_BrokenClient())
    except AssertionError as exc:
        assert "content_sources" in str(exc)
    else:
        raise AssertionError("missing sections must fail")


def test_e2e_check_runs_real_walkthrough() -> None:
    """E2E 项 = M7-02 onboarding 全路径真实执行（非 mock）。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        detail = check_e2e(client)
    assert "steps" in detail


def test_cli_release_check_registered() -> None:
    """CLI 子命令已注册：--api-base/--db-url/--local-only 三参数可解析。"""
    from app.ops import cli as cli_mod

    assert hasattr(cli_mod, "_run_release_check")
    src = Path(cli_mod.__file__).read_text(encoding="utf-8")
    for kw in ("api-base", "db-url", "local-only"):
        assert kw in src, kw
    assert "release-check" in src


def test_api_test_env_isolates_caller_pollution() -> None:
    """调用方 shell 带了 DATABASE_URL 时，api-test 子进程必须拿不到它（None=删除语义）。

    终验暴露的真实缺陷：pytest 继承调用方 DATABASE_URL 后，「无 DB」测试
    预期 503 却拿到 201，24 测误报失败。env 分离必须对调用方环境鲁棒。
    api-test 能看到的唯一 DB URL 是门控放行后注入的 AIOS_PG_TEST_URL。
    """
    import os

    from app.ops.release_check import default_command_checks

    checks = default_command_checks(db_url=ISOLATED_TEST_DB_URL)
    api_test = next(c for c in checks if c.id == "api-test")
    assert api_test.env["DATABASE_URL"] is None  # 删除语义：调用方污染被剥离
    assert api_test.env["AIOS_PG_TEST_URL"] == ISOLATED_TEST_DB_URL
    # migration/backup 仍需真实 DATABASE_URL（非 None）
    mig = next(c for c in checks if c.id == "migration")
    assert mig.env["DATABASE_URL"] == ISOLATED_TEST_DB_URL
    assert os.environ.get("_AIOS_SENTINEL_") is None  # sanity: 不改全局


# ------------------------------------------ M10-04: PG test URL 安全门控 --

#: 必须被拒绝注入 api-test 的 db_url 形态（fail-closed；pytest 侧门控同源）。
UNSAFE_DB_URLS = {
    "main-database": "postgresql+asyncpg://aios:aios@127.0.0.1:5433/ai_learning_os",
    "missing-database": "postgresql+asyncpg://aios:aios@127.0.0.1:5433",
    "non-postgres-driver": "mysql+pymysql://u:p@localhost/ai_learning_os_test",
    "unparsable-url": ":://x",
}


def test_isolated_test_db_url_reaches_api_test_env() -> None:
    """门控放行隔离测试库 -> gate.url 注入 AIOS_PG_TEST_URL；同 URL 仍供
    migration/backup 的 DATABASE_URL 运行时检查。"""
    checks = {c.id: c for c in default_command_checks(db_url=ISOLATED_TEST_DB_URL)}
    assert checks["api-test"].env.get("AIOS_PG_TEST_URL") == ISOLATED_TEST_DB_URL
    assert checks["migration"].env["DATABASE_URL"] == ISOLATED_TEST_DB_URL
    assert checks["backup"].env["DATABASE_URL"] == ISOLATED_TEST_DB_URL


@pytest.mark.parametrize(
    "db_url", list(UNSAFE_DB_URLS.values()), ids=list(UNSAFE_DB_URLS)
)
def test_unsafe_db_url_never_reaches_api_test_env(db_url: str) -> None:
    """门控拒绝的 URL 一律不得注入 api-test 的 AIOS_PG_TEST_URL。

    M10-04 事故重演防线：release_check 自身也不得把主库 URL 递给 pytest。
    None（删除）语义同时剥离从调用方 shell 继承的值；被拒 URL 不得以任何
    形式出现在检查 env 里（不回显完整 URL/凭据）。migration/backup 的
    DATABASE_URL 不受测试门控影响，仍传原 URL。
    """
    checks = {c.id: c for c in default_command_checks(db_url=db_url)}
    api_test = checks["api-test"]
    assert api_test.env.get("AIOS_PG_TEST_URL") is None
    assert not any(isinstance(v, str) and db_url in v for v in api_test.env.values())
    assert checks["migration"].env["DATABASE_URL"] == db_url
    assert checks["backup"].env["DATABASE_URL"] == db_url


def test_no_db_url_keeps_api_test_env_clean() -> None:
    """未传 db_url：api-test 既不设 AIOS_PG_TEST_URL 也不继承 DATABASE_URL；
    migration/backup 无 DATABASE_URL 可传。"""
    checks = {c.id: c for c in default_command_checks()}
    env = checks["api-test"].env
    assert env.get("AIOS_PG_TEST_URL") is None
    assert env.get("DATABASE_URL") is None
    assert checks["migration"].env.get("DATABASE_URL") is None
    assert checks["backup"].env.get("DATABASE_URL") is None
