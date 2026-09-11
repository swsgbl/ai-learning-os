r"""M14-06 Round 2/3（R2.1 + R3 修正）契约测试：windows_startup_task.py + 静默 VBS wrapper。

零真实系统触碰：全部经 FakeSchtasks 注入——绝不调用真实 schtasks 写路径、
不注册/删除/运行任何任务、不碰 Docker/8010/8011。

覆盖：
- Task XML 关键项逐项断言（Hidden/LogonTrigger(Enabled)/Settings/Enabled/
  InteractiveToken/LeastPrivilege/IgnoreNew/StartWhenAvailable/PT2H/电池不禁
  启停/wscript //B //Nologo + 仓库内 wrapper 路径/WorkingDirectory=repo/
  URI 标识 + Description 持久归属标记）；
- XML 转义（R2.1 缺陷 2）：repo/VBS 路径含 &、<、>、" 时 XML 仍可解析、
  解析后字段精确还原、verify round-trip 仍 installed；
- verify_task_xml 四态（R2.1 缺陷 3/4；R3 归一化适配）：installed（round-trip
  及 Task Scheduler 归一化形态）/ foreign（URI 非两种形态之一，或 Command
  别处）/ malformed（归属标识匹配但任一字段缺失或漂移——含 WorkingDirectory
  等元素缺失即 mismatch；XML 垃圾；DOCTYPE/ENTITY 拒绝），且缺失/漂移逐项
  报告字段名；
- R3 真实回读回归：/XML 输出 UTF-16LE **无 BOM**（生产实证形态）/ 带 BOM /
  BE BOM 均解码；OEM 乱码/奇数字节 → unknown fail-closed + 零删除；Task
  Scheduler 归一化（URI 重写为 \\<TaskName>、默认值元素省略、新增 UserId/
  IdleSettings 等额外元素）仍判 exact-owned；归一化 URI 单独不构成归属
  （Description 漂移/缺失 → 拒绝）；省略默认值仅在其余字段全部精确在场时
  认可（其余字段漂移时省略同样计 mismatch）；
- wrapper 一致性（R2.1 缺陷 1）：无 --python 覆盖（parser 拒绝、cmd_dry_run/
  cmd_install 签名无 python_path）、preflight 恒查 repo 自带 .venv——有
  .venv dry-run OK、无 .venv fail-closed（外部 python 存在也不放行）；
- dry-run：零写操作（仅 /Query）、任务缺失 + 预检过 → exit 0；预检缺项/任务
  存在/查询事实不完整 → exit 1；
- install：happy path（/Create /TN /XML 临时文件、临时 XML 内容精确等于
  build_task_xml、UTF-16、用后即删、**归一化回读**复查通过——R3 真实回环）；
  拒绝同名（installed/foreign/unknown）、二次复核不确认 missing、pin env
  缺失、venv python 缺失——一律零 /Create；
- status 五态 + 退出码；uninstall（R2.1 缺陷 5）：仅 exact-owned（归属标识+
  全部关键字段精确匹配）才 /Delete /F；missing 幂等 exit 0；foreign/
  malformed/明细不可读 unknown/全量列表查询失败/解码失败 → 零 /Delete；
- wrapper 契约：无盘符硬编码、不内嵌 secret、隐藏窗口 Run(...,0,True)、
  WScript.Quit 透传、仓库根经 GetParentFolderName 推导、固定 repo/.venv
  python 调用目标；
- 源码契约：无 "/Run"；"/F" 全文恰好一次且仅在 /Delete argv（exact-owned
  分支）；/Create 永不带 /F；无 secret 模式。
"""
from __future__ import annotations

import importlib.util
import inspect
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "windows_startup_task.py"
VBS = REPO_ROOT / "tools" / "ops" / "run_production_recovery_silent.vbs"
SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb_", "-----BEGIN")
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


wst = _load_module(SCRIPT, "windows_startup_task_under_test")


def normalize_task_xml(xml_text: str) -> str:
    """模拟 Task Scheduler 归一化存储（2026-09-11 生产 + COM 快照实证形态）。

    schtasks /Create 注册后回读的 XML 与写入 XML 的差异：URI 重写为
    ``\\<TaskName>``；省略「值恰为 Windows 默认值」的元素（LogonTrigger/
    Enabled、Settings/Enabled、Principal/RunLevel）；Action/Arguments/
    WorkingDirectory/Hidden 与非默认设置逐字保留。测试用它把 build_task_xml
    输出变换成真实回读形态（extra 元素注入见各测试）。
    """
    text = xml_text.replace(wst.TASK_URI, wst.NORMALIZED_TASK_URI)
    text = text.replace(
        "    <LogonTrigger>\n      <Enabled>true</Enabled>\n    </LogonTrigger>",
        "    <LogonTrigger />")
    text = text.replace(
        "    <Hidden>true</Hidden>\n    <Enabled>true</Enabled>\n  </Settings>",
        "    <Hidden>true</Hidden>\n  </Settings>")
    text = text.replace("      <RunLevel>LeastPrivilege</RunLevel>\n", "")
    return text


# ---------------------------------------------------------------- fakes / fixtures

class FakeSchtasks:
    """伪 schtasks：按命令形态回放预制结果；记录全部 argv；/Create 时捕获临时 XML。

    list_query_rc != 0 模拟「全量列表查询失败」（STATE_UNKNOWN 的事实不完整路径）。
    R3：/XML 明细经 run_raw 回放**原始字节**；xml_output 选择编码形态——
    "utf-16le-nobom"（生产实证形态）/ "utf-16le-bom"（经典形态）/
    "utf-16be-bom"（解码兼容面）/ "oem-garbage"（rc=0 但非 XML 字节 → 解码
    fail-closed）。xml_bytes 直接给定字节时优先于一切。默认 _xml() 回放
    **归一化**形态（真实 schtasks 注册后的回读即归一化——R3 回归主路径）。
    """

    def __init__(self, *, repo_root: Path, task_state: str = "missing",
                 xml_text: str | None = None, create_rc: int = 0, delete_rc: int = 0,
                 plain_query_exists: bool | None = None,
                 list_query_rc: int = 0,
                 xml_output: str = "utf-16le-nobom",
                 xml_bytes: bytes | None = None,
                 normalized_readback: bool = True) -> None:
        self.repo_root = repo_root
        self.task_state = task_state
        self.xml_text = xml_text
        self.create_rc = create_rc
        self.delete_rc = delete_rc
        self.plain_query_exists = plain_query_exists
        self.list_query_rc = list_query_rc
        self.xml_output = xml_output
        self.xml_bytes = xml_bytes
        self.normalized_readback = normalized_readback
        self.calls: list[tuple[str, ...]] = []
        self.captured_xml: str | None = None
        self.captured_xml_path: Path | None = None

    def _xml(self) -> str:
        if self.xml_text is not None:
            return self.xml_text
        if self.normalized_readback:
            return normalize_task_xml(wst.build_task_xml(self.repo_root))
        return wst.build_task_xml(self.repo_root)

    def _xml_payload(self) -> bytes:
        if self.xml_bytes is not None:
            return self.xml_bytes
        if self.xml_output == "oem-garbage":
            # rc=0 但输出不是 XML：模拟 OEM 代码页错误文案被误当明细回读
            return "ERROR: 拒绝访问: 取不到任务详细信息。\r\n".encode("gbk")
        text = self._xml()
        if self.xml_output == "utf-16le-bom":
            return b"\xff\xfe" + text.encode("utf-16-le")
        if self.xml_output == "utf-16be-bom":
            return b"\xfe\xff" + text.encode("utf-16-be")
        return text.encode("utf-16-le")  # utf-16le-nobom：生产实证形态

    def run(self, argv, *, timeout: float = 60.0, encoding: str | None = None):
        argv = tuple(str(item) for item in argv)
        self.calls.append(argv)
        ok = wst.pr.CommandResult  # type: ignore[attr-defined]
        if "/Create" in argv:
            xml_path = Path(argv[argv.index("/XML") + 1])
            self.captured_xml_path = xml_path
            self.captured_xml = xml_path.read_text(encoding="utf-16")
            if self.create_rc == 0:
                self.task_state = "installed"
            return ok(argv, self.create_rc, "SUCCESS: created", "")
        if "/Delete" in argv:
            if self.delete_rc == 0:
                self.task_state = "missing"
            return ok(argv, self.delete_rc, "SUCCESS: deleted" if self.delete_rc == 0 else "", "refused")
        # /Query：全量列表（存在性，编码无关）在明细 /XML 之前
        if "/FO" in argv:
            if self.list_query_rc != 0:
                return ok(argv, self.list_query_rc, "", "ERROR: schtasks service unavailable")
            exists = self.plain_query_exists if self.plain_query_exists is not None \
                else self.task_state != "missing"
            rows = '"HOST","\\SomeOtherTask","2026/09/11 09:00:00","Ready"\n'
            if exists:
                rows += f'"HOST","\\{wst.TASK_NAME}","2026/09/11 09:30:00","Ready"\n'
            return ok(argv, 0, rows, "")
        raise AssertionError(f"FakeSchtasks.run 不应再被 /XML 明细路径调用（应走 run_raw）: {argv}")

    def run_raw(self, argv, *, timeout: float = 60.0):
        """R3：/Query /TN ... /XML 明细走原始字节（真实 schtasks 管道形态）。"""
        argv = tuple(str(item) for item in argv)
        self.calls.append(argv)
        ok = wst.pr.RawCommandResult  # type: ignore[attr-defined]
        if self.task_state == "missing":
            return ok(argv, 1, b"", "ERROR: cannot find".encode("gbk"))
        if self.task_state == "unknown":
            return ok(argv, 1, b"", "ERROR: Access is denied.".encode("gbk"))
        return ok(argv, 0, self._xml_payload(), b"")


@pytest.fixture()
def fake_repo(tmp_path: Path):
    """结构完整的临时仓库（stub 文件；pin env 仅存在性用，绝不读值）。"""
    ops = tmp_path / "tools" / "ops"
    ops.mkdir(parents=True)
    (ops / "production_recovery.py").write_text("# stub\n", encoding="utf-8")
    (ops / "run_production_recovery_silent.vbs").write_text("' stub\n", encoding="utf-8")
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "env.production-recovery").write_text("# stub（值不重要）\n", encoding="utf-8")
    venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    return tmp_path


def _no_mutating_calls(runner: FakeSchtasks) -> None:
    for argv in runner.calls:
        assert "/Create" not in argv and "/Delete" not in argv and "/Run" not in argv


def _log_spy() -> tuple[list[str], object]:
    lines: list[str] = []

    def log(message: str) -> None:
        lines.append(message)

    return lines, log


# ---------------------------------------------------------------- Task XML 关键项

def test_task_xml_key_settings(fake_repo: Path) -> None:
    xml_text = wst.build_task_xml(fake_repo)
    root = ET.fromstring(xml_text)

    def text_of(path: str) -> str:
        node = root.find(path, NS)
        assert node is not None and node.text, path
        return node.text.strip()

    assert text_of("t:RegistrationInfo/t:URI") == wst.TASK_URI
    assert text_of("t:RegistrationInfo/t:Description") == wst.TASK_DESCRIPTION  # 持久归属标记
    assert root.find("t:Triggers/t:LogonTrigger", NS) is not None
    assert text_of("t:Triggers/t:LogonTrigger/t:Enabled") == "true"
    assert text_of("t:Settings/t:Enabled") == "true"
    assert text_of("t:Settings/t:Hidden") == "true"
    assert text_of("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
    assert text_of("t:Settings/t:StartWhenAvailable") == "true"
    assert text_of("t:Settings/t:ExecutionTimeLimit") == "PT2H"
    assert text_of("t:Settings/t:DisallowStartIfOnBatteries") == "false"
    assert text_of("t:Settings/t:StopIfGoingOnBatteries") == "false"
    assert text_of("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert text_of("t:Principals/t:Principal/t:RunLevel") == "LeastPrivilege"
    assert text_of("t:Actions/t:Exec/t:Command") == "wscript.exe"
    expected_vbs = (fake_repo / "tools" / "ops" / "run_production_recovery_silent.vbs").resolve()
    assert text_of("t:Actions/t:Exec/t:Arguments") == f'//B //Nologo "{expected_vbs}"'
    assert text_of("t:Actions/t:Exec/t:WorkingDirectory") == str(fake_repo.resolve())
    assert xml_text.count("<StopIfGoingOnBatteries>") == 1  # 单元素（开+闭标签计 2）
    assert xml_text.startswith('<?xml version="1.0" encoding="UTF-16"?>')
    for pattern in SECRET_PATTERNS:
        assert pattern not in xml_text


# ------------------------------------------------- XML 转义（R2.1 缺陷 2）

def test_build_task_xml_escapes_paths_with_xml_special_chars(tmp_path: Path) -> None:
    """repo/VBS 路径含 &、<、>、"：XML 必须可解析、字段解析后精确还原。"""
    weird_root = tmp_path / 're&po<r>t"o<&x'  # 仅构造路径，不落盘
    xml_text = wst.build_task_xml(weird_root)
    # 未转义的 & 会在此直接抛 ET.ParseError
    root = ET.fromstring(xml_text)

    def text_of(path: str) -> str:
        node = root.find(path, NS)
        assert node is not None and node.text, path
        return node.text.strip()

    expected_vbs = (weird_root / "tools" / "ops" / "run_production_recovery_silent.vbs").resolve()
    assert text_of("t:Actions/t:Exec/t:Arguments") == f'//B //Nologo "{expected_vbs}"'
    assert text_of("t:Actions/t:Exec/t:WorkingDirectory") == str(weird_root.resolve())
    # 原文确含实体转义（而非碰巧未触发）
    assert "&amp;" in xml_text and "&lt;" in xml_text and "&gt;" in xml_text and "&quot;" in xml_text
    # round-trip：转义后的 XML 仍被认作本工具精确拥有
    assert wst.verify_task_xml(xml_text, weird_root) == (wst.STATE_INSTALLED, [])


# ------------------------------------------------- verify 四态（R2.1 缺陷 3/4）

def test_verify_task_xml_roundtrip_installed(fake_repo: Path) -> None:
    state, issues = wst.verify_task_xml(wst.build_task_xml(fake_repo), fake_repo)
    assert state == wst.STATE_INSTALLED and issues == []


def test_verify_task_xml_foreign_variants(fake_repo: Path) -> None:
    xml_text = wst.build_task_xml(fake_repo)
    uri_swap = xml_text.replace(wst.TASK_URI, "urn:someone-else:task")
    assert wst.verify_task_xml(uri_swap, fake_repo)[0] == wst.STATE_FOREIGN
    cmd_swap = xml_text.replace("<Command>wscript.exe</Command>", "<Command>evil.exe</Command>")
    assert wst.verify_task_xml(cmd_swap, fake_repo)[0] == wst.STATE_FOREIGN


@pytest.mark.parametrize("mutate,field", [
    (lambda x: x.replace("//B //Nologo", "//X"), "Actions/Exec/Arguments"),
    (lambda x: x.replace("<Hidden>true</Hidden>", "<Hidden>false</Hidden>"), "Settings/Hidden"),
    (lambda x: x.replace("LogonTrigger", "BootTrigger"), "Triggers/LogonTrigger"),
    (lambda x: x.replace("<ExecutionTimeLimit>PT2H", "<ExecutionTimeLimit>PT10H"), "Settings/ExecutionTimeLimit"),
    # R2.1 缺陷 4：归属关键字段逐项精确匹配（漂移报告对应字段名）
    (lambda x: x.replace("<LogonTrigger>\n      <Enabled>true</Enabled>",
                         "<LogonTrigger>\n      <Enabled>false</Enabled>"), "Triggers/LogonTrigger/Enabled"),
    (lambda x: x.replace("<Hidden>true</Hidden>\n    <Enabled>true</Enabled>",
                         "<Hidden>true</Hidden>\n    <Enabled>false</Enabled>"), "Settings/Enabled"),
    (lambda x: x.replace("IgnoreNew", "Parallel"), "Settings/MultipleInstancesPolicy"),
    (lambda x: x.replace("<StartWhenAvailable>true", "<StartWhenAvailable>false"), "Settings/StartWhenAvailable"),
    (lambda x: x.replace("<DisallowStartIfOnBatteries>false", "<DisallowStartIfOnBatteries>true"),
     "Settings/DisallowStartIfOnBatteries"),
    (lambda x: x.replace("<StopIfGoingOnBatteries>false", "<StopIfGoingOnBatteries>true"),
     "Settings/StopIfGoingOnBatteries"),
    (lambda x: x.replace("<LogonType>InteractiveToken", "<LogonType>S4U"), "Principals/Principal/LogonType"),
    (lambda x: x.replace("<RunLevel>LeastPrivilege", "<RunLevel>HighestAvailable"), "Principals/Principal/RunLevel"),
])
def test_verify_task_xml_drift_reports_field_names_only(fake_repo: Path, mutate, field) -> None:
    state, issues = wst.verify_task_xml(mutate(wst.build_task_xml(fake_repo)), fake_repo)
    assert state == wst.STATE_MALFORMED
    assert field in issues  # 字段名可见
    assert all(issue.startswith(("Actions", "Settings", "Triggers", "Principals", "RegistrationInfo", "XML"))
               for issue in issues)  # 绝无字段值


@pytest.mark.parametrize("pattern,field", [
    (r"<WorkingDirectory>[^<]*</WorkingDirectory>", "Actions/Exec/WorkingDirectory"),
    (r"<Arguments>[^<]*</Arguments>", "Actions/Exec/Arguments"),
    (r"<MultipleInstancesPolicy>[^<]*</MultipleInstancesPolicy>", "Settings/MultipleInstancesPolicy"),
    # R3：RunLevel/LogonTrigger(Enabled)/Settings(Enabled) 属归一化省略面（值=
    # Windows 默认时 Task Scheduler 会省略），不再作为「缺失即 mismatch」样本；
    # 非默认值元素缺失仍拒绝：
    (r"<Hidden>[^<]*</Hidden>", "Settings/Hidden"),
    (r"<Description>[^<]*</Description>", "RegistrationInfo/Description"),
])
def test_verify_task_xml_missing_elements_are_mismatches(fake_repo: Path, pattern, field) -> None:
    """R2.1 缺陷 3：元素缺失 = mismatch（不得 if working_dir and ... 短路放行）。"""
    xml_text = re.sub(pattern, "", wst.build_task_xml(fake_repo))
    state, issues = wst.verify_task_xml(xml_text, fake_repo)
    assert state == wst.STATE_MALFORMED
    assert field in issues


def test_verify_task_xml_rejects_garbage_and_entities(fake_repo: Path) -> None:
    assert wst.verify_task_xml("not xml at all", fake_repo) == (wst.STATE_MALFORMED, ["XML 解析失败"])
    entity = wst.build_task_xml(fake_repo).replace(
        "<Task ", "<!DOCTYPE Task [<!ENTITY xxe SYSTEM \"file:///c:/win.ini\">]><Task ", 1)
    state, issues = wst.verify_task_xml(entity, fake_repo)
    assert state == wst.STATE_MALFORMED and any("ENTITY" in i for i in issues)


# ------------------------------------------------- R3：真实回读/归一化回归
# 生产实证（2026-09-11，canonical 安装 AIOS-Production-Recovery 后回读）：
# ① /XML 管道输出 UTF-16LE 无 BOM——text-mode encoding='utf-16' 在读线程抛
#    UnicodeError 丢输出（status 误判 unknown 的根因）；
# ② Task Scheduler 归一化存储：URI 重写为 \\<TaskName>，省略默认值元素
#    （LogonTrigger/Enabled、Settings/Enabled、Principal/RunLevel），自行
#    新增 UserId/IdleSettings 等；Action/Arguments/cwd/Hidden 与非默认设置
#    逐字保留（COM 快照 .verify/m14-06-production-resilience/）。

def test_decode_schtasks_xml_bom_and_byte_variants(fake_repo: Path) -> None:
    """R3：UTF-16LE 无 BOM（生产形态）/ LE BOM / BE BOM 三形态解码等价。"""
    xml_text = wst.build_task_xml(fake_repo)
    assert wst.decode_schtasks_xml(xml_text.encode("utf-16-le")) == xml_text.strip()
    assert wst.decode_schtasks_xml(b"\xff\xfe" + xml_text.encode("utf-16-le")) == xml_text.strip()
    assert wst.decode_schtasks_xml(b"\xfe\xff" + xml_text.encode("utf-16-be")) == xml_text.strip()


@pytest.mark.parametrize("bad", [
    b"ERROR: something in plain ASCII",  # ASCII/OEM 文案强解成乱码 → 非 '<' 开头
    b"\xd2\xfb\xbe\xfc\xbd\xfb",        # GBK 错误文案字节 → 解出 CJK 乱码
    b"<",                                # 奇数字节（截断的 UTF-16）
    b"\xff\xfe",                         # 仅 BOM 无正文
])
def test_decode_schtasks_xml_rejects_non_xml_bytes(bad: bytes) -> None:
    """非 XML 字节一律 ValueError——调用方按 unknown fail-closed，绝不弱解。"""
    with pytest.raises(ValueError):
        wst.decode_schtasks_xml(bad)


def test_query_task_decodes_production_no_bom_output(fake_repo: Path) -> None:
    """R3 主回归：真实回读形态（UTF-16LE 无 BOM + 归一化 XML）→ installed。"""
    runner = FakeSchtasks(repo_root=fake_repo, task_state="installed",
                          xml_output="utf-16le-nobom")
    query = wst.query_task(runner, fake_repo)
    assert query.state == wst.STATE_INSTALLED and query.issues == ()


@pytest.mark.parametrize("xml_output", ["utf-16le-bom", "utf-16be-bom"])
def test_query_task_accepts_bommed_variants(fake_repo: Path, xml_output: str) -> None:
    runner = FakeSchtasks(repo_root=fake_repo, task_state="installed", xml_output=xml_output)
    assert wst.query_task(runner, fake_repo).state == wst.STATE_INSTALLED


def test_query_task_decode_failure_is_unknown_and_uninstall_refuses(fake_repo: Path) -> None:
    """rc=0 但输出非 XML 字节（OEM 乱码）→ unknown；uninstall 可见拒绝、零 /Delete。"""
    runner = FakeSchtasks(repo_root=fake_repo, task_state="installed", xml_output="oem-garbage")
    query = wst.query_task(runner, fake_repo)
    assert query.state == wst.STATE_UNKNOWN
    _, log = _log_spy()
    assert wst.cmd_uninstall(runner=runner, repo_root=fake_repo, log=log) == wst.EXIT_ERROR
    assert not any("/Delete" in argv for argv in runner.calls)


def test_verify_accepts_scheduler_normalized_roundtrip(fake_repo: Path) -> None:
    """归一化回读（URI 重写 + 默认值元素省略 + 调度器新增额外元素）仍 exact-owned。"""
    normalized = normalize_task_xml(wst.build_task_xml(fake_repo))
    with_extras = normalized.replace(
        "    <Principal id=\"Author\">",
        "    <Principal id=\"Author\">\n"
        "      <UserId>S-1-5-21-0000000000-0000000000-0000000000-0000</UserId>")
    with_extras = with_extras.replace(
        "  </Settings>",
        "    <IdleSettings>\n      <StopOnIdleEnd>true</StopOnIdleEnd>\n"
        "      <RestartOnIdle>false</RestartOnIdle>\n    </IdleSettings>\n  </Settings>")
    assert wst.verify_task_xml(with_extras, fake_repo) == (wst.STATE_INSTALLED, [])


def test_verify_normalized_uri_alone_is_not_ownership(fake_repo: Path) -> None:
    """归一化 URI 形态对任何同名任务都出现——单独绝不构成归属（须叠加
    Description 持久标记；其他任意 URI / 别处 Command → foreign）。"""
    normalized = normalize_task_xml(wst.build_task_xml(fake_repo))
    desc_missing = normalized.replace(f"    <Description>{wst.TASK_DESCRIPTION}</Description>\n", "")
    assert wst.verify_task_xml(desc_missing, fake_repo)[0] == wst.STATE_MALFORMED
    desc_drift = normalized.replace(wst.TASK_DESCRIPTION, "some other tool owns this task")
    assert wst.verify_task_xml(desc_drift, fake_repo)[0] == wst.STATE_MALFORMED
    other_uri = normalized.replace(wst.NORMALIZED_TASK_URI, "\\SomeOtherTask")
    assert wst.verify_task_xml(other_uri, fake_repo)[0] == wst.STATE_FOREIGN
    evil_cmd = normalized.replace("<Command>wscript.exe</Command>", "<Command>evil.exe</Command>")
    assert wst.verify_task_xml(evil_cmd, fake_repo)[0] == wst.STATE_FOREIGN


def test_verify_omitted_defaults_require_all_other_fields_exact(fake_repo: Path) -> None:
    """省略的默认值元素仅在其余必检字段全部精确在场时认可——Hidden 缺失或
    触发器类型缺失时，省略同样计 mismatch（默认值假设绝不独立放行）。"""
    normalized = normalize_task_xml(wst.build_task_xml(fake_repo))
    hidden_missing = normalized.replace("    <Hidden>true</Hidden>\n", "")
    state, issues = wst.verify_task_xml(hidden_missing, fake_repo)
    assert state == wst.STATE_MALFORMED
    assert "Settings/Hidden" in issues
    assert "Triggers/LogonTrigger/Enabled" in issues  # 其余事实不全时省略不再被掩盖
    no_trigger = normalized.replace("    <LogonTrigger />", "")
    state, issues = wst.verify_task_xml(no_trigger, fake_repo)
    assert state == wst.STATE_MALFORMED and "Triggers/LogonTrigger" in issues


def test_status_and_uninstall_on_normalized_no_bom_roundtrip(fake_repo: Path) -> None:
    """端到端（R3 生产形态）：归一化 + 无 BOM 回读下 status=exit 0、
    uninstall 仅 exact-owned 才 /Delete /F。"""
    runner = FakeSchtasks(repo_root=fake_repo, task_state="installed",
                          xml_output="utf-16le-nobom")
    _, log = _log_spy()
    assert wst.cmd_status(runner=runner, repo_root=fake_repo, log=log) == wst.EXIT_OK
    runner2 = FakeSchtasks(repo_root=fake_repo, task_state="installed",
                           xml_output="utf-16le-nobom")
    _, log2 = _log_spy()
    assert wst.cmd_uninstall(runner=runner2, repo_root=fake_repo, log=log2) == wst.EXIT_OK
    deletes = [argv for argv in runner2.calls if "/Delete" in argv]
    assert deletes == [("schtasks.exe", "/Delete", "/TN", wst.TASK_NAME, "/F")]


# ------------------------------------------------- wrapper 一致性（R2.1 缺陷 1）

def test_no_python_override_surface() -> None:
    """--python 覆盖已移除：parser 拒绝、命令入口签名无 python_path 参数。"""
    parser = wst.build_parser()
    args = parser.parse_args(["dry-run"])
    assert args.command == "dry-run"
    assert not hasattr(args, "python")  # 无覆盖面
    with pytest.raises(SystemExit):
        parser.parse_args(["dry-run", "--python", "C:/External/python.exe"])
    with pytest.raises(SystemExit):
        parser.parse_args(["install", "--python", "C:/External/python.exe"])
    for func in (wst.cmd_dry_run, wst.cmd_install, wst.preflight):
        assert "python_path" not in inspect.signature(func).parameters


def test_dry_run_ok_when_repo_has_venv(fake_repo: Path) -> None:
    """fake repo 自带 .venv：dry-run 预检通过。"""
    runner = FakeSchtasks(repo_root=fake_repo, task_state="missing")
    lines, log = _log_spy()
    code = wst.cmd_dry_run(runner=runner, repo_root=fake_repo, log=log)
    assert code == wst.EXIT_OK
    _no_mutating_calls(runner)
    assert any("OK（可安装；未做任何修改）" in line for line in lines)


def test_dry_run_fail_closed_without_repo_venv(fake_repo: Path, tmp_path: Path) -> None:
    """repo 无 .venv：fail-closed——外部 python 存在也不放行（无覆盖面可传）。"""
    shutil.rmtree(fake_repo / ".venv")
    # 一个「外部 python」确实存在于别处——预检只认 repo 自带 .venv
    external = tmp_path / "external-python" / "python.exe"
    external.parent.mkdir(parents=True, exist_ok=True)
    external.write_text("", encoding="utf-8")
    runner = FakeSchtasks(repo_root=fake_repo, task_state="missing")
    lines, log = _log_spy()
    code = wst.cmd_dry_run(runner=runner, repo_root=fake_repo, log=log)
    assert code == wst.EXIT_ERROR
    assert any("venv Python" in line and "缺失" in line for line in lines)
    assert not any("OK（可安装" in line for line in lines)
    _no_mutating_calls(runner)


def test_install_fail_closed_without_repo_venv(fake_repo: Path) -> None:
    """repo 无 .venv：install 预检拒绝，零 /Create。"""
    shutil.rmtree(fake_repo / ".venv")
    runner = FakeSchtasks(repo_root=fake_repo, task_state="missing")
    lines, log = _log_spy()
    code = wst.cmd_install(runner=runner, repo_root=fake_repo, log=log)
    assert code == wst.EXIT_ERROR
    assert any("venv Python" in line and "缺失" in line for line in lines)
    assert not any("/Create" in argv for argv in runner.calls)


# ---------------------------------------------------------------- dry-run

def test_dry_run_zero_writes_and_exit_ok_when_missing(fake_repo: Path) -> None:
    runner = FakeSchtasks(repo_root=fake_repo, task_state="missing")
    lines, log = _log_spy()
    code = wst.cmd_dry_run(runner=runner, repo_root=fake_repo, log=log)
    assert code == wst.EXIT_OK
    _no_mutating_calls(runner)  # 零 /Create /Delete /Run
    assert any("ExecutionTimeLimit=PT2H" in line for line in lines)


def test_dry_run_previews_refusal_when_task_exists(fake_repo: Path) -> None:
    runner = FakeSchtasks(repo_root=fake_repo, task_state="installed")
    lines, log = _log_spy()
    code = wst.cmd_dry_run(runner=runner, repo_root=fake_repo, log=log)
    assert code == wst.EXIT_ERROR
    assert any("拒绝覆盖" in line for line in lines)
    _no_mutating_calls(runner)


# ---------------------------------------------------------------- install

def test_install_happy_path_creates_with_temp_xml(fake_repo: Path) -> None:
    runner = FakeSchtasks(repo_root=fake_repo, task_state="missing")
    lines, log = _log_spy()
    code = wst.cmd_install(runner=runner, repo_root=fake_repo, log=log)
    assert code == wst.EXIT_OK
    creates = [argv for argv in runner.calls if "/Create" in argv]
    assert len(creates) == 1
    argv = creates[0]
    assert argv[:2] == ("schtasks.exe", "/Create")
    assert argv[argv.index("/TN") + 1] == wst.TASK_NAME
    assert "/F" not in argv  # create 绝不 force
    assert runner.captured_xml == wst.build_task_xml(fake_repo)  # 临时 XML 内容精确
    assert runner.captured_xml_path is not None and not runner.captured_xml_path.exists()  # 用后即删
    assert any("install 成功" in line for line in lines)


@pytest.mark.parametrize("scenario,kw", [
    ("同名已安装", {"task_state": "installed"}),
    ("同名 foreign", {"task_state": "foreign"}),
    ("查询 unknown", {"task_state": "unknown"}),
    ("二次复核不确认 missing", {"task_state": "missing", "plain_query_exists": True}),
    ("pin env 缺失", {"task_state": "missing", "plain_query_exists": False}),
])
def test_install_refusals_zero_create(fake_repo: Path, scenario, kw) -> None:
    if scenario == "pin env 缺失":
        (fake_repo / "infra" / "env.production-recovery").unlink()
    runner = FakeSchtasks(repo_root=fake_repo, **kw)
    lines, log = _log_spy()
    code = wst.cmd_install(runner=runner, repo_root=fake_repo, log=log)
    assert code == wst.EXIT_ERROR, scenario
    assert not any("/Create" in argv for argv in runner.calls), scenario
    assert any("拒绝" in line or "失败" in line for line in lines), scenario


def test_install_post_verify_mismatch_visible_failure(fake_repo: Path) -> None:
    runner = FakeSchtasks(repo_root=fake_repo, task_state="missing", create_rc=0)
    # 构造「创建后回读仍是漂移 XML」：xml_text 指向 Hidden=false 的变体
    runner.xml_text = wst.build_task_xml(fake_repo).replace("<Hidden>true</Hidden>", "<Hidden>false</Hidden>")
    runner.task_state = "missing"
    lines, log = _log_spy()
    code = wst.cmd_install(runner=runner, repo_root=fake_repo, log=log)
    assert code == wst.EXIT_ERROR
    assert any("复查不符" in line and "Settings/Hidden" in line for line in lines)


# ---------------------------------------------------------------- status / uninstall

def test_status_four_states(fake_repo: Path) -> None:
    cases = [
        ("installed", wst.EXIT_OK),
        ("missing", wst.EXIT_MISSING),
        ("foreign", wst.EXIT_FOREIGN),
        ("unknown", wst.EXIT_ERROR),
    ]
    for state, expected in cases:
        runner = FakeSchtasks(repo_root=fake_repo, task_state=state)
        if state == "foreign":
            runner.xml_text = wst.build_task_xml(fake_repo).replace(wst.TASK_URI, "urn:other:x")
        _, log = _log_spy()
        assert wst.cmd_status(runner=runner, repo_root=fake_repo, log=log) == expected, state
        assert not any("/Create" in a or "/Delete" in a for a in runner.calls), state


def test_status_malformed_exit_code(fake_repo: Path) -> None:
    runner = FakeSchtasks(repo_root=fake_repo, task_state="installed", xml_text="<<<garbage>>>")
    _, log = _log_spy()
    assert wst.cmd_status(runner=runner, repo_root=fake_repo, log=log) == wst.EXIT_MALFORMED


def test_uninstall_deletes_exact_owned_with_force(fake_repo: Path) -> None:
    """exact-owned（URI+全部归属关键字段精确匹配）→ /Delete 附 /F（免交互挂起）。"""
    runner = FakeSchtasks(repo_root=fake_repo, task_state="installed")
    _, log = _log_spy()
    code = wst.cmd_uninstall(runner=runner, repo_root=fake_repo, log=log)
    assert code == wst.EXIT_OK
    deletes = [argv for argv in runner.calls if "/Delete" in argv]
    assert deletes == [("schtasks.exe", "/Delete", "/TN", wst.TASK_NAME, "/F")]


@pytest.mark.parametrize("state,expected", [
    ("missing", wst.EXIT_OK),      # 幂等：无任务零操作
    ("foreign", wst.EXIT_FOREIGN),  # 拒绝碰他人任务
    ("unknown", wst.EXIT_ERROR),    # 明细不可读：事实不完整
])
def test_uninstall_refusals_zero_delete(fake_repo: Path, state, expected) -> None:
    runner = FakeSchtasks(repo_root=fake_repo, task_state=state)
    if state == "foreign":
        runner.xml_text = wst.build_task_xml(fake_repo).replace("wscript.exe", "other.exe")
    _, log = _log_spy()
    code = wst.cmd_uninstall(runner=runner, repo_root=fake_repo, log=log)
    assert code == expected, state
    assert not any("/Delete" in argv for argv in runner.calls), state


def test_uninstall_malformed_refuses(fake_repo: Path) -> None:
    runner = FakeSchtasks(repo_root=fake_repo, task_state="installed",
                          xml_text=wst.build_task_xml(fake_repo).replace("PT2H", "PT9H"))
    _, log = _log_spy()
    assert wst.cmd_uninstall(runner=runner, repo_root=fake_repo, log=log) == wst.EXIT_MALFORMED
    assert not any("/Delete" in argv for argv in runner.calls)


def test_uninstall_query_failure_refuses(fake_repo: Path) -> None:
    """全量列表查询失败（事实不完整）→ unknown，零 /Delete。"""
    runner = FakeSchtasks(repo_root=fake_repo, task_state="installed", list_query_rc=1)
    _, log = _log_spy()
    code = wst.cmd_uninstall(runner=runner, repo_root=fake_repo, log=log)
    assert code == wst.EXIT_ERROR
    assert not any("/Delete" in argv for argv in runner.calls)
    assert not any("/Create" in argv for argv in runner.calls)


def test_uninstall_ownership_drift_in_new_fields_refuses(fake_repo: Path) -> None:
    """R2.1 缺陷 4/5 交叉：新增归属字段漂移（RunLevel=HighestAvailable）→
    非 exact-owned → 零 /Delete。"""
    drifted = wst.build_task_xml(fake_repo).replace(
        "<RunLevel>LeastPrivilege", "<RunLevel>HighestAvailable")
    runner = FakeSchtasks(repo_root=fake_repo, task_state="installed", xml_text=drifted)
    lines, log = _log_spy()
    code = wst.cmd_uninstall(runner=runner, repo_root=fake_repo, log=log)
    assert code == wst.EXIT_MALFORMED
    assert not any("/Delete" in argv for argv in runner.calls)
    assert any("Principals/Principal/RunLevel" in line for line in lines)


# ---------------------------------------------------------------- wrapper / 源码契约

def test_vbs_wrapper_contract() -> None:
    assert VBS.is_file()
    text = VBS.read_text(encoding="utf-8")
    # 无盘符/机器特定路径硬编码（仓库根自脚本位置推导）
    assert not re.search(r"[A-Za-z]:[\\\\/]", text), "wrapper 不得硬编码盘符路径"
    assert "GetParentFolderName" in text and "BuildPath" in text
    # 隐藏窗口 + 等待 + 退出码透传
    assert ", 0, True)" in text
    assert "WScript.Quit exitCode" in text
    # 固定调用 repo/.venv/Scripts/python.exe（与 preflight 检查目标一致）
    assert '".venv"), "Scripts"), "python.exe"' in text
    assert "production_recovery.py" in text
    for pattern in SECRET_PATTERNS:
        assert pattern not in text
    forbidden = ("http://", "https://", "mshta", "powershell", "cmd.exe", "CreateTextFile", "FileSystemObject\".Write")
    for token in forbidden:
        assert token not in text, token


def test_source_contract_force_only_on_owned_delete() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"/Run"' not in source  # 绝不运行任务
    # /F 全文恰好一次，且仅在 /Delete argv 行（exact-owned 分支）
    assert source.count('"/F"') == 1
    delete_argv_line = next(line for line in source.splitlines() if '"/Delete"' in line)
    assert "TASK_NAME" in delete_argv_line and '"/F"' in delete_argv_line
    # /Create 永不带 /F；写路径仅 /Create 与 /Delete 两个字面量
    for line in source.splitlines():
        if '"/Create"' in line:
            assert "/F" not in line
    assert source.count('"/Create"') == 1 and source.count('"/Delete"') == 1
    for pattern in SECRET_PATTERNS:
        assert pattern not in source


def test_parser_defaults_and_choices() -> None:
    parser = wst.build_parser()
    args = parser.parse_args(["status"])
    assert args.command == "status"
    with pytest.raises(SystemExit):
        parser.parse_args(["run"])  # 不存在 run 子命令（设计即拒绝）
