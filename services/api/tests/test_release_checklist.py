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

M11-03 machine-readable evidence export: build_release_check_evidence
assembles the executed results into a JSON contract consumed by
release-readiness (gate field) / cutover-rehearsal (step field). Under
--local-only the live items are honestly not_executed (all_green=false,
passed counts only real passes, failed_ids holds only real fails) - an
absent check is never a pass. CLI --json/--output mirror the shared
artifacts/temp path guard + atomic write semantics.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.ops.release_check import (
    ACCEPTANCE_COVERAGE,
    DEFAULT_LIVE_CHECKS,
    CheckResult,
    CommandCheck,
    build_release_check_evidence,
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


# ------------------------------------------ M11-03: JSON 证据导出 -------------

SECRET_MARKER = "PROD-PW-9901"


def _cmd_results(*statuses: str) -> list[CheckResult]:
    """按 default_command_checks 的顺序构造本地七项结果（假执行）。"""
    specs = default_command_checks()
    assert len(statuses) == len(specs)
    return [
        CheckResult(c.id, c.title, status, f"detail {c.id}", "command")
        for c, status in zip(specs, statuses, strict=True)
    ]


def _live_results(*statuses: str) -> list[CheckResult]:
    return [
        CheckResult(lc.id, lc.title, status, f"detail {lc.id}", "live")
        for lc, status in zip(DEFAULT_LIVE_CHECKS, statuses, strict=True)
    ]


def _assert_counts_self_consistent(evidence: dict) -> None:
    """契约字段自洽：passed + failed + not_executed == total；
    all_green=true 蕴含 passed == total 且 failed/not_executed 为空。"""
    assert evidence["passed"] + len(evidence["failed_ids"]) + len(
        evidence["not_executed_ids"]
    ) == evidence["total"], "计数必须自洽（M11-03 契约）"
    assert evidence["passed"] <= evidence["total"]
    if evidence["all_green"]:
        assert evidence["passed"] == evidence["total"]
        assert evidence["failed_ids"] == [] and evidence["not_executed_ids"] == []
    assert len(evidence["checks"]) == evidence["total"]


def test_build_evidence_all_green_full() -> None:
    """full 模式 10 项全 pass：all_green=true，计数自洽，checks 全量。"""
    evidence = build_release_check_evidence(
        _cmd_results(*["pass"] * 7) + _live_results("pass", "pass", "pass"),
        execution_scope="full",
    )
    assert evidence["gate"] == "release-check" and evidence["step"] == "release-check"
    assert evidence["execution_scope"] == "full"
    assert evidence["all_green"] is True
    assert (evidence["total"], evidence["passed"]) == (10, 10)
    assert evidence["failed_ids"] == [] and evidence["not_executed_ids"] == []
    _assert_counts_self_consistent(evidence)


def test_build_evidence_failure_is_honest() -> None:
    """一项真实 fail：failed_ids 只放真实 fail；passed 只计真实 pass。"""
    evidence = build_release_check_evidence(
        _cmd_results("pass", "fail", "pass", "pass", "pass", "pass", "pass")
        + _live_results("pass", "fail", "pass"),
        execution_scope="full",
    )
    assert evidence["all_green"] is False
    assert evidence["total"] == 10 and evidence["passed"] == 8
    assert set(evidence["failed_ids"]) == {"web-lint", "license"}
    assert evidence["not_executed_ids"] == []
    _assert_counts_self_consistent(evidence)


def test_build_evidence_local_only_never_fakes_green() -> None:
    """--local-only：本地 7 项真实 pass + live 3 项未执行——total 覆盖
    本地 7 + live 3、passed 只统计真实 pass（7）、live 项如实
    not_executed、all_green=false；failed_ids 不放未执行项。"""
    evidence = build_release_check_evidence(
        _cmd_results(*["pass"] * 7),
        skipped_live_checks=DEFAULT_LIVE_CHECKS,
        execution_scope="local-only",
    )
    assert evidence["execution_scope"] == "local-only"
    assert evidence["all_green"] is False  # 缺席不冒充全绿
    assert evidence["total"] == 10 and evidence["passed"] == 7
    assert set(evidence["not_executed_ids"]) == {"e2e", "voice", "license"}
    assert evidence["failed_ids"] == []  # 未执行不是 fail
    live_entries = [c for c in evidence["checks"] if c["kind"] == "live"]
    assert {c["id"] for c in live_entries} == {"e2e", "voice", "license"}
    assert all(c["status"] == "not_executed" for c in live_entries)
    _assert_counts_self_consistent(evidence)


def test_build_evidence_consumed_by_readiness_and_rehearsal() -> None:
    """生成的证据（全绿/失败/local-only 三形态）直接过两个消费方评估器：
    白名单字段可提取、额外字段（not_executed_ids/execution_scope/checks）
    不导致 malformed；全绿 => pass，失败与 local-only => blocked（不伪装）。"""
    from app.ops.cutover_rehearsal import _step_release_check
    from app.ops.evidence_kit import find_embedded_credential, find_sensitive_key
    from app.ops.release_readiness import _eval_release_check

    green = build_release_check_evidence(
        _cmd_results(*["pass"] * 7) + _live_results("pass", "pass", "pass"),
        execution_scope="full",
    )
    failed = build_release_check_evidence(
        _cmd_results("pass", "fail", "pass", "pass", "pass", "pass", "pass")
        + _live_results("pass", "fail", "pass"),
        execution_scope="full",
    )
    local = build_release_check_evidence(
        _cmd_results(*["pass"] * 7),
        skipped_live_checks=DEFAULT_LIVE_CHECKS,
        execution_scope="local-only",
    )
    root = Path(".")
    for evidence in (green, failed, local):
        assert find_sensitive_key(evidence) is None
        assert find_embedded_credential(evidence) is None
        status, _, data, _ = _eval_release_check(evidence, root, {})
        assert data["total"] == evidence["total"]
        status2, _, _, data2, _ = _step_release_check(evidence, root, {})
        assert data2["passed"] == evidence["passed"]
        expected = "pass" if evidence["all_green"] else "blocked"
        assert status == expected and status2 == expected


def test_build_evidence_redacts_embedded_credentials() -> None:
    """detail 内嵌 `://user:pass@` 凭据时导出前抹除（复用 redact_secrets）。"""
    results = _cmd_results(*["fail"] * 7)
    results[0] = CheckResult(
        results[0].id,
        results[0].title,
        "fail",
        f"connect postgresql+asyncpg://aios:{SECRET_MARKER}@127.0.0.1:5433/db",
        "command",
    )
    evidence = build_release_check_evidence(results, execution_scope="full")
    dumped = json.dumps(evidence, ensure_ascii=False)
    assert SECRET_MARKER not in dumped
    assert f"//aios:{SECRET_MARKER}@" not in dumped


@pytest.mark.parametrize("scope", ["local-only", "full"])
def test_generated_evidence_accepted_by_full_manifest_tools(tmp_path, scope) -> None:
    """落盘的生成证据经完整（非裁剪）manifest 工具消费：release-readiness
    的 release-check 门与 cutover-rehearsal 的 release-check 步都给出诚实
    blocked（local-only 与失败形态），绝不 malformed/missing——同一份导出
    文件同时满足两侧的自声明（gate/step）。"""
    from app.ops.cutover_rehearsal import run_cutover_rehearsal
    from app.ops.release_readiness import run_release_readiness

    if scope == "local-only":
        evidence = build_release_check_evidence(
            _cmd_results(*["pass"] * 7),
            skipped_live_checks=DEFAULT_LIVE_CHECKS,
            execution_scope="local-only",
        )
    else:  # full 模式带真实 fail（live license）
        evidence = build_release_check_evidence(
            _cmd_results(*["pass"] * 7) + _live_results("pass", "fail", "pass"),
            execution_scope="full",
        )
    dir_ready = tmp_path / "readiness"
    dir_ready.mkdir()
    (dir_ready / "release-check.json").write_text(
        json.dumps(evidence, ensure_ascii=False), encoding="utf-8"
    )
    readiness = run_release_readiness(dir_ready)
    gate = next(g for g in readiness["gates"] if g["gate"] == "release-check")
    assert gate["status"] == "blocked"  # not_executed/failed 不伪装 pass
    assert gate["data"]["total"] == 10 and gate["data"]["failed_ids"] == (
        ["license"] if scope == "full" else []
    )

    dir_rehearsal = tmp_path / "rehearsal"
    dir_rehearsal.mkdir()
    (dir_rehearsal / "release-check.json").write_text(
        json.dumps(evidence, ensure_ascii=False), encoding="utf-8"
    )
    rehearsal = run_cutover_rehearsal(dir_rehearsal)
    step = next(s for s in rehearsal["steps"] if s["step"] == "release-check")
    assert step["status"] == "blocked"
    assert rehearsal["exit_code"] == 1 and not rehearsal["rehearsal_ready"]


def test_cli_release_check_json_and_output_args_registered(
    monkeypatch, capsys
) -> None:
    """argparse 真实注册：release-check --json/--output 可解析并进入 handler。"""
    from app.ops import cli as cli_mod

    captured: dict = {}

    def fake_run(args) -> int:
        captured.update(vars(args))
        return 0

    monkeypatch.setattr(cli_mod, "_run_release_check", fake_run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "app.ops.cli",
            "release-check",
            "--json",
            "--output",
            "artifacts/release-check.json",
            "--local-only",
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()
    assert exc_info.value.code == 0
    assert captured["command"] == "release-check"
    assert captured["as_json"] is True
    assert captured["output"] == "artifacts/release-check.json"
    assert captured["local_only"] is True
    assert captured["api_base"] == "http://127.0.0.1:8000"


def _fake_release_run(monkeypatch, results: list[CheckResult]) -> None:
    """替换 run_release_check 为假执行（不跑真实子进程/live 请求）。"""
    from app.ops import release_check as rc_mod

    monkeypatch.setattr(
        rc_mod, "run_release_check", lambda *a, **kw: list(results)
    )


def test_cli_local_only_output_writes_honest_evidence(
    tmp_path, monkeypatch, capsys
) -> None:
    """local-only + --output：本地 7 项全过时退出码仍 0（不倒退），但落盘
    证据 all_green=false、live 3 项 not_executed、计数不虚报。"""
    from app.ops import cli as cli_mod

    _fake_release_run(monkeypatch, _cmd_results(*["pass"] * 7))
    safe = tmp_path / "artifacts" / "release-check.json"
    code = cli_mod._run_release_check(
        SimpleNamespace(
            api_base=None,
            db_url=None,
            local_only=True,
            as_json=False,
            output=str(safe),
        )
    )
    assert code == 0  # 退出语义按已执行门禁：本地 7 项全过
    out = capsys.readouterr().out
    assert "报告已写入" in out and "ALL GREEN" in out
    evidence = json.loads(safe.read_text(encoding="utf-8"))
    assert evidence["execution_scope"] == "local-only"
    assert evidence["all_green"] is False  # 证据不伪装全绿
    assert evidence["total"] == 10 and evidence["passed"] == 7
    assert set(evidence["not_executed_ids"]) == {"e2e", "voice", "license"}
    assert evidence["failed_ids"] == []
    _assert_counts_self_consistent(evidence)


def test_cli_json_stdout_matches_written_evidence(tmp_path, monkeypatch, capsys) -> None:
    """--json 与 --output 同用：stdout 是纯 JSON（可管道给 jq，「报告已写入」
    提示走 stderr）且与落盘文件解析后一致（含墙钟 generated_at 逐字相等）。"""
    from app.ops import cli as cli_mod

    _fake_release_run(
        monkeypatch,
        _cmd_results(*["pass"] * 7) + _live_results("pass", "fail", "pass"),
    )
    safe = tmp_path / "temp" / "release-check.json"
    code = cli_mod._run_release_check(
        SimpleNamespace(
            api_base=None, db_url=None, local_only=False, as_json=True, output=str(safe)
        )
    )
    assert code == 1  # live license 一项真实 fail
    captured = capsys.readouterr()
    assert "报告已写入" in captured.err  # 提示不污染 JSON 流
    stdout_evidence = json.loads(captured.out)  # stdout 是纯 JSON
    file_evidence = json.loads(safe.read_text(encoding="utf-8"))
    assert stdout_evidence.pop("generated_at") == file_evidence.pop("generated_at")
    assert stdout_evidence == file_evidence
    assert stdout_evidence["all_green"] is False
    assert stdout_evidence["failed_ids"] == ["license"]
    assert stdout_evidence["not_executed_ids"] == []
    assert stdout_evidence["execution_scope"] == "full"


def test_cli_output_rejects_non_artifact_path(tmp_path, monkeypatch, capsys) -> None:
    """--output 普通路径：exit 2 且不执行任何门禁（护栏先于执行）。"""
    from app.ops import cli as cli_mod
    from app.ops import release_check as rc_mod

    def _must_not_run(*a, **kw):  # pragma: no cover - 守卫测试的红线
        raise AssertionError("输出路径被拒时不得执行门禁")

    monkeypatch.setattr(rc_mod, "run_release_check", _must_not_run)
    bad = tmp_path / "release-check.json"
    assert not bad.parent.name.lower() in ("artifacts", "temp")
    code = cli_mod._run_release_check(
        SimpleNamespace(
            api_base=None, db_url=None, local_only=True, as_json=False, output=str(bad)
        )
    )
    assert code == 2
    assert "拒绝写入" in capsys.readouterr().out
    assert not bad.exists()


def test_cli_output_write_failure_keeps_old_report(
    tmp_path, monkeypatch, capsys
) -> None:
    """原子写失败：稳定 exit 2、无 traceback、旧报告字节原样、无 .tmp 残留、
    不打印门禁结论摘要（防半途报告被误读）。"""
    from app.ops import cli as cli_mod

    _fake_release_run(monkeypatch, _cmd_results(*["pass"] * 7))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    target = artifacts / "release-check.json"
    target.write_text('{"old": true}', encoding="utf-8")

    def boom(path, text):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cli_mod, "_write_report_atomic", boom)
    code = cli_mod._run_release_check(
        SimpleNamespace(
            api_base=None, db_url=None, local_only=True, as_json=False, output=str(target)
        )
    )
    assert code == 2
    captured = capsys.readouterr()
    assert "报告写入失败" in captured.out
    assert captured.err == ""  # 无 traceback
    assert "ALL GREEN" not in captured.out  # 不打印门禁结论
    assert target.read_text(encoding="utf-8") == '{"old": true}'
    assert list(artifacts.glob("*.tmp")) == []  # 无 .tmp 残留


def test_cli_output_symlink_rejected_fail_closed(tmp_path, monkeypatch) -> None:
    """--output 指向 symlink：拒绝写入且不写穿目标（Windows 无特权环境跳过）。"""
    import os

    from app.ops import cli as cli_mod

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    real = artifacts / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = artifacts / "release-check.json"
    try:
        os.symlink(real, link)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    _fake_release_run(monkeypatch, _cmd_results(*["pass"] * 7))
    code = cli_mod._run_release_check(
        SimpleNamespace(
            api_base=None, db_url=None, local_only=True, as_json=False, output=str(link)
        )
    )
    assert code == 2
    assert real.read_text(encoding="utf-8") == "{}", "symlink 目标不得被写穿"
