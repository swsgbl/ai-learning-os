r"""M14-129 tools/ops/production_drift_watch_task.py +
run_production_drift_watch_silent.vbs 契约测试：production drift watch
独立周期计划任务 readiness 管理器（零真实 schtasks 写路径、零注册、零
Docker/零网络/零生产读取）。

覆盖（全部经 FakeSchtasks 注入 + 临时 fake repo；本开发回合 install/
uninstall 永不真实执行——短语门禁先于一切 schtasks 调用）：
- Task XML：固定身份（URI/Description 持久归属标记）+ 关键设置逐项断言
  （Hidden/IgnoreNew/StartWhenAvailable/电池双 false/ExecutionTimeLimit=
  PT10M < 重复间隔 PT15M/TimeTrigger+StartBoundary/Repetition/
  InteractiveToken+LeastPrivilege/wscript //B //Nologo + 仓库内 wrapper
  路径/WorkingDirectory=repo）；_esc 转义单元 + 特殊字符仓库路径
  round-trip；与 drift watch 工具预算交叉 pin（间隔 PT15M > 时限 PT10M >
  单轮最坏只读子进程硬顶 330s——compose ps 60 + 7 容器 inspect×30 +
  2 image inspect×30，常量逐项取自 production_drift_watch.py）；
- verify_task_xml 四态：installed（round-trip 及 Task Scheduler 归一化
  形态——URI 重写/默认值元素省略按 M14-06 实证先例条件认可）/ foreign
  （URI 或 Command）/ malformed（字段缺失/漂移逐项报告字段名；XML 垃圾；
  DOCTYPE/ENTITY 拒绝——XXE 防护）；省略的默认值仅在其余字段全部精确时
  认可；
- decode_schtasks_xml 四形态（UTF-16LE 无 BOM/LE BOM/BE BOM/UTF-8 prolog）
  与拒绝面（OEM 乱码/奇数截断/非 XML）；
- 白名单门（结构性）：只读门恒放行两查询形态、拒绝一切 mutation（含
  /Run//Change//End 形态与带 /F 的 create）；mutation 门仅放行
  ``/Create /TN <固定名> /XML <单路径>`` 与 ``/Delete /TN <固定名> /F``
  两精确形态（create 绝不带 /F、任务名恒固定）；``/XML`` 后唯一 token
  是值位置——POSIX 绝对 tempfile 路径（``/`` 起头）是路径而非旗标
  （M14-14 PR #89 R3 回归），旗标形态逐项拒绝；
- plan：任务缺失+预检过 → exit 0（仅一次列表查询，零 mutation）；预检缺
  项（无 .venv）→ exit 1 fail-closed；任务存在/事实不完整 → exit 1；
- generate：XML 原子导出到临时目录 + verify 回读 installed + 零 schtasks
  调用；输出路径 symlink 拒绝零写入；产物为 UTF-16 with BOM 字节（与
  XML 声明及 install 临时字节一致）——逐字节断言 BOM 起始 + 外部解析
  语义（BOM 感知解码）+ 声明匹配 + 本工具解码器/归属校验通过；
- status 五态 + 退出码（installed 0/missing 2/foreign 3/malformed 4/
  unknown 1）；
- install/uninstall 短语门禁：缺失/近似短语（含 drift watch 自身的
  execute 短语与 M14-14 的 scheduler 短语——两短语绝不互通）→ exit 1
  且零 schtasks 调用；install happy path（FakeSchtasks：临时 XML 内容
  精确等于 build_task_xml、UTF-16、用后即删、归一化回读复查通过）；
  install 拒绝同名（installed/列表复核不确认 missing）；uninstall 仅
  exact-owned 才 /Delete /F、missing 幂等 exit 0、foreign/malformed/
  unknown 零删除；
- VBS wrapper 契约：无盘符硬编码、无 secret、隐藏窗口 Run(...,0,True)、
  WScript.Quit 透传、仓库根推导、固定 venv python + production_drift_watch.py
  调用目标、携带 --execute 与 drift watch 精确确认短语（与
  production_drift_watch.CONFIRM_PHRASE 交叉 pin）、预检退出码 2/3/4；
- 与 AIOS-Monitoring-Pipeline（M14-14）、AIOS-Production-Recovery
  （M14-06）、AIOS-Voice-Sidecar-Watchdog（M14-77）无任务名/URI/描述/
  VBS 身份冲突；本工具 XML 只指向自己的 wrapper；
- 源码契约：无 '"/Run"'；'"/F"' 仅出现在 schtasks argv 定义行；'"/Create"'
  行绝不带 /F；零 secret 形态、零 env 读取、零 docker/网络面；
  subprocess 执行点无 shell=。
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "production_drift_watch_task.py"
DRIFT_WATCH_SCRIPT = REPO_ROOT / "tools" / "ops" / "production_drift_watch.py"
VBS = REPO_ROOT / "tools" / "ops" / "run_production_drift_watch_silent.vbs"
MONITORING_TASK_SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_pipeline_task.py"
MONITORING_VBS = REPO_ROOT / "tools" / "ops" / "run_monitoring_pipeline_silent.vbs"
STARTUP_TASK_SCRIPT = REPO_ROOT / "tools" / "ops" / "windows_startup_task.py"
STARTUP_VBS = REPO_ROOT / "tools" / "ops" / "run_production_recovery_silent.vbs"
VOICE_TASK_SCRIPT = REPO_ROOT / "tools" / "voice" / "voice_sidecar_watchdog_task.py"
VOICE_VBS = REPO_ROOT / "tools" / "voice" / "run_voice_sidecar_watchdog_silent.vbs"
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


pdwt = _load_module(SCRIPT, "production_drift_watch_task_under_test")
pdw = _load_module(DRIFT_WATCH_SCRIPT, "production_drift_watch_for_task_tests")
mpt = _load_module(MONITORING_TASK_SCRIPT, "monitoring_pipeline_task_for_collision_pin")
wst = _load_module(STARTUP_TASK_SCRIPT, "windows_startup_task_for_collision_pin")
vswt = _load_module(VOICE_TASK_SCRIPT, "voice_sidecar_watchdog_task_for_collision_pin")


def _log_spy() -> tuple[list[str], object]:
    lines: list[str] = []
    return lines, lines.append


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    ops = repo / "tools" / "ops"
    ops.mkdir(parents=True)
    for name in ("production_drift_watch.py", "run_production_drift_watch_silent.vbs"):
        (ops / name).write_text("# fake", encoding="utf-8")
    venv_scripts = repo / ".venv" / "Scripts"
    venv_scripts.mkdir(parents=True)
    (venv_scripts / "python.exe").write_bytes(b"fake")
    return repo


def normalize_task_xml(xml_text: str) -> str:
    """模拟 Task Scheduler 归一化存储（M14-06 生产 + COM 快照实证形态）：
    URI 重写为 ``\\<TaskName>``；省略值恰为 Windows 默认值的元素
    （TimeTrigger/Enabled、Settings/Enabled、Principal/RunLevel）；
    其余逐字保留。"""
    text = xml_text.replace(pdwt.TASK_URI, pdwt.NORMALIZED_TASK_URI)
    text = text.replace(
        f"      <StartBoundary>{pdwt.START_BOUNDARY}</StartBoundary>\n"
        f"      <Enabled>true</Enabled>\n",
        f"      <StartBoundary>{pdwt.START_BOUNDARY}</StartBoundary>\n")
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
                return pdwt.CommandResult(tokens, self._list_rc, "", "err")
            stdout = ('"\\\\SomeOtherTask","Foo"\n' if not listed
                      else f'"{pdwt.TASK_NAME}","AIOS Production Drift Watch"\n')
            return pdwt.CommandResult(tokens, 0, stdout, "")
        if tokens[1] == "/Create":
            self.created_xml_bytes = Path(tokens[5]).read_bytes()
            if self._flip and self._create_rc == 0:
                self._task_state = "installed"
                self._xml_text = normalize_task_xml(
                    pdwt.build_task_xml(self._repo_root))
            return pdwt.CommandResult(tokens, self._create_rc, "", "")
        if tokens[1] == "/Delete":
            if self._delete_rc == 0:
                self._task_state = "missing"
            return pdwt.CommandResult(tokens, self._delete_rc, "", "")
        raise AssertionError(f"unexpected argv: {tokens}")

    def run_raw(self, argv, *, timeout: float = 30.0):
        tokens = tuple(str(item) for item in argv)
        self.calls.append(tokens)
        assert tokens[1:5] == ("/Query", "/TN", pdwt.TASK_NAME, "/XML")
        if self._task_state == "missing":
            return pdwt.RawCommandResult(tokens, 1, b"")
        payload = self._xml_text.encode("utf-16-le")
        return pdwt.RawCommandResult(tokens, 0, payload)


def _gated(fake: FakeSchtasks, *, allow_mutation: bool = False) -> pdwt.GatedSchtasks:
    return pdwt.GatedSchtasks(fake, allow_mutation=allow_mutation)


def _minutes(spec: str) -> int:
    match = re.fullmatch(r"PT(\d+)M", spec)
    assert match is not None, spec
    return int(match.group(1)) * 60


# ---------------------------------------------------------------- Task XML


def test_build_task_xml_key_fields(fake_repo: Path) -> None:
    xml_text = pdwt.build_task_xml(fake_repo)
    root = ET.fromstring(xml_text)

    def text_of(path: str) -> str:
        node = root.find(path, NS)
        assert node is not None and node.text, path
        return node.text.strip()

    assert text_of("t:RegistrationInfo/t:URI") == pdwt.TASK_URI
    assert text_of("t:RegistrationInfo/t:Description") == pdwt.TASK_DESCRIPTION
    assert text_of("t:Triggers/t:TimeTrigger/t:StartBoundary") == pdwt.START_BOUNDARY
    assert text_of("t:Triggers/t:TimeTrigger/t:Enabled") == "true"
    assert text_of("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval") == "PT15M"
    assert text_of("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
    assert text_of("t:Settings/t:DisallowStartIfOnBatteries") == "false"
    assert text_of("t:Settings/t:StopIfGoingOnBatteries") == "false"
    assert text_of("t:Settings/t:StartWhenAvailable") == "true"
    assert text_of("t:Settings/t:ExecutionTimeLimit") == pdwt.EXECUTION_TIME_LIMIT
    assert text_of("t:Settings/t:Hidden") == "true"
    assert text_of("t:Settings/t:Enabled") == "true"
    assert text_of("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert text_of("t:Principals/t:Principal/t:RunLevel") == "LeastPrivilege"
    assert text_of("t:Actions/t:Exec/t:Command") == pdwt.WSCRIPT
    expected_args = pdwt.WSCRIPT_ARGS_TEMPLATE.format(
        vbs=pdwt._repo_paths(fake_repo)["vbs"].resolve())
    assert text_of("t:Actions/t:Exec/t:Arguments") == expected_args
    assert Path(text_of("t:Actions/t:Exec/t:WorkingDirectory")).resolve() == fake_repo.resolve()
    # Repetition 无 Duration（= 无限期重复）
    assert "<Duration>" not in xml_text


def test_interval_covers_drift_watch_budget(fake_repo: Path) -> None:
    """预算交叉 pin（调度器绝不先于内部超时杀整任务）：间隔 > 执行时限 >
    drift watch 单轮最坏只读子进程硬顶。硬顶逐项取自 production_drift_watch.py
    常量——compose ps 60s + 7 容器 inspect×30s + 2 image inspect×30s = 330s
    （M14-127 固定画像：STACK_SERVICES 七服务 + EXPECTED_ANCHORS 两锚点）。"""
    interval = _minutes(pdwt.REPETITION_INTERVAL)
    limit = _minutes(pdwt.EXECUTION_TIME_LIMIT)
    assert (pdwt.REPETITION_INTERVAL, pdwt.EXECUTION_TIME_LIMIT) == ("PT15M", "PT10M")
    assert interval == 900 and limit == 600
    assert interval > limit  # PT15M=900s > PT10M=600s：调度器侧不自我重叠
    # 固定画像 pin：七 compose 服务 + 两镜像锚点（漂移即测试失败，预算随之重审）
    assert len(pdw.STACK_SERVICES) == 7
    assert len(pdw.EXPECTED_ANCHORS) == 2
    hard_ceiling = (pdw.COMPOSE_PS_TIMEOUT_SECONDS
                    + len(pdw.STACK_SERVICES) * pdw.INSPECT_TIMEOUT_SECONDS
                    + len(pdw.EXPECTED_ANCHORS) * pdw.INSPECT_TIMEOUT_SECONDS)
    assert hard_ceiling == 330.0  # 60 + 7×30 + 2×30
    assert limit > hard_ceiling  # PT10M=600s > 330s：内部超时先于调度器时限
    assert limit - hard_ceiling >= 270  # 恒留 ≥270s 给报告写入与收尾


def test_esc_unit() -> None:
    assert pdwt._esc('a<b>&"c"') == "a&lt;b&gt;&amp;&quot;c&quot;"


def test_xml_escapes_special_repo_path_roundtrip(tmp_path: Path) -> None:
    repo = tmp_path / "re&po"
    xml_text = pdwt.build_task_xml(repo)
    state, issues = pdwt.verify_task_xml(xml_text, repo)
    assert state == pdwt.STATE_INSTALLED and issues == []
    root = ET.fromstring(xml_text)
    working_dir = root.find("t:Actions/t:Exec/t:WorkingDirectory", NS)
    assert working_dir is not None and working_dir.text is not None
    assert Path(working_dir.text.strip()).resolve() == repo.resolve()


# ---------------------------------------------------------------- verify 四态


def test_verify_installed_roundtrip(fake_repo: Path) -> None:
    state, issues = pdwt.verify_task_xml(pdwt.build_task_xml(fake_repo), fake_repo)
    assert state == pdwt.STATE_INSTALLED and issues == []


def test_verify_accepts_scheduler_normalization(fake_repo: Path) -> None:
    xml_text = normalize_task_xml(pdwt.build_task_xml(fake_repo))
    state, issues = pdwt.verify_task_xml(xml_text, fake_repo)
    assert state == pdwt.STATE_INSTALLED and issues == []


def test_verify_normalization_not_accepted_when_other_field_drifts(
        fake_repo: Path) -> None:
    drifted = normalize_task_xml(pdwt.build_task_xml(fake_repo)).replace(
        "<Interval>PT15M</Interval>", "<Interval>PT5M</Interval>")
    state, issues = pdwt.verify_task_xml(drifted, fake_repo)
    assert state == pdwt.STATE_MALFORMED
    assert "Triggers/TimeTrigger/Repetition/Interval" in issues
    # 省略的默认值不再获认可（其余字段已漂移）
    assert "Triggers/TimeTrigger/Enabled" in issues


def test_verify_explicit_nondefault_enabled_rejected(fake_repo: Path) -> None:
    xml_text = pdwt.build_task_xml(fake_repo).replace(
        "    <Hidden>true</Hidden>\n    <Enabled>true</Enabled>",
        "    <Hidden>true</Hidden>\n    <Enabled>false</Enabled>")
    state, issues = pdwt.verify_task_xml(xml_text, fake_repo)
    assert state == pdwt.STATE_MALFORMED and "Settings/Enabled" in issues


@pytest.mark.parametrize("mutation,field", [
    (lambda x: x.replace(pdwt.TASK_DESCRIPTION, "other"), "RegistrationInfo/Description"),
    (lambda x: x.replace("<Hidden>true</Hidden>", "<Hidden>false</Hidden>"), "Settings/Hidden"),
    (lambda x: x.replace("PT15M", "PT5M"), "Triggers/TimeTrigger/Repetition/Interval"),
    (lambda x: x.replace(pdwt.START_BOUNDARY, "2030-01-01T00:00:00"),
     "Triggers/TimeTrigger/StartBoundary"),
    (lambda x: x.replace(pdwt.EXECUTION_TIME_LIMIT, "PT2H"), "Settings/ExecutionTimeLimit"),
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
    xml_text = mutation(pdwt.build_task_xml(fake_repo))
    state, issues = pdwt.verify_task_xml(xml_text, fake_repo)
    assert state == pdwt.STATE_MALFORMED, field
    assert field in issues


def test_verify_malformed_missing_elements(fake_repo: Path) -> None:
    xml_text = pdwt.build_task_xml(fake_repo).replace(
        "<WorkingDirectory>" + pdwt._esc(fake_repo.resolve()) + "</WorkingDirectory>", "")
    state, issues = pdwt.verify_task_xml(xml_text, fake_repo)
    assert state == pdwt.STATE_MALFORMED and "Actions/Exec/WorkingDirectory" in issues
    no_trigger = pdwt.build_task_xml(fake_repo).replace("<TimeTrigger>", "<CalendarTrigger>")
    state, _ = pdwt.verify_task_xml(no_trigger, fake_repo)
    assert state == pdwt.STATE_MALFORMED


def test_verify_malformed_garbage(fake_repo: Path) -> None:
    state, _issues = pdwt.verify_task_xml("not xml at all", fake_repo)
    assert state == pdwt.STATE_MALFORMED


def test_verify_unattributable_task_is_foreign(fake_repo: Path) -> None:
    """可解析但无归属标识（URI 缺失）——不可归属 = 非本工具（foreign）。"""
    state, _issues = pdwt.verify_task_xml("<Task></Task>", fake_repo)
    assert state == pdwt.STATE_FOREIGN


def test_verify_rejects_doctype_entity(fake_repo: Path) -> None:
    evil = ("<?xml version='1.0'?><!DOCTYPE Task [<!ENTITY xxe SYSTEM 'file:///c:/win.ini'>]>"
            "<Task>&xe;</Task>")
    state, issues = pdwt.verify_task_xml(evil, fake_repo)
    assert state == pdwt.STATE_MALFORMED
    assert any("DOCTYPE" in issue or "ENTITY" in issue for issue in issues)


def test_verify_foreign_uri_or_command(fake_repo: Path) -> None:
    xml_text = pdwt.build_task_xml(fake_repo)
    foreign_uri = xml_text.replace(pdwt.TASK_URI, "urn:someone:else:task")
    assert pdwt.verify_task_xml(foreign_uri, fake_repo)[0] == pdwt.STATE_FOREIGN
    foreign_cmd = xml_text.replace(f"<Command>{pdwt.WSCRIPT}</Command>",
                                   "<Command>powershell.exe</Command>")
    assert pdwt.verify_task_xml(foreign_cmd, fake_repo)[0] == pdwt.STATE_FOREIGN


# ---------------------------------------------------------------- 解码


def test_decode_four_byte_forms() -> None:
    text = "<?xml version=\"1.0\"?><Task/>"
    assert pdwt.decode_schtasks_xml(text.encode("utf-16-le")) == text
    assert pdwt.decode_schtasks_xml(b"\xff\xfe" + text.encode("utf-16-le")) == text
    assert pdwt.decode_schtasks_xml(b"\xfe\xff" + text.encode("utf-16-be")) == text
    assert pdwt.decode_schtasks_xml(text.encode("utf-8")) == text


@pytest.mark.parametrize("bad", [b"", b"garbage", b"\xff\xfe\x00", b"3c\x00\x00?x"])
def test_decode_rejects_unknown_forms(bad: bytes) -> None:
    with pytest.raises(ValueError):
        pdwt.decode_schtasks_xml(bad)


# ---------------------------------------------------------------- 白名单门（结构性）


class _RecordingInner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout: float = 60.0):
        self.calls.append(tuple(str(x) for x in argv))
        return pdwt.CommandResult(tuple(str(x) for x in argv), 0, "", "")

    def run_raw(self, argv, *, timeout: float = 30.0):
        self.calls.append(tuple(str(x) for x in argv))
        return pdwt.RawCommandResult(tuple(str(x) for x in argv), 0, b"<?xml")


@pytest.mark.parametrize("argv", [
    ["schtasks.exe", "/Query", "/FO", "CSV", "/NH"],
    ["schtasks.exe", "/Query", "/TN", "AIOS-Production-Drift-Watch", "/XML"],
])
def test_gate_readonly_forms_pass_without_mutation_flag(argv) -> None:
    inner = _RecordingInner()
    gate = pdwt.GatedSchtasks(inner, allow_mutation=False)
    gate.run(argv)
    assert inner.calls == [tuple(argv)]


@pytest.mark.parametrize("argv", [
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML", "C:/tmp/x.xml"],
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML",
     "/tmp/aios-production-drift-watch-test.xml"],                # POSIX 路径也不得绕过读门
    ["schtasks.exe", "/Delete", "/TN", "AIOS-Production-Drift-Watch", "/F"],
    ["schtasks.exe", "/Run", "/TN", "AIOS-Production-Drift-Watch"],
    ["schtasks.exe", "/Change", "/TN", "AIOS-Production-Drift-Watch", "/TR", "x"],
    ["schtasks.exe", "/End", "/TN", "AIOS-Production-Drift-Watch"],
    ["schtasks.exe", "/Query", "/FO", "CSV"],                       # 缺 /NH
    ["schtasks.exe", "/Query", "/TN", "Other-Task", "/XML"],        # 非固定任务名
    ["schtasks.exe", "/Query", "/TN", "AIOS-Monitoring-Pipeline", "/XML"],  # 兄弟任务名也不放行
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML", "a", "b"],
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML", "-F"],
    ["schtasks.exe", "/Delete", "/TN", "AIOS-Production-Drift-Watch"],  # 缺 /F
    ["cmd.exe", "/c", "schtasks.exe", "/Query", "/FO", "CSV", "/NH"],
])
def test_gate_rejects_when_mutation_not_allowed(argv) -> None:
    inner = _RecordingInner()
    gate = pdwt.GatedSchtasks(inner, allow_mutation=False)
    with pytest.raises(pdwt.RunnerError):
        gate.run(argv)
    assert inner.calls == []


@pytest.mark.parametrize("argv", [
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML", "C:/tmp/x.xml"],
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML",
     "/tmp/aios-production-drift-watch-test.xml"],
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML",
     "/tmp/pytest-of-runner/popen-gw0/test_install0/aios-production-drift-watch-ab12cd.xml"],
    ["schtasks.exe", "/Delete", "/TN", "AIOS-Production-Drift-Watch", "/F"],
])
def test_gate_allows_exact_mutation_forms_when_enabled(argv) -> None:
    inner = _RecordingInner()
    gate = pdwt.GatedSchtasks(inner, allow_mutation=True)
    gate.run(argv)
    assert inner.calls == [tuple(argv)]


def test_create_allowlist_posix_tempfile_path_is_value_not_switch() -> None:
    """M14-14 PR #89 CI R3 回归：argv 形态恒定（精确前缀 + 长度 6），``/XML``
    之后的**唯一 token 是值位置**——POSIX 绝对 tempfile 路径以 ``/`` 起头是
    路径前缀而非旗标；旗标防护靠结构（.xml 后缀 + 非 ``-`` 起头 + 无多余
    token），不靠 slash 首字符。"""
    posix = "/tmp/aios-production-drift-watch-test.xml"
    ci_shaped = "/tmp/pytest-of-runner/popen-gw0/test_install0/aios-production-drift-watch-ab12cd.xml"
    windows = "C:\\Users\\runner\\AppData\\Local\\Temp\\aios-production-drift-watch-ab12cd.xml"
    accepted = [
        ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML", posix],
        ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML", ci_shaped],
        ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML", windows],
    ]
    for argv in accepted:
        assert pdwt.is_create_command(argv) is True, argv
        inner = _RecordingInner()
        pdwt.GatedSchtasks(inner, allow_mutation=True).run(argv)
        assert inner.calls == [tuple(argv)]
    rejected_values = [
        "-F",                       # dash 旗标形态
        "/F",                       # 裸 slash 旗标（非 .xml）
        "x.xml/F",                  # 尾随旗标
        "/tmp/x.xml/F",             # POSIX 路径 + 尾随旗标
        "/tmp/aios-production-drift-watch-notxml.txt",  # 非 .xml 路径
        "-.xml",                    # dash 起头伪路径
        "",
    ]
    for value in rejected_values:
        argv = ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML", value]
        assert pdwt.is_create_command(argv) is False, value
        inner = _RecordingInner()
        with pytest.raises(pdwt.RunnerError):
            pdwt.GatedSchtasks(inner, allow_mutation=True).run(argv)
        assert inner.calls == []
    # 值位置的放行不外溢到其它任何位置：路径 token 放错位置（/XML 前）仍拒
    misplaced = ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch",
                 posix, "/XML"]
    assert pdwt.is_create_command(misplaced) is False
    # 错任务名 + 任意路径值仍拒；多余 token 仍拒
    assert pdwt.is_create_command(
        ["schtasks.exe", "/Create", "/TN", "Other-Task", "/XML", posix]) is False
    assert pdwt.is_create_command(
        ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch",
         "/XML", posix, "/F"]) is False


@pytest.mark.parametrize("argv", [
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML", "C:/tmp/x.xml", "/F"],
    ["schtasks.exe", "/Create", "/TN", "Other-Task", "/XML", "C:/tmp/x.xml"],
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML", "-F"],
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML", "x.xml/F"],
    ["schtasks.exe", "/Delete", "/TN", "AIOS-Production-Drift-Watch", "/F", "/F"],
])
def test_gate_rejects_mutated_mutation_forms_even_when_enabled(argv) -> None:
    inner = _RecordingInner()
    gate = pdwt.GatedSchtasks(inner, allow_mutation=True)
    with pytest.raises(pdwt.RunnerError):
        gate.run(argv)
    assert inner.calls == []


# ---------------------------------------------------------------- plan / generate / status


def test_plan_missing_and_preflight_ok_exit0(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo, task_state="missing")
    lines, log = _log_spy()
    code = pdwt.cmd_plan(runner=_gated(fake), repo_root=fake_repo, log=log)
    assert code == pdwt.EXIT_OK
    assert fake.calls == [("schtasks.exe", "/Query", "/FO", "CSV", "/NH")]
    assert any("supervisor-only" in line for line in lines)
    assert not any("/Create" in " ".join(argv) for argv in fake.calls)


def test_plan_preflight_missing_venv_fail_closed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    ops = repo / "tools" / "ops"
    ops.mkdir(parents=True)
    (ops / "production_drift_watch.py").write_text("#", encoding="utf-8")
    (ops / "run_production_drift_watch_silent.vbs").write_text("'v", encoding="utf-8")
    # 无 .venv——外部 python 存在也不放行（与 M14-06 R2.1 同款）
    fake = FakeSchtasks(repo_root=repo, task_state="missing")
    lines, log = _log_spy()
    code = pdwt.cmd_plan(runner=_gated(fake), repo_root=repo, log=log)
    assert code == pdwt.EXIT_ERROR
    assert any("venv" in line and "缺失" in line for line in lines)


@pytest.mark.parametrize("state", ["installed", "foreign", "malformed", "unknown"])
def test_plan_existing_or_incomplete_refuses(fake_repo: Path, state: str) -> None:
    xml_text = pdwt.build_task_xml(fake_repo)
    if state == "foreign":
        xml_text = xml_text.replace(pdwt.TASK_URI, "urn:someone:else")
    elif state == "malformed":
        xml_text = xml_text.replace("PT15M", "PT5M")
    if state == "unknown":  # 列表查询本身失败（事实不完整）
        fake = FakeSchtasks(repo_root=fake_repo, list_rc=1)
    else:
        fake = FakeSchtasks(repo_root=fake_repo, task_state=state, xml_text=xml_text)
    code = pdwt.cmd_plan(runner=_gated(fake), repo_root=fake_repo, log=_log_spy()[1])
    assert code == pdwt.EXIT_ERROR
    assert not any("/Create" in " ".join(argv) for argv in fake.calls)


def test_generate_writes_xml_no_scheduler_calls(fake_repo: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "export"
    _lines, log = _log_spy()
    code = pdwt.cmd_generate(repo_root=fake_repo, log=log, out_dir=out_dir)
    assert code == pdwt.EXIT_OK
    out_path = out_dir / pdwt.GENERATE_OUTPUT_NAME
    assert out_path.is_file()
    # 字节级读回（UTF-16 with BOM）后与 build_task_xml 全文一致
    raw = out_path.read_bytes()
    assert raw == pdwt.build_task_xml(fake_repo).encode("utf-16")
    assert not any(name.endswith(".tmp") for name in os.listdir(out_dir))
    assert pdwt.verify_task_xml(pdwt.decode_schtasks_xml(raw),
                                fake_repo)[0] == pdwt.STATE_INSTALLED


def test_generate_artifact_utf16_bom_externally_parseable(fake_repo: Path,
                                                          tmp_path: Path) -> None:
    """产物必须与其 XML 声明一致——UTF-16 **带 BOM** 字节（M14-14 R2 实证
    缺陷形态：UTF-8 字节 + UTF-16 声明，System.Xml XmlDocument.Load 拒载
    "no Unicode byte order mark"）。断言逐字节形态，非纯文本 round-trip。"""
    out_dir = tmp_path / "export"
    code = pdwt.cmd_generate(repo_root=fake_repo, log=_log_spy()[1], out_dir=out_dir)
    assert code == pdwt.EXIT_OK
    raw = (out_dir / pdwt.GENERATE_OUTPUT_NAME).read_bytes()
    # UTF-16 LE BOM 起始（外部解析器据 BOM 判 UTF-16；UTF-8 字节则以
    # 3C 3F 78 6D 6C "<?xml" 起始——旧缺陷形态必须在此失败）
    assert raw.startswith(b"\xff\xfe"), "generate 产物必须以 UTF-16 BOM 起始"
    assert not raw.startswith(b"<?xml")
    # 声明与字节一致：BOM 后按 UTF-16LE 解出 prolog 声明 UTF-16
    decoded = raw.decode("utf-16")  # BOM 感知解码（外部解析器同款语义）
    assert decoded.startswith('<?xml version="1.0" encoding="UTF-16"?>')
    # 本工具自身的字节形态严格解码器同样接受该工件，且归属校验通过
    # （decode_schtasks_xml 内部 strip——比较去尾空白后的全文）
    assert pdwt.decode_schtasks_xml(raw) == decoded.strip()
    assert pdwt.verify_task_xml(decoded, fake_repo) == (pdwt.STATE_INSTALLED, [])
    # 与 install 临时文件字节完全一致（同一 encode("utf-16") 形态）
    assert raw == pdwt.build_task_xml(fake_repo).encode("utf-16")


def test_generate_symlink_output_refused(fake_repo: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "export"
    out_dir.mkdir()
    target = tmp_path / "target.xml"
    target.write_text("sentinel", encoding="utf-8")
    try:
        os.symlink(target, out_dir / pdwt.GENERATE_OUTPUT_NAME)
    except OSError:
        pytest.skip("symlink unavailable on this host")
    code = pdwt.cmd_generate(repo_root=fake_repo, log=_log_spy()[1], out_dir=out_dir)
    assert code == pdwt.EXIT_REFUSED
    assert target.read_text(encoding="utf-8") == "sentinel"  # symlink 目标零写入


def test_status_five_states_and_exit_codes(fake_repo: Path) -> None:
    xml_text = pdwt.build_task_xml(fake_repo)
    cases = {
        "installed": (xml_text, pdwt.EXIT_OK),
        "missing": ("", pdwt.EXIT_MISSING),
        "foreign": (xml_text.replace(pdwt.TASK_URI, "urn:someone:else"), pdwt.EXIT_FOREIGN),
        "malformed": (xml_text.replace(pdwt.EXECUTION_TIME_LIMIT, "PT2H"), pdwt.EXIT_MALFORMED),
    }
    for state, (xml, expected) in cases.items():
        fake = FakeSchtasks(repo_root=fake_repo, task_state=state, xml_text=xml)
        code = pdwt.cmd_status(runner=_gated(fake), repo_root=fake_repo, log=_log_spy()[1])
        assert code == expected, state
        assert not any("/Delete" in " ".join(argv) for argv in fake.calls)
    # unknown：列表查询失败（事实不完整）
    fake = FakeSchtasks(repo_root=fake_repo, list_rc=1)
    code = pdwt.cmd_status(runner=_gated(fake), repo_root=fake_repo, log=_log_spy()[1])
    assert code == pdwt.EXIT_ERROR


def test_status_unknown_on_undecodable_xml(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo, task_state="installed", xml_text="")
    fake.run_raw = lambda argv, timeout=30.0: pdwt.RawCommandResult(
        tuple(str(x) for x in argv), 0, b"\xff\xfe\x33")  # 奇数 UTF-16 截断
    code = pdwt.cmd_status(runner=_gated(fake), repo_root=fake_repo, log=_log_spy()[1])
    assert code == pdwt.EXIT_ERROR
    fake2 = FakeSchtasks(repo_root=fake_repo, task_state="installed", xml_text="")
    fake2.run_raw = lambda argv, timeout=30.0: pdwt.RawCommandResult(
        tuple(str(x) for x in argv), 0, "OEM 错误文案".encode("gbk"))
    code = pdwt.cmd_status(runner=_gated(fake2), repo_root=fake_repo, log=_log_spy()[1])
    assert code == pdwt.EXIT_ERROR


# ---------------------------------------------------------------- install / uninstall


@pytest.mark.parametrize("argv", [["install"], ["uninstall"],
                                  ["install", "--confirm",
                                   "EXECUTE PRODUCTION DRIFT WATCH SCHEDULER CHANGES"],
                                  ["uninstall", "--confirm",
                                   " execute production drift watch scheduler change"],
                                  # drift watch 自身 execute 短语≠任务管理器短语（绝不互通）
                                  ["install", "--confirm",
                                   "EXECUTE READ-ONLY PRODUCTION DRIFT WATCH"],
                                  # M14-14 监控调度短语同样不互通
                                  ["uninstall", "--confirm",
                                   "EXECUTE MONITORING SCHEDULER CHANGE"]])
def test_mutation_requires_exact_phrase_zero_calls(argv) -> None:
    fake = FakeSchtasks(repo_root=REPO_ROOT)
    code = pdwt.main(argv, runner=_gated(fake, allow_mutation=True))
    assert code == pdwt.EXIT_ERROR
    assert fake.calls == []  # 短语门禁先于一切 schtasks 调用


def test_install_happy_path_fake(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo, task_state="missing")
    lines, log = _log_spy()
    code = pdwt.cmd_install(runner=_gated(fake, allow_mutation=True),
                            repo_root=fake_repo, log=log)
    assert code == pdwt.EXIT_OK
    creates = [argv for argv in fake.calls if "/Create" in argv]
    assert len(creates) == 1
    assert pdwt.is_create_command(creates[0])
    assert "/F" not in creates[0]  # create 绝不 force
    # 临时 XML：UTF-16 编码 + 内容精确等于 build_task_xml + 用后即删
    assert fake.created_xml_bytes is not None
    assert fake.created_xml_bytes.decode("utf-16") == pdwt.build_task_xml(fake_repo)
    tmp_arg = creates[0][5]
    assert not Path(tmp_arg).exists()
    assert not any("/Delete" in " ".join(argv) for argv in fake.calls)
    assert any("install 成功" in line for line in lines)


def test_install_refuses_existing_task(fake_repo: Path) -> None:
    for state in ("installed", "foreign", "malformed"):
        xml_text = pdwt.build_task_xml(fake_repo)
        if state == "foreign":
            xml_text = xml_text.replace(pdwt.TASK_URI, "urn:someone:else")
        elif state == "malformed":
            xml_text = xml_text.replace("PT15M", "PT5M")
        fake = FakeSchtasks(repo_root=fake_repo, task_state=state, xml_text=xml_text)
        code = pdwt.cmd_install(runner=_gated(fake, allow_mutation=True),
                                repo_root=fake_repo, log=_log_spy()[1])
        assert code == pdwt.EXIT_ERROR, state
        assert not any("/Create" in " ".join(argv) for argv in fake.calls)


def test_install_second_existence_check_must_confirm_missing(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo, task_state="missing")
    original_list = fake.run

    def run(argv, *, timeout: float = 60.0):
        tokens = tuple(str(x) for x in argv)
        # 第二次列表查询（_confirm_missing）翻转出现——绝不覆盖
        if tokens[1:5] == ("/Query", "/FO", "CSV", "/NH"):
            prior_lists = [c for c in fake.calls if c[1:5] == ("/Query", "/FO", "CSV", "/NH")]
            if len(prior_lists) >= 1:
                fake._task_state = "installed"
                fake._xml_text = pdwt.build_task_xml(fake_repo)
        return original_list(argv, timeout=timeout)

    fake.run = run  # type: ignore[method-assign]
    code = pdwt.cmd_install(runner=_gated(fake, allow_mutation=True),
                            repo_root=fake_repo, log=_log_spy()[1])
    assert code == pdwt.EXIT_ERROR
    assert not any("/Create" in " ".join(argv) for argv in fake.calls)


def test_uninstall_missing_idempotent(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo, task_state="missing")
    code = pdwt.cmd_uninstall(runner=_gated(fake, allow_mutation=True),
                              repo_root=fake_repo, log=_log_spy()[1])
    assert code == pdwt.EXIT_OK
    assert not any("/Delete" in " ".join(argv) for argv in fake.calls)


def test_uninstall_exact_owned_deletes(fake_repo: Path) -> None:
    xml_text = normalize_task_xml(pdwt.build_task_xml(fake_repo))
    fake = FakeSchtasks(repo_root=fake_repo, task_state="installed", xml_text=xml_text)
    code = pdwt.cmd_uninstall(runner=_gated(fake, allow_mutation=True),
                              repo_root=fake_repo, log=_log_spy()[1])
    assert code == pdwt.EXIT_OK
    deletes = [argv for argv in fake.calls if "/Delete" in argv]
    assert deletes == [("schtasks.exe", "/Delete", "/TN", pdwt.TASK_NAME, "/F")]
    assert not any("/Create" in " ".join(argv) for argv in fake.calls)


@pytest.mark.parametrize("state,expected", [
    ("foreign", pdwt.EXIT_FOREIGN),
    ("malformed", pdwt.EXIT_MALFORMED),
    ("unknown", pdwt.EXIT_ERROR),
])
def test_uninstall_non_owned_zero_delete(fake_repo: Path, state: str, expected: int) -> None:
    xml_text = pdwt.build_task_xml(fake_repo)
    if state == "foreign":
        xml_text = xml_text.replace(pdwt.TASK_URI, "urn:someone:else")
    elif state == "malformed":
        xml_text = xml_text.replace("PT15M", "PT5M")
    fake = FakeSchtasks(repo_root=fake_repo, task_state=state, xml_text=xml_text)
    if state == "unknown":
        fake = FakeSchtasks(repo_root=fake_repo, list_rc=1)
    code = pdwt.cmd_uninstall(runner=_gated(fake, allow_mutation=True),
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
    assert "production_drift_watch.py" in text
    # 携带 drift watch 门禁旗标与精确短语（与工具常量交叉 pin；短语是公开常量非 secret）
    assert "--execute" in text
    assert f'"{pdw.CONFIRM_PHRASE}"' in text
    # 预检失败专用退出码（2=venv 缺失/3=脚本缺失/4=repo 缺失）
    assert "WScript.Quit 2" in text and "WScript.Quit 3" in text
    assert "WScript.Quit 4" in text
    for pattern in SECRET_PATTERNS:
        assert pattern not in text
    for token in ("http://", "https://", "mshta", "powershell", "cmd.exe",
                  "CreateTextFile"):
        assert token not in text, token


def test_no_identity_collision_with_sibling_tasks() -> None:
    """与 M14-14 monitoring pipeline、M14-06 startup（production recovery）、
    M14-77 voice sidecar watchdog 三任务零身份冲突（同一机器并存）。"""
    siblings = (mpt, wst, vswt)
    for sibling in siblings:
        assert pdwt.TASK_NAME != sibling.TASK_NAME
        assert pdwt.TASK_URI != sibling.TASK_URI
        assert pdwt.NORMALIZED_TASK_URI != sibling.NORMALIZED_TASK_URI
        assert pdwt.TASK_DESCRIPTION != sibling.TASK_DESCRIPTION
    assert VBS.name not in (MONITORING_VBS.name, STARTUP_VBS.name, VOICE_VBS.name)
    # 本工具 XML 只指向自己的 wrapper/URI，绝不含兄弟任务标识
    xml_text = pdwt.build_task_xml(REPO_ROOT)
    assert "run_production_drift_watch_silent.vbs" in xml_text
    assert pdwt.TASK_URI in xml_text
    for foreign_marker in ("run_monitoring_pipeline_silent", "run_production_recovery_silent",
                           "run_voice_sidecar_watchdog_silent", "monitoring_pipeline_task",
                           "windows_startup_task", "voice_sidecar_watchdog_task",
                           mpt.TASK_URI, wst.TASK_URI, vswt.TASK_URI):
        assert foreign_marker not in xml_text, foreign_marker
    # 任务管理器短语与 drift watch 自身 execute 短语互不相等（防混淆）
    assert pdwt.TASK_CONFIRM_PHRASE != pdw.CONFIRM_PHRASE
    assert pdwt.TASK_CONFIRM_PHRASE != mpt.TASK_CONFIRM_PHRASE


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
    for pattern in SECRET_PATTERNS:
        assert pattern not in source
    for token in ("environ", "getenv", "docker", "urlopen", "requests"):
        assert token not in source
    # 两个 subprocess 执行点（run / run_raw）均无 shell=
    assert source.count("subprocess.run(") == 2
    for match in re.finditer(r"subprocess\.run\(", source):
        assert "shell" not in source[match.start():match.start() + 260]
    assert "CREATE_NO_WINDOW" in source


def test_parser_defaults_and_choices() -> None:
    parser = pdwt.build_parser()
    args = parser.parse_args(["status"])
    assert args.command == "status" and args.confirm == ""
    with pytest.raises(SystemExit):
        parser.parse_args(["run"])  # 不存在 run 子命令（设计即拒绝）
