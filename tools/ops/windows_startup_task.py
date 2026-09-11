#!/usr/bin/env python
"""M14-06 Round 2/3：Windows 登录自愈计划任务管理器。

管理隐藏 logon startup task（Task Scheduler），把 tools/ops/production_recovery.py
经静默 VBS wrapper（run_production_recovery_silent.vbs）挂到当前用户登录触发。
子命令：``dry-run``（只读预检+计划输出）/ ``install`` / ``status`` / ``uninstall``。

设计纪律（与 production_recovery.py 同源）：
- 纯标准库；平台命令经注入 Runner（复用 production_recovery.RealRunner：
  capture + Windows 侧恒 CREATE_NO_WINDOW，无弹窗）；测试注入 FakeRunner——
  绝不触碰真实 schtasks 的写路径。
- 绝不覆盖同名任务：install 前必先只读 query，已存在（无论归属）一律拒绝；
  install 绝不 /F、绝不 /Run（源码契约测试锁定）。uninstall 的 /Delete 仅在
  归属标识+全部关键字段 exact-owned 时附带 /F（schtasks 无 /F 会交互式确认，
  capture 管道下挂起）；foreign/missing/malformed/unknown/查询失败绝不
  force、绝不删除。
- wrapper 调用目标一致（fail-closed）：VBS 固定调用
  ``<repo>/.venv/Scripts/python.exe``；本工具**不提供 --python 覆盖**——
  preflight/install/dry-run 恒检查 ``_repo_paths(repo_root)["venv_python"]``，
  feature worktree 无 .venv 即拒绝（外部 python 路径无法使 dry-run/install
  成功）。
- 归属判定（fail-closed，逐项精确；R3 适配 Task Scheduler 归一化）：任务 XML
  的 RegistrationInfo/URI 为本工具标识之一——写入值
  ``urn:aios:m14-06:production-recovery`` 或 schtasks 注册后的归一化值
  ``\\<TASK_NAME>``（生产实证：Task Scheduler 重写 URI 为任务路径，**该形式
  对任何同名任务都不具区分性**，故不可单独作归属凭据）；持久归属标记为
  RegistrationInfo/Description（COM 快照实证逐字保留），须与
  ``TASK_DESCRIPTION`` 精确相等。其余逐项精确：Actions/Exec 的 Command/
  Arguments/WorkingDirectory（缺失即 mismatch，不得短路）、Settings/Hidden、
  MultipleInstancesPolicy=IgnoreNew、StartWhenAvailable、电池双 false、
  ExecutionTimeLimit、Principal/LogonType=InteractiveToken。
- 归一化省略的默认值元素（R3）：Task Scheduler 存储时省略「值恰为 Windows
  默认值」的元素——LogonTrigger/Enabled、Settings/Enabled、Principal/
  RunLevel（生产 + COM 快照实证）。仅在（a）父节点/触发器类型存在、（b）
  其余全部必检字段精确在场且匹配时，省略才按 Windows 默认值认可；显式
  非默认值（如 Enabled=false / RunLevel=HighestAvailable）仍逐项拒绝。
- /XML 解码（R3）：schtasks /Query /XML 管道输出实测 UTF-16LE **无 BOM**
  （text-mode encoding='utf-16' 在读线程抛 UnicodeError 且丢输出）；一律经
  ``runner.run_raw`` 取原始字节 + ``decode_schtasks_xml`` 鲁棒解码（UTF-16LE
  带/不带 BOM，兼容 BE BOM）；解码失败按 unknown fail-closed，绝不猜。
- XML 生成加固：repo/VBS 路径与 Arguments 经 xml.sax.saxutils.escape 转义
  （&、<、>、"）——含特殊字符的路径仍产出可解析 XML，解析后字段精确还原。
- XML 解析加固（安全评审）：解析前拒绝 DOCTYPE/ENTITY（XXE/实体膨胀防护），
  命中即按 malformed fail-closed。
- secret 纪律：pin env（infra/env.production-recovery）只做**存在性**检查，
  绝不读取/展示其值；本工具自身不产生、不落盘任何 secret。

Task XML 关键设置（install 写入、status 逐项校验）：
  Hidden=true；LogonTrigger(Enabled)；Principal LogonType=InteractiveToken
  （当前用户）/ RunLevel=LeastPrivilege；MultipleInstancesPolicy=IgnoreNew；
  StartWhenAvailable=true；ExecutionTimeLimit=PT2H；电池不禁启不停
  （DisallowStartIfOnBatteries=false / StopIfGoingOnBatteries=false）；
  Action=wscript.exe，Arguments=``//B //Nologo "<repo>\\tools\\ops\\
  run_production_recovery_silent.vbs"``；WorkingDirectory=<repo>。

退出码：dry-run 0=可安装（结构预检过且无同名任务）；install 0=成功；status
0=installed / 1=查询失败(unknown) / 2=missing / 3=foreign / 4=malformed；
uninstall 0=已删或本就无任务（幂等），其余可见拒绝非 0。

用法（仓库根；install 需提升令牌——生产实证 schtasks /Create 仅在 elevated
token 下成功，UAC 拒绝非提升创建）：
  python tools/ops/windows_startup_task.py dry-run
  python tools/ops/windows_startup_task.py status
  python tools/ops/windows_startup_task.py install
  python tools/ops/windows_startup_task.py uninstall
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

OPS_DIR = Path(__file__).resolve().parent
REPO_ROOT = OPS_DIR.parent.parent

TASK_NAME = "AIOS-Production-Recovery"
TASK_URI = "urn:aios:m14-06:production-recovery"
#: schtasks /Create 注册后 Task Scheduler 把 RegistrationInfo/URI 重写为任务
#: 路径形式（2026-09-11 生产 + COM 快照实证）。该形式对任意同名任务都会出现，
#: 不具归属区分性——仅作为「本任务名下」的已归一化形态接受，且必须叠加
#: Description 持久标记 + 全部字段精确匹配才构成 exact-owned。
NORMALIZED_TASK_URI = f"\\{TASK_NAME}"
OWNED_TASK_URIS = frozenset({TASK_URI, NORMALIZED_TASK_URI})
#: 持久归属标记：Task Scheduler 归一化存储逐字保留 Description（COM 快照
#: 实证）——比对须精确相等，任何漂移/缺失按 malformed 拒绝管理。
TASK_DESCRIPTION = (
    "AIOS production recovery (M14-06) hidden logon self-heal; "
    "managed by tools/ops/windows_startup_task.py - do not edit by hand"
)
EXECUTION_TIME_LIMIT = "PT2H"
WSCRIPT = "wscript.exe"
WSCRIPT_ARGS_TEMPLATE = '//B //Nologo "{vbs}"'
#: 任务存在性判定不依赖本地化错误文案：schtasks 的错误输出是 OEM 代码页
#: （zh-CN 为 GBK），UTF-16 强解会在读线程炸掉且丢失输出（2026-09-11 实证）。
#: 改用全量列表（/Query /FO CSV /NH，任务名为 ASCII，跨代码页稳定）判断
#: 存在性；/XML 明细仅在任务存在时取**原始字节**（runner.run_raw）并经
#: decode_schtasks_xml 鲁棒解码（UTF-16LE 带/不带 BOM，R3 生产实证无 BOM）。
TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_MISSING = 2
EXIT_FOREIGN = 3
EXIT_MALFORMED = 4

STATE_INSTALLED = "installed"
STATE_MISSING = "missing"
STATE_FOREIGN = "foreign"
STATE_MALFORMED = "malformed"
STATE_UNKNOWN = "unknown"


def _load_recovery_module() -> object:
    """加载同目录 production_recovery（复用 RealRunner/CommandResult）。"""
    spec = importlib.util.spec_from_file_location("production_recovery_shared",
                                                  OPS_DIR / "production_recovery.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pr = _load_recovery_module()  # type: ignore[assignment]


def _repo_paths(repo_root: Path) -> dict[str, Path]:
    return {
        "recovery": repo_root / "tools" / "ops" / "production_recovery.py",
        "vbs": repo_root / "tools" / "ops" / "run_production_recovery_silent.vbs",
        "pin_env": repo_root / "infra" / "env.production-recovery",
        "venv_python": repo_root / ".venv" / "Scripts" / "python.exe",
    }


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
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
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
#: Description 是持久归属标记（Task Scheduler 逐字保留，COM 快照实证）。
_STRICT_EXACT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("t:RegistrationInfo/t:Description", TASK_DESCRIPTION, "RegistrationInfo/Description"),
    ("t:Settings/t:Hidden", "true", "Settings/Hidden"),
    ("t:Settings/t:MultipleInstancesPolicy", "IgnoreNew", "Settings/MultipleInstancesPolicy"),
    ("t:Settings/t:StartWhenAvailable", "true", "Settings/StartWhenAvailable"),
    ("t:Settings/t:DisallowStartIfOnBatteries", "false", "Settings/DisallowStartIfOnBatteries"),
    ("t:Settings/t:StopIfGoingOnBatteries", "false", "Settings/StopIfGoingOnBatteries"),
    ("t:Settings/t:ExecutionTimeLimit", EXECUTION_TIME_LIMIT, "Settings/ExecutionTimeLimit"),
    ("t:Principals/t:Principal/t:LogonType", "InteractiveToken", "Principals/Principal/LogonType"),
)

#: Task Scheduler 归一化会省略「值恰为 Windows 默认值」的元素（R3 生产 +
#: COM 快照实证）。每项 (xpath, 默认期望值, 字段名, 父节点 xpath)：省略仅在
#: （a）父节点/触发器类型存在、（b）其余全部必检字段（_STRICT_EXACT_FIELDS
#: + Action 三件套 + 触发器节点）精确在场且匹配时，才按 Windows 默认值认可；
#: 显式非默认值、或其余字段有任何缺失/漂移时，一律计入 mismatch——省略的
#: 默认值假设永不独立成立，绝不放行「其余事实不全」的任务。
_DEFAULTABLE_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("t:Triggers/t:LogonTrigger/t:Enabled", "true",
     "Triggers/LogonTrigger/Enabled", "t:Triggers/t:LogonTrigger"),
    ("t:Settings/t:Enabled", "true", "Settings/Enabled", "t:Settings"),
    ("t:Principals/t:Principal/t:RunLevel", "LeastPrivilege",
     "Principals/Principal/RunLevel", "t:Principals/t:Principal"),
)


def verify_task_xml(xml_text: str, repo_root: Path) -> tuple[str, list[str]]:
    """校验任务 XML 是否本工具精确拥有。返回 (state, 不匹配字段名列表)。

    state：installed（归属标识（URI 两种形态之一 + Description 持久标记 +
    Command）+ Action 三件套 + 全部关键设置 + 触发器 + Principal 逐项精确
    匹配；R3：归一化省略的三项默认值元素按上述条件认可）/ foreign（URI 非
    两种形态之一，或 Command 指向别处）/ malformed（不可解析/含实体定义/
    归属标识匹配但任一字段缺失或漂移——含 WorkingDirectory 等元素缺失，
    缺失即 mismatch，不得短路）。返回信息只含字段名，绝不回显系统侧字段值。
    """
    text = xml_text.strip().lstrip("﻿")
    lowered = text.lower()
    if "<!doctype" in lowered or "<!entity" in lowered:
        # 安全加固：拒绝实体定义（XXE/实体膨胀面）——按损坏 fail-closed
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
    # Action 三件套：Arguments 精确匹配（含 //B //Nologo 静默旗标）；
    # WorkingDirectory 必须存在且 resolve 后等于 repo root——缺失即 mismatch。
    if text_of("t:Actions/t:Exec/t:Arguments") != expected_args:
        issues.append("Actions/Exec/Arguments")
    working_dir = text_of("t:Actions/t:Exec/t:WorkingDirectory")
    if working_dir is None or Path(working_dir).resolve() != repo_root.resolve():
        issues.append("Actions/Exec/WorkingDirectory")
    # 触发器类型必须在场（LogonTrigger 节点存在）。
    if root.find("t:Triggers/t:LogonTrigger", TASK_NS) is None:
        issues.append("Triggers/LogonTrigger")
    # 归属关键字段逐项精确匹配（缺失 None ≠ 期望值，自然计入）。
    for xpath, expected, field_name in _STRICT_EXACT_FIELDS:
        if text_of(xpath) != expected:
            issues.append(field_name)
    # 归一化省略的三项：仅当其余事实（上面全部检查）精确无 issue 且父节点/
    # 触发器类型在场时，省略才按 Windows 默认值认可；否则计 mismatch。
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
        return STATE_MALFORMED, issues  # 归属标识匹配但字段缺失/漂移——拒绝管理
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

    生产实证（2026-09-11，canonical 安装后回读）：管道输出为 UTF-16LE
    **无 BOM**——text-mode ``encoding='utf-16'`` 依赖 BOM/偶数分块，在读线程
    抛 UnicodeError 且输出丢失（status 误判 unknown 的根因之一）。本函数按
    字节面判定：LE BOM → 去 BOM 按 UTF-16LE；BE BOM → 去 BOM 按 UTF-16BE
    （兼容）；无 BOM → 按 UTF-16LE 严格解（奇长度/孤代理即 ValueError）。
    解码成功但首字符非 ``<``（如 OEM 错误文案被强解成的乱码）同样 ValueError
    ——调用方按事实不完整（unknown）fail-closed，绝不猜编码、绝不弱解放行。
    """
    if data.startswith(b"\xff\xfe"):
        text = data[2:].decode("utf-16-le")
    elif data.startswith(b"\xfe\xff"):
        text = data[2:].decode("utf-16-be")
    else:
        text = data.decode("utf-16-le")
    text = text.strip()
    if not text.startswith("<"):
        raise ValueError("解码成功但非 XML 形态（首字符非 '<'）")
    return text


def _run_schtasks(runner, argv: list[str], *, timeout: float = 30.0):
    return runner.run(argv, timeout=timeout)


def _task_listed(runner, task_name: str) -> bool | None:
    """全量列表存在性判定（只读；任务名 ASCII，跨代码页稳定）。

    返回 None = 列表查询本身失败（事实不完整，调用方 fail-closed）。
    """
    result = _run_schtasks(runner, ["schtasks.exe", "/Query", "/FO", "CSV", "/NH"],
                           timeout=60.0)
    if result.returncode != 0:
        return None
    text = result.stdout
    return rf'"\{task_name}"' in text or f'"{task_name}"' in text


def query_task(runner, repo_root: Path, task_name: str = TASK_NAME) -> TaskQuery:
    """只读查询：先列表判存在（编码无关），存在才取 /XML 明细（原始字节+鲁棒解码）。"""
    listed = _task_listed(runner, task_name)
    if listed is None:
        return TaskQuery(STATE_UNKNOWN, ("schtasks /Query 全量列表失败（事实不完整）",))
    if not listed:
        return TaskQuery(STATE_MISSING)
    argv = ["schtasks.exe", "/Query", "/TN", task_name, "/XML"]
    try:
        result = runner.run_raw(argv, timeout=30.0)
    except pr.RunnerError:  # type: ignore[attr-defined]
        return TaskQuery(STATE_UNKNOWN, ("任务在列表中但 /XML 明细查询失败（事实不完整）",))
    if result.returncode != 0 or not result.stdout.strip():
        return TaskQuery(STATE_UNKNOWN, ("任务在列表中但 /XML 明细不可读（事实不完整）",))
    try:
        xml_text = decode_schtasks_xml(result.stdout)
    except ValueError:
        return TaskQuery(STATE_UNKNOWN,
                         ("任务在列表中但 /XML 输出无法按 UTF-16LE（带/不带 BOM）鲁棒解码（事实不完整）",))
    state, issues = verify_task_xml(xml_text, repo_root)
    return TaskQuery(state, tuple(issues), xml_text)


def _confirm_missing(runner, task_name: str) -> bool:
    """二次存在性复核（install 预检用）：全量列表不含任务名才确认 missing。"""
    return _task_listed(runner, task_name) is False


# ---------------------------------------------------------------- 预检

@dataclass
class Preflight:
    ok: bool
    env_present: bool
    lines: list[str] = field(default_factory=list)


def preflight(*, repo_root: Path, log, require_env: bool) -> Preflight:
    """结构预检（只读；pin env 仅存在性检查，绝不读取值）。

    venv Python 恒取 ``_repo_paths(repo_root)["venv_python"]``——与 VBS wrapper
    实际调用目标一致；本工具不提供 --python 覆盖，repo 无 .venv 即 fail-closed
    （dry-run/install 均拒绝，外部 python 路径无法使其成功）。
    """
    paths = _repo_paths(repo_root)
    env_present = paths["pin_env"].is_file()
    checks = [
        ("repo 根目录", repo_root.is_dir()),
        ("恢复编排脚本", paths["recovery"].is_file()),
        ("VBS 静默 wrapper", paths["vbs"].is_file()),
        ("venv Python（wrapper 实际调用目标，不可覆盖）", paths["venv_python"].is_file()),
    ]
    ok = True
    for name, passed in checks:
        log(f"预检 {name}: {'通过' if passed else '缺失'}")
        ok = ok and passed
    if require_env:
        log(f"预检 pin env 文件（存在性，不读值）: {'存在' if env_present else '缺失'}")
        ok = ok and env_present
    else:
        log(f"预检 pin env 文件（存在性，不读值）: {'存在' if env_present else '缺失（install 前须创建；recovery 自身 fail-closed 兜底）'}")
    return Preflight(ok=ok, env_present=env_present)


# ---------------------------------------------------------------- 子命令

def cmd_dry_run(*, runner, repo_root: Path, log) -> int:
    """只读预检 + 安装计划输出（零写操作；同名任务存在即预告拒绝）。

    预检含 ``<repo>/.venv/Scripts/python.exe``（wrapper 实际调用目标）——
    feature worktree 无 .venv 时 fail-closed（exit 1），不输出「OK 可安装」。
    """
    log(f"=== startup task DRY-RUN（零写操作）: task={TASK_NAME} ===")
    pre = preflight(repo_root=repo_root, log=log, require_env=False)
    query = query_task(runner, repo_root)
    if query.state == STATE_INSTALLED:
        log("任务状态: installed（本工具所有且匹配）——install 将拒绝覆盖（幂等无需操作）")
        return EXIT_ERROR
    if query.exists:  # foreign / malformed
        log(f"任务状态: {query.state}（不匹配字段: {', '.join(query.issues) or '归属不符'}）"
            f"——同名任务存在，install 将拒绝（绝不覆盖）")
        return EXIT_ERROR
    if query.state == STATE_UNKNOWN:
        log(f"任务查询事实不完整（{'; '.join(query.issues)}）——fail-closed，不做任何预判")
        return EXIT_ERROR
    log("任务状态: missing——install 计划如下（未执行）：")
    log(f"  schtasks.exe /Create /TN {TASK_NAME} /XML <临时文件（UTF-16，用后即删）>")
    log("  关键设置: Hidden=true | LogonTrigger | InteractiveToken/LeastPrivilege | "
        "IgnoreNew | StartWhenAvailable | ExecutionTimeLimit=PT2H | 电池不禁启不停")
    log(f"  Action: {WSCRIPT} //B //Nologo \"<repo>\\tools\\ops\\run_production_recovery_silent.vbs\"")
    log(f"  WorkingDirectory: <repo> = {repo_root}")
    log("  回滚: python tools/ops/windows_startup_task.py uninstall（仅删本工具精确拥有的任务）")
    if not pre.ok:
        log("预检未全过（见上）——install 将拒绝；dry-run 仍输出计划供审查")
        return EXIT_ERROR
    log("=== dry-run 结果: OK（可安装；未做任何修改）===")
    return EXIT_OK


def cmd_install(*, runner, repo_root: Path, log) -> int:
    """真实安装（本轮禁止人工执行；预检+无同名+临时 XML+复查全过才 /Create）。

    预检恒含 repo 自带 .venv Python（与 wrapper 调用目标一致，无覆盖面）。
    """
    log(f"=== startup task INSTALL: task={TASK_NAME} ===")
    pre = preflight(repo_root=repo_root, log=log, require_env=True)
    if not pre.ok:
        log("install 拒绝：预检未全过（见上；字段名-only，不回显值）")
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
        with tempfile.NamedTemporaryFile("wb", suffix=".xml", prefix="aios-logon-recovery-",
                                         delete=False) as handle:
            handle.write(xml_text.encode("utf-16"))
            tmp_path = Path(handle.name)
        created = _run_schtasks(runner, ["schtasks.exe", "/Create", "/TN", TASK_NAME,
                                         "/XML", str(tmp_path)], timeout=60.0)
        if created.returncode != 0:
            log(f"install 失败：schtasks /Create rc={created.returncode}")
            log((created.stderr or created.stdout).strip()[:300])
            return EXIT_ERROR
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)  # 临时 XML 无 secret，仍即弃
    verify = query_task(runner, repo_root)
    if verify.state != STATE_INSTALLED:
        log(f"install 后复查不符（state={verify.state}, 字段: {', '.join(verify.issues)}）——请 uninstall 后重试")
        return EXIT_ERROR
    log(f"install 成功：task={TASK_NAME}（hidden logon；本工具所有，Action/cwd/Hidden 逐项复查通过）")
    return EXIT_OK


def cmd_status(*, runner, repo_root: Path, log) -> int:
    """只读状态：installed/missing/foreign/malformed 四态 + 字段级校验。"""
    log(f"=== startup task STATUS（只读）: task={TASK_NAME} ===")
    query = query_task(runner, repo_root)
    if query.state == STATE_INSTALLED:
        log("状态: installed——本工具所有，Action/参数/cwd/Hidden/触发器/时限逐项匹配")
        return EXIT_OK
    if query.state == STATE_MISSING:
        log("状态: missing——未注册（Round 2 不安装，符合预期）")
        return EXIT_MISSING
    if query.state == STATE_FOREIGN:
        log("状态: foreign——同名任务不属本工具（字段: RegistrationInfo/URI、Actions/Exec/Command）")
        return EXIT_FOREIGN
    if query.state == STATE_MALFORMED:
        log(f"状态: malformed——归属标识匹配但字段缺失/漂移或 XML 不可解析（字段: {', '.join(query.issues)}）")
        return EXIT_MALFORMED
    log(f"状态: unknown——查询事实不完整（{'; '.join(query.issues)}），fail-closed")
    return EXIT_ERROR


def cmd_uninstall(*, runner, repo_root: Path, log) -> int:
    """卸载：仅在本工具精确拥有的任务上 ``/Delete /F``；其余状态零删除。

    /F 仅出现在 exact-owned 分支——STATE_INSTALLED 意味着归属标识（URI 两种
    形态之一 + Description 持久标记 + Command）与全部关键字段（Arguments/
    WorkingDirectory/Settings 各项/触发器/Principal；R3 含归一化省略的默认
    值条件认可）逐项精确匹配；foreign / missing / malformed / unknown /
    查询失败一律可见拒绝且零 /Delete（绝不 force、绝不碰他人任务）。附 /F
    的原因：schtasks /Delete 无 /F 会交互式确认，capture 管道无 stdin 时挂起。
    """
    log(f"=== startup task UNINSTALL: task={TASK_NAME} ===")
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
    removed = _run_schtasks(runner,
                            ["schtasks.exe", "/Delete", "/TN", TASK_NAME, "/F"],
                            timeout=60.0)
    if removed.returncode != 0:
        log(f"卸载失败：schtasks /Delete rc={removed.returncode}")
        return EXIT_ERROR
    log(f"卸载成功：task={TASK_NAME} 已删除（仅此一项，无级联；exact-owned /F）")
    return EXIT_OK


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="windows_startup_task.py",
        description="M14-06 隐藏 logon 自愈任务管理器（dry-run/install/status/uninstall；绝不覆盖同名任务）",
    )
    parser.add_argument("command", choices=["dry-run", "install", "status", "uninstall"],
                        help="dry-run=只读预检；install=安装（supervisor 审查后执行）；"
                             "status=只读状态；uninstall=精确匹配卸载")
    # 刻意不提供 --python 覆盖：venv 检查目标恒为 wrapper 实际调用的
    # <repo>/.venv/Scripts/python.exe（见 preflight），外部 python 路径不得
    # 使 feature worktree（无 .venv）的 dry-run/install 通过。
    return parser


def main(argv: list[str] | None = None, *, runner=None, log=None) -> int:
    args = build_parser().parse_args(argv)
    if log is None:
        def log(message: str) -> None:
            print(f"[startup-task] {message}", flush=True)
    if runner is None:
        runner = pr.RealRunner()  # type: ignore[attr-defined]
    handlers = {
        "dry-run": lambda: cmd_dry_run(runner=runner, repo_root=REPO_ROOT, log=log),
        "install": lambda: cmd_install(runner=runner, repo_root=REPO_ROOT, log=log),
        "status": lambda: cmd_status(runner=runner, repo_root=REPO_ROOT, log=log),
        "uninstall": lambda: cmd_uninstall(runner=runner, repo_root=REPO_ROOT, log=log),
    }
    return handlers[args.command]()


if __name__ == "__main__":
    sys.exit(main())
