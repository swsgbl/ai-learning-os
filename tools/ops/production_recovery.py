#!/usr/bin/env python
"""M14-06 Windows 生产恢复编排：Docker 引擎等待 → compose 幂等 up → 健康核查 → 本地语音受控调和。

设计（与 tools/voice/voice_service_control.py 同款纪律：单文件、纯标准库、
零第三方依赖；平台操作经 Runner 注入，测试注入 FakeRunner——不碰真实
Docker/WSL/8010/8011）：

- 恢复顺序：等 Docker 引擎（轮询 ``docker version``，登录自愈给 Docker
  Desktop 留启动时间）→ ``docker compose config --quiet`` 静态校验 →
  pin check（env 文件 vs 在线容器，键名比对，绝不输出值）→ 只读栈健康
  快照 → up 决策 → 六服务健康等待 → 本地语音调和（status first）。
- up 决策（M14-39 恢复边界）：startup recovery 是「恢复健康」，不是部署/
  config drift 收敛——六服务全部 healthy/running 时，dry-run 与 enforce
  一致**跳过 compose up**（明确输出健康栈无需 up，继续语音调和与最终
  判定；拓扑变更由 tools/ops/livekit_lan_cutover.py 显式执行）。栈有
  缺失/不健康时保留原唯一 up 语义 ``up -d --no-build``（dry-run 附加
  compose 原生 ``--dry-run``；幂等：配置未变即 no-op；--env-file 锁定
  部署事实）——不加 ``--no-deps``、不加 ``--no-recreate``、不隐藏漂移
  （恢复路径整栈 up 口径，与 M14-38 cutover 的最小范围重建分工）。
- up 前只读本地镜像预检（M14-39）：compose 自建镜像锚点（``aios/minio:
  <RELEASE>``——M14-13 起本地构建、registry 不可拉取）经
  ``docker image inspect`` 探测（零 pull/build/stop/restart）；缺失且栈
  非全健康 → 输出 ``minio-local-image-missing`` 并在 up 之前 fail-closed
  （本工具绝不 pull/build——缺镜像 = 人工在获准窗口构建后重试；探测
  失败同样按缺失处理，fail-closed）。栈全健康时跳过 up 即不触发预检
  （健康栈不因「本机未构建自建镜像」被误伤）。
- pin check（fail-closed）：``infra/env.production-recovery``（gitignored，
  模板 infra/env.production-recovery.example）缺失、必需键缺失、含模板占位
  值、在线容器事实缺失、或值与在线容器不一致（仅报键名）→ enforce 在 up
  之前可见拒绝——防止恢复路径用默认值/漂移值/占位值静默重建容器（镜像 tag /
  端口 / 密钥轮换）。secret 永不进入进程 env、日志或输出：所有子进程输出经
  redact() 防御性脱敏后才落日志。
- 语音调和决策（decide_voice_action，纯函数）：先经既有 status 控制
  （tools/voice/voice_service_control.py 的 inspect_engine，只读）取状态——
  * unmanaged-running/managed-running 且 /health 200 → 不触碰（leave）；
  * stopped → 仅此情形经 voice_service_control.py start（受控 spawn）；
  * unknown / managed-mismatch（foreign）/ port-mismatch / 既有
    managed-starting（非本轮启动）→ 可见失败：不 spawn、不发信号、不清理
    manifest（start 一律不调用，由语音工具自身的 fail-closed 语义兜底）；
  * unmanaged-running 非 200 → 不触碰但可见 DEGRADED（拒绝误杀边界，交人工）。
- 不可触碰边界（源码契约测试锁定）：本工具绝不构造 stop/rm/kill/down/
  restart 命令；绝不 pkill/按端口杀；绝不 pull/build；恢复路径 up 恒整栈
  （绝不 --no-deps/--no-recreate）；Windows 侧子进程恒 CREATE_NO_WINDOW
  （无弹窗）。--dry-run 模式全程只读（compose 自带 --dry-run 亦只读）。

退出码：0 恢复完成/无需恢复且全部核查通过；1 可见失败（引擎等待超时/
compose 失败/健康未达/语音 fail 状态/pin 拒绝/自建镜像缺失
（minio-local-image-missing）/DEGRADED）；2 参数错误。

用法（仓库根）：
  python tools/ops/production_recovery.py --dry-run   # 只读体检 + 决策预告
  python tools/ops/production_recovery.py             # 实际恢复（需 env pin 就绪）
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
VOICE_SCRIPT = REPO_ROOT / "tools" / "voice" / "voice_service_control.py"
ARTIFACT_DIR = REPO_ROOT / "artifacts" / "recovery"

DEFAULT_PROJECT = "aios-m14-03-production-rehearsal"
DEFAULT_PROFILE = "local"
DEFAULT_ENV_FILE = REPO_ROOT / "infra" / "env.production-recovery"

#: 生产彩排栈（--profile local）的服务集——运行时以 compose config --services
#: 渲染为准，此集合用于漂移告警（不硬性阻断，可见报告由人裁决）
EXPECTED_STACK_SERVICES = frozenset({"postgres", "redis", "minio", "api", "web", "livekit"})

#: up 前只读本地镜像预检集（M14-39）：compose 里带 build: 的自建镜像锚点
#: （registry 拉不到，up -d --no-build 需要本机已构建）。M14-13 起 minio
#: 为本地自建（infra/minio/Dockerfile，compose image: aios/minio:<RELEASE>）；
#: 本机未构建该镜像而栈又非全健康时，up 会在重建阶段失败——预检把该失败
#: 提前为 up 之前的可见拒绝（绝不 pull/build）。镜像引用是 repo 公开 compose
#: 锚点（非 env secret），升版 = 与 compose image: 同步改此处（契约测试
#: 交叉锁定）。
LOCAL_BUILD_IMAGE_REFS: tuple[str, ...] = (
    "aios/minio:RELEASE.2025-10-15T17-29-55Z",
)

#: 自建镜像缺失的固定失败词汇（日志可见；镜像引用本身可回显——repo 锚点）
LOCAL_IMAGE_MISSING = "minio-local-image-missing"

#: pin 必需键（env 文件与在线容器比对；输出仅键名，值绝不落日志）。
#: M14-09：AIOS_WEB_IMAGE_TAG 独立成键——web 镜像 tag 不再与 api 共用
#: AIOS_IMAGE_TAG（同 tag 发布 = env 文件里两键显式同值；Web-only 升级 =
#: 仅改 AIOS_WEB_IMAGE_TAG）。在线事实取自 web 容器镜像（collect_live_pins）。
#: M14-38 拓扑键（LAN cutover 防漂移）：信令/媒体面绑定与浏览器可达 URL 纳入
#: pin——否则恢复路径会以 env 缺省把拓扑静默重建回 loopback（compose 中
#: AIOS_LIVEKIT_BIND_IP 缺省回落 AIOS_BIND_IP→127.0.0.1）。在线事实：
#: AIOS_BIND_IP/AIOS_PUBLIC_LIVEKIT_URL 取自 api 容器 env（HOST_BIND_IP=/
#: PUBLIC_LIVEKIT_URL=；PUBLIC 为空串也是事实 = 容器未注入公开地址 → 与 env
#: 非空值不等 = 漂移可见）；AIOS_LIVEKIT_BIND_IP 取自 `docker port <livekit>
#: 7880` 的宿主绑定段。loopback 部署同样显式填 127.0.0.1 / ws://127.0.0.1:7880。
TOPOLOGY_PIN_KEYS: tuple[str, ...] = (
    "AIOS_BIND_IP",
    "AIOS_LIVEKIT_BIND_IP",
    "AIOS_PUBLIC_LIVEKIT_URL",
)
PIN_KEYS: tuple[str, ...] = (
    "AIOS_IMAGE_TAG",
    "AIOS_WEB_IMAGE_TAG",
    "AIOS_APP_ENV",
    "AIOS_WEB_PORT",
    "AIOS_AUTH_SECRET",
    "AIOS_LIVEKIT_API_SECRET",
    *TOPOLOGY_PIN_KEYS,
)
#: env 文件键 → 在线容器事实的提取方式（inspect format 见 collect_live_pins）
ENGINE_POLL_SECONDS = 5.0
DEFAULT_ENGINE_WAIT_SECONDS = 300
DEFAULT_HEALTH_WAIT_SECONDS = 420
TAG = "[recovery]"


class RunnerError(RuntimeError):
    """平台命令执行失败（transport 不可用/超时）——调用方转为可见失败。"""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class RawCommandResult:
    """子进程原始字节结果——供调用方自行做编码鲁棒解码（如 schtasks /XML）。

    M14-06 R3：schtasks /Query /XML 管道输出实测为 UTF-16LE **可带可不带 BOM**；
    text-mode（encoding='utf-16'）解法会在读线程抛 UnicodeError 丢输出（生产
    实证）。需要字节面判断编码的调用方改用 ``run_raw`` 后自行解码。
    """

    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes


class Runner(Protocol):
    def run(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0,
            encoding: str | None = None) -> CommandResult: ...

    def run_raw(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0,
                ) -> RawCommandResult: ...


class RealRunner:
    """真实子进程执行：capture + UTF-8 + Windows 侧恒 CREATE_NO_WINDOW（无弹窗）。

    ``run`` 默认 UTF-8 + replace（向后兼容，语义不变）；encoding 覆写保留给
    已知恒定编码的调用方。UTF-16 管道输出（schtasks /Query /XML，BOM 有无随
    版本/通道而变）一律改走 ``run_raw`` 原始字节 + 调用方鲁棒解码（M14-06 R3，
    见 windows_startup_task.decode_schtasks_xml）。
    """

    def run(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0,
            encoding: str | None = None) -> CommandResult:
        text_argv = [str(item) for item in argv]
        kwargs: dict[str, object] = {
            "capture_output": True,
            "text": True,
            "encoding": encoding or "utf-8",
            "errors": "replace",
        }
        if os_windows():
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        try:
            result = subprocess.run(
                text_argv, check=False, timeout=timeout, **kwargs  # type: ignore[arg-type]
            )
        except (OSError, subprocess.TimeoutExpired) as cause:
            raise RunnerError(f"命令不可执行/超时: {text_argv[0]}: {type(cause).__name__}") from cause
        return CommandResult(tuple(text_argv), result.returncode, result.stdout or "", result.stderr or "")

    def run_raw(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0,
                ) -> RawCommandResult:
        """字节面 capture（不做任何文本解码）：调用方自行判定编码后解码。"""
        text_argv = [str(item) for item in argv]
        kwargs: dict[str, object] = {"capture_output": True}
        if os_windows():
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        try:
            result = subprocess.run(
                text_argv, check=False, timeout=timeout, **kwargs  # type: ignore[arg-type]
            )
        except (OSError, subprocess.TimeoutExpired) as cause:
            raise RunnerError(f"命令不可执行/超时: {text_argv[0]}: {type(cause).__name__}") from cause
        return RawCommandResult(tuple(text_argv), result.returncode,
                                result.stdout or b"", result.stderr or b"")


def os_windows() -> bool:
    return os.name == "nt"


class RunLog:
    """运行日志：行缓冲（stdout + gitignored artifacts 文件）；写前经 redact。"""

    def __init__(self, redactions: dict[str, str] | None = None, echo: bool = True) -> None:
        self.lines: list[str] = []
        self._redactions = {k: v for k, v in (redactions or {}).items() if len(k) >= 8}
        self._echo = echo

    def redact(self, text: str) -> str:
        for secret, label in self._redactions.items():
            if secret and secret in text:
                text = text.replace(secret, label)
        return text

    def say(self, message: str) -> None:
        line = self.redact(message)
        self.lines.append(line)
        if self._echo:
            print(f"{TAG} {line}", flush=True)

    def write_file(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        path = directory / f"recovery-{stamp}.log"
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8", newline="\n")
        return path


# ---------------------------------------------------------------- pin check

@dataclass(frozen=True)
class PinReport:
    ok: bool
    env_present: bool
    missing_keys: tuple[str, ...] = ()
    mismatched_keys: tuple[str, ...] = ()
    missing_live_keys: tuple[str, ...] = ()
    placeholder_keys: tuple[str, ...] = ()
    live_present: bool = True
    reasons: tuple[str, ...] = ()


def parse_env_file(path: Path) -> dict[str, str]:
    """解析 KEY=VALUE 行（# 注释/空行跳过）——值只留在内存，绝不回显。"""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        values[key.strip()] = value.strip()
    return values


def container_name(project: str, service: str, suffix: int = 1) -> str:
    return f"{project}-{service}-{suffix}"


def collect_live_pins(runner: Runner, project: str) -> dict[str, str] | None:
    """从在线容器提取部署 pin 事实（值只进内存；容器缺失 → None）。"""
    api = container_name(project, "api")
    web = container_name(project, "web")
    env_result = runner.run(
        ["docker", "inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", api],
        timeout=30.0,
    )
    if env_result.returncode != 0:
        return None  # api 容器不存在（栈未起）——无需比对
    live: dict[str, str] = {}
    for line in env_result.stdout.splitlines():
        if line.startswith("APP_ENV="):
            live["AIOS_APP_ENV"] = line.partition("=")[2]
        elif line.startswith("AUTH_SECRET="):
            live["AIOS_AUTH_SECRET"] = line.partition("=")[2]
        elif line.startswith("LIVEKIT_API_SECRET="):
            live["AIOS_LIVEKIT_API_SECRET"] = line.partition("=")[2]
        # M14-38 拓扑事实（api 容器 env）：HOST_BIND_IP= 即 AIOS_BIND_IP 插值；
        # PUBLIC_LIVEKIT_URL= 空串也是事实（容器未注入公开地址 → 与 env 非空值不等）
        elif line.startswith("HOST_BIND_IP="):
            live["AIOS_BIND_IP"] = line.partition("=")[2]
        elif line.startswith("PUBLIC_LIVEKIT_URL="):
            live["AIOS_PUBLIC_LIVEKIT_URL"] = line.partition("=")[2]
    image_result = runner.run(
        ["docker", "inspect", "--format", "{{.Config.Image}}", api], timeout=30.0
    )
    if image_result.returncode == 0 and ":" in image_result.stdout:
        live["AIOS_IMAGE_TAG"] = image_result.stdout.strip().rpartition(":")[2]
    # M14-09：web 镜像 tag 独立事实（与 api 镜像分别 inspect，互不派生）
    web_image_result = runner.run(
        ["docker", "inspect", "--format", "{{.Config.Image}}", web], timeout=30.0
    )
    if web_image_result.returncode == 0 and ":" in web_image_result.stdout:
        live["AIOS_WEB_IMAGE_TAG"] = web_image_result.stdout.strip().rpartition(":")[2]
    port_result = runner.run(
        ["docker", "port", web, "3000"], timeout=30.0
    )
    if port_result.returncode == 0 and port_result.stdout.strip():
        # 输出形如 127.0.0.1:3011（多行时取首个）
        live["AIOS_WEB_PORT"] = port_result.stdout.splitlines()[0].strip().rpartition(":")[2]
    # M14-38：livekit 信令端口的宿主绑定段（127.0.0.1:7880 / <LAN-IP>:7880）
    # 即媒体面拓扑事实——与 web 端口提取方向相反（取冒号前的宿主地址段）
    livekit_port_result = runner.run(
        ["docker", "port", container_name(project, "livekit"), "7880"], timeout=30.0
    )
    if livekit_port_result.returncode == 0 and livekit_port_result.stdout.strip():
        live["AIOS_LIVEKIT_BIND_IP"] = (
            livekit_port_result.stdout.splitlines()[0].strip().rpartition(":")[0]
        )
    return live


#: 模板（infra/env.production-recovery.example）中的占位值——照抄模板未填真实
#: 值时（尤其「栈未起、无在线容器可比对」的恢复场景）必须在 up 之前拒绝
TEMPLATE_PLACEHOLDER_VALUES = frozenset({
    "<部署时生成的真实值——绝不提交>",
})


def placeholder_pin_keys(values: dict[str, str]) -> tuple[str, ...]:
    """识别仍是模板占位值的 pin 键（``<...>`` 包裹或模板原文）——返回键名。"""
    placeholders: list[str] = []
    for key in PIN_KEYS:
        value = values.get(key, "")
        wrapped = value.startswith("<") and value.endswith(">") and len(value) > 2
        if wrapped or value in TEMPLATE_PLACEHOLDER_VALUES:
            placeholders.append(key)
    return tuple(placeholders)


def check_pins(env_path: Path, runner: Runner, project: str, log: RunLog) -> PinReport:
    """env 文件与在线容器的 pin 一致性核查（输出仅键名与布尔，绝不输出值）。

    九键一致性 fail-closed（M14-09 增 AIOS_WEB_IMAGE_TAG；M14-38 增拓扑键
    AIOS_BIND_IP/AIOS_LIVEKIT_BIND_IP/AIOS_PUBLIC_LIVEKIT_URL）：在线容器存在时，
    任一 PIN_KEY 在线事实缺失（inspect/port 探测不完整）或与 env 不等 →
    ok=False（supervisor 评审修正：缺事实不得按「跳过」放行）；模板占位值
    恒拒绝（含无在线容器路径）。
    """
    env_values = parse_env_file(env_path)
    if not env_path.is_file():
        log.say(f"pin: env 文件缺失（{env_path}）——enforce 将拒绝执行 up")
        log.say(f"      请自模板 {env_path}.example 创建并填入部署事实（必需键: {', '.join(PIN_KEYS)}）")
        return PinReport(ok=False, env_present=False, missing_keys=PIN_KEYS)
    missing = tuple(key for key in PIN_KEYS if not env_values.get(key))
    if missing:
        log.say(f"pin: env 文件缺必需键: {', '.join(missing)}——enforce 将拒绝执行 up")
    placeholders = placeholder_pin_keys(env_values)
    if placeholders:
        log.say(f"pin: env 文件含模板占位值键: {', '.join(placeholders)}——请填入真实部署值（值不回显）")
        log.say("      enforce 将拒绝执行 up（占位 secret 不得用于创建/重建容器）")
    live = collect_live_pins(runner, project)
    if live is None:
        log.say("pin: 在线容器不存在（栈未起）——跳过在线比对，up 将按 env 事实拉起")
        report = PinReport(ok=not missing and not placeholders, env_present=True,
                           missing_keys=missing, placeholder_keys=placeholders, live_present=False)
        if report.ok:
            log.say("pin: OK（env 九键齐全且无占位值；无在线容器可比对）")
        return report
    missing_live = tuple(key for key in PIN_KEYS if live.get(key) is None)
    if missing_live:
        log.say(f"pin: 在线容器事实缺失键: {', '.join(missing_live)}——九键一致性不可证")
        log.say("      enforce 将拒绝执行 up（inspect/port 探测不完整时不得按跳过放行）")
    mismatched = tuple(
        key for key in PIN_KEYS
        if key not in missing_live and env_values.get(key) != live.get(key)
    )
    matched = [key for key in PIN_KEYS if key not in mismatched and key not in missing_live]
    log.say(f"pin: 一致键 {len(matched)}/{len(PIN_KEYS)}（值不回显）；比对对象: api/web/livekit 容器")
    if mismatched:
        # 仅报键名：值差异（密钥轮换/漂移）用 up 重建是危险的，必须可见拒绝
        log.say(f"pin: 不一致键: {', '.join(mismatched)}——与在线容器不符（值不回显）")
        log.say("      enforce 将拒绝执行 up（防止漂移值静默重建容器）；请人工核实后更新 env 文件")
    report = PinReport(ok=not missing and not placeholders and not missing_live and not mismatched,
                       env_present=True, missing_keys=missing, mismatched_keys=mismatched,
                       missing_live_keys=missing_live, placeholder_keys=placeholders)
    if report.ok:
        log.say("pin: OK（九键齐全：env 无缺键/占位，且与在线容器逐键一致）")
    return report


# ---------------------------------------------------------------- 语音决策

LEAVE = "leave"
START = "start"
FAIL = "fail"


@dataclass(frozen=True)
class VoiceDecision:
    action: str
    severity: str  # "ok" | "warn"
    reason: str


def decide_voice_action(state: str, health_code: int | None, *, started_this_run: bool = False) -> VoiceDecision:
    """本地语音调和决策（纯函数；状态来自 voice_service_control 的 Inspection.state）。

    边界：fail = 可见失败且不调用 start（不 spawn、不发信号、不清理 manifest）；
    leave = 不触碰；start = 唯一放行的受控动作（仅 stopped）。
    """
    if state == "stopped":
        return VoiceDecision(START, "ok", "stopped（无 manifest 无监听）——经受控工具 start")
    if state == "managed-starting":
        if started_this_run:
            return VoiceDecision(LEAVE, "ok", "本轮已 start，bootstrap 进行中（预期，无需干预）")
        return VoiceDecision(FAIL, "warn",
                             "managed-starting（既有 manifest 归属进程存活但端口未监听）——"
                             "疑似半启动/卡死的 bootstrap；不 spawn 第二实例、不发信号、不清理 manifest")
    if state == "port-mismatch":
        return VoiceDecision(FAIL, "warn",
                             "port-mismatch（manifest 端口与当前请求端口不符）——"
                             "不 spawn、不发信号、manifest 原样保留，人工裁决")
    if state == "managed-mismatch":
        return VoiceDecision(FAIL, "warn",
                             "managed-mismatch（cmdline 匹配但工作区不符，疑似另一检出实例）——"
                             "不 spawn、不发信号，人工裁决")
    if state == "unknown":
        return VoiceDecision(FAIL, "warn",
                             "unknown（manifest 归属无法核实）——不 spawn、不发信号，人工裁决")
    # unmanaged-running / managed-running：已在监听——恒不触碰（拒绝误杀边界）
    if health_code == 200:
        return VoiceDecision(LEAVE, "ok", f"{state} 且 /health 200——不触碰（healthy untouched）")
    if started_this_run:
        return VoiceDecision(LEAVE, "ok",
                             f"{state}，本轮启动后已监听，/health={health_code}（升温中，预期）")
    return VoiceDecision(LEAVE, "warn",
                         f"{state} 但 /health={health_code}——不触碰（拒绝误杀边界），"
                         f"需人工关注（DEGRADED）")


@dataclass(frozen=True)
class VoiceStatus:
    engine: str
    state: str
    health_code: int | None
    notes: tuple[str, ...] = ()


class VoiceGateway(Protocol):
    def inspect(self, engine: str) -> VoiceStatus: ...
    def start(self, engine: str) -> tuple[int, str]: ...


def _load_voice_module() -> object:
    spec = importlib.util.spec_from_file_location("voice_service_control", VOICE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RealVoiceGateway:
    """既有 status 控制的薄封装：inspect 复用 voice_service_control.inspect_engine（只读）；
    start 经受控 CLI 子进程（其自身锁/幂等/fail-closed 语义不变）。"""

    def __init__(self, runner: Runner | None = None) -> None:
        self._module = _load_voice_module()
        self._runner = runner or RealRunner()

    def inspect(self, engine: str) -> VoiceStatus:
        module = self._module
        spec = next(s for s in module.ENGINE_SPECS if s.name == engine)  # type: ignore[attr-defined]
        ops = module.BashOps(module.REPO_ROOT)  # type: ignore[attr-defined]
        port = module.resolve_port(spec, None)  # type: ignore[attr-defined]
        service_dir = module.default_service_dir(spec)  # type: ignore[attr-defined]
        result = module.inspect_engine(spec, port, service_dir, ops)  # type: ignore[attr-defined]
        return VoiceStatus(engine=engine, state=result.state,
                           health_code=result.health_code, notes=tuple(result.notes))

    def start(self, engine: str) -> tuple[int, str]:
        result = self._runner.run(
            [sys.executable, str(VOICE_SCRIPT), "start", "--engine", engine], timeout=180.0
        )
        return result.returncode, result.stdout + result.stderr


# ---------------------------------------------------------------- 编排

def _compose_base(compose_file: Path, project: str, profile: str, env_file: Path | None) -> list[str]:
    argv = ["docker", "compose", "-f", str(compose_file), "-p", project]
    if env_file is not None:
        argv += ["--env-file", str(env_file)]
    argv += ["--profile", profile]
    return argv


def wait_docker_engine(runner: Runner, log: RunLog, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        result = runner.run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=30.0)
        if result.returncode == 0 and result.stdout.strip():
            log.say(f"docker engine: 就绪（server {result.stdout.strip()}）")
            return True
        if time.monotonic() >= deadline:
            log.say(f"docker engine: 等待超时（{timeout_seconds}s）——可见失败（Docker Desktop 未就绪？）")
            return False
        time.sleep(ENGINE_POLL_SECONDS)


def validate_compose(runner: Runner, log: RunLog, compose_file: Path, project: str,
                     profile: str, env_file: Path | None) -> bool:
    argv = _compose_base(compose_file, project, profile, env_file) + ["config", "--quiet"]
    result = runner.run(argv, timeout=120.0)
    if result.returncode != 0:
        log.say(f"compose config: 校验失败（rc={result.returncode}）——可见失败")
        log.say(f"stderr: {result.stderr.strip()[:400]}")
        return False
    log.say("compose config: OK（--quiet 静态校验通过）")
    return True


def rendered_services(runner: Runner, compose_file: Path, project: str, profile: str,
                      env_file: Path | None) -> set[str] | None:
    argv = _compose_base(compose_file, project, profile, env_file) + ["config", "--services"]
    result = runner.run(argv, timeout=120.0)
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def snapshot_health(runner: Runner, compose_file: Path, project: str, profile: str) -> dict[str, str]:
    """docker compose ps --format json → {service: health 或 state}（只读快照）。"""
    argv = _compose_base(compose_file, project, profile, None) + ["ps", "--format", "json"]
    result = runner.run(argv, timeout=60.0)
    state: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        service = str(row.get("Service", ""))
        if service:
            state[service] = str(row.get("Health") or row.get("State") or "")
    return state


def stack_health_gaps(health: dict[str, str], expected: set[str]) -> tuple[set[str], set[str]]:
    """健康快照 vs 预期服务集 → (缺失, 未达 healthy/running)。

    单一判定口径：up 决策（M14-39 健康栈跳过）、dry-run 报告与
    wait_stack_healthy 共用——running 无 healthcheck 的服务同样达标。
    """
    missing = expected - set(health)
    unhealthy = {name for name in expected - missing if health.get(name) not in ("healthy", "running")}
    return missing, unhealthy


def wait_stack_healthy(runner: Runner, log: RunLog, compose_file: Path, project: str,
                       profile: str, expected: set[str], timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, str] = {}
    while True:
        last = snapshot_health(runner, compose_file, project, profile)
        missing, unhealthy = stack_health_gaps(last, expected)
        if not missing and not unhealthy:
            log.say(f"stack health: {len(expected)}/{len(expected)} 服务 healthy/running——OK")
            return True
        if time.monotonic() >= deadline:
            for name in sorted(missing):
                log.say(f"stack health: 服务 {name} 未出现在 compose ps——可见失败")
            for name in sorted(unhealthy):
                log.say(f"stack health: 服务 {name} 状态 {last.get(name)!r} 非 healthy——可见失败")
            return False
        time.sleep(ENGINE_POLL_SECONDS)


def check_local_build_images(runner: Runner, log: RunLog) -> tuple[str, ...]:
    """up 前只读本地镜像预检：docker image inspect（零 pull/build/stop）。

    返回本机缺失的自建镜像引用（探测失败同样按缺失处理——fail-closed，
    引擎故障不得伪装成「镜像在场」）；缺失时逐项可见并输出
    ``minio-local-image-missing`` 固定词汇。调用方据此在 up 之前 fail-closed。
    仅在将执行 up（栈非全健康）时调用——健康栈跳过 up 即无需预检。
    """
    missing: list[str] = []
    for ref in LOCAL_BUILD_IMAGE_REFS:
        result = runner.run(["docker", "image", "inspect", ref, "--format", "{{.Id}}"], timeout=30.0)
        if result.returncode != 0:
            missing.append(ref)
            log.say(f"local image: {ref} 本机缺失（compose 自建锚点，registry 不可拉取）")
    if missing:
        log.say(f"local image 预检: {LOCAL_IMAGE_MISSING}——up 将在之前拒绝"
                "（本工具绝不 pull/build；请在获准窗口构建后重试）")
    return tuple(missing)


def compose_up(runner: Runner, log: RunLog, compose_file: Path, project: str, profile: str,
               env_file: Path, *, dry_run: bool) -> bool:
    argv = _compose_base(compose_file, project, profile, env_file) + ["up", "-d", "--no-build"]
    if dry_run:
        argv += ["--dry-run"]
    result = runner.run(argv, timeout=600.0)
    plan = result.stdout.strip()[-1200:] if result.stdout.strip() else result.stderr.strip()[-400:]
    if result.returncode != 0:
        log.say(f"compose up{' --dry-run' if dry_run else ''}: rc={result.returncode}——可见失败")
        log.say(log.redact(plan))
        return False
    verb = "计划（--dry-run，零改动）" if dry_run else "已执行（幂等：配置未变即 no-op）"
    log.say(f"compose up -d --no-build: {verb}")
    if plan:
        for line in plan.splitlines():
            if line.strip():
                log.say(log.redact(f"  | {line.strip()}"))
    return True


def reconcile_voice(gateway: VoiceGateway, log: RunLog, *, dry_run: bool) -> tuple[list[str], list[str]]:
    """逐引擎调和：status first → 决策 → （仅 stopped 且 enforce）受控 start。

    返回 (failures, degraded)：failures = 可见失败（含语音 fail 状态/start 失败）；
    degraded = 未触碰但健康非 200 的引擎（拒绝误杀边界，交人工——恢复结果
    仍算未达成，调用方据此退出非 0）。
    """
    engines = ("funasr", "cosyvoice")
    failures: list[str] = []
    degraded: list[str] = []
    for engine in engines:
        status = gateway.inspect(engine)
        for note in status.notes:
            log.say(f"{engine}: note: {note}")
        decision = decide_voice_action(status.state, status.health_code)
        log.say(f"{engine}: state={status.state} health={status.health_code} → {decision.action}（{decision.reason}）")
        if decision.action == FAIL:
            failures.append(f"{engine}:{status.state}")
            continue
        if decision.severity == "warn" and decision.action == LEAVE:
            degraded.append(f"{engine}:{status.state}/health={status.health_code}")
        if decision.action != START:
            continue
        if dry_run:
            log.say(f"{engine}: enforce 将执行受控 start（经 tools/voice/voice_service_control.py，幂等）")
            continue
        rc, output = gateway.start(engine)
        if rc != 0:
            log.say(f"{engine}: 受控 start 失败（rc={rc}）——可见失败；输出尾部:")
            log.say(log.redact(output[-800:]))
            failures.append(f"{engine}:start-rc{rc}")
            continue
        log.say(f"{engine}: 受控 start 完成（rc=0）——复查状态:")
        after = gateway.inspect(engine)
        log.say(f"{engine}: 启动后 state={after.state} health={after.health_code}")
        after_decision = decide_voice_action(after.state, after.health_code, started_this_run=True)
        log.say(f"{engine}: 启动后决策（按本轮已启动口径）: {after_decision.action}（{after_decision.reason}）")
        if after_decision.action == FAIL:
            failures.append(f"{engine}:post-start:{after.state}")
    return failures, degraded


def run_recovery(*, runner: Runner, voice: VoiceGateway, log: RunLog, dry_run: bool,
                 project: str = DEFAULT_PROJECT, profile: str = DEFAULT_PROFILE,
                 env_file: Path = DEFAULT_ENV_FILE, compose_file: Path = COMPOSE_FILE,
                 engine_wait_seconds: int = DEFAULT_ENGINE_WAIT_SECONDS,
                 health_wait_seconds: int = DEFAULT_HEALTH_WAIT_SECONDS) -> int:
    """恢复编排主流程。返回进程退出码；每步失败均可见（log 行）。"""
    mode = "DRY-RUN（全程只读）" if dry_run else "ENFORCE"
    log.say(f"=== 生产恢复编排 {mode}: project={project} profile={profile} env={env_file.name} ===")
    if not wait_docker_engine(runner, log, engine_wait_seconds):
        return EXIT_ERROR
    env_for_validation = env_file if env_file.is_file() else None
    if env_for_validation is None:
        log.say("compose config: env 文件缺失——按默认插值校验（pin check 稍后给出拒绝原因）")
    if not validate_compose(runner, log, compose_file, project, profile, env_for_validation):
        return EXIT_ERROR
    services = rendered_services(runner, compose_file, project, profile, env_for_validation)
    if services is None:
        log.say("compose config --services: 渲染失败——可见失败")
        return EXIT_ERROR
    log.say(f"compose services（--profile {profile}）: {', '.join(sorted(services))}")
    if services != set(EXPECTED_STACK_SERVICES):
        log.say(f"警告: 渲染服务集与预期漂移（预期 {sorted(EXPECTED_STACK_SERVICES)}）——继续执行并如实报告")
    pin = check_pins(env_file, runner, project, log)
    failures: list[str] = []
    gaps: tuple[set[str], set[str]] | None = None  # 健康快照缺口（pin ok 时已取）
    if not pin.ok:
        if dry_run:
            log.say("dry-run: enforce 将在 up 之前拒绝（pin 未就绪）——跳过 compose up 计划步骤")
            failures.append("pin-not-ready（dry-run 退出码镜像 enforce 拒绝）")
        else:
            log.say("enforce: pin 未就绪——拒绝执行 up（fail-closed，零容器改动）")
            return EXIT_ERROR
    else:
        # M14-39：up 前只读健康快照——全健康跳过 up（dry-run 与 enforce 同
        # 语义）；非全健康保留原唯一 up 语义，且 up 之前先做只读镜像预检。
        health = snapshot_health(runner, compose_file, project, profile)
        missing, unhealthy = stack_health_gaps(health, set(EXPECTED_STACK_SERVICES))
        gaps = (missing, unhealthy)
        if dry_run:
            log.say(f"stack health 快照（只读）: {json.dumps(dict(sorted(health.items())), ensure_ascii=False)}")
        if not missing and not unhealthy:
            log.say(f"stack health: {len(EXPECTED_STACK_SERVICES)}/{len(EXPECTED_STACK_SERVICES)} 服务 "
                    "healthy/running——健康栈无需 up（dry-run 与 enforce 同语义跳过）")
            log.say("      恢复边界：startup recovery 恢复健康，不做部署/config drift 收敛；"
                    "拓扑/配置变更由 tools/ops/livekit_lan_cutover.py 显式执行")
        elif check_local_build_images(runner, log):
            if dry_run:
                log.say("dry-run: enforce 将在 up 之前拒绝（up 所需自建镜像本机缺失）——跳过 compose up 计划步骤")
                failures.append(f"{LOCAL_IMAGE_MISSING}（dry-run 退出码镜像 enforce 拒绝）")
            else:
                log.say(f"enforce: {LOCAL_IMAGE_MISSING}——拒绝执行 up（fail-closed，零容器改动；绝不 pull/build）")
                return EXIT_ERROR
        else:
            if not compose_up(runner, log, compose_file, project, profile, env_file, dry_run=dry_run):
                failures.append("compose-up")
    if dry_run:
        if gaps is None:  # pin 未就绪路径：dry-run 仍给只读健康预告
            health = snapshot_health(runner, compose_file, project, profile)
            log.say(f"stack health 快照（只读）: {json.dumps(dict(sorted(health.items())), ensure_ascii=False)}")
            gaps = stack_health_gaps(health, set(EXPECTED_STACK_SERVICES))
        missing, unhealthy = gaps
        if missing or unhealthy:
            log.say(f"dry-run: enforce 将等待健康（当前缺失 {sorted(missing)} / 未达 {sorted(unhealthy)}，"
                    f"预算 {health_wait_seconds}s）")
    else:
        if not wait_stack_healthy(runner, log, compose_file, project, profile,
                                  set(EXPECTED_STACK_SERVICES), health_wait_seconds):
            failures.append("stack-health")
    voice_failures, degraded = reconcile_voice(voice, log, dry_run=dry_run)
    failures += voice_failures
    if failures:
        log.say(f"=== 结果: FAIL（{'; '.join(failures)}）===")
        return EXIT_ERROR
    if degraded:
        log.say(f"=== 结果: DEGRADED（未触碰但需人工关注: {'; '.join(degraded)}）===")
        return EXIT_ERROR
    log.say("=== 结果: OK（恢复完成/无需恢复，全部核查通过）===")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="production_recovery.py",
        description="Windows 生产恢复编排：等 Docker → compose 幂等 up → 健康核查 → 本地语音受控调和",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="全程只读体检 + 决策预告（compose 经 --dry-run 计划，零容器改动）")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help=f"compose 项目名（默认 {DEFAULT_PROJECT}）")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help=f"compose profile（默认 {DEFAULT_PROFILE}）")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE,
                        help="部署 pin env 文件（默认 infra/env.production-recovery；模板 .example）")
    parser.add_argument("--engine-wait-seconds", type=int, default=DEFAULT_ENGINE_WAIT_SECONDS,
                        help=f"等待 Docker 引擎时限（默认 {DEFAULT_ENGINE_WAIT_SECONDS}）")
    parser.add_argument("--health-wait-seconds", type=int, default=DEFAULT_HEALTH_WAIT_SECONDS,
                        help=f"等待栈健康时限（默认 {DEFAULT_HEALTH_WAIT_SECONDS}）")
    parser.add_argument("--no-log-file", action="store_true",
                        help="不写 artifacts 日志文件（测试/诊断用；stdout 照常）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # redactions：env 文件中的 secret 值 → 防御性脱敏标签（写日志前替换；
    # 值只进入本进程内存，永不进入输出）
    env_values = parse_env_file(args.env_file)
    redactions = {
        env_values[key]: f"***REDACTED:{key}***"
        for key in ("AIOS_AUTH_SECRET", "AIOS_LIVEKIT_API_SECRET")
        if env_values.get(key)
    }
    log = RunLog(redactions=redactions)
    outcome = run_recovery(
        runner=RealRunner(), voice=RealVoiceGateway(), log=log, dry_run=args.dry_run,
        project=args.project, profile=args.profile, env_file=args.env_file,
        engine_wait_seconds=args.engine_wait_seconds, health_wait_seconds=args.health_wait_seconds,
    )
    if not args.no_log_file:
        try:
            path = log.write_file(ARTIFACT_DIR)
            log.say(f"日志: {path}")
        except OSError as cause:
            log.say(f"日志写入失败（不影响恢复结果）: {type(cause).__name__}")
    return outcome


if __name__ == "__main__":
    sys.exit(main())
