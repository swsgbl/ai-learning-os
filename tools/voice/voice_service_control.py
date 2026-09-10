#!/usr/bin/env python
"""M14-04 本地语音服务受控生命周期管理：status / start / stop / restart。

设计（定版——「复杂度留给系统，简单留给用户」）：
- 单文件、纯标准库、零第三方依赖。引擎进程全部跑在 WSL 内；本工具在
  Windows 侧经 ``wsl.exe --cd <仓库> bash -c`` 编排（在 WSL/Linux 内运行时
  直接用本地 bash——同一套命令，零路径转换）。进程探测一律只读：
  /proc/<pid> 存在性、cmdline、cwd、``ss -tln``、TCP/HTTP 探测。
- manifest 即事实源（gitignored artifacts）：start 生成 launcher——
  ``echo $$ > spawn.pid`` 后 ``exec bash bootstrap_*.sh``（bash 的 exec 链
  保持同一 PID：launcher → bootstrap（cosyvoice 含快照 re-exec）→ 服务进程，
  故 spawn.pid 恒等于最终服务进程 PID），随后把 PID、端口、启动时间、
  cmdline 匹配标记、WSL 工作区（launcher 内 ``pwd -P`` 落档）原子写进
  ``artifacts/voice/<engine>/service/manifest.json``；日志在同级 service.log。
- start 幂等（manifest/PID/端口三重事实校验后才 spawn）：
  * manifest PID 活且 cmdline 匹配 → 不重复启动（健康 200 = already running；
    端口未起/503 = 正在启动，bootstrap 阶段可达数分钟）；
  * manifest PID 已死/被无关进程复用 → stale，清理并明确报告后放行；
  * 端口已有不受管监听 → 拒绝启动（不 spawn 注定绑不上端口的实例），
    报告属主 PID（可得时）；并发 start/stop 用 O_EXCL lock 文件串行化。
- stop 只停「manifest 归属 且 cmdline 仍含启动标记 且 工作区仍一致」的目标：
  kill -TERM → 宽限轮询（/proc 消失即止）→ kill -KILL 兜底；PID 被无关进程
  复用 / cmdline 不匹配 / cwd 指向别的工作区（疑似另一检出实例）→ 一律拒绝
  并明确报告。绝不 pkill / killall / fuser / 按端口杀 / 碰无 manifest 的进程。
- restart = stop + start 受控序列；模型缓存由 bootstrap 的载荷文件判定复用
  （M14-02 实证重启不重新下载 8.5GB 模型；wetext 离线缓存见 M14-03）。
- 端口/路径可配置：``--port`` 或 FUNASR_PORT / COSYVOICE_PORT（默认
  8010/8011）；所有路径自脚本位置 repo-relative 派生，无机器特定绝对路径。

可测性：平台操作全部经 ServiceOps 注入——真实实现 BashOps 只做只读探测与
显式 spawn/terminate；测试注入 FakeOps（services/api/tests/
test_voice_service_control.py）以 fake 进程/fake health 验证全部生命周期分支，
不启动真实引擎、不占用 8010/8011。

退出码：0 成功/幂等无操作；1 操作失败；2 参数错误；3 安全拒绝（拒绝误杀/
端口竞争/无法核实归属）。status 恒只读（不写不杀），退出码不反映健康度。

用法（仓库根；Windows 侧 python 或 WSL 内 python3 均可）：
  python tools/voice/voice_service_control.py status
  python tools/voice/voice_service_control.py start --engine cosyvoice
  python tools/voice/voice_service_control.py stop --engine funasr
  python tools/voice/voice_service_control.py restart
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from collections.abc import Sequence
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
TAG = "[voice-ctl]"

#: lock 文件视为陈旧的阈值（秒）——start/stop 串行化；进程崩溃留下的锁在
#: 阈值后可被下一次操作安全回收（回收动作明确报告）
LOCK_STALE_SECONDS = 600.0
#: spawn 后等待 launcher 落 spawn.pid 的时限（秒；含 wsl.exe 启动开销）
SPAWN_PID_TIMEOUT_SECONDS = 20.0
#: stop 的 TERM 宽限（秒）——宽限内 /proc 消失即视为优雅退出，超时才 KILL
TERMINATE_GRACE_SECONDS = 15
#: HTTP 健康探测超时（秒）
HEALTH_TIMEOUT_SECONDS = 5.0
#: TCP 端口探测超时（秒）
TCP_TIMEOUT_SECONDS = 2.0
MANIFEST_NAME = "manifest.json"
LAUNCHER_NAME = "launcher.sh"
LOG_NAME = "service.log"
SPAWN_PID_NAME = "spawn.pid"
WORKDIR_NAME = "workdir.txt"
LOCK_NAME = "control.lock"


@dataclass(frozen=True)
class EngineSpec:
    """引擎定义：端口环境变量、bootstrap 脚本（repo-relative）、cmdline 标记。

    cmd_markers 覆盖 exec 链各阶段：bootstrap bash 阶段（脚本名）、cosyvoice
    快照 re-exec 阶段（快照文件名）、最终服务进程（funasr-server 二进制名 /
    cosyvoice_openai_bridge.py 脚本名）。stop 的归属核验以 manifest 记录的
    标记为准（manifest 记录的是启动当时的事实，spec 演进不影响旧实例判定）。
    """

    name: str
    port_env: str
    default_port: int
    bootstrap_script: str
    cmd_markers: tuple[str, ...]
    artifacts_subdir: str
    model_fallback: str


ENGINE_SPECS: tuple[EngineSpec, ...] = (
    EngineSpec(
        name="funasr",
        port_env="FUNASR_PORT",
        default_port=8010,
        bootstrap_script="tools/voice/bootstrap_funasr_wsl.sh",
        cmd_markers=("bootstrap_funasr_wsl.sh", "funasr-server"),
        artifacts_subdir="funasr",
        model_fallback="sensevoice",
    ),
    EngineSpec(
        name="cosyvoice",
        port_env="COSYVOICE_PORT",
        default_port=8011,
        bootstrap_script="tools/voice/bootstrap_cosyvoice_wsl.sh",
        # 快照 re-exec：bootstrap 启动即 exec artifacts 内快照副本（M14-02），
        # cmdline 会出现快照文件名——必须一并纳入标记
        cmd_markers=(
            "bootstrap_cosyvoice_wsl.sh",
            "bootstrap_cosyvoice_wsl.snapshot.sh",
            "cosyvoice_openai_bridge.py",
        ),
        artifacts_subdir="cosyvoice",
        model_fallback="Fun-CosyVoice3-0.5B-2512",
    ),
)


@dataclass
class Manifest:
    """一次受控 start 的事实记录（artifacts 内，gitignored）。"""

    engine: str
    pid: int
    port: int
    started_at: str
    bootstrap: str
    cmd_markers: list[str]
    workdir: str | None
    log: str

    def to_json(self) -> str:
        payload = {
            "schema_version": 1,
            "engine": self.engine,
            "pid": self.pid,
            "port": self.port,
            "started_at": self.started_at,
            "bootstrap": self.bootstrap,
            "cmd_markers": list(self.cmd_markers),
            "workdir": self.workdir,
            "log": self.log,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Manifest | None:
        """解析 manifest；结构不合法返回 None（调用方按 corrupt 清理并报告）。"""
        try:
            payload = json.loads(text)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        try:
            manifest = cls(
                engine=str(payload["engine"]),
                pid=int(payload["pid"]),
                port=int(payload["port"]),
                started_at=str(payload.get("started_at", "")),
                bootstrap=str(payload.get("bootstrap", "")),
                cmd_markers=[str(m) for m in payload.get("cmd_markers", [])],
                workdir=(str(payload["workdir"]) if payload.get("workdir") else None),
                log=str(payload.get("log", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if manifest.pid <= 0 or manifest.port <= 0:
            return None
        return manifest


class OpsError(RuntimeError):
    """平台操作失败（transport 不可用等）——由调用方转为可见失败。"""


class ServiceOps:
    """平台操作抽象：真实实现 BashOps；测试注入 FakeOps（见测试文件）。

    tcp_listening / http_health 为纯 Python 回环探测（与主 API / 冒烟脚本同一
    可达性域：Windows 侧依赖 WSL2 localhost 转发——本机 M14-02 已实证）；其余
    为进程面操作，真实实现经 bash（Windows 侧自动经 wsl.exe --cd 仓库根）。
    """

    def tcp_listening(self, port: int, host: str = "127.0.0.1") -> bool:
        try:
            with socket.create_connection((host, port), timeout=TCP_TIMEOUT_SECONDS):
                return True
        except OSError:
            return False

    def http_health(self, port: int, host: str = "127.0.0.1") -> tuple[int | None, str]:
        """GET /health → (状态码, body 片段)；不可达 → (None, 错误类别名)。"""
        url = f"http://{host}:{port}/health"
        try:
            with urllib_request.urlopen(url, timeout=HEALTH_TIMEOUT_SECONDS) as response:
                return int(response.status), response.read(4096).decode("utf-8", "replace")
        except urllib_error.HTTPError as cause:
            body = cause.read(4096).decode("utf-8", "replace") if cause.fp else ""
            return int(cause.code), body
        except OSError as cause:
            return None, type(cause).__name__

    # ---- 进程面操作（真实实现见 BashOps；FakeOps 覆盖） ----

    def pid_alive(self, pid: int) -> bool | None:
        raise NotImplementedError

    def pid_cmdline(self, pid: int) -> str | None:
        raise NotImplementedError

    def pid_cwd(self, pid: int) -> str | None:
        raise NotImplementedError

    def shell_port_state(self, port: int) -> tuple[bool, list[int]] | None:
        """引擎上下文（WSL）内 ``ss -tln`` 探测端口：(是否监听, 属主 PID 列表)。

        返回 None = ss 不可用（按未知处理，不影响分类——回环 TCP 探测兜底）。
        """
        raise NotImplementedError

    def spawn_detached(self, launcher: str, log: str) -> None:
        """setsid nohup 后台启动 launcher（脱离 wsl.exe 会话存活，M14-02 实证）。"""
        raise NotImplementedError

    def terminate(self, pid: int, grace_seconds: int) -> str:
        """受控终止：TERM → 宽限轮询 → KILL。返回 terminated/killed/already-dead/failed。"""
        raise NotImplementedError


class BashOps(ServiceOps):
    """经 bash 执行的真实平台操作。

    Windows 侧：``wsl.exe --cd <仓库根> bash -c <cmd>``（--cd 免路径转换，
    仓库路径含空格安全——argv 直传不经 Windows shell）；WSL/Linux 侧：本地
    ``bash -c`` + cwd=仓库根。所有内插路径经 shlex.quote。

    命令纪律（契约测试锁定）：命令串**零 bash 变量**——wsl.exe --cd 形态会
    剥掉 bash 变量展开（2026-09-10 实证：变量成空串 → /proc/ 判定恒真、
    kill 无参），故 PID（int 校验）/ 路径（shlex.quote）/ 轮询计数全部
    Python 侧内插；launcher 文件内的 ``$$`` 不经 wsl.exe 参数面（WSL 内
    bash 从文件读取执行），不受影响。
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._on_windows = os.name == "nt"

    def _bash(self, command: str, *, timeout: float = 60.0) -> tuple[int, str, str]:
        if self._on_windows:
            argv = ["wsl.exe", "--cd", str(self.repo_root), "bash", "-c", command]
            cwd = None
        else:
            argv = ["bash", "-c", command]
            cwd = self.repo_root
        try:
            # argv 恒为本模块构造的只读探测 / 受控终止命令（pid 经 int 校验、
            # 路径经 shlex.quote，无外部输入拼接 shell）
            result = subprocess.run(
                argv,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=cwd,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as cause:
            raise OpsError(f"bash transport 不可用: {type(cause).__name__}") from cause
        return result.returncode, result.stdout or "", result.stderr or ""

    def pid_alive(self, pid: int) -> bool | None:
        code, out, _ = self._bash(f"[ -d {shlex.quote(f'/proc/{pid}')} ] && echo alive || echo dead")
        if code != 0:
            return None
        return out.strip() == "alive"

    def pid_cmdline(self, pid: int) -> str | None:
        cmdline = f"tr '\\0' ' ' < {shlex.quote(f'/proc/{pid}/cmdline')}"
        code, out, _ = self._bash(cmdline)
        if code != 0:
            return None
        text = out.strip()
        return text or None

    def pid_cwd(self, pid: int) -> str | None:
        cmdline = f"readlink {shlex.quote(f'/proc/{pid}/cwd')} || printf __READLINK_FAIL__"
        code, out, _ = self._bash(cmdline)
        if code != 0:
            return None
        text = out.strip()
        return None if (not text or text == "__READLINK_FAIL__") else text

    def shell_port_state(self, port: int) -> tuple[bool, list[int]] | None:
        code, out, _ = self._bash("ss -tlnp", timeout=30.0)
        if code != 0 or not out.strip():
            return None  # ss 缺失/不可用——未知，不臆断
        listening = False
        owners: set[int] = set()
        for line in out.splitlines()[1:]:  # 首行表头
            fields = line.split()
            if len(fields) < 4:
                continue
            if fields[3].endswith(f":{port}"):  # 冒号锚定，避免 8010 命中 58010
                listening = True
                owners.update(int(m) for m in re.findall(r"pid=(\d+)", line))
        return listening, sorted(owners)

    def spawn_detached(self, launcher: str, log: str) -> None:
        # setsid 必需：wsl.exe 会话退出会连带终止同会话后台进程（M14-02 实证）；
        # 末尾 sleep 1 同样必需：bash -c "cmd &" 会立即退出并拆除 wsl.exe 会话，
        # setsid 尚未完成脱离的后台子进程会被一并杀死（2026-09-10 本机实证：
        # 裸 & 的 spawn.pid/日志均未产生；+sleep 1 后存活——与 compose_voice_
        # reachability.sh 的 probe 同款 settle 模式）。追加重定向保留跨
        # restart 的日志历史（失败排查证据）。
        command = (
            f"setsid nohup bash {shlex.quote(launcher)} >> {shlex.quote(log)} 2>&1 < /dev/null &"
            " sleep 1"
        )
        code, _, err = self._bash(command, timeout=30.0)
        if code != 0:
            raise OpsError(f"spawn 失败: {err.strip()[:200]}")

    def terminate(self, pid: int, grace_seconds: int) -> str:
        # 全程无 bash 变量（契约测试锁定）：wsl.exe --cd 形态会剥掉命令里的
        # bash 变量展开（2026-09-10 实证：变量成空串 → /proc/ 判定恒真、
        # kill 无参）；PID 为 int 校验值、路径经 shlex.quote，Python 侧内插
        # 即安全。
        proc = shlex.quote(f"/proc/{pid}")
        ticks = " ".join(str(n) for n in range(1, max(1, int(grace_seconds)) + 1))
        command = (
            f"[ -d {proc} ] || " + "{ echo already-dead; exit 0; }; "
            f"kill -TERM {pid} 2>/dev/null || true; "
            f"for n in {ticks}; do [ -d {proc} ] || " + "{ echo terminated; exit 0; }; sleep 1; done; "
            f"kill -KILL {pid} 2>/dev/null || true; sleep 1; "
            f"if [ -d {proc} ]; then echo failed; else echo killed; fi"
        )
        code, out, err = self._bash(command, timeout=float(grace_seconds) + 45.0)
        lines = [line for line in out.splitlines() if line.strip()]
        if code == 0 and lines and lines[-1] in {"terminated", "killed", "already-dead", "failed"}:
            return lines[-1]
        raise OpsError(f"terminate 失败: {(err or out).strip()[:200]}")


@dataclass
class Inspection:
    """一次引擎体检的结果（含体检中完成的 stale manifest 清理动作）。"""

    spec: EngineSpec
    port: int
    manifest: Manifest | None = None
    #: None=无 manifest；"ok"=归属成立；"foreign"=cmdline 匹配但工作区不符；
    #: "unknown"=无法核实（/proc 探测不可用）
    ownership: str | None = None
    listening: bool = False
    owners: list[int] | None = None
    health_code: int | None = None
    health_body: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        if self.manifest is None:
            return "unmanaged-running" if self.listening else "stopped"
        if self.ownership == "ok":
            return "managed-running" if self.listening else "managed-starting"
        if self.ownership == "foreign":
            return "managed-mismatch"
        return "unknown"


def say(out: IO[str], message: str) -> None:
    out.write(f"{TAG} {message}\n")
    out.flush()


def default_service_dir(spec: EngineSpec) -> Path:
    """默认服务目录（gitignored artifacts 内，repo-relative 派生）。"""
    return REPO_ROOT / "artifacts" / "voice" / spec.artifacts_subdir / "service"


def resolve_port(spec: EngineSpec, override: int | None) -> int:
    if override is not None:
        return override
    raw = os.environ.get(spec.port_env, "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass  # 非法环境变量按未设置处理（不 crash，status 仍可读）
    return spec.default_port


def load_manifest(service_dir: Path, spec: EngineSpec, notes: list[str]) -> Manifest | None:
    """读取并校验 manifest；缺失/损坏/引擎不符 → 清理文件并报告（None）。"""
    path = service_dir / MANIFEST_NAME
    if not path.is_file():
        return None
    manifest = Manifest.from_json(path.read_text(encoding="utf-8"))
    if manifest is not None and manifest.engine == spec.name:
        return manifest
    notes.append(
        f"manifest 损坏或引擎不符（{path}）——已清理；未据此发送任何信号"
    )
    path.unlink(missing_ok=True)
    return None


def marker_match(cmdline: str, markers: Sequence[str]) -> bool:
    return any(marker in cmdline for marker in markers)


def inspect_engine(
    spec: EngineSpec, port: int, service_dir: Path, ops: ServiceOps
) -> Inspection:
    """只读体检（stale manifest 清理是唯一写动作，且明确记录在 notes）。"""
    result = Inspection(spec=spec, port=port)
    manifest = load_manifest(service_dir, spec, result.notes)
    if manifest is not None:
        alive = ops.pid_alive(manifest.pid)
        if alive is False:
            manifest_path = service_dir / MANIFEST_NAME
            manifest_path.unlink(missing_ok=True)
            result.notes.append(
                f"stale manifest 已清理（PID {manifest.pid} 已退出）: {manifest_path}"
            )
            manifest = None
        elif alive is None:
            result.notes.append(
                f"PID {manifest.pid} 存活状态不可探测（/proc 不可用？）——按记录呈现，未核实"
            )
            result.ownership = "unknown"  # 无法核实 → start/stop 必须拒绝（防双实例/误杀）
        else:
            cmdline = ops.pid_cmdline(manifest.pid)
            if cmdline is None:
                result.notes.append(
                    f"PID {manifest.pid} cmdline 不可读——归属无法核实，未发送任何信号"
                )
                result.ownership = "unknown"
            elif not marker_match(cmdline, manifest.cmd_markers):
                manifest_path = service_dir / MANIFEST_NAME
                manifest_path.unlink(missing_ok=True)
                result.notes.append(
                    f"manifest PID {manifest.pid} 已被无关进程复用"
                    f"（cmdline 不含启动标记 {list(manifest.cmd_markers)}）——"
                    f"manifest 判 stale 并清理；未发送任何信号"
                )
                manifest = None
            else:
                cwd = ops.pid_cwd(manifest.pid)
                if cwd and manifest.workdir and cwd != manifest.workdir:
                    result.ownership = "foreign"
                    result.notes.append(
                        f"PID {manifest.pid} cmdline 匹配但 cwd={cwd} 与记录工作区 "
                        f"{manifest.workdir} 不符——疑似另一检出的实例，本工具拒绝认定归属"
                    )
                else:
                    result.ownership = "ok"
    result.manifest = manifest

    # 端口事实：回环 TCP 探测（与主 API 同域）优先；不通时用引擎上下文内
    # ss 复核（WSL localhost 转发被关时仍能如实区分 stopped/running）
    if ops.tcp_listening(port):
        result.listening = True
        shell_state = ops.shell_port_state(port)
        result.owners = shell_state[1] if shell_state is not None else None
    else:
        shell_state = ops.shell_port_state(port)
        if shell_state is None:
            result.listening = False
            result.owners = None
        else:
            result.listening, result.owners = shell_state
    if result.listening:
        result.health_code, result.health_body = ops.http_health(port)
    return result


def describe_health(result: Inspection) -> str:
    spec = result.spec
    if result.health_code is None:
        return "不可达"
    model = ""
    try:
        payload = json.loads(result.health_body)
        if isinstance(payload, dict):
            # CosyVoice bridge: {"model": "..."}；funasr-server: {"models_loaded": [...]}
            if payload.get("model"):
                model = f" model={payload['model']}"
            elif payload.get("models_loaded"):
                loaded = [str(m) for m in payload["models_loaded"]]
                model = f" model={'+'.join(loaded)}"
    except ValueError:
        pass
    if not model and result.health_code == 200:
        model = f" model={spec.model_fallback}(未在 /health 报告，回退标注)"
    snippet = result.health_body.strip().replace("\n", " ")[:120]
    detail = f" body={snippet}" if snippet else ""
    return f"HTTP {result.health_code}{model}{detail}"


class ControlLock:
    """start/stop 串行化锁（O_EXCL 原子创建；陈旧锁按 TTL 回收并报告）。

    同一 service 目录内的并发操作互斥；锁内容记录持有者 PID 与时间，崩溃
    残留的锁在 LOCK_STALE_SECONDS 后可被安全回收。
    """

    def __init__(self, service_dir: Path) -> None:
        self.path = service_dir / LOCK_NAME
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
                    notes.append(f"陈旧控制锁已回收（age>{LOCK_STALE_SECONDS:.0f}s）: {self.path}")
                    continue
                notes.append(
                    f"另一 start/stop 操作进行中（锁 {self.path}，age={age:.0f}s）——拒绝并发"
                )
                return False
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} pid={os.getpid()}\n")
            self._held = True
            return True

    def release(self) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False


def _repo_relative(service_dir: Path) -> str:
    """bash 侧路径：仓库内 → repo-relative（posix）；仓库外 → 绝对 posix。"""
    try:
        return service_dir.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return service_dir.as_posix()


def write_launcher(spec: EngineSpec, port: int, service_dir: Path) -> str:
    """生成 launcher（bash；cwd=仓库根）：落 PID → 落工作区 → exec bootstrap。

    ``echo $$`` 在 exec 之前落档，bash exec 链（launcher → bootstrap → 服务）
    保持同一 PID，故 spawn.pid 恒为最终服务进程 PID——stop 据此精确回收。
    """
    launcher = service_dir / LAUNCHER_NAME
    lines = [
        "#!/usr/bin/env bash",
        "# 由 tools/voice/voice_service_control.py 生成（M14-04）——可安全删除，",
        "# 下次 start 会按当前端口重新生成。cwd 恒为仓库根（相对路径由此成立）。",
        "set -euo pipefail",
        f'export {spec.port_env}="{port}"',
        f"printf '%s\\n' \"$$\" > {shlex.quote(_repo_relative(service_dir / SPAWN_PID_NAME))}",
        f"pwd -P > {shlex.quote(_repo_relative(service_dir / WORKDIR_NAME))}",
        f"exec bash {shlex.quote(spec.bootstrap_script)}",
    ]
    launcher.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" 必需：Windows 默认把 \n 翻译成 \r\n，WSL bash 会把
    # 「set -euo pipefail\r」当非法选项（2026-09-10 真实 spawn 实证）
    launcher.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return _repo_relative(launcher)


def wait_spawn_pid(service_dir: Path) -> int | None:
    """轮询读取 launcher 落档的 spawn.pid（部分写容错：解析失败重试）。"""
    path = service_dir / SPAWN_PID_NAME
    deadline = time.monotonic() + SPAWN_PID_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return int(text)
        except (OSError, ValueError):
            pass  # 未落档/写入中——继续轮询
        time.sleep(0.5)
    return None


def tail_log(service_dir: Path, lines: int = 15) -> str:
    """读取 service.log 尾部（启动失败排查；文件缺失返回提示）。"""
    path = service_dir / LOG_NAME
    if not path.is_file():
        return "(日志尚未生成)"
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(日志不可读)"
    return "\n".join(content[-lines:])


def write_manifest(manifest: Manifest, service_dir: Path) -> None:
    """原子写 manifest（tmp + rename；写入方崩溃不留半文件）。"""
    service_dir.mkdir(parents=True, exist_ok=True)
    path = service_dir / MANIFEST_NAME
    tmp = service_dir / f"{MANIFEST_NAME}.tmp"
    tmp.write_text(manifest.to_json(), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def remove_manifest(service_dir: Path) -> None:
    (service_dir / MANIFEST_NAME).unlink(missing_ok=True)


def print_status(result: Inspection, service_dir: Path, out: IO[str]) -> None:
    spec = result.spec
    say(out, f"=== {spec.name} (port {result.port}) ===")
    for note in result.notes:
        say(out, f"note: {note}")
    say(out, f"state: {result.state}")
    if result.manifest is not None:
        say(out, f"pid: {result.manifest.pid}（manifest 记录；归属核验={result.ownership}）")
        say(out, f"started_at: {result.manifest.started_at or '(未记录)'}")
        if result.manifest.workdir:
            say(out, f"workdir: {result.manifest.workdir}")
        say(out, f"manifest: {_repo_relative(service_dir / MANIFEST_NAME)}")
        say(out, f"log: {result.manifest.log or _repo_relative(service_dir / LOG_NAME)}")
    if result.listening:
        owners = result.owners if result.owners is not None else "未知（ss 不可用）"
        say(out, f"port: listening（属主 PID: {owners}）")
        say(out, f"health: {describe_health(result)}")
        if result.manifest is None:
            say(out, "提示: 端口有监听但无有效 manifest——该进程不受本工具管辖，")
            say(out, "       stop/restart 会拒绝碰它（拒绝误杀边界）；如需接管请先手动处理")
    else:
        say(out, "port: no listener")
    if result.state == "managed-starting":
        say(out, "提示: manifest 进程存活但端口未监听——bootstrap 阶段（依赖安装/模型加载），")
        say(out, f"       日志: {_repo_relative(service_dir / LOG_NAME)}")


def cmd_status(spec: EngineSpec, port: int, service_dir: Path, ops: ServiceOps, out: IO[str]) -> int:
    result = inspect_engine(spec, port, service_dir, ops)
    print_status(result, service_dir, out)
    return EXIT_OK


def cmd_start(spec: EngineSpec, port: int, service_dir: Path, ops: ServiceOps, out: IO[str]) -> int:
    notes: list[str] = []
    lock = ControlLock(service_dir)
    acquired = lock.acquire(notes)
    for note in notes:  # 锁获取结果（含陈旧锁回收）恒可见
        say(out, note)
    if not acquired:
        return EXIT_REFUSED
    try:
        result = inspect_engine(spec, port, service_dir, ops)
        for note in result.notes:
            say(out, note)
        if result.ownership == "ok":
            health = describe_health(result)
            say(out, f"{spec.name}: 已在运行（PID {result.manifest.pid}，{result.state}，{health}）——幂等跳过")
            return EXIT_OK
        if result.ownership == "unknown":
            say(out, f"{spec.name}: manifest 进程归属无法核实——拒绝启动第二实例（避免双实例/端口竞争）")
            return EXIT_REFUSED
        if result.ownership == "foreign":
            say(out, f"{spec.name}: manifest PID cmdline 匹配但工作区不符（疑似另一检出实例）——拒绝启动")
            return EXIT_REFUSED
        if result.listening:
            owners = result.owners if result.owners else "未知（ss 不可用）"
            say(out, f"{spec.name}: 拒绝启动——端口 {port} 已被不受管进程占用（属主 PID: {owners}）")
            say(out, "      spawn 注定绑不上端口；请先处理该进程或换 --port/端口环境变量")
            return EXIT_REFUSED

        log_rel = _repo_relative(service_dir / LOG_NAME)
        launcher_rel = write_launcher(spec, port, service_dir)
        # 先清旧 spawn.pid：launcher 是唯一写者，但上一轮残留会让 wait_spawn_pid
        # 在新进程落档前读到陈旧 PID（restart 场景实测竞态窗口）
        (service_dir / SPAWN_PID_NAME).unlink(missing_ok=True)
        (service_dir / WORKDIR_NAME).unlink(missing_ok=True)
        try:
            ops.spawn_detached(launcher_rel, log_rel)
        except OpsError as cause:
            say(out, f"{spec.name}: 启动失败——{cause}")
            return EXIT_ERROR
        pid = wait_spawn_pid(service_dir)
        if pid is None:
            say(out, f"{spec.name}: 启动失败——{SPAWN_PID_TIMEOUT_SECONDS:.0f}s 内未落 spawn.pid")
            say(out, f"日志尾部:\n{tail_log(service_dir)}")
            return EXIT_ERROR
        alive = ops.pid_alive(pid)
        if alive is False:
            say(out, f"{spec.name}: 启动失败——进程 {pid} 立即退出；日志尾部:")
            say(out, tail_log(service_dir))
            return EXIT_ERROR
        workdir = None
        try:
            workdir = (service_dir / WORKDIR_NAME).read_text(encoding="utf-8").strip() or None
        except OSError:
            pass
        manifest = Manifest(
            engine=spec.name,
            pid=pid,
            port=port,
            started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            bootstrap=spec.bootstrap_script,
            cmd_markers=list(spec.cmd_markers),
            workdir=workdir,
            log=log_rel,
        )
        write_manifest(manifest, service_dir)
        say(out, f"{spec.name}: 已启动（PID {pid}，端口 {port}）")
        say(out, f"      日志: {log_rel}；manifest: {_repo_relative(service_dir / MANIFEST_NAME)}")
        say(out, "      /health 200 前属启动期（首次含依赖安装/模型下载；restart 复用缓存不重下）")
        return EXIT_OK
    finally:
        lock.release()


def cmd_stop(spec: EngineSpec, port: int, service_dir: Path, ops: ServiceOps, out: IO[str]) -> int:
    notes: list[str] = []
    lock = ControlLock(service_dir)
    acquired = lock.acquire(notes)
    for note in notes:  # 锁获取结果（含陈旧锁回收）恒可见
        say(out, note)
    if not acquired:
        return EXIT_REFUSED
    try:
        result = inspect_engine(spec, port, service_dir, ops)
        for note in result.notes:
            say(out, note)
        if result.manifest is None:
            if result.listening:
                owners = result.owners if result.owners else "未知（ss 不可用）"
                say(out, f"{spec.name}: 拒绝停止——端口 {port} 有不受管监听（属主 PID: {owners}）")
                say(out, "      本工具只停 manifest 归属的进程（拒绝误杀边界）；确需停止请手动处理该进程")
                return EXIT_REFUSED
            say(out, f"{spec.name}: 无 manifest——无需停止（幂等）")
            return EXIT_OK
        if result.ownership == "unknown":
            say(out, f"{spec.name}: manifest PID {result.manifest.pid} 归属无法核实——拒绝发送信号")
            return EXIT_REFUSED
        if result.ownership == "foreign":
            say(out, f"{spec.name}: 拒绝停止——PID {result.manifest.pid} 的工作区与记录不符（疑似另一检出实例）")
            say(out, "      manifest 保留；请人工核实 /proc 后处理")
            return EXIT_REFUSED
        pid = result.manifest.pid
        try:
            outcome = ops.terminate(pid, TERMINATE_GRACE_SECONDS)
        except OpsError as cause:
            say(out, f"{spec.name}: 停止失败（PID {pid}）——{cause}；manifest 保留以便重试")
            return EXIT_ERROR
        if outcome == "failed":
            say(out, f"{spec.name}: 停止失败——PID {pid} 经 TERM+KILL 仍存活；manifest 保留以便重试")
            return EXIT_ERROR
        remove_manifest(service_dir)
        verb = {"terminated": "已优雅退出（TERM）", "killed": "宽限超时，已 KILL 兜底",
                "already-dead": "进程已不存在（按 stale 清理）"}[outcome]
        say(out, f"{spec.name}: 已停止（PID {pid}，{verb}）；manifest 已清理")
        return EXIT_OK
    finally:
        lock.release()


def cmd_restart(spec: EngineSpec, port: int, service_dir: Path, ops: ServiceOps, out: IO[str]) -> int:
    say(out, f"=== restart {spec.name}: stop → start 受控序列 ===")
    stop_rc = cmd_stop(spec, port, service_dir, ops, out)
    if stop_rc != EXIT_OK:
        say(out, f"restart 中止：stop 未成功（rc={stop_rc}）——未启动新实例")
        return stop_rc
    return cmd_start(spec, port, service_dir, ops, out)


COMMANDS = {"status": cmd_status, "start": cmd_start, "stop": cmd_stop, "restart": cmd_restart}


def _parse_int_env(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def build_parser() -> argparse.ArgumentParser:
    engine_choices = [spec.name for spec in ENGINE_SPECS] + ["all"]
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--engine", choices=engine_choices, default="all",
                        help="目标引擎（默认 all）")
    common.add_argument("--port", type=int, default=None,
                        help="覆盖监听端口（默认: --engine 单选时可用；all 时按各自默认/环境变量）")
    common.add_argument("--service-dir", type=Path, default=None,
                        help="覆盖服务目录（默认 <repo>/artifacts/voice/<engine>/service；主要供测试/诊断）")
    parser = argparse.ArgumentParser(
        prog="voice_service_control.py",
        description="本地语音服务（FunASR/CosyVoice）受控生命周期管理：status/start/stop/restart",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "start", "stop", "restart"):
        subparsers.add_parser(name, parents=[common], help=f"{name} 生命周期操作")
    return parser


def main(argv: Sequence[str] | None = None, *, ops: ServiceOps | None = None,
         stdout: IO[str] | None = None) -> int:
    out = stdout if stdout is not None else sys.stdout
    if out is sys.stdout and sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):  # pragma: no cover —— 非 Windows 控制台
            pass
    args = build_parser().parse_args(argv)
    if args.engine == "all" and args.port is not None:
        say(out, "--port 只能配合单引擎 --engine 使用（all 时两引擎端口不同）")
        return 2
    if args.engine == "all" and args.service_dir is not None:
        say(out, "--service-dir 只能配合单引擎 --engine 使用（两引擎 manifest 不可共目录）")
        return 2
    specs = ENGINE_SPECS if args.engine == "all" else tuple(s for s in ENGINE_SPECS if s.name == args.engine)
    service_ops = ops if ops is not None else BashOps(REPO_ROOT)
    handler = COMMANDS[args.command]
    worst = EXIT_OK
    for spec in specs:
        port = resolve_port(spec, args.port)
        service_dir = args.service_dir if args.service_dir is not None else default_service_dir(spec)
        try:
            rc = handler(spec, port, service_dir, service_ops, out)
        except OpsError as cause:
            say(out, f"{spec.name}: 操作失败——{cause}")
            rc = EXIT_ERROR
        worst = max(worst, rc)
        if out is not sys.stdout:
            out.write("\n")
    return worst


if __name__ == "__main__":
    sys.exit(main())
