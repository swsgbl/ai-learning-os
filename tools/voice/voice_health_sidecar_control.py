#!/usr/bin/env python
"""M14-26 WSL 语音健康 sidecar 的 Windows 侧编排控制器：start / status / stop。

背景（M14-25 回填实证，2026-09-14）：Windows→WSL loopback 转发（wslrelay）
间歇 5s 超时 / 0x8007274c，7 轮失败窗口；sidecar（voice_health_sidecar.py）
跑在 WSL 内绑 eth0，为监控提供不经 wslrelay 的直达健康路径。本控制器
负责其在 Windows 侧的受控生命周期，不改动现有引擎与 relay。

命令纪律（契约测试锁定）：
- 只经**固定 allowlist 的 wsl.exe 命令形态**编排（argv 直传，无 bash、
  无 shell、无 ``shell=True``）：
  * start：``wsl.exe --distribution Ubuntu --cd <repo> --exec python3 -u
    tools/voice/voice_health_sidecar.py --status-file <repo-relative>``；
    Windows 上 CREATE_NO_WINDOW（不弹控制台窗）；
  * probe：``wsl.exe … --exec python3 -c <PROBE_CODE> <pid|-> <status>``——
    读 sidecar 落档的 status JSON + /proc/<pid> 存在性与 cmdline；
  * signal：``wsl.exe … --exec python3 -c <SIGNAL_CODE> <pid> <SIG 名>``——
    信号名 Python 侧白名单（SIGTERM/SIGKILL），PID 为 int 校验值。
- start 幂等：manifest 归属成立（PID 活 + 身份标记 + 双端口健康 200）→
  不重复 spawn；存活但健康未全 200 → 视为启动中，同样不重复 spawn；
  manifest PID 已死/被无关进程复用 → 清理后放行全新启动。
- WSL 管理面失败（wsl.exe 超时/连接错误/rc≠0/输出不可解析）统一分类
  ``wsl-management-unavailable``，单次尝试、不激进重试（rc 3 拒绝）。
- stop 只停「manifest 归属 且 cmdline 含身份标记」的目标，TERM → 有界
  宽限 → 单次 KILL 兜底；**生产保护硬边界**：FunASR PID 867、CosyVoice
  PID 26008（PROTECTED_PIDS）与 cmdline 含生产标记（wslrelay/docker/
  wsl/engine bootstrap/systemd 等，PROTECTED_MARKERS）的目标一律拒绝
  （零信号、manifest 保留）；PID 被无关进程复用同样拒绝。绝不 pkill /
  killall / fuser / 按端口杀 / 碰无 manifest 的进程。
- artifacts（gitignored ``.verify/artifacts/m14-26-voice-health-sidecar/``）：
  sidecar-status.json（sidecar 自落）、sidecar-manifest.json（本控制器
  事实源，schema 版本化 + 原子写）、sidecar-control.log（编排日志）。
  读写均拒符号链接与路径越界（safe_join/resolve 核验）。不落任何密钥、
  原始环境变量或生产日志内容。
- 写路径命令（start/stop）的 --artifacts-dir 须位于仓库内（status 只读
  不受限）；测试豁免开关 VOICE_SIDECAR_ALLOW_OUTSIDE_ARTIFACTS 仅测试用。

退出码：0 成功/幂等无操作；1 操作失败（启动超时/停止失败）；2 参数错误；
3 安全拒绝（保护目标/身份不符/并发锁/WSL 管理不可用）。status 恒只读
（不写不杀不清理），退出码不反映健康度。

可测性：Runner / HealthProber 全部可注入——测试以 FakeRunner/FakeHealth
覆盖全部分支，零真实 wsl.exe/网络/进程触碰。

用法（仓库根）：
  python tools/voice/voice_health_sidecar_control.py status
  python tools/voice/voice_health_sidecar_control.py start
  python tools/voice/voice_health_sidecar_control.py stop
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO
from urllib import error as urllib_error
from urllib import request as urllib_request

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 3

REPO_ROOT = Path(__file__).resolve().parents[2]
TAG = "[voice-sidecar-ctl]"

DISTRO = "Ubuntu"
SIDECAR_SCRIPT = "tools/voice/voice_health_sidecar.py"
ARTIFACTS_RELPATH = ".verify/artifacts/m14-26-voice-health-sidecar"
STATUS_NAME = "sidecar-status.json"
MANIFEST_NAME = "sidecar-manifest.json"
LOG_NAME = "sidecar-control.log"
LOCK_NAME = "control.lock"
#: sidecar 双端口（与 voice_health_sidecar.py 的 PORT_ROUTES 同源事实）
EXPECTED_PORTS = (18010, 18011)
#: 身份标记：目标 cmdline 必须全部包含（缺失即身份不符）
IDENTITY_MARKERS = ("python3", "voice_health_sidecar.py")
#: 生产保护 PID（M14-26 委托硬边界：FunASR 867 / CosyVoice 26008 绝不成为目标）
PROTECTED_PIDS = frozenset({867, 26008})
#: 生产保护标记：cmdline 命中任一即拒绝（防 manifest 指向引擎/relay/WSL 底座）
PROTECTED_MARKERS = (
    "wslrelay",
    "wsl.exe",
    "wslservice",
    "vmmem",
    "docker",
    "funasr",
    "cosyvoice",
    "bootstrap_funasr_wsl.sh",
    "bootstrap_cosyvoice_wsl.sh",
    "systemd",
)
WSL_MANAGEMENT_UNAVAILABLE = "wsl-management-unavailable"
ALLOWED_SIGNAL_NAMES = ("SIGTERM", "SIGKILL")

#: spawn 后等待 sidecar 落 status 文件的时限（含 wsl.exe 启动开销）
START_STATUS_TIMEOUT_SECONDS = 20.0
#: stop 的 TERM 宽限（秒）——宽限内 /proc 消失即优雅退出，超时才单次 KILL
STOP_GRACE_SECONDS = 10.0
#: KILL 后确认死亡的有界等待（秒）
KILL_CONFIRM_SECONDS = 5.0
PROBE_TIMEOUT_SECONDS = 15.0
SIGNAL_TIMEOUT_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 0.5
LOCK_STALE_SECONDS = 600.0
HEALTH_TIMEOUT_SECONDS = 5.0

#: 测试豁免开关：仓库外 --artifacts-dir 仅测试可用（生产勿设）
ALLOW_OUTSIDE_ARTIFACTS_ENV = "VOICE_SIDECAR_ALLOW_OUTSIDE_ARTIFACTS"
MUTATING_COMMANDS = frozenset({"start", "stop"})

#: 固定探测脚本（WSL 内 python3 -c 执行；argv: <pid|-> <status 文件路径>）。
#: 只读：/proc/<pid> 存在性 + cmdline（NUL→空格）+ sidecar status JSON。
PROBE_CODE = (
    'import json,os,sys\n'
    'out={"pid_alive":None,"cmdline":None,"status":None}\n'
    'pid=sys.argv[1]\n'
    'path=sys.argv[2]\n'
    'if pid!="-":\n'
    '    out["pid_alive"]=os.path.isdir("/proc/"+pid)\n'
    '    try:\n'
    '        raw=open("/proc/"+pid+"/cmdline","rb").read()\n'
    '        text=raw.replace(b"\\x00",b" ").decode("utf-8","replace").strip()\n'
    '        out["cmdline"]=text or None\n'
    '    except OSError:\n'
    '        out["pid_alive"]=False\n'
    'try:\n'
    '    out["status"]=json.load(open(path,encoding="utf-8"))\n'
    'except (OSError,ValueError):\n'
    '    pass\n'
    'print(json.dumps(out,ensure_ascii=False))'
)

#: 固定信号脚本（WSL 内 python3 -c 执行；argv: <pid> <信号名>）。
#: 信号名仅允许 SIGTERM/SIGKILL（Python 侧白名单后才拼入 argv）。
SIGNAL_CODE = (
    'import os,signal,sys\n'
    'os.kill(int(sys.argv[1]),getattr(signal,sys.argv[2]))\n'
    'print("signaled")'
)


class RunnerError(RuntimeError):
    """wsl.exe 编排面失败——classification 恒 wsl-management-unavailable。"""

    def __init__(self, classification: str, detail: str) -> None:
        super().__init__(f"{classification}: {detail}")
        self.classification = classification
        self.detail = detail


class UnsafePathError(RuntimeError):
    """artifacts 目标不安全（符号链接/路径越界/非普通文件）。"""


def creation_flags() -> int:
    """Windows 上 CREATE_NO_WINDOW（不弹控制台窗）；非 Windows 平台为 0。"""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------- 固定 allowlist 的 wsl.exe 命令形态（argv 直传，零 shell） ----------


def build_start_argv(repo_root: Path, status_relpath: str) -> list[str]:
    """start 唯一命令形态：--exec python3 -u 直跑 sidecar（--cd 免路径转换）。"""
    return [
        "wsl.exe",
        "--distribution",
        DISTRO,
        "--cd",
        str(repo_root),
        "--exec",
        "python3",
        "-u",
        SIDECAR_SCRIPT,
        "--status-file",
        status_relpath,
    ]


def build_probe_argv(repo_root: Path, pid_or_dash: str, status_relpath: str) -> list[str]:
    """probe 命令形态：pid_or_dash 为 '-' 时只读 status 文件（等待落档用）。"""
    return [
        "wsl.exe",
        "--distribution",
        DISTRO,
        "--cd",
        str(repo_root),
        "--exec",
        "python3",
        "-c",
        PROBE_CODE,
        pid_or_dash,
        status_relpath,
    ]


def build_signal_argv(repo_root: Path, pid: int, name: str) -> list[str]:
    """signal 命令形态：pid 为 int 校验值，name ∈ ALLOWED_SIGNAL_NAMES。"""
    if name not in ALLOWED_SIGNAL_NAMES:
        raise ValueError(f"信号名不在白名单: {name}")
    if not isinstance(pid, int) or pid <= 0:
        raise ValueError(f"PID 非法: {pid!r}")
    return [
        "wsl.exe",
        "--distribution",
        DISTRO,
        "--cd",
        str(repo_root),
        "--exec",
        "python3",
        "-c",
        SIGNAL_CODE,
        str(pid),
        name,
    ]


class Runner:
    """真实编排实现：三类固定命令（spawn / probe / signal），零 shell。

    任何 wsl.exe 超时 / OSError / rc≠0 / 输出不可解析 → RunnerError
    （wsl-management-unavailable，单次尝试——重试策略由调用方明确禁用）。
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def spawn_sidecar(self, argv: list[str], log_path: Path) -> subprocess.Popen:
        """启动 sidecar（固定 start argv）；stdout/stderr 追加进编排日志。"""
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = log_path.open("ab")
        except OSError as cause:
            raise RunnerError(
                WSL_MANAGEMENT_UNAVAILABLE, f"日志不可写: {type(cause).__name__}"
            ) from cause
        try:
            return subprocess.Popen(
                argv,
                stdout=handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=creation_flags(),
                close_fds=True,
            )
        except OSError as cause:
            raise RunnerError(
                WSL_MANAGEMENT_UNAVAILABLE, f"wsl.exe 启动失败: {type(cause).__name__}"
            ) from cause
        finally:
            handle.close()  # 子进程持有继承句柄；父侧即时关闭

    def probe(self, pid_or_dash: str, status_relpath: str) -> dict:
        """运行 PROBE_CODE：返回 {pid_alive, cmdline, status}；失败即 RunnerError。"""
        argv = build_probe_argv(self.repo_root, pid_or_dash, status_relpath)
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=PROBE_TIMEOUT_SECONDS,
                creationflags=creation_flags(),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as cause:
            raise RunnerError(
                WSL_MANAGEMENT_UNAVAILABLE, f"probe 失败: {type(cause).__name__}"
            ) from cause
        if result.returncode != 0:
            raise RunnerError(
                WSL_MANAGEMENT_UNAVAILABLE, f"probe rc={result.returncode}"
            )
        lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
        try:
            payload = json.loads(lines[-1])
        except (IndexError, ValueError) as cause:
            raise RunnerError(
                WSL_MANAGEMENT_UNAVAILABLE, "probe 输出不可解析"
            ) from cause
        if not isinstance(payload, dict):
            raise RunnerError(WSL_MANAGEMENT_UNAVAILABLE, "probe 输出非对象")
        return payload

    def send_signal(self, pid: int, name: str) -> None:
        """运行 SIGNAL_CODE（TERM/KILL）；失败/超时即 RunnerError。"""
        argv = build_signal_argv(self.repo_root, pid, name)
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=SIGNAL_TIMEOUT_SECONDS,
                creationflags=creation_flags(),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as cause:
            raise RunnerError(
                WSL_MANAGEMENT_UNAVAILABLE, f"signal 失败: {type(cause).__name__}"
            ) from cause
        if result.returncode != 0:
            raise RunnerError(
                WSL_MANAGEMENT_UNAVAILABLE, f"signal rc={result.returncode}"
            )

    def kill_spawned(self, popen: subprocess.Popen) -> None:
        """仅终止**本控制器自己 spawn 的** wsl.exe 句柄（启动失败回收；
        绝不触碰任何其他进程——生产保护边界的收编前兜底）。"""
        try:
            popen.kill()
        except OSError:
            pass


# ---------- 健康探测（零代理 opener；同 voice_service_control 口径） ----------

_HEALTH_OPENER = urllib_request.build_opener(urllib_request.ProxyHandler({}))


class HealthProber:
    """GET http://<bind>:<port>/health → (状态码|None, body 片段)。"""

    def probe(self, bind: str, port: int) -> tuple[int | None, str]:
        url = f"http://{bind}:{port}/health"
        try:
            with _HEALTH_OPENER.open(url, timeout=HEALTH_TIMEOUT_SECONDS) as response:
                return (
                    int(response.status),
                    response.read(4096).decode("utf-8", "replace"),
                )
        except urllib_error.HTTPError as cause:
            body = cause.read(4096).decode("utf-8", "replace") if cause.fp else ""
            return int(cause.code), body
        except OSError as cause:  # URLError 亦属 OSError（统一连接层失败口径）
            return None, type(cause).__name__


# ---------- manifest（schema 版本化 + 原子写 + 符号链接拒绝） ----------


@dataclass
class Manifest:
    """一次受控 start 的事实记录（sidecar 身份 = status 落档的 PID/bind/端口）。"""

    pid: int
    bind: str
    ports: list[int]
    started_at: str
    log: str

    def to_payload(self) -> dict:
        return {
            "schema_version": 1,
            "service": "voice-health-sidecar",
            "pid": self.pid,
            "bind": self.bind,
            "ports": list(self.ports),
            "started_at": self.started_at,
            "log": self.log,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Manifest | None:
        """解析 manifest；结构不合法返回 None（调用方按损坏处理）。"""
        try:
            payload = json.loads(text)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        try:
            manifest = cls(
                pid=int(payload["pid"]),
                bind=str(payload["bind"]),
                ports=[int(p) for p in payload["ports"]],
                started_at=str(payload.get("started_at", "")),
                log=str(payload.get("log", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if manifest.pid <= 0 or not manifest.bind or not manifest.ports:
            return None
        return manifest


# ---------- artifacts 路径安全（safe_join / 原子写 / 符号链接拒绝） ----------


def artifacts_default_dir(repo_root: Path) -> Path:
    return repo_root / ARTIFACTS_RELPATH


def safe_join(root: Path, name: str) -> Path:
    """目录内文件名拼接：拒绝绝对路径 / 分隔符 / '..' / 越出 root（resolve 核验）。"""
    if not name or Path(name).is_absolute() or Path(name).name != name:
        raise UnsafePathError(f"非法文件名: {name!r}")
    candidate = root / name
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        raise UnsafePathError(f"越出 artifacts 目录: {name!r}") from None
    return candidate


def atomic_write_json(path: Path, payload: dict) -> None:
    """原子写 JSON（tmp O_EXCL + os.replace）；符号链接/非普通文件目标拒绝。"""
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise UnsafePathError(f"拒绝写入非普通文件目标: {path}")
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}"
    fd: int | None = None
    for _ in range(2):  # 同 PID 残留 tmp 极罕见 → 清除后重试一次
        try:
            fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            tmp.unlink(missing_ok=True)
    if fd is None:  # pragma: no cover —— 两次 O_EXCL 冲突属异常环境
        raise UnsafePathError("临时文件冲突")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(data)
    os.replace(tmp, path)


def read_manifest(path: Path) -> Manifest | None:
    """读 manifest：缺失/损坏 → None；符号链接（含悬空）/非普通文件 → 拒绝。"""
    if path.is_symlink():  # 先于 exists()：悬空符号链接同样 fail-closed
        raise UnsafePathError(f"拒绝读取符号链接 manifest: {path}")
    if not path.exists():
        return None
    if not path.is_file():
        raise UnsafePathError(f"拒绝读取非普通文件 manifest: {path}")
    return Manifest.from_json(path.read_text(encoding="utf-8"))


def remove_file(path: Path) -> None:
    """幂等删除（stale 清理）；符号链接目标只删除链接本身，不跟进。"""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


class ControlLock:
    """start/stop 串行化锁（O_EXCL 原子创建；陈旧锁按 TTL 回收并报告）。"""

    def __init__(self, artifacts: Path) -> None:
        self.path = artifacts / LOCK_NAME
        self._held = False

    def acquire(self, notes: list[str]) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    age = 0.0
                if age > LOCK_STALE_SECONDS:
                    self.path.unlink(missing_ok=True)
                    notes.append(
                        f"陈旧控制锁已回收（age>{LOCK_STALE_SECONDS:.0f}s）: {self.path}"
                    )
                    continue
                notes.append(
                    f"另一 start/stop 操作进行中（锁 {self.path}，age={age:.0f}s）——拒绝并发"
                )
                return False
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    f"{datetime.now().astimezone().isoformat(timespec='seconds')}"
                    f" pid={os.getpid()}\n"
                )
            self._held = True
            return True

    def release(self) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False


# ---------- 归属核验（生产保护硬边界） ----------


def identity_matches(cmdline: str) -> bool:
    """身份标记全部命中（python3 + voice_health_sidecar.py）。"""
    return all(marker in cmdline for marker in IDENTITY_MARKERS)


def has_protected_marker(cmdline: str) -> bool:
    """cmdline 命中任一生产保护标记（引擎/relay/WSL 底座/docker/systemd）。"""
    lowered = cmdline.lower()
    return any(marker in lowered for marker in PROTECTED_MARKERS)


@dataclass
class EvalResult:
    """manifest 目标的归属判定（绝不据此之外发信号）。"""

    kind: str  # runner-error / dead / reused / protected / own
    cmdline: str | None = None
    error: RunnerError | None = None
    notes: list[str] = field(default_factory=list)


def evaluate_target(manifest: Manifest, runner: Runner, status_relpath: str) -> EvalResult:
    """探活 + 双重身份核验（保护标记 → 身份标记），输出机器可读判定。"""
    try:
        probe = runner.probe(str(manifest.pid), status_relpath)
    except RunnerError as cause:
        return EvalResult(kind="runner-error", error=cause)
    if probe.get("pid_alive") is not True:
        return EvalResult(kind="dead")
    cmdline = str(probe.get("cmdline") or "")
    if not cmdline:
        return EvalResult(kind="dead")
    if manifest.pid in PROTECTED_PIDS or has_protected_marker(cmdline):
        return EvalResult(kind="protected", cmdline=cmdline)
    if not identity_matches(cmdline):
        return EvalResult(kind="reused", cmdline=cmdline)
    return EvalResult(kind="own", cmdline=cmdline)


def report(out: IO[str], log_path: Path | None, message: str) -> None:
    """stdout 恒输出；log_path 非 None 时追加进编排日志（有界行）。"""
    out.write(f"{TAG} {message}\n")
    out.flush()
    if log_path is None:
        return
    line = (
        f"{datetime.now().astimezone().isoformat(timespec='seconds')} {message}"
    )[:500]
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
    except OSError:
        pass  # 日志失败不阻断控制流


def status_file_relpath(artifacts: Path, repo_root: Path) -> str:
    """sidecar --status-file 参数：仓库内 → repo-relative；仓库外 → 绝对 posix。

    先 resolve：相对形态的 --artifacts-dir / 大小写差异统一后再做归属判定，
    保证生产路径恒得 repo-relative（WSL 侧以仓库根 cwd 寻址的前提）。
    """
    resolved = artifacts.resolve()
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError:
        return resolved.as_posix()


def wait_for_status(runner: Runner, status_relpath: str, timeout: float) -> dict | None:
    """轮询 sidecar 落档的 status JSON（只重试「未落档」，不重试传输错误）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        probe = runner.probe("-", status_relpath)
        status = probe.get("status")
        if isinstance(status, dict) and status.get("pid"):
            return status
        time.sleep(POLL_INTERVAL_SECONDS)
    return None


def wait_dead(runner: Runner, pid: int, status_relpath: str, timeout: float) -> bool:
    """有界轮询 /proc 死亡确认（TERM 宽限 / KILL 确认共用）。"""
    deadline = time.monotonic() + timeout
    while True:
        try:
            probe = runner.probe(str(pid), status_relpath)
        except RunnerError:
            return False
        if probe.get("pid_alive") is not True:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL_SECONDS)


def term_spawned_sidecar(
    runner: Runner, pid: int, rel: str, out: IO[str], log_path: Path
) -> None:
    """wsl.exe 句柄回收后的 Linux 侧有界补回收（best-effort，不改退出码）。

    泄漏评估：Popen.kill() 只终止 Windows 侧 wsl.exe 中继，WSL 内
    --exec 启动的 python3 sidecar **不保证**随之终止（可能被 reparent
    存活，占住 18010/18011 与 status 文件）。唯一安全清理域：**本次
    启动落档的 PID** + 探活 + 无保护标记 + 身份标记全过 → 单次
    SIGTERM（零宽限等待、零 KILL 升级、零重试）。核验不可用（WSL
    管理面已失败）或身份不符时**放弃** Linux 侧回收并记录——盲发
    信号比泄漏更危险；落档 PID 可能指向无关进程。
    """
    if pid <= 0 or pid in PROTECTED_PIDS:
        report(
            out, log_path,
            f"note: 落档 PID {pid} 非法或属生产保护清单——不做 Linux 侧回收",
        )
        return
    try:
        probe = runner.probe(str(pid), rel)
    except RunnerError as cause:
        report(
            out, log_path,
            f"note: Linux 子进程无法核验（{cause}）——仅回收 wsl.exe 句柄",
        )
        return
    cmdline = str(probe.get("cmdline") or "")
    if (
        probe.get("pid_alive") is not True
        or has_protected_marker(cmdline)
        or not identity_matches(cmdline)
    ):
        report(
            out, log_path,
            "note: 落档 PID 未通过身份核验——不向其发信号（仅回收 wsl.exe 句柄）",
        )
        return
    try:
        runner.send_signal(pid, "SIGTERM")
    except RunnerError as cause:
        report(
            out, log_path,
            f"note: Linux sidecar SIGTERM 未送达（{cause}）——仅回收 wsl.exe 句柄",
        )
        return
    report(
        out, log_path,
        f"note: 已向本次启动的 Linux sidecar（PID {pid}）补发单次 SIGTERM",
    )


# ---------- 子命令 ----------


def cmd_start(
    artifacts: Path, runner: Runner, health: HealthProber, out: IO[str]
) -> int:
    log_path = safe_join(artifacts, LOG_NAME)
    status_path = safe_join(artifacts, STATUS_NAME)
    manifest_path = safe_join(artifacts, MANIFEST_NAME)
    rel = status_file_relpath(artifacts, REPO_ROOT)

    notes: list[str] = []
    lock = ControlLock(artifacts)
    acquired = lock.acquire(notes)
    for note in notes:
        report(out, log_path, note)
    if not acquired:
        return EXIT_REFUSED
    try:
        try:
            manifest = read_manifest(manifest_path)
        except UnsafePathError as cause:
            report(out, log_path, f"拒绝启动——manifest 目标不安全: {cause}")
            return EXIT_REFUSED
        if manifest_path.exists() and manifest is None:
            remove_file(manifest_path)
            report(out, log_path, "manifest 损坏——已清理（未据此发送任何信号）")
            manifest = None

        if manifest is not None:
            if manifest.pid in PROTECTED_PIDS:
                report(
                    out, log_path,
                    f"拒绝启动——manifest PID {manifest.pid} 属生产保护清单"
                    f"（FunASR 867 / CosyVoice 26008）",
                )
                return EXIT_REFUSED
            verdict = evaluate_target(manifest, runner, rel)
            if verdict.kind == "runner-error":
                report(
                    out, log_path,
                    f"拒绝启动——{verdict.error}",
                )
                report(
                    out, log_path,
                    "WSL 管理面不可用（单次尝试，不重试）；请稍后重试或人工核实",
                )
                return EXIT_REFUSED
            if verdict.kind == "protected":
                report(
                    out, log_path,
                    f"拒绝启动——manifest PID {manifest.pid} cmdline 含生产保护标记"
                    f"（疑似引擎/relay/WSL 底座进程）；不 spawn、manifest 保留",
                )
                return EXIT_REFUSED
            if verdict.kind == "reused":
                remove_file(manifest_path)
                remove_file(status_path)
                report(
                    out, log_path,
                    f"manifest PID {manifest.pid} 已被无关进程复用（身份不符）——"
                    f"manifest/status 已清理，未发送任何信号",
                )
            elif verdict.kind == "dead":
                remove_file(manifest_path)
                remove_file(status_path)
                report(
                    out, log_path,
                    f"stale manifest 已清理（PID {manifest.pid} 已退出）",
                )
            else:  # own：幂等（健康或启动中都不重复 spawn）
                codes = [health.probe(manifest.bind, port)[0] for port in EXPECTED_PORTS]
                if all(code == 200 for code in codes):
                    report(
                        out, log_path,
                        f"sidecar 已在运行（PID {manifest.pid}，bind {manifest.bind}，"
                        f"双端口 /health 200）——幂等跳过",
                    )
                else:
                    report(
                        out, log_path,
                        f"sidecar 已在运行（PID {manifest.pid}，/health 探测 "
                        f"{list(codes)}——启动中/上游降级）——幂等跳过（不重复 spawn）",
                    )
                return EXIT_OK

        # ---- 全新启动：固定 allowlist start argv，单次尝试 ----
        argv = build_start_argv(REPO_ROOT, rel)
        report(out, log_path, f"启动 sidecar（固定命令形态）: {' '.join(argv)}")
        try:
            popen = runner.spawn_sidecar(argv, log_path)
        except RunnerError as cause:
            report(
                out, log_path,
                f"启动失败——{cause}；不重试（{WSL_MANAGEMENT_UNAVAILABLE}）",
            )
            return EXIT_REFUSED
        try:
            spawned = wait_for_status(runner, rel, START_STATUS_TIMEOUT_SECONDS)
        except RunnerError as cause:
            runner.kill_spawned(popen)
            report(
                out, log_path,
                f"启动失败——status 等待中 WSL 管理面不可用——{cause}；"
                f"已终止本次启动的 wsl.exe 句柄（不触碰任何其他进程）",
            )
            report(
                out, log_path,
                "WSL 管理面不可用（单次尝试，不重试）；Linux 侧子进程无法核验"
                "——不盲发信号，留待下次 start/人工核实",
            )
            return EXIT_REFUSED
        if spawned is None:
            runner.kill_spawned(popen)
            report(
                out, log_path,
                f"启动失败——{START_STATUS_TIMEOUT_SECONDS:.0f}s 内未落 status 文件；"
                f"已终止本次启动的 wsl.exe 句柄（不触碰任何其他进程）",
            )
            report(out, log_path, f"排查日志: {rel}/{LOG_NAME}")
            return EXIT_ERROR
        try:
            pid = int(spawned["pid"])
            bind = str(spawned["bind"])
            ports = sorted(int(port) for port in spawned["ports"])
        except (KeyError, TypeError, ValueError):
            runner.kill_spawned(popen)
            report(out, log_path, f"启动失败——status 文件结构异常: {spawned!r}"[:400])
            return EXIT_ERROR
        if pid <= 0 or pid in PROTECTED_PIDS or tuple(ports) != EXPECTED_PORTS or not bind:
            runner.kill_spawned(popen)
            report(
                out, log_path,
                f"启动失败——status 事实异常（pid={pid}, bind={bind!r}, ports={ports}）；"
                f"已终止本次启动句柄",
            )
            term_spawned_sidecar(runner, pid, rel, out, log_path)
            return EXIT_ERROR
        try:
            probe = runner.probe(str(pid), rel)
        except RunnerError as cause:
            runner.kill_spawned(popen)
            report(
                out, log_path,
                f"启动失败——身份核验探测不可用——{cause}；已终止本次启动句柄",
            )
            return EXIT_REFUSED
        cmdline = str(probe.get("cmdline") or "")
        if (
            probe.get("pid_alive") is not True
            or has_protected_marker(cmdline)
            or not identity_matches(cmdline)
        ):
            runner.kill_spawned(popen)
            report(
                out, log_path,
                "启动失败——新进程身份核验未通过（cmdline 缺身份标记或命中保护标记）；"
                "已终止本次启动句柄",
            )
            return EXIT_ERROR

        manifest = Manifest(
            pid=pid,
            bind=bind,
            ports=ports,
            started_at=str(spawned.get("started_at", "")),
            log=f"{rel}/{LOG_NAME}" if not Path(rel).is_absolute() else str(log_path),
        )
        try:
            atomic_write_json(manifest_path, manifest.to_payload())
        except UnsafePathError as cause:
            runner.kill_spawned(popen)
            report(out, log_path, f"启动失败——manifest 写入被拒: {cause}")
            term_spawned_sidecar(runner, pid, rel, out, log_path)
            return EXIT_ERROR
        report(
            out, log_path,
            f"sidecar 已启动（PID {pid}，bind {bind}，端口 {ports}）",
        )
        report(out, log_path, f"      manifest: {rel}/{MANIFEST_NAME}")
        return EXIT_OK
    finally:
        lock.release()


def cmd_stop(
    artifacts: Path, runner: Runner, health: HealthProber, out: IO[str]
) -> int:
    log_path = safe_join(artifacts, LOG_NAME)
    status_path = safe_join(artifacts, STATUS_NAME)
    manifest_path = safe_join(artifacts, MANIFEST_NAME)
    rel = status_file_relpath(artifacts, REPO_ROOT)

    notes: list[str] = []
    lock = ControlLock(artifacts)
    acquired = lock.acquire(notes)
    for note in notes:
        report(out, log_path, note)
    if not acquired:
        return EXIT_REFUSED
    try:
        try:
            manifest = read_manifest(manifest_path)
        except UnsafePathError as cause:
            report(out, log_path, f"拒绝停止——manifest 目标不安全: {cause}")
            report(out, log_path, "不发信号、manifest 保留（请人工核实 artifacts 目录）")
            return EXIT_REFUSED
        if manifest is None:
            if manifest_path.exists():
                remove_file(manifest_path)
                report(out, log_path, "manifest 损坏——已清理（未据此发送任何信号）")
            remove_file(status_path)
            report(out, log_path, "无有效 manifest——无需停止（幂等；未发送任何信号）")
            return EXIT_OK
        if manifest.pid in PROTECTED_PIDS:
            report(
                out, log_path,
                f"拒绝停止——PID {manifest.pid} 属生产保护清单"
                f"（FunASR 867 / CosyVoice 26008，绝不成为终止目标）；"
                f"不发信号、manifest 保留",
            )
            return EXIT_REFUSED

        verdict = evaluate_target(manifest, runner, rel)
        if verdict.kind == "runner-error":
            report(out, log_path, f"拒绝停止——{verdict.error}")
            report(out, log_path, "WSL 管理面不可用（单次尝试，不重试）；manifest 保留")
            return EXIT_REFUSED
        if verdict.kind == "dead":
            remove_file(manifest_path)
            remove_file(status_path)
            report(
                out, log_path,
                f"stale manifest 已清理（PID {manifest.pid} 已退出，未发送任何信号）",
            )
            return EXIT_OK
        if verdict.kind == "protected":
            report(
                out, log_path,
                f"拒绝停止——PID {manifest.pid} cmdline 命中生产保护标记"
                f"（引擎/relay/WSL 底座/docker/systemd）；不发信号、manifest 保留",
            )
            return EXIT_REFUSED
        if verdict.kind == "reused":
            report(
                out, log_path,
                f"拒绝停止——PID {manifest.pid} 存活但 cmdline 身份不符"
                f"（疑似被无关进程复用）；不发信号、manifest 保留",
            )
            return EXIT_REFUSED

        pid = manifest.pid
        try:
            runner.send_signal(pid, "SIGTERM")
        except RunnerError as cause:
            report(
                out, log_path,
                f"停止失败——TERM 信号未确认送达——{cause}；manifest 保留以便重试",
            )
            return EXIT_REFUSED
        outcome = "terminated"
        if not wait_dead(runner, pid, rel, STOP_GRACE_SECONDS):
            try:
                runner.send_signal(pid, "SIGKILL")
            except RunnerError as cause:
                report(
                    out, log_path,
                    f"停止失败——KILL 信号未确认送达——{cause}；manifest 保留以便重试",
                )
                return EXIT_REFUSED
            outcome = "killed"
            if not wait_dead(runner, pid, rel, KILL_CONFIRM_SECONDS):
                report(
                    out, log_path,
                    f"停止失败——PID {pid} 经 TERM+KILL 仍存活；manifest 保留以便重试",
                )
                return EXIT_ERROR
        remove_file(manifest_path)
        remove_file(status_path)
        verb = "已优雅退出（TERM）" if outcome == "terminated" else "宽限超时，已单次 KILL 兜底"
        report(out, log_path, f"sidecar 已停止（PID {pid}，{verb}）；manifest 已清理")
        return EXIT_OK
    finally:
        lock.release()


def cmd_status(
    artifacts: Path, runner: Runner, health: HealthProber, out: IO[str]
) -> int:
    # 只读命令：零写零杀零清理（日志也不追加）
    manifest_path = artifacts / MANIFEST_NAME
    status_path = artifacts / STATUS_NAME
    log_path = artifacts / LOG_NAME
    rel = status_file_relpath(artifacts, REPO_ROOT)
    report(out, None, f"artifacts: {artifacts}")
    if not manifest_path.exists():
        report(out, None, "state: stopped（无 manifest）")
        if status_path.exists():
            report(
                out, None,
                "note: 残留 sidecar-status.json 存在——status 只读不清理"
                f"（start/stop 会处理）；日志: {log_path}",
            )
        return EXIT_OK
    try:
        manifest = read_manifest(manifest_path)
    except UnsafePathError as cause:
        report(out, None, f"state: manifest-unsafe（拒绝读取: {cause}）")
        report(out, None, "start/stop 将拒绝操作该 manifest（fail-closed）")
        return EXIT_OK
    if manifest is None:
        report(out, None, "state: manifest-损坏（无法解析；start/stop 会清理）")
        return EXIT_OK
    report(out, None, f"pid: {manifest.pid}（manifest 记录）")
    report(out, None, f"bind: {manifest.bind}")
    report(out, None, f"ports: {manifest.ports}")
    report(out, None, f"started_at: {manifest.started_at or '(未记录)'}")
    report(out, None, f"log: {manifest.log or log_path}")
    if manifest.pid in PROTECTED_PIDS:
        report(
            out, None,
            f"state: protected-target（PID {manifest.pid} 属生产保护清单——"
            f"start/stop 一律拒绝）",
        )
        return EXIT_OK
    verdict = evaluate_target(manifest, runner, rel)
    if verdict.kind == "runner-error":
        report(
            out, None,
            f"state: unknown（{verdict.error}——无法核实，不臆断）",
        )
        return EXIT_OK
    if verdict.kind == "dead":
        report(out, None, f"state: stale（PID {manifest.pid} 已退出；start/stop 将清理）")
        return EXIT_OK
    if verdict.kind == "protected":
        report(
            out, None,
            "state: protected-target（cmdline 命中生产保护标记——"
            "start/stop 一律拒绝）",
        )
        return EXIT_OK
    if verdict.kind == "reused":
        report(
            out, None,
            "state: pid-reused（cmdline 身份不符——stop 拒绝发信号、start 清理重开）",
        )
        return EXIT_OK
    codes = [health.probe(manifest.bind, port)[0] for port in EXPECTED_PORTS]
    if all(code == 200 for code in codes):
        report(out, None, "state: running-healthy（双端口 /health 200）")
    else:
        report(out, None, f"state: running-degraded（/health 探测 {list(codes)}）")
    return EXIT_OK


COMMANDS = {"start": cmd_start, "status": cmd_status, "stop": cmd_stop}


def artifacts_usable(artifacts: Path) -> bool:
    """写路径命令（start/stop）的 --artifacts-dir 仓库内约束（豁免开关仅测试）。"""
    if os.environ.get(ALLOW_OUTSIDE_ARTIFACTS_ENV, "") == "1":
        return True
    try:
        artifacts.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice_health_sidecar_control.py",
        description=(
            "M14-26 WSL 语音健康 sidecar 编排：start/status/stop"
            "（固定 wsl.exe 命令形态；生产 PID/进程硬保护）"
        ),
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=None,
        help=(
            "覆盖 artifacts 目录"
            f"（默认 <repo>/{ARTIFACTS_RELPATH}；写路径命令要求仓库内）"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "status", "stop"):
        subparsers.add_parser(name, help=f"{name} sidecar")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    runner: Runner | None = None,
    health: HealthProber | None = None,
    stdout: IO[str] | None = None,
    artifacts_dir: Path | None = None,
) -> int:
    out = stdout if stdout is not None else sys.stdout
    if out is sys.stdout and sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):  # pragma: no cover —— 非 Windows 控制台
            pass
    args = build_parser().parse_args(argv)
    artifacts = (
        artifacts_dir
        if artifacts_dir is not None
        else (args.artifacts_dir if args.artifacts_dir is not None else artifacts_default_dir(REPO_ROOT))
    )
    if args.command in MUTATING_COMMANDS and not artifacts_usable(artifacts):
        report(
            out, None,
            f"拒绝执行——--artifacts-dir 须位于仓库内（{REPO_ROOT}）："
            f"sidecar 以仓库根 cwd 的 repo-relative 路径寻址，仓库外目录"
            f"在 WSL 侧不可达",
        )
        report(
            out, None,
            f"      status 只读不受限；测试/诊断需显式设"
            f" {ALLOW_OUTSIDE_ARTIFACTS_ENV}=1（仅测试用，生产勿设）",
        )
        return 2
    handler = COMMANDS[args.command]
    real_runner = runner if runner is not None else Runner(REPO_ROOT)
    real_health = health if health is not None else HealthProber()
    try:
        return handler(artifacts, real_runner, real_health, out)
    except UnsafePathError as cause:
        report(out, None, f"拒绝——artifacts 目标不安全: {cause}")
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
