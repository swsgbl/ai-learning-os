"""M7-05 Release checklist：发布前九项门禁的可执行汇总清单。

验收（backlog M7-05）：lint / typecheck / test / build / E2E / migration /
backup / voice / license 全绿。

两类检查项：
- command 项：本地子进程真实执行（ruff / npm lint / tsc / next build /
  pytest / alembic / backup drill），cwd 相对仓库根，命令与 CI 同构；
- live 项：对运行中的 API 执行（E2E = M7-02 onboarding walkthrough 全路径、
  voice providers+synthesize、license report 四区段）。服务未运行时该项
  如实 fail 并写明原因，不虚报。

run_release_check 为纯编排（execute 可注入），任一项 fail 则整体不绿；
CLI 汇总每项一行状态与退出码。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.ops.version import REPO_ROOT  # M8-00: 同源探测，兼容容器布局

# 验收九字面 -> 检查项 id 的映射（测试用它守卫「无漏项」）
ACCEPTANCE_COVERAGE = {
    "lint": ["api-lint", "web-lint"],
    "typecheck": ["web-typecheck"],
    "test": ["api-test"],
    "build": ["web-build"],
    "E2E": ["e2e"],
    "migration": ["migration"],
    "backup": ["backup"],
    "voice": ["voice"],
    "license": ["license"],
}


@dataclass(frozen=True)
class CommandCheck:
    id: str
    title: str
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str | None] = field(default_factory=dict)  # None=从继承环境删除


@dataclass(frozen=True)
class LiveCheck:
    id: str
    title: str
    run: Callable[[object], str]  # client -> detail；失败抛 AssertionError


@dataclass
class CheckResult:
    id: str
    title: str
    status: str  # pass / fail
    detail: str = ""
    kind: str = ""


def default_command_checks(*, db_url: str | None = None) -> list[CommandCheck]:
    """七项本地命令门禁。命令与 CI 工作流同构。

    migration 为幂等安全形态（upgrade head + current 对账），不跑
    downgrade base——那是 CI 在独立库上的 roundtrip 职责，对业务主库
    是破坏性操作。
    """
    api_dir = REPO_ROOT / "services" / "api"
    web_dir = REPO_ROOT / "apps" / "web"
    test_env: dict[str, str] = {}
    db_env: dict[str, str] = {}
    if db_url:
        test_env["AIOS_PG_TEST_URL"] = db_url  # pytest 门控专用
        test_env["DATABASE_URL"] = None  # 隔离调用方 shell 的 DATABASE_URL（None=删除）
        db_env["DATABASE_URL"] = db_url  # alembic/backup 子进程需要
    python = sys.executable
    return [
        CommandCheck("api-lint", "API lint (ruff)", (python, "-m", "ruff", "check", "."), api_dir),
        CommandCheck("web-lint", "Web lint (eslint)", ("npm", "run", "lint"), web_dir),
        CommandCheck(
            "web-typecheck", "Web typecheck (tsc)", ("npm", "run", "typecheck"), web_dir
        ),
        CommandCheck("web-build", "Web build (next build)", ("npm", "run", "build"), web_dir),
        CommandCheck(
            "api-test", "API tests (pytest)", (python, "-m", "pytest", "-q"), api_dir, test_env
        ),
        CommandCheck(
            "migration",
            "Migration head 对账 (upgrade head 幂等 + current)",
            (python, "-m", "alembic", "upgrade", "head"),
            api_dir,
            db_env,
        ),
        CommandCheck(
            "backup",
            "Backup drill (三件套备份 + manifest 校验)",
            (
                python,
                "-m",
                "app.ops.cli",
                "backup",
                *(["--db-url", db_url] if db_url else []),
                "--out",
                tempfile.mkdtemp(prefix="aios-release-backup-"),
            ),
            api_dir,
            db_env,
        ),
    ]


def check_migration_current(api_dir: Path, alembic="alembic", env: dict | None = None) -> str:
    """migration 项的第二步：upgrade head 后 current 必须等于唯一 head。

    alembic 为可执行名（子进程）或 callable(argv)->process-like（测试注入）；
    env 透传子进程（DATABASE_URL 门控）。
    """
    import os

    def _exec(cmd: str):
        argv = [sys.executable, "-m", "alembic", cmd]
        if callable(alembic):
            return alembic(argv)
        _env = {**os.environ, **(env or {})}
        _env = {k: v for k, v in _env.items() if v is not None}
        return subprocess.run(
            argv, cwd=str(api_dir), capture_output=True, text=True, check=False,
            env=_env,
        )

    heads = _exec("heads")
    current = _exec("current")
    head_rev = next(
        (ln.split()[0] for ln in heads.stdout.splitlines() if ln and ln[0].isalnum()), ""
    )
    cur_rev = next(
        (ln.split()[0] for ln in current.stdout.splitlines() if ln and ln[0].isalnum()), ""
    )
    if not head_rev:
        raise AssertionError(f"alembic heads 无输出: {heads.stdout!r} {heads.stderr!r}")
    if cur_rev != head_rev:
        raise AssertionError(f"current={cur_rev or '(none)'} != head={head_rev}")
    return f"current == head == {head_rev}"


# ---------------------------------------------------------------- live 项 --


def check_voice(client) -> str:
    """运行中服务：providers 视图 + synthesize 真实音频。"""
    r = client.get("/api/v1/voice/providers")
    assert r.status_code == 200, f"providers {r.status_code}: {r.text}"
    mode = r.json()["voice_mode"]
    s = client.post(
        "/api/v1/voice/synthesize",
        json={"text": "发布前语音链路检查"},
        headers={"Content-Type": "application/json"},
    )
    assert s.status_code == 200, f"synthesize {s.status_code}: {s.text}"
    assert s.headers.get("content-type", "").startswith("audio/"), s.headers
    return f"voice_mode={mode}, synthesize audio/wav ({len(s.content)} bytes)"


def check_license(client) -> str:
    """运行中服务：license report 四区段。"""
    r = client.get("/api/v1/license/report")
    assert r.status_code == 200, f"license report {r.status_code}: {r.text}"
    body = r.json()
    missing = {"dependencies", "models", "content_sources", "derived_objects"} - set(body)
    assert not missing, f"report 缺区段: {sorted(missing)}"
    return (
        f"api deps {len(body['dependencies']['api'])}, "
        f"web {body['dependencies']['web']['status']}, "
        f"models {len(body['models'])}, "
        f"sources {len(body['content_sources'])}"
    )


def check_e2e(client) -> str:
    """运行中服务：M7-02 onboarding 全路径 walkthrough。"""
    from app.ops.onboarding import run_walkthrough

    results = run_walkthrough(client)
    assert results["total_ms"] < 600.0 * 1000, "onboarding 超出 10 分钟预算"
    return f"walkthrough {len(results['steps'])} steps, {results['total_ms']:.0f} ms"


DEFAULT_LIVE_CHECKS: list[LiveCheck] = [
    LiveCheck("voice", "Voice providers + synthesize", check_voice),
    LiveCheck("license", "License report 四区段", check_license),
    LiveCheck("e2e", "E2E onboarding walkthrough", check_e2e),
]


def run_release_check(
    command_checks: list[CommandCheck],
    live_checks: list[LiveCheck] | None,
    *,
    execute: Callable[[CommandCheck], tuple[bool, str]] | None = None,
    client: object | None = None,
) -> list[CheckResult]:
    """纯编排：逐项执行收集结果。execute 与 client 可注入（测试）。

    live_checks 为 None 表示「仅本地门禁」模式（不依赖运行中服务）；
    传 live_checks 但 client=None 时 live 项如实 fail（不虚报）。
    """
    results: list[CheckResult] = []
    run_cmd = execute or _execute_command
    for c in command_checks:
        if c.id == "migration":
            # upgrade head（子进程）+ current 对账（两步合一项）
            try:
                ok, detail = run_cmd(c)
                if ok:
                    detail = detail + "; " + check_migration_current(c.cwd, env=c.env)
            except AssertionError as exc:
                ok, detail = False, str(exc)
        else:
            ok, detail = run_cmd(c)
        results.append(CheckResult(c.id, c.title, "pass" if ok else "fail", detail, "command"))
    for lc in live_checks or []:
        if client is None:
            results.append(
                CheckResult(
                    lc.id, lc.title, "fail",
                    "需要运行中的 API（release-check --api-base 注入 client）", "live",
                )
            )
            continue
        try:
            detail = lc.run(client)
        except Exception as exc:  # noqa: BLE001 - 任何异常都如实计为该项 fail
            results.append(CheckResult(lc.id, lc.title, "fail", str(exc)[:300], "live"))
        else:
            results.append(CheckResult(lc.id, lc.title, "pass", detail, "live"))
    return results


def _execute_command(check: CommandCheck) -> tuple[bool, str]:
    """真实子进程执行；超时 900s，失败带 stderr 尾部。"""
    try:
        import os as _os

        _env = {**_os.environ, **check.env}
        _env = {k: v for k, v in _env.items() if v is not None}
        proc = subprocess.run(
            list(check.argv),
            cwd=str(check.cwd),
            env=_env,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except FileNotFoundError as exc:
        return False, f"命令不可用: {exc}"
    except subprocess.TimeoutExpired:
        return False, "超时 (>900s)"
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    detail = " | ".join(tail[-3:]) if tail else ""
    return proc.returncode == 0, detail or f"exit {proc.returncode}"


def summarize(results: list[CheckResult]) -> tuple[bool, str]:
    """汇总：全部 pass 才全绿；返回 (all_green, 多行报告)。"""
    lines = [f"{'STATUS':<6} {'KIND':<8} {'ID':<16} TITLE / DETAIL"]
    for r in results:
        lines.append(f"{r.status:<6} {r.kind:<8} {r.id:<16} {r.title}")
        if r.detail:
            lines.append(f"{'':<6} {'':<8} {'':<16} -> {r.detail}")
    failed = [r.id for r in results if r.status != "pass"]
    all_green = not failed
    lines.append(
        f"RESULT: {'ALL GREEN' if all_green else 'FAILED'} "
        f"({len(results) - len(failed)}/{len(results)} pass)"
        + (f" failed: {', '.join(failed)}" if failed else "")
    )
    return all_green, "\n".join(lines)


def build_release_checks(db_url: str | None = None) -> tuple[list[CommandCheck], list[LiveCheck]]:
    return default_command_checks(db_url=db_url), list(DEFAULT_LIVE_CHECKS)
