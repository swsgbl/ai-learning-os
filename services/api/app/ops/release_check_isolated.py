"""M11-10 隔离本地 full release-check 一键编排器。

把 M11-09 的人工流程（一次性 gitignored SQLite -> alembic upgrade head ->
127.0.0.1 回环临时 uvicorn -> /health 就绪 -> full 模式 10 项门禁 -> JSON 证据
原子落盘 -> 关停临时 API）收敛为一条命令：`python -m app.ops.cli
release-check-isolated`（复杂度留在系统里，用户只需一条命令）。

编排护栏（fail-closed，exit 2、未迁移/未启服务/未写证据）：
- 工作区与证据输出都必须位于 gitignore 的 artifacts/temp（复用
  `is_safe_artifact_path`；位于已过护栏工作区内的嵌套证据路径同样放行）；
  工作区是 symlink 一律拒绝；
- 一次性 SQLite 已存在即拒绝（保护既有证据，绝不改写）；
- 迁移失败 => 不启动临时 API、不写证据；
- 临时 API 未在限时内通过 /health => 不执行门禁、不写证据，附日志尾部诊断；
- 证据原子落盘（复用 CLI 共享 `_write_report_atomic`：同目录临时文件 +
  fsync + os.replace，symlink 拒绝；失败旧报告字节原样、无 .tmp 残留、
  不打印门禁结论）；
- finally 路径保证关停临时 API：terminate -> 限时等待 -> kill 兜底，并复核
  进程已回收（不留常驻子进程）。

环境隔离（子进程显式构造，绝不继承调用方敏感面）：剥离 AUTH_SECRET /
AIOS_PG_TEST_URL / DATABASE_URL，显式注入 APP_ENV=development、
VOICE_MODE=local、HOST_BIND_IP=127.0.0.1、DATABASE_URL=<一次性 SQLite>。
所有对外消息过 `redact_secrets`，绝不打印密钥/凭据。

退出码：全绿=0 / 门禁真实 fail=1（证据照常落盘）/ 编排或护栏失败=2（无证据）。

边界（必须随证据一起被读到）：`all_green=true` 只表示「隔离本地运行面上
10 项门禁全部真实通过」（execution_scope=full），不是 production readiness、
不授权生产发布；`production_ready=false` 保持不变。本工具不连接任何生产
DB、不部署、不打 tag、不发 Release、不推镜像；一次性 SQLite 与证据留在
gitignored artifacts/ 本地，不入 git。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Any

from app.ops.legacy_papers import is_safe_artifact_path
from app.ops.production_preflight import redact_secrets
from app.ops.release_check import (
    CheckResult,
    build_release_check_evidence,
    build_release_checks,
    run_release_check,
    summarize,
)
from app.ops.version import REPO_ROOT

API_DIR = REPO_ROOT / "services" / "api"
DEFAULT_WORKSPACE_PARENT = REPO_ROOT / "artifacts" / "release-check-isolated"
DB_FILENAME = "release-check.sqlite"
EVIDENCE_FILENAME = "release-check-isolated.json"
UVICORN_LOG_FILENAME = "uvicorn.log"

#: 必须从子进程环境剥离的继承变量（防调用方生产面泄入隔离运行面）。
STRIP_ENV_KEYS = ("AUTH_SECRET", "AIOS_PG_TEST_URL", "DATABASE_URL")
#: 隔离运行面的显式口径（M11-09 人工流程同一口径）。
ISOLATED_ENV: dict[str, str] = {
    "APP_ENV": "development",
    "VOICE_MODE": "local",
    "HOST_BIND_IP": "127.0.0.1",
}

DEFAULT_HEALTH_TIMEOUT = 60.0
MIGRATION_TIMEOUT = 600
SERVER_GRACE_SECONDS = 10.0
KILL_WAIT_SECONDS = 5.0
HEALTH_POLL_INTERVAL = 0.5

#: 工作区唯一序号（Windows 时钟粒度下时间戳可能同值，进程内单调兜底）。
_WS_SEQ = count(1)

BOUNDARY_NOTE = (
    "边界：这是隔离本地环境的一次性 full release-check（一次性 gitignored SQLite + "
    "127.0.0.1 回环临时 API + auth-off + 本地语音），all_green 仅代表该隔离运行面"
    "10 项门禁真实通过，不是 production readiness、不授权生产发布；"
    "production_ready=false 保持不变。"
)


class _OrchestrationFailure(Exception):
    """编排护栏/基础设施失败（exit 2 语义，不写证据）。"""


@dataclass
class IsolatedRunResult:
    """编排结果：CLI 据此打印与退出；测试据此断言语义。"""

    exit_code: int
    summary: str
    workspace: Path | None = None
    db_path: Path | None = None
    db_url: str | None = None
    api_base: str | None = None
    port: int | None = None
    evidence: dict[str, Any] | None = None
    evidence_path: Path | None = None
    evidence_written: bool = False
    all_green: bool = False
    server_stop: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- 基础件 --


def build_isolated_env(base_env: Mapping[str, str], db_url: str) -> dict[str, str]:
    """构造子进程隔离环境：先剥离敏感继承变量，再显式注入隔离口径。

    顺序有讲究：DATABASE_URL 先删后设（覆盖语义 vs 继承语义不混淆）；
    AUTH_SECRET / AIOS_PG_TEST_URL 只删不设（本地 auth-off、不连任何 PG）。
    不修改入参（调用方 os.environ 原样保留）。
    """
    env = dict(base_env)
    for key in STRIP_ENV_KEYS:
        env.pop(key, None)
    env.update(ISOLATED_ENV)
    env["DATABASE_URL"] = db_url
    return env


def sqlite_url(db_path: Path) -> str:
    """一次性 SQLite 的绝对 URL（POSIX 分隔符，cwd 无关，含空格路径安全）。"""
    return "sqlite+aiosqlite:///" + db_path.resolve().as_posix()


def default_workspace() -> Path:
    """默认工作区：artifacts/release-check-isolated/run-<UTC>-<pid>-<序号>（每次唯一）。

    唯一命名保证「一次性」语义——重跑永不触碰上一次的 DB 与证据；时间戳
    在 Windows 时钟粒度下可能同值，追加进程内单调序号兜底（pid 隔跨进程）。
    需要固定目录时用 --workdir 显式提供（此时既有 DB 会被拒绝以保护证据）。
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return DEFAULT_WORKSPACE_PARENT / f"run-{stamp}-{os.getpid()}-{next(_WS_SEQ)}"


def pick_free_port(host: str = "127.0.0.1") -> int:
    """本机回环选一个当前空闲端口（bind(0) 后立即释放，交由 uvicorn 绑定）。

    释放到绑定之间存在竞态窗口（回环低流量面，实践可忽略）；若 uvicorn 因
    端口被抢占而退出，/health 等待会带日志尾部如实失败（fail-closed 不虚报）。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


# ---------------------------------------------------------------- 步骤件 --


def run_alembic_upgrade(
    db_url: str,
    *,
    api_dir: Path = API_DIR,
    base_env: Mapping[str, str] | None = None,
    timeout: float = MIGRATION_TIMEOUT,
) -> tuple[bool, str]:
    """对一次性 SQLite 执行 alembic upgrade head（隔离环境子进程）。

    返回 (ok, 脱敏消息)。失败消息取 stdout/stderr 尾部并过 redact_secrets
    （错误可能内嵌连接串）。
    """
    env = build_isolated_env(base_env if base_env is not None else os.environ, db_url)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=str(api_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"alembic upgrade head 超时 (>{timeout:.0f}s)"
    except OSError as cause:
        return False, f"alembic 子进程启动失败: {type(cause).__name__}"
    if proc.returncode != 0:
        tail = " | ".join(
            ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-5:]
        )
        return False, redact_secrets(tail) or f"exit {proc.returncode}"
    return True, "alembic upgrade head: exit 0"


def start_uvicorn(
    port: int,
    env: Mapping[str, str],
    log_path: Path,
    *,
    api_dir: Path = API_DIR,
) -> subprocess.Popen:
    """启动临时 uvicorn（repo venv 同解释器、仅回环、单进程无 reload）。

    stdout/stderr 重定向到工作区 uvicorn.log（审计留档）；不带 --reload /
    --workers——单进程即整个进程树，关停时不留孤儿子进程。
    """
    log_handle = log_path.open("ab")
    try:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "info",
            ],
            cwd=str(api_dir),
            env=dict(env),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
    finally:
        log_handle.close()  # Popen 已复制句柄；父进程副本立即关闭


def wait_for_health(
    port: int,
    timeout: float = DEFAULT_HEALTH_TIMEOUT,
    *,
    client: Any = None,
    sleep: Any = time.sleep,
    clock: Any = time.monotonic,
) -> tuple[bool, str]:
    """限时轮询 /health（trust_env=False，不走任何系统代理）。

    成功 = HTTP 200 且 body status=ok。失败消息含尝试次数与最后错误（脱敏），
    供编排层拼接 uvicorn 日志尾部给出完整诊断。client 可注入（测试）。
    """
    import httpx

    base_url = f"http://127.0.0.1:{port}"
    own_client = client is None
    if own_client:
        client = httpx.Client(base_url=base_url, trust_env=False, timeout=5.0)
    deadline = clock() + timeout
    attempts = 0
    last_error = ""
    try:
        while clock() < deadline:
            attempts += 1
            try:
                response = client.get("/health")
                if response.status_code == 200:
                    body_status = response.json().get("status")
                    if body_status == "ok":
                        return True, f"/health ok（{attempts} 次尝试内就绪）"
                    last_error = f"/health 200 但 status={body_status!r}"
                else:
                    last_error = f"/health -> HTTP {response.status_code}"
            except Exception as exc:  # noqa: BLE001 - 启动期连接被拒等如实记录
                last_error = f"{type(exc).__name__}: {exc}"
            sleep(HEALTH_POLL_INTERVAL)
    finally:
        if own_client:
            client.close()
    return False, redact_secrets(
        f"/health 在 {timeout:.0f}s 内未就绪（{attempts} 次尝试，最后: {last_error}）"
    )


def stop_uvicorn(
    proc: subprocess.Popen,
    *,
    grace: float = SERVER_GRACE_SECONDS,
    kill_wait: float = KILL_WAIT_SECONDS,
) -> str:
    """关停临时 API：terminate -> 限时等待 -> kill 兜底，返回关停方式。

    单进程 uvicorn（无 reload/workers）下这就是完整进程树；kill 后仍未退出
    视为关停失败（抛 RuntimeError，由调用方如实报告并请人工核查，不装作干净）。
    """
    if proc.poll() is not None:
        return "already-exited"
    proc.terminate()
    try:
        proc.wait(timeout=grace)
        return "terminated"
    except subprocess.TimeoutExpired:
        pass
    proc.kill()
    try:
        proc.wait(timeout=kill_wait)
    except subprocess.TimeoutExpired as cause:
        raise RuntimeError(
            f"kill 后临时 API 进程仍未退出（可能残留，请人工核查）: {cause}"
        ) from cause
    return "killed"


def uvicorn_log_tail(log_path: Path, max_lines: int = 15) -> str:
    """uvicorn.log 尾部（诊断用；脱敏——日志理论上不含密钥，纵深防御）。"""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(uvicorn 日志不可读)"
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return "(uvicorn 日志为空)"
    return redact_secrets(" | ".join(lines[-max_lines:]))


def _run_full_gate(api_base: str, db_url: str) -> tuple[bool, str, list[CheckResult]]:
    """既有 full release-check 零改动复用：10 项门禁对临时 API + 一次性 SQLite。

    build_release_checks 的 db_url 门控（evaluate_pg_test_url）对 sqlite URL
    天然拒绝注入 AIOS_PG_TEST_URL——一次性 SQLite 只进 migration/backup 的
    DATABASE_URL 运行时检查，不会把任何 PG 面带进 api-test 子进程。
    """
    import httpx

    cmd_checks, live_checks = build_release_checks(db_url=db_url)
    with httpx.Client(base_url=api_base, trust_env=False, timeout=600.0) as client:
        results = run_release_check(cmd_checks, live_checks, client=client)
    all_green, report = summarize(results)
    return all_green, report, results


def _write_evidence_atomic(path: Path, text: str) -> None:
    """证据原子落盘（复用 CLI 共享实现：临时文件 + fsync + os.replace）。"""
    from app.ops.cli import _write_report_atomic

    _write_report_atomic(path, text)


# ---------------------------------------------------------------- 编排器 --


def _is_within(child: Path, parent: Path) -> bool:
    """child 是否位于 parent 目录内（双方 resolve 后比较，`..` 逃逸不通过）。"""
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def run_isolated_release_check(
    *,
    workdir: str | Path | None = None,
    output: str | Path | None = None,
    health_timeout: float = DEFAULT_HEALTH_TIMEOUT,
    base_env: Mapping[str, str] | None = None,
) -> IsolatedRunResult:
    """编排隔离本地 full release-check（各步骤为模块级函数，可注入替身测试）。

    顺序：路径护栏 -> 一次性 DB 就绪（拒绝既有）-> alembic upgrade head ->
    选空闲端口起回环临时 API -> 限时等 /health -> 既有 10 项 full 门禁 ->
    证据原子落盘（execution_scope=full 同一契约）-> finally 关停临时 API。

    退出码：0=全绿 / 1=门禁真实 fail（证据已写） / 2=护栏或编排失败（无证据）。
    """
    workspace = Path(workdir) if workdir is not None else default_workspace()
    evidence_path = (
        Path(output) if output is not None else workspace / EVIDENCE_FILENAME
    )
    db_path = workspace / DB_FILENAME

    # -- 护栏先于一切副作用（不建目录、不迁移、不启服务、不写证据） --------
    if not is_safe_artifact_path(workspace):
        return IsolatedRunResult(
            2,
            f"拒绝工作区 {workspace}：一次性 SQLite 与证据只能落在 gitignore 的 "
            "artifacts/ 或 temp/ 目录",
        )
    # 证据路径：自身过护栏，或位于已过护栏的工作区内（工作区整个目录都在
    # gitignored artifacts/temp 下，其内部任意嵌套路径同样不会进 git——
    # is_safe_artifact_path 只认直接父目录名，嵌套布局需此包含式放行）
    if not (is_safe_artifact_path(evidence_path) or _is_within(evidence_path, workspace)):
        return IsolatedRunResult(
            2,
            f"拒绝写入 {evidence_path}：证据只能写入 gitignore 的 artifacts/ 或 "
            "temp/ 目录（或已过护栏的隔离工作区内）",
        )
    if workspace.is_symlink():
        return IsolatedRunResult(2, f"拒绝工作区 {workspace}：路径是符号链接")
    if db_path.exists() or db_path.is_symlink():
        return IsolatedRunResult(
            2,
            f"拒绝执行：一次性数据库 {db_path} 已存在——为保护既有证据不改写，"
            "请换一个新工作区（--workdir）或移走该文件",
        )

    db_url_value = sqlite_url(db_path)
    result = IsolatedRunResult(
        0,
        "",
        workspace=workspace,
        db_path=db_path,
        db_url=db_url_value,
        evidence_path=evidence_path,
    )
    proc: subprocess.Popen | None = None
    port: int | None = None
    try:
        workspace.mkdir(parents=True, exist_ok=True)  # M11-09 教训：父目录必须先建
        # 1) 迁移先行：失败 => 不启 API、不写证据（exit 2）
        migrated, migration_detail = run_alembic_upgrade(
            db_url_value, base_env=base_env
        )
        if not migrated:
            # 步骤实现已抹凭据；编排边界再抹一次（纵深防御，替身实现同理兜底）
            raise _OrchestrationFailure(
                f"一次性 SQLite 迁移失败（未启动临时 API、未写证据）: "
                f"{redact_secrets(migration_detail)}"
            )
        # 2) 回环临时 API：repo venv 同解释器、隔离环境、本地选端口
        port = pick_free_port()
        result.port = port
        result.api_base = f"http://127.0.0.1:{port}"
        server_env = build_isolated_env(
            base_env if base_env is not None else os.environ, db_url_value
        )
        try:
            proc = start_uvicorn(port, server_env, workspace / UVICORN_LOG_FILENAME)
        except OSError as cause:
            raise _OrchestrationFailure(
                f"临时 API 启动失败（未执行门禁、未写证据）: {type(cause).__name__}"
            ) from cause
        # 3) 限时等待 /health：失败 => 不执行门禁、不写证据（附日志尾部诊断）
        healthy, health_detail = wait_for_health(port, health_timeout)
        if not healthy:
            raise _OrchestrationFailure(
                f"临时 API 未就绪（未执行门禁、未写证据）: "
                f"{redact_secrets(health_detail)} | "
                f"uvicorn 日志尾部: "
                f"{uvicorn_log_tail(workspace / UVICORN_LOG_FILENAME)}"
            )
        # 4) 既有 full release-check 零改动复用（10 项，execution_scope=full）
        all_green, report, check_results = _run_full_gate(
            result.api_base, db_url_value
        )
        evidence = build_release_check_evidence(check_results, execution_scope="full")
        # 5) 证据原子落盘（失败 => exit 2、旧报告原样、不打印门禁结论）
        try:
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            import json as _json

            _write_evidence_atomic(
                evidence_path, _json.dumps(evidence, ensure_ascii=False, indent=2)
            )
        except OSError as cause:
            raise _OrchestrationFailure(
                f"证据写入失败（路径/权限/磁盘问题，未产生报告文件）: "
                f"{type(cause).__name__}: {cause}"
            ) from cause
        result.evidence = evidence
        result.evidence_written = True
        result.all_green = all_green
        result.exit_code = 0 if all_green else 1
        result.summary = report + "\n" + BOUNDARY_NOTE
        return result
    except _OrchestrationFailure as cause:
        result.exit_code = 2
        result.evidence = None
        result.evidence_written = False
        result.summary = str(cause)
        return result
    except Exception as cause:  # noqa: BLE001 - 稳定 exit 2、无 traceback、消息脱敏
        result.exit_code = 2
        result.evidence = None
        result.evidence_written = False
        result.summary = redact_secrets(
            f"隔离编排意外失败（未写证据）: {type(cause).__name__}: {cause}"
        )
        return result
    finally:
        # -- 关停保证：无论成功、门禁失败还是编排异常，临时 API 都被关停 --
        if proc is not None:
            try:
                result.server_stop = stop_uvicorn(proc)
                if proc.poll() is None:  # 防御性复核：不允许静默留进程
                    raise RuntimeError("关停后进程仍存活")
            except RuntimeError as cause:
                result.exit_code = 2  # 即使门禁全绿，关停失败也如实降级
                result.summary = (
                    f"临时 API 关停失败（端口 {port}，请人工核查残留进程）: "
                    f"{cause}\n" + result.summary
                )
