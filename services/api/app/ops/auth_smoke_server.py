"""M14-95: 一次性真实 FastAPI 认证后端启动器（Harmony 真机 auth smoke 用）。

设计边界（直接复用仓库 FastAPI/auth 实现，不重复造认证）：
- create_app() 含 M9-01/M9-04/M9-06 门禁与 M10-03 cookie 行为；
- 隔离 SQLite 文件库（不碰主库；冷重启 = 同库文件 + 同 AUTH_SECRET 新进程）；
- 任务自有 loopback + 空闲端口（bind(0) 由 OS 分配，绝不写死、不跨任务复用）；
- 种子一个合成用户（m1495_smoke_user + 强随机口令），/api/v1/auth/login
  走真实 bcrypt 校验路径；
- 暴露面：/health（仓库）、auth status/login/me/logout（仓库实现），
  外加受保护 GET /api/v1/smoke/protected（证明业务门禁真实生效）；
- fail-closed：AUTH_SECRET 必须显式提供且 ≥32 字节（与 M9-04 同款
  validate_auth_secret 语义），否则拒绝启动；
- 脱敏：全部日志/状态文本对 secret 与口令做 redact（永不回显）；
  合成口令只驻留 env 与 DB bcrypt 哈希，不进任何输出。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from starlette.responses import JSONResponse

from app.core.security import validate_auth_secret

API_DIR = Path(__file__).resolve().parents[2]

_DEFAULT_USERNAME = "m1495_smoke_user"


router = APIRouter(prefix="/api/v1/smoke", tags=["smoke"])


@router.get("/protected")
async def smoke_protected() -> JSONResponse:
    """M14-95: 受保护业务 GET（走 app 级 require_user 门禁；auth 开时无 token 401）。"""
    return JSONResponse({"status": "ok", "endpoint": "smoke-protected"})


def _now_utc():
    from datetime import UTC, datetime

    return datetime.now(UTC)


def free_loopback_port() -> int:
    """任务自有空闲端口：bind(0) 由 OS 分配（loopback 127.0.0.1）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _child_argv(port: int, api_dir: Path, db_url: str) -> list[str]:
    """子进程 argv：显式 PYTHONPATH + create_app + uvicorn（真实端口/真实事件循环）。

    关键：子进程必须用与 pytest 宿主同一解释器——launcher 的 sys.executable
    只是宿主环境（宿主无 aiosqlite 会 fail-closed 崩在 create_app 之前）；
    pytest 场景经 env 覆盖为仓库 venv 解释器。DATABASE_URL 走 create_app 形参
    （Settings 字段无 AIOS_ 前缀映射，env 走 bare AUTH_SECRET/DATABASE_URL）。
    """
    import_code = (
        "import sys\n"
        f"sys.path.insert(0, {str(api_dir)!r})\n"
        "from app.main import create_app\n"
        "from app.ops.auth_smoke_server import router as smoke_router\n"
        "import uvicorn\n"
        f"app = create_app(database_url={str(db_url)!r})\n"
        "app.include_router(smoke_router)\n"
        f"uvicorn.run(app, host='127.0.0.1', port={port}, log_level='error')\n"
    )
    python = os.environ.get("AIOS_AUTH_SMOKE_PYTHON") or sys.executable
    return [python, "-c", import_code]


class AuthSmokeServer:
    """一次性真实认证后端：仓库 create_app + 隔离 SQLite + 合成用户 + 任务端口。

    用法（测试）：
        server = AuthSmokeServer(work_dir=tmp_path)
        info = server.start(auth_secret, seed_password)   # 已就绪；secret/口令已脱敏
        ...http 请求（真端口真 socket）...
        server.stop()
        # 冷重启：同 db_path + 同 secret + 同口令
        server2 = AuthSmokeServer(tmp_path, db_path=info["db_path"])
        info2 = server2.start(same_secret, same_password)
    """

    def __init__(
        self,
        work_dir: str | Path,
        *,
        username: str = _DEFAULT_USERNAME,
        db_path: str | Path | None = None,
    ) -> None:
        # B5/B6: 归一 work_dir 为绝对路径。相对 work_dir（如 launcher 传
        # "work"）+ 子进程 cwd=services/api 时，裸相对路径仍会漂移进
        # services/api/<work>，导致宿主种子与子进程数据库错位（登录 401）。
        # 绝对化后无论宿主/子进程在哪个 cwd 打开，都指向同一文件。
        self.work_dir = Path(work_dir).resolve()
        self._username = username
        self._secret: str = ""
        self._password: str = ""
        if db_path is not None:
            candidate = Path(db_path)
            # 相对 db_path 一律解析到（已绝对化的）work_dir 下（宿主种子与
            # 子进程 uvicorn 必须看到同一个文件；子进程 cwd 是 services/api，
            # 裸相对路径会漂移进 services/api/<db>，导致宿主种子与子进程
            # 数据库错位、登录 401）。绝对路径保持原语义，原样使用。
            self._db_path = (
                self.work_dir / candidate
                if not candidate.is_absolute()
                else candidate
            )
        else:
            self._db_path = None
        self._engine: Any = None
        self._proc: subprocess.Popen | None = None
        self._info: dict | None = None

    # ---- 脱敏（fail-closed 日志边界）----

    def redact(self, value: str | None, extra_secrets: list[str] | None = None) -> str:
        """把 secret/口令/token 子串从文本中脱敏为 [REDACTED]（永不回显）。"""
        if not value:
            return value or ""
        out = value
        for s in (self._secret, self._password, *(extra_secrets or [])):
            if s:
                out = out.replace(s, "[REDACTED]")
        return out

    def redacted_status(self, base: dict) -> dict:
        """可序列化状态：端口/用户/库路径/pid 可观测；secret 与口令永不出现。"""
        out = dict(base)
        out["auth_secret"] = "<configured>" if self._secret else "<absent>"
        out["seed_password"] = "[REDACTED]" if self._password else ""
        return out

    # ---- 校验（fail-closed）----

    @staticmethod
    def _validate_secret(secret: str) -> None:
        if not secret:
            raise RuntimeError(
                "AuthSmokeServer 拒绝启动：未提供 AUTH_SECRET（fail-closed，"
                "认证后端不能以未认证模式冒烟）"
            )
        # 与仓库 M9-04 同款强度门禁：development 下 ≥32 字节
        validate_auth_secret(secret, app_env="development")

    # ---- 启动 ----

    def start(self, auth_secret: str, seed_password: str, *, host: str = "127.0.0.1") -> dict:
        self._validate_secret(auth_secret)
        self._secret = auth_secret
        self._password = seed_password
        self.work_dir.mkdir(parents=True, exist_ok=True)
        if self._db_path is None:
            self._db_path = self.work_dir / f"m14-95-auth-smoke-{uuid.uuid4().hex[:8]}.db"
        else:
            # 兜底：相对 db_path 必须落在 work_dir 下（构造器已解析；
            # 双保险覆盖 start 后直接改属性的调用面）。
            if not self._db_path.is_absolute():
                self._db_path = self.work_dir / self._db_path
        # db_url 继续用 Path.as_posix()：SQLite 驱动按 POSIX 形式解析绝对
        # 路径；绝不让 URL 变成反斜杠（Windows 原生分隔符）形式。
        db_url = f"sqlite+aiosqlite:///{self._db_path.as_posix()}"
        port = free_loopback_port()
        base_url = f"http://{host}:{port}"

        env = dict(os.environ)
        env["AUTH_SECRET"] = self._secret  # Settings 字段 bare 名（无 AIOS_ 前缀）
        env["AUTH_COOKIE_SAMESITE"] = "lax"
        # 合成用户/口令不进子进程 env（宿主进程内种子；最小注入面）
        # 隔离：共享库/Redis/宿主库注入一律剥掉，防指向主库
        for strip in (
            "DATABASE_URL",
            "AIOS_DATABASE_URL",
            "AIOS_REDIS_URL",
            "REDIS_URL",
            "AIOS_LIVEKIT_URL",
        ):
            env.pop(strip, None)
        env.pop("AIOS_ENV_FILE", None)

        log_path = self.work_dir / "auth_smoke_server.log"
        argv = _child_argv(port, API_DIR, db_url)
        with open(log_path, "w", encoding="utf-8") as log:
            self._proc = subprocess.Popen(
                argv,
                env=env,
                cwd=str(API_DIR),
                stdout=log,
                stderr=subprocess.STDOUT,
            )

        # 有界等端口就绪；子进程死亡 → fail-closed（日志脱敏后回显）
        deadline = time.monotonic() + 60
        ready = False
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                break
            try:
                with socket.create_connection((host, port), timeout=0.5):
                    ready = True
                    break
            except OSError:
                time.sleep(0.25)
        if not ready:
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            tail = self.redact(log_tail, [self._password, self._secret])
            self._terminate()
            self._proc = None
            raise RuntimeError(
                f"AuthSmokeServer 启动失败（fail-closed）：端口 {port} 未就绪。"
                f"日志（已脱敏）尾部：{tail}"
            )

        # 宿主进程内做建表 + 合成用户种子（同库文件，独立连接安全；幂等——
        # 冷重启时用户已存在则跳过 create，用户与角色跨重启保持）
        import asyncio

        from sqlalchemy import select

        from app.core.security import hash_password
        from app.db.orm import UserRow
        from app.db.session import create_engine, make_sessionmaker, prepare_database

        engine = create_engine(db_url)
        try:

            async def _seed() -> None:
                await prepare_database(engine, db_url)
                sessionmaker = make_sessionmaker(engine)
                async with sessionmaker() as session, session.begin():
                    existing = await session.scalar(
                        select(UserRow).where(UserRow.username == self._username)
                    )
                    if existing is None:
                        session.add(
                            UserRow(
                                id=uuid.uuid4().hex,
                                username=self._username,
                                password_hash=hash_password(seed_password),
                                role="learner",
                                created_at=_now_utc(),
                            )
                        )

            asyncio.run(_seed())
        except BaseException:
            try:
                asyncio.run(engine.dispose())
            except BaseException:  # noqa: BLE001, S110 —— 失败路径尽力 dispose，绝不掩盖原始异常
                pass
            self._terminate()
            self._proc = None
            self._engine = None
            raise
        self._engine = engine

        self._info = self.redacted_status(
            {
                "host": host,
                "port": port,
                "base_url": base_url,
                "username": self._username,
                "db_path": str(self._db_path),
                "seeded": True,
                "pid": self._proc.pid,
            }
        )
        return self._info

    def _terminate(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    def stop(self) -> None:
        self._terminate()
        self._proc = None
        engine, self._engine = self._engine, None
        if engine is not None:
            import asyncio

            try:
                asyncio.run(engine.dispose())
            except BaseException:  # noqa: BLE001, S110 —— 清理路径尽力 dispose，绝不影响 stop 语义
                pass

    @property
    def info(self) -> dict:
        return self._info or {}

    @property
    def db_path(self) -> str:
        return str(self._db_path)


def _main_argv() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--work-dir")
    ap.add_argument("--username", default=_DEFAULT_USERNAME)
    ap.add_argument("--auth-secret", default=None)
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--seed-password", default=None)
    ns = ap.parse_args()

    secret = ns.auth_secret or os.environ.get("AIOS_AUTH_SECRET") or ""
    work_dir = ns.work_dir or ""
    if not work_dir:
        print("AuthSmokeServer fail-closed：--work-dir 必需。")
        return 2
    if not secret:
        print(
            "AuthSmokeServer fail-closed：未提供 AUTH_SECRET（--auth-secret 或 "
            "AIOS_AUTH_SECRET 环境变量）。拒绝以未认证模式启动。"
        )
        return 2
    if not ns.seed_password:
        print("AuthSmokeServer fail-closed：--seed-password 必需（合成用户口令）。")
        return 2

    server = AuthSmokeServer(work_dir, username=ns.username, db_path=ns.db_path)
    try:
        info = server.start(secret, ns.seed_password)
    except Exception as cause:  # noqa: BLE001
        print(f"AuthSmokeServer fail-closed 启动失败：{cause}")
        return 1
    print(json.dumps(info, ensure_ascii=False, sort_keys=True))
    server.stop()
    return 0


def main() -> int:
    return _main_argv()


if __name__ == "__main__":
    raise SystemExit(main())
