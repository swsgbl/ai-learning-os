"""M11-10 isolated release-check runner: focused tests with injectable fakes.

覆盖面（不跑真实 4 分钟门禁——各步骤以模块级替身注入）：
- CLI 注册与参数解析（argparse 真实进 handler）；
- 工作区/输出 artifacts-temp 护栏（护栏先于一切副作用）；
- 一次性 SQLite 既有即拒绝（保护既有证据）；
- 迁移失败 => 不启临时 API、不写证据、exit 2、消息脱敏；
- 临时 API 启动失败 / /health 限时未就绪 => 稳定 exit 2 + 日志尾部诊断；
- 成功编排 => execution_scope=full 的同一证据契约原子落盘、exit 0/1 分工；
- 环境净化（AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL 剥离 + 隔离口径注入）；
- 关停保证：成功/门禁失败/编排异常路径都关停，terminate->kill 升级与
  kill 失败如实降级 exit 2；
- 证据原子写失败 => 旧报告字节原样、无 .tmp 残留、不打印门禁结论。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ops import release_check_isolated as runner
from app.ops.release_check import CheckResult

#: 密码 marker——任何输出面（stdout/消息/证据）都不得出现。
SECRET = "PROD-PW-9901"
CMD_IDS = [
    "api-lint",
    "web-lint",
    "web-typecheck",
    "web-build",
    "api-test",
    "migration",
    "backup",
]
LIVE_IDS = ["voice", "license", "e2e"]


def _gate_results(
    cmd_statuses: list[str] | None = None,
    live_statuses: list[str] | None = None,
) -> list[CheckResult]:
    """按真实 10 项 id 构造结果（缺省全 pass）。"""
    cmd = cmd_statuses or ["pass"] * 7
    live = live_statuses or ["pass"] * 3
    assert len(cmd) == 7 and len(live) == 3
    return [
        CheckResult(cid, cid, status, f"detail {cid}", "command")
        for cid, status in zip(CMD_IDS, cmd, strict=True)
    ] + [
        CheckResult(lid, lid, status, f"detail {lid}", "live")
        for lid, status in zip(LIVE_IDS, live, strict=True)
    ]


class FakeProc:
    """Popen 替身：记录 terminate/kill/wait 调用，可配置顽固程度。"""

    def __init__(self, *, terminate_works: bool = True, kill_works: bool = True):
        self.terminate_works = terminate_works
        self.kill_works = kill_works
        self._exit: int | None = None
        self.terminated = 0
        self.killed = 0
        self.wait_calls = 0

    def poll(self) -> int | None:
        return self._exit

    def terminate(self) -> None:
        self.terminated += 1
        if self.terminate_works:
            self._exit = 0

    def kill(self) -> None:
        self.killed += 1
        if self.kill_works:
            self._exit = 1

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if self._exit is None:
            raise subprocess.TimeoutExpired(cmd="uvicorn", timeout=timeout)
        return self._exit


class _Resp:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}

    def json(self) -> dict:
        return self._body


class FakeClient:
    """httpx client 替身：按脚本回放响应/异常，记录 close。"""

    def __init__(self, script):
        self.script = list(script)
        self.gets = 0
        self.close_calls = 0

    def get(self, path: str):
        self.gets += 1
        step = self.script.pop(0) if self.script else self._fallback()
        if isinstance(step, Exception):
            raise step
        return step

    def _fallback(self):
        return _Resp(503)

    def close(self) -> None:
        self.close_calls += 1


def _patch_green_path(monkeypatch, tmp_path: Path, *, gate_results=None, proc=None):
    """注入全绿路径替身：迁移 ok（并落一次性 DB 文件）-> 端口 -> 进程 -> 健康 -> 门禁。"""
    proc = proc if proc is not None else FakeProc()
    state: dict = {"proc": proc}

    def fake_migrate(db_url, *, base_env=None, **kw):
        db_path = Path(db_url.replace("sqlite+aiosqlite:///", "", 1))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_bytes(b"sqlite-one-off")
        return True, "alembic upgrade head: exit 0"

    def fake_start(port, env, log_path, *, api_dir=None):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("INFO: Uvicorn running\n", encoding="utf-8")
        return state["proc"]

    monkeypatch.setattr(runner, "run_alembic_upgrade", fake_migrate)
    monkeypatch.setattr(runner, "pick_free_port", lambda: 8017)
    monkeypatch.setattr(runner, "start_uvicorn", fake_start)
    monkeypatch.setattr(
        runner, "wait_for_health", lambda port, timeout=60.0, **kw: (True, "ok")
    )
    results = gate_results if gate_results is not None else _gate_results()
    monkeypatch.setattr(
        runner,
        "_run_full_gate",
        lambda api_base, db_url: (
            all(r.status == "pass" for r in results),
            "RESULT: ALL GREEN" if all(r.status == "pass" for r in results)
            else "RESULT: FAILED",
            results,
        ),
    )
    return state


def _safe_workspace(tmp_path: Path, name: str = "ws") -> Path:
    """测试用合法工作区（父目录名 artifacts——is_safe_artifact_path 放行形态）。"""
    return tmp_path / "artifacts" / name


def _must_not_run(name: str):
    def _boom(*a, **kw):  # pragma: no cover - 守卫测试的红线
        raise AssertionError(f"{name} 在护栏拒绝后仍被调用")

    return _boom


def _assert_counts_self_consistent(evidence: dict) -> None:
    assert evidence["passed"] + len(evidence["failed_ids"]) + len(
        evidence["not_executed_ids"]
    ) == evidence["total"]
    if evidence["all_green"]:
        assert evidence["passed"] == evidence["total"]
        assert evidence["failed_ids"] == [] and evidence["not_executed_ids"] == []


# ------------------------------------------ CLI 注册与参数解析 ---------------


def test_cli_handler_present_and_registered() -> None:
    """子命令已注册：handler 存在，源码含 release-check-isolated 注册与分发。"""
    from app.ops import cli as cli_mod

    assert hasattr(cli_mod, "_run_release_check_isolated")
    src = Path(cli_mod.__file__).read_text(encoding="utf-8")
    assert '"release-check-isolated"' in src
    assert "_run_release_check_isolated(args)" in src
    assert "--health-timeout" in src


def test_cli_argparse_parses_all_flags(monkeypatch) -> None:
    """argparse 真实解析：workdir/output/health-timeout/json 进 handler。"""
    from app.ops import cli as cli_mod

    captured: dict = {}

    def fake_run(args) -> int:
        captured.update(vars(args))
        return 0

    monkeypatch.setattr(cli_mod, "_run_release_check_isolated", fake_run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "app.ops.cli",
            "release-check-isolated",
            "--workdir",
            "artifacts/rc-isolated/run-1",
            "--output",
            "artifacts/rc-isolated/run-1/rc.json",
            "--health-timeout",
            "12.5",
            "--json",
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.main()
    assert exc_info.value.code == 0
    assert captured["command"] == "release-check-isolated"
    assert captured["workdir"] == "artifacts/rc-isolated/run-1"
    assert captured["output"] == "artifacts/rc-isolated/run-1/rc.json"
    assert captured["health_timeout"] == 12.5
    assert captured["as_json"] is True


def test_cli_handler_prints_summary_and_returns_code(monkeypatch, capsys) -> None:
    """handler 打印工作区/报告路径/结论并透传退出码；--json 时 stdout 纯 JSON。"""
    from app.ops import cli as cli_mod

    evidence = {"all_green": True, "total": 10, "passed": 10}
    result = runner.IsolatedRunResult(
        exit_code=0,
        summary="REPORT",
        workspace=Path("artifacts/ws"),
        db_path=Path("artifacts/ws/release-check.sqlite"),
        api_base="http://127.0.0.1:8017",
        evidence=evidence,
        evidence_path=Path("artifacts/ws/rc.json"),
        evidence_written=True,
        all_green=True,
        server_stop="terminated",
    )
    monkeypatch.setattr(
        runner, "run_isolated_release_check", lambda **kw: result
    )
    code = cli_mod._run_release_check_isolated(
        SimpleNamespace(
            workdir=None, output=None, health_timeout=60.0, as_json=False
        )
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "隔离工作区" in out and "报告已写入" in out
    assert "REPORT" in out and "production_ready=false" in out

    code = cli_mod._run_release_check_isolated(
        SimpleNamespace(
            workdir=None, output=None, health_timeout=60.0, as_json=True
        )
    )
    assert code == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == evidence  # stdout 纯 JSON
    assert "报告已写入" in captured.err  # 人读提示不污染 JSON 流


# ------------------------------------------ 环境净化 -----------------------


def test_build_isolated_env_strips_inherited_sensitive_vars() -> None:
    """继承的 AUTH_SECRET/AIOS_PG_TEST_URL/DATABASE_URL 一律剥离（先删后设）。"""
    polluted = {
        "AUTH_SECRET": SECRET,
        "AIOS_PG_TEST_URL": f"postgresql://u:{SECRET}@127.0.0.1:5433/db",
        "DATABASE_URL": f"postgresql://u:{SECRET}@prod/db",
        "PATH": "keep-me",
    }
    env = runner.build_isolated_env(polluted, "sqlite+aiosqlite:///one-off.db")
    assert env.get("PATH") == "keep-me"  # 其余继承变量保留（子进程仍需 PATH）
    assert "AUTH_SECRET" not in env and "AIOS_PG_TEST_URL" not in env
    assert env["DATABASE_URL"] == "sqlite+aiosqlite:///one-off.db"  # 覆盖非继承
    assert SECRET not in json.dumps(env)
    assert polluted["AUTH_SECRET"] == SECRET  # 入参（调用方环境）原样不动


def test_build_isolated_env_sets_isolated_surface() -> None:
    """隔离口径显式注入：development / voice local / 仅回环绑定 / 一次性 DB。"""
    env = runner.build_isolated_env({"PATH": "x"}, "sqlite+aiosqlite:///a.db")
    assert env["APP_ENV"] == "development"
    assert env["VOICE_MODE"] == "local"
    assert env["HOST_BIND_IP"] == "127.0.0.1"
    assert env["DATABASE_URL"] == "sqlite+aiosqlite:///a.db"


def test_sqlite_url_is_absolute_and_posix() -> None:
    """一次性 SQLite URL：绝对路径 + POSIX 分隔符（cwd 无关，含空格安全）。"""
    from sqlalchemy.engine import make_url

    db = Path(__file__).resolve() / "does-not-exist" / "one off.sqlite"
    url = runner.sqlite_url(db)
    assert url.startswith("sqlite+aiosqlite:///")
    assert "\\" not in url
    assert make_url(url).database == db.resolve().as_posix()


def test_default_workspace_unique_and_gitignored() -> None:
    """默认工作区每次唯一（一次性语义）且位于 gitignored artifacts 内。"""
    from app.ops.legacy_papers import is_safe_artifact_path

    first = runner.default_workspace()
    second = runner.default_workspace()
    assert first != second
    assert first.parent == second.parent
    assert first.name.startswith("run-")
    assert is_safe_artifact_path(first)


def test_pick_free_port_returns_usable_loopback_port() -> None:
    import socket

    port = runner.pick_free_port()
    assert isinstance(port, int) and 0 < port < 65536
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))  # 选中即应可绑定（回环空闲）


# ------------------------------------------ 工作区/输出护栏 ------------------


def test_workdir_outside_artifacts_refused_before_side_effects(
    tmp_path, monkeypatch
) -> None:
    """工作区不在 gitignored artifacts/temp：exit 2，且不建目录/不迁移/不启服务。"""
    monkeypatch.setattr(runner, "run_alembic_upgrade", _must_not_run("迁移"))
    monkeypatch.setattr(runner, "start_uvicorn", _must_not_run("启动 API"))
    bad = tmp_path / "plain-ws"
    result = runner.run_isolated_release_check(workdir=bad)
    assert result.exit_code == 2
    assert "拒绝工作区" in result.summary
    assert not bad.exists()  # 护栏先于副作用：目录都未创建


def test_output_outside_artifacts_refused_before_side_effects(
    tmp_path, monkeypatch
) -> None:
    """证据输出越界：exit 2，工作区也未创建（护栏先于一切副作用）。"""
    monkeypatch.setattr(runner, "run_alembic_upgrade", _must_not_run("迁移"))
    ws = _safe_workspace(tmp_path)
    result = runner.run_isolated_release_check(
        workdir=ws, output=tmp_path / "leak.json"
    )
    assert result.exit_code == 2
    assert "拒绝写入" in result.summary
    assert not ws.exists() and not (tmp_path / "artifacts").exists()


def test_existing_db_refused_to_protect_prior_evidence(
    tmp_path, monkeypatch
) -> None:
    """一次性 DB 已存在：拒绝执行（不改写既有证据），迁移零调用。"""
    monkeypatch.setattr(runner, "run_alembic_upgrade", _must_not_run("迁移"))
    ws = _safe_workspace(tmp_path)
    ws.mkdir(parents=True)
    db = ws / runner.DB_FILENAME
    db.write_bytes(b"prior-evidence-bytes")
    result = runner.run_isolated_release_check(workdir=ws)
    assert result.exit_code == 2
    assert "已存在" in result.summary
    assert db.read_bytes() == b"prior-evidence-bytes"  # 既有证据字节原样


def test_symlink_workspace_refused(tmp_path, monkeypatch) -> None:
    """工作区是符号链接：拒绝（防写穿护栏外目标；无特权环境跳过）。"""
    import os

    monkeypatch.setattr(runner, "run_alembic_upgrade", _must_not_run("迁移"))
    real = _safe_workspace(tmp_path, "real")
    real.mkdir(parents=True)
    link = _safe_workspace(tmp_path, "link")
    try:
        os.symlink(real, link, target_is_directory=True)
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    result = runner.run_isolated_release_check(workdir=link)
    assert result.exit_code == 2
    assert "符号链接" in result.summary


# ------------------------------------------ 迁移失败 ------------------------


def test_migration_failure_skips_server_and_evidence(tmp_path, monkeypatch) -> None:
    """迁移失败：exit 2、不启临时 API、不写证据；消息抹凭据。"""
    monkeypatch.setattr(
        runner,
        "run_alembic_upgrade",
        lambda db_url, *, base_env=None, **kw: (
            False,
            f"FAILED: conn postgresql+asyncpg://aios:{SECRET}@127.0.0.1:5433/db",
        ),
    )
    monkeypatch.setattr(runner, "start_uvicorn", _must_not_run("启动 API"))
    monkeypatch.setattr(runner, "_run_full_gate", _must_not_run("门禁"))
    ws = _safe_workspace(tmp_path)
    result = runner.run_isolated_release_check(workdir=ws)
    assert result.exit_code == 2
    assert "迁移失败" in result.summary
    assert SECRET not in result.summary and "***" in result.summary  # 凭据已抹
    assert result.evidence is None and not result.evidence_written
    assert result.evidence_path is not None and not result.evidence_path.exists()
    assert not (ws / runner.UVICORN_LOG_FILENAME).exists()  # API 从未启动
    assert result.server_stop == ""  # 无进程需要关停


# ------------------------------------------ 临时 API 启动/健康失败 -----------


def test_server_start_failure_is_stable_exit_2(tmp_path, monkeypatch) -> None:
    """uvicorn 启动异常（OSError）：稳定 exit 2、无 traceback、不写证据。"""
    def fake_migrate(db_url, *, base_env=None, **kw):
        return True, "ok"

    def boom_start(port, env, log_path, *, api_dir=None):
        raise OSError(2, "no such executable")

    monkeypatch.setattr(runner, "run_alembic_upgrade", fake_migrate)
    monkeypatch.setattr(runner, "pick_free_port", lambda: 8017)
    monkeypatch.setattr(runner, "start_uvicorn", boom_start)
    ws = _safe_workspace(tmp_path)
    result = runner.run_isolated_release_check(workdir=ws)
    assert result.exit_code == 2
    assert "启动失败" in result.summary and "FileNotFoundError" in result.summary
    assert result.evidence is None
    assert not result.evidence_path.exists()


def test_health_timeout_stops_server_and_redacts_log(tmp_path, monkeypatch) -> None:
    """/health 限时未就绪：不执行门禁、不写证据、关停进程、日志尾部脱敏诊断。"""
    proc = FakeProc()

    def fake_migrate(db_url, *, base_env=None, **kw):
        return True, "ok"

    def fake_start(port, env, log_path, *, api_dir=None):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            "INFO: Started server process\n"
            f"ERROR: db postgresql://aios:{SECRET}@127.0.0.1:5433/db\n",
            encoding="utf-8",
        )
        return proc

    monkeypatch.setattr(runner, "run_alembic_upgrade", fake_migrate)
    monkeypatch.setattr(runner, "pick_free_port", lambda: 8017)
    monkeypatch.setattr(runner, "start_uvicorn", fake_start)
    monkeypatch.setattr(
        runner,
        "wait_for_health",
        lambda port, timeout=60.0, **kw: (False, "/health 在 5s 内未就绪（10 次尝试）"),
    )
    monkeypatch.setattr(runner, "_run_full_gate", _must_not_run("门禁"))
    ws = _safe_workspace(tmp_path)
    result = runner.run_isolated_release_check(workdir=ws, health_timeout=5.0)
    assert result.exit_code == 2
    assert "未就绪" in result.summary and "uvicorn 日志尾部" in result.summary
    assert SECRET not in result.summary and "***" in result.summary  # 日志尾部已抹凭据
    assert result.evidence is None and not result.evidence_path.exists()
    assert proc.terminated == 1 and proc.killed == 0  # 已干净关停
    assert result.server_stop == "terminated"


# ------------------------------------------ 成功编排与证据语义 ---------------


def test_success_all_green_writes_full_evidence(tmp_path, monkeypatch) -> None:
    """全绿路径：exit 0、execution_scope=full 同一契约原子落盘、进程关停、
    一次性 DB 保留本地审计。"""
    proc = FakeProc()
    _patch_green_path(monkeypatch, tmp_path, proc=proc)
    ws = _safe_workspace(tmp_path)
    result = runner.run_isolated_release_check(workdir=ws)
    assert result.exit_code == 0 and result.all_green is True
    # 证据契约与既有 release-check 完全同源（full 语义，10 项全真实 pass）
    assert result.evidence is not None
    evidence = result.evidence
    assert evidence["execution_scope"] == "full"
    assert evidence["gate"] == "release-check" and evidence["step"] == "release-check"
    assert evidence["all_green"] is True and evidence["total"] == 10
    assert evidence["failed_ids"] == [] and evidence["not_executed_ids"] == []
    _assert_counts_self_consistent(evidence)
    # 落盘文件与 result.evidence 一致；默认输出路径在工作区内
    assert result.evidence_path == ws / runner.EVIDENCE_FILENAME
    assert json.loads(result.evidence_path.read_text(encoding="utf-8")) == evidence
    # 一次性 DB 与 uvicorn 日志保留（本地审计，不入 git）
    assert result.db_path is not None and result.db_path.exists()
    assert (ws / runner.UVICORN_LOG_FILENAME).exists()
    # 临时 API 已关停且进程已回收（无残留）
    assert proc.terminated == 1 and proc.killed == 0
    assert proc.poll() is not None
    assert result.server_stop == "terminated"
    assert result.port == 8017 and result.api_base == "http://127.0.0.1:8017"
    assert "production_ready=false" in result.summary  # 边界随结论一起输出


def test_gate_failure_writes_evidence_exit_1(tmp_path, monkeypatch) -> None:
    """门禁真实 fail：exit 1、证据照常落盘（execution_scope=full 不降级）、
    临时 API 仍被关停（失败路径同样保证关停）。"""
    proc = FakeProc()
    results = _gate_results(cmd_statuses=["pass", "fail"] + ["pass"] * 5)
    _patch_green_path(monkeypatch, tmp_path, gate_results=results, proc=proc)
    ws = _safe_workspace(tmp_path)
    result = runner.run_isolated_release_check(workdir=ws)
    assert result.exit_code == 1 and result.all_green is False
    assert result.evidence_written and result.evidence is not None
    assert result.evidence["execution_scope"] == "full"  # 仍按 full 如实记录
    assert result.evidence["failed_ids"] == ["web-lint"]
    assert result.evidence["not_executed_ids"] == []  # full 模式无缺席项
    _assert_counts_self_consistent(result.evidence)
    assert json.loads(result.evidence_path.read_text(encoding="utf-8"))["all_green"] is False
    assert proc.terminated == 1  # 门禁失败也关停
    assert "RESULT: FAILED" in result.summary or "FAILED" in result.summary


def test_orchestrator_passes_sanitized_env_to_server(tmp_path, monkeypatch) -> None:
    """编排层送进临时 API 的环境：调用方污染面被剥离、隔离口径注入。"""
    captured: dict = {}

    def fake_start(port, env, log_path, *, api_dir=None):
        captured.update(env)
        return FakeProc()

    _patch_green_path(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "start_uvicorn", fake_start)
    polluted = {
        "AUTH_SECRET": SECRET,
        "AIOS_PG_TEST_URL": f"postgresql://u:{SECRET}@127.0.0.1:5433/ai_learning_os_test",
        "DATABASE_URL": f"postgresql://u:{SECRET}@prod/ai_learning_os",
        "PATH": "venv-bin",
    }
    ws = _safe_workspace(tmp_path)
    result = runner.run_isolated_release_check(workdir=ws, base_env=polluted)
    assert result.exit_code == 0
    assert captured.get("APP_ENV") == "development"
    assert captured.get("VOICE_MODE") == "local"
    assert captured.get("HOST_BIND_IP") == "127.0.0.1"
    assert captured["DATABASE_URL"].startswith("sqlite+aiosqlite:///")  # 一次性 SQLite
    assert "AUTH_SECRET" not in captured and "AIOS_PG_TEST_URL" not in captured
    assert SECRET not in json.dumps(captured)
    assert polluted["DATABASE_URL"].startswith("postgresql")  # 调用方环境未被改动


# ------------------------------------------ 关停保证 ------------------------


def test_stop_uvicorn_terminate_reaps_cleanly() -> None:
    """正常关停：terminate 一次即回收，返回 terminated，kill 零调用。"""
    proc = FakeProc()
    assert runner.stop_uvicorn(proc) == "terminated"
    assert proc.terminated == 1 and proc.killed == 0 and proc.poll() is not None


def test_stop_uvicorn_escalates_to_kill() -> None:
    """terminate 不凑效：升级 kill 并回收，返回 killed。"""
    proc = FakeProc(terminate_works=False, kill_works=True)
    assert runner.stop_uvicorn(proc, grace=0.1, kill_wait=0.1) == "killed"
    assert proc.terminated == 1 and proc.killed == 1 and proc.poll() is not None


def test_stop_uvicorn_already_exited_is_noop() -> None:
    """进程已自行退出：不 terminate，如实返回 already-exited。"""
    proc = FakeProc()
    proc.terminate()  # 使 poll() 非 None
    assert runner.stop_uvicorn(proc) == "already-exited"
    assert proc.terminated == 1  # 仅上面手动那次，stop 未再调用


def test_stop_uvicorn_kill_failure_raises_honestly() -> None:
    """kill 后仍不退出：如实抛错请人工核查（不装作干净关停）。"""
    proc = FakeProc(terminate_works=False, kill_works=False)
    with pytest.raises(RuntimeError, match="人工核查"):
        runner.stop_uvicorn(proc, grace=0.1, kill_wait=0.1)


def test_gate_exception_still_shuts_down_server(tmp_path, monkeypatch) -> None:
    """门禁抛异常：exit 2、无证据、消息脱敏、临时 API 仍被关停。"""
    proc = FakeProc()
    _patch_green_path(monkeypatch, tmp_path, proc=proc)

    def boom_gate(api_base, db_url):
        raise RuntimeError(f"client blew up postgresql://u:{SECRET}@h/db")

    monkeypatch.setattr(runner, "_run_full_gate", boom_gate)
    ws = _safe_workspace(tmp_path)
    result = runner.run_isolated_release_check(workdir=ws)
    assert result.exit_code == 2
    assert "意外失败" in result.summary
    assert SECRET not in result.summary and "***" in result.summary
    assert result.evidence is None and not result.evidence_path.exists()
    assert proc.terminated == 1 and result.server_stop == "terminated"


def test_shutdown_failure_downgrades_exit_code(tmp_path, monkeypatch) -> None:
    """关停失败：即使门禁全绿也如实降级 exit 2 并提示人工核查残留进程。"""
    proc = FakeProc(terminate_works=False, kill_works=False)
    _patch_green_path(monkeypatch, tmp_path, proc=proc)

    def failing_stop(p, **kw):
        raise RuntimeError("kill 后临时 API 进程仍未退出（可能残留，请人工核查）")

    monkeypatch.setattr(runner, "stop_uvicorn", failing_stop)
    ws = _safe_workspace(tmp_path)
    result = runner.run_isolated_release_check(workdir=ws)
    assert result.exit_code == 2  # 全绿也被关停失败如实降级
    assert "关停失败" in result.summary and "人工核查" in result.summary
    assert result.evidence_written  # 门禁证据照常（gate 确实跑完并落盘）


# ------------------------------------------ 证据原子写 ----------------------


def test_evidence_write_failure_keeps_old_report(tmp_path, monkeypatch) -> None:
    """证据写入失败：exit 2、旧报告字节原样、无 .tmp 残留、不打印门禁结论。"""
    _patch_green_path(monkeypatch, tmp_path)
    ws = _safe_workspace(tmp_path)
    ws.mkdir(parents=True)
    target = ws / runner.EVIDENCE_FILENAME
    target.write_text('{"old": true}', encoding="utf-8")

    def boom_write(path, text):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(runner, "_write_evidence_atomic", boom_write)
    result = runner.run_isolated_release_check(workdir=ws)
    assert result.exit_code == 2
    assert "证据写入失败" in result.summary
    assert "REPORT" not in result.summary  # 不打印门禁结论（防误读）
    assert target.read_text(encoding="utf-8") == '{"old": true}'
    assert list(ws.glob("*.tmp")) == []  # 无 .tmp 残留
    assert result.evidence is None and not result.evidence_written


# ------------------------------------------ /health 等待 -------------------


def test_wait_for_health_ok_on_first_probe() -> None:
    """就绪即返回：1 次尝试成功；注入 client 不被关闭（属主另有其人）。"""
    client = FakeClient([_Resp(200, {"status": "ok"})])
    ok, msg = runner.wait_for_health(8017, 10.0, client=client, sleep=lambda s: None)
    assert ok and "1 次尝试" in msg
    assert client.gets == 1 and client.close_calls == 0


def test_wait_for_health_retries_then_succeeds() -> None:
    """启动期连接被拒后恢复：如实重试，成功消息含尝试次数。"""
    client = FakeClient(
        [
            ConnectionError("[WinError 10061] 连接被拒"),
            _Resp(503),
            _Resp(200, {"status": "ok"}),
        ]
    )
    ok, msg = runner.wait_for_health(8017, 10.0, client=client, sleep=lambda s: None)
    assert ok and "3 次尝试" in msg


def test_wait_for_health_timeout_reports_diagnostics() -> None:
    """限时耗尽：失败消息含尝试次数与最后错误（脱敏）。"""
    client = FakeClient([ConnectionError("refused postgresql://u:x@h/db")])
    ticks = iter([0.0, 0.0, 1.0, 1.0, 2.0, 99.0])  # 第 3 轮后越过 deadline

    ok, msg = runner.wait_for_health(
        8017, 1.0, client=client, sleep=lambda s: None, clock=lambda: next(ticks)
    )
    assert not ok
    assert "未就绪" in msg and "ConnectionError" in msg
    assert SECRET not in msg and "x@h" not in msg  # 凭据形态已抹


def test_wait_for_health_rejects_wrong_body() -> None:
    """HTTP 200 但 body 非 ok：不当作就绪，诊断指明 body status。"""
    client = FakeClient([_Resp(200, {"status": "degraded"})])
    ticks = iter([0.0, 0.0, 1.0, 1.0, 2.0, 99.0])
    ok, msg = runner.wait_for_health(
        8017, 1.0, client=client, sleep=lambda s: None, clock=lambda: next(ticks)
    )
    assert not ok
    assert "status='degraded'" in msg


# ------------------------------------------ 日志尾部脱敏 --------------------


def test_uvicorn_log_tail_redacts_credentials(tmp_path) -> None:
    log = tmp_path / "uvicorn.log"
    log.write_text(
        "INFO: line1\nERROR: postgresql://aios:" + SECRET + "@h/db\n", encoding="utf-8"
    )
    tail = runner.uvicorn_log_tail(log)
    assert SECRET not in tail and "***" in tail
    assert runner.uvicorn_log_tail(tmp_path / "missing.log") == "(uvicorn 日志不可读)"