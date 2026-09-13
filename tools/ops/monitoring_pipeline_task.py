#!/usr/bin/env python
"""M14-14 监控管道计划任务 readiness 管理器（持续/定时采集的调度面）。

管理隐藏周期计划任务（Task Scheduler），把 tools/ops/monitoring_pipeline.py
经静默 VBS wrapper（run_monitoring_pipeline_silent.vbs）挂到固定间隔触发。
子命令：``plan``（只读预检+计划）/ ``generate``（导出任务 XML 供审查——
**UTF-16 with BOM 字节**，与 XML 声明及 install 临时字节一致、外部解析器
可直接加载；零调度器改动）/ ``status``（只读状态）/ ``install`` /
``uninstall``。

设计纪律（与 tools/ops/windows_startup_task.py 同源，M14-06 R2–R4 实证模式）：
- 纯标准库；平台命令经注入 Runner + **结构性白名单门**（GatedSchtasks）——
  仅四形态放行：全量列表查询 / 单任务 /XML 明细 / ``/Create /TN <固定名>
  /XML <tmp>`` / ``/Delete /TN <固定名> /F``；其余（含 /Run、/Change、
  /End、/Create 带 /F 等一切形态）在任何执行之前拒绝。测试注入
  FakeRunner——绝不触碰真实 schtasks 的写路径。
- **install/uninstall 各自需精确确认短语**（``--confirm
  "EXECUTE MONITORING SCHEDULER CHANGE"`` 一字不差），缺一即拒绝且零
  schtasks 调用；**本开发回合零安装/零卸载/零注册**——实际注册是
  supervisor-only（获准窗口 + 提升令牌），本工具只交付 readiness。
- 绝不覆盖同名任务：install 前必先只读 query + 二次全量列表复核，已存在
  （无论归属）一律拒绝；install 绝不 /F、绝不 /Run。uninstall 的 /Delete
  仅在归属标识 + 全部关键字段 exact-owned 时附带 /F（schtasks 无 /F 会
  交互式确认，capture 管道下挂起）；foreign/missing/malformed/unknown/
  查询失败绝不 force、绝不删除。
- 固定任务身份 + 保守重复间隔：``AIOS-Monitoring-Pipeline`` /
  ``urn:aios:m14-14:monitoring-pipeline``；TimeTrigger 重复间隔 **PT15M**
  （每小时 4 次只读采集，ExecutionTimeLimit=PT12M < 间隔，配合
  MultipleInstancesPolicy=IgnoreNew 与管道自身 pipeline.lock 双重防重叠）。
- 归属判定（fail-closed，逐项精确；适配 M14-06 实证的 Task Scheduler 归一
  化）：URI 为本工具标识之一（写入值或归一化 ``\\<TASK_NAME>``——该形态
  对任何同名任务都会出现，不具区分性）+ Description 持久归属标记精确相等
  + Command/Arguments/WorkingDirectory 与全部安全相关设置逐项精确；
  归一化省略的默认值元素（TimeTrigger/Enabled、Settings/Enabled、
  Principal/RunLevel）仅在父节点在场且其余字段全部精确时按 Windows 默认值
  认可。**诚实边界**：TimeTrigger/Repetition/StartBoundary 的注册后归一化
  行为未经真实安装实证（M14-06 实证覆盖的是 LogonTrigger 形态）——首次
  supervisor 注册若暴露归一化漂移，按 malformed fail-closed（绝不弱解放
  行），处置走 M14-06 R3 同款修正回合。
- /XML 解码：schtasks /XML 输出编码随捕获通道而变（R3 管道观测 UTF-16LE
  无 BOM；R4 生产机 run_raw 观测 ASCII/UTF-8）——一律经 ``run_raw`` 取
  原始字节 + ``decode_schtasks_xml`` 按字节形态严格解码四形态；之外/
  失败按 unknown fail-closed，绝不猜。XML 解析前拒绝 DOCTYPE/ENTITY
  （XXE/实体膨胀防护）；生成侧路径/Arguments 经 xml.sax.saxutils.escape
  转义。
- wrapper 调用目标一致（无覆盖面）：VBS 固定调用
  ``<repo>/.venv/Scripts/python.exe``；本工具不提供 --python 覆盖——
  preflight/plan/install 恒检查 repo 自带 venv python。
- secret 纪律：零 secret 产生/落盘/传输；本工具不读任何 env 值。

Task XML 关键设置（generate/install 写入、status 逐项校验）：
Hidden=true；TimeTrigger(Enabled)+Repetition Interval=PT15M（无 Duration=
无限期）+固定 StartBoundary（过去时刻——注册即生效，按间隔重复）；
Principal LogonType=InteractiveToken（当前用户）/ RunLevel=LeastPrivilege；
MultipleInstancesPolicy=IgnoreNew；StartWhenAvailable=true；
ExecutionTimeLimit=PT12M；电池不禁启不停；Action=wscript.exe，
Arguments=``//B //Nologo "<repo>\\tools\\ops\\
run_monitoring_pipeline_silent.vbs"``；WorkingDirectory=<repo>。

退出码：plan 0=可安装（预检过且无同名任务）/ 1 否；generate 0=已导出 /
2 路径/写拒绝；status 0=installed / 1=unknown / 2=missing / 3=foreign /
4=malformed；install 0=成功 / 1 拒绝或失败；uninstall 0=已删或本就无任务
（幂等）/ 3=foreign / 4=malformed / 1=unknown 或失败。

用法（仓库根；install 需提升令牌——M14-06 生产实证 schtasks /Create 仅在
elevated token 下成功；本开发回合禁止人工执行 install/uninstall）：
  python tools/ops/monitoring_pipeline_task.py plan
  python tools/ops/monitoring_pipeline_task.py generate
  python tools/ops/monitoring_pipeline_task.py status
  python tools/ops/monitoring_pipeline_task.py install --confirm "EXECUTE MONITORING SCHEDULER CHANGE"
  python tools/ops/monitoring_pipeline_task.py uninstall --confirm "EXECUTE MONITORING SCHEDULER CHANGE"
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

OPS_DIR = Path(__file__).resolve().parent
REPO_ROOT = OPS_DIR.parent.parent

TASK_NAME = "AIOS-Monitoring-Pipeline"
TASK_URI = "urn:aios:m14-14:monitoring-pipeline"
#: schtasks /Create 注册后 Task Scheduler 把 RegistrationInfo/URI 重写为任务
#: 路径形式（M14-06 生产实证；对任意同名任务都会出现，不具归属区分性——
#: 仅作为「本任务名下」的已归一化形态接受，且必须叠加 Description 持久
#: 标记 + 全部字段精确匹配才构成 exact-owned）。
NORMALIZED_TASK_URI = f"\\{TASK_NAME}"
OWNED_TASK_URIS = frozenset({TASK_URI, NORMALIZED_TASK_URI})
#: 持久归属标记：Task Scheduler 归一化存储逐字保留 Description（M14-06 COM
#: 快照实证）——比对须精确相等，任何漂移/缺失按 malformed 拒绝管理。
TASK_DESCRIPTION = (
    "AIOS monitoring pipeline (M14-14) hidden scheduled read-only collection; "
    "managed by tools/ops/monitoring_pipeline_task.py - do not edit by hand"
)
#: 保守重复间隔：PT15M（每小时 4 次只读采集）；执行时限 PT12M < 间隔
#: （调度器侧不会自我重叠，另有 IgnoreNew + 管道 pipeline.lock 兜底）。
#: PT12M=720s 同时 > 管道三步超时硬顶之和 540+120+50=710s（M14-21 起
#: monitor → history → insights；交叉 pin 测试锁定——调度器绝不先于内部
#: 超时杀整任务，避免击杀留 stale lock）。
REPETITION_INTERVAL = "PT15M"
EXECUTION_TIME_LIMIT = "PT12M"
#: 固定 StartBoundary（过去时刻）：注册即生效、按间隔无限期重复
#: （Repetition 不带 Duration = indefinitely）。
START_BOUNDARY = "2026-01-01T00:00:00"
WSCRIPT = "wscript.exe"
WSCRIPT_ARGS_TEMPLATE = '//B //Nologo "{vbs}"'

#: install/uninstall 各自的精确确认短语（一字不差；本开发回合永不满足执行）
TASK_CONFIRM_PHRASE = "EXECUTE MONITORING SCHEDULER CHANGE"

#: generate 输出（gitignored，与管道报告同目录）
GENERATE_OUTPUT_NAME = "scheduled-task.xml"

TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2
EXIT_MISSING = 2
EXIT_FOREIGN = 3
EXIT_MALFORMED = 4

STATE_INSTALLED = "installed"
STATE_MISSING = "missing"
STATE_FOREIGN = "foreign"
STATE_MALFORMED = "malformed"
STATE_UNKNOWN = "unknown"

#: 无 BOM 形态的 ``<?xml`` prolog 起始字节（双编码形态，decode 入口用）。
_PROLOG_UTF8 = b"<?xml"
_PROLOG_UTF16LE = "<?xml".encode("utf-16-le")


def _repo_paths(repo_root: Path) -> dict[str, Path]:
    return {
        "pipeline": repo_root / "tools" / "ops" / "monitoring_pipeline.py",
        "monitor": repo_root / "tools" / "ops" / "production_monitor.py",
        "history": repo_root / "tools" / "ops" / "monitoring_history.py",
        "vbs": repo_root / "tools" / "ops" / "run_monitoring_pipeline_silent.vbs",
        "venv_python": repo_root / ".venv" / "Scripts" / "python.exe",
    }


# ---------------------------------------------------------------- Runner + 白名单门


class RunnerError(RuntimeError):
    """平台命令执行失败（不可执行/超时）——类别化处理，绝不保留文本。"""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class RawCommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes


def os_windows() -> bool:
    return os.name == "nt"


class RealRunner:
    """真实子进程执行：list-argv（无 shell）、capture、Windows 侧恒
    CREATE_NO_WINDOW；run_raw 取原始字节（/XML 解码通道，编码随通道而变）。"""

    def run(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0) -> CommandResult:
        text_argv = [str(item) for item in argv]
        kwargs: dict[str, object] = {"capture_output": True, "text": True,
                                     "encoding": "utf-8", "errors": "replace"}
        if os_windows():
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        try:
            result = subprocess.run(text_argv, check=False, timeout=timeout,
                                    **kwargs)  # type: ignore[arg-type]
        except (OSError, subprocess.TimeoutExpired) as cause:
            raise RunnerError(f"schtasks 不可执行/超时: {text_argv[0]}") from cause
        return CommandResult(tuple(text_argv), result.returncode,
                             result.stdout or "", result.stderr or "")

    def run_raw(self, argv: tuple[str, ...] | list[str], *,
                timeout: float = 30.0) -> RawCommandResult:
        text_argv = [str(item) for item in argv]
        kwargs: dict[str, object] = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
        if os_windows():
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        try:
            result = subprocess.run(text_argv, check=False, timeout=timeout,
                                    **kwargs)  # type: ignore[arg-type]
        except (OSError, subprocess.TimeoutExpired) as cause:
            raise RunnerError(f"schtasks 不可执行/超时: {text_argv[0]}") from cause
        return RawCommandResult(tuple(text_argv), result.returncode, result.stdout or b"")


def is_readonly_schtasks_command(argv: tuple[str, ...] | list[str]) -> bool:
    """结构性只读白名单：全量列表查询 / 单任务 /XML 明细，二形态精确放行。"""
    tokens = tuple(str(item) for item in argv)
    if tokens == ("schtasks.exe", "/Query", "/FO", "CSV", "/NH"):
        return True
    return tokens == ("schtasks.exe", "/Query", "/TN", TASK_NAME, "/XML")


def is_create_command(argv: tuple[str, ...] | list[str]) -> bool:
    """``/Create /TN <固定名> /XML <单个 .xml 临时文件>``（绝不 /F）。

    argv 形态恒定（精确前缀 schtasks.exe /Create /TN <固定名> + 长度 6）——
    ``/XML`` 之后的**唯一 token 是值位置**，其以 ``/`` 起头是 POSIX 绝对
    路径前缀而非旗标（R3 修正：Linux CI 的 tempfile 产出
    ``/tmp/.../aios-monitoring-pipeline-*.xml``，旧规则误拒）；旗标形态
    防护不靠首字符黑名单，而靠**结构**：必须以 ``.xml`` 结尾、不以 ``-``
    起头、且之后无任何多余 token（``-F``/``/F``/``x.xml/F``/追加旗标/
    非 .xml 路径/错任务名一律拒绝）。白名单不因该值位置放宽到其它任何
    位置。"""
    tokens = tuple(str(item) for item in argv)
    return (len(tokens) == 6 and tokens[:4] == ("schtasks.exe", "/Create", "/TN", TASK_NAME)
            and tokens[4] == "/XML" and tokens[5].endswith(".xml")
            and not tokens[5].startswith("-"))


def is_delete_command(argv: tuple[str, ...] | list[str]) -> bool:
    """``/Delete /TN <固定名> /F``（唯一允许 /F 的形态；仅 exact-owned 分支）。"""
    tokens = tuple(str(item) for item in argv)
    return tokens == ("schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F")


class GatedSchtasks:
    """白名单门装饰器：非四形态之一在任何执行之前拒绝（fail-closed）。
    mutation 形态（create/delete）需显式 ``allow_mutation=True`` 才放行
    ——plan/status/generate 路径恒 False（结构性零调度器改动）。"""

    def __init__(self, inner: RealRunner, *, allow_mutation: bool = False) -> None:
        self._inner = inner
        self._allow_mutation = allow_mutation

    def _check(self, argv: tuple[str, ...] | list[str]) -> None:
        if is_readonly_schtasks_command(argv):
            return
        if (self._allow_mutation and (is_create_command(argv) or is_delete_command(argv))):
            return
        raise RunnerError("schtasks-command-not-whitelisted")

    def run(self, argv: tuple[str, ...] | list[str], *, timeout: float = 60.0) -> CommandResult:
        self._check(argv)
        return self._inner.run(argv, timeout=timeout)

    def run_raw(self, argv: tuple[str, ...] | list[str], *,
                timeout: float = 30.0) -> RawCommandResult:
        self._check(argv)
        return self._inner.run_raw(argv, timeout=timeout)


# ---------------------------------------------------------------- Task XML


def _esc(value: object) -> str:
    """XML 元素文本转义（&、<、>、"）——路径含特殊字符时仍产出可解析 XML。"""
    return _xml_escape(str(value), {'"': "&quot;"})


def build_task_xml(repo_root: Path) -> str:
    """生成本工具的计划任务 XML（纯函数；无 secret，仅仓库路径）。

    repo/VBS 路径（经 Arguments）与 WorkingDirectory 一律经 ``_esc`` 转义；
    解析侧（ElementTree）自动还原，故 verify_task_xml 的精确匹配不受影响。
    """
    vbs = _repo_paths(repo_root)["vbs"].resolve()
    args = WSCRIPT_ARGS_TEMPLATE.format(vbs=vbs)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{TASK_DESCRIPTION}</Description>
    <URI>{TASK_URI}</URI>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{START_BOUNDARY}</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>{REPETITION_INTERVAL}</Interval>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>{EXECUTION_TIME_LIMIT}</ExecutionTimeLimit>
    <Hidden>true</Hidden>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{WSCRIPT}</Command>
      <Arguments>{_esc(args)}</Arguments>
      <WorkingDirectory>{_esc(repo_root.resolve())}</WorkingDirectory>
    </Exec>
  </Actions>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
</Task>
"""


#: 归属必检字段（无归一化豁免；缺失/漂移即按字段名 mismatch）。
_STRICT_EXACT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("t:RegistrationInfo/t:Description", TASK_DESCRIPTION, "RegistrationInfo/Description"),
    ("t:Triggers/t:TimeTrigger/t:StartBoundary", START_BOUNDARY, "Triggers/TimeTrigger/StartBoundary"),
    ("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", REPETITION_INTERVAL,
     "Triggers/TimeTrigger/Repetition/Interval"),
    ("t:Settings/t:Hidden", "true", "Settings/Hidden"),
    ("t:Settings/t:MultipleInstancesPolicy", "IgnoreNew", "Settings/MultipleInstancesPolicy"),
    ("t:Settings/t:StartWhenAvailable", "true", "Settings/StartWhenAvailable"),
    ("t:Settings/t:DisallowStartIfOnBatteries", "false", "Settings/DisallowStartIfOnBatteries"),
    ("t:Settings/t:StopIfGoingOnBatteries", "false", "Settings/StopIfGoingOnBatteries"),
    ("t:Settings/t:ExecutionTimeLimit", EXECUTION_TIME_LIMIT, "Settings/ExecutionTimeLimit"),
    ("t:Principals/t:Principal/t:LogonType", "InteractiveToken", "Principals/Principal/LogonType"),
)

#: 归一化省略的默认值元素（M14-06 生产 + COM 快照实证的省略面；TimeTrigger/
#: Enabled 按 LogonTrigger/Enabled 同款先例条件认可）。每项 (xpath, 默认期望值,
#: 字段名, 父节点 xpath)：省略仅在（a）父节点在场、（b）其余全部必检字段精确
#: 在场且匹配时才按 Windows 默认值认可；显式非默认值一律计 mismatch。
_DEFAULTABLE_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("t:Triggers/t:TimeTrigger/t:Enabled", "true",
     "Triggers/TimeTrigger/Enabled", "t:Triggers/t:TimeTrigger"),
    ("t:Settings/t:Enabled", "true", "Settings/Enabled", "t:Settings"),
    ("t:Principals/t:Principal/t:RunLevel", "LeastPrivilege",
     "Principals/Principal/RunLevel", "t:Principals/t:Principal"),
)


def verify_task_xml(xml_text: str, repo_root: Path) -> tuple[str, list[str]]:
    """校验任务 XML 是否本工具精确拥有。返回 (state, 不匹配字段名列表)。

    state：installed（归属标识（URI 两种形态之一 + Description 持久标记 +
    Command）+ Action 三件套 + 全部关键设置 + TimeTrigger/Repetition +
    Principal 逐项精确匹配；归一化省略的三项默认值元素按上述条件认可）/
    foreign（URI 非两种形态之一，或 Command 指向别处）/ malformed（不可
    解析/含实体定义/归属标识匹配但任一字段缺失或漂移——缺失即 mismatch，
    不得短路）。返回信息只含字段名，绝不回显系统侧字段值。
    """
    text = xml_text.strip().lstrip("﻿")
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        return STATE_MALFORMED, ["XML 含 DOCTYPE/ENTITY（拒绝解析）"]
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return STATE_MALFORMED, ["XML 解析失败"]

    def text_of(path: str) -> str | None:
        node = root.find(path, TASK_NS)
        return node.text.strip() if node is not None and node.text else None

    uri = text_of("t:RegistrationInfo/t:URI")
    command = text_of("t:Actions/t:Exec/t:Command")
    if uri not in OWNED_TASK_URIS or command != WSCRIPT:
        return STATE_FOREIGN, ["RegistrationInfo/URI", "Actions/Exec/Command"]
    expected_vbs = _repo_paths(repo_root)["vbs"].resolve()
    expected_args = WSCRIPT_ARGS_TEMPLATE.format(vbs=expected_vbs)
    issues: list[str] = []
    if text_of("t:Actions/t:Exec/t:Arguments") != expected_args:
        issues.append("Actions/Exec/Arguments")
    working_dir = text_of("t:Actions/t:Exec/t:WorkingDirectory")
    if working_dir is None or Path(working_dir).resolve() != repo_root.resolve():
        issues.append("Actions/Exec/WorkingDirectory")
    if root.find("t:Triggers/t:TimeTrigger", TASK_NS) is None:
        issues.append("Triggers/TimeTrigger")
    for xpath, expected, field_name in _STRICT_EXACT_FIELDS:
        if text_of(xpath) != expected:
            issues.append(field_name)
    all_other_fields_exact = not issues
    for xpath, expected, field_name, parent_path in _DEFAULTABLE_FIELDS:
        value = text_of(xpath)
        if value == expected:
            continue
        parent_present = root.find(parent_path, TASK_NS) is not None
        if value is None and parent_present and all_other_fields_exact:
            continue  # Task Scheduler 省略的默认值元素——按 Windows 默认值认可
        issues.append(field_name)
    if issues:
        return STATE_MALFORMED, issues
    return STATE_INSTALLED, []


# ---------------------------------------------------------------- schtasks 面


@dataclass(frozen=True)
class TaskQuery:
    state: str  # installed/missing/foreign/malformed/unknown
    issues: tuple[str, ...] = ()
    xml_text: str = ""

    @property
    def exists(self) -> bool:
        return self.state in (STATE_INSTALLED, STATE_FOREIGN, STATE_MALFORMED)


def decode_schtasks_xml(data: bytes) -> str:
    """鲁棒解码 schtasks /XML 原始输出（严格；失败抛 ValueError）。

    编码按**字节形态**严格判定（M14-06 R3/R4 实证边界：① UTF-16LE 无 BOM
    （管道观测）② LE BOM ③ BE BOM ④ ASCII/UTF-8 无 BOM + ``<?xml`` 起始
    （生产机 run_raw 观测）；prolog 声明与实际字节可不一致，不作判定依据）。
    四形态之外/解码失败/解码后非 XML 形态一律 ValueError——调用方按事实
    不完整（unknown）fail-closed，绝不猜。
    """
    if data.startswith(b"\xff\xfe"):
        text = data[2:].decode("utf-16-le")
    elif data.startswith(b"\xfe\xff"):
        text = data[2:].decode("utf-16-be")
    elif data.startswith(_PROLOG_UTF16LE):
        text = data.decode("utf-16-le")
    elif data.startswith(_PROLOG_UTF8):
        text = data.decode("utf-8")
    else:
        raise ValueError("非可识别 XML 起始形态")
    text = text.strip()
    if not text.startswith("<"):
        raise ValueError("解码成功但非 XML 形态")
    return text


def _task_listed(runner: GatedSchtasks, task_name: str) -> bool | None:
    """全量列表存在性判定（只读；任务名 ASCII，跨代码页稳定）。
    None = 列表查询本身失败（事实不完整，调用方 fail-closed）。"""
    result = runner.run(["schtasks.exe", "/Query", "/FO", "CSV", "/NH"], timeout=60.0)
    if result.returncode != 0:
        return None
    return rf'"\{task_name}"' in result.stdout or f'"{task_name}"' in result.stdout


def query_task(runner: GatedSchtasks, repo_root: Path,
               task_name: str = TASK_NAME) -> TaskQuery:
    """只读查询：先列表判存在（编码无关），存在才取 /XML 明细（原始字节+鲁棒解码）。"""
    listed = _task_listed(runner, task_name)
    if listed is None:
        return TaskQuery(STATE_UNKNOWN, ("schtasks /Query 全量列表失败（事实不完整）",))
    if not listed:
        return TaskQuery(STATE_MISSING)
    argv = ["schtasks.exe", "/Query", "/TN", task_name, "/XML"]
    try:
        result = runner.run_raw(argv, timeout=30.0)
    except RunnerError:
        return TaskQuery(STATE_UNKNOWN, ("任务在列表中但 /XML 明细查询失败（事实不完整）",))
    if result.returncode != 0 or not result.stdout.strip():
        return TaskQuery(STATE_UNKNOWN, ("任务在列表中但 /XML 明细不可读（事实不完整）",))
    try:
        xml_text = decode_schtasks_xml(result.stdout)
    except ValueError:
        return TaskQuery(STATE_UNKNOWN, ("任务在列表中但 /XML 输出无法按四形态解码（事实不完整）",))
    state, issues = verify_task_xml(xml_text, repo_root)
    return TaskQuery(state, tuple(issues), xml_text)


def _confirm_missing(runner: GatedSchtasks, task_name: str) -> bool:
    """二次存在性复核（install 预检用）：全量列表不含任务名才确认 missing。"""
    return _task_listed(runner, task_name) is False


# ---------------------------------------------------------------- 预检


def preflight(*, repo_root: Path, log) -> bool:
    """结构预检（只读）：管道两步脚本 + VBS wrapper + repo 自带 venv python
    （wrapper 实际调用目标，无覆盖面——feature worktree 无 .venv 即拒绝）。"""
    paths = _repo_paths(repo_root)
    checks = [
        ("repo 根目录", repo_root.is_dir()),
        ("监控管道脚本", paths["pipeline"].is_file()),
        ("monitor 脚本（管道步骤 1）", paths["monitor"].is_file()),
        ("history 脚本（管道步骤 2）", paths["history"].is_file()),
        ("VBS 静默 wrapper", paths["vbs"].is_file()),
        ("venv Python（wrapper 实际调用目标，不可覆盖）", paths["venv_python"].is_file()),
    ]
    ok = True
    for name, passed in checks:
        log(f"预检 {name}: {'通过' if passed else '缺失'}")
        ok = ok and passed
    return ok


# ---------------------------------------------------------------- 子命令


def cmd_plan(*, runner: GatedSchtasks, repo_root: Path, log) -> int:
    """只读预检 + 注册计划输出（零写操作、零调度器改动；同名任务存在即预告拒绝）。"""
    log(f"=== 监控管道计划任务 PLAN（零写操作）: task={TASK_NAME} ===")
    pre_ok = preflight(repo_root=repo_root, log=log)
    query = query_task(runner, repo_root)
    if query.state == STATE_INSTALLED:
        log("任务状态: installed（本工具所有且匹配）——install 将拒绝覆盖（幂等无需操作）")
        return EXIT_ERROR
    if query.exists:
        log(f"任务状态: {query.state}（不匹配字段: {', '.join(query.issues) or '归属不符'}）"
            f"——同名任务存在，install 将拒绝（绝不覆盖）")
        return EXIT_ERROR
    if query.state == STATE_UNKNOWN:
        log(f"任务查询事实不完整（{'; '.join(query.issues)}）——fail-closed，不做任何预判")
        return EXIT_ERROR
    log("任务状态: missing——注册计划如下（未执行；本开发回合零安装，实际注册 supervisor-only）：")
    log(f"  schtasks.exe /Create /TN {TASK_NAME} /XML <临时文件（UTF-16，用后即删）>")
    log(f"  关键设置: Hidden=true | TimeTrigger 重复间隔 {REPETITION_INTERVAL}（无 Duration=无限期）"
        f" | InteractiveToken/LeastPrivilege | IgnoreNew | StartWhenAvailable"
        f" | ExecutionTimeLimit={EXECUTION_TIME_LIMIT} | 电池不禁启不停")
    log(f"  Action: {WSCRIPT} //B //Nologo \"<repo>\\tools\\ops\\run_monitoring_pipeline_silent.vbs\"")
    log(f"  WorkingDirectory: <repo> = {repo_root}")
    log("  回滚: python tools/ops/monitoring_pipeline_task.py uninstall"
        " --confirm \"EXECUTE MONITORING SCHEDULER CHANGE\"（仅删本工具精确拥有的任务）")
    log('  实际安装需: install --confirm "EXECUTE MONITORING SCHEDULER CHANGE"'
        "（提升令牌 + supervisor 获准窗口）")
    if not pre_ok:
        log("预检未全过（见上）——install 将拒绝；plan 仍输出计划供审查")
        return EXIT_ERROR
    log("=== plan 结果: OK（可注册；未做任何修改；本回合不注册）===")
    return EXIT_OK


def cmd_generate(*, repo_root: Path, log, out_dir: Path | None = None) -> int:
    """导出任务 XML 到 gitignored 工件目录供审查（零调度器改动）。

    字节编码 = **UTF-16 with BOM**（``text.encode("utf-16")``，与
    build_task_xml 的 ``encoding="UTF-16"`` 声明及 install 临时文件字节
    **完全一致**）——外部解析器（System.Xml 等）可直接加载；R2 修正前误以
    UTF-8 写出，声明与字节不符致 XmlDocument 拒载。落盘后**回读原始字节**
    经 decode_schtasks_xml（字节形态严格解码）+ verify_task_xml 复核——
    校验的是磁盘上的真实工件而非仅内存文本。
    """
    log(f"=== 监控管道计划任务 GENERATE（零调度器改动）: task={TASK_NAME} ===")
    directory = out_dir if out_dir is not None else (
        repo_root / ".verify" / "artifacts" / "m14-14-monitoring-pipeline")
    out_path = directory / GENERATE_OUTPUT_NAME
    if out_path.is_symlink():
        log("拒绝: 输出路径含 symlink（零写入）")
        return EXIT_REFUSED
    for ancestor in out_path.parents:
        if ancestor.exists() and ancestor.is_symlink():
            log("拒绝: 输出祖先含 symlink（零写入）")
            return EXIT_REFUSED
    try:
        directory.mkdir(parents=True, exist_ok=True)
        payload = build_task_xml(repo_root).encode("utf-16")  # BOM + UTF-16LE
        tmp_path = directory / f".{GENERATE_OUTPUT_NAME}.tmp"
        with open(tmp_path, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, out_path)
    except OSError as cause:
        log(f"拒绝: XML 导出失败: {type(cause).__name__}")
        return EXIT_REFUSED
    # 回读磁盘真实字节复核：声明/字节一致（UTF-16 BOM 形态）+ 归属校验
    try:
        on_disk = out_path.read_bytes()
        decoded = decode_schtasks_xml(on_disk)
    except (OSError, ValueError) as cause:
        log(f"拒绝: 导出工件不可解析（声明/字节形态不一致？）: {type(cause).__name__}")
        return EXIT_ERROR
    state, issues = verify_task_xml(decoded, repo_root)
    log(f"导出: {out_path.name} -> {directory}（gitignored；UTF-16 with BOM，与声明/安装临时字节一致；"
        f"回读 state={state}{' 字段: ' + ', '.join(issues) if issues else ''}）")
    return EXIT_OK if state == STATE_INSTALLED else EXIT_ERROR


def cmd_status(*, runner: GatedSchtasks, repo_root: Path, log) -> int:
    """只读状态：installed/missing/foreign/malformed/unknown 五态 + 字段级校验。"""
    log(f"=== 监控管道计划任务 STATUS（只读）: task={TASK_NAME} ===")
    query = query_task(runner, repo_root)
    if query.state == STATE_INSTALLED:
        log("状态: installed——本工具所有，Action/参数/cwd/Hidden/触发器/间隔/时限逐项匹配")
        return EXIT_OK
    if query.state == STATE_MISSING:
        log("状态: missing——未注册（本开发回合不注册，符合预期）")
        return EXIT_MISSING
    if query.state == STATE_FOREIGN:
        log("状态: foreign——同名任务不属本工具（字段: RegistrationInfo/URI、Actions/Exec/Command）")
        return EXIT_FOREIGN
    if query.state == STATE_MALFORMED:
        log(f"状态: malformed——归属标识匹配但字段缺失/漂移或 XML 不可解析（字段: {', '.join(query.issues)}）")
        return EXIT_MALFORMED
    log(f"状态: unknown——查询事实不完整（{'; '.join(query.issues)}），fail-closed")
    return EXIT_ERROR


def cmd_install(*, runner: GatedSchtasks, repo_root: Path, log) -> int:
    """真实安装（**supervisor-only**；本开发回合禁止执行——契约测试仅注入
    FakeRunner）。预检+无同名+临时 XML+归一化容错复查全过才 /Create。"""
    log(f"=== 监控管道计划任务 INSTALL: task={TASK_NAME} ===")
    if not preflight(repo_root=repo_root, log=log):
        log("install 拒绝：预检未全过（见上）")
        return EXIT_ERROR
    query = query_task(runner, repo_root)
    if query.state != STATE_MISSING:
        log(f"install 拒绝：任务已存在或事实不完整（state={query.state}）——绝不覆盖")
        return EXIT_ERROR
    if not _confirm_missing(runner, TASK_NAME):
        log("install 拒绝：二次存在性复核未确认 missing——fail-closed")
        return EXIT_ERROR
    xml_text = build_task_xml(repo_root)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", suffix=".xml", prefix="aios-monitoring-pipeline-",
                                         delete=False) as handle:
            handle.write(xml_text.encode("utf-16"))
            tmp_path = Path(handle.name)
        created = runner.run(["schtasks.exe", "/Create", "/TN", TASK_NAME,
                              "/XML", str(tmp_path)], timeout=60.0)
        if created.returncode != 0:
            log(f"install 失败：schtasks /Create rc={created.returncode}")
            return EXIT_ERROR
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)  # 临时 XML 无 secret，仍即弃
    verify = query_task(runner, repo_root)
    if verify.state != STATE_INSTALLED:
        log(f"install 后复查不符（state={verify.state}, 字段: {', '.join(verify.issues)}）"
            "——请 uninstall 后重试（归一化漂移按 malformed fail-closed）")
        return EXIT_ERROR
    log(f"install 成功：task={TASK_NAME}（hidden，间隔 {REPETITION_INTERVAL}；"
        "本工具所有，Action/cwd/Hidden/触发器逐项复查通过）")
    return EXIT_OK


def cmd_uninstall(*, runner: GatedSchtasks, repo_root: Path, log) -> int:
    """卸载：仅在本工具精确拥有的任务上 ``/Delete /F``；其余状态零删除。

    /F 仅出现在 exact-owned 分支（schtasks /Delete 无 /F 会交互式确认，
    capture 管道无 stdin 时挂起）；foreign/missing/malformed/unknown/查询
    失败一律可见拒绝且零 /Delete（绝不 force、绝不碰他人任务）。
    """
    log(f"=== 监控管道计划任务 UNINSTALL: task={TASK_NAME} ===")
    query = query_task(runner, repo_root)
    if query.state == STATE_MISSING:
        log("任务不存在——幂等无需卸载（零操作）")
        return EXIT_OK
    if query.state != STATE_INSTALLED:
        log(f"卸载拒绝：state={query.state}（不匹配字段: {', '.join(query.issues) or '归属不符'}）"
            f"——只删本工具精确拥有的任务，不碰他人任务")
        if query.state == STATE_FOREIGN:
            return EXIT_FOREIGN
        if query.state == STATE_MALFORMED:
            return EXIT_MALFORMED
        return EXIT_ERROR  # unknown：查询事实不完整，可见拒绝
    removed = runner.run(["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"],
                         timeout=60.0)
    if removed.returncode != 0:
        log(f"卸载失败：schtasks /Delete rc={removed.returncode}")
        return EXIT_ERROR
    log(f"卸载成功：task={TASK_NAME} 已删除（仅此一项，无级联；exact-owned /F）")
    return EXIT_OK


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitoring_pipeline_task.py",
        description="M14-14 监控管道计划任务 readiness 管理器"
                    "（plan/generate/status/install/uninstall；install/uninstall 需精确确认短语，"
                    "实际注册 supervisor-only，本开发回合零安装）",
    )
    parser.add_argument("command", choices=["plan", "generate", "status", "install", "uninstall"],
                        help="plan=只读预检；generate=导出任务 XML（UTF-16 with BOM，外部解析器可直接加载；零调度器改动）；"
                             "status=只读状态；install=安装（supervisor 审查后）；"
                             "uninstall=精确匹配卸载")
    parser.add_argument("--confirm", default="",
                        help=f'install/uninstall 必配 --confirm "{TASK_CONFIRM_PHRASE}"（精确匹配）')
    # 刻意不提供 --python 覆盖：venv 检查目标恒为 wrapper 实际调用的
    # <repo>/.venv/Scripts/python.exe（见 preflight）。
    return parser


def main(argv: list[str] | None = None, *, runner: GatedSchtasks | None = None,
         log=None) -> int:
    args = build_parser().parse_args(argv)
    if log is None:
        def log(message: str) -> None:
            print(f"[pipeline-task] {message}", flush=True)
    mutating = args.command in ("install", "uninstall")
    if mutating and args.confirm != TASK_CONFIRM_PHRASE:
        log(f'拒绝: {args.command} 必配 --confirm "{TASK_CONFIRM_PHRASE}"'
            "（精确匹配，当前不匹配）——零 schtasks 调用（fail-closed）")
        return EXIT_ERROR
    # 读路径恒 allow_mutation=False（结构性零调度器改动）；mutation 子命令
    # 在短语门禁通过后才构造放行门。
    execute_runner = runner if runner is not None else GatedSchtasks(
        RealRunner(), allow_mutation=mutating)
    handlers = {
        "plan": lambda: cmd_plan(runner=execute_runner, repo_root=REPO_ROOT, log=log),
        "generate": lambda: cmd_generate(repo_root=REPO_ROOT, log=log),
        "status": lambda: cmd_status(runner=execute_runner, repo_root=REPO_ROOT, log=log),
        "install": lambda: cmd_install(runner=execute_runner, repo_root=REPO_ROOT, log=log),
        "uninstall": lambda: cmd_uninstall(runner=execute_runner, repo_root=REPO_ROOT, log=log),
    }
    return handlers[args.command]()


if __name__ == "__main__":
    sys.exit(main())
