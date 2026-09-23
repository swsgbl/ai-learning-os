#!/usr/bin/env python
"""M14-14/M14-21/M14-110 持续/定时监控采集管道 readiness：monitor(M14-12)
→ history(M14-13) → insights(M14-15) → calibration(M14-109) 单次组合。

设计（与 tools/ops/production_monitor.py / monitoring_history.py 同款纪律：
单文件、纯标准库、零第三方依赖；子进程经 Runner 注入 + **固定命令白名单门**、
UTC 时间经 Clock 注入、文件面经 Fs 注入——开发回合零真实 Docker/零生产
HTTP/零计划任务注册，全部行为用 fake 注入测试锁定；真实执行仅由 supervisor
在获准窗口运行）：

- 双模式：默认 **plan（dry-run）**——零 subprocess、零网络、零生产读取、
  零调度器改动，仅打印四步计划并落 plan 报告（Runner **零构造**，构造计数
  测试锁定）；**execute** 需同时满足「旗标 + 精确确认短语」（``--execute``
  + ``--confirm "EXECUTE READ-ONLY MONITORING PIPELINE"`` 一字不差），缺一
  或短语不匹配即 EXIT 2 且**零 Runner 构造/调用**（fail-closed）。数值面
  （四步超时）硬性拒绝：非有限浮点（nan/inf/-inf）与超硬顶一律 EXIT 2
  （plan 同样校验，先于任何报告写入）。
- **固定命令白名单门（结构性）**：execute 仅允许四个**精确固定形态**——
  ``<python> tools/ops/production_monitor.py --execute --confirm "EXECUTE
  READ-ONLY PRODUCTION MONITORING"``（monitor 自身的门禁短语，与
  production_monitor.CONFIRM_PHRASE 逐字一致，回归测试锁定）、
  ``<python> tools/ops/monitoring_history.py``（全部默认参数）、
  ``<python> tools/ops/monitoring_insights.py --execute --confirm "EXECUTE
  READ-ONLY MONITORING INSIGHTS"``（insights 自身门禁短语，与
  monitoring_insights.CONFIRM_PHRASE 逐字一致；**恒不带 --source**——输入
  恒为 insights 默认源 = history canonical 输出目录，M14-21 起两处常量
  三方 resolve 全等，单一事实源）与 ``<python>
  tools/ops/monitoring_threshold_calibration.py --format json``（M14-110
  起第四步——**仅形态旗标 ``--format json``，恒不带 ``--history``/
  ``--samples``/阈值参数**：输入面全部经校准工具**既有默认值**生效
  （canonical history.jsonl + 默认窗口 200 + 既有阈值），管道 CLI 不暴露
  任何可注入校准 argv 的参数）。任何其它 argv 在执行之前拒绝
  （CommandNotAllowedError，内层 runner 零调用）；**无 shell=True、无用户
  可注入命令/URL/env 展开**。子进程输出纪律分两型：monitor/history/
  insights 三步仅取 returncode，stdout/stderr **绝不持久化/回显**；
  calibration 步为**唯一显式例外**——stdout 是被捕获的产物本体（见下），
  绝不回显到控制台。
- 序列语义：恒为 monitor → history → insights → calibration；**history 仅在
  monitor exit 0 后运行**（monitor ok|warn 才有完整可索引样本）；**insights
  仅在 history status=ok 后运行**（history.jsonl 完整落盘才可洞察）；
  **calibration 仅在 insights status=ok 后运行**（M14-110——完整 history
  已被洞察过一轮才标定）；任一前置失败（非零退出/超时/执行错误/skipped）
  → 后续步骤状态 skipped + 固定词汇原因（``<prev>-status-<status>``），
  **前置步骤的退出码/类别如实保留绝不遮蔽**；insights 失败不改变
  monitor/history 事实，calibration 失败不改变前三步事实。逐步有界超时
  + 保守硬顶：monitor 60–540s（默认 480s，覆盖 monitor 内部最坏预算
  ~445s + 启动余量）、history 10–120s（默认 90s——M14-79 由 45s 上调：
  2026-09-21 生产三次实测 45.2–47.0s 刚过界即杀，90s ≈ 1.9× 最坏观测（46.955s）且
  正常轮 0.3–7.2s；超时事实照常入档绝不隐藏）、insights 5–50s（默认
  15s——纯本地只读工件处理，秒级完成即兜底杀停）、calibration 1–5s
  （默认 5s，M14-110——与 insights 同型纯本地只读 history.jsonl 处理，
  秒级完成）——默认总和 480+90+15+5=590s，四步硬顶之和
  540+120+50+5=715s < 计划任务执行时限 PT12M=720s < PT15M 间隔
  （monitor/history/insights 三步既有硬顶 540+120+50=710s 由既有测试
  pin 不变；calibration 硬顶 5s 使执行时限恒留 ≥5s 给管道自身开销——
  启动、锁、证据报告与调度器余量）。
- **calibration 步产物纪律（M14-110，管道独占持久化）**：校准工具自身
  零文件写入（stdout-only 契约不变，绝不改动/删除 history.jsonl 或任何
  输入工件）——管道捕获其 stdout 为**内存产物**，json 校验（顶层对象且
  ``schema_version``/``tool`` 与受支持的校准产物契约**精确匹配**——仅键
  在场不够，版本不兼容/身份不符同样拒绝；malformed 或不匹配 = 可见
  calibration 失败 ``calibration-output-not-json``，绝不静默接受）后经
  redact_secrets 终防线 + symlink 拒绝 + 原子写（tmp+fsync+os.replace）
  持久化为管道工件目录内**固定名** ``calibration.json``（校准 json 模式
  恒 ASCII-safe（ensure_ascii 转义）——任意子进程 stdout 编码（含 Windows
  ACP=cp936 调度链路）下捕获字节零损坏、零墙钟逐字节确定——同输入同
  字节，固定名覆盖无歧义）；写入失败 = 可见 calibration 失败
  （``calibration-artifact-write-error``），管道证据报告照常落盘。
  失败/跳过轮绝不写、绝不引用旧 calibration.json（artifacts=None，与
  history/insights 固定名同轮新鲜度语义同款——旧文件留存但零引用）。
- 重叠保护（fail-closed）：gitignored 工件目录内独占锁 ``pipeline.lock``
  （O_CREAT|O_EXCL 原子创建；锁/目录 symlink 一律拒绝）。锁已存在 → 可见
  拒绝 EXIT 2 且零步骤执行；**本轮零破坏性 stale-lock 清理**（陈旧锁由
  操作者人工处置，工具绝不代删）。锁体仅安全事实（schema/tool/UTC/pid）。
- 证据报告（schema 版本化 JSON + Markdown 原子写入，同目录 tmp + fsync +
  os.replace）：**仅安全事实**——模式、逐步状态（ok/failed/timeout/error/
  skipped/planned）与退出码、逐步 UTC 起止与时长、固定命令身份（仓内相对
  身份 + ``<python>`` 占位，绝无绝对本机路径）、有界脱敏错误类别/异常类名
  （固定词汇 detail）、步骤产物名 + SHA-256 + 字节数（若可得；monitor 产物
  按「步骤前后目录差集」发现，仅 hash ≤8 个 monitor-*.json；history 与
  insights 产物为各两个固定名，M14-101 起经**同轮新鲜度判定**——步骤
  执行前对每固定名取 (size, mtime_ns) 指纹基线，步骤后指纹未变的预存
  文件记 ``stale-preexisting-not-cited``（sha=None，绝不引用为该轮产物），
  仅新建/指纹变化者才带 SHA-256 引用——**绝无「超时报告引用旧固定名
  产物」的歧义**）、边界注记。**绝无 env 值/
  token/header/子进程原文日志/生产 ID**（写前 redact_secrets 终防线 +
  生成面固定词汇双保险）。报告写入失败 = 证据不可失 → EXIT 2。
- 退出码：0 plan 成功 / execute 四步全 ok；1 execute 已执行但有步骤失败
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
CALIBRATION_SCRIPT = OPS_DIR / "monitoring_threshold_calibration.py"
#: 三步产物目录（与三工具自身默认一致——契约测试锁定 resolve 全等；本工具
#: 只读发现，绝不写入）
MONITOR_ARTIFACT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-12-production-monitoring"
HISTORY_OUTPUT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-13-monitoring-history"
INSIGHTS_OUTPUT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-15-monitoring-insights"
#: 本工具自身报告/锁目录（gitignored）；M14-110 起 calibration 步捕获产物
#: （固定名 calibration.json）也由本工具持久化在此目录（校准工具自身零
#: 文件写入——stdout-only 契约不变，持久化责任独占归管道）
ARTIFACT_DIR = REPO_ROOT / ".verify" / "artifacts" / "m14-14-monitoring-pipeline"

TAG = "[pipeline]"
#: 报告 schema 恒为 1（M14-110 加法扩展惯例，与 M14-21 加第三步时的
#: add-stage-keep-version 先例一致）：stages/config 为**加键**扩展——
#: 既有键语义零变化，既有消费者（pipeline_incident_review 按键取值 +
#: stages 键集子集校验）对旧两步/三步与新四步报告同面兼容；升级版本号
#: 反而会造成新旧报告的硬分叉，无消费者需求驱动。
REPORT_SCHEMA_VERSION = 1
MILESTONE = "M14-14"
TOOL_NAME = "tools/ops/monitoring_pipeline.py"
MONITOR_TOOL_NAME = "tools/ops/production_monitor.py"
HISTORY_TOOL_NAME = "tools/ops/monitoring_history.py"
INSIGHTS_TOOL_NAME = "tools/ops/monitoring_insights.py"
CALIBRATION_TOOL_NAME = "tools/ops/monitoring_threshold_calibration.py"

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
#: M14-110 calibration 步产物固定名（管道工件目录内；由管道从捕获的
#: stdout 原子写入——校准工具自身零文件写入）
CALIBRATION_OUTPUT_NAME = "calibration.json"

#: 步骤超时（秒）——有界 + 保守硬顶。预算链（与 monitoring_pipeline_task
#: 的 ExecutionTimeLimit=PT12M=720s 交叉 pin，测试锁定）：monitor 内部最坏
#: ~445s（compose ps 60 + 6×inspect 30 + 6×logs 30 + 5×HTTP 5）+ 启动余量
#: → 默认 480s；history 默认 90s（M14-79 由 45s 上调：2026-09-21 生产实测
#: 45.206s/45.522s/46.955s 三次刚过 45s 即被杀——工件目录已 830+ 份时冷
#: 缓存下重校验偶超 45s；90s ≈ 1.9× 最坏观测（46.955s），仍留约 2 分钟余量；正常完成轮
#: 实测 0.3–7.2s）；insights 纯本地只读工件处理秒级完成 → 默认 15s；
#: calibration（M14-110）与 insights 同型纯本地只读 history.jsonl 处理
#: （行级校验 + 统计，秒级完成）→ 默认 5s、硬顶 5s（supervisor 边界：
#: 不把 PT12M 用满——三步既有硬顶 540+120+50=710s 由既有测试 pin 不变，
#: calibration 硬顶 5s 使四步硬顶之和 540+120+50+5=715s，对 PT12M=720s
#: 执行时限**恒留 ≥5s** 给管道自身开销（启动、锁获取/释放、证据报告
#: 原子写与调度器开销——调度器绝不先于内部超时+收尾杀整任务，避免击杀
#: 留 stale lock/半写报告）；默认总和 480+90+15+5=590s。超时事实照常入档
#: （step status=timeout），绝不因上调而隐藏或改记成功。
MONITOR_TIMEOUT_DEFAULT = 480.0
MONITOR_TIMEOUT_MIN = 60.0
MONITOR_TIMEOUT_MAX = 540.0
HISTORY_TIMEOUT_DEFAULT = 90.0
HISTORY_TIMEOUT_MIN = 10.0
HISTORY_TIMEOUT_MAX = 120.0
INSIGHTS_TIMEOUT_DEFAULT = 15.0
INSIGHTS_TIMEOUT_MIN = 5.0
INSIGHTS_TIMEOUT_MAX = 50.0
CALIBRATION_TIMEOUT_DEFAULT = 5.0
CALIBRATION_TIMEOUT_MIN = 1.0
CALIBRATION_TIMEOUT_MAX = 5.0

#: 产物发现边界：每步至多 hash 的文件数 + 单文件字节数上限（超限记数不记哈希）
MAX_ARTIFACTS_HASHED_PER_STAGE = 8
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024

#: M14-101 固定名产物同轮新鲜度判定词汇（报告 note 固定词汇；绝不携带路径
#: /内容文本）。基线缺失 = 同轮产出不可证——预存固定名文件**绝不**引用为
#: 本轮产物（超时/失败轮尤其如此：旧文件指纹未变即不是本轮写的）。
NOTE_STALE_PREEXISTING = "stale-preexisting-not-cited"
NOTE_CREATED_THIS_RUN = "created-this-run"
NOTE_REMOVED_THIS_RUN = "removed-this-run"
NOTE_SYMLINK_NOT_HASHED = "symlink-not-hashed"
NOTE_BASELINE_UNREADABLE = "baseline-unreadable-not-cited"
NOTE_UNREADABLE = "unreadable-not-hashed"
#: M14-110 calibration 产物 note（写入者 = 管道本身而非子进程：同轮产出
#: 由「内存捕获 → 校验 → 原子写」路径结构性可证，与发现面指纹判定区分）
NOTE_PERSISTED_FROM_STAGE = "persisted-from-stage-stdout"
#: M14-110 calibration 步失败类别（固定词汇；exit_code=0 但 stdout 不满足
#: JSON 产物契约 = 可见失败，绝不静默接受；写入面拒绝/失败同可见失败）
CALIBRATION_OUTPUT_NOT_JSON = "calibration-output-not-json"
CALIBRATION_ARTIFACT_WRITE_ERROR = "calibration-artifact-write-error"
#: calibration 产物契约常量（管道侧独立定义、经回归测试与
#: monitoring_threshold_calibration.CALIBRATION_SCHEMA_VERSION / TOOL_NAME
#: 交叉 pin——管道不导入兄弟工具，单一事实源由测试锁定）
CALIBRATION_SCHEMA_VERSION = 1

#: monitor 产物 stem 白名单（与 monitoring_history.ARTIFACT_STEM_RE 同款）
MONITOR_STEM_RE = re.compile(r"^monitor-[0-9]{8}-[0-9]{6}$")

_STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# ---------------------------------------------------------------- 数值面校验（纯）


def validate_timeouts(*, monitor_timeout: float, history_timeout: float,
                      insights_timeout: float,
                      calibration_timeout: float) -> list[str]:
    """纯函数：返回违规清单（空 = 放行）。非有限浮点显式拒绝；plan 同样校验。"""
    problems: list[str] = []
    for name, value in (("monitor-timeout-seconds", monitor_timeout),
                        ("history-timeout-seconds", history_timeout),
                        ("insights-timeout-seconds", insights_timeout),
                        ("calibration-timeout-seconds", calibration_timeout)):
        if not math.isfinite(value):
            problems.append(f"{name} 必须为有限数值（nan/inf 一律拒绝）")
    if math.isfinite(monitor_timeout) and not MONITOR_TIMEOUT_MIN <= monitor_timeout <= MONITOR_TIMEOUT_MAX:
        problems.append(f"monitor-timeout-seconds 必须在 {MONITOR_TIMEOUT_MIN:g}-{MONITOR_TIMEOUT_MAX:g}s（收到 {monitor_timeout}）")
    if math.isfinite(history_timeout) and not HISTORY_TIMEOUT_MIN <= history_timeout <= HISTORY_TIMEOUT_MAX:
        problems.append(f"history-timeout-seconds 必须在 {HISTORY_TIMEOUT_MIN:g}-{HISTORY_TIMEOUT_MAX:g}s（收到 {history_timeout}）")
    if math.isfinite(insights_timeout) and not INSIGHTS_TIMEOUT_MIN <= insights_timeout <= INSIGHTS_TIMEOUT_MAX:
        problems.append(f"insights-timeout-seconds 必须在 {INSIGHTS_TIMEOUT_MIN:g}-{INSIGHTS_TIMEOUT_MAX:g}s（收到 {insights_timeout}）")
    if math.isfinite(calibration_timeout) and not CALIBRATION_TIMEOUT_MIN <= calibration_timeout <= CALIBRATION_TIMEOUT_MAX:
        problems.append(f"calibration-timeout-seconds 必须在 {CALIBRATION_TIMEOUT_MIN:g}-{CALIBRATION_TIMEOUT_MAX:g}s（收到 {calibration_timeout}）")
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
                      insights_script: Path = INSIGHTS_SCRIPT,
                      calibration_script: Path = CALIBRATION_SCRIPT
                      ) -> dict[str, tuple[str, ...]]:
    """四个**精确固定形态**（唯一可执行面；python 路径由构造侧固定，非用户
    输入。insights 恒不带 --source——输入恒为其默认源 = history canonical
    输出目录，M14-21 起两处常量三方 resolve 全等，单一事实源；calibration
    仅 ``--format json``（工具既有输出形态选项——JSON 产物契约所必需），
    恒不带 --history/--samples/阈值参数——输入面全部经校准工具既有默认值
    生效，M14-110 起与 monitoring_threshold_calibration.DEFAULT_HISTORY_PATH
    resolve 全等，单一事实源）。"""
    return {
        "monitor": (python_exe, str(monitor_script),
                    "--execute", "--confirm", MONITOR_CONFIRM_PHRASE,
                    "--voice-health-source", "sidecar"),
        "history": (python_exe, str(history_script)),
        "insights": (python_exe, str(insights_script),
                     "--execute", "--confirm", INSIGHTS_CONFIRM_PHRASE),
        "calibration": (python_exe, str(calibration_script), "--format", "json"),
    }


def is_allowed_step_command(argv: tuple[str, ...] | list[str], python_exe: str, *,
                            monitor_script: Path = MONITOR_SCRIPT,
                            history_script: Path = HISTORY_SCRIPT,
                            insights_script: Path = INSIGHTS_SCRIPT,
                            calibration_script: Path = CALIBRATION_SCRIPT
                            ) -> bool:
    """结构性白名单：argv 与四个固定形态之一**逐 token 全等**才放行。"""
    tokens = tuple(str(item) for item in argv)
    return tokens in allowed_step_argv(
        python_exe, monitor_script=monitor_script,
        history_script=history_script, insights_script=insights_script,
        calibration_script=calibration_script).values()


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

    def stat_fingerprint(self, path: Path) -> tuple[int, int] | None: ...

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

    def stat_fingerprint(self, path: Path) -> tuple[int, int] | None:
        """M14-101 同轮新鲜度指纹 (size, mtime_ns)；缺失/不可 stat → None
        （如实入档，绝不猜）。写侧 tmp+fsync+os.replace 原子替换必然推进
        mtime——指纹未变即未在本轮被重写（history 输出零墙钟逐字节可复现，
        内容哈希无法区分「重写了相同字节」与「没写」，mtime 指纹可以）。"""
        try:
            info = path.stat()
        except OSError:
            return None
        return (info.st_size, info.st_mtime_ns)

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
    if step_id == "calibration":
        return ["<python>", CALIBRATION_TOOL_NAME, "--format", "json"]
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


def fixed_name_fingerprints(fs: Fs, directory: Path,
                             names: tuple[str, ...]
                             ) -> dict[str, tuple[int, int] | None] | None:
    """M14-101 步骤执行前基线：每固定名 → (size, mtime_ns) 指纹或 None
    （基线时不存在）。目录不可读 → 整体 None（同轮新鲜度不可证——后续
    对任何现存固定名零引用，fail-honest）。"""
    fingerprints: dict[str, tuple[int, int] | None] = {}
    for name in names:
        try:
            fingerprints[name] = fs.stat_fingerprint(directory / name)
        except OSError:
            return None
    return fingerprints


def _discover_fixed_name_artifacts(
        fs: Fs, spec: StepSpec,
        baseline: dict[str, tuple[int, int] | None] | None
        ) -> list[dict[str, object]] | dict[str, str]:
    """固定名产物（history/insights）同轮新鲜度发现（M14-101）：

    仅当固定名能证明是**本步同一轮执行写入**的（基线后新建，或
    (size, mtime_ns) 指纹相对基线变化——原子替换必然推进 mtime）才带
    SHA-256 引用；指纹未变的预存文件记 ``stale-preexisting-not-cited``
    （sha=None）——超时/失败轮引用旧固定名产物的歧义从此不可能。"""
    after = _safe_listing(fs, spec.artifacts_dir)
    if after is None:
        return {"unavailable_reason": "artifact-dir-unreadable"}
    entries: list[dict[str, object]] = []
    for name in _fixed_names_for(spec.step_id):
        present = name in after
        if baseline is None:
            # 基线不可读——同轮产出不可证：零引用（事实性 note，绝不带哈希）
            if present:
                try:
                    fingerprint = fs.stat_fingerprint(spec.artifacts_dir / name)
                except OSError:
                    fingerprint = None
                entries.append({"name": name, "sha256": None,
                                "bytes": fingerprint[0] if fingerprint else None,
                                "note": NOTE_BASELINE_UNREADABLE})
            continue
        baseline_fp = baseline.get(name)
        if baseline_fp is None:
            if not present:
                continue  # 前后皆不存在：无事实，零条目
        elif not present:
            entries.append({"name": name, "sha256": None, "bytes": None,
                            "note": NOTE_REMOVED_THIS_RUN})
            continue
        path = spec.artifacts_dir / name
        if fs.is_symlink(path):
            entries.append({"name": name, "sha256": None, "bytes": None,
                            "note": NOTE_SYMLINK_NOT_HASHED})
            continue
        try:
            current_fp = fs.stat_fingerprint(path)
        except OSError:
            current_fp = None
        if baseline_fp is not None and current_fp == baseline_fp:
            # 指纹未变 = 非本轮写入（旧文件）——记录名字/大小事实，零哈希引用
            entries.append({"name": name, "sha256": None,
                            "bytes": current_fp[0] if current_fp else None,
                            "note": NOTE_STALE_PREEXISTING})
            continue
        try:
            data = fs.read_bytes(path)
        except OSError:
            entries.append({"name": name, "sha256": None, "bytes": None,
                            "note": NOTE_UNREADABLE})
            continue
        entry: dict[str, object] = {"name": name, "sha256": hashlib.sha256(data).hexdigest(),
                                    "bytes": len(data)}
        if len(data) > MAX_ARTIFACT_BYTES:
            entry = {"name": name, "sha256": None, "bytes": len(data),
                     "note": "oversize-not-hashed"}
        elif baseline_fp is None:
            entry["note"] = NOTE_CREATED_THIS_RUN
        entries.append(entry)
    return entries


def _fixed_names_for(step_id: str) -> tuple[str, ...]:
    return HISTORY_OUTPUT_NAMES if step_id == "history" else INSIGHTS_OUTPUT_NAMES


def discover_stage_artifacts(fs: Fs, spec: StepSpec,
                             baseline: object) -> list[dict[str, object]] | dict[str, str]:
    """步骤产物发现（只读）：monitor=前后差集 ∩ monitor-*.json（baseline=
    步骤前目录清单 list[str] | None）；history 与 insights=各自两固定名
    + **同轮新鲜度判定**（baseline=步骤前固定名指纹 dict[name, (size,
    mtime_ns) | None] | None——指纹未变的预存文件绝不引用为本轮产物）。"""
    if spec.step_id == "monitor":
        after = _safe_listing(fs, spec.artifacts_dir)
        if after is None:
            return {"unavailable_reason": "artifact-dir-unreadable"}
        listing = baseline if isinstance(baseline, list) else None
        # 基线目录此前不存在 = 首次运行（差集即全部现存产物，不误报不可读）
        new_names = sorted(set(after) - (set(listing) if listing is not None else set()))
        stems = [n[:-len(".json")] for n in new_names
                 if n.startswith("monitor-") and n.endswith(".json")
                 and MONITOR_STEM_RE.match(n[:-len(".json")]) is not None]
        return _artifact_entries(fs, spec.artifacts_dir,
                                 [f"{stem}.json" for stem in sorted(stems)])
    fingerprint_baseline = (baseline if isinstance(baseline, dict) else None)
    return _discover_fixed_name_artifacts(fs, spec, fingerprint_baseline)


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


# ---------------------------------------------------------------- calibration 步（M14-110：唯一 stdout 捕获步）


def validate_calibration_stdout(stdout: str) -> bool:
    """校准 stdout 产物契约校验（纯）：单一 JSON 文档、顶层对象、且
    ``schema_version`` / ``tool`` 与受支持的校准产物契约**精确匹配**（仅
    键在场不够——版本不兼容或身份不符的 JSON 一律 False）。malformed /
    非对象 / 缺键 / 版本或工具不匹配均为可见失败
    （``calibration-output-not-json``），绝不静默接受为产物。"""
    try:
        parsed = json.loads(stdout)
    except ValueError:
        return False
    return (isinstance(parsed, dict)
            and parsed.get("schema_version") == CALIBRATION_SCHEMA_VERSION
            and parsed.get("tool") == CALIBRATION_TOOL_NAME)


def persist_calibration_artifact(fs: Fs, artifact_dir: Path,
                                 stdout_text: str) -> dict[str, object]:
    """校准 stdout（已验证）→ redact_secrets 终防线 → symlink 拒绝 → 原子写
    固定名 calibration.json → 报告产物条目（sha256/bytes 基于最终写入文本，
    与磁盘字节一致）。写入面异常（ReportPathError/OSError）向上抛，由调用方
    转为可见 calibration 失败——本函数绝不吞错、绝不部分写。"""
    text = redact_secrets(stdout_text)
    data = text.encode("utf-8")
    path = artifact_dir / CALIBRATION_OUTPUT_NAME
    reject_symlinked_path(fs, path)
    fs.write_atomic(path, text)
    return {"name": CALIBRATION_OUTPUT_NAME,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "note": NOTE_PERSISTED_FROM_STAGE}


def run_calibration_stage(runner: Runner, spec: StepSpec, argv: tuple[str, ...],
                          clock: Clock, fs: Fs,
                          artifact_dir: Path) -> dict[str, object]:
    """执行 calibration 步（M14-110 第四步）——**唯一捕获 stdout 的步骤**：
    其余三步 stdout 绝不保留，本步 stdout 是被持久化的产物本体（校准工具
    自身零文件写入——stdout-only 契约不变，持久化责任独占归管道）。契约：
    非零退出 = failed（stdout 丢弃）；退出 0 但 stdout 不过 JSON 产物校验 =
    failed（``calibration-output-not-json``，零写入）；写入面拒绝/失败 =
    failed（``calibration-artifact-write-error``）；成功 = ok + 固定名产物
    条目。任何失败路径绝不写/绝不引用旧 calibration.json。"""
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
    if result.returncode != 0:
        return {
            "step_id": spec.step_id, "tool": spec.tool_identity,
            "command_identity": command_identity(spec.step_id),
            "status": "failed", "exit_code": result.returncode, "timed_out": False,
            "failure_category": "stage-exit-nonzero", "error_class": None,
            "failure_detail": f"exit-code-{result.returncode}", "skipped_reason": None,
            "started_at_utc": started_utc, "ended_at_utc": ended_utc,
            "duration_seconds": round(ended_perf - started_perf, 3),
            "timeout_seconds": spec.timeout_seconds, "artifacts": None,
        }
    if not validate_calibration_stdout(result.stdout):
        return {
            "step_id": spec.step_id, "tool": spec.tool_identity,
            "command_identity": command_identity(spec.step_id),
            "status": "failed", "exit_code": result.returncode, "timed_out": False,
            "failure_category": CALIBRATION_OUTPUT_NOT_JSON, "error_class": None,
            "failure_detail": "stage-stdout-not-valid-json", "skipped_reason": None,
            "started_at_utc": started_utc, "ended_at_utc": ended_utc,
            "duration_seconds": round(ended_perf - started_perf, 3),
            "timeout_seconds": spec.timeout_seconds, "artifacts": None,
        }
    try:
        entry = persist_calibration_artifact(fs, artifact_dir, result.stdout)
    except (ReportPathError, OSError) as cause:
        return {
            "step_id": spec.step_id, "tool": spec.tool_identity,
            "command_identity": command_identity(spec.step_id),
            "status": "failed", "exit_code": result.returncode, "timed_out": False,
            "failure_category": CALIBRATION_ARTIFACT_WRITE_ERROR,
            "error_class": type(cause).__name__,
            "failure_detail": "stage-artifact-persist-refused", "skipped_reason": None,
            "started_at_utc": started_utc, "ended_at_utc": ended_utc,
            "duration_seconds": round(ended_perf - started_perf, 3),
            "timeout_seconds": spec.timeout_seconds, "artifacts": None,
        }
    return {
        "step_id": spec.step_id, "tool": spec.tool_identity,
        "command_identity": command_identity(spec.step_id),
        "status": "ok", "exit_code": result.returncode, "timed_out": False,
        "failure_category": None, "error_class": None, "failure_detail": None,
        "skipped_reason": None,
        "started_at_utc": started_utc, "ended_at_utc": ended_utc,
        "duration_seconds": round(ended_perf - started_perf, 3),
        "timeout_seconds": spec.timeout_seconds, "artifacts": [entry],
    }


# ---------------------------------------------------------------- 报告（原子写 + symlink/越界拒绝）


PIPELINE_BOUNDARIES: tuple[str, ...] = (
    "plan mode is completely inert: zero subprocess, zero network, zero production reads, zero scheduler mutation",
    "execute requires --execute plus the exact confirmation phrase; malformed or missing gates exit 2 before any runner is constructed",
    "only four fixed allowlisted command forms are ever invoked (monitor --execute with its own confirm phrase; history with defaults; insights --execute with its own confirm phrase and no --source, relying on the canonical history output default; calibration with only --format json and every other input left at the tool's existing defaults — no --history, no --samples, no threshold parameters, and no pipeline CLI surface to inject any of them); no shell=True, no user command/URL/env expansion",
    "sequence is monitor then history then insights then calibration; history runs only after monitor exits 0; insights runs only after history status is ok; calibration runs only after insights status is ok; stage failures and skips are preserved with fixed-vocabulary reasons, never masked",
    "the calibration stage captures the child's stdout as an in-memory artifact, validates it as JSON (top-level object whose schema_version and tool exactly match the supported calibration artifact contract) before persistence, and persists it atomically as the pipeline-owned fixed-name calibration.json; malformed or contract-incompatible output is a visible calibration failure (calibration-output-not-json), never silently accepted; calibration JSON mode is ASCII-safe so capture stays byte-faithful under any child stdout encoding; the standalone calibration tool stays offline/read-only/stdout-only with its zero-write contract unchanged and history.jsonl is never altered or deleted",
    "per-step bounded timeouts with conservative hard caps (monitor 60-540s, history 10-120s, insights 5-50s, calibration 1-5s with a 5s default; caps sum to 715s, keeping at least 5s of the PT12M task execution time limit for pipeline startup, locking, atomic evidence reporting, and scheduler overhead; history default raised 45s -> 90s in M14-79 after three observed 45.2-47.0s kills in production, about 1.9x worst observed (46.955s) while completed runs take 0.3-7.2s; timeout facts remain recorded as-is and are never suppressed)",
    "overlap protection: fail-closed exclusive lock; zero destructive stale-lock cleanup in this round",
    "report contains only safe facts: statuses, exit codes, stage timing, fixed command identities, sanitized error categories/classes, artifact names/hashes",
    "fixed-name stage artifacts (history/insights) are cited with a SHA-256 only when the same run provably wrote them (pre-step (size, mtime_ns) fingerprint baseline; new file or changed fingerprint); preexisting unchanged files are recorded as stale-preexisting-not-cited and never cited as this run's evidence, so a timeout or failed stage can no longer reference an old fixed-name artifact",
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
                 insights_timeout: float,
                 calibration_timeout: float) -> dict[str, object]:
    return {
        "python_identity": "<sys.executable>",
        "monitor_tool": MONITOR_TOOL_NAME,
        "history_tool": HISTORY_TOOL_NAME,
        "insights_tool": INSIGHTS_TOOL_NAME,
        "calibration_tool": CALIBRATION_TOOL_NAME,
        "sequence": ["monitor", "history", "insights", "calibration"],
        "monitor_timeout_seconds": monitor_timeout,
        "history_timeout_seconds": history_timeout,
        "insights_timeout_seconds": insights_timeout,
        "calibration_timeout_seconds": calibration_timeout,
        "monitor_confirm_phrase": MONITOR_CONFIRM_PHRASE,
        "insights_confirm_phrase": INSIGHTS_CONFIRM_PHRASE,
        "calibration_output": CALIBRATION_OUTPUT_NAME,
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
        f"- overall_status=**{report['overall_status']}**（序列 monitor → history → insights → calibration，history 仅在 monitor exit 0 后运行，insights 仅在 history status=ok 后运行，calibration 仅在 insights status=ok 后运行）",
        "",
        "| 步骤 | 工具 | 状态 | 退出码 | 时长(s) | 原因/类别 |",
        "|---|---|---|---|---:|---|",
    ]
    for step_id in ("monitor", "history", "insights", "calibration"):
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
                    "insights": "insights_timeout_seconds",
                    "calibration": "calibration_timeout_seconds"}
    tool_names = {"monitor": MONITOR_TOOL_NAME, "history": HISTORY_TOOL_NAME,
                  "insights": INSIGHTS_TOOL_NAME,
                  "calibration": CALIBRATION_TOOL_NAME}
    for step_id in ("monitor", "history", "insights", "calibration"):
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
                insights_timeout: float, calibration_timeout: float,
                python_exe: str,
                log: SafeLog) -> tuple[dict[str, object], int]:
    """execute 主管道：锁 → monitor → (exit 0 才) history → (ok 才)
    insights → (ok 才) calibration → 报告 → 释锁。

    退出码契约见模块头；任何步骤失败/跳过如实入档（绝不遮蔽前置步骤
    事实），报告写入失败（证据不可失）与锁路径违规一律 EXIT_USAGE。"""
    started_utc = clock.utc_now_iso()
    monitor_spec = StepSpec("monitor", MONITOR_TOOL_NAME, monitor_timeout,
                            MONITOR_ARTIFACT_DIR)
    history_spec = StepSpec("history", HISTORY_TOOL_NAME, history_timeout,
                            HISTORY_OUTPUT_DIR)
    insights_spec = StepSpec("insights", INSIGHTS_TOOL_NAME, insights_timeout,
                             INSIGHTS_OUTPUT_DIR)
    # calibration 产物由本管道持久化到自身工件目录（校准工具零文件写入，
    # stdout-only 契约不变——故 artifacts_dir 即管道工件目录，不走发现面）
    calibration_spec = StepSpec("calibration", CALIBRATION_TOOL_NAME,
                                calibration_timeout, artifact_dir)
    config = build_config(monitor_timeout=monitor_timeout,
                          history_timeout=history_timeout,
                          insights_timeout=insights_timeout,
                          calibration_timeout=calibration_timeout)
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
        # 3) history 步——仅 monitor exit 0（ok|warn）后运行；执行前取固定名
        #    指纹基线（M14-101 同轮新鲜度：指纹未变的预存文件绝不引用为
        #    本轮产物——超时轮引用旧固定名产物的歧义不可能）
        if monitor["status"] == "ok":
            log.say(f"步骤 history: 执行（timeout {history_timeout:g}s，固定白名单形态）")
            history_baseline = fixed_name_fingerprints(fs, history_spec.artifacts_dir,
                                                       HISTORY_OUTPUT_NAMES)
            history = run_step(runner, history_spec, argvs["history"], clock)
            history["artifacts"] = discover_stage_artifacts(fs, history_spec, history_baseline)
            log.say(f"步骤 history: status={history['status']} exit_code={history['exit_code']}")
        else:
            history = skipped_step(history_spec, "monitor", monitor)
            log.say(f"步骤 history: skipped（monitor status={monitor['status']}，"
                    f"exit_code={monitor['exit_code']}——失败如实保留不遮蔽）")
        # 4) insights 步——仅 history status=ok 后运行（history.jsonl 完整
        #    落盘才可洞察）；输入恒为 insights 默认源 = history canonical
        #    输出目录（固定命令形态不带 --source，无用户可注入面）；同款
        #    指纹基线（M14-101）
        if monitor["status"] == "ok" and history["status"] == "ok":
            log.say(f"步骤 insights: 执行（timeout {insights_timeout:g}s，固定白名单形态，"
                    "默认源=history canonical 输出）")
            insights_baseline = fixed_name_fingerprints(fs, insights_spec.artifacts_dir,
                                                       INSIGHTS_OUTPUT_NAMES)
            insights = run_step(runner, insights_spec, argvs["insights"], clock)
            insights["artifacts"] = discover_stage_artifacts(fs, insights_spec, insights_baseline)
            log.say(f"步骤 insights: status={insights['status']} exit_code={insights['exit_code']}")
        else:
            insights = skipped_step(insights_spec, "history", history)
            log.say(f"步骤 insights: skipped（history status={history['status']}——"
                    "前置事实如实保留不遮蔽）")
        # 5) calibration 步——仅前三步全 ok 后运行（M14-110 第四步）：固定
        #    形态仅 --format json（输入面全部经校准工具既有默认值生效），
        #    stdout 捕获为内存产物 → JSON 校验 → 固定名 calibration.json
        #    原子持久化（管道独占持久化责任）；校准工具自身零文件写入、
        #    history.jsonl 绝不改动
        if (monitor["status"] == "ok" and history["status"] == "ok"
                and insights["status"] == "ok"):
            log.say(f"步骤 calibration: 执行（timeout {calibration_timeout:g}s，固定白名单形态"
                    " --format json，输入=校准工具既有默认值）")
            calibration = run_calibration_stage(runner, calibration_spec,
                                                argvs["calibration"], clock,
                                                fs, artifact_dir)
            if calibration["artifacts"] is not None:
                entry = calibration["artifacts"][0]  # type: ignore[index]
                log.say(f"步骤 calibration: status=ok exit_code={calibration['exit_code']}"
                        f"（产物 {entry['name']} 已持久化，sha256 在档）")  # type: ignore[union-attr]
            else:
                log.say(f"步骤 calibration: status={calibration['status']} "
                        f"exit_code={calibration['exit_code']} "
                        f"category={calibration['failure_category']}（零产物写入）")
        else:
            calibration = skipped_step(calibration_spec, "insights", insights)
            log.say(f"步骤 calibration: skipped（insights status={insights['status']}——"
                    "前置事实如实保留不遮蔽）")
        stages = {"monitor": monitor, "history": history, "insights": insights,
                  "calibration": calibration}
        all_ok = all(stage["status"] == "ok" for stage in stages.values())
        exit_code = EXIT_OK if all_ok else EXIT_STAGE_FAILED
        # 6) 释放锁（记录结果；失败为可见失败 EXIT_STAGE_FAILED，不遮蔽步骤事实）
        try:
            fs.lock_release(lock_path)
            lock["released"] = True
        except OSError as cause:
            lock["released"] = False
            lock["release_failure"] = type(cause).__name__
            log.say(f"锁释放失败（可见失败）: {type(cause).__name__}")
            exit_code = EXIT_STAGE_FAILED
        # 7) 证据报告（原子写；写入失败 = 证据不可失 → EXIT_USAGE）
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
                f"insights={stages['insights']['status']} "
                f"calibration={stages['calibration']['status']} ===")
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
        description="M14-14/M14-21/M14-110 监控管道 readiness（默认 plan 零执行；execute 需旗标+精确确认短语，"
                    "monitor→history→insights→calibration 单次组合，固定命令白名单 + 重叠锁 + 原子证据报告）",
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
                        help=f"insights 步超时 {INSIGHTS_TIMEOUT_MIN:g}-{INSIGHTS_TIMEOUT_MAX:g}s（默认 {INSIGHTS_TIMEOUT_DEFAULT:g}）")
    parser.add_argument("--calibration-timeout-seconds", type=float, default=CALIBRATION_TIMEOUT_DEFAULT,
                        help=f"calibration 步超时 {CALIBRATION_TIMEOUT_MIN:g}-{CALIBRATION_TIMEOUT_MAX:g}s（默认 {CALIBRATION_TIMEOUT_DEFAULT:g}；"
                             f"四步硬顶之和 {MONITOR_TIMEOUT_MAX:g}+{HISTORY_TIMEOUT_MAX:g}+{INSIGHTS_TIMEOUT_MAX:g}"
                             f"+{CALIBRATION_TIMEOUT_MAX:g}"
                             f"={MONITOR_TIMEOUT_MAX + HISTORY_TIMEOUT_MAX + INSIGHTS_TIMEOUT_MAX + CALIBRATION_TIMEOUT_MAX:g}s，"
                             f"对 PT12M=720s 执行时限恒留 ≥5s 管道自身开销。"
                             f"本 CLI 不暴露任何校准 argv/源/阈值参数——固定形态恒为 --format json，"
                             f"输入面全部经校准工具既有默认值生效）")
    parser.add_argument("--artifact-dir", type=Path, default=ARTIFACT_DIR,
                        help="管道报告/锁目录（默认 .verify/artifacts/m14-14-monitoring-pipeline，gitignored；"
                             "calibration 步产物 calibration.json 亦持久化于此；"
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
                                  insights_timeout=args.insights_timeout_seconds,
                                  calibration_timeout=args.calibration_timeout_seconds)
    if problems:
        for problem in problems:
            log.say(f"拒绝: {problem}")
        return EXIT_USAGE
    resolved_python = python_exe if python_exe is not None else sys.executable
    config = build_config(monitor_timeout=args.monitor_timeout_seconds,
                          history_timeout=args.history_timeout_seconds,
                          insights_timeout=args.insights_timeout_seconds,
                          calibration_timeout=args.calibration_timeout_seconds)
    # 2) plan 模式（默认）：零 subprocess、零网络、零生产读取、零调度器改动
    if not args.execute:
        log.say("=== M14-14/M14-21/M14-110 监控管道 PLAN（零 subprocess / 零网络 / 零生产读取 / 零调度器改动） ===")
        timeouts = {"monitor": args.monitor_timeout_seconds,
                    "history": args.history_timeout_seconds,
                    "insights": args.insights_timeout_seconds,
                    "calibration": args.calibration_timeout_seconds}
        for step_id in ("monitor", "history", "insights", "calibration"):
            identity = " ".join(command_identity(step_id))
            log.say(f"步骤 {step_id}: {identity}（timeout {timeouts[step_id]:g}s，固定白名单形态）")
        log.say("序列: monitor → history → insights → calibration（history 仅在 monitor exit 0 后运行；"
                "insights 仅在 history status=ok 后运行，默认源=history canonical 输出；"
                "calibration 仅在 insights status=ok 后运行，固定形态 --format json、输入=校准工具"
                "既有默认值，stdout 捕获后 JSON 校验并持久化为 calibration.json；失败如实保留）")
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
    log.say("=== M14-14/M14-21/M14-110 监控管道 EXECUTE: monitor → history → insights → calibration（固定白名单形态） ===")
    _, exit_code = run_execute(runner=execute_runner, clock=execute_clock,
                               fs=execute_fs, artifact_dir=args.artifact_dir,
                               monitor_timeout=args.monitor_timeout_seconds,
                               history_timeout=args.history_timeout_seconds,
                               insights_timeout=args.insights_timeout_seconds,
                               calibration_timeout=args.calibration_timeout_seconds,
                               python_exe=resolved_python, log=log)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
