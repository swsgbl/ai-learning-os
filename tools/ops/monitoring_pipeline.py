#!/usr/bin/env python
"""M14-14/M14-21 持续/定时监控采集管道 readiness：monitor(M14-12) →
history(M14-13) → insights(M14-15) 单次组合。

设计（与 tools/ops/production_monitor.py / monitoring_history.py 同款纪律：
单文件、纯标准库、零第三方依赖；子进程经 Runner 注入 + **固定命令白名单门**、
UTC 时间经 Clock 注入、文件面经 Fs 注入——开发回合零真实 Docker/零生产
HTTP/零计划任务注册，全部行为用 fake 注入测试锁定；真实执行仅由 supervisor
在获准窗口运行）：

- 双模式：默认 **plan（dry-run）**——零 subprocess、零网络、零生产读取、
  零调度器改动，仅打印三步计划并落 plan 报告（Runner **零构造**，构造计数
  测试锁定）；**execute** 需同时满足「旗标 + 精确确认短语」（``--execute``
  + ``--confirm "EXECUTE READ-ONLY MONITORING PIPELINE"`` 一字不差），缺一
  或短语不匹配即 EXIT 2 且**零 Runner 构造/调用**（fail-closed）。数值面
  （三步超时）硬性拒绝：非有限浮点（nan/inf/-inf）与超硬顶一律 EXIT 2
  （plan 同样校验，先于任何报告写入）。
- **固定命令白名单门（结构性）**：execute 仅允许三个**精确固定形态**——
  ``<python> tools/ops/production_monitor.py --execute --confirm "EXECUTE
  READ-ONLY PRODUCTION MONITORING"``（monitor 自身的门禁短语，与
  production_monitor.CONFIRM_PHRASE 逐字一致，回归测试锁定）、
  ``<python> tools/ops/monitoring_history.py``（全部默认参数）与
  ``<python> tools/ops/monitoring_insights.py --execute --confirm "EXECUTE
  READ-ONLY MONITORING INSIGHTS"``（insights 自身门禁短语，与
  monitoring_insights.CONFIRM_PHRASE 逐字一致；**恒不带 --source**——输入
  恒为 insights 默认源 = history canonical 输出目录，M14-21 起两处常量
  三方 resolve 全等，单一事实源）。任何其它 argv 在执行之前拒绝
  （CommandNotAllowedError，内层 runner 零调用）；**无 shell=True、无用户
  可注入命令/URL/env 展开**（CLI 不暴露任何进入命令的参数；子进程输出仅取
  returncode，stdout/stderr **绝不持久化/回显**）。
- 序列语义：恒为 monitor → history → insights；**history 仅在 monitor
  exit 0 后运行**（monitor ok|warn 才有完整可索引样本）；**insights 仅在
  history status=ok 后运行**（history.jsonl 完整落盘才可洞察）；任一前置
  失败（非零退出/超时/执行错误/skipped）→ 后续步骤状态 skipped + 固定
  词汇原因（``<prev>-status-<status>``），**前置步骤的退出码/类别如实
  保留绝不遮蔽**；insights 失败不改变 monitor/history 事实。逐步有界超时
  + 保守硬顶：monitor 60–540s（默认 480s，覆盖 monitor 内部最坏预算
  ~445s + 启动余量）、history 10–120s（默认 45s）、insights 5–50s（默认
  15s——纯本地只读工件处理，秒级完成即兜底杀停）——三步硬顶之和
  540+120+50=710s < 计划任务执行时限 PT12M=720s < PT15M 间隔。
- 重叠保护（fail-closed）：gitignored 工件目录内独占锁 ``pipeline.lock``
  （O_CREAT|O_EXCL 原子创建；锁/目录 symlink 一律拒绝）。锁已存在 → 可见
  拒绝 EXIT 2 且零步骤执行；**本轮零破坏性 stale-lock 清理**（陈旧锁由
  操作者人工处置，工具绝不代删）。锁体仅安全事实（schema/tool/UTC/pid）。
- 证据报告（schema 版本化 JSON + Markdown 原子写入，同目录 tmp + fsync +
  os.replace）：**仅安全事实**——模式、逐步状态（ok/failed/timeout/error/
  skipped/planned）与退出码、逐步 UTC 起止与时长、固定命令身份（仓内相对
  身份 + ``<python>`` 占位，绝无绝对本机路径）、有界脱敏错误类别/异常类名
  （固定词汇 detail）、步骤产物名 + SHA-256 + 字节数（若可得；monitor 产物
  按「步骤前后目录差集」发现，仅 hash ≤8 个 monitor-*.json；history 产物
  为两个固定名；insights 产物为两个固定名）、边界注记。**绝无 env 值/
  token/header/子进程原文日志/生产 ID**（写前 redact_secrets 终防线 +
  生成面固定词汇双保险）。报告写入失败 = 证据不可失 → EXIT 2。
- 退出码：0 plan 成功 / execute 三步全 ok；1 execute 已执行但有步骤失败
  （任一步非 ok / 锁释放失败——可见失败不遮蔽）；2 门禁缺失/不匹配、数值
  超顶、锁占用、symlink/越界路径、报告写入失败。
- 本工具零 HTTP、零 Docker 命令、零计划任务改动（源码契约锁定）；调度注册
  由 monitoring_pipeline_task.py（supervisor-only）承担；**本工具绝不宣称
  生产就绪；单次管道成功 ≠ production_ready**。

用法（仓库根）：
  python tools/ops/monitoring_pipeline.py                        # plan（默认，零执行）
  python tools/ops/monitoring_pipeline.py --execute \
      --confirm "EXECUTE READ-ONLY MONITORING PIPELINE"          # execute（单次组合）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

EXIT_OK = 0
#: 1 = execute 已执行但可见失败（步骤失败/锁释放失败）——绝不遮蔽
EXIT_STAGE_FAILED = 1
#: 2 = 门禁/数值/锁/路径/报告写入等 fail-closed 拒绝（零或终止执行）
EXIT_USAGE = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = REPO_ROOT / "tools" / "ops"
MONITOR_SCRIPT = OPS_DIR / "production_monitor.py"
HISTORY_SCRIPT = OPS_DIR / "monitoring_history.py"
INSIGHTS_SCRIPT = OPS_DIR / "monitoring_insights.py"
#: 三步产物目录（与三工具自身默认一致——契约测试锁定 resolve 全等；本工具
#: 只读发现，绝不写入）
MONITOR_ARTIFACT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-12-production-monitoring"
HISTORY_OUTPUT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-13-monitoring-history"
INSIGHTS_OUTPUT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-15-monitoring-insights"
#: 本工具自身报告/锁目录（gitignored）
ARTIFACT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-14-monitoring-pipeline"

TAG = "[pipeline]"
REPORT_SCHEMA_VERSION = 1
MILESTONE = "M14-14"
TOOL_NAME = "tools/ops/monitoring_pipeline.py"
MONITOR_TOOL_NAME = "tools/ops/production_monitor.py"
HISTORY_TOOL_NAME = "tools/ops/monitoring_history.py"
INSIGHTS_TOOL_NAME = "tools/ops/monitoring_insights.py"

#: execute 门禁之二：精确确认短语（一字不差）
CONFIRM_PHRASE = "EXECUTE READ-ONLY MONITORING PIPELINE"
#: monitor 自身门禁短语——与 production_monitor.CONFIRM_PHRASE 逐字一致
#: （固定命令白名单的组成部分；回归测试锁定两处恒相等）
MONITOR_CONFIRM_PHRASE = "EXECUTE READ-ONLY PRODUCTION MONITORING"
#: insights 自身门禁短语——与 monitoring_insights.CONFIRM_PHRASE 逐字一致
#: （固定命令白名单的组成部分；回归测试锁定两处恒相等）
INSIGHTS_CONFIRM_PHRASE = "EXECUTE READ-ONLY MONITORING INSIGHTS"

LOCK_NAME = "pipeline.lock"
LOCK_SCHEMA_VERSION = 1
HISTORY_OUTPUT_NAMES: tuple[str, ...] = ("history.jsonl", "history-summary.md")
INSIGHTS_OUTPUT_NAMES: tuple[str, ...] = ("insights.json", "insights-summary.md")

#: 步骤超时（秒）——有界 + 保守硬顶。预算链（与 monitoring_pipeline_task
#: 的 ExecutionTimeLimit=PT12M=720s 交叉 pin，测试锁定）：monitor 内部最坏
#: ~445s（compose ps 60 + 6×inspect 30 + 6×logs 30 + 5×HTTP 5）+ 启动余量
#: → 默认 480s；history 45s；insights 纯本地只读工件处理秒级完成 → 默认
#: 15s；三步硬顶之和 540+120+50=710s < 720s 执行时限 < PT15M 重复间隔
#: （调度器绝不先于内部超时杀整任务——避免调度器击杀留下 stale lock）。
MONITOR_TIMEOUT_DEFAULT = 480.0
MONITOR_TIMEOUT_MIN = 60.0
MONITOR_TIMEOUT_MAX = 540.0
HISTORY_TIMEOUT_DEFAULT = 45.0
HISTORY_TIMEOUT_MIN = 10.0
HISTORY_TIMEOUT_MAX = 120.0
INSIGHTS_TIMEOUT_DEFAULT = 15.0
INSIGHTS_TIMEOUT_MIN = 5.0
INSIGHTS_TIMEOUT_MAX = 50.0

#: 产物发现边界：每步至多 hash 的文件数 + 单文件字节数上限（超限记数不记哈希）
MAX_ARTIFACTS_HASHED_PER_STAGE = 8
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024

#: monitor 产物 stem 白名单（与 monitoring_history.ARTIFACT_STEM_RE 同款）
MONITOR_STEM_RE = re.compile(r"^monitor-[0-9]{8}-[0-9]{6}$")

_STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# ---------------------------------------------------------------- 数值面校验（纯）


def validate_timeouts(*, monitor_timeout: float, history_timeout: float,
                      insights_timeout: float) -> list[str]:
    """纯函数：返回违规清单（空 = 放行）。非有限浮点显式拒绝；plan 同样校验。"""
    problems: list[str] = []
    for name, value in (("monitor-timeout-seconds", monitor_timeout),
                        ("history-timeout-seconds", history_timeout),
                        ("insights-timeout-seconds", insights_timeout)):
        if not math.isfinite(value):
            problems.append(f"{name} 必须为有限数值（nan/inf 一律拒绝）")
    if math.isfinite(monitor_timeout) and not MONITOR_TIMEOUT_MIN <= monitor_timeout <= MONITOR_TIMEOUT_MAX:
        problems.append(f"monitor-timeout-seconds 必须在 {MONITOR_TIMEOUT_MIN:g}-{MONITOR_TIMEOUT_MAX:g}s（收到 {monitor_timeout}）")
    if math.isfinite(history_timeout) and not HISTORY_TIMEOUT_MIN <= history_timeout <= HISTORY_TIMEOUT_MAX:
        problems.append(f"history-timeout-seconds 必须在 {HISTORY_TIMEOUT_MIN:g}-{HISTORY_TIMEOUT_MAX:g}s（收到 {history_timeout}）")
    if math.isfinite(insights_timeout) and not INSIGHTS_TIMEOUT_MIN <= insights_timeout <= INSIGHTS_TIMEOUT_MAX:
        problems.append(f"insights-timeout-seconds 必须在 {INSIGHTS_TIMEOUT_MIN:g}-{INSIGHTS_TIMEOUT_MAX:g}s（收到 {insights_timeout}）")
    return problems


# ---------------------------------------------------------------- Runner（注入点 + 固定命令白名单门）


class RunnerError(RuntimeError):
    """平台命令执行失败（不可执行/超时）——调用方转为安全类别，绝不保留文本。"""


class RunnerTimeout(RunnerError):
    """子进程超时（subprocess.run 已杀子进程后抛出）。"""


class CommandNotAllowedError(RunnerError):
    """非白名单固定命令——在任何执行之前拒绝（fail-closed）。"""


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
    """真实子进程执行：list-argv 直跑（无 shell）、capture + UTF-8、Windows 侧
    恒 CREATE_NO_WINDOW。超时 → RunnerTimeout（subprocess.run 已终止子进程）。"""

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
        except subprocess.TimeoutExpired as cause:
            raise RunnerTimeout("step-timeout") from cause
        except (OSError, ValueError) as cause:
            raise RunnerError("step-exec-error") from cause
        return CommandResult(tuple(text_argv), result.returncode, result.stdout or "", result.stderr or "")


def allowed_step_argv(python_exe: str, *, monitor_script: Path = MONITOR_SCRIPT,
                      history_script: Path = HISTORY_SCRIPT,
                      insights_script: Path = INSIGHTS_SCRIPT) -> dict[str, tuple[str, ...]]:
    """三个**精确固定形态**（唯一可执行面；python 路径由构造侧固定，非用户
    输入。insights 恒不带 --source——输入恒为其默认源 = history canonical
    输出目录，M14-21 起两处常量三方 resolve 全等，单一事实源）。"""
    return {
        "monitor": (python_exe, str(monitor_script),
                    "--execute", "--confirm", MONITOR_CONFIRM_PHRASE),
        "history": (python_exe, str(history_script)),
        "insights": (python_exe, str(insights_script),
                     "--execute", "--confirm", INSIGHTS_CONFIRM_PHRASE),
    }


def is_allowed_step_command(argv: tuple[str, ...] | list[str], python_exe: str, *,
                            monitor_script: Path = MONITOR_SCRIPT,
                            history_script: Path = HISTORY_SCRIPT,
                            insights_script: Path = INSIGHTS_SCRIPT) -> bool:
    """结构性白名单：argv 与三个固定形态之一**逐 token 全等**才放行。"""
    tokens = tuple(str(item) for item in argv)
    return tokens in allowed_step_argv(python_exe, monitor_script=monitor_script,
                                       history_script=history_script,
                                       insights_script=insights_script).values()


class StepRunner:
    """白名单门装饰器：非固定命令在任何执行之前拒绝（fail-closed）。"""

    def __init__(self, inner: Runner, python_exe: str) -> None:
        self._inner = inner
        self._python_exe = python_exe

    def run(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0,
            encoding: str | None = None) -> CommandResult:
        if not is_allowed_step_command(argv, self._python_exe):
            raise CommandNotAllowedError("command-not-whitelisted")
        return self._inner.run(argv, timeout=timeout, encoding=encoding)


def categorize_runner_exception(exc: BaseException) -> tuple[str, str]:
    """Runner 异常 →（安全类别, 异常类名）。绝不保留 str(exc) 文本。"""
    if isinstance(exc, CommandNotAllowedError):
        category = "command-not-whitelisted"
    elif isinstance(exc, (RunnerTimeout, subprocess.TimeoutExpired)):
        category = "command-timeout"
    elif isinstance(exc, (RunnerError, OSError)):
        category = "command-exec-error"
    else:
        category = "internal-error"
    return category, type(exc).__name__


# ---------------------------------------------------------------- Clock（注入点）


class Clock(Protocol):
    def utc_now_iso(self) -> str: ...

    def stamp(self) -> str: ...

    def perf(self) -> float: ...


class RealClock:
    def utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def stamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    def perf(self) -> float:
        return time.perf_counter()


# ---------------------------------------------------------------- Fs（注入点）


class Fs(Protocol):
    def exists(self, path: Path) -> bool: ...

    def is_symlink(self, path: Path) -> bool: ...

    def mkdirs(self, directory: Path) -> None: ...

    def list_dir(self, directory: Path) -> list[str]: ...

    def read_bytes(self, path: Path) -> bytes: ...

    def lock_acquire(self, path: Path, body_text: str) -> bool: ...

    def lock_release(self, path: Path) -> None: ...

    def write_atomic(self, path: Path, text: str) -> None: ...


class RealFs:
    """真实文件面：目录/字节读 + O_EXCL 独占锁 + 原子写（tmp + fsync + replace）。"""

    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_symlink(self, path: Path) -> bool:
        return path.is_symlink()

    def mkdirs(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)

    def list_dir(self, directory: Path) -> list[str]:
        return sorted(entry.name for entry in directory.iterdir())

    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()

    def lock_acquire(self, path: Path, body_text: str) -> bool:
        """O_CREAT|O_EXCL|O_WRONLY 原子创建——已存在即 False（不读、不删、不覆盖）。"""
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        try:
            data = body_text.encode("utf-8")
            os.write(handle, data)
            os.fsync(handle)
        finally:
            os.close(handle)
        return True

    def lock_release(self, path: Path) -> None:
        path.unlink()

    def write_atomic(self, path: Path, text: str) -> None:
        tmp_path = path.with_name(f".{path.name}.tmp")
        if tmp_path.is_symlink():
            raise OSError("symlink-output-tmp")
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(tmp_path, path)
        except OSError:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise


class ReportPathError(RuntimeError):
    """报告路径非法（symlink 组件 / 越界 stem）——可见拒绝，零写入。"""


def reject_symlinked_path(fs: Fs, *paths: Path) -> None:
    """拒绝 symlink：目标自身 + 现存祖先组件（防静默越界重定向）。"""
    for path in paths:
        if fs.is_symlink(path):
            raise ReportPathError("symlink-target")
        for ancestor in path.parents:
            if fs.exists(ancestor) and fs.is_symlink(ancestor):
                raise ReportPathError("symlink-in-path")


# ---------------------------------------------------------------- 脱敏（防御性）

SECRET_SHAPE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "[REDACTED:token]"),
    (re.compile(r"ghp_[A-Za-z0-9]{8,}"), "[REDACTED:token]"),
    (re.compile(r"gho_[A-Za-z0-9]{8,}"), "[REDACTED:token]"),
    (re.compile(r"AKIA[0-9A-Z]{12,}"), "[REDACTED:key]"),
    (re.compile(r"xoxb-[A-Za-z0-9-]{8,}"), "[REDACTED:token]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"), "[REDACTED:token]"),
    (re.compile(r"(?i)(api[_-]?key|secret|password)\s*[=:]\s*\S{8,}"), "[REDACTED:credential]"),
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


# ---------------------------------------------------------------- 步骤执行


@dataclass(frozen=True)
class StepSpec:
    """一步的固定身份（argv 由 allowed_step_argv 派生，非用户输入）。"""

    step_id: str
    tool_identity: str
    timeout_seconds: float
    artifacts_dir: Path


def command_identity(step_id: str) -> list[str]:
    """报告用固定命令身份（仓内相对身份 + <python> 占位；绝无绝对本机路径）。"""
    if step_id == "monitor":
        return ["<python>", MONITOR_TOOL_NAME, "--execute", "--confirm", MONITOR_CONFIRM_PHRASE]
    if step_id == "insights":
        return ["<python>", INSIGHTS_TOOL_NAME, "--execute", "--confirm", INSIGHTS_CONFIRM_PHRASE]
    return ["<python>", HISTORY_TOOL_NAME]


def _artifact_entries(fs: Fs, directory: Path, names: list[str]) -> list[dict[str, object]]:
    """≤上限个文件 → {name, sha256, bytes}；超限文件记数不记哈希（bounded）。"""
    entries: list[dict[str, object]] = []
    for name in names[:MAX_ARTIFACTS_HASHED_PER_STAGE]:
        try:
            data = fs.read_bytes(directory / name)
        except OSError:
            continue
        if len(data) > MAX_ARTIFACT_BYTES:
            entries.append({"name": name, "sha256": None, "bytes": len(data),
                            "note": "oversize-not-hashed"})
            continue
        entries.append({"name": name, "sha256": hashlib.sha256(data).hexdigest(),
                        "bytes": len(data)})
    if len(names) > MAX_ARTIFACTS_HASHED_PER_STAGE:
        entries.append({"name": f"+{len(names) - MAX_ARTIFACTS_HASHED_PER_STAGE}-more",
                        "sha256": None, "bytes": None,
                        "note": "hash-limit-reached"})
    return entries


def _safe_listing(fs: Fs, directory: Path) -> list[str] | None:
    """目录列表；缺失/不可读 → None（如实入档，绝不猜）。"""
    try:
        return fs.list_dir(directory)
    except OSError:
        return None


def discover_stage_artifacts(fs: Fs, spec: StepSpec,
                             baseline: list[str] | None) -> list[dict[str, object]] | dict[str, str]:
    """步骤产物发现（只读）：monitor=前后差集 ∩ monitor-*.json；history 与
    insights=各自两固定名（同款固定名模式）。"""
    if spec.step_id == "monitor":
        after = _safe_listing(fs, spec.artifacts_dir)
        if after is None:
            return {"unavailable_reason": "artifact-dir-unreadable"}
        # 基线目录此前不存在 = 首次运行（差集即全部现存产物，不误报不可读）
        new_names = sorted(set(after) - (set(baseline) if baseline is not None else set()))
        stems = [n[:-len(".json")] for n in new_names
                 if n.startswith("monitor-") and n.endswith(".json")
                 and MONITOR_STEM_RE.match(n[:-len(".json")]) is not None]
        return _artifact_entries(fs, spec.artifacts_dir,
                                 [f"{stem}.json" for stem in sorted(stems)])
    fixed_names = HISTORY_OUTPUT_NAMES if spec.step_id == "history" else INSIGHTS_OUTPUT_NAMES
    after = _safe_listing(fs, spec.artifacts_dir)
    if after is None:
        return {"unavailable_reason": "artifact-dir-unreadable"}
    return _artifact_entries(fs, spec.artifacts_dir,
                             [name for name in fixed_names if name in after])


def run_step(runner: Runner, spec: StepSpec, argv: tuple[str, ...],
             clock: Clock) -> dict[str, object]:
    """执行单步（argv 由 run_execute 经固定白名单派生；异常类别化，子进程
    输出仅取 returncode，stdout/stderr 绝不保留）。"""
    started_utc, started_perf = clock.utc_now_iso(), clock.perf()
    try:
        result = runner.run(argv, timeout=spec.timeout_seconds)
    except Exception as exc:  # noqa: BLE001 —— 类别化兜底，文本不保留
        category, klass = categorize_runner_exception(exc)
        ended_utc, ended_perf = clock.utc_now_iso(), clock.perf()
        status = "timeout" if category == "command-timeout" else "error"
        return {
            "step_id": spec.step_id, "tool": spec.tool_identity,
            "command_identity": command_identity(spec.step_id),
            "status": status, "exit_code": None, "timed_out": status == "timeout",
            "failure_category": category, "error_class": klass,
            "failure_detail": "stage-killed-after-timeout" if status == "timeout" else "stage-exec-refused",
            "skipped_reason": None,
            "started_at_utc": started_utc, "ended_at_utc": ended_utc,
            "duration_seconds": round(ended_perf - started_perf, 3),
            "timeout_seconds": spec.timeout_seconds, "artifacts": None,
        }
    ended_utc, ended_perf = clock.utc_now_iso(), clock.perf()
    status = "ok" if result.returncode == 0 else "failed"
    return {
        "step_id": spec.step_id, "tool": spec.tool_identity,
        "command_identity": command_identity(spec.step_id),
        "status": status, "exit_code": result.returncode, "timed_out": False,
        "failure_category": None if status == "ok" else "stage-exit-nonzero",
        "error_class": None,
        "failure_detail": None if status == "ok" else f"exit-code-{result.returncode}",
        "skipped_reason": None,
        "started_at_utc": started_utc, "ended_at_utc": ended_utc,
        "duration_seconds": round(ended_perf - started_perf, 3),
        "timeout_seconds": spec.timeout_seconds, "artifacts": None,
    }


def skipped_step(spec: StepSpec, prev_step_id: str,
                 prev_stage: dict[str, object]) -> dict[str, object]:
    """本步因前置步骤非 ok 跳过（固定词汇原因 ``<prev>-status-<status>``；
    前置步骤事实由其自身条目如实保留）。"""
    return {
        "step_id": spec.step_id, "tool": spec.tool_identity,
        "command_identity": command_identity(spec.step_id),
        "status": "skipped", "exit_code": None, "timed_out": False,
        "failure_category": None, "error_class": None, "failure_detail": None,
        "skipped_reason": f"{prev_step_id}-status-{prev_stage['status']}",
        "started_at_utc": None, "ended_at_utc": None, "duration_seconds": None,
        "timeout_seconds": spec.timeout_seconds, "artifacts": None,
    }


# ---------------------------------------------------------------- 报告（原子写 + symlink/越界拒绝）


PIPELINE_BOUNDARIES: tuple[str, ...] = (
    "plan mode is completely inert: zero subprocess, zero network, zero production reads, zero scheduler mutation",
    "execute requires --execute plus the exact confirmation phrase; malformed or missing gates exit 2 before any runner is constructed",
    "only three fixed allowlisted command forms are ever invoked (monitor --execute with its own confirm phrase; history with defaults; insights --execute with its own confirm phrase and no --source, relying on the canonical history output default); no shell=True, no user command/URL/env expansion",
    "sequence is monitor then history then insights; history runs only after monitor exits 0; insights runs only after history status is ok; stage failures and skips are preserved with fixed-vocabulary reasons, never masked",
    "per-step bounded timeouts with conservative hard caps (monitor 60-540s, history 10-120s, insights 5-50s; caps sum to 710s below the PT12M task execution time limit)",
    "overlap protection: fail-closed exclusive lock; zero destructive stale-lock cleanup in this round",
    "report contains only safe facts: statuses, exit codes, stage timing, fixed command identities, sanitized error categories/classes, artifact names/hashes",
    "no env values, tokens, headers, raw child output, or production IDs are ever read into the report",
    "single pipeline success is not production readiness; scheduled registration is supervisor-only; this tool never claims production ready",
)


def artifact_dir_note(directory: Path) -> str:
    if directory == ARTIFACT_DIR:
        return "默认目录（gitignored，不入库）"
    return "自定义目录（操作者显式自选，位置与入库与否由操作者负责）"


def build_report(*, mode: str, started_utc: str, ended_utc: str,
                 config: dict[str, object], stages: dict[str, object],
                 lock: dict[str, object] | None) -> dict[str, object]:
    """schema 版本化报告（纯数据；写盘前再经 redact_secrets 终防线）。"""
    if mode == "execute":
        all_ok = all(stage["status"] == "ok" for stage in stages.values()) and stages
        overall = "ok" if all_ok else "failed"
    else:
        overall = "planned"
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "milestone": MILESTONE,
        "mode": mode,
        "started_at_utc": started_utc,
        "ended_at_utc": ended_utc,
        "config": config,
        "stages": stages,
        "overall_status": overall,
        "lock": lock,
        "boundaries": list(PIPELINE_BOUNDARIES),
    }


def build_config(*, monitor_timeout: float, history_timeout: float,
                 insights_timeout: float) -> dict[str, object]:
    return {
        "python_identity": "<sys.executable>",
        "monitor_tool": MONITOR_TOOL_NAME,
        "history_tool": HISTORY_TOOL_NAME,
        "insights_tool": INSIGHTS_TOOL_NAME,
        "sequence": ["monitor", "history", "insights"],
        "monitor_timeout_seconds": monitor_timeout,
        "history_timeout_seconds": history_timeout,
        "insights_timeout_seconds": insights_timeout,
        "monitor_confirm_phrase": MONITOR_CONFIRM_PHRASE,
        "insights_confirm_phrase": INSIGHTS_CONFIRM_PHRASE,
        "lock": LOCK_NAME,
    }


def render_markdown(report: dict[str, object]) -> str:
    """Markdown 摘要（固定词汇表 + 渲染后统一脱敏）。"""
    stages = report["stages"]
    assert isinstance(stages, dict)
    lines: list[str] = [
        f"# {report['milestone']} 监控管道报告（mode={report['mode']}）",
        "",
        f"- 工具：`{report['tool']}`（schema_version={report['schema_version']}）",
        f"- 开始（UTC）：{report['started_at_utc']}；结束（UTC）：{report['ended_at_utc']}",
        f"- overall_status=**{report['overall_status']}**（序列 monitor → history → insights，history 仅在 monitor exit 0 后运行，insights 仅在 history status=ok 后运行）",
        "",
        "| 步骤 | 工具 | 状态 | 退出码 | 时长(s) | 原因/类别 |",
        "|---|---|---|---|---:|---|",
    ]
    for step_id in ("monitor", "history", "insights"):
        stage = stages[step_id]
        assert isinstance(stage, dict)
        reason = (stage.get("skipped_reason") or stage.get("failure_category")
                  or stage.get("failure_detail") or "-")
        lines.append(f"| {step_id} | {stage['tool']} | {stage['status']} "
                     f"| {stage['exit_code'] if stage['exit_code'] is not None else '-'} "
                     f"| {stage['duration_seconds'] if stage['duration_seconds'] is not None else '-'} "
                     f"| {reason} |")
    lock = report["lock"]
    if isinstance(lock, dict):
        lines += ["", f"- 锁：acquired={lock.get('acquired')} released={lock.get('released')}（{LOCK_NAME}）"]
    lines += ["", "边界："]
    lines += [f"- {item}" for item in report["boundaries"]]  # type: ignore[arg-type]
    return "\n".join(lines) + "\n"


def write_reports_atomic(fs: Fs, report: dict[str, object], directory: Path,
                         stem: str) -> tuple[Path, Path]:
    """JSON + Markdown 双写：内容先经 redact_secrets 终防线，再同目录 tmp +
    os.replace 原子落盘；symlink/越界路径一律 ReportPathError（零写入）。"""
    if "/" in stem or "\\" in stem or ".." in stem or not _STEM_RE.match(stem):
        raise ReportPathError("bad-stem")
    json_path = directory / f"{stem}.json"
    md_path = directory / f"{stem}.md"
    # 先于 mkdir 拒绝目录自身/现存祖先 symlink（mkdir(parents=True) 会穿越
    # symlink 建目录——拒绝必须发生在任何创建之前）；mkdir 后复查（TOCTOU）。
    reject_symlinked_path(fs, directory)
    fs.mkdirs(directory)
    reject_symlinked_path(fs, json_path, md_path, directory)
    json_text = redact_secrets(json.dumps(report, ensure_ascii=False, indent=2))
    md_text = redact_secrets(render_markdown(report))
    fs.write_atomic(json_path, json_text)
    fs.write_atomic(md_path, md_text)
    return json_path, md_path


# ---------------------------------------------------------------- execute 主管道


def _planned_stages(config: dict[str, object]) -> dict[str, object]:
    stages: dict[str, object] = {}
    timeout_keys = {"monitor": "monitor_timeout_seconds",
                    "history": "history_timeout_seconds",
                    "insights": "insights_timeout_seconds"}
    tool_names = {"monitor": MONITOR_TOOL_NAME, "history": HISTORY_TOOL_NAME,
                  "insights": INSIGHTS_TOOL_NAME}
    for step_id in ("monitor", "history", "insights"):
        stages[step_id] = {
            "step_id": step_id,
            "tool": tool_names[step_id],
            "command_identity": command_identity(step_id),
            "status": "planned", "exit_code": None, "timed_out": False,
            "failure_category": None, "error_class": None, "failure_detail": None,
            "skipped_reason": None,
            "started_at_utc": None, "ended_at_utc": None, "duration_seconds": None,
            "timeout_seconds": config[timeout_keys[step_id]], "artifacts": None,
        }
    return stages


def run_execute(*, runner: StepRunner, clock: Clock, fs: Fs, artifact_dir: Path,
                monitor_timeout: float, history_timeout: float,
                insights_timeout: float, python_exe: str,
                log: SafeLog) -> tuple[dict[str, object], int]:
    """execute 主管道：锁 → monitor → (exit 0 才) history → (ok 才)
    insights → 报告 → 释锁。

    退出码契约见模块头；任何步骤失败/跳过如实入档（绝不遮蔽前置步骤
    事实），报告写入失败（证据不可失）与锁路径违规一律 EXIT_USAGE。"""
    started_utc = clock.utc_now_iso()
    monitor_spec = StepSpec("monitor", MONITOR_TOOL_NAME, monitor_timeout,
                            MONITOR_ARTIFACT_DIR)
    history_spec = StepSpec("history", HISTORY_TOOL_NAME, history_timeout,
                            HISTORY_OUTPUT_DIR)
    insights_spec = StepSpec("insights", INSIGHTS_TOOL_NAME, insights_timeout,
                             INSIGHTS_OUTPUT_DIR)
    config = build_config(monitor_timeout=monitor_timeout,
                          history_timeout=history_timeout,
                          insights_timeout=insights_timeout)
    # 1) 重叠锁（fail-closed；目录 symlink 先拒，mkdir 先于拒绝会穿越 symlink）
    lock_path = artifact_dir / LOCK_NAME
    try:
        reject_symlinked_path(fs, artifact_dir)
        fs.mkdirs(artifact_dir)
        reject_symlinked_path(fs, artifact_dir, lock_path)
    except (ReportPathError, OSError) as cause:
        log.say(f"拒绝: 报告/锁目录路径非法: {type(cause).__name__}")
        return {}, EXIT_USAGE
    lock_body = json.dumps({
        "schema_version": LOCK_SCHEMA_VERSION, "tool": TOOL_NAME,
        "created_at_utc": started_utc, "pid": os.getpid(),
    }, ensure_ascii=False)
    if not fs.lock_acquire(lock_path, lock_body):
        log.say(f"拒绝: 重叠保护——锁 {LOCK_NAME} 已存在（零步骤执行；"
                "本轮零 stale-lock 清理，陈旧锁由操作者人工处置）")
        return {}, EXIT_USAGE
    lock: dict[str, object] = {"acquired": True, "path": LOCK_NAME,
                               "created_at_utc": started_utc, "released": None}
    log.say(f"锁获取: {LOCK_NAME}（O_CREAT|O_EXCL 原子创建）")
    exit_code = EXIT_STAGE_FAILED
    try:
        # 2) monitor 步（产物差集基线在执行前取）
        argvs = allowed_step_argv(python_exe)
        baseline = _safe_listing(fs, monitor_spec.artifacts_dir)
        log.say(f"步骤 monitor: 执行（timeout {monitor_timeout:g}s，固定白名单形态）")
        monitor = run_step(runner, monitor_spec, argvs["monitor"], clock)
        monitor["artifacts"] = discover_stage_artifacts(fs, monitor_spec, baseline)
        log.say(f"步骤 monitor: status={monitor['status']} exit_code={monitor['exit_code']}")
        # 3) history 步——仅 monitor exit 0（ok|warn）后运行
        if monitor["status"] == "ok":
            log.say(f"步骤 history: 执行（timeout {history_timeout:g}s，固定白名单形态）")
            history = run_step(runner, history_spec, argvs["history"], clock)
            history["artifacts"] = discover_stage_artifacts(fs, history_spec, None)
            log.say(f"步骤 history: status={history['status']} exit_code={history['exit_code']}")
        else:
            history = skipped_step(history_spec, "monitor", monitor)
            log.say(f"步骤 history: skipped（monitor status={monitor['status']}，"
                    f"exit_code={monitor['exit_code']}——失败如实保留不遮蔽）")
        # 4) insights 步——仅 history status=ok 后运行（history.jsonl 完整
        #    落盘才可洞察）；输入恒为 insights 默认源 = history canonical
        #    输出目录（固定命令形态不带 --source，无用户可注入面）
        if monitor["status"] == "ok" and history["status"] == "ok":
            log.say(f"步骤 insights: 执行（timeout {insights_timeout:g}s，固定白名单形态，"
                    "默认源=history canonical 输出）")
            insights = run_step(runner, insights_spec, argvs["insights"], clock)
            insights["artifacts"] = discover_stage_artifacts(fs, insights_spec, None)
            log.say(f"步骤 insights: status={insights['status']} exit_code={insights['exit_code']}")
        else:
            insights = skipped_step(insights_spec, "history", history)
            log.say(f"步骤 insights: skipped（history status={history['status']}——"
                    "前置事实如实保留不遮蔽）")
        stages = {"monitor": monitor, "history": history, "insights": insights}
        all_ok = all(stage["status"] == "ok" for stage in stages.values())
        exit_code = EXIT_OK if all_ok else EXIT_STAGE_FAILED
        # 5) 释放锁（记录结果；失败为可见失败 EXIT_STAGE_FAILED，不遮蔽步骤事实）
        try:
            fs.lock_release(lock_path)
            lock["released"] = True
        except OSError as cause:
            lock["released"] = False
            lock["release_failure"] = type(cause).__name__
            log.say(f"锁释放失败（可见失败）: {type(cause).__name__}")
            exit_code = EXIT_STAGE_FAILED
        # 6) 证据报告（原子写；写入失败 = 证据不可失 → EXIT_USAGE）
        report = build_report(mode="execute", started_utc=started_utc,
                              ended_utc=clock.utc_now_iso(), config=config,
                              stages=stages, lock=lock)
        stamp = clock.stamp()
        try:
            json_path, md_path = write_reports_atomic(fs, report, artifact_dir,
                                                      f"pipeline-{stamp}")
            log.say(f"报告: {json_path.name} / {md_path.name}（{artifact_dir_note(artifact_dir)}）")
        except (ReportPathError, OSError) as cause:
            log.say(f"报告写入失败（证据不可失——按拒绝处理）: {type(cause).__name__}")
            return {}, EXIT_USAGE
        log.say(f"=== 结果: overall_status={report['overall_status']} "
                f"monitor={stages['monitor']['status']} history={stages['history']['status']} "
                f"insights={stages['insights']['status']} ===")
        return report, exit_code
    finally:
        # 兜底释锁：正常路径已在上方释放；此处仅覆盖报告构建期异常逃逸的场景
        if lock["released"] is None:
            try:
                fs.lock_release(lock_path)
            except OSError:
                pass


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitoring_pipeline.py",
        description="M14-14/M14-21 监控管道 readiness（默认 plan 零执行；execute 需旗标+精确确认短语，"
                    "monitor→history→insights 单次组合，固定命令白名单 + 重叠锁 + 原子证据报告）",
    )
    parser.add_argument("--execute", action="store_true",
                        help="真实单次组合执行（默认 plan：零 subprocess/零网络/零生产读取）")
    parser.add_argument("--confirm", default="",
                        help=f'execute 必配 --confirm "{CONFIRM_PHRASE}"（精确匹配）')
    parser.add_argument("--monitor-timeout-seconds", type=float, default=MONITOR_TIMEOUT_DEFAULT,
                        help=f"monitor 步超时 {MONITOR_TIMEOUT_MIN:g}-{MONITOR_TIMEOUT_MAX:g}s（默认 {MONITOR_TIMEOUT_DEFAULT:g}）")
    parser.add_argument("--history-timeout-seconds", type=float, default=HISTORY_TIMEOUT_DEFAULT,
                        help=f"history 步超时 {HISTORY_TIMEOUT_MIN:g}-{HISTORY_TIMEOUT_MAX:g}s（默认 {HISTORY_TIMEOUT_DEFAULT:g}）")
    parser.add_argument("--insights-timeout-seconds", type=float, default=INSIGHTS_TIMEOUT_DEFAULT,
                        help=f"insights 步超时 {INSIGHTS_TIMEOUT_MIN:g}-{INSIGHTS_TIMEOUT_MAX:g}s（默认 {INSIGHTS_TIMEOUT_DEFAULT:g}；"
                             f"三步硬顶之和 {MONITOR_TIMEOUT_MAX:g}+{HISTORY_TIMEOUT_MAX:g}+{INSIGHTS_TIMEOUT_MAX:g}"
                             f"={MONITOR_TIMEOUT_MAX + HISTORY_TIMEOUT_MAX + INSIGHTS_TIMEOUT_MAX:g}s < PT12M=720s）")
    parser.add_argument("--artifact-dir", type=Path, default=ARTIFACT_DIR,
                        help="管道报告/锁目录（默认 .verify/artifacts/m14-14-monitoring-pipeline，gitignored；"
                             "自定义路径为操作者显式自选，其位置与入库与否由操作者负责）")
    return parser


def main(argv: list[str] | None = None, *, runner: StepRunner | None = None,
         clock: Clock | None = None, fs: Fs | None = None,
         python_exe: str | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = SafeLog()
    # 1) 数值面校验（plan 与 execute 都校验；超顶/非有限一律先拒，零报告写入）
    problems = validate_timeouts(monitor_timeout=args.monitor_timeout_seconds,
                                  history_timeout=args.history_timeout_seconds,
                                  insights_timeout=args.insights_timeout_seconds)
    if problems:
        for problem in problems:
            log.say(f"拒绝: {problem}")
        return EXIT_USAGE
    resolved_python = python_exe if python_exe is not None else sys.executable
    config = build_config(monitor_timeout=args.monitor_timeout_seconds,
                          history_timeout=args.history_timeout_seconds,
                          insights_timeout=args.insights_timeout_seconds)
    # 2) plan 模式（默认）：零 subprocess、零网络、零生产读取、零调度器改动
    if not args.execute:
        log.say("=== M14-14/M14-21 监控管道 PLAN（零 subprocess / 零网络 / 零生产读取 / 零调度器改动） ===")
        timeouts = {"monitor": args.monitor_timeout_seconds,
                    "history": args.history_timeout_seconds,
                    "insights": args.insights_timeout_seconds}
        for step_id in ("monitor", "history", "insights"):
            identity = " ".join(command_identity(step_id))
            log.say(f"步骤 {step_id}: {identity}（timeout {timeouts[step_id]:g}s，固定白名单形态）")
        log.say("序列: monitor → history → insights（history 仅在 monitor exit 0 后运行；"
                "insights 仅在 history status=ok 后运行，默认源=history canonical 输出；失败如实保留）")
        log.say(f"重叠锁: {LOCK_NAME}（fail-closed；本轮零 stale-lock 清理）")
        log.say(f'执行需: --execute --confirm "{CONFIRM_PHRASE}"')
        report = build_report(mode="plan", started_utc="", ended_utc="",
                              config=config, stages=_planned_stages(config), lock=None)
        plan_clock = clock if clock is not None else RealClock()
        plan_fs = fs if fs is not None else RealFs()
        report["started_at_utc"] = plan_clock.utc_now_iso()
        report["ended_at_utc"] = plan_clock.utc_now_iso()
        try:
            json_path, md_path = write_reports_atomic(plan_fs, report, args.artifact_dir,
                                                      f"plan-{plan_clock.stamp()}")
            log.say(f"plan 报告: {json_path.name} / {md_path.name}（{artifact_dir_note(args.artifact_dir)}）")
        except (ReportPathError, OSError) as cause:
            log.say(f"plan 报告路径非法/写入失败: {type(cause).__name__}")
            return EXIT_USAGE
        return EXIT_OK
    # 3) execute 门禁：精确确认短语（缺一即拒，零 Runner 构造/调用）
    if args.confirm != CONFIRM_PHRASE:
        log.say(f'拒绝: --execute 必配 --confirm "{CONFIRM_PHRASE}"（精确匹配，当前不匹配）——零执行')
        return EXIT_USAGE
    # 4) execute（真实执行仅由 supervisor 在获准窗口运行；Runner 仅在此后构造）
    execute_runner = runner if runner is not None else StepRunner(RealRunner(), resolved_python)
    execute_clock = clock if clock is not None else RealClock()
    execute_fs = fs if fs is not None else RealFs()
    log.say("=== M14-14/M14-21 监控管道 EXECUTE: monitor → history → insights（固定白名单形态） ===")
    _, exit_code = run_execute(runner=execute_runner, clock=execute_clock,
                               fs=execute_fs, artifact_dir=args.artifact_dir,
                               monitor_timeout=args.monitor_timeout_seconds,
                               history_timeout=args.history_timeout_seconds,
                               insights_timeout=args.insights_timeout_seconds,
                               python_exe=resolved_python, log=log)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
