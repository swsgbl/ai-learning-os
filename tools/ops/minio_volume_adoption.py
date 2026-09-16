r"""M14-41：MinIO 生产卷属主迁移（root → uid 1000）受控采纳工具。

单文件纯标准库；Runner/FS/Clock/Sleeper 全注入（测试零真实 Docker）。

三命令：

- ``plan``：零 subprocess / 零写盘，stdout 输出纯 JSON 计划（固定风险、
  精确目标、阶段、固定 argv 模板）。
- ``execute --confirm "EXECUTE MINIO VOLUME ADOPTION"``：确认短语精确匹配，
  缺失/近似一律零副作用拒绝。状态机：只读 preflight（引擎/本地固定镜像
  元数据/六容器健康+基线 ID/卷 root-only 递归普查/目标容器）→ 备份路径
  先校验/预备（停容器之前，路径失败零容器变更）→ 控制面文件
  （COMPOSE_FILE/ENV_FILE）仅元数据核对（存在+常规文件+非 symlink；
  内容绝不读取）→ 停仅 minio → 本地
  pinned image root helper 备份卷（tar+sha256+size+manifest）→
  ``chown -R 1000:1000`` → 递归复核 uid1000 → ``compose --env-file
  env.production-recovery --profile local up -d --no-build --no-deps
  minio`` → 健康 → 重建 minio 容器实际镜像 ID 精确等于预检本地固定镜像
  ID → 除 minio 外五容器 ID 不变 → JSON+MD 报告。
  全 uid1000 → 跳过 chown 但必须备份；mixed/unknown/empty fail-closed。
- ``rollback --backup-file PATH --confirm "EXECUTE MINIO VOLUME ROLLBACK"``：
  路径/文件名/伴生 .sha256/校验和/manifest（须记录 old image id 与迁移前
  属主类）任一不满足 → 拒绝并输出人工恢复指引（零 Docker 副作用）；备份
  tar/.sha256/.manifest.json 三工件各自独立 fail-closed（resolve 边界 +
  symlink 拒绝 + 仅常规文件 + 存在性/校验和/manifest 语义校验）；
  manifest old image id ≠ 当前本地固定镜像 id → 在 stop/restore 之前
  fail-closed；预检只要求 minio 容器存在（不要求 healthy——失败
  execute 后 minio 可能 stopped/unhealthy），其余五容器须存在且 healthy；
  停 minio 之前同样先做控制面文件仅元数据核对；全部满足才执行仅 minio
  服务/卷的受控恢复。

边界（结构性 argv 门逐 token 白名单）：禁 pull/build/down/kill/prune/
rm/exec/restart；禁触碰 api/web/livekit/postgres/redis；helper 容器恒显式
``--user 0:0``（本地固定镜像默认用户 minio:minio，root 属主卷上隐式用户
不安全）；挂载最小化——普查/备份卷只读(:ro)、备份宿主目的地可写、恢复
卷可写、宿主备份源只读(:ro)；helper/报告/备份
路径限制在 gitignored ``.verify/artifacts/m14-41-minio-volume-adoption/``
内——词法边界先行 + 从 REPO_ROOT 沿未 resolve 的原始组件逐级 symlink
检查（resolve 折叠不掉 symlinked root/component）；compose up 恒带
``--env-file``（仅路径锚定）+ ``--profile local``；容器 ID 记录/比较前
一律 ``.strip()`` 归一；``infra/env.production-recovery`` 仅作锚定常量，
内容绝不读取、绝不输出。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# ---------------------------------------------------------------- 常量（固定生产面）

EXIT_OK = 0
EXIT_REJECT = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
ENV_FILE = REPO_ROOT / "infra" / "env.production-recovery"  # 锚定常量；内容绝不读取
ARTIFACT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-41-minio-volume-adoption"

TAG = "[minio-volume-adoption]"
REPORT_SCHEMA_VERSION = 1
MILESTONE = "M14-41"
TOOL_NAME = "tools/ops/minio_volume_adoption.py"

CONFIRM_EXECUTE = "EXECUTE MINIO VOLUME ADOPTION"
CONFIRM_ROLLBACK = "EXECUTE MINIO VOLUME ROLLBACK"

PROJECT = "aios-m14-03-production-rehearsal"
MINIO_RELEASE = "RELEASE.2025-10-15T17-29-55Z"
SELF_IMAGE_REF = f"aios/minio:{MINIO_RELEASE}"
VOLUME_NAME = f"{PROJECT}_minio-data"
PROD_SERVICE = "minio"
TARGET_UID_GID = "1000:1000"
COMPOSE_PROFILE = "local"  # 与 production_recovery.DEFAULT_PROFILE 对齐

STACK_SERVICES: tuple[str, ...] = ("postgres", "redis", "minio", "api", "web", "livekit")
OTHER_SERVICES: tuple[str, ...] = tuple(s for s in STACK_SERVICES if s != PROD_SERVICE)
MINIO_CONTAINER = f"{PROJECT}-{PROD_SERVICE}-1"
CONTAINER_NAMES = frozenset(f"{PROJECT}-{service}-1" for service in STACK_SERVICES)

LOCAL_IMAGE_MISSING = "minio-local-image-missing"  # 与 production_recovery 词汇交叉锁定
BACKUP_STEM_PREFIX = "minio-data-backup-"
BACKUP_STEM_RE = re.compile(r"^minio-data-backup-\d{8}-\d{6}\.tar$")

ENGINE_TIMEOUT_SECONDS = 15.0
INSPECT_TIMEOUT_SECONDS = 30.0
CENSUS_TIMEOUT_SECONDS = 120.0
STOP_TIMEOUT_SECONDS = 60.0
TAR_TIMEOUT_SECONDS = 1800.0
CHOWN_TIMEOUT_SECONDS = 900.0
UP_TIMEOUT_SECONDS = 300.0
HEALTH_ATTEMPTS = 40
HEALTH_INTERVAL_SECONDS = 1.0

MANUAL_RECOVERY_GUIDANCE = (
    "自动回滚被拒绝（备份/校验和/manifest 不满足前置条件）。人工恢复路径：\n"
    "1) 停止 minio 容器（仅 minio，勿动其它五容器）；\n"
    "2) 用本地固定镜像 " + SELF_IMAGE_REF + " 起 --rm helper（--network none "
    "+ 显式 --user 0:0 + 宿主备份源只读挂载 :ro），"
    "把备份 tar 解回卷 " + VOLUME_NAME + "（写挂载 /data）；\n"
    "3) helper 内 ls -lnAR 复核属主与 manifest 记录一致；\n"
    "4) docker compose -f infra/docker-compose.yml --project-name " + PROJECT
    + " --env-file infra/" + ENV_FILE.name + " --profile " + COMPOSE_PROFILE
    + " up -d --no-build --no-deps minio；\n"
    "5) 全程禁止 pull/build/down/kill/prune，禁止在 shell 历史中留存 env secret。"
)

# ---------------------------------------------------------------- 固定 argv 形态


def container_name(service: str) -> str:
    return f"{PROJECT}-{service}-1"


VERSION_ARGV: tuple[str, ...] = ("docker", "version", "--format", "{{.Server.Version}}")
IMAGE_ID_ARGV: tuple[str, ...] = ("docker", "image", "inspect", SELF_IMAGE_REF,
                                  "--format", "{{.Id}}")
# R2-1/2：helper 容器恒显式 --user 0:0——本地固定镜像默认用户 minio:minio
# (uid 1000)，root 属主生产卷上隐式用户不可用/不安全；挂载最小化——普查
# 卷只读（/probe:ro），chown 需写卷（/data）。
CENSUS_ARGV: tuple[str, ...] = ("docker", "run", "--rm", "--user", "0:0",
                                "--network", "none",
                                "-v", f"{VOLUME_NAME}:/probe:ro",
                                "--entrypoint", "/bin/ls", SELF_IMAGE_REF,
                                "-lnAR", "/probe")
CHOWN_ARGV: tuple[str, ...] = ("docker", "run", "--rm", "--user", "0:0",
                               "-v", f"{VOLUME_NAME}:/data",
                               "--entrypoint", "/bin/chown", SELF_IMAGE_REF,
                               "-R", TARGET_UID_GID, "/data")
STOP_ARGV: tuple[str, ...] = ("docker", "stop", MINIO_CONTAINER)
# 生产恢复 compose 纪律：显式 --env-file 锚定固定 ENV_FILE（仅路径——内容
# 绝不读取/输出）+ --profile local，再 up -d --no-build --no-deps minio。
UP_ARGV: tuple[str, ...] = ("docker", "compose", "-f", str(COMPOSE_FILE),
                            "--project-name", PROJECT,
                            "--env-file", str(ENV_FILE),
                            "--profile", COMPOSE_PROFILE,
                            "up", "-d", "--no-build", "--no-deps", PROD_SERVICE)
FIXED_ARGV_SHAPES = frozenset({VERSION_ARGV, IMAGE_ID_ARGV, CENSUS_ARGV,
                               CHOWN_ARGV, STOP_ARGV, UP_ARGV})
INSPECT_FORMATS = frozenset({"{{.Id}}", "{{.State.Health.Status}}"})
# minio-only：重建后容器实际镜像 ID 复核（{{.Image}} == 镜像 {{.Id}}）
MINIO_ONLY_INSPECT_FORMATS = frozenset({"{{.Image}}"})


def tar_helper_argv(helper_tar_name: str, host_backup_dir: Path,
                    *, extract: bool) -> tuple[str, ...]:
    """备份/恢复 helper 形态：本地固定镜像 + /bin/tar + 恒 --user 0:0。
    R2-2 挂载最小化：备份（extract=False）卷 :ro 只读 + 宿主备份目的地
    可写；恢复（extract=True）卷可写 + 宿主备份源 :ro 只读。宿主绑定目录
    必须在白名单 Surface 上登记。"""
    volume_mount = f"{VOLUME_NAME}:/data" if extract else f"{VOLUME_NAME}:/data:ro"
    host_mount = (f"{host_backup_dir}:/backup:ro" if extract
                  else f"{host_backup_dir}:/backup")
    tail: tuple[str, ...]
    if extract:
        tail = (f"/backup/{helper_tar_name}", "-C", "/data")
    else:
        tail = (f"/backup/{helper_tar_name}", "-C", "/data", ".")
    return ("docker", "run", "--rm", "--user", "0:0", "-v", volume_mount,
            "-v", host_mount,
            "--entrypoint", "/bin/tar", SELF_IMAGE_REF,
            "-xf" if extract else "-cf", *tail)


# ---------------------------------------------------------------- Runner（注入点）


class RunnerError(RuntimeError):
    """平台命令执行失败（不可执行/超时）——调用方转为安全类别，绝不保留文本。"""


class CommandNotAllowedError(RunnerError):
    """非白名单命令——在任何执行之前拒绝（fail-closed）。"""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    def run(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0,
            encoding: str | None = None) -> CommandResult: ...


def os_windows() -> bool:
    return os.name == "nt"


class RealRunner:
    """真实子进程执行：capture + UTF-8 + Windows 侧恒 CREATE_NO_WINDOW。"""

    def run(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0,
            encoding: str | None = None) -> CommandResult:
        text_argv = [str(item) for item in argv]
        kwargs: dict[str, object] = {
            "capture_output": True, "text": True,
            "encoding": encoding or "utf-8", "errors": "replace",
        }
        if os_windows():
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        try:
            result = subprocess.run(text_argv, check=False, timeout=timeout,
                                    **kwargs)  # type: ignore[arg-type]
        except (OSError, subprocess.TimeoutExpired) as cause:
            raise RunnerError(
                f"命令不可执行/超时: {text_argv[0]}: {type(cause).__name__}") from cause
        return CommandResult(tuple(text_argv), result.returncode,
                             result.stdout or "", result.stderr or "")


def categorize_runner_exception(exc: BaseException) -> tuple[str, str]:
    """Runner 异常 →（安全类别, 异常类名）。绝不保留 str(exc) 文本。"""
    if isinstance(exc, CommandNotAllowedError):
        category = "argv-not-whitelisted"
    elif isinstance(exc, subprocess.TimeoutExpired):
        category = "command-timeout"
    elif isinstance(exc, (RunnerError, OSError)):
        category = "command-exec-error"
    else:
        category = "internal-error"
    return category, type(exc).__name__


# ---------------------------------------------------------------- argv 门（结构性白名单）


@dataclass(frozen=True)
class Surface:
    """argv 门的事实面：本轮时间戳 + 已登记的 helper tar 名与宿主备份目录。"""

    stamp: str = ""
    helper_tar_name: str | None = None
    host_backup_dir: Path | None = None


def is_allowed_docker_argv(argv: tuple[str, ...] | list[str],
                           surface: Surface) -> bool:
    """结构性白名单：逐 token 匹配固定形态。任何偏差（pull/build/down/kill/
    prune/rm/exec/restart/他服务/错误镜像/非 :ro 普查/插入 token/未登记的
    备份目录）一律 False——头尾匹配不充分，必须全长逐 token 相等。"""
    tokens = tuple(str(item) for item in argv)
    if len(tokens) < 3 or tokens[0] != "docker":
        return False
    if tokens in FIXED_ARGV_SHAPES:
        return True
    if (tokens[:2] == ("docker", "inspect") and len(tokens) == 5
            and tokens[3] == "--format"
            and tokens[2] in CONTAINER_NAMES
            and tokens[4] in INSPECT_FORMATS):
        return True
    # {{.Image}} 仅允许 minio 容器（重建后镜像 ID 精确复核）
    if (tokens[:2] == ("docker", "inspect") and len(tokens) == 5
            and tokens[3] == "--format"
            and tokens[2] == MINIO_CONTAINER
            and tokens[4] in MINIO_ONLY_INSPECT_FORMATS):
        return True
    if surface.helper_tar_name is not None and surface.host_backup_dir is not None:
        allowed_tar = {
            tar_helper_argv(surface.helper_tar_name, surface.host_backup_dir,
                            extract=False),
            tar_helper_argv(surface.helper_tar_name, surface.host_backup_dir,
                            extract=True),
        }
        if tokens in allowed_tar:
            return True
    return False


class GateRunner:
    """白名单守卫：非白名单 argv 在任何执行之前抛 CommandNotAllowedError。"""

    def __init__(self, inner: Runner, surface: Surface) -> None:
        self._inner = inner
        self._surface = surface

    def run(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0,
            encoding: str | None = None) -> CommandResult:
        if not is_allowed_docker_argv(tuple(str(item) for item in argv), self._surface):
            raise CommandNotAllowedError("argv-not-whitelisted")
        return self._inner.run(argv, timeout=timeout, encoding=encoding)


# ---------------------------------------------------------------- 时钟 / 睡眠


class Clock(Protocol):
    def utc_now_iso(self) -> str: ...

    def stamp(self) -> str: ...


class RealClock:
    def utc_now_iso(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def stamp(self) -> str:
        return time.strftime("%Y%m%d-%H%M%S", time.gmtime())


class Sleeper(Protocol):
    def sleep(self, seconds: float) -> None: ...


class RealSleeper:
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


# ---------------------------------------------------------------- FS（注入点）


class FS(Protocol):
    def exists(self, path: Path) -> bool: ...
    def is_file(self, path: Path) -> bool: ...
    def is_symlink(self, path: Path) -> bool: ...
    def size(self, path: Path) -> int: ...
    def sha256(self, path: Path) -> str: ...
    def read_text(self, path: Path) -> str: ...
    def write_text_atomic(self, path: Path, text: str) -> None: ...
    def mkdirs(self, path: Path) -> None: ...


class RealFS:
    """真实文件系统：sha256 流式分块；写盘走同目录 tmp + os.replace。"""

    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_file(self, path: Path) -> bool:
        return path.is_file()

    def is_symlink(self, path: Path) -> bool:
        return path.is_symlink()

    def size(self, path: Path) -> int:
        return path.stat().st_size

    def sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def write_text_atomic(self, path: Path, text: str) -> None:
        tmp_path = path.with_name(f".{path.name}.tmp")
        if tmp_path.is_symlink():
            raise ReportPathError("symlink-tmp")
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)

    def mkdirs(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 路径安全（artifact 目录边界）


class ReportPathError(RuntimeError):
    """报告/备份路径非法（symlink 组件 / 越界 stem）——可见拒绝，零写入。"""


def validate_artifact_path(candidate: Path) -> tuple[Path | None, str | None]:
    """候选路径必须落在 ARTIFACT_DIR 内：相对名视为锚定目录内文件名；
    含 ``..`` → path-escape；词法边界先行（原始未 resolve 路径必须在锚定
    目录内——resolve 把 symlinked root/component 折叠出的"resolve 后在内"
    不放行）；resolve 仅作第二道防线（绝对路径/盘符漂移 → outside）。"""
    path = Path(candidate)
    if not path.is_absolute():
        path = ARTIFACT_DIR / path
    if ".." in path.parts:
        return None, "path-escape"
    try:
        path.relative_to(ARTIFACT_DIR)  # 词法包含，零 symlink 折叠
    except ValueError:
        return None, "outside-artifact-dir"
    resolved_root = ARTIFACT_DIR.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return None, "outside-artifact-dir"
    return resolved, None


def artifact_symlink_problem(path: Path, fs: FS) -> str | None:
    """symlink 组件检查：目标本身 → symlink-target；任何中间组件（含
    .verify / artifacts / 锚定目录自身）→ symlink-in-path。从 REPO_ROOT 沿
    未 resolve 的原始组件逐级检查——resolve 折叠不掉 symlinked
    root/component。"""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = ARTIFACT_DIR / candidate
    if fs.is_symlink(candidate):
        return "symlink-target"
    try:
        relative = candidate.relative_to(REPO_ROOT)
    except ValueError:
        return None  # 越界判定由 validate_artifact_path 负责
    current = REPO_ROOT
    for part in relative.parts[:-1]:
        current = current / part
        if fs.is_symlink(current):
            return "symlink-in-path"
    return None


def rollback_artifact_defect(path: Path, fs: FS, *, prefix: str) -> str | None:
    """R2-5：rollback 工件（.sha256/.manifest.json）独立 fail-closed 缺陷
    判定——resolve 边界 + symlink（目标/组件）+ 存在 + 常规文件，任一不
    满足返回 ``{prefix}-<defect>`` 问题码；全满足返回 None。"""
    resolved, path_problem = validate_artifact_path(path)
    if resolved is None or path_problem is not None:
        return f"{prefix}-path-{path_problem}"
    if fs.is_symlink(path):
        return f"{prefix}-symlink-target"
    if artifact_symlink_problem(path, fs) is not None:
        return f"{prefix}-symlink-in-path"
    if not fs.exists(resolved):
        return f"{prefix}-missing"
    if not fs.is_file(resolved):
        return f"{prefix}-not-regular-file"
    return None


# ---------------------------------------------------------------- 日志（防御性脱敏）


SECRET_SHAPE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"), "[REDACTED:token]"),
    (re.compile(r"(?i)(api[_-]?key|secret|password)\s*[=:]\s*\S{8,}"),
     "[REDACTED:credential]"),
)


def redact_secrets(text: str) -> str:
    for pattern, label in SECRET_SHAPE_PATTERNS:
        text = pattern.sub(label, text)
    return text


class SafeLog:
    """行式日志：stdout 输出前经 redact_secrets 防御性脱敏。"""

    def __init__(self, echo: bool = True) -> None:
        self.lines: list[str] = []
        self._echo = echo

    def say(self, message: str) -> None:
        line = redact_secrets(message)
        self.lines.append(line)
        if self._echo:
            print(f"{TAG} {line}", flush=True)


# ---------------------------------------------------------------- 卷属主普查


_CENSUS_LINE_RE = re.compile(
    r"^(?P<perm>[bcdlps-][rwxSsTt-]{9})\s+\d+\s+(?P<uid>\d+)\s+(?P<gid>\d+)\s")


def classify_census(stdout: str) -> tuple[str, list[str]]:
    """``ls -lnAR /probe`` 输出 →（分类, 观察到的 uid 集合）。
    全 0 → root；全 1000 → uid1000；并存 → mixed；无文件行 → unknown。"""
    uids: set[str] = set()
    for line in stdout.splitlines():
        match = _CENSUS_LINE_RE.match(line.strip())
        if match:
            uids.add(match.group("uid"))
    if not uids:
        return "unknown", []
    if uids == {"0"}:
        return "root", sorted(uids)
    if uids == {"1000"}:
        return "uid1000", sorted(uids)
    return "mixed", sorted(uids)


def run_census(gated: Runner) -> tuple[str, list[str], str | None]:
    """只读递归普查（--network none + 卷 :ro + --user 0:0 + 本地固定镜像）。
    返回（分类, uids, 失败码或 None）。"""
    result = safe_run(gated, CENSUS_ARGV, timeout=CENSUS_TIMEOUT_SECONDS)
    if result.returncode != 0:
        return "unknown", [], "volume-census-failed"
    classification, uids = classify_census(result.stdout)
    problem = None if classification in ("root", "uid1000") else \
        f"volume-ownership-{classification}"
    return classification, uids, problem


# ------------------------------------------------- 控制面文件预停机门（R2-4）

CONTROL_FILES: tuple[tuple[str, Path], ...] = (
    ("compose_file", COMPOSE_FILE),
    ("env_file", ENV_FILE),
)


def verify_control_files(fs: FS, facts: dict[str, object], log: SafeLog) -> bool:
    """停 minio 之前核对固定控制面文件（COMPOSE_FILE/ENV_FILE）仅元数据：
    存在 + 常规文件 + 非 symlink。内容绝不读取、绝不输出（env secret
    边界不变）。任一不满足 → fail-closed 零停机。"""
    for label, path in CONTROL_FILES:
        if fs.is_symlink(path):
            add_problem(facts, f"control-file-symlink:{label}",
                        f"{path.name} 是 symlink（拒绝，零停机）", log)
            return False
        if not fs.exists(path):
            add_problem(facts, f"control-file-missing:{label}",
                        f"{path} 不存在（拒绝，零停机）", log)
            return False
        if not fs.is_file(path):
            add_problem(facts, f"control-file-not-regular:{label}",
                        f"{path.name} 非常规文件（拒绝，零停机）", log)
            return False
    add_check(facts, "control-files-verified", "pass",
              "COMPOSE_FILE/ENV_FILE 元数据核对通过（存在+常规文件+非 "
              "symlink；内容未读取）")
    return True


# ---------------------------------------------------------------- 报告


def safe_run(gated: Runner, argv: tuple[str, ...], *, timeout: float) -> CommandResult:
    """Runner 异常 → 安全类别化的 rc=1 结果（绝不保留异常文本）。"""
    try:
        return gated.run(argv, timeout=timeout)
    except (RunnerError, OSError) as cause:
        category, exc_name = categorize_runner_exception(cause)
        return CommandResult(argv, 1, "", f"{category}:{exc_name}")


def stderr_tail(stderr: str, limit: int = 200) -> str | None:
    lines = [line for line in stderr.splitlines() if line.strip()]
    if not lines:
        return None
    return lines[-1][:limit]


def new_facts() -> dict[str, object]:
    return {"status": "fail", "checks": [], "problems": [], "census": None,
            "backup": None, "baseline": None, "post": None, "restore": None,
            "rollback_hint": None, "manual_recovery_guidance": None}


def add_check(facts: dict[str, object], check_id: str, status: str,
              detail: str, severity: str = "high") -> None:
    checks = facts["checks"]
    assert isinstance(checks, list)
    checks.append({"check_id": check_id, "status": status,
                   "severity": severity, "detail": redact_secrets(detail)})


def add_problem(facts: dict[str, object], code: str, detail: str,
                log: SafeLog, tail: str | None = None) -> None:
    problems = facts["problems"]
    assert isinstance(problems, list)
    problem: dict[str, str] = {"code": code, "detail": redact_secrets(detail)}
    if tail:
        problem["stderr_tail"] = redact_secrets(tail)
    problems.append(problem)
    facts["status"] = "fail"
    log.say(f"FAIL {code}: {redact_secrets(detail)}")


def collect_container_baseline(gated: Runner, facts: dict[str, object],
                               log: SafeLog, *,
                               minio_health_required: bool = True
                               ) -> dict[str, str] | None:
    """六容器只读基线：每容器 Id（记录前 .strip() 归一）。execute 预检
    （minio_health_required=True）要求六容器全 healthy；rollback 预检只要求
    minio 容器存在（记录 ID——失败 execute 后 minio 可能 stopped/
    unhealthy），其余五容器仍必须存在且 healthy。"""
    baseline: dict[str, str] = {}
    for service in STACK_SERVICES:
        name = container_name(service)
        rid = safe_run(gated, ("docker", "inspect", name, "--format", "{{.Id}}"),
                       timeout=INSPECT_TIMEOUT_SECONDS)
        if rid.returncode != 0:
            add_problem(facts, f"container-missing:{service}",
                        "目标容器不存在（compose 项目面漂移）", log, stderr_tail(rid.stderr))
            return None
        baseline[service] = rid.stdout.strip()
        if service == PROD_SERVICE and not minio_health_required:
            continue
        health = safe_run(
            gated, ("docker", "inspect", name, "--format", "{{.State.Health.Status}}"),
            timeout=INSPECT_TIMEOUT_SECONDS)
        status = health.stdout.strip()
        if health.returncode != 0 or status != "healthy":
            detail = ("迁移前必须六容器全 healthy" if minio_health_required
                      else "除 minio 外五容器必须 healthy（minio 容器只需存在）")
            add_problem(facts, f"container-unhealthy:{service}",
                        f"health={status or 'unknown'}（{detail}）",
                        log, stderr_tail(health.stderr))
            return None
    if minio_health_required:
        add_check(facts, "six-container-health", "pass",
                  "六容器 healthy，基线 ID 已记录（.strip() 归一）")
    else:
        add_check(facts, "five-container-health", "pass",
                  "五容器 healthy + minio 容器存在（ID 已记录，健康不作前置）")
    return baseline


def wait_minio_healthy(gated: Runner, facts: dict[str, object], log: SafeLog,
                       sleeper: Sleeper) -> bool:
    for _ in range(HEALTH_ATTEMPTS):
        result = safe_run(
            gated, ("docker", "inspect", MINIO_CONTAINER,
                    "--format", "{{.State.Health.Status}}"),
            timeout=INSPECT_TIMEOUT_SECONDS)
        if result.returncode == 0 and result.stdout.strip() == "healthy":
            add_check(facts, "health-wait-minio", "pass", "minio 容器恢复 healthy")
            return True
        sleeper.sleep(HEALTH_INTERVAL_SECONDS)
    add_problem(facts, "health-wait-timeout",
                f"minio 未在 {HEALTH_ATTEMPTS} 次轮询内恢复 healthy", log)
    return False


def verify_post_state(gated: Runner, facts: dict[str, object], log: SafeLog,
                      baseline: dict[str, str],
                      pinned_image_id: str) -> bool:
    """后验：六容器 post ID 全记录（.strip() 归一）；重建 minio 容器实际
    镜像 ID（{{.Image}}）必须与预检本地固定镜像 ID 精确相等——不一致
    fail-closed；五容器（除 minio）健康且 ID 不变。"""
    post: dict[str, str] = {}
    for service in STACK_SERVICES:
        rid = safe_run(gated, ("docker", "inspect", container_name(service),
                               "--format", "{{.Id}}"),
                       timeout=INSPECT_TIMEOUT_SECONDS)
        if rid.returncode != 0:
            add_problem(facts, f"post-container-missing:{service}",
                        "后验容器缺失", log, stderr_tail(rid.stderr))
            return False
        post[service] = rid.stdout.strip()
    actual = safe_run(
        gated, ("docker", "inspect", MINIO_CONTAINER, "--format", "{{.Image}}"),
        timeout=INSPECT_TIMEOUT_SECONDS)
    if actual.returncode != 0:
        add_problem(facts, "post-minio-image-inspect-failed",
                    "重建 minio 容器实际镜像 ID 读取失败", log,
                    stderr_tail(actual.stderr))
        return False
    actual_image_id = actual.stdout.strip()
    facts["post"] = {"containers": post, "minio_image_id": actual_image_id}
    if actual_image_id != pinned_image_id:
        add_problem(facts, "post-minio-image-mismatch",
                    "重建 minio 容器实际镜像 ID ≠ 预检本地固定镜像 ID"
                    "（精确比对，绝不带病放行）", log)
        return False
    add_check(facts, "post-minio-image", "pass",
              f"重建 minio 容器镜像 == 预检固定镜像（{pinned_image_id}）")
    for service in OTHER_SERVICES:
        health = safe_run(
            gated, ("docker", "inspect", container_name(service),
                    "--format", "{{.State.Health.Status}}"),
            timeout=INSPECT_TIMEOUT_SECONDS)
        if health.returncode != 0 or health.stdout.strip() != "healthy":
            add_problem(facts, f"post-unhealthy:{service}",
                        "后验健康失败（minio 之外容器被扰动）", log,
                        stderr_tail(health.stderr))
            return False
    replaced = [s for s in OTHER_SERVICES if post[s] != baseline[s]]
    if replaced:
        add_problem(facts, "container-replaced:" + ",".join(replaced),
                    "除 minio 外容器 ID 发生变化（严禁重建被违反）", log)
        return False
    add_check(facts, "post-ids-unchanged", "pass",
              "除 minio 外五容器 ID 与基线一致")
    return True


# ---------------------------------------------------------------- execute 状态机


def run_execute(gated: Runner, fs: FS, log: SafeLog, sleeper: Sleeper,
                surface: Surface) -> dict[str, object]:
    facts = new_facts()

    engine = safe_run(gated, VERSION_ARGV, timeout=ENGINE_TIMEOUT_SECONDS)
    if engine.returncode != 0:
        add_problem(facts, "engine-unavailable", "Docker 引擎不可用", log,
                    stderr_tail(engine.stderr))
        return facts
    add_check(facts, "engine-version", "pass", f"server={engine.stdout.strip()}")

    image = safe_run(gated, IMAGE_ID_ARGV, timeout=INSPECT_TIMEOUT_SECONDS)
    if image.returncode != 0:
        # 镜像缺失绝不隐式 pull（结构性杜绝）——与 production_recovery 词汇交叉锁定
        add_problem(facts, LOCAL_IMAGE_MISSING,
                    f"本地固定镜像 {SELF_IMAGE_REF} 缺失（绝不隐式 pull）", log,
                    stderr_tail(image.stderr))
        return facts
    image_id = image.stdout.strip()
    add_check(facts, "local-image", "pass", f"image_id={image_id}")

    baseline = collect_container_baseline(gated, facts, log)
    if baseline is None:
        return facts
    facts["baseline"] = {"image_id": image_id, "containers": baseline}

    classification, uids, census_problem = run_census(gated)
    facts["census"] = {"classification": classification, "uids": uids}
    if census_problem is not None:
        add_problem(facts, census_problem,
                    f"卷属主普查分类={classification}（mixed/unknown/失败一律 "
                    "fail-closed，零变更）", log)
        return facts
    needs_chown = classification == "root"
    add_check(facts, "volume-census", "pass",
              f"分类={classification}（uids={uids}）")

    # 备份路径先校验/预备（词法边界 + symlink 组件 + mkdirs）——任何路径
    # 失败都发生在停容器之前，零容器变更。
    backup_candidate = ARTIFACT_DIR / f"{BACKUP_STEM_PREFIX}{surface.stamp}.tar"
    tar_path, path_problem = validate_artifact_path(backup_candidate)
    if tar_path is None or path_problem is not None:
        add_problem(facts, f"backup-path-{path_problem}", "备份路径越界/非法", log)
        return facts
    symlink_problem = artifact_symlink_problem(backup_candidate, fs)
    if symlink_problem is not None:
        add_problem(facts, f"backup-path-{symlink_problem}",
                    "备份路径含 symlink 组件", log)
        return facts
    fs.mkdirs(ARTIFACT_DIR)
    add_check(facts, "backup-path-prepared", "pass",
              f"备份路径已校验/预备（停容器之前）：{tar_path.name}")

    # R2-4：停 minio 之前核对控制面文件元数据（存在+常规文件+非 symlink；
    # 内容绝不读取）——不满足零停机。
    if not verify_control_files(fs, facts, log):
        return facts

    stop = safe_run(gated, STOP_ARGV, timeout=STOP_TIMEOUT_SECONDS)
    if stop.returncode != 0:
        add_problem(facts, "stop-minio-failed", "停仅 minio 失败", log,
                    stderr_tail(stop.stderr))
        return facts
    add_check(facts, "stop-minio", "pass", "仅 minio 容器已停止")

    assert surface.helper_tar_name is not None and surface.host_backup_dir is not None
    backup_argv = tar_helper_argv(surface.helper_tar_name,
                                  surface.host_backup_dir, extract=False)
    backup = safe_run(gated, backup_argv, timeout=TAR_TIMEOUT_SECONDS)
    if backup.returncode != 0:
        add_problem(facts, "backup-tar-failed", "root helper tar 备份失败（不 chown）",
                    log, stderr_tail(backup.stderr))
        return facts
    if not fs.exists(tar_path) or not fs.is_file(tar_path):
        add_problem(facts, "backup-file-missing",
                    "宿主侧备份 tar 未出现（不 chown）", log)
        return facts
    sha256 = fs.sha256(tar_path)
    size_bytes = fs.size(tar_path)
    sha_file = tar_path.with_name(tar_path.name + ".sha256")
    manifest_file = tar_path.with_name(tar_path.name + ".manifest.json")
    fs.write_text_atomic(sha_file, f"{sha256}  {tar_path.name}\n")
    manifest = {
        "schema_version": REPORT_SCHEMA_VERSION, "tool": TOOL_NAME,
        "volume": VOLUME_NAME, "project": PROJECT, "image_id": image_id,
        "pre_migration_owner_class": classification, "tar_file": tar_path.name,
        "sha256": sha256, "size_bytes": size_bytes,
        "created_at_utc": RealClock().utc_now_iso(),
    }
    fs.write_text_atomic(manifest_file,
                         json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    facts["backup"] = {
        "performed": True, "skipped_chown": not needs_chown,
        "tar": tar_path.name, "sha256": sha256, "size_bytes": size_bytes,
        "sha256_file": sha_file.name, "manifest_file": manifest_file.name,
    }
    add_check(facts, "backup", "pass",
              f"tar={tar_path.name} sha256={sha256} size={size_bytes}")

    if needs_chown:
        chown = safe_run(gated, CHOWN_ARGV, timeout=CHOWN_TIMEOUT_SECONDS)
        if chown.returncode != 0:
            add_problem(facts, "chown-failed", "chown -R 1000:1000 失败（不 up）",
                        log, stderr_tail(chown.stderr))
            return facts
        add_check(facts, "chown-uid1000", "pass", "chown -R 1000:1000 已执行")
        verify_class, _, verify_problem = run_census(gated)
        census_fact = facts["census"]
        assert isinstance(census_fact, dict)
        census_fact["classification_after_chown"] = verify_class
        if verify_problem is not None or verify_class != "uid1000":
            add_problem(facts, "chown-verify-failed",
                        f"复核分类={verify_class}（≠ uid1000，不 up）", log)
            return facts
        add_check(facts, "chown-verify", "pass", "递归复核全 uid1000")
    else:
        add_check(facts, "chown-skipped-uid1000", "pass",
                  "普查全 uid1000——跳过 chown（备份已强制执行）")

    up = safe_run(gated, UP_ARGV, timeout=UP_TIMEOUT_SECONDS)
    if up.returncode != 0:
        add_problem(facts, "compose-up-failed",
                    "compose --env-file/--profile local up -d --no-build "
                    "--no-deps minio 失败", log,
                    stderr_tail(up.stderr))
        return facts
    add_check(facts, "compose-up", "pass",
              "仅 minio 以 --env-file 锚定 + --profile local + --no-build "
              "--no-deps 重建")

    if not wait_minio_healthy(gated, facts, log, sleeper):
        return facts
    if not verify_post_state(gated, facts, log, baseline, image_id):
        return facts

    facts["rollback_hint"] = {
        "rollback_command": (f"python {TOOL_NAME} rollback --backup-file "
                             f"{tar_path.name} --confirm \"{CONFIRM_ROLLBACK}\""),
        "backup_tar": tar_path.name, "sha256_file": sha_file.name,
        "manifest_file": manifest_file.name,
        "note": ("manifest 记录 old image id 与迁移前属主类；manifest 缺失 → "
                 "拒绝自动回滚，仅人工恢复"),
    }
    facts["status"] = "pass"
    log.say("execute 全阶段通过：卷已迁移 uid1000 且五容器未受扰动")
    return facts


# ---------------------------------------------------------------- rollback 状态机


def run_rollback(gated: Runner, fs: FS, log: SafeLog, sleeper: Sleeper,
                 backup_file: Path) -> dict[str, object]:
    facts = new_facts()

    def refuse(code: str, detail: str, *, guidance: bool = False
               ) -> dict[str, object]:
        add_problem(facts, code, detail, log)
        if guidance:
            facts["manual_recovery_guidance"] = MANUAL_RECOVERY_GUIDANCE
            log.say("已拒绝自动恢复——人工恢复指引见报告 manual_recovery_guidance")
        return facts

    candidate = Path(backup_file)
    resolved, path_problem = validate_artifact_path(candidate)
    if resolved is None or path_problem is not None:
        return refuse(f"backup-path-{path_problem}", "备份路径越界/非法")
    # 词法组件检查用原始（未 resolve）候选路径——resolve 折叠不掉 symlink
    symlink_problem = artifact_symlink_problem(candidate, fs)
    if symlink_problem is not None:
        return refuse(f"backup-path-{symlink_problem}", "备份路径含 symlink 组件")
    if not BACKUP_STEM_RE.match(resolved.name):
        return refuse("bad-backup-filename",
                      f"文件名不匹配 {BACKUP_STEM_PREFIX}<YYYYMMDD-HHMMSS>.tar")
    if not fs.exists(resolved) or not fs.is_file(resolved):
        return refuse("backup-file-missing", "备份 tar 不存在")
    sha_file = resolved.with_name(resolved.name + ".sha256")
    # R2-5：.sha256 独立 fail-closed（边界/symlink/常规文件），缺一即拒
    sha_defect = rollback_artifact_defect(sha_file, fs, prefix="checksum-sidecar")
    if sha_defect is not None:
        return refuse(sha_defect, "伴生 .sha256 工件不满足前置条件"
                      "（越界/symlink/非常规文件/缺失）")
    recorded = fs.read_text(sha_file).split()
    actual = fs.sha256(resolved)
    if not recorded or recorded[0] != actual:
        return refuse("checksum-mismatch",
                      "校验和不匹配（记录值不回显，零副作用）")
    manifest_file = resolved.with_name(resolved.name + ".manifest.json")
    # R2-5：.manifest.json 独立 fail-closed（边界/symlink/常规文件），缺一即拒
    manifest_defect = rollback_artifact_defect(manifest_file, fs, prefix="manifest")
    if manifest_defect is not None:
        return refuse(manifest_defect, "伴生 manifest 工件不满足前置条件"
                      "（越界/symlink/非常规文件/缺失）", guidance=True)
    try:
        manifest = json.loads(fs.read_text(manifest_file))
    except ValueError:
        return refuse("manifest-unparsable", "manifest JSON 不可解析", guidance=True)
    if not isinstance(manifest, dict):
        return refuse("manifest-unparsable", "manifest 结构非法", guidance=True)
    old_image_id = manifest.get("image_id")
    if not old_image_id:
        return refuse("manifest-missing-old-image-id",
                      "manifest 未记录 old image id（禁止半恢复）", guidance=True)
    if manifest.get("volume") != VOLUME_NAME or manifest.get("project") != PROJECT:
        return refuse("manifest-target-mismatch",
                      "manifest 记录的卷/项目与固定生产面不符", guidance=True)
    expected_class = manifest.get("pre_migration_owner_class")
    if expected_class not in ("root", "uid1000"):
        return refuse("manifest-owner-class-invalid",
                      "manifest 迁移前属主类非法", guidance=True)
    facts["restore"] = {"tar": resolved.name, "expected_owner_class": expected_class,
                        "old_image_id": old_image_id}
    add_check(facts, "backup-integrity", "pass",
              f"tar={resolved.name} sha256 校验通过；manifest 完整")

    engine = safe_run(gated, VERSION_ARGV, timeout=ENGINE_TIMEOUT_SECONDS)
    if engine.returncode != 0:
        add_problem(facts, "engine-unavailable", "Docker 引擎不可用", log,
                    stderr_tail(engine.stderr))
        return facts
    image = safe_run(gated, IMAGE_ID_ARGV, timeout=INSPECT_TIMEOUT_SECONDS)
    if image.returncode != 0:
        add_problem(facts, LOCAL_IMAGE_MISSING,
                    f"本地固定镜像 {SELF_IMAGE_REF} 缺失（绝不隐式 pull）", log,
                    stderr_tail(image.stderr))
        return facts
    current_image_id = image.stdout.strip()
    # 修正 3：manifest old image id 必须等于当前本地固定镜像 id——任何漂移
    # 都在 stop/restore 等 Docker 变更之前 fail-closed（禁止跨镜像半恢复）。
    if str(old_image_id).strip() != current_image_id:
        return refuse("image-id-mismatch",
                      "manifest old image id ≠ 当前本地固定镜像 id"
                      "（镜像面漂移，禁止自动恢复）", guidance=True)
    # 修正 5：rollback 预检只要求 minio 容器存在（记录 ID，不要求 healthy
    # ——失败 execute 后 minio 可能 stopped/unhealthy）；其余五容器须 healthy。
    baseline = collect_container_baseline(gated, facts, log,
                                          minio_health_required=False)
    if baseline is None:
        return facts
    facts["baseline"] = {"image_id": current_image_id, "containers": baseline}

    # R2-4：停 minio 之前核对控制面文件元数据（存在+常规文件+非 symlink；
    # 内容绝不读取）——不满足零停机。
    if not verify_control_files(fs, facts, log):
        return facts

    stop = safe_run(gated, STOP_ARGV, timeout=STOP_TIMEOUT_SECONDS)
    if stop.returncode != 0:
        add_problem(facts, "stop-minio-failed", "停仅 minio 失败", log,
                    stderr_tail(stop.stderr))
        return facts
    add_check(facts, "stop-minio", "pass", "仅 minio 容器已停止")

    restore_argv = tar_helper_argv(resolved.name, resolved.parent, extract=True)
    restore = safe_run(gated, restore_argv, timeout=TAR_TIMEOUT_SECONDS)
    if restore.returncode != 0:
        add_problem(facts, "restore-tar-failed",
                    "恢复 tar 失败（不 up，保持 minio 停止）", log,
                    stderr_tail(restore.stderr))
        return facts
    add_check(facts, "restore-tar", "pass", f"tar={resolved.name} 已解回卷")

    classification, _, census_problem = run_census(gated)
    facts["census"] = {"classification": classification}
    if census_problem is not None or classification != expected_class:
        add_problem(facts, "restore-verify-failed",
                    f"复核分类={classification}（manifest 期望 {expected_class}，不 up）",
                    log)
        return facts
    add_check(facts, "restore-verify", "pass",
              f"恢复后属主分类与 manifest 一致（{expected_class}）")

    up = safe_run(gated, UP_ARGV, timeout=UP_TIMEOUT_SECONDS)
    if up.returncode != 0:
        add_problem(facts, "compose-up-failed",
                    "compose --env-file/--profile local up -d --no-build "
                    "--no-deps minio 失败", log,
                    stderr_tail(up.stderr))
        return facts
    if not wait_minio_healthy(gated, facts, log, sleeper):
        return facts
    if not verify_post_state(gated, facts, log, baseline, current_image_id):
        return facts

    facts["status"] = "pass"
    log.say("rollback 全阶段通过：卷内容已按备份恢复且五容器未受扰动")
    return facts


# ---------------------------------------------------------------- plan（零 subprocess / 零写盘）


PLAN_RISKS: tuple[str, ...] = (
    ("无备份即迁移 = 数据不可逆风险：execute 强制先 tar 备份（含 sha256/size/"
     "manifest）再 chown，任何备份失败都不进入 chown；备份路径在停容器之前"
     "校验/预备——路径失败零容器变更"),
    ("chown 后无法直接回退属主：回退依赖备份 tar + manifest（old image id 与"
     "迁移前属主类），manifest 缺失或 old image id ≠ 当前本地固定镜像 id "
     "（image-id-mismatch）时拒绝自动回滚（stop/restore 之前 fail-closed）"),
    ("compose 依赖链爆炸：up 必须带 --env-file env.production-recovery + "
     "--profile local + --no-build --no-deps 且仅 minio，后验除 minio 外"
     "五容器 ID 不变且重建 minio 容器实际镜像 ID 精确等于预检固定镜像 ID"),
    ("镜像缺失时隐式 pull 风险：本地固定镜像缺失即 fail-closed，"
     "结构性白名单杜绝 pull/build"),
    ("误停/误删其它服务：结构性 argv 门只允许停 minio 容器，"
     "api/web/livekit/postgres/redis 容器零写路径"),
    ("env secret 泄漏：infra/env.production-recovery 仅作锚定常量，"
     "内容绝不读取；--env-file 仅携带路径；日志/报告出口恒经 redact_secrets"),
    ("迁移窗口内 minio 停机：api 依赖 minio service_healthy，窗口应选低峰；"
     "健康轮询超时即 fail-closed"),
    ("rollback 预检不对称：execute 预检要求六容器全 healthy；rollback 只要求"
     "minio 容器存在（失败 execute 后可能 stopped/unhealthy），其余五容器"
     "仍须存在且 healthy"),
    ("helper 隐式用户/过宽挂载：本地固定镜像默认用户 minio:minio(1000)，"
     "root 属主卷上不可用——全部 helper 恒显式 --user 0:0；挂载最小化——"
     "普查/备份卷只读(:ro)，备份宿主目的地可写，恢复卷可写、宿主备份源"
     "只读(:ro)，结构性白名单逐 token 锁定这些形态"),
    ("控制面文件漂移即停机风险：停 minio 之前先核对 COMPOSE_FILE/ENV_FILE"
     "仅元数据（存在+常规文件+非 symlink；内容绝不读取/输出）——不满足"
     "零停机 fail-closed"),
    ("rollback 工件降级攻击面：备份 tar/.sha256/.manifest.json 三工件各自"
     "独立 fail-closed（resolve 边界 + symlink 拒绝 + 仅常规文件 + 存在"
     "性/校验和/manifest 语义校验），任一不满足零 Docker 副作用并给人工"
     "恢复指引"),
    "混合/未知属主不可安全迁移：mixed/unknown/普查失败一律零变更拒绝",
)

PLAN_STAGES: tuple[tuple[str, str], ...] = (
    ("preflight-engine", "docker version 只读探活"),
    ("preflight-image", "本地固定镜像 image inspect（缺失即拒，零隐式 pull）"),
    ("preflight-six-health", ("六容器 Id+Health 只读基线（必须全 healthy；"
                              "rollback 变体：minio 仅须存在，其余五容器 healthy）")),
    ("preflight-census", ("--network none + :ro 递归 uid 普查（root/uid1000/"
                          "mixed/unknown 分类，mixed/unknown fail-closed）")),
    ("backup-path-prepare", ("备份路径词法边界 + symlink 组件校验/预备"
                             "（停容器之前，路径失败零容器变更）")),
    ("control-files-verify", ("COMPOSE_FILE/ENV_FILE 仅元数据核对（存在+常规"
                             "文件+非 symlink；内容绝不读取）——不满足零停机")),
    ("stop-minio", "docker stop 仅 minio 容器"),
    ("backup", ("本地固定镜像 root helper：tar 备份卷 + 宿主 sha256/size + "
                "伴生 .sha256/.manifest.json（manifest 记录 old image id）")),
    ("chown", "chown -R 1000:1000（全 uid1000 时跳过，但备份恒执行）"),
    ("chown-verify", "递归复核必须全 uid1000，否则不 up"),
    ("compose-up", ("compose --env-file env.production-recovery --profile local "
                    "up -d --no-build --no-deps minio（仅此服务）")),
    ("health-wait", "minio 健康轮询（有界次数）"),
    ("post-verify", ("重建 minio 容器实际镜像 ID == 预检固定镜像 ID + 除 minio "
                     "外五容器 ID 不变 + 六容器健康")),
    ("report", "JSON+MD 报告原子落盘 .verify/artifacts/m14-41-minio-volume-adoption/"),
)


def build_plan_report(clock: Clock) -> dict[str, object]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION, "tool": TOOL_NAME,
        "milestone": MILESTONE, "mode": "plan",
        "generated_at_utc": clock.utc_now_iso(),
        "targets": {
            "project": PROJECT, "volume": VOLUME_NAME, "image": SELF_IMAGE_REF,
            "compose_file": str(COMPOSE_FILE), "env_file": ENV_FILE.name,
            "env_file_note": "锚定常量；--env-file 仅携带路径，内容绝不读取、绝不输出",
            "compose_profile": COMPOSE_PROFILE,
            "minio_container": MINIO_CONTAINER,
            "containers": {service: container_name(service)
                           for service in STACK_SERVICES},
            "target_uid_gid": TARGET_UID_GID,
            "artifact_dir": str(ARTIFACT_DIR),
            "argv_whitelist_note": (
                "全部外部命令逐 token 匹配固定结构白名单；pull/build/down/kill/"
                "prune/rm/exec/restart 与 api/web/livekit/postgres/redis 容器"
                "零允许路径"),
        },
        "risks": list(PLAN_RISKS),
        "stages": [{"id": stage_id, "description": description}
                   for stage_id, description in PLAN_STAGES],
        "argv_templates": {
            "census": list(CENSUS_ARGV), "stop": list(STOP_ARGV),
            "chown": list(CHOWN_ARGV), "compose_up": list(UP_ARGV),
            "backup_tar": {"image": SELF_IMAGE_REF, "user": "0:0",
                           "tar_member": "/backup/minio-data-backup-<stamp>.tar",
                           "host_bind": str(ARTIFACT_DIR),
                           "volume_mount": f"{VOLUME_NAME}:/data:ro",
                           "host_mount": f"{ARTIFACT_DIR}:/backup"},
            "restore_tar": {"image": SELF_IMAGE_REF, "user": "0:0",
                            "tar_member": "/backup/minio-data-backup-<stamp>.tar",
                            "host_bind": str(ARTIFACT_DIR),
                            "volume_mount": f"{VOLUME_NAME}:/data",
                            "host_mount": f"{ARTIFACT_DIR}:/backup:ro"},
        },
        "confirm_phrases": {"execute": CONFIRM_EXECUTE, "rollback": CONFIRM_ROLLBACK},
    }


# ---------------------------------------------------------------- 报告渲染与原子写


def render_markdown(report: dict[str, object]) -> str:
    lines = [f"# {MILESTONE} MinIO 卷属主迁移报告",
             "",
             f"- tool: `{report.get('tool')}`",
             f"- mode: **{report.get('mode')}**",
             f"- status: **{report.get('status', 'n/a')}**",
             f"- started: {report.get('started_at_utc')}",
             f"- ended: {report.get('ended_at_utc')}",
             ""]
    checks = report.get("checks")
    if isinstance(checks, list) and checks:
        lines.append("## checks")
        for check in checks:
            assert isinstance(check, dict)
            lines.append(f"- [{check.get('status')}] {check.get('check_id')}: "
                         f"{check.get('detail')}")
        lines.append("")
    problems = report.get("problems")
    if isinstance(problems, list) and problems:
        lines.append("## problems")
        for problem in problems:
            assert isinstance(problem, dict)
            lines.append(f"- `{problem.get('code')}`: {problem.get('detail')}")
        lines.append("")
    guidance = report.get("manual_recovery_guidance")
    if guidance:
        lines.extend(["## manual recovery", str(guidance), ""])
    return "\n".join(lines)


_STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def write_reports_atomic(report: dict[str, object], directory: Path, stem: str,
                         fs: FS) -> tuple[Path, Path]:
    """JSON + MD 双写：内容先经 redact_secrets 终防线；目录必须等于锚定
    ARTIFACT_DIR；symlink/越界 stem 一律 ReportPathError（零写入）。"""
    if "/" in stem or "\\" in stem or ".." in stem or not _STEM_RE.match(stem):
        raise ReportPathError("bad-stem")
    fs.mkdirs(directory)
    if directory.resolve() != ARTIFACT_DIR.resolve():
        raise ReportPathError("outside-artifact-dir")
    json_path = directory / f"{stem}.json"
    md_path = directory / f"{stem}.md"
    for path in (json_path, md_path):
        problem = artifact_symlink_problem(path, fs)
        if problem is not None:
            raise ReportPathError(problem)
    fs.write_text_atomic(json_path, redact_secrets(
        json.dumps(report, ensure_ascii=False, indent=2)))
    fs.write_text_atomic(md_path, redact_secrets(render_markdown(report)))
    return json_path, md_path


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="M14-41 MinIO 生产卷属主迁移（root → uid1000）受控工具")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("plan", help="零副作用计划（stdout 纯 JSON）")
    execute_parser = subparsers.add_parser(
        "execute", help=f'真实执行（必配 --confirm "{CONFIRM_EXECUTE}"）')
    execute_parser.add_argument(
        "--confirm", default="",
        help=f'精确确认短语："{CONFIRM_EXECUTE}"')
    rollback_parser = subparsers.add_parser(
        "rollback", help=f'受控恢复（必配 --backup-file 与 --confirm '
                         f'"{CONFIRM_ROLLBACK}"）')
    rollback_parser.add_argument("--backup-file", required=True, type=Path,
                                 help="execute 产出的备份 tar（须在锚定 artifact 目录内）")
    rollback_parser.add_argument(
        "--confirm", default="",
        help=f'精确确认短语："{CONFIRM_ROLLBACK}"')
    return parser


def build_config(mode: str, stamp: str, backup_file: str | None = None
                 ) -> dict[str, object]:
    return {
        "mode": mode, "project": PROJECT, "volume": VOLUME_NAME,
        "image": SELF_IMAGE_REF, "compose_file": str(COMPOSE_FILE),
        "env_file": ENV_FILE.name, "env_file_note": "锚定常量；内容绝不读取",
        "compose_profile": COMPOSE_PROFILE,
        "artifact_dir": str(ARTIFACT_DIR), "stamp": stamp,
        "backup_file": backup_file,
    }


def build_report(*, mode: str, started: str, ended: str,
                 config: dict[str, object], payload: dict[str, object]
                 ) -> dict[str, object]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION, "tool": TOOL_NAME,
        "milestone": MILESTONE, "mode": mode,
        "started_at_utc": started, "ended_at_utc": ended,
        "config": config, **payload,
    }


def main(argv: list[str] | None = None, *, runner: Runner | None = None,
         fs: FS | None = None, clock: Clock | None = None,
         sleeper: Sleeper | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        # plan：零 subprocess、零写盘——stdout 只输出纯 JSON（无 TAG 行）
        print(json.dumps(build_plan_report(clock or RealClock()),
                         ensure_ascii=False, indent=2), flush=True)
        return EXIT_OK

    log = SafeLog()
    clock = clock or RealClock()
    stamp = clock.stamp()
    backup_file_arg = str(getattr(args, "backup_file", "") or "") or None

    if args.command == "execute":
        if args.confirm != CONFIRM_EXECUTE:
            log.say(f'拒绝: execute 必配 --confirm "{CONFIRM_EXECUTE}"'
                    "（精确匹配，当前不匹配）——零副作用")
            return EXIT_REJECT
        surface = Surface(stamp=stamp,
                          helper_tar_name=f"{BACKUP_STEM_PREFIX}{stamp}.tar",
                          host_backup_dir=ARTIFACT_DIR)
        log.say(f"=== M14-41 MinIO 卷属主迁移 EXECUTE: project={PROJECT} ===")
        payload = run_execute(GateRunner(runner or RealRunner(), surface),
                              fs or RealFS(), log, sleeper or RealSleeper(),
                              surface)
    else:
        if args.confirm != CONFIRM_ROLLBACK:
            log.say(f'拒绝: rollback 必配 --confirm "{CONFIRM_ROLLBACK}"'
                    "（精确匹配，当前不匹配）——零副作用")
            return EXIT_REJECT
        candidate = Path(args.backup_file)
        surface = Surface(stamp=stamp, helper_tar_name=candidate.name,
                          host_backup_dir=candidate.parent
                          if candidate.is_absolute() else ARTIFACT_DIR)
        log.say(f"=== M14-41 MinIO 卷属主迁移 ROLLBACK: backup={candidate.name} ===")
        payload = run_rollback(GateRunner(runner or RealRunner(), surface),
                               fs or RealFS(), log, sleeper or RealSleeper(),
                               candidate)

    report = build_report(mode=args.command, started=clock.utc_now_iso(),
                          ended=clock.utc_now_iso(),
                          config=build_config(args.command, stamp, backup_file_arg),
                          payload=payload)
    try:
        write_reports_atomic(report, ARTIFACT_DIR, f"{args.command}-{stamp}",
                             fs or RealFS())
    except (ReportPathError, OSError) as cause:
        log.say(f"报告写入失败: {type(cause).__name__}（已完成事实仅见于上方日志）")
        return EXIT_REJECT
    ok = payload.get("status") == "pass"
    log.say(f"=== 结果: {args.command} {payload.get('status')}"
            "（≠ production ready） ===")
    return EXIT_OK if ok else EXIT_REJECT


if __name__ == "__main__":
    sys.exit(main())
