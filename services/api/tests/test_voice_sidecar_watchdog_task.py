r"""M14-77 tools/voice/voice_sidecar_watchdog_task.py +
run_voice_sidecar_watchdog_silent.vbs 契约测试：语音健康 sidecar 看护
计划任务 readiness 管理器（零真实 schtasks 写路径、零注册、零 Docker/零
网络/零 WSL/零生产触碰）。

背景（2026-09-21 生产事实）：FunASR/CosyVoice 引擎 managed-running、
health 200，但 voice_health_sidecar PID 6660 于 01:00 后静默退出——
15 分钟监控管道（monitoring_pipeline 固定 argv
``--voice-health-source sidecar``）因 sidecar 端点不可达连续 ~7 小时
monitor exit 2，long-soak 被污染。根因缺陷：sidecar 是「spawn 一次、
无看护」的未托管进程；控制器 start 幂等自愈（stale manifest → 清理 →
重启，全部安全核验保留）已存在，但生产拓扑中无任何周期性调用者。

本切片交付（全部加法式，零改动 production_monitor / monitoring_pipeline /
voice_health_sidecar{,_control}.py / 既有 VBS 与任务）：
- ``tools/voice/voice_sidecar_watchdog_task.py``：看护计划任务 readiness
  管理器（plan/generate/status/install/uninstall；M14-14
  monitoring_pipeline_task 同款纪律——GatedSchtasks 结构性白名单、
  归属判定 fail-closed、install/uninstall 精确确认短语、绝不覆盖同名
  任务、本开发回合零安装零注册）；
- ``tools/voice/run_voice_sidecar_watchdog_silent.vbs``：静默入口，调用
  既有 ``voice_health_sidecar_control.py start``（幂等 ensure：活着跳过、
  死了以全部既有生产保护核验重启、WSL 不可用 rc=3 可见失败不遮蔽）。

覆盖（全部 FakeSchtasks 注入 + 临时 fake repo + importlib 装载）：
- Task XML：固定身份（URI/Description 持久归属标记）+ 关键设置逐项断言
  （Hidden/IgnoreNew/StartWhenAvailable/电池双 false/ExecutionTimeLimit=
  PT4M < 重复间隔 PT5M/TimeTrigger+StartBoundary/Repetition/
  InteractiveToken+LeastPrivilege/wscript //B //Nologo + 仓库内 VBS 路径/
  WorkingDirectory=repo）；_esc 转义单元 + 特殊字符仓库路径 round-trip；
- 预算链交叉 pin：间隔 PT5M > 执行时限 PT4M > WATCHDOG_RUN_BUDGET_SECONDS
  ≥ 控制器 start 单轮最坏推算（probe×2 + status 等待 + 健康探测×2，
  常量取自 voice_health_sidecar_control 同源事实）；看护间隔 PT5M <
  监控管道间隔 PT15M（sidecar 死亡暴露窗收敛到一个看护周期，绝不超过
  一个监控轮次）；
- verify_task_xml 四态：installed（round-trip 及 Task Scheduler 归一化
  形态——URI 重写/默认值元素省略按 M14-06 实证先例条件认可）/ foreign
  （URI 或 Command）/ malformed（字段缺失/漂移逐项报告字段名；XML 垃圾；
  DOCTYPE/ENTITY 拒绝——XXE 防护）；省略的默认值仅在其余字段全部精确时
  认可；
- decode_schtasks_xml 四形态（UTF-16LE 无 BOM/LE BOM/BE BOM/UTF-8
  prolog）与拒绝面（OEM 乱码/奇数截断/非 XML）；
- 白名单门（结构性）：只读门恒放行两查询形态、拒绝一切 mutation（含
  /Run//Change//End 与带 /F 的 create）；mutation 门仅放行
  ``/Create /TN <固定名> /XML <单路径>`` 与 ``/Delete /TN <固定名> /F``
  两精确形态（create 绝不带 /F、任务名恒固定；/XML 值位置接受 POSIX
  绝对 tempfile 路径——Linux CI 形态）；
- plan：任务缺失+预检过 → exit 0（恰一次列表查询，零 mutation）；预检
  缺项（无 .venv）→ exit 1 fail-closed；任务存在/事实不完整 → exit 1；
- generate：XML 原子导出（UTF-16 with BOM 字节断言 + 回读 installed +
  零 schtasks 调用）；输出路径 symlink 拒绝零写入（平台不支持时 skip）；
- status 五态 + 退出码（installed 0/missing 2/foreign 3/malformed 4/
  unknown 1）；
- install/uninstall 短语门禁：缺失/近似短语 → exit 1 且零 schtasks 调用；
  install happy path（临时 XML 字节=build_task_xml 的 UTF-16、用后即删、
  归一化回读复查通过）；install 拒绝同名（installed/列表复核不确认
  missing）；uninstall 仅 exact-owned 才 /Delete /F、missing 幂等 exit 0、
  foreign/malformed/unknown 零删除；
- VBS wrapper 契约：无盘符硬编码、无 secret、隐藏窗口 Run(...,0,True)、
  WScript.Quit 透传、仓库根推导、固定 venv python +
  voice_health_sidecar_control.py 调用目标、恒带 start 子命令（幂等
  ensure）、预检退出码 2/3、零 http/powershell/cmd.exe/mshta/
  CreateTextFile；
- 与既有任务（AIOS-Monitoring-Pipeline / M14-06 恢复任务）无身份冲突
  （任务名/URI/Description/VBS 文件互不相同）；
- 源码契约：无 '"/Run"'；'"/F"' 仅出现在 schtasks argv 定义行；'"/Create"'
  行绝不带 /F；零 shell=；零 secret 形态。
"""
from __future__ import annotations

import importlib.util
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "voice" / "voice_sidecar_watchdog_task.py"
CONTROLLER_SCRIPT = REPO_ROOT / "tools" / "voice" / "voice_health_sidecar_control.py"
VBS = REPO_ROOT / "tools" / "voice" / "run_voice_sidecar_watchdog_silent.vbs"
MONITORING_TASK_SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_pipeline_task.py"
STARTUP_TASK_SCRIPT = REPO_ROOT / "tools" / "ops" / "windows_startup_task.py"
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


vswt = _load_module(SCRIPT, "voice_sidecar_watchdog_task_under_test")
vhsc = _load_module(CONTROLLER_SCRIPT, "voice_health_sidecar_control_for_watchdog_pin")
mpt = _load_module(MONITORING_TASK_SCRIPT, "monitoring_pipeline_task_for_watchdog_pin")
wst = _load_module(STARTUP_TASK_SCRIPT, "windows_startup_task_for_watchdog_pin")


def _log_spy() -> tuple[list[str], object]:
    lines: list[str] = []
    return lines, lines.append


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    voice = repo / "tools" / "voice"
    voice.mkdir(parents=True)
    for name in ("voice_health_sidecar_control.py", "voice_health_sidecar.py",
                 "run_voice_sidecar_watchdog_silent.vbs"):
        (voice / name).write_text("# fake", encoding="utf-8")
    venv_scripts = repo / ".venv" / "Scripts"
    venv_scripts.mkdir(parents=True)
    (venv_scripts / "python.exe").write_bytes(b"fake")
    return repo


def normalize_task_xml(xml_text: str) -> str:
    """模拟 Task Scheduler 归一化存储（M14-06 生产 + COM 快照实证形态）：
    URI 重写为 ``\\<TaskName>``；省略值恰为 Windows 默认值的元素
    （TimeTrigger/Enabled、Settings/Enabled、Principal/RunLevel）；
    其余逐字保留。"""
    text = xml_text.replace(vswt.TASK_URI, vswt.NORMALIZED_TASK_URI)
    text = text.replace(
        f"      <StartBoundary>{vswt.START_BOUNDARY}</StartBoundary>\n"
        f"      <Enabled>true</Enabled>\n",
        f"      <StartBoundary>{vswt.START_BOUNDARY}</StartBoundary>\n")
    text = text.replace(
        "    <Hidden>true</Hidden>\n    <Enabled>true</Enabled>\n  </Settings>",
        "    <Hidden>true</Hidden>\n  </Settings>")
    text = text.replace("      <RunLevel>LeastPrivilege</RunLevel>\n", "")
    return text


class FakeSchtasks:
    """伪 schtasks：记录全部调用；任务在列/回读 XML/各 rc 可配置；
    create 成功后切换为 installed（归一化回读形态）。"""

    def __init__(self, *, repo_root: Path, task_state: str = "missing",
                 xml_text: str = "", list_rc: int = 0,
                 create_rc: int = 0, delete_rc: int = 0,
                 flip_to_installed_after_create: bool = True) -> None:
        self._repo_root = repo_root
        self._task_state = task_state
        self._xml_text = xml_text
        self._list_rc = list_rc
        self._create_rc = create_rc
        self._delete_rc = delete_rc
        self._flip = flip_to_installed_after_create
        self.calls: list[tuple[str, ...]] = []
        self.created_xml_bytes: bytes | None = None

    # -- FakeSchtasks 实现 RealRunner 协议（run / run_raw）--
    def run(self, argv, *, timeout: float = 60.0):
        tokens = tuple(str(item) for item in argv)
        self.calls.append(tokens)
        if tokens[1:5] == ("/Query", "/FO", "CSV", "/NH"):
            listed = self._task_state != "missing" or self._list_rc != 0
            if self._list_rc != 0:
                return vswt.CommandResult(tokens, self._list_rc, "", "err")
            stdout = ('"\\\\SomeOtherTask","Foo"\n' if not listed
                      else f'"{vswt.TASK_NAME}","AIOS Voice Sidecar Watchdog"\n')
            return vswt.CommandResult(tokens, 0, stdout, "")
        if tokens[1] == "/Create":
            self.created_xml_bytes = Path(tokens[5]).read_bytes()
            if self._flip and self._create_rc == 0:
                self._task_state = "installed"
                self._xml_text = normalize_task_xml(
                    vswt.build_task_xml(self._repo_root))
            return vswt.CommandResult(tokens, self._create_rc, "", "")
        if tokens[1] == "/Delete":
            if self._delete_rc == 0:
                self._task_state = "missing"
            return vswt.CommandResult(tokens, self._delete_rc, "", "")
        raise AssertionError(f"unexpected argv: {tokens}")

    def run_raw(self, argv, *, timeout: float = 30.0):
        tokens = tuple(str(item) for item in argv)
        self.calls.append(tokens)
        assert tokens[1:5] == ("/Query", "/TN", vswt.TASK_NAME, "/XML")
        if self._task_state == "missing":
            return vswt.RawCommandResult(tokens, 1, b"")
        payload = self._xml_text.encode("utf-16-le")
        return vswt.RawCommandResult(tokens, 0, payload)


def _gated(fake: FakeSchtasks, *, allow_mutation: bool = False) -> vswt.GatedSchtasks:
    return vswt.GatedSchtasks(fake, allow_mutation=allow_mutation)


def _minutes(spec: str) -> int:
    match = re.fullmatch(r"PT(\d+)M", spec)
    assert match is not None, spec
    return int(match.group(1)) * 60


# ---------------------------------------------------------------- Task XML


def test_build_task_xml_key_fields(fake_repo: Path) -> None:
    xml_text = vswt.build_task_xml(fake_repo)
    root = ET.fromstring(xml_text)

    def text_of(path: str) -> str:
        node = root.find(path, NS)
        assert node is not None and node.text, path
        return node.text.strip()

    assert text_of("t:RegistrationInfo/t:URI") == vswt.TASK_URI
    assert text_of("t:RegistrationInfo/t:Description") == vswt.TASK_DESCRIPTION
    assert text_of("t:Triggers/t:TimeTrigger/t:StartBoundary") == vswt.START_BOUNDARY
    assert text_of("t:Triggers/t:TimeTrigger/t:Enabled") == "true"
    assert text_of("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval") == "PT5M"
    assert text_of("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
    assert text_of("t:Settings/t:DisallowStartIfOnBatteries") == "false"
    assert text_of("t:Settings/t:StopIfGoingOnBatteries") == "false"
    assert text_of("t:Settings/t:StartWhenAvailable") == "true"
    assert text_of("t:Settings/t:ExecutionTimeLimit") == vswt.EXECUTION_TIME_LIMIT
    assert text_of("t:Settings/t:Hidden") == "true"
    assert text_of("t:Settings/t:Enabled") == "true"
    assert text_of("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert text_of("t:Principals/t:Principal/t:RunLevel") == "LeastPrivilege"
    assert text_of("t:Actions/t:Exec/t:Command") == vswt.WSCRIPT
    expected_args = vswt.WSCRIPT_ARGS_TEMPLATE.format(
        vbs=vswt._repo_paths(fake_repo)["vbs"].resolve())
    assert text_of("t:Actions/t:Exec/t:Arguments") == expected_args
    assert Path(text_of("t:Actions/t:Exec/t:WorkingDirectory")).resolve() == fake_repo.resolve()
    # Action 指向 tools/voice 下本切片 VBS（绝不指向监控/恢复任务 wrapper）
    assert "run_voice_sidecar_watchdog_silent.vbs" in text_of("t:Actions/t:Exec/t:Arguments")
    assert "tools\\voice" in text_of("t:Actions/t:Exec/t:Arguments")


def test_budget_chain_cross_pin() -> None:
    """看护预算链交叉 pin：间隔 PT5M > 执行时限 PT4M > 单轮 ensure 预算 ≥
    控制器 start 最坏推算（常量取自 voice_health_sidecar_control 同源事实：
    前置归属 probe + spawn 后身份 probe 各 1×PROBE_TIMEOUT、status 落档等待
    START_STATUS_TIMEOUT、幂等分支双端口健康探测 2×HEALTH_TIMEOUT）；且
    看护间隔 < 监控管道间隔 PT15M——sidecar 死亡暴露窗收敛到一个看护周期，
    绝不超过一个监控轮次（15 分钟管道最多污染一轮）。"""
    interval = _minutes(vswt.REPETITION_INTERVAL)
    limit = _minutes(vswt.EXECUTION_TIME_LIMIT)
    controller_worst = (vhsc.PROBE_TIMEOUT_SECONDS * 2
                        + vhsc.START_STATUS_TIMEOUT_SECONDS
                        + vhsc.HEALTH_TIMEOUT_SECONDS * 2)
    assert vswt.WATCHDOG_RUN_BUDGET_SECONDS >= controller_worst
    assert limit > vswt.WATCHDOG_RUN_BUDGET_SECONDS
    assert interval > limit
    monitoring_interval = _minutes(mpt.REPETITION_INTERVAL)
    assert monitoring_interval > interval


def test_esc_unit() -> None:
    assert vswt._esc('a<b>&"c"') == "a&lt;b&gt;&amp;&quot;c&quot;"


def test_xml_escapes_special_repo_path_roundtrip(tmp_path: Path) -> None:
    repo = tmp_path / "re&po"
    xml_text = vswt.build_task_xml(repo)
    state, issues = vswt.verify_task_xml(xml_text, repo)
    assert state == vswt.STATE_INSTALLED and issues == []
    root = ET.fromstring(xml_text)
    working_dir = root.find("t:Actions/t:Exec/t:WorkingDirectory", NS)
    assert working_dir is not None and working_dir.text is not None
    assert Path(working_dir.text.strip()).resolve() == repo.resolve()


# ---------------------------------------------------------------- verify 四态


def test_verify_installed_roundtrip(fake_repo: Path) -> None:
    state, issues = vswt.verify_task_xml(vswt.build_task_xml(fake_repo), fake_repo)
    assert state == vswt.STATE_INSTALLED and issues == []


def test_verify_accepts_scheduler_normalization(fake_repo: Path) -> None:
    xml_text = normalize_task_xml(vswt.build_task_xml(fake_repo))
    state, issues = vswt.verify_task_xml(xml_text, fake_repo)
    assert state == vswt.STATE_INSTALLED and issues == []


def test_verify_normalization_not_accepted_when_other_field_drifts(
        fake_repo: Path) -> None:
    drifted = normalize_task_xml(vswt.build_task_xml(fake_repo)).replace(
        "<Interval>PT5M</Interval>", "<Interval>PT15M</Interval>")
    state, issues = vswt.verify_task_xml(drifted, fake_repo)
    assert state == vswt.STATE_MALFORMED
    assert "Triggers/TimeTrigger/Repetition/Interval" in issues
    # 省略的默认值不再获认可（其余字段已漂移）
    assert "Triggers/TimeTrigger/Enabled" in issues


def test_verify_explicit_nondefault_enabled_rejected(fake_repo: Path) -> None:
    xml_text = vswt.build_task_xml(fake_repo).replace(
        "    <Hidden>true</Hidden>\n    <Enabled>true</Enabled>",
        "    <Hidden>true</Hidden>\n    <Enabled>false</Enabled>")
    state, issues = vswt.verify_task_xml(xml_text, fake_repo)
    assert state == vswt.STATE_MALFORMED and "Settings/Enabled" in issues


@pytest.mark.parametrize("mutation,field", [
    (lambda x: x.replace(vswt.TASK_DESCRIPTION, "other"), "RegistrationInfo/Description"),
    (lambda x: x.replace("<Hidden>true</Hidden>", "<Hidden>false</Hidden>"), "Settings/Hidden"),
    (lambda x: x.replace("PT5M", "PT15M"), "Triggers/TimeTrigger/Repetition/Interval"),
    (lambda x: x.replace(vswt.START_BOUNDARY, "2030-01-01T00:00:00"),
     "Triggers/TimeTrigger/StartBoundary"),
    (lambda x: x.replace(vswt.EXECUTION_TIME_LIMIT, "PT2H"), "Settings/ExecutionTimeLimit"),
    (lambda x: x.replace("<MultipleInstancesPolicy>IgnoreNew",
                         "<MultipleInstancesPolicy>Parallel"),
     "Settings/MultipleInstancesPolicy"),
    (lambda x: x.replace("<LogonType>InteractiveToken", "<LogonType>S4U"),
     "Principals/Principal/LogonType"),
    (lambda x: x.replace("<StartWhenAvailable>true", "<StartWhenAvailable>false"),
     "Settings/StartWhenAvailable"),
    (lambda x: x.replace("<RunLevel>LeastPrivilege", "<RunLevel>HighestAvailable"),
     "Principals/Principal/RunLevel"),
    (lambda x: x.replace("<DisallowStartIfOnBatteries>false",
                         "<DisallowStartIfOnBatteries>true"),
     "Settings/DisallowStartIfOnBatteries"),
])
def test_verify_malformed_field_drift_reports_name(fake_repo: Path, mutation, field) -> None:
    xml_text = mutation(vswt.build_task_xml(fake_repo))
    state, issues = vswt.verify_task_xml(xml_text, fake_repo)
    assert state == vswt.STATE_MALFORMED, field
    assert field in issues


def test_verify_malformed_missing_elements(fake_repo: Path) -> None:
    xml_text = vswt.build_task_xml(fake_repo).replace(
        "<WorkingDirectory>" + vswt._esc(fake_repo.resolve()) + "</WorkingDirectory>", "")
    state, issues = vswt.verify_task_xml(xml_text, fake_repo)
    assert state == vswt.STATE_MALFORMED and "Actions/Exec/WorkingDirectory" in issues
    no_trigger = vswt.build_task_xml(fake_repo).replace("<TimeTrigger>", "<CalendarTrigger>")
    state, _ = vswt.verify_task_xml(no_trigger, fake_repo)
    assert state == vswt.STATE_MALFORMED


def test_verify_unattributable_task_is_foreign(fake_repo: Path) -> None:
    """可解析但无归属标识（URI 缺失）——不可归属 = 非本工具（foreign）。"""
    state, _issues = vswt.verify_task_xml("<Task></Task>", fake_repo)
    assert state == vswt.STATE_FOREIGN


def test_verify_malformed_garbage_xml(fake_repo: Path) -> None:
    state, issues = vswt.verify_task_xml("not xml at all <", fake_repo)
    assert state == vswt.STATE_MALFORMED and issues


@pytest.mark.parametrize("payload", [
    '<?xml version="1.0"?><!DOCTYPE Task [<!ENTITY x "y">]><Task/>',
    '<?xml version="1.0"?><Task xmlns="urn:t"><!ENTITY undeclared></Task>',
])
def test_verify_malformed_doctype_entity(fake_repo: Path, payload: str) -> None:
    state, issues = vswt.verify_task_xml(payload, fake_repo)
    assert state == vswt.STATE_MALFORMED and issues


def test_verify_foreign_uri_or_command(fake_repo: Path) -> None:
    xml_text = vswt.build_task_xml(fake_repo).replace(vswt.TASK_URI, "urn:someone:else")
    state, _issues = vswt.verify_task_xml(xml_text, fake_repo)
    assert state == vswt.STATE_FOREIGN
    xml_text = vswt.build_task_xml(fake_repo).replace(">wscript.exe<", ">evil.exe<")
    state, _issues = vswt.verify_task_xml(xml_text, fake_repo)
    assert state == vswt.STATE_FOREIGN


def test_verify_foreign_arguments(fake_repo: Path) -> None:
    """Action 参数漂移（VBS 被换指向）按 malformed 计——归属标识仍匹配。"""
    xml_text = vswt.build_task_xml(fake_repo).replace(
        "//B //Nologo", "//B //Nologo-extra")
    state, issues = vswt.verify_task_xml(xml_text, fake_repo)
    assert state == vswt.STATE_MALFORMED
    assert "Actions/Exec/Arguments" in issues


# ---------------------------------------------------------------- decode 四形态


@pytest.mark.parametrize("encode", [
    lambda text: text.encode("utf-16-le"),                       # 无 BOM LE
    lambda text: b"\xff\xfe" + text.encode("utf-16-le"),         # LE BOM
    lambda text: b"\xfe\xff" + text.encode("utf-16-be"),         # BE BOM
    lambda text: text.encode("utf-8"),                           # UTF-8 prolog
])
def test_decode_schtasks_xml_forms(encode) -> None:
    text = '<?xml version="1.0"?><Task><A>x</A></Task>'
    assert vswt.decode_schtasks_xml(encode(text)) == text


@pytest.mark.parametrize("data", [
    b"\xc4\xe3\xba\xc3",          # OEM 乱码（非可识别形态）
    b"\xff\xfe<",                 # LE BOM + 奇数截断
    b"plain text",                # 非 XML
])
def test_decode_schtasks_xml_rejects(data: bytes) -> None:
    with pytest.raises(ValueError):
        vswt.decode_schtasks_xml(data)


# ---------------------------------------------------------------- 白名单门


def test_gate_readonly_allows_two_query_forms() -> None:
    assert vswt.is_readonly_schtasks_command(
        ("schtasks.exe", "/Query", "/FO", "CSV", "/NH"))
    assert vswt.is_readonly_schtasks_command(
        ("schtasks.exe", "/Query", "/TN", vswt.TASK_NAME, "/XML"))


@pytest.mark.parametrize("argv", [
    ("schtasks.exe", "/Run", "/TN", "AIOS-Voice-Sidecar-Watchdog"),
    ("schtasks.exe", "/Change", "/TN", "AIOS-Voice-Sidecar-Watchdog", "/Enable"),
    ("schtasks.exe", "/End", "/TN", "AIOS-Voice-Sidecar-Watchdog"),
    ("schtasks.exe", "/Query", "/TN", "AIOS-Voice-Sidecar-Watchdog"),  # 非白名单查询形态
    ("schtasks.exe", "/Create", "/TN", "AIOS-Voice-Sidecar-Watchdog"),
    ("schtasks.exe", "/Delete", "/TN", "AIOS-Voice-Sidecar-Watchdog"),
    ("schtasks.exe", "/Create", "/TN", "AIOS-Voice-Sidecar-Watchdog", "/F", "/XML", "x.xml"),
    ("schtasks.exe", "/Delete", "/TN", "Other-Task", "/F"),
    ("schtasks.exe", "/Create", "/TN", "AIOS-Voice-Sidecar-Watchdog", "/XML", "x.xml", "/F"),
    ("schtasks.exe", "/Create", "/TN", "AIOS-Voice-Sidecar-Watchdog", "/XML", "task.txt"),
    ("schtasks.exe", "/Create", "/TN", "AIOS-Voice-Sidecar-Watchdog", "/XML", "-F.xml"),
    ("schtasks.exe", "/Create", "/TN", "AIOS-Voice-Sidecar-Watchdog", "/XML", "a.xml", "b.xml"),
    ("cmd.exe", "/c", "schtasks.exe", "/Query", "/FO", "CSV", "/NH"),
])
def test_gate_rejects_non_whitelisted(argv) -> None:
    assert not vswt.is_readonly_schtasks_command(argv)
    mutating = argv[1:2] == ("/Create",) or argv[1:2] == ("/Delete",)
    assert not (mutating and (vswt.is_create_command(argv) or vswt.is_delete_command(argv)))


def test_gate_create_accepts_posix_tempfile_value_position() -> None:
    """PR #89 CI 回归：/XML 后唯一 token 是值位置——POSIX 绝对 tempfile
    路径（``/`` 起头）是路径而非旗标。"""
    argv = ("schtasks.exe", "/Create", "/TN", vswt.TASK_NAME,
            "/XML", "/tmp/aios-voice-sidecar-watchdog-abc.xml")
    assert vswt.is_create_command(argv)


def test_gate_mutation_flag_required() -> None:
    fake = FakeSchtasks(repo_root=Path("."))
    gate = vswt.GatedSchtasks(fake, allow_mutation=False)
    with pytest.raises(vswt.RunnerError):
        gate.run(("schtasks.exe", "/Create", "/TN", vswt.TASK_NAME,
                  "/XML", "x.xml"))
    with pytest.raises(vswt.RunnerError):
        gate.run(("schtasks.exe", "/Delete", "/TN", vswt.TASK_NAME, "/F"))
    assert fake.calls == []


def test_gate_mutation_allows_exact_forms(tmp_path: Path) -> None:
    fake = FakeSchtasks(repo_root=Path("."))
    gate = vswt.GatedSchtasks(fake, allow_mutation=True)
    gate.run(("schtasks.exe", "/Query", "/FO", "CSV", "/NH"))
    xml_file = tmp_path / "t.xml"
    xml_file.write_text("x", encoding="utf-8")
    gate.run(("schtasks.exe", "/Create", "/TN", vswt.TASK_NAME,
              "/XML", str(xml_file)))
    gate.run(("schtasks.exe", "/Delete", "/TN", vswt.TASK_NAME, "/F"))
    assert len(fake.calls) == 3


# ---------------------------------------------------------------- plan


def test_plan_missing_preflight_ok(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo)
    code = vswt.cmd_plan(runner=_gated(fake), repo_root=fake_repo, log=_log_spy()[1])
    assert code == vswt.EXIT_OK
    queries = [argv for argv in fake.calls if "/Query" in argv]
    assert len(queries) == 1
    assert not any("/Create" in " ".join(argv) or "/Delete" in " ".join(argv)
                   for argv in fake.calls)


def test_plan_preflight_missing_venv(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    voice = repo / "tools" / "voice"
    voice.mkdir(parents=True)
    for name in ("voice_health_sidecar_control.py", "voice_health_sidecar.py",
                 "run_voice_sidecar_watchdog_silent.vbs"):
        (voice / name).write_text("# fake", encoding="utf-8")
    fake = FakeSchtasks(repo_root=repo)
    code = vswt.cmd_plan(runner=_gated(fake), repo_root=repo, log=_log_spy()[1])
    assert code == vswt.EXIT_ERROR


def test_plan_task_already_installed(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo, task_state="installed",
                        xml_text=vswt.build_task_xml(fake_repo))
    code = vswt.cmd_plan(runner=_gated(fake), repo_root=fake_repo, log=_log_spy()[1])
    assert code == vswt.EXIT_ERROR
    assert not any("/Create" in " ".join(argv) for argv in fake.calls)


def test_plan_query_incomplete(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo, list_rc=1)
    code = vswt.cmd_plan(runner=_gated(fake), repo_root=fake_repo, log=_log_spy()[1])
    assert code == vswt.EXIT_ERROR


# ---------------------------------------------------------------- generate


def test_generate_writes_utf16_bom_and_roundtrips(fake_repo: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    fake = FakeSchtasks(repo_root=fake_repo)
    code = vswt.cmd_generate(repo_root=fake_repo, log=_log_spy()[1], out_dir=out_dir)
    assert code == vswt.EXIT_OK
    out_path = out_dir / vswt.GENERATE_OUTPUT_NAME
    assert out_path.is_file()
    raw = out_path.read_bytes()
    assert raw.startswith(b"\xff\xfe")  # UTF-16 with BOM（与 XML 声明/安装字节一致）
    decoded = vswt.decode_schtasks_xml(raw)
    state, _ = vswt.verify_task_xml(decoded, fake_repo)
    assert state == vswt.STATE_INSTALLED
    assert fake.calls == []  # generate 零 schtasks 调用


def test_generate_default_dir_is_gitignored_artifacts(fake_repo: Path) -> None:
    """默认导出目录 = gitignored .verify/artifacts/m14-77-voice-sidecar-watchdog。"""
    default = fake_repo / ".verify" / "artifacts" / "m14-77-voice-sidecar-watchdog"
    code = vswt.cmd_generate(repo_root=fake_repo, log=_log_spy()[1])
    assert code == vswt.EXIT_OK
    assert (default / vswt.GENERATE_OUTPUT_NAME).is_file()


def test_generate_symlink_rejected(fake_repo: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    target = out_dir / vswt.GENERATE_OUTPUT_NAME
    try:
        target.symlink_to(out_dir / "elsewhere.xml")
    except OSError:
        pytest.skip("平台不支持 symlink")
    code = vswt.cmd_generate(repo_root=fake_repo, log=_log_spy()[1], out_dir=out_dir)
    assert code == vswt.EXIT_REFUSED
    assert not (out_dir / "elsewhere.xml").exists()


# ---------------------------------------------------------------- status


def test_status_states(fake_repo: Path) -> None:
    cases = [
        ("installed", vswt.STATE_INSTALLED, vswt.EXIT_OK),
        ("missing", vswt.STATE_MISSING, vswt.EXIT_MISSING),
        ("foreign", vswt.STATE_FOREIGN, vswt.EXIT_FOREIGN),
        ("malformed", vswt.STATE_MALFORMED, vswt.EXIT_MALFORMED),
    ]
    for task_state, expected_state, expected_code in cases:
        xml_text = vswt.build_task_xml(fake_repo)
        if task_state == "foreign":
            xml_text = xml_text.replace(vswt.TASK_URI, "urn:someone:else")
        elif task_state == "malformed":
            xml_text = xml_text.replace("PT5M", "PT30M")
        fake = FakeSchtasks(repo_root=fake_repo, task_state=task_state,
                            xml_text=xml_text)
        code = vswt.cmd_status(runner=_gated(fake), repo_root=fake_repo,
                               log=_log_spy()[1])
        assert code == expected_code, task_state


def test_status_unknown(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo, list_rc=1)
    code = vswt.cmd_status(runner=_gated(fake), repo_root=fake_repo,
                           log=_log_spy()[1])
    assert code == vswt.EXIT_ERROR


# ---------------------------------------------------------------- install / uninstall


@pytest.mark.parametrize("confirm", ["", "EXECUTE MONITORING SCHEDULER CHANGE",
                                     "execute voice sidecar watchdog scheduler change"])
def test_install_phrase_gate(fake_repo: Path, confirm: str) -> None:
    assert vswt.main(["install", "--confirm", confirm],
                     runner=_gated(FakeSchtasks(repo_root=fake_repo),
                                   allow_mutation=True),
                     log=_log_spy()[1]) == vswt.EXIT_ERROR


@pytest.mark.parametrize("confirm", ["", "EXECUTE MONITORING SCHEDULER CHANGE"])
def test_uninstall_phrase_gate(fake_repo: Path, confirm: str) -> None:
    assert vswt.main(["uninstall", "--confirm", confirm],
                     runner=_gated(FakeSchtasks(repo_root=fake_repo),
                                   allow_mutation=True),
                     log=_log_spy()[1]) == vswt.EXIT_ERROR


def test_install_phrase_gate_zero_calls(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo)
    vswt.main(["install", "--confirm", ""], runner=_gated(fake, allow_mutation=True),
              log=_log_spy()[1])
    assert fake.calls == []


def test_install_happy_path(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo)
    code = vswt.cmd_install(runner=_gated(fake, allow_mutation=True),
                            repo_root=fake_repo, log=_log_spy()[1])
    assert code == vswt.EXIT_OK
    creates = [argv for argv in fake.calls if "/Create" in argv]
    assert len(creates) == 1
    assert creates[0][:4] == ("schtasks.exe", "/Create", "/TN", vswt.TASK_NAME)
    assert creates[0][4] == "/XML" and creates[0][5].endswith(".xml")
    # 临时 XML 字节 = build_task_xml 的 UTF-16 编码；用后即删
    assert fake.created_xml_bytes == vswt.build_task_xml(fake_repo).encode("utf-16")
    assert not Path(creates[0][5]).exists()


def test_install_refuses_existing_task(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo, task_state="installed",
                        xml_text=vswt.build_task_xml(fake_repo))
    code = vswt.cmd_install(runner=_gated(fake, allow_mutation=True),
                            repo_root=fake_repo, log=_log_spy()[1])
    assert code == vswt.EXIT_ERROR
    assert not any("/Create" in " ".join(argv) for argv in fake.calls)


def test_install_refuses_when_second_check_not_missing(fake_repo: Path) -> None:
    """二次存在性复核不确认 missing → 拒绝（fail-closed，绝不覆盖）：
    首次 query 报 missing 但二次全量列表复核时任务已列入（另一进程抢先
    注册同形任务）——该分支由 query_task 非 missing 的既有拒绝面覆盖，
    此处锁定「任务已在列 → install 零 /Create」。"""
    fake2 = FakeSchtasks(repo_root=fake_repo, task_state="installed",
                         xml_text=vswt.build_task_xml(fake_repo))
    code = vswt.cmd_install(runner=_gated(fake2, allow_mutation=True),
                            repo_root=fake_repo, log=_log_spy()[1])
    assert code == vswt.EXIT_ERROR
    assert not any("/Create" in " ".join(argv) for argv in fake2.calls)


def test_uninstall_exact_owned(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo, task_state="installed",
                        xml_text=vswt.build_task_xml(fake_repo))
    code = vswt.cmd_uninstall(runner=_gated(fake, allow_mutation=True),
                              repo_root=fake_repo, log=_log_spy()[1])
    assert code == vswt.EXIT_OK
    deletes = [argv for argv in fake.calls if "/Delete" in argv]
    assert deletes == [("schtasks.exe", "/Delete", "/TN", vswt.TASK_NAME, "/F")]
    assert not any("/Create" in " ".join(argv) for argv in fake.calls)


def test_uninstall_missing_idempotent(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo)
    code = vswt.cmd_uninstall(runner=_gated(fake, allow_mutation=True),
                              repo_root=fake_repo, log=_log_spy()[1])
    assert code == vswt.EXIT_OK
    assert not any("/Delete" in " ".join(argv) for argv in fake.calls)


@pytest.mark.parametrize("state,expected", [
    ("foreign", vswt.EXIT_FOREIGN),
    ("malformed", vswt.EXIT_MALFORMED),
    ("unknown", vswt.EXIT_ERROR),
])
def test_uninstall_non_owned_zero_delete(fake_repo: Path, state: str, expected: int) -> None:
    xml_text = vswt.build_task_xml(fake_repo)
    if state == "foreign":
        xml_text = xml_text.replace(vswt.TASK_URI, "urn:someone:else")
    elif state == "malformed":
        xml_text = xml_text.replace("PT5M", "PT15M")
    fake = FakeSchtasks(repo_root=fake_repo, task_state=state, xml_text=xml_text)
    if state == "unknown":
        fake = FakeSchtasks(repo_root=fake_repo, list_rc=1)
    code = vswt.cmd_uninstall(runner=_gated(fake, allow_mutation=True),
                              repo_root=fake_repo, log=_log_spy()[1])
    assert code == expected
    assert not any("/Delete" in " ".join(argv) for argv in fake.calls)


# ---------------------------------------------------------------- VBS wrapper / 源码契约 / 身份不冲突


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
    assert "voice_health_sidecar_control.py" in text
    # 恒带 start 子命令（幂等 ensure：活着跳过、死了重启、拒绝时可见失败）
    assert '& """ start"' in text
    # 预检失败专用退出码
    assert "WScript.Quit 2" in text and "WScript.Quit 3" in text
    for pattern in SECRET_PATTERNS:
        assert pattern not in text
    for token in ("http://", "https://", "mshta", "powershell", "cmd.exe",
                  "CreateTextFile"):
        assert token not in text, token


def test_no_identity_collision_with_existing_tasks() -> None:
    """与 M14-14 监控管道任务 / M14-06 恢复任务零身份冲突（同一机器并存）。"""
    for other in (mpt, wst):
        assert vswt.TASK_NAME != other.TASK_NAME
        assert vswt.TASK_URI != other.TASK_URI
        assert vswt.NORMALIZED_TASK_URI != other.NORMALIZED_TASK_URI
        assert vswt.TASK_DESCRIPTION != other.TASK_DESCRIPTION
    assert VBS.name != "run_monitoring_pipeline_silent.vbs"
    assert VBS.name != "run_production_recovery_silent.vbs"
    assert "run_voice_sidecar_watchdog_silent.vbs" in vswt.WSCRIPT_ARGS_TEMPLATE.format(
        vbs=vswt._repo_paths(REPO_ROOT)["vbs"])
    xml_text = vswt.build_task_xml(REPO_ROOT)
    assert "monitoring_pipeline" not in xml_text
    assert "production_recovery" not in xml_text


def test_confirm_phrase_distinct_from_monitoring_task() -> None:
    """确认短语与 M14-14 任务管理器不同（变更面隔离：看护任务注册不共享
    监控调度变更授权）。"""
    assert vswt.TASK_CONFIRM_PHRASE != mpt.TASK_CONFIRM_PHRASE


def test_source_contract_mutation_tokens_gated() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"/Run"' not in source          # 绝不运行任务
    assert '"/Change"' not in source and '"/End"' not in source
    # "/F" 仅出现在 schtasks argv 定义行（delete 白名单 + exact-owned 删除）
    for line in source.splitlines():
        if '"/F"' in line:
            assert "schtasks" in line or "TASK_NAME" in line, line
    # /Create 行绝不带 /F；写路径仅 /Create 与 /Delete 两字面量族
    for line in source.splitlines():
        if '"/Create"' in line:
            assert "/F" not in line
    assert source.count('"/Create"') == 2 and source.count('"/Delete"') == 2
    # 零 shell 展开
    assert "shell=" not in source
    for pattern in SECRET_PATTERNS:
        assert pattern not in source


def test_repo_paths_pin_watchdog_targets() -> None:
    """preflight/VBS/任务 Action 的调用目标交叉锁定：控制器脚本（幂等
    ensure 入口）与 sidecar 脚本恒在 tools/voice 下。"""
    paths = vswt._repo_paths(REPO_ROOT)
    assert paths["controller"] == CONTROLLER_SCRIPT
    assert paths["vbs"] == VBS
    assert paths["sidecar"] == REPO_ROOT / "tools" / "voice" / "voice_health_sidecar.py"
