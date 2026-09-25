#!/usr/bin/env python
"""M14-141 production drift watch 告警分发周期计划任务 readiness 管理器。

管理隐藏周期计划任务（Task Scheduler），把 M14-137 告警任务桥
``tools/ops/production_drift_watch_alert_task.py`` 经静默 VBS wrapper
（``run_production_drift_watch_alert_silent.vbs``）挂到固定间隔触发的
readiness 面，为后续 supervisor 显式 install 做准备。**独立任务**：与
M14-129 AIOS-Production-Drift-Watch（watcher）、M14-06 AIOS-Production-
Recovery、M14-14 AIOS-Monitoring-Pipeline 互不接入/互不改动（不同任务
名/URI/wrapper），各任务同机并存、各自独立管理；本工具**绝不复用、绝不
改动既有 watcher 任务**。子命令：``plan``（只读预检+计划）/ ``generate``
（导出任务 XML 供审查——**UTF-16 with BOM 字节**，与 XML 声明及 install
临时字节一致、外部解析器可直接加载；零调度器改动）/ ``status``（只读
状态）/ ``install`` / ``uninstall``。

设计纪律（与 tools/ops/production_drift_watch_task.py（M14-129）同源的
已验证模式；windows_startup_task.py M14-06 R2–R4 实证先例）：
- 纯标准库；平台命令经注入 Runner + **结构性白名单门**（GatedSchtasks）
  ——仅四形态放行：全量列表查询 / 单任务 /XML 明细 / ``/Create /TN
  <固定名> /XML <tmp>`` / ``/Delete /TN <固定名> /F``；其余（含 /Run、
  /Change、/End、/Create 带 /F 等一切形态，以及**任何其它任务名——含
  M14-129 watcher 任务名**）在任何执行之前拒绝。测试注入 FakeRunner
  ——绝不触碰真实 schtasks 的写路径。
- **install/uninstall 各自需精确确认短语**（``--confirm
  "EXECUTE PRODUCTION DRIFT WATCH ALERT SCHEDULER CHANGE"`` 一字不
  差），缺一/近似即拒绝且零 schtasks 调用；**本开发回合零安装/零卸载/
  零注册**——实际注册是 supervisor-only（获准窗口），本工具只交付
  readiness。
- 绝不覆盖同名任务：install 前必先只读 query + 二次全量列表复核，已存在
  （无论归属）一律拒绝；install 绝不 /F、绝不 /Run。uninstall 的 /Delete
  仅在归属标识 + 全部关键字段 exact-owned 时附带 /F（schtasks 无 /F 会
  交互式确认，capture 管道下挂起）；foreign/missing/malformed/unknown/
  查询失败绝不 force、绝不删除。
- 固定任务身份 + 非重叠间隔：``AIOS-Production-Drift-Watch-Alert`` /
  ``urn:aios:m14-141:production-drift-watch-alert``；TimeTrigger 重复
  间隔 **PT15M** 与 M14-129 watcher 同节奏，但固定 StartBoundary
  **2026-01-01T00:05:00**（watcher 为 00:00:00）——恒定 +5 分钟偏移，
  告警槽位（:05/:20/:35:50）与 watcher 槽位（:00/:15/:30/:45）互不
  重叠：watcher 每轮先跑并产出报告，告警任务桥在下一槽位对**已完整落盘**
  的最新报告做分发判定（M14-127 报告原子写——告警桥绝不读半写文件；
  病态全超时情形下 watcher 单轮可达 330s 硬顶 > 300s 偏移，此时告警桥
  评估的是上一份**完整**报告——M14-137「最新合法 execute 报告」选择
  语义不受影响，fail-closed 恒成立）。ExecutionTimeLimit=PT10M < 间隔，
  配合 MultipleInstancesPolicy=IgnoreNew 防同任务自重叠（IgnoreNew 是
  每任务自身的多实例策略；跨任务不重叠靠上述固定偏移）。
- **预算交叉 pin（调度器绝不先于内部超时杀整任务）**：M14-137 任务桥
  单轮最坏墙钟 = 其唯一 dispatch 子进程预算 CHILD_TIMEOUT_SECONDS 300s
  （M14-137 常量）+ 解释器启动/报告扫描余量；选择 PT10M=600s > 300s
  （执行时限覆盖子进程预算并留 ≥300s 收尾余量）、PT15M=900s > 600s
  （间隔大于执行时限，调度器侧不自我重叠）——契约测试逐项断言
  interval > limit > 300s（常量经模块加载交叉取自 M14-137，漂移即测试
  失败）。
- 归属判定（fail-closed，逐项精确；适配 M14-06 实证的 Task Scheduler
  归一化）：URI 为本工具标识之一（写入值或归一化 ``\\<TASK_NAME>``——
  该形式对任何同名任务都会出现，不具区分性）+ Description 持久归属标记
  精确相等 + Command/Arguments/WorkingDirectory 与全部安全相关设置逐项
  精确；归一化省略的默认值元素（TimeTrigger/Enabled、Settings/Enabled、
  Settings/StartWhenAvailable（R1 起恒 false——错失槽位绝不补跑）、
  Principal/RunLevel）仅在父节点在场且其余字段全部精确时按 Windows 默认
  值认可。**诚实边界**：TimeTrigger/Repetition/StartBoundary 的注册后
  归一化行为已有 M14-131 真实安装实证（M14-129 同款形态），但本任务
  自身的注册后归一化未在本回合实证——首次 supervisor 注册若暴露归一化
  漂移，按 malformed fail-closed（绝不弱解放行）。
- /XML 解码：schtasks /XML 输出编码随捕获通道而变——一律经 ``run_raw``
  取原始字节 + ``decode_schtasks_xml`` 按字节形态严格解码四形态（UTF-16LE
  无 BOM / LE BOM / BE BOM / ASCII/UTF-8 prolog）；之外/失败按 unknown
  fail-closed，绝不猜。XML 解析前拒绝 DOCTYPE/ENTITY（XXE/实体膨胀
  防护）；生成侧路径/Arguments 经 xml.sax.saxutils.escape 转义。
- wrapper 调用目标一致（无覆盖面）：VBS 固定调用
  ``<repo>/.venv/Scripts/python.exe`` + 仓库内 M14-137 任务桥；本工具
  不提供 --python 覆盖——preflight 恒检查 repo 自带 venv python。
- **wrapper 内容校验（fail-closed）**：plan/generate/install 恒经
  ``verify_wrapper_content`` 校验仓库内 wrapper 的结构性标记——仓库根
  推导、隐藏窗口、退出码透传、固定调用目标、固定 secret 路径、
  ``--execute`` 与 M14-137 自有确认短语（经模块加载交叉 pin）；并拒绝
  盘符硬编码/网络面/解释器面/文件读写面（CreateTextFile/OpenTextFile）
  token 与 secret 形态值。
- secret 纪律：零 secret 产生/落盘/传输/回显。操作者 webhook secret
  JSON 由操作者放在固定仓库相对路径
  ``infra/env.production-drift-watch-alert-secret.json``（gitignored；
  与 M14-06 ``infra/env.production-recovery`` 同款「固定路径 + 仅存在性
  检查」先例）——preflight 只查该路径**存在性**（is_file 且非 symlink），
  绝不读取/绝不回显其值（内容校验全部由 M14-135 承担）。任何输出面
  （XML/wrapper/日志）只允许出现该路径本身。本工具不读任何 env 值、
  零网络、零生产采集——本工具只管理任务注册面，从不运行任务桥本体
  （那是 VBS→Task Scheduler 侧的 supervisor 获准行为）。

Task XML 关键设置（generate/install 写入、status 逐项校验）：
Hidden=true；TimeTrigger(Enabled)+Repetition Interval=PT15M（无 Duration=
无限期）+固定 StartBoundary 2026-01-01T00:05:00（过去时刻——注册即生效，
按间隔重复；与 M14-129 watcher 恒差 5 分钟）；Principal
LogonType=InteractiveToken（当前用户）/ RunLevel=LeastPrivilege；
MultipleInstancesPolicy=IgnoreNew；StartWhenAvailable=**false**
（R1 supervisor 修正：固定过去 StartBoundary + true 会在注册后立即产生
**不可控补跑**——错失槽位绝不补跑，下一个固定 PT15M 节点运行）；
ExecutionTimeLimit=PT10M；电池不禁启不停；Action=wscript.exe，
Arguments=``//B //Nologo "<repo>\\tools\\ops\\
run_production_drift_watch_alert_silent.vbs"``；WorkingDirectory=<repo>。

退出码：plan 0=可注册（预检过且无同名任务）/ 1 否；generate 0=已导出 /
2 预检或路径/写拒绝；status 0=installed / 1=unknown / 2=missing /
3=foreign / 4=malformed；install 0=成功 / 1=拒绝或失败；uninstall
0=已删或本就无任务（幂等）/ 3=foreign / 4=malformed / 1=unknown 或失败。

用法（仓库根；实际注册 supervisor-only——本开发回合禁止人工执行
install/uninstall）：
  python tools/ops/production_drift_watch_alert_scheduler.py plan
  python tools/ops/production_drift_watch_alert_scheduler.py generate
  python tools/ops/production_drift_watch_alert_scheduler.py status
  python tools/ops/production_drift_watch_alert_scheduler.py install --confirm "EXECUTE PRODUCTION DRIFT WATCH ALERT SCHEDULER CHANGE"
  python tools/ops/production_drift_watch_alert_scheduler.py uninstall --confirm "EXECUTE PRODUCTION DRIFT WATCH ALERT SCHEDULER CHANGE"
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

OPS_DIR = Path(__file__).resolve().parent
REPO_ROOT = OPS_DIR.parent.parent

TASK_NAME = "AIOS-Production-Drift-Watch-Alert"
TASK_URI = "urn:aios:m14-141:production-drift-watch-alert"
#: schtasks /Create 注册后 Task Scheduler 把 RegistrationInfo/URI 重写为任务
#: 路径形式（M14-06 生产实证 + M14-131 真实安装复核；对任意同名任务都会
#: 出现，不具归属区分性——仅作为「本任务名下」的已归一化形态接受，且必须
#: 叠加 Description 持久标记 + 全部字段精确匹配才构成 exact-owned）。
NORMALIZED_TASK_URI = f"\\{TASK_NAME}"
OWNED_TASK_URIS = frozenset({TASK_URI, NORMALIZED_TASK_URI})
#: 持久归属标记：Task Scheduler 归一化存储逐字保留 Description（M14-06 COM
#: 快照实证）——比对须精确相等，任何漂移/缺失按 malformed 拒绝管理。
TASK_DESCRIPTION = (
    "AIOS production drift watch alert (M14-141) hidden scheduled "
    "alert-dispatch bridge; managed by tools/ops/"
    "production_drift_watch_alert_scheduler.py - do not edit by hand"
)
#: 非重叠周期：与 M14-129 watcher（StartBoundary 2026-01-01T00:00:00、
#: PT15M）同节奏、恒差 5 分钟——watcher 槽位 :00/:15/:30/:45，本任务槽位
#: :05/:20/:35/:50（契约测试交叉取 M14-129 常量断言偏移恰 300s）。
START_BOUNDARY = "2026-01-01T00:05:00"
#: 保守重复间隔：PT15M=900s；执行时限 PT10M=600s < 间隔（调度器侧不会
#: 自我重叠，另有 IgnoreNew 兜底）。PT10M=600s 同时 > M14-137 任务桥
#: 单轮最坏墙钟（唯一 dispatch 子进程预算 CHILD_TIMEOUT_SECONDS 300s
# + 解释器启动/报告扫描余量）——契约测试交叉 pin：调度器绝不先于内部
# 超时杀整任务，恒留 ≥300s 收尾余量。
REPETITION_INTERVAL = "PT15M"
EXECUTION_TIME_LIMIT = "PT10M"
WSCRIPT = "wscript.exe"
WSCRIPT_ARGS_TEMPLATE = '//B //Nologo "{vbs}"'

#: 操作者 webhook secret JSON 的固定仓库相对路径（gitignored，由操作者
#: 按 M14-135 secret 文件语义创建；本工具与 wrapper 都只做存在性检查，
#: 绝不读取其内容——内容校验全部由 M14-135 承担）。wrapper 内以
#: BuildPath(<repo>, "infra", "env.production-drift-watch-alert-secret.json")
#: 拼接——内容校验按引号包裹的两段常量逐字出现判定（POSIX 斜杠连写形态
#: 在 VBS 文本中不出现）。
SECRET_FILE_DIR_NAME = "infra"
SECRET_FILE_NAME = "env.production-drift-watch-alert-secret.json"
SECRET_FILE_RELATIVE = Path(SECRET_FILE_DIR_NAME) / SECRET_FILE_NAME
#: 展示用固定相对路径文本（输出面只允许出现路径本身，绝不出现值）
SECRET_FILE_DISPLAY = SECRET_FILE_RELATIVE.as_posix()

#: install/uninstall 各自的精确确认短语（一字不差；本开发回合永不满足执行）
TASK_CONFIRM_PHRASE = "EXECUTE PRODUCTION DRIFT WATCH ALERT SCHEDULER CHANGE"

#: generate 输出（gitignored，与 drift watch 家族工件同根的专属子目录）
GENERATE_DIR_NAME = "m14-141-drift-watch-alert-scheduler"
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

#: wrapper/工具源码的 secret 形态黑名单（防御性终防线；只查形态绝不读值）
SECRET_PATTERNS: tuple[str, ...] = ("sk-", "AKIA", "ghp_", "xoxb_", "-----BEGIN")


def _load_alert_task_module() -> object:
    """加载同目录 M14-137 告警任务桥（wrapper 校验与其确认短语交叉 pin）。"""
    spec = importlib.util.spec_from_file_location(
        "production_drift_watch_alert_task_shared",
        OPS_DIR / "production_drift_watch_alert_task.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


alert_task = _load_alert_task_module()  # type: ignore[assignment]
#: M14-137 任务桥 execute 确认短语（wrapper 必须一字不差携带；单一事实源）
ALERT_TASK_CONFIRM_PHRASE: str = alert_task.TASK_CONFIRM_PHRASE  # type: ignore[attr-defined]
#: M14-137 任务桥子进程墙钟预算（契约测试预算交叉 pin 的单一事实源）
ALERT_CHILD_TIMEOUT_SECONDS: float = alert_task.CHILD_TIMEOUT_SECONDS  # type: ignore[attr-defined]


def _repo_paths(repo_root: Path) -> dict[str, Path]:
    return {
        "alert_task": repo_root / "tools" / "ops" / "production_drift_watch_alert_task.py",
        "vbs": repo_root / "tools" / "ops" / "run_production_drift_watch_alert_silent.vbs",
        "venv_python": repo_root / ".venv" / "Scripts" / "python.exe",
        "secret_file": repo_root / SECRET_FILE_RELATIVE,
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
    """结构性只读白名单：全量列表查询 / 单任务 /XML 明细，二形态精确放行
    （单任务明细仅限本任务名——**M14-129 watcher 等任何其它任务名恒拒**）。"""
    tokens = tuple(str(item) for item in argv)
    if tokens == ("schtasks.exe", "/Query", "/FO", "CSV", "/NH"):
        return True
    return tokens == ("schtasks.exe", "/Query", "/TN", TASK_NAME, "/XML")


def is_create_command(argv: tuple[str, ...] | list[str]) -> bool:
    """``/Create /TN <固定名> /XML <单个 .xml 临时文件>``（绝不 /F）。

    argv 形态恒定（精确前缀 schtasks.exe /Create /TN <固定名> + 长度 6）
    ——``/XML`` 之后的**唯一 token 是值位置**，其以 ``/`` 起头是 POSIX
    绝对路径前缀而非旗标（Linux CI 的 tempfile 产出 ``/tmp/...`` 路径，
    M14-14 PR #89 R3 实证）；旗标形态防护不靠首字符黑名单，而靠**结构**：
    必须以 ``.xml`` 结尾、不以 ``-`` 起头、且之后无任何多余 token（``-F``
    / ``/F`` / ``x.xml/F`` / 追加旗标 / 非 .xml 路径 / 错任务名一律拒绝）。
    白名单不因该值位置放宽到其它任何位置。"""
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
    """生成本工具的计划任务 XML（纯函数；零 secret，仅仓库路径）。

    repo/VBS 路径（经 Arguments）与 WorkingDirectory 一律经 ``_esc`` 转义；
    解析侧（ElementTree）自动还原，故 verify_task_xml 的精确匹配不受影响。
    XML 只含 wrapper 路径，**绝不含 secret 路径或任何 secret 值**（secret
    路径只存在于 wrapper 内部，经其固定相对路径引用）。"""
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
    <StartWhenAvailable>false</StartWhenAvailable>
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
    ("t:Settings/t:DisallowStartIfOnBatteries", "false", "Settings/DisallowStartIfOnBatteries"),
    ("t:Settings/t:StopIfGoingOnBatteries", "false", "Settings/StopIfGoingOnBatteries"),
    ("t:Settings/t:ExecutionTimeLimit", EXECUTION_TIME_LIMIT, "Settings/ExecutionTimeLimit"),
    ("t:Principals/t:Principal/t:LogonType", "InteractiveToken", "Principals/Principal/LogonType"),
)

#: 归一化省略的默认值元素（M14-06 生产 + COM 快照实证的省略规则：值恰
#: 为 Windows 默认值的元素注册存储时省略；M14-131 真实安装复核 TimeTrigger
#: 同款）。每项 (xpath, 默认期望值, 字段名, 父节点 xpath)：省略仅在
#: （a）父节点在场、（b）其余全部必检字段精确在场且匹配时才按 Windows
#: 默认值认可；显式非默认值一律计 mismatch。Settings/StartWhenAvailable
#: 的 Windows 默认值即 false——R1 supervisor 修正把该字段从 true 收紧为
#: false（错失槽位绝不补跑）后，其省略形态按本机制条件认可（显式 true
#: 恒 mismatch）。
_DEFAULTABLE_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("t:Triggers/t:TimeTrigger/t:Enabled", "true",
     "Triggers/TimeTrigger/Enabled", "t:Triggers/t:TimeTrigger"),
    ("t:Settings/t:Enabled", "true", "Settings/Enabled", "t:Settings"),
    ("t:Settings/t:StartWhenAvailable", "false",
     "Settings/StartWhenAvailable", "t:Settings"),
    ("t:Principals/t:Principal/t:RunLevel", "LeastPrivilege",
     "Principals/Principal/RunLevel", "t:Principals/t:Principal"),
)


def verify_task_xml(xml_text: str, repo_root: Path) -> tuple[str, list[str]]:
    """校验任务 XML 是否本工具精确拥有。返回 (state, 不匹配字段名列表)。

    state：installed（归属标识（URI 两种形态之一 + Description 持久标记 +
    Command）+ Action 三件套 + 全部关键设置 + TimeTrigger/Repetition +
    Principal 逐项精确匹配；归一化省略的四项默认值元素按上述条件认可）/
    foreign（URI 非两种形态之一，或 Command 指向别处）/ malformed（不可
    解析/含实体定义/归属标识匹配但任一字段缺失或漂移——缺失即 mismatch，
    不得短路）。返回信息只含字段名，绝不回显系统侧字段值。"""
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
    不完整（unknown）fail-closed，绝不猜。"""
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


# ---------------------------------------------------------------- wrapper 内容校验（fail-closed）


#: wrapper 必须逐字包含的结构性标记（与 run_production_drift_watch_silent.vbs
#: 同款纪律 + M14-141 告警链扩展）。确认短语经模块加载交叉 pin 自 M14-137
#: 单一事实源常量，绝不字符串复制。
_WRAPPER_REQUIRED_MARKERS: tuple[str, ...] = (
    "GetParentFolderName",
    "BuildPath",
    ", 0, True)",
    "WScript.Quit exitCode",
    '".venv"), "Scripts"), "python.exe"',
    "production_drift_watch_alert_task.py",
    "--secret-file",
    "--execute",
    f'"{SECRET_FILE_DIR_NAME}"',
    f'"{SECRET_FILE_NAME}"',
    f'"{ALERT_TASK_CONFIRM_PHRASE}"',
)

#: wrapper 恒不得包含的 token（网络面/解释器面/文件读写面——secret 只查
#: 存在性，绝不读：OpenTextFile/CreateTextFile 都不在允许面）
_WRAPPER_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "http://", "https://", "mshta", "powershell", "cmd.exe",
    "CreateTextFile", "OpenTextFile", "ADODB.Stream",
) + SECRET_PATTERNS

#: 盘符硬编码形态（``D:\`` / ``C:/`` 等——wrapper 必须自脚本位置推导）
_DRIVE_LETTER_RE = re.compile(r"[A-Za-z]:[\\\\/]")


def verify_wrapper_content(repo_root: Path) -> tuple[bool, list[str]]:
    """校验仓库内 VBS wrapper 的结构性内容（只读；fail-closed）。

    必检标记逐项在场（仓库根推导/隐藏窗口/退出码透传/固定 venv python/
    M14-137 任务桥目标/--secret-file/--execute/固定 secret 相对路径/
    M14-137 自有确认短语——经模块常量交叉 pin）；任何盘符硬编码、网络/
    解释器/文件读写面 token、secret 形态值 → 拒绝。返回 (ok, 问题类别列表)
    ——只含类别词汇，绝不回显 wrapper 内容。"""
    issues: list[str] = []
    wrapper = _repo_paths(repo_root)["vbs"]
    try:
        text = wrapper.read_text(encoding="utf-8")
    except OSError:
        return False, ["wrapper-unreadable"]
    for marker in _WRAPPER_REQUIRED_MARKERS:
        if marker not in text:
            issues.append("wrapper-marker-missing")
            break
    if _DRIVE_LETTER_RE.search(text):
        issues.append("wrapper-drive-letter-hardcoded")
    for token in _WRAPPER_FORBIDDEN_TOKENS:
        if token in text:
            issues.append("wrapper-forbidden-token")
            break
    return (not issues), issues


# ---------------------------------------------------------------- 预检


@dataclass
class Preflight:
    ok: bool
    secret_present: bool
    wrapper_ok: bool


def preflight(*, repo_root: Path, log) -> Preflight:
    """结构预检（只读；fail-closed，fail-fast——任一项不过即整体不过）。

    恒查：repo 根目录、M14-137 任务桥脚本、VBS wrapper（存在 + 非
    symlink）、repo 自带 venv python（wrapper 实际调用目标，无覆盖面——
    feature worktree 无 .venv 即拒绝）、secret 文件**存在性**（固定相对
    路径，绝不读值——缺失即拒绝，install/plan 都不放行）+ wrapper 内容
    结构校验（必检标记 + 禁面 token）。"""
    paths = _repo_paths(repo_root)
    secret_present = (paths["secret_file"].is_file()
                      and not paths["secret_file"].is_symlink())
    wrapper_ok = False
    checks: list[tuple[str, bool]] = [
        ("repo 根目录", repo_root.is_dir()),
        ("M14-137 告警任务桥脚本", paths["alert_task"].is_file()
         and not paths["alert_task"].is_symlink()),
        ("VBS 静默 wrapper", paths["vbs"].is_file() and not paths["vbs"].is_symlink()),
        ("venv Python（wrapper 实际调用目标，不可覆盖）",
         paths["venv_python"].is_file()),
    ]
    ok = True
    for name, passed in checks:
        log(f"预检 {name}: {'通过' if passed else '缺失'}")
        ok = ok and passed
    log(f"预检 secret 文件（固定路径 {SECRET_FILE_DISPLAY}，仅存在性，绝不读值）: "
        f"{'存在' if secret_present else '缺失（操作者须先按 M14-135 secret 语义创建）'}")
    ok = ok and secret_present
    if ok:
        wrapper_ok, wrapper_issues = verify_wrapper_content(repo_root)
        log(f"预检 wrapper 内容结构: {'通过' if wrapper_ok else '不通过（' + ', '.join(wrapper_issues) + '）'}")
        ok = ok and wrapper_ok
    return Preflight(ok=ok, secret_present=secret_present, wrapper_ok=wrapper_ok)


# ---------------------------------------------------------------- 子命令


def cmd_plan(*, runner: GatedSchtasks, repo_root: Path, log) -> int:
    """只读预检 + 注册计划输出（零写操作、零调度器改动；fail-fast——
    预检未过即零 schtasks 调用拒绝；同名任务存在即预告拒绝）。"""
    log(f"=== production drift watch alert 计划任务 PLAN（零写操作）: task={TASK_NAME} ===")
    if not preflight(repo_root=repo_root, log=log).ok:
        log("plan 拒绝：预检未全过（见上；fail-closed，零调度器调用）")
        return EXIT_ERROR
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
        f" | StartBoundary={START_BOUNDARY}（与 M14-129 watcher 恒差 5 分钟，槽位互不重叠）"
        f" | InteractiveToken/LeastPrivilege | IgnoreNew"
        f" | StartWhenAvailable=false（错失槽位绝不补跑——下一个固定 PT15M 节点运行）"
        f" | ExecutionTimeLimit={EXECUTION_TIME_LIMIT} | 电池不禁启不停")
    log(f"  Action: {WSCRIPT} //B //Nologo \"<repo>\\tools\\ops\\run_production_drift_watch_alert_silent.vbs\"")
    log("  wrapper 链: VBS → <repo>/.venv python + tools/ops/production_drift_watch_alert_task.py"
        f" --secret-file <repo>/{SECRET_FILE_DISPLAY} --execute"
        ' --confirm "<M14-137 自有确认短语>"（移交 M14-135，全部既有门禁零削弱）')
    log(f"  WorkingDirectory: <repo> = {repo_root}")
    log("  回滚: python tools/ops/production_drift_watch_alert_scheduler.py uninstall"
        " --confirm \"EXECUTE PRODUCTION DRIFT WATCH ALERT SCHEDULER CHANGE\""
        "（仅删本工具精确拥有的任务）")
    log('  实际安装需: install --confirm "EXECUTE PRODUCTION DRIFT WATCH ALERT SCHEDULER CHANGE"'
        "（supervisor 获准窗口；真实 HTTPS 外发仍由 M14-135 既有门禁约束）")
    log("=== plan 结果: OK（可注册；未做任何修改；本回合不注册）===")
    return EXIT_OK


def cmd_generate(*, repo_root: Path, log, out_dir: Path | None = None) -> int:
    """导出任务 XML 到 gitignored 工件目录供审查（零调度器改动）。

    预检（文件身份 + wrapper 内容 + secret 存在性）未过即拒绝且零写入。
    字节编码 = **UTF-16 with BOM**（``text.encode("utf-16")``，与
    build_task_xml 的 ``encoding="UTF-16"`` 声明及 install 临时文件字节
    **完全一致**）——外部解析器（System.Xml 等）可直接加载；M14-14 R2
    实证：UTF-8 字节 + UTF-16 声明会被 XmlDocument 拒载。落盘后**回读
    原始字节**经 decode_schtasks_xml（字节形态严格解码）+ verify_task_xml
    复核——校验的是磁盘上的真实工件而非仅内存文本。"""
    log(f"=== production drift watch alert 计划任务 GENERATE（零调度器改动）: task={TASK_NAME} ===")
    if not preflight(repo_root=repo_root, log=log).ok:
        log("拒绝: 预检未全过（见上；零写入，fail-closed）")
        return EXIT_REFUSED
    directory = out_dir if out_dir is not None else (
        repo_root / ".verify" / "artifacts" / GENERATE_DIR_NAME)
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
    log(f"=== production drift watch alert 计划任务 STATUS（只读）: task={TASK_NAME} ===")
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
    log(f"=== production drift watch alert 计划任务 INSTALL: task={TASK_NAME} ===")
    if not preflight(repo_root=repo_root, log=log).ok:
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
        with tempfile.NamedTemporaryFile(
                "wb", suffix=".xml", prefix="aios-drift-watch-alert-",
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
    log(f"install 成功：task={TASK_NAME}（hidden，间隔 {REPETITION_INTERVAL}、"
        f"StartBoundary {START_BOUNDARY}；本工具所有，Action/cwd/Hidden/触发器逐项复查通过）")
    return EXIT_OK


def cmd_uninstall(*, runner: GatedSchtasks, repo_root: Path, log) -> int:
    """卸载：仅在本工具精确拥有的任务上 ``/Delete /F``；其余状态零删除。

    /F 仅出现在 exact-owned 分支（schtasks /Delete 无 /F 会交互式确认，
    capture 管道无 stdin 时挂起）；foreign/missing/malformed/unknown/查询
    失败一律可见拒绝且零 /Delete（绝不 force、绝不碰他人任务——含
    M14-129 watcher 任务）。"""
    log(f"=== production drift watch alert 计划任务 UNINSTALL: task={TASK_NAME} ===")
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
        prog="production_drift_watch_alert_scheduler.py",
        description="M14-141 production drift watch 告警分发周期计划任务 readiness 管理器"
                    "（plan/generate/status/install/uninstall；install/uninstall 需精确确认短语，"
                    "实际注册 supervisor-only，本开发回合零安装；绝不复用/改动既有 watcher 任务）",
    )
    parser.add_argument("command",
                        choices=["plan", "generate", "status", "install", "uninstall"],
                        help="plan=只读预检（fail-fast，预检不过零调度器调用）；"
                             "generate=导出任务 XML（UTF-16 with BOM，外部解析器可直接加载；"
                             "预检不过零写入；零调度器改动）；status=只读状态；"
                             "install=安装（supervisor 审查后）；uninstall=精确匹配卸载")
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
            print(f"[drift-watch-alert-scheduler] {message}", flush=True)
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
