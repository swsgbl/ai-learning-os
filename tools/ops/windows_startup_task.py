#!/usr/bin/env python
"""M14-06 Round 2：Windows 登录自愈计划任务管理器（开发-only；本轮不注册任何真实任务）。

管理隐藏 logon startup task（Task Scheduler），把 tools/ops/production_recovery.py
经静默 VBS wrapper（run_production_recovery_silent.vbs）挂到当前用户登录触发。
子命令：``dry-run``（只读预检+计划输出）/ ``install`` / ``status`` / ``uninstall``。

设计纪律（与 production_recovery.py 同源）：
- 纯标准库；平台命令经注入 Runner（复用 production_recovery.RealRunner：
  capture + Windows 侧恒 CREATE_NO_WINDOW，无弹窗）；测试注入 FakeRunner——
  绝不触碰真实 schtasks 的写路径。
- 绝不覆盖同名任务：install 前必先只读 query，已存在（无论归属）一律拒绝；
  install 绝不 /F、绝不 /Run（源码契约测试锁定）。uninstall 的 /Delete 仅在
  URI+全部归属关键字段 exact-owned 时附带 /F（schtasks 无 /F 会交互式确认，
  capture 管道下挂起）；foreign/missing/malformed/unknown/查询失败绝不
  force、绝不删除。
- wrapper 调用目标一致（fail-closed）：VBS 固定调用
  ``<repo>/.venv/Scripts/python.exe``；本工具**不提供 --python 覆盖**——
  preflight/install/dry-run 恒检查 ``_repo_paths(repo_root)["venv_python"]``，
  feature worktree 无 .venv 即拒绝（外部 python 路径无法使 dry-run/install
  成功）。
- 归属判定（fail-closed，逐项精确）：任务 XML 的 RegistrationInfo/URI 为本
  工具标识（``urn:aios:m14-06:production-recovery``）；以下字段缺失或漂移
  均按字段名报告并拒绝管理——Actions/Exec 的 Command/Arguments/
  WorkingDirectory（缺失即 mismatch，不得短路）、Settings/Hidden、
  Settings/Enabled、MultipleInstancesPolicy=IgnoreNew、StartWhenAvailable、
  DisallowStartIfOnBatteries/StopIfGoingOnBatteries（双 false）、
  ExecutionTimeLimit、LogonTrigger(Enabled=true)、Principal/LogonType=
  InteractiveToken、Principal/RunLevel=LeastPrivilege。
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

用法（仓库根；真实 install 由 supervisor 审查后另行执行）：
  python tools/ops/windows_startup_task.py dry-run
  python tools/ops/windows_startup_task.py status
  python tools/ops/windows_startup_task.py install      # 本轮禁止真实执行
  python tools/ops/windows_startup_task.py uninstall    # 本轮禁止真实执行
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
EXECUTION_TIME_LIMIT = "PT2H"
WSCRIPT = "wscript.exe"
WSCRIPT_ARGS_TEMPLATE = '//B //Nologo "{vbs}"'
#: 任务存在性判定不依赖本地化错误文案：schtasks 的错误输出是 OEM 代码页
#: （zh-CN 为 GBK），UTF-16 强解会在读线程炸掉且丢失输出（2026-09-11 实证）。
#: 改用全量列表（/Query /FO CSV /NH，任务名为 ASCII，跨代码页稳定）判断
#: 存在性；/XML 明细仅在任务存在时以 UTF-16（带 BOM）读取。
SCHTASKS_XML_ENCODING = "utf-16"
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
    <Description>AIOS production recovery (M14-06) hidden logon self-heal; managed by tools/ops/windows_startup_task.py - do not edit by hand</Description>
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


def verify_task_xml(xml_text: str, repo_root: Path) -> tuple[str, list[str]]:
    """校验任务 XML 是否本工具精确拥有。返回 (state, 不匹配字段名列表)。

    state：installed（URI+Command+Arguments+WorkingDirectory+Settings 全部
    关键项+触发器+Principal 逐项精确匹配）/ foreign（URI 或 Command 指向
    别处）/ malformed（不可解析/含实体定义/我们的 URI 但任一字段缺失或
    漂移——含 WorkingDirectory 等元素缺失，缺失即 mismatch，不得短路）。
    返回信息只含字段名，绝不回显系统侧字段值。
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
    if uri != TASK_URI or command != WSCRIPT:
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
    # 触发器：节点存在且 Enabled=true（缺失节点与漂移分别报告字段名）。
    if root.find("t:Triggers/t:LogonTrigger", TASK_NS) is None:
        issues.append("Triggers/LogonTrigger")
    # 归属关键字段逐项精确匹配（缺失 None ≠ 期望值，自然计入）。
    exact_fields: tuple[tuple[str, str, str], ...] = (
        ("t:Triggers/t:LogonTrigger/t:Enabled", "true", "Triggers/LogonTrigger/Enabled"),
        ("t:Settings/t:Enabled", "true", "Settings/Enabled"),
        ("t:Settings/t:Hidden", "true", "Settings/Hidden"),
        ("t:Settings/t:MultipleInstancesPolicy", "IgnoreNew", "Settings/MultipleInstancesPolicy"),
        ("t:Settings/t:StartWhenAvailable", "true", "Settings/StartWhenAvailable"),
        ("t:Settings/t:DisallowStartIfOnBatteries", "false", "Settings/DisallowStartIfOnBatteries"),
        ("t:Settings/t:StopIfGoingOnBatteries", "false", "Settings/StopIfGoingOnBatteries"),
        ("t:Settings/t:ExecutionTimeLimit", EXECUTION_TIME_LIMIT, "Settings/ExecutionTimeLimit"),
        ("t:Principals/t:Principal/t:LogonType", "InteractiveToken", "Principals/Principal/LogonType"),
        ("t:Principals/t:Principal/t:RunLevel", "LeastPrivilege", "Principals/Principal/RunLevel"),
    )
    for xpath, expected, field_name in exact_fields:
        if text_of(xpath) != expected:
            issues.append(field_name)
    if issues:
        return STATE_MALFORMED, issues  # 我们的 URI 但字段缺失/漂移——拒绝管理
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


def _run_schtasks(runner, argv: list[str], *, encoding: str | None = None, timeout: float = 30.0):
    if encoding:
        return runner.run(argv, timeout=timeout, encoding=encoding)
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
    """只读查询：先列表判存在（编码无关），存在才取 /XML 明细（UTF-16）。"""
    listed = _task_listed(runner, task_name)
    if listed is None:
        return TaskQuery(STATE_UNKNOWN, ("schtasks /Query 全量列表失败（事实不完整）",))
    if not listed:
        return TaskQuery(STATE_MISSING)
    result = _run_schtasks(runner, ["schtasks.exe", "/Query", "/TN", task_name, "/XML"],
                           encoding=SCHTASKS_XML_ENCODING)
    if result.returncode != 0 or not result.stdout.strip():
        return TaskQuery(STATE_UNKNOWN, ("任务在列表中但 /XML 明细不可读（事实不完整）",))
    state, issues = verify_task_xml(result.stdout, repo_root)
    return TaskQuery(state, tuple(issues), result.stdout)


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
        log(f"状态: malformed——URI 是本工具但字段漂移或 XML 不可解析（字段: {', '.join(query.issues)}）")
        return EXIT_MALFORMED
    log(f"状态: unknown——查询事实不完整（{'; '.join(query.issues)}），fail-closed")
    return EXIT_ERROR


def cmd_uninstall(*, runner, repo_root: Path, log) -> int:
    """卸载：仅在本工具精确拥有的任务上 ``/Delete /F``；其余状态零删除。

    /F 仅出现在 exact-owned 分支——STATE_INSTALLED 意味着 URI 与全部归属
    关键字段（Arguments/WorkingDirectory/Settings 八项/触发器/Principal）
    逐项精确匹配；foreign / missing / malformed / unknown / 查询失败一律
    可见拒绝且零 /Delete（绝不 force、绝不碰他人任务）。附 /F 的原因：
    schtasks /Delete 无 /F 会交互式确认，capture 管道无 stdin 时挂起。
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
