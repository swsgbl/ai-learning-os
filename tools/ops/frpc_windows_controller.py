#!/usr/bin/env python
"""M14-155 Windows frpc 常驻控制器：preflight / plan / status / install / uninstall。

管理家机侧 frpc 计划任务（Task Scheduler，任务名恒为 ``AIOS-Edge-FRPC``），
配置来源是 public_edge_prepare 渲染出的 ``frpc.windows.toml``。

设计纪律（对齐 tools/ops/windows_startup_task.py 的 M14-06 契约）：
- 纯标准库；平台命令经注入 Runner（测试注入 FakeRunner——绝不触碰真实
  schtasks 的写路径；默认 Runner 也只在本工具显式 ``--execute`` 时才发起
  写命令）；
- preflight/plan 零写入、零网络（``frpc.exe verify`` 只在 plan 里**打印**
  命令，不代跑）；本工具不 SSH、不上传、不启停任何服务/容器；
- 绝不覆盖同名任务：install 前必先只读 query，任务名已存在（无论归属）
  一律拒绝；绝不 /F 创建、绝不 /Run；
- 归属判定 fail-closed：uninstall 只在查询 XML 的 RegistrationInfo/
  Description 与本工具标识精确相等时执行；foreign/missing/malformed/
  查询失败一律不删；**绝不枚举、绝不触碰任何其他任务名**；
- install/uninstall 需 (1) 显式确认短语 ``--confirm-phrase
  INSTALL-AIOS-EDGE-FRPC`` 精确匹配 且 (2) ``--execute``——缺一即只打印
  计划命令（dry-run 语义），不执行任何写命令；
- secret 纪律：token 文件只做结构与存在性校验（复用 prepare 的
  validate_secret_file——0600/UTF-8/长度/非占位；错误只透出类别），绝不
  读取/展示其内容，输出不含任何 secret。

Exit codes：0 = 成功（含 dry-run 计划输出）；1 = 预检失败/拒绝执行；
2 = 用法错误（argparse）；status 专用：0=installed / 1=unknown /
2=missing / 3=foreign / 4=malformed。
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Protocol

import tomllib

try:  # 包导入（pytest）与脚本直跑两种形态
    from tools.ops import public_edge_prepare as prepare
except ImportError:  # python tools/ops/frpc_windows_controller.py
    import public_edge_prepare as prepare

TOOL_NAME = "frpc_windows_controller"
REPO_ROOT = Path(__file__).resolve().parents[2]

TASK_NAME = "AIOS-Edge-FRPC"
TASK_URI_MARKER = "urn:aios:m14-155:edge-frpc-controller"
CONFIRM_PHRASE = "INSTALL-AIOS-EDGE-FRPC"
DNS_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
# schtasks 任务 XML 的默认命名空间（归属判定必须命名空间感知，否则
# 无命名空间 findall 恒空 → 自有任务被误判 foreign）
TASK_XML_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
TASK_NS = f"{{{TASK_XML_NS}}}"

EXIT_OK = 0
EXIT_FAILURE = 1
STATUS_INSTALLED, STATUS_UNKNOWN, STATUS_MISSING, STATUS_FOREIGN, STATUS_MALFORMED = 0, 1, 2, 3, 4


class ControllerError(Exception):
    """预检失败或拒绝执行（fail-closed；文本不含 secret）。"""


class Runner(Protocol):
    def run(self, args: list[str]) -> tuple[int, str]:
        """执行平台命令，返回 (returncode, 合并输出文本)。"""


class RealRunner:
    """真实 Runner：仅捕获输出；Windows 下 CREATE_NO_WINDOW（无弹窗）。"""

    def run(self, args: list[str]) -> tuple[int, str]:
        creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=False, creationflags=creationflags,
        )
        return result.returncode, (result.stdout or "") + (result.stderr or "")


# ---------------------------------------------------------------- preflight


def _require_regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ControllerError(f"{label} 必须是常规文件（存在、非符号链接）: {path.name}")


def preflight_checks(frpc_exe: Path, config_path: Path) -> dict[str, Any]:
    """只读预检：exe/配置/token 文件全部不变量；零写入零网络。"""
    if frpc_exe.name.lower() != "frpc.exe":
        raise ControllerError("--frpc-exe 必须指向名为 frpc.exe 的可执行文件（官方 release）")
    _require_regular_file(frpc_exe, "frpc.exe")
    _require_regular_file(config_path, "frpc 配置")
    try:
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as cause:
        raise ControllerError(f"frpc 配置不可读/非 UTF-8: {type(cause).__name__}") from cause
    except tomllib.TOMLDecodeError as cause:
        raise ControllerError(f"frpc 配置不可解析: {cause}") from cause

    server_addr = str(config.get("serverAddr", ""))
    try:
        ip = ipaddress.ip_address(server_addr)
    except ValueError as cause:
        raise ControllerError(f"serverAddr 必须是公网 IPv4（渲染值不得为占位）: {server_addr!r}") from cause
    reserved_doc = (
        ipaddress.ip_network("192.0.2.0/24"),
        ipaddress.ip_network("198.51.100.0/24"),
        ipaddress.ip_network("203.0.113.0/24"),
    )
    if ip.version != 4 or not ip.is_global or any(ip in net for net in reserved_doc):
        raise ControllerError(f"serverAddr 不是公网 IPv4: {server_addr}")
    if config.get("serverPort") != 7000:
        raise ControllerError("serverPort != 7000（与 frps 控制面约定不符）")
    if config.get("transport", {}).get("tls", {}).get("enable") is not True:
        raise ControllerError("transport.tls.enable != true（TLS 必须显式开启）")
    auth = config.get("auth", {})
    if "token" in auth:
        raise ControllerError("配置含 inline auth.token——token 只允许文件引用（拒绝内联 secret）")
    token_path = str(auth.get("tokenSource", {}).get("file", {}).get("path", ""))
    if auth.get("tokenSource", {}).get("type") != "file" or not token_path:
        raise ControllerError("认证必须来自 tokenSource 文件引用")
    proxies = config.get("proxies", [])
    if not proxies:
        raise ControllerError("配置缺少 proxies")
    for proxy in proxies:
        if proxy.get("type") != "http" or proxy.get("localIP") != "127.0.0.1":
            raise ControllerError("家机侧代理必须是 http 且只回连 127.0.0.1（loopback 后端）")
        for domain in proxy.get("customDomains", []):
            if not DNS_NAME_RE.fullmatch(domain) or domain.endswith(".example.com"):
                raise ControllerError(f"customDomains 残留占位/畸形域名: {domain}")
    # token 文件结构与权限校验（复用 prepare：错误只透出类别，不回显路径/内容）
    prepare.validate_secret_file(token_path, "frpc_token")
    return {
        "frpc_exe": str(frpc_exe),
        "config": str(config_path),
        "server_addr": server_addr,
        "proxies": [p.get("name") for p in proxies],
        "token_file_present": True,
    }


# ---------------------------------------------------------------- schtasks 只读查询与归属


def _query_task_xml(runner: Runner) -> str | None:
    """只读查询本工具精确任务名的 XML；不存在返回 None；失败抛 ControllerError。"""
    code, output = runner.run(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"])
    if code == 0 and output.strip():
        return output
    if code != 0 and ("cannot find" in output.lower() or "找不到" in output):
        return None
    raise ControllerError(f"任务查询失败（不猜测状态）: returncode={code}")


def _classify_task(xml_text: str) -> str:
    """归属判定：Description 与标识精确相等 → owned；XML 畸形 → malformed。

    XXE/实体膨胀防护：解析前拒绝 DOCTYPE/ENTITY（零第三方依赖下与
    windows_startup_task.py M14-06 R4 同款纪律；defusedxml 不引入）。
    """
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        return "malformed"
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return "malformed"
    descriptions = root.findall(f"./{TASK_NS}RegistrationInfo/{TASK_NS}Description")
    if len(descriptions) != 1 or (descriptions[0].text or "").strip() != TASK_URI_MARKER:
        return "foreign"
    actions = root.findall(f"./{TASK_NS}Actions/{TASK_NS}Exec")
    if len(actions) != 1:
        return "malformed"
    return "installed"


def task_status(runner: Runner) -> int:
    xml_text = _query_task_xml(runner)
    if xml_text is None:
        return STATUS_MISSING
    classification = _classify_task(xml_text)
    return {"installed": STATUS_INSTALLED, "foreign": STATUS_FOREIGN,
            "malformed": STATUS_MALFORMED}.get(classification, STATUS_UNKNOWN)


# ---------------------------------------------------------------- 计划命令构造


def planned_commands(facts: dict[str, Any]) -> dict[str, str]:
    """打印用计划命令（不执行）：verify 预检 / schtasks 创建 / 删除。"""
    exe, config = facts["frpc_exe"], facts["config"]
    return {
        "verify": f'"{exe}" verify -c "{config}"',
        "install": (
            f'schtasks /Create /TN "{TASK_NAME}" '
            f'/TR "\\"{exe}\\" -c \\"{config}\\"" /SC ONSTART /RL LIMITED'
        ),
        "uninstall": f'schtasks /Delete /TN "{TASK_NAME}"',
        "query": f'schtasks /Query /TN "{TASK_NAME}" /XML',
    }


INSTALL_XML_TEMPLATE = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{TASK_URI_MARKER}</Description>
    <URI>\\{TASK_NAME}</URI>
  </RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Hidden>false</Hidden>
  </Settings>
  <Actions><Exec>
    <Command>__FRPC_EXE__</Command>
    <Arguments>-c __FRPC_CONFIG__</Arguments>
  </Exec></Actions>
</Task>
"""


def _install_xml(facts: dict[str, Any]) -> str:
    from xml.sax.saxutils import escape

    return (
        INSTALL_XML_TEMPLATE
        .replace("__FRPC_EXE__", escape(facts["frpc_exe"]))
        .replace("__FRPC_CONFIG__", escape(facts["config"]))
    )


# ---------------------------------------------------------------- 子命令


def cmd_preflight(frpc_exe: Path, config_path: Path, log) -> int:
    facts = preflight_checks(frpc_exe, config_path)
    for key, value in facts.items():
        log(f"{key}: {value}")
    log("preflight PASS（零写入零网络；token 文件仅存在性/结构校验，内容不读取）")
    return EXIT_OK


def cmd_plan(frpc_exe: Path, config_path: Path, log) -> int:
    facts = preflight_checks(frpc_exe, config_path)
    log("以下命令均为**计划**（本工具不代跑；真实执行由 supervisor 审查后进行）：")
    for name, command in planned_commands(facts).items():
        log(f"plan[{name}]: {command}")
    log("install 需 --confirm-phrase INSTALL-AIOS-EDGE-FRPC 且 --execute（缺一即 dry-run）")
    return EXIT_OK


def cmd_install(frpc_exe: Path, config_path: Path, *, confirm_phrase: str | None,
                execute: bool, runner: Runner, log) -> int:
    facts = preflight_checks(frpc_exe, config_path)
    if confirm_phrase != CONFIRM_PHRASE:
        log("拒绝执行：缺少精确确认短语（dry-run 计划如下；短语见文档）")
        for name, command in planned_commands(facts).items():
            log(f"plan[{name}]: {command}")
        return EXIT_FAILURE
    status = task_status(runner)
    if status != STATUS_MISSING:
        label = {STATUS_INSTALLED: "已存在（本工具安装）", STATUS_FOREIGN: "已存在（非本工具归属）",
                 STATUS_MALFORMED: "已存在（XML 畸形）", STATUS_UNKNOWN: "状态未知"}.get(status, "?")
        log(f"拒绝安装：任务名 {TASK_NAME} {label}——绝不覆盖同名任务")
        return EXIT_FAILURE
    if not execute:
        log("确认短语正确但未给 --execute：只打印安装计划（零写入）")
        log(f"plan[install-xml]: {_install_xml(facts).splitlines()[1].strip()} ...")
        return EXIT_OK
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as handle:
        handle.write(_install_xml(facts))
        xml_path = handle.name
    code, _output = runner.run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", xml_path])
    try:
        os.unlink(xml_path)
    except OSError:
        pass  # 临时文件清理尽力而为（安装结果以 schtasks 返回码为准）
    if code != 0:
        log(f"安装失败: returncode={code}")
        return EXIT_FAILURE
    log(f"install OK：任务 {TASK_NAME}（LogonTrigger + LeastPrivilege 语义经 XML 显式写入）")
    return EXIT_OK


def cmd_uninstall(*, confirm_phrase: str | None, execute: bool, runner: Runner, log) -> int:
    if confirm_phrase != CONFIRM_PHRASE:
        log("拒绝执行：缺少精确确认短语（uninstall 需确认短语 + --execute）")
        return EXIT_FAILURE
    status = task_status(runner)
    if status == STATUS_MISSING:
        log("任务不存在（幂等：无需卸载）")
        return EXIT_OK
    if status != STATUS_INSTALLED:
        log(f"拒绝卸载：任务非本工具精确归属（status={status}）——绝不删除非自有任务")
        return EXIT_FAILURE
    if not execute:
        log("确认短语正确但未给 --execute：只打印卸载计划（零写入）")
        log(f"plan[uninstall]: schtasks /Delete /TN \"{TASK_NAME}\"")
        return EXIT_OK
    code, _output = runner.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if code != 0:
        log(f"卸载失败: returncode={code}")
        return EXIT_FAILURE
    log("uninstall OK")
    return EXIT_OK


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None, *, runner: Runner | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Windows frpc 常驻控制器（preflight/plan/status/install/uninstall）。"
            "preflight/plan 零写入零网络；install/uninstall 需确认短语"
            f" {CONFIRM_PHRASE} 且 --execute；绝不覆盖/触碰其他任务。"
        ),
    )
    parser.add_argument("command", choices=["preflight", "plan", "status", "install", "uninstall"])
    parser.add_argument("--frpc-exe", type=Path, help="frpc.exe 路径（官方 release）")
    parser.add_argument("--config", type=Path, help="渲染产物 frpc.windows.toml 路径")
    parser.add_argument("--confirm-phrase", help=f"执行确认短语（恒为 {CONFIRM_PHRASE}）")
    parser.add_argument("--execute", action="store_true",
                        help="真实执行写命令（默认 dry-run：只打印计划）")
    args = parser.parse_args(argv)

    def log(message: str) -> None:
        print(f"[{TOOL_NAME}] {message}", flush=True)

    if args.command in ("preflight", "plan", "install") and (not args.frpc_exe or not args.config):
        parser.error(f"{args.command} 需要 --frpc-exe 与 --config")
    if runner is None:
        runner = RealRunner()

    try:
        if args.command == "preflight":
            return cmd_preflight(args.frpc_exe, args.config, log)
        if args.command == "plan":
            return cmd_plan(args.frpc_exe, args.config, log)
        if args.command == "status":
            status = task_status(runner)
            label = {0: "installed", 1: "unknown", 2: "missing", 3: "foreign", 4: "malformed"}[status]
            log(f"status: {label}")
            return status
        if args.command == "install":
            return cmd_install(args.frpc_exe, args.config, confirm_phrase=args.confirm_phrase,
                               execute=args.execute, runner=runner, log=log)
        return cmd_uninstall(confirm_phrase=args.confirm_phrase, execute=args.execute,
                             runner=runner, log=log)
    except ControllerError as cause:
        print(f"[{TOOL_NAME}] FAIL: {cause}", file=sys.stderr)
        return EXIT_FAILURE
    except prepare.PrepareError as cause:
        print(f"[{TOOL_NAME}] FAIL: token 文件校验未过: {cause}", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
