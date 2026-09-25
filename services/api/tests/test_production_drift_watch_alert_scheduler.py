r"""M14-141 tools/ops/production_drift_watch_alert_scheduler.py +
run_production_drift_watch_alert_silent.vbs 契约测试：production drift
watch 告警分发周期计划任务 readiness 管理器（零真实 schtasks 读写路径、
零注册、零 Docker/零网络/零生产读取、零 secret 值接触）。

覆盖（全部经 FakeSchtasks 注入 + 临时 fake repo；本开发回合 install/
uninstall 永不真实执行——短语门禁先于一切 schtasks 调用）：
- Task XML：固定身份（URI/Description 持久归属标记）+ 关键设置逐项断言
  （Hidden/IgnoreNew/**StartWhenAvailable=false（R1 supervisor 修正：固定
  过去 StartBoundary + true 会在注册后立即不可控补跑——错失槽位绝不
  补跑，下一个固定 PT15M 节点运行）**/电池双 false/ExecutionTimeLimit=
  PT10M < 重复间隔 PT15M/TimeTrigger+StartBoundary 2026-01-01T00:05:00/
  Repetition 无 Duration/InteractiveToken+LeastPrivilege/wscript //B
  //Nologo + 仓库内 wrapper 路径/WorkingDirectory=repo）；XML 恒不含
  secret 路径/secret 值；_esc 转义单元 + 特殊字符仓库路径 round-trip；
  StartWhenAvailable=false 恰为 Windows 默认值——注册后归一化省略形态
  仅在其余字段全部精确时条件认可（显式 true 恒 malformed），R1 专项
  测试锁定不补跑语义；
- 非重叠 + 预算交叉 pin（单一事实源模块加载，漂移即测试失败）：与
  M14-129 watcher 同为 PT15M 且 StartBoundary 恒差恰 300s（告警槽位
  :05/:20/:35:50，watcher 槽位 :00/:15/:30/:45，互不重叠）；间隔 900s >
  时限 600s > M14-137 任务桥子进程预算 CHILD_TIMEOUT_SECONDS 300s，
  时限留给收尾余量 ≥300s；
- verify_task_xml 四态：installed（round-trip 及 Task Scheduler 归一化
  形态——URI 重写/默认值元素省略按 M14-06/M14-131 实证先例条件认可）/
  foreign（URI 或 Command）/ malformed（字段缺失/漂移逐项报告字段名；
  XML 垃圾；DOCTYPE/ENTITY 拒绝——XXE 防护）；省略的默认值仅在其余
  字段全部精确时认可；
- decode_schtasks_xml 四形态（UTF-16LE 无 BOM/LE BOM/BE BOM/UTF-8 prolog）
  与拒绝面（OEM 乱码/奇数截断/非 XML）；
- 白名单门（结构性）：只读门恒放行两查询形态、拒绝一切 mutation（含
  /Run//Change//End 形态与带 /F 的 create）；mutation 门仅放行
  ``/Create /TN <固定名> /XML <单路径>`` 与 ``/Delete /TN <固定名> /F``
  两精确形态；**任何其它任务名——含 M14-129 watcher
  （AIOS-Production-Drift-Watch）、M14-06（AIOS-Production-Recovery）、
  M14-14（AIOS-Monitoring-Pipeline）——查询/建/删一律拒绝**（绝不复用、
  绝不改动既有 watcher 任务）；``/XML`` 后唯一 token 是值位置——POSIX
  绝对 tempfile 路径（``/`` 起头）是路径而非旗标（M14-14 PR #89 R3
  回归），旗标形态逐项拒绝；
- plan：fail-fast——预检缺项（无 .venv/无 secret 文件/wrapper 内容被篡改）
  → exit 1 且**零 schtasks 调用**；任务缺失+预检过 → exit 0（恰一次列表
  查询，零 mutation）；任务存在/事实不完整 → exit 1；
- generate：预检不过 → exit 2 零写入（输出目录不创建）；XML 原子导出
  （tmp+fsync+replace，无 .tmp 残留）到临时目录 + UTF-16 with BOM 字节
  （与 XML 声明及 install 临时字节逐字节一致，BOM 起始 + BOM 感知解码
  语义 + 本工具解码器/归属校验通过）；输出路径 symlink/祖先 symlink
  拒绝零写入；
- status 五态 + 退出码（installed 0/missing 2/foreign 3/malformed 4/
  unknown 1）；unknown on 不可解码 XML；
- install/uninstall 短语门禁：缺失/近似短语（含 M14-127 watcher、
  M14-129 scheduler、M14-135 dispatch、M14-137 task 四个既有短语——
  五短语绝不互通）→ exit 1 且零 schtasks 调用；install happy path
  （FakeSchtasks：临时 XML 内容精确等于 build_task_xml、UTF-16、用后
  即删、归一化回读复查通过、create 绝不带 /F）；install 拒绝同名
  （installed/二次列表复核不确认 missing）；uninstall 仅 exact-owned 才
  /Delete /F、missing 幂等 exit 0、foreign/malformed/unknown 零删除；
- VBS wrapper 契约：无盘符硬编码、隐藏窗口 Run(...,0,True)、
  WScript.Quit 透传、仓库根推导、固定 venv python + M14-137 任务桥调用
  目标、--secret-file + 固定 secret 相对路径、--execute 与 M14-137 精确
  确认短语（经模块常量交叉 pin）、预检退出码 2/3/4/5；零文件读写面
  （CreateTextFile/OpenTextFile 恒拒）、零网络/解释器面、零 secret 形态值；
- verify_wrapper_content：真实 wrapper 通过；参数化篡改（换短语/删 secret
  路径/删 --secret-file/删 --execute/盘符硬编码/OpenTextFile/https URL/
  secret 形态值）逐项拒绝且 plan 零调度器调用；
- secret 值永不披露：fake repo secret 文件内合成 sentinel 值绝不出现在
  plan 日志/生成 XML/wrapper；输出面只允许固定相对路径本身；源码契约
  （凡提及 secret 的行恒无读取面：read_text/read_bytes/json.load/open）；
- 与 AIOS-Production-Drift-Watch（M14-129）、AIOS-Monitoring-Pipeline
  （M14-14）、AIOS-Production-Recovery（M14-06）、AIOS-Voice-Sidecar-
  Watchdog（M14-77）无任务名/URI/描述/VBS 身份冲突；本工具 XML 只指向
  自己的 wrapper；
- 源码契约：无 '"/Run"'；'"/F"' 仅出现在 schtasks argv 定义行；'"/Create"'
  行绝不带 /F；写路径仅 /Create 与 /Delete 两字面量族；零 secret 形态、
  零 env 读取、零 docker/网络面；subprocess 执行点无 shell=。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "production_drift_watch_alert_scheduler.py"
VBS = REPO_ROOT / "tools" / "ops" / "run_production_drift_watch_alert_silent.vbs"
WATCHER_TASK_SCRIPT = REPO_ROOT / "tools" / "ops" / "production_drift_watch_task.py"
WATCHER_VBS = REPO_ROOT / "tools" / "ops" / "run_production_drift_watch_silent.vbs"
WATCHER_SCRIPT = REPO_ROOT / "tools" / "ops" / "production_drift_watch.py"
ALERT_TASK_SCRIPT = REPO_ROOT / "tools" / "ops" / "production_drift_watch_alert_task.py"
DISPATCH_SCRIPT = REPO_ROOT / "tools" / "ops" / "production_drift_watch_alert_dispatch.py"
MONITORING_TASK_SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_pipeline_task.py"
MONITORING_VBS = REPO_ROOT / "tools" / "ops" / "run_monitoring_pipeline_silent.vbs"
STARTUP_TASK_SCRIPT = REPO_ROOT / "tools" / "ops" / "windows_startup_task.py"
STARTUP_VBS = REPO_ROOT / "tools" / "ops" / "run_production_recovery_silent.vbs"
VOICE_TASK_SCRIPT = REPO_ROOT / "tools" / "voice" / "voice_sidecar_watchdog_task.py"
VOICE_VBS = REPO_ROOT / "tools" / "voice" / "run_voice_sidecar_watchdog_silent.vbs"
SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb_", "-----BEGIN")
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

#: 合成 secret sentinel（仅存在于 fake repo 临时文件与测试内存——断言其
#: 绝不进入任何输出面；非真实 secret）
SECRET_SENTINEL_TOKEN = "sk-ZXm141sentinel012345"


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


sched = _load_module(SCRIPT, "production_drift_watch_alert_scheduler_under_test")
pdwt = _load_module(WATCHER_TASK_SCRIPT, "production_drift_watch_task_for_alert_sched_pin")
pdw = _load_module(WATCHER_SCRIPT, "production_drift_watch_for_alert_sched_pin")
alert_task_mod = _load_module(ALERT_TASK_SCRIPT,
                              "production_drift_watch_alert_task_for_sched_pin")
dispatch_mod = _load_module(DISPATCH_SCRIPT,
                            "production_drift_watch_alert_dispatch_for_sched_pin")
mpt = _load_module(MONITORING_TASK_SCRIPT, "monitoring_pipeline_task_for_alert_sched_pin")
wst = _load_module(STARTUP_TASK_SCRIPT, "windows_startup_task_for_alert_sched_pin")
vswt = _load_module(VOICE_TASK_SCRIPT, "voice_sidecar_watchdog_task_for_alert_sched_pin")


def _log_spy() -> tuple[list[str], object]:
    lines: list[str] = []
    return lines, lines.append


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    ops = repo / "tools" / "ops"
    ops.mkdir(parents=True)
    (ops / "production_drift_watch_alert_task.py").write_text("# fake", encoding="utf-8")
    # wrapper 用真实仓库内容（verify_wrapper_content 校验真实契约面）
    (ops / "run_production_drift_watch_alert_silent.vbs").write_text(
        VBS.read_text(encoding="utf-8"), encoding="utf-8")
    venv_scripts = repo / ".venv" / "Scripts"
    venv_scripts.mkdir(parents=True)
    (venv_scripts / "python.exe").write_bytes(b"fake")
    infra = repo / "infra"
    infra.mkdir()
    (infra / sched.SECRET_FILE_NAME).write_text(
        json.dumps({"url": "https://hooks.example.invalid/hook",
                    "token": SECRET_SENTINEL_TOKEN}), encoding="utf-8")
    return repo


def normalize_task_xml(xml_text: str) -> str:
    """模拟 Task Scheduler 归一化存储（M14-06 生产 + COM 快照实证形态；
    M14-131 真实安装复核 TimeTrigger 同款）：URI 重写为 ``\\<TaskName>``；
    省略值恰为 Windows 默认值的元素（TimeTrigger/Enabled、Settings/Enabled、
    Principal/RunLevel）；其余逐字保留。"""
    text = xml_text.replace(sched.TASK_URI, sched.NORMALIZED_TASK_URI)
    text = text.replace(
        f"      <StartBoundary>{sched.START_BOUNDARY}</StartBoundary>\n"
        f"      <Enabled>true</Enabled>\n",
        f"      <StartBoundary>{sched.START_BOUNDARY}</StartBoundary>\n")
    text = text.replace(
        "    <Hidden>true</Hidden>\n    <Enabled>true</Enabled>\n  </Settings>",
        "    <Hidden>true</Hidden>\n  </Settings>")
    # R1：StartWhenAvailable=false 恰为 Windows 默认值——归一化同样省略
    text = text.replace("    <StartWhenAvailable>false</StartWhenAvailable>\n", "")
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
                return sched.CommandResult(tokens, self._list_rc, "", "err")
            stdout = ('"\\\\SomeOtherTask","Foo"\n' if not listed
                      else f'"{sched.TASK_NAME}","AIOS Production Drift Watch Alert"\n')
            return sched.CommandResult(tokens, 0, stdout, "")
        if tokens[1] == "/Create":
            self.created_xml_bytes = Path(tokens[5]).read_bytes()
            if self._flip and self._create_rc == 0:
                self._task_state = "installed"
                self._xml_text = normalize_task_xml(
                    sched.build_task_xml(self._repo_root))
            return sched.CommandResult(tokens, self._create_rc, "", "")
        if tokens[1] == "/Delete":
            if self._delete_rc == 0:
                self._task_state = "missing"
            return sched.CommandResult(tokens, self._delete_rc, "", "")
        raise AssertionError(f"unexpected argv: {tokens}")

    def run_raw(self, argv, *, timeout: float = 30.0):
        tokens = tuple(str(item) for item in argv)
        self.calls.append(tokens)
        assert tokens[1:5] == ("/Query", "/TN", sched.TASK_NAME, "/XML")
        if self._task_state == "missing":
            return sched.RawCommandResult(tokens, 1, b"")
        payload = self._xml_text.encode("utf-16-le")
        return sched.RawCommandResult(tokens, 0, payload)


def _gated(fake: FakeSchtasks, *, allow_mutation: bool = False) -> sched.GatedSchtasks:
    return sched.GatedSchtasks(fake, allow_mutation=allow_mutation)


def _minutes(spec: str) -> int:
    match = re.fullmatch(r"PT(\d+)M", spec)
    assert match is not None, spec
    return int(match.group(1)) * 60


def _boundary(spec: str) -> datetime:
    return datetime.strptime(spec, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- Task XML


def test_build_task_xml_key_fields(fake_repo: Path) -> None:
    xml_text = sched.build_task_xml(fake_repo)
    root = ET.fromstring(xml_text)

    def text_of(path: str) -> str:
        node = root.find(path, NS)
        assert node is not None and node.text, path
        return node.text.strip()

    assert text_of("t:RegistrationInfo/t:URI") == sched.TASK_URI
    assert text_of("t:RegistrationInfo/t:Description") == sched.TASK_DESCRIPTION
    assert text_of("t:Triggers/t:TimeTrigger/t:StartBoundary") == sched.START_BOUNDARY
    assert text_of("t:Triggers/t:TimeTrigger/t:Enabled") == "true"
    assert text_of("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval") == "PT15M"
    assert text_of("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
    assert text_of("t:Settings/t:DisallowStartIfOnBatteries") == "false"
    assert text_of("t:Settings/t:StopIfGoingOnBatteries") == "false"
    assert text_of("t:Settings/t:StartWhenAvailable") == "false"
    assert text_of("t:Settings/t:ExecutionTimeLimit") == sched.EXECUTION_TIME_LIMIT
    assert text_of("t:Settings/t:Hidden") == "true"
    assert text_of("t:Settings/t:Enabled") == "true"
    assert text_of("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert text_of("t:Principals/t:Principal/t:RunLevel") == "LeastPrivilege"
    assert text_of("t:Actions/t:Exec/t:Command") == sched.WSCRIPT
    expected_args = sched.WSCRIPT_ARGS_TEMPLATE.format(
        vbs=sched._repo_paths(fake_repo)["vbs"].resolve())
    assert text_of("t:Actions/t:Exec/t:Arguments") == expected_args
    assert Path(text_of("t:Actions/t:Exec/t:WorkingDirectory")).resolve() == fake_repo.resolve()
    # Repetition 无 Duration（= 无限期重复）
    assert "<Duration>" not in xml_text
    # XML 只指向 wrapper，恒不含 secret 路径/secret 形态值（secret 路径只
    # 存在于 wrapper 内部，经其固定相对路径引用）
    assert sched.SECRET_FILE_NAME not in xml_text
    assert "secret" not in xml_text.lower()
    for pattern in SECRET_PATTERNS:
        assert pattern not in xml_text, pattern


def test_nonoverlap_and_budget_cross_pins() -> None:
    """非重叠 + 预算交叉 pin（常量逐项取自 M14-129/M14-137 单一事实源）：
    ① 与 watcher 同为 PT15M 且 StartBoundary 恒差恰 300s——告警槽位
    :05/:20/:35:50 与 watcher 槽位 :00/:15/:30/:45 互不重叠；② 间隔
    900s > 时限 600s > M14-137 子进程预算 300s——调度器绝不先于内部超时
    杀整任务，恒留 ≥300s 收尾余量。任一常量漂移即本测试失败。"""
    assert (sched.REPETITION_INTERVAL, sched.EXECUTION_TIME_LIMIT,
            sched.START_BOUNDARY) == ("PT15M", "PT10M", "2026-01-01T00:05:00")
    assert pdwt.REPETITION_INTERVAL == "PT15M"
    assert pdwt.START_BOUNDARY == "2026-01-01T00:00:00"
    offset = (_boundary(sched.START_BOUNDARY)
              - _boundary(pdwt.START_BOUNDARY)).total_seconds()
    interval = _minutes(sched.REPETITION_INTERVAL)
    limit = _minutes(sched.EXECUTION_TIME_LIMIT)
    assert interval == 900 and limit == 600
    assert 0 < offset < interval and offset == 300  # 恒差恰 5 分钟
    budget = sched.ALERT_CHILD_TIMEOUT_SECONDS
    assert budget == alert_task_mod.CHILD_TIMEOUT_SECONDS == 300.0  # 单一事实源
    assert interval > limit            # 调度器侧不自我重叠（另有 IgnoreNew 兜底）
    assert limit > budget              # 内部子进程预算先于调度器时限
    assert limit - budget >= 300       # 恒留 ≥300s 收尾余量


def test_esc_unit() -> None:
    assert sched._esc('a<b>&"c"') == "a&lt;b&gt;&amp;&quot;c&quot;"


def test_start_when_available_false_no_catchup_r1(fake_repo: Path) -> None:
    """R1 supervisor 修正专项：固定过去 StartBoundary + StartWhenAvailable=
    true 会在注册后立即产生**不可控补跑**——告警任务恒 StartWhenAvailable=
    false：错失槽位绝不补跑，下一个固定 PT15M 节点运行。false 恰为 Windows
    默认值，注册后归一化省略形态按 defaultable 机制条件认可（其余字段全部
    精确才认可；显式 true 恒 malformed）。"""
    xml_text = sched.build_task_xml(fake_repo)
    # ① 写入面：恒 false + 固定过去边界（字面 pin——配合 R1 语义）
    assert "<StartWhenAvailable>false</StartWhenAvailable>" in xml_text
    assert "<StartWhenAvailable>true</StartWhenAvailable>" not in xml_text
    assert sched.START_BOUNDARY == "2026-01-01T00:05:00"
    assert _boundary(sched.START_BOUNDARY) < _boundary("2026-12-31T00:00:00")
    # ② 注册后归一化（默认值元素省略，含 SwA）→ 仍 exact-owned
    normalized = normalize_task_xml(xml_text)
    assert "<StartWhenAvailable>" not in normalized
    state, issues = sched.verify_task_xml(normalized, fake_repo)
    assert state == sched.STATE_INSTALLED and issues == []
    # ③ 显式 true（补跑语义回归）→ 恒 malformed，逐项报字段名
    catchup = xml_text.replace("<StartWhenAvailable>false",
                               "<StartWhenAvailable>true")
    state, issues = sched.verify_task_xml(catchup, fake_repo)
    assert state == sched.STATE_MALFORMED and "Settings/StartWhenAvailable" in issues


def test_xml_escapes_special_repo_path_roundtrip(tmp_path: Path) -> None:
    repo = tmp_path / "re&po"
    xml_text = sched.build_task_xml(repo)
    state, issues = sched.verify_task_xml(xml_text, repo)
    assert state == sched.STATE_INSTALLED and issues == []
    root = ET.fromstring(xml_text)
    working_dir = root.find("t:Actions/t:Exec/t:WorkingDirectory", NS)
    assert working_dir is not None and working_dir.text is not None
    assert Path(working_dir.text.strip()).resolve() == repo.resolve()


# ---------------------------------------------------------------- verify 四态


def test_verify_installed_roundtrip(fake_repo: Path) -> None:
    state, issues = sched.verify_task_xml(sched.build_task_xml(fake_repo), fake_repo)
    assert state == sched.STATE_INSTALLED and issues == []


def test_verify_accepts_scheduler_normalization(fake_repo: Path) -> None:
    xml_text = normalize_task_xml(sched.build_task_xml(fake_repo))
    state, issues = sched.verify_task_xml(xml_text, fake_repo)
    assert state == sched.STATE_INSTALLED and issues == []


def test_verify_normalization_not_accepted_when_other_field_drifts(
        fake_repo: Path) -> None:
    drifted = normalize_task_xml(sched.build_task_xml(fake_repo)).replace(
        "<Interval>PT15M</Interval>", "<Interval>PT5M</Interval>")
    state, issues = sched.verify_task_xml(drifted, fake_repo)
    assert state == sched.STATE_MALFORMED
    assert "Triggers/TimeTrigger/Repetition/Interval" in issues
    # 省略的默认值不再获认可（其余字段已漂移）——含 R1 起恒 false 的
    # StartWhenAvailable（其省略不再按默认认可）
    assert "Triggers/TimeTrigger/Enabled" in issues
    assert "Settings/StartWhenAvailable" in issues


def test_verify_explicit_nondefault_enabled_rejected(fake_repo: Path) -> None:
    xml_text = sched.build_task_xml(fake_repo).replace(
        "    <Hidden>true</Hidden>\n    <Enabled>true</Enabled>",
        "    <Hidden>true</Hidden>\n    <Enabled>false</Enabled>")
    state, issues = sched.verify_task_xml(xml_text, fake_repo)
    assert state == sched.STATE_MALFORMED and "Settings/Enabled" in issues


@pytest.mark.parametrize("mutation,field", [
    (lambda x: x.replace(sched.TASK_DESCRIPTION, "other"), "RegistrationInfo/Description"),
    (lambda x: x.replace("<Hidden>true</Hidden>", "<Hidden>false</Hidden>"), "Settings/Hidden"),
    (lambda x: x.replace("PT15M", "PT5M"), "Triggers/TimeTrigger/Repetition/Interval"),
    (lambda x: x.replace(sched.START_BOUNDARY, "2026-01-01T00:00:00"),
     "Triggers/TimeTrigger/StartBoundary"),
    (lambda x: x.replace(sched.EXECUTION_TIME_LIMIT, "PT2H"), "Settings/ExecutionTimeLimit"),
    (lambda x: x.replace("<MultipleInstancesPolicy>IgnoreNew",
                         "<MultipleInstancesPolicy>Parallel"),
     "Settings/MultipleInstancesPolicy"),
    (lambda x: x.replace("<LogonType>InteractiveToken", "<LogonType>S4U"),
     "Principals/Principal/LogonType"),
    (lambda x: x.replace("<StartWhenAvailable>false", "<StartWhenAvailable>true"),
     "Settings/StartWhenAvailable"),
    (lambda x: x.replace("<RunLevel>LeastPrivilege", "<RunLevel>HighestAvailable"),
     "Principals/Principal/RunLevel"),
    (lambda x: x.replace("<DisallowStartIfOnBatteries>false",
                         "<DisallowStartIfOnBatteries>true"),
     "Settings/DisallowStartIfOnBatteries"),
])
def test_verify_malformed_field_drift_reports_name(fake_repo: Path, mutation, field) -> None:
    xml_text = mutation(sched.build_task_xml(fake_repo))
    state, issues = sched.verify_task_xml(xml_text, fake_repo)
    assert state == sched.STATE_MALFORMED, field
    assert field in issues


def test_verify_malformed_missing_elements(fake_repo: Path) -> None:
    xml_text = sched.build_task_xml(fake_repo).replace(
        "<WorkingDirectory>" + sched._esc(fake_repo.resolve()) + "</WorkingDirectory>", "")
    state, issues = sched.verify_task_xml(xml_text, fake_repo)
    assert state == sched.STATE_MALFORMED and "Actions/Exec/WorkingDirectory" in issues
    no_trigger = sched.build_task_xml(fake_repo).replace("<TimeTrigger>", "<CalendarTrigger>")
    state, _ = sched.verify_task_xml(no_trigger, fake_repo)
    assert state == sched.STATE_MALFORMED


def test_verify_malformed_garbage(fake_repo: Path) -> None:
    state, _issues = sched.verify_task_xml("not xml at all", fake_repo)
    assert state == sched.STATE_MALFORMED


def test_verify_unattributable_task_is_foreign(fake_repo: Path) -> None:
    """可解析但无归属标识（URI 缺失）——不可归属 = 非本工具（foreign）。"""
    state, _issues = sched.verify_task_xml("<Task></Task>", fake_repo)
    assert state == sched.STATE_FOREIGN


def test_verify_rejects_doctype_entity(fake_repo: Path) -> None:
    evil = ("<?xml version='1.0'?><!DOCTYPE Task [<!ENTITY xxe SYSTEM 'file:///c:/win.ini'>]>"
            "<Task>&xe;</Task>")
    state, issues = sched.verify_task_xml(evil, fake_repo)
    assert state == sched.STATE_MALFORMED
    assert any("DOCTYPE" in issue or "ENTITY" in issue for issue in issues)


def test_verify_foreign_uri_or_command(fake_repo: Path) -> None:
    xml_text = sched.build_task_xml(fake_repo)
    foreign_uri = xml_text.replace(sched.TASK_URI, "urn:someone:else:task")
    assert sched.verify_task_xml(foreign_uri, fake_repo)[0] == sched.STATE_FOREIGN
    foreign_cmd = xml_text.replace(f"<Command>{sched.WSCRIPT}</Command>",
                                   "<Command>powershell.exe</Command>")
    assert sched.verify_task_xml(foreign_cmd, fake_repo)[0] == sched.STATE_FOREIGN


# ---------------------------------------------------------------- 解码


def test_decode_four_byte_forms() -> None:
    text = "<?xml version=\"1.0\"?><Task/>"
    assert sched.decode_schtasks_xml(text.encode("utf-16-le")) == text
    assert sched.decode_schtasks_xml(b"\xff\xfe" + text.encode("utf-16-le")) == text
    assert sched.decode_schtasks_xml(b"\xfe\xff" + text.encode("utf-16-be")) == text
    assert sched.decode_schtasks_xml(text.encode("utf-8")) == text


@pytest.mark.parametrize("bad", [b"", b"garbage", b"\xff\xfe\x00", b"3c\x00\x00?x"])
def test_decode_rejects_unknown_forms(bad: bytes) -> None:
    with pytest.raises(ValueError):
        sched.decode_schtasks_xml(bad)


# ---------------------------------------------------------------- 白名单门（结构性）


class _RecordingInner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout: float = 60.0):
        self.calls.append(tuple(str(x) for x in argv))
        return sched.CommandResult(tuple(str(x) for x in argv), 0, "", "")

    def run_raw(self, argv, *, timeout: float = 30.0):
        self.calls.append(tuple(str(x) for x in argv))
        return sched.RawCommandResult(tuple(str(x) for x in argv), 0, b"<?xml")


@pytest.mark.parametrize("argv", [
    ["schtasks.exe", "/Query", "/FO", "CSV", "/NH"],
    ["schtasks.exe", "/Query", "/TN", "AIOS-Production-Drift-Watch-Alert", "/XML"],
])
def test_gate_readonly_forms_pass_without_mutation_flag(argv) -> None:
    inner = _RecordingInner()
    gate = sched.GatedSchtasks(inner, allow_mutation=False)
    gate.run(argv)
    assert inner.calls == [tuple(argv)]


@pytest.mark.parametrize("argv", [
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch-Alert", "/XML", "C:/tmp/x.xml"],
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch-Alert", "/XML",
     "/tmp/aios-drift-watch-alert-test.xml"],               # POSIX 路径也不得绕过读门
    ["schtasks.exe", "/Delete", "/TN", "AIOS-Production-Drift-Watch-Alert", "/F"],
    ["schtasks.exe", "/Run", "/TN", "AIOS-Production-Drift-Watch-Alert"],
    ["schtasks.exe", "/Change", "/TN", "AIOS-Production-Drift-Watch-Alert", "/TR", "x"],
    ["schtasks.exe", "/End", "/TN", "AIOS-Production-Drift-Watch-Alert"],
    ["schtasks.exe", "/Query", "/FO", "CSV"],                # 缺 /NH
    # 既有 watcher 任务（M14-129）与其它兄弟任务名——查询/建/删一律拒绝
    #（绝不复用、绝不改动既有 watcher 任务）
    ["schtasks.exe", "/Query", "/TN", "AIOS-Production-Drift-Watch", "/XML"],
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML", "C:/tmp/x.xml"],
    ["schtasks.exe", "/Delete", "/TN", "AIOS-Production-Drift-Watch", "/F"],
    ["schtasks.exe", "/Query", "/TN", "AIOS-Production-Recovery", "/XML"],
    ["schtasks.exe", "/Query", "/TN", "AIOS-Monitoring-Pipeline", "/XML"],
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch-Alert", "/XML", "a", "b"],
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch-Alert", "/XML", "-F"],
    ["schtasks.exe", "/Delete", "/TN", "AIOS-Production-Drift-Watch-Alert"],  # 缺 /F
    ["cmd.exe", "/c", "schtasks.exe", "/Query", "/FO", "CSV", "/NH"],
])
def test_gate_rejects_when_mutation_not_allowed(argv) -> None:
    inner = _RecordingInner()
    gate = sched.GatedSchtasks(inner, allow_mutation=False)
    with pytest.raises(sched.RunnerError):
        gate.run(argv)
    assert inner.calls == []


@pytest.mark.parametrize("argv", [
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch-Alert", "/XML", "C:/tmp/x.xml"],
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch-Alert", "/XML",
     "/tmp/aios-drift-watch-alert-test.xml"],
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch-Alert", "/XML",
     "/tmp/pytest-of-runner/popen-gw0/test_install0/aios-drift-watch-alert-ab12cd.xml"],
    ["schtasks.exe", "/Delete", "/TN", "AIOS-Production-Drift-Watch-Alert", "/F"],
])
def test_gate_allows_exact_mutation_forms_when_enabled(argv) -> None:
    inner = _RecordingInner()
    gate = sched.GatedSchtasks(inner, allow_mutation=True)
    gate.run(argv)
    assert inner.calls == [tuple(argv)]


def test_create_allowlist_posix_tempfile_path_is_value_not_switch() -> None:
    """M14-14 PR #89 CI R3 回归：argv 形态恒定（精确前缀 + 长度 6），
    ``/XML`` 之后的**唯一 token 是值位置**——POSIX 绝对 tempfile 路径以
    ``/`` 起头是路径前缀而非旗标；旗标防护靠结构（.xml 后缀 + 非 ``-``
    起头 + 无多余 token），不靠 slash 首字符。"""
    posix = "/tmp/aios-drift-watch-alert-test.xml"
    ci_shaped = "/tmp/pytest-of-runner/popen-gw0/test_install0/aios-drift-watch-alert-ab12cd.xml"
    windows = "C:\\Users\\runner\\AppData\\Local\\Temp\\aios-drift-watch-alert-ab12cd.xml"
    accepted = [
        ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch-Alert", "/XML", posix],
        ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch-Alert", "/XML", ci_shaped],
        ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch-Alert", "/XML", windows],
    ]
    for argv in accepted:
        assert sched.is_create_command(argv) is True, argv
        inner = _RecordingInner()
        sched.GatedSchtasks(inner, allow_mutation=True).run(argv)
        assert inner.calls == [tuple(argv)]
    rejected_values = [
        "-F",                       # dash 旗标形态
        "/F",                       # 裸 slash 旗标（非 .xml）
        "x.xml/F",                  # 尾随旗标
        "/tmp/x.xml/F",             # POSIX 路径 + 尾随旗标
        "/tmp/aios-drift-watch-alert-notxml.txt",  # 非 .xml 路径
        "-.xml",                    # dash 起头伪路径
        "",
    ]
    for value in rejected_values:
        argv = ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch-Alert",
                "/XML", value]
        assert sched.is_create_command(argv) is False, value
        inner = _RecordingInner()
        with pytest.raises(sched.RunnerError):
            sched.GatedSchtasks(inner, allow_mutation=True).run(argv)
        assert inner.calls == []
    # 值位置的放行不外溢到其它任何位置：路径 token 放错位置（/XML 前）仍拒
    misplaced = ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch-Alert",
                 posix, "/XML"]
    assert sched.is_create_command(misplaced) is False
    # 错任务名（含 watcher 任务名）+ 任意路径值仍拒；多余 token 仍拒
    assert sched.is_create_command(
        ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML", posix]) is False
    assert sched.is_create_command(
        ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch-Alert",
         "/XML", posix, "/F"]) is False


@pytest.mark.parametrize("argv", [
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch-Alert", "/XML", "C:/tmp/x.xml", "/F"],
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch", "/XML", "C:/tmp/x.xml"],
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch-Alert", "/XML", "-F"],
    ["schtasks.exe", "/Create", "/TN", "AIOS-Production-Drift-Watch-Alert", "/XML", "x.xml/F"],
    ["schtasks.exe", "/Delete", "/TN", "AIOS-Production-Drift-Watch-Alert", "/F", "/F"],
])
def test_gate_rejects_mutated_mutation_forms_even_when_enabled(argv) -> None:
    inner = _RecordingInner()
    gate = sched.GatedSchtasks(inner, allow_mutation=True)
    with pytest.raises(sched.RunnerError):
        gate.run(argv)
    assert inner.calls == []


# ---------------------------------------------------------------- plan（fail-fast 零调度器调用）


def test_plan_missing_and_preflight_ok_exit0(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo, task_state="missing")
    lines, log = _log_spy()
    code = sched.cmd_plan(runner=_gated(fake), repo_root=fake_repo, log=log)
    assert code == sched.EXIT_OK
    assert fake.calls == [("schtasks.exe", "/Query", "/FO", "CSV", "/NH")]
    assert any("supervisor-only" in line for line in lines)
    assert not any("/Create" in " ".join(argv) for argv in fake.calls)


def test_plan_preflight_missing_venv_fail_closed_zero_calls(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    ops = repo / "tools" / "ops"
    ops.mkdir(parents=True)
    (ops / "production_drift_watch_alert_task.py").write_text("#", encoding="utf-8")
    (ops / "run_production_drift_watch_alert_silent.vbs").write_text(
        VBS.read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "infra").mkdir()
    (repo / "infra" / sched.SECRET_FILE_NAME).write_text("{}", encoding="utf-8")
    # 无 .venv——外部 python 存在也不放行（与 M14-06 R2.1/M14-129 同款）
    fake = FakeSchtasks(repo_root=repo, task_state="missing")
    lines, log = _log_spy()
    code = sched.cmd_plan(runner=_gated(fake), repo_root=repo, log=log)
    assert code == sched.EXIT_ERROR
    assert any("venv" in line and "缺失" in line for line in lines)
    assert fake.calls == []  # fail-fast：预检不过零调度器调用


def test_plan_preflight_missing_secret_fail_closed_zero_calls(fake_repo: Path) -> None:
    (fake_repo / "infra" / sched.SECRET_FILE_NAME).unlink()
    fake = FakeSchtasks(repo_root=fake_repo, task_state="missing")
    lines, log = _log_spy()
    code = sched.cmd_plan(runner=_gated(fake), repo_root=fake_repo, log=log)
    assert code == sched.EXIT_ERROR
    assert any("secret" in line and "缺失" in line for line in lines)
    assert fake.calls == []  # fail-fast：预检不过零调度器调用


@pytest.mark.parametrize("state", ["installed", "foreign", "malformed", "unknown"])
def test_plan_existing_or_incomplete_refuses(fake_repo: Path, state: str) -> None:
    xml_text = sched.build_task_xml(fake_repo)
    if state == "foreign":
        xml_text = xml_text.replace(sched.TASK_URI, "urn:someone:else")
    elif state == "malformed":
        xml_text = xml_text.replace("PT15M", "PT5M")
    if state == "unknown":  # 列表查询本身失败（事实不完整）
        fake = FakeSchtasks(repo_root=fake_repo, list_rc=1)
    else:
        fake = FakeSchtasks(repo_root=fake_repo, task_state=state, xml_text=xml_text)
    code = sched.cmd_plan(runner=_gated(fake), repo_root=fake_repo, log=_log_spy()[1])
    assert code == sched.EXIT_ERROR
    assert not any("/Create" in " ".join(argv) for argv in fake.calls)


# ---------------------------------------------------------------- generate


def test_generate_writes_xml_no_scheduler_calls(fake_repo: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "export"
    _lines, log = _log_spy()
    code = sched.cmd_generate(repo_root=fake_repo, log=log, out_dir=out_dir)
    assert code == sched.EXIT_OK
    out_path = out_dir / sched.GENERATE_OUTPUT_NAME
    assert out_path.is_file()
    # 字节级读回（UTF-16 with BOM）后与 build_task_xml 全文一致
    raw = out_path.read_bytes()
    assert raw == sched.build_task_xml(fake_repo).encode("utf-16")
    assert not any(name.endswith(".tmp") for name in os.listdir(out_dir))
    assert sched.verify_task_xml(sched.decode_schtasks_xml(raw),
                                 fake_repo)[0] == sched.STATE_INSTALLED


def test_generate_artifact_utf16_bom_externally_parseable(fake_repo: Path,
                                                          tmp_path: Path) -> None:
    """产物必须与其 XML 声明一致——UTF-16 **带 BOM** 字节（M14-14 R2 实证
    缺陷形态：UTF-8 字节 + UTF-16 声明，System.Xml XmlDocument.Load 拒载
    "no Unicode byte order mark"）。断言逐字节形态，非纯文本 round-trip。"""
    out_dir = tmp_path / "export"
    code = sched.cmd_generate(repo_root=fake_repo, log=_log_spy()[1], out_dir=out_dir)
    assert code == sched.EXIT_OK
    raw = (out_dir / sched.GENERATE_OUTPUT_NAME).read_bytes()
    # UTF-16 LE BOM 起始（外部解析器据 BOM 判 UTF-16；UTF-8 字节则以
    # 3C 3F 78 6D 6C "<?xml" 起始——旧缺陷形态必须在此失败）
    assert raw.startswith(b"\xff\xfe"), "generate 产物必须以 UTF-16 BOM 起始"
    assert not raw.startswith(b"<?xml")
    # 声明与字节一致：BOM 后按 UTF-16LE 解出 prolog 声明 UTF-16
    decoded = raw.decode("utf-16")  # BOM 感知解码（外部解析器同款语义）
    assert decoded.startswith('<?xml version="1.0" encoding="UTF-16"?>')
    # 本工具自身的字节形态严格解码器同样接受该工件，且归属校验通过
    assert sched.decode_schtasks_xml(raw) == decoded.strip()
    assert sched.verify_task_xml(decoded, fake_repo) == (sched.STATE_INSTALLED, [])
    # 与 install 临时文件字节完全一致（同一 encode("utf-16") 形态）
    assert raw == sched.build_task_xml(fake_repo).encode("utf-16")


@pytest.mark.parametrize("break_preflight", ["secret", "venv"])
def test_generate_preflight_failure_zero_write(fake_repo: Path, tmp_path: Path,
                                               break_preflight: str) -> None:
    """generate 预检不过（secret 缺失 / venv 缺失）→ exit 2 且零写入
    （输出目录根本不创建）。"""
    if break_preflight == "secret":
        (fake_repo / "infra" / sched.SECRET_FILE_NAME).unlink()
    else:
        (fake_repo / ".venv" / "Scripts" / "python.exe").unlink()
    out_dir = tmp_path / "export"
    code = sched.cmd_generate(repo_root=fake_repo, log=_log_spy()[1], out_dir=out_dir)
    assert code == sched.EXIT_REFUSED
    assert not out_dir.exists()  # 零写入：目录不创建


def test_generate_symlink_output_refused(fake_repo: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "export"
    out_dir.mkdir()
    target = tmp_path / "target.xml"
    target.write_text("sentinel", encoding="utf-8")
    try:
        os.symlink(target, out_dir / sched.GENERATE_OUTPUT_NAME)
    except OSError:
        pytest.skip("symlink unavailable on this host")
    code = sched.cmd_generate(repo_root=fake_repo, log=_log_spy()[1], out_dir=out_dir)
    assert code == sched.EXIT_REFUSED
    assert target.read_text(encoding="utf-8") == "sentinel"  # symlink 目标零写入


def test_generate_symlink_ancestor_refused(fake_repo: Path, tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    try:
        os.symlink(real_dir, link_dir)
    except OSError:
        pytest.skip("symlink unavailable on this host")
    code = sched.cmd_generate(repo_root=fake_repo, log=_log_spy()[1], out_dir=link_dir)
    assert code == sched.EXIT_REFUSED
    assert list(real_dir.iterdir()) == []  # symlink 祖先目标零写入


# ---------------------------------------------------------------- status


def test_status_five_states_and_exit_codes(fake_repo: Path) -> None:
    xml_text = sched.build_task_xml(fake_repo)
    cases = {
        "installed": (xml_text, sched.EXIT_OK),
        "missing": ("", sched.EXIT_MISSING),
        "foreign": (xml_text.replace(sched.TASK_URI, "urn:someone:else"), sched.EXIT_FOREIGN),
        "malformed": (xml_text.replace(sched.EXECUTION_TIME_LIMIT, "PT2H"), sched.EXIT_MALFORMED),
    }
    for state, (xml, expected) in cases.items():
        fake = FakeSchtasks(repo_root=fake_repo, task_state=state, xml_text=xml)
        code = sched.cmd_status(runner=_gated(fake), repo_root=fake_repo, log=_log_spy()[1])
        assert code == expected, state
        assert not any("/Delete" in " ".join(argv) for argv in fake.calls)
    # unknown：列表查询失败（事实不完整）
    fake = FakeSchtasks(repo_root=fake_repo, list_rc=1)
    code = sched.cmd_status(runner=_gated(fake), repo_root=fake_repo, log=_log_spy()[1])
    assert code == sched.EXIT_ERROR


def test_status_unknown_on_undecodable_xml(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo, task_state="installed", xml_text="")
    fake.run_raw = lambda argv, timeout=30.0: sched.RawCommandResult(
        tuple(str(x) for x in argv), 0, b"\xff\xfe\x33")  # 奇数 UTF-16 截断
    code = sched.cmd_status(runner=_gated(fake), repo_root=fake_repo, log=_log_spy()[1])
    assert code == sched.EXIT_ERROR
    fake2 = FakeSchtasks(repo_root=fake_repo, task_state="installed", xml_text="")
    fake2.run_raw = lambda argv, timeout=30.0: sched.RawCommandResult(
        tuple(str(x) for x in argv), 0, "OEM 错误文案".encode("gbk"))
    code = sched.cmd_status(runner=_gated(fake2), repo_root=fake_repo, log=_log_spy()[1])
    assert code == sched.EXIT_ERROR


# ---------------------------------------------------------------- install / uninstall


@pytest.mark.parametrize("argv", [["install"], ["uninstall"],
                                  ["install", "--confirm",
                                   "EXECUTE PRODUCTION DRIFT WATCH ALERT SCHEDULER CHANGES"],
                                  ["uninstall", "--confirm",
                                   " execute production drift watch alert scheduler change"],
                                  # 四个既有短语绝不互通（M14-127 watcher）
                                  ["install", "--confirm",
                                   "EXECUTE READ-ONLY PRODUCTION DRIFT WATCH"],
                                  # M14-129 scheduler 短语
                                  ["uninstall", "--confirm",
                                   "EXECUTE PRODUCTION DRIFT WATCH SCHEDULER CHANGE"],
                                  # M14-135 dispatch 短语
                                  ["install", "--confirm",
                                   "EXECUTE PRODUCTION DRIFT WATCH ALERT DISPATCH"],
                                  # M14-137 task 短语
                                  ["uninstall", "--confirm",
                                   "EXECUTE PRODUCTION DRIFT WATCH ALERT TASK"]])
def test_mutation_requires_exact_phrase_zero_calls(argv, fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo)
    code = sched.main(argv, runner=_gated(fake, allow_mutation=True))
    assert code == sched.EXIT_ERROR
    assert fake.calls == []  # 短语门禁先于一切 schtasks 调用


def test_all_family_phrases_distinct() -> None:
    """五短语互不相等（watcher/scheduler/dispatch/task/alert-scheduler）——
    冒充/近似永不放行。"""
    phrases = {pdw.CONFIRM_PHRASE, pdwt.TASK_CONFIRM_PHRASE,
               dispatch_mod.CONFIRM_PHRASE,
               alert_task_mod.TASK_CONFIRM_PHRASE,
               sched.TASK_CONFIRM_PHRASE}
    assert sched.TASK_CONFIRM_PHRASE == "EXECUTE PRODUCTION DRIFT WATCH ALERT SCHEDULER CHANGE"
    assert sched.ALERT_TASK_CONFIRM_PHRASE == alert_task_mod.TASK_CONFIRM_PHRASE
    assert len(phrases) == 5


def test_install_happy_path_fake(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo, task_state="missing")
    lines, log = _log_spy()
    code = sched.cmd_install(runner=_gated(fake, allow_mutation=True),
                             repo_root=fake_repo, log=log)
    assert code == sched.EXIT_OK
    creates = [argv for argv in fake.calls if "/Create" in argv]
    assert len(creates) == 1
    assert sched.is_create_command(creates[0])
    assert "/F" not in creates[0]  # create 绝不 force
    # 临时 XML：UTF-16 编码 + 内容精确等于 build_task_xml + 用后即删
    assert fake.created_xml_bytes is not None
    assert fake.created_xml_bytes.decode("utf-16") == sched.build_task_xml(fake_repo)
    tmp_arg = creates[0][5]
    assert not Path(tmp_arg).exists()
    assert not any("/Delete" in " ".join(argv) for argv in fake.calls)
    assert any("install 成功" in line for line in lines)


def test_install_refuses_existing_task(fake_repo: Path) -> None:
    for state in ("installed", "foreign", "malformed"):
        xml_text = sched.build_task_xml(fake_repo)
        if state == "foreign":
            xml_text = xml_text.replace(sched.TASK_URI, "urn:someone:else")
        elif state == "malformed":
            xml_text = xml_text.replace("PT15M", "PT5M")
        fake = FakeSchtasks(repo_root=fake_repo, task_state=state, xml_text=xml_text)
        code = sched.cmd_install(runner=_gated(fake, allow_mutation=True),
                                 repo_root=fake_repo, log=_log_spy()[1])
        assert code == sched.EXIT_ERROR, state
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
                fake._xml_text = sched.build_task_xml(fake_repo)
        return original_list(argv, timeout=timeout)

    fake.run = run  # type: ignore[method-assign]
    code = sched.cmd_install(runner=_gated(fake, allow_mutation=True),
                             repo_root=fake_repo, log=_log_spy()[1])
    assert code == sched.EXIT_ERROR
    assert not any("/Create" in " ".join(argv) for argv in fake.calls)


def test_uninstall_missing_idempotent(fake_repo: Path) -> None:
    fake = FakeSchtasks(repo_root=fake_repo, task_state="missing")
    code = sched.cmd_uninstall(runner=_gated(fake, allow_mutation=True),
                               repo_root=fake_repo, log=_log_spy()[1])
    assert code == sched.EXIT_OK
    assert not any("/Delete" in " ".join(argv) for argv in fake.calls)


def test_uninstall_exact_owned_deletes(fake_repo: Path) -> None:
    xml_text = normalize_task_xml(sched.build_task_xml(fake_repo))
    fake = FakeSchtasks(repo_root=fake_repo, task_state="installed", xml_text=xml_text)
    code = sched.cmd_uninstall(runner=_gated(fake, allow_mutation=True),
                               repo_root=fake_repo, log=_log_spy()[1])
    assert code == sched.EXIT_OK
    deletes = [argv for argv in fake.calls if "/Delete" in argv]
    assert deletes == [("schtasks.exe", "/Delete", "/TN", sched.TASK_NAME, "/F")]
    assert not any("/Create" in " ".join(argv) for argv in fake.calls)


@pytest.mark.parametrize("state,expected", [
    ("foreign", sched.EXIT_FOREIGN),
    ("malformed", sched.EXIT_MALFORMED),
    ("unknown", sched.EXIT_ERROR),
])
def test_uninstall_non_owned_zero_delete(fake_repo: Path, state: str, expected: int) -> None:
    xml_text = sched.build_task_xml(fake_repo)
    if state == "foreign":
        xml_text = xml_text.replace(sched.TASK_URI, "urn:someone:else")
    elif state == "malformed":
        xml_text = xml_text.replace("PT15M", "PT5M")
    fake = FakeSchtasks(repo_root=fake_repo, task_state=state, xml_text=xml_text)
    if state == "unknown":
        fake = FakeSchtasks(repo_root=fake_repo, list_rc=1)
    code = sched.cmd_uninstall(runner=_gated(fake, allow_mutation=True),
                               repo_root=fake_repo, log=_log_spy()[1])
    assert code == expected
    assert not any("/Delete" in " ".join(argv) for argv in fake.calls)


# ---------------------------------------------------------------- VBS wrapper / wrapper 内容校验


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
    # 只调用 M14-137 任务桥（绝不直接调用 M14-135，绝不绕过门禁）
    assert "production_drift_watch_alert_task.py" in text
    assert "production_drift_watch_alert_dispatch.py" not in text
    # 携带 --secret-file + 固定 secret 相对路径 + M14-137 门禁旗标与精确短语
    #（短语经模块常量交叉 pin；短语是公开常量非 secret）
    assert "--secret-file" in text
    assert f'"{sched.SECRET_FILE_DIR_NAME}"' in text
    assert f'"{sched.SECRET_FILE_NAME}"' in text
    assert "--execute" in text
    assert f'"{sched.ALERT_TASK_CONFIRM_PHRASE}"' in text
    # 预检失败专用退出码（2=venv 缺失/3=脚本缺失/4=repo 缺失/5=secret 缺失）
    assert "WScript.Quit 2" in text and "WScript.Quit 3" in text
    assert "WScript.Quit 4" in text and "WScript.Quit 5" in text
    for pattern in SECRET_PATTERNS:
        assert pattern not in text
    # 零文件读写面（secret 只查存在性）、零网络/解释器面
    for token in ("http://", "https://", "mshta", "powershell", "cmd.exe",
                  "CreateTextFile", "OpenTextFile", "ADODB.Stream"):
        assert token not in text, token


def test_verify_wrapper_content_accepts_real_wrapper() -> None:
    ok, issues = sched.verify_wrapper_content(REPO_ROOT)
    assert ok and issues == []


@pytest.mark.parametrize("tamper,issue", [
    # M14-135 dispatch 短语冒充 M14-137 task 短语
    (lambda t: t.replace(sched.ALERT_TASK_CONFIRM_PHRASE,
                         dispatch_mod.CONFIRM_PHRASE), "wrapper-marker-missing"),
    (lambda t: t.replace(sched.SECRET_FILE_NAME, "other-secret.json"),
     "wrapper-marker-missing"),
    (lambda t: t.replace("--secret-file", "--secret"), "wrapper-marker-missing"),
    (lambda t: t.replace(" --execute", ""), "wrapper-marker-missing"),
    (lambda t: t + "\n' C:\\evil\\hardcoded\\path\n", "wrapper-drive-letter-hardcoded"),
    (lambda t: t + "\n' leak " + SECRET_SENTINEL_TOKEN + "\n", "wrapper-forbidden-token"),
    (lambda t: t + "\n' browse https://evil.example/x\n", "wrapper-forbidden-token"),
    (lambda t: t.replace("If Not fso.FileExists(secretFile)",
                         "Set sf = fso.OpenTextFile(secretFile) ' read\n"
                         "If Not fso.FileExists(secretFile)"),
     "wrapper-forbidden-token"),
])
def test_wrapper_content_tamper_refuses_plan_zero_calls(fake_repo: Path,
                                                        tamper, issue: str) -> None:
    wrapper = fake_repo / "tools" / "ops" / "run_production_drift_watch_alert_silent.vbs"
    wrapper.write_text(tamper(VBS.read_text(encoding="utf-8")), encoding="utf-8")
    ok, issues = sched.verify_wrapper_content(fake_repo)
    assert not ok and issue in issues
    # plan fail-fast：预检不过 → exit 1 且零 schtasks 调用
    fake = FakeSchtasks(repo_root=fake_repo, task_state="missing")
    code = sched.cmd_plan(runner=_gated(fake), repo_root=fake_repo, log=_log_spy()[1])
    assert code == sched.EXIT_ERROR
    assert fake.calls == []


def test_wrapper_content_unreadable_refused(tmp_path: Path) -> None:
    ok, issues = sched.verify_wrapper_content(tmp_path)
    assert not ok and issues == ["wrapper-unreadable"]


def test_preflight_rejects_symlinked_identities(fake_repo: Path, tmp_path: Path) -> None:
    """安全路径校验：必需文件身份（任务桥脚本 / secret 文件）为 symlink
    → fail-closed 拒绝（可用性受限主机 skip）。"""
    real = tmp_path / "real-target.py"
    real.write_text("# real", encoding="utf-8")
    script_link = fake_repo / "tools" / "ops" / "production_drift_watch_alert_task.py"
    script_link.unlink()
    secret_link = fake_repo / "infra" / sched.SECRET_FILE_NAME
    secret_link.unlink()
    try:
        os.symlink(real, script_link)
        os.symlink(real, secret_link)
    except OSError:
        pytest.skip("symlink unavailable on this host")
    fake = FakeSchtasks(repo_root=fake_repo, task_state="missing")
    code = sched.cmd_plan(runner=_gated(fake), repo_root=fake_repo, log=_log_spy()[1])
    assert code == sched.EXIT_ERROR
    assert fake.calls == []


# ---------------------------------------------------------------- secret 值永不披露


def test_secret_value_never_disclosed(fake_repo: Path, tmp_path: Path) -> None:
    """secret 纪律：合成 sentinel 值绝不出现在任何输出面（plan 日志 /
    generate 工件 / wrapper 文本）；输出面只允许固定相对路径本身。"""
    fake = FakeSchtasks(repo_root=fake_repo, task_state="missing")
    lines, log = _log_spy()
    code = sched.cmd_plan(runner=_gated(fake), repo_root=fake_repo, log=log)
    assert code == sched.EXIT_OK
    joined = "\n".join(lines)
    assert SECRET_SENTINEL_TOKEN not in joined
    assert "hooks.example.invalid" not in joined  # secret 文件内的 URL 值
    assert sched.SECRET_FILE_DISPLAY in joined     # 路径本身允许出现
    # generate 工件：XML 不含 secret 路径/值
    out_dir = tmp_path / "export"
    assert sched.cmd_generate(repo_root=fake_repo, log=_log_spy()[1],
                              out_dir=out_dir) == sched.EXIT_OK
    raw = (out_dir / sched.GENERATE_OUTPUT_NAME).read_bytes().decode("utf-16")
    assert SECRET_SENTINEL_TOKEN not in raw
    assert sched.SECRET_FILE_NAME not in raw
    # wrapper：含路径不含值
    wrapper_text = VBS.read_text(encoding="utf-8")
    assert SECRET_SENTINEL_TOKEN not in wrapper_text
    assert sched.SECRET_FILE_NAME in wrapper_text


def test_source_never_reads_secret_file() -> None:
    """源码契约：凡提及 secret 的行恒无读取面（存在性检查 only）。"""
    source = SCRIPT.read_text(encoding="utf-8")
    for line in source.splitlines():
        if "secret" in line.lower():
            assert not any(tok in line for tok in
                           ("read_text", "read_bytes", "json.load", "open(", "urlopen")), line


# ---------------------------------------------------------------- 身份不冲突 / 源码契约


def test_no_identity_collision_with_sibling_tasks() -> None:
    """与 M14-129 drift watch watcher、M14-14 monitoring pipeline、M14-06
    startup（production recovery）、M14-77 voice sidecar watchdog 四任务
    零身份冲突（同一机器并存）；绝不复用/改动既有 watcher 任务。"""
    siblings = (pdwt, mpt, wst, vswt)
    for sibling in siblings:
        assert sched.TASK_NAME != sibling.TASK_NAME
        assert sched.TASK_URI != sibling.TASK_URI
        assert sched.NORMALIZED_TASK_URI != sibling.NORMALIZED_TASK_URI
        assert sched.TASK_DESCRIPTION != sibling.TASK_DESCRIPTION
    assert VBS.name not in (WATCHER_VBS.name, MONITORING_VBS.name,
                            STARTUP_VBS.name, VOICE_VBS.name)
    assert VBS.name != WATCHER_VBS.name  # 与 watcher wrapper 文件名一字之差也是不同文件
    # 本工具 XML 只指向自己的 wrapper/URI，绝不含兄弟任务标识（含 watcher）
    xml_text = sched.build_task_xml(REPO_ROOT)
    assert "run_production_drift_watch_alert_silent.vbs" in xml_text
    assert sched.TASK_URI in xml_text
    for foreign_marker in ("run_production_drift_watch_silent",
                           "run_monitoring_pipeline_silent", "run_production_recovery_silent",
                           "run_voice_sidecar_watchdog_silent", "monitoring_pipeline_task",
                           "windows_startup_task", "voice_sidecar_watchdog_task",
                           "production_drift_watch_task.py",
                           pdwt.TASK_URI, mpt.TASK_URI, wst.TASK_URI, vswt.TASK_URI):
        assert foreign_marker not in xml_text, foreign_marker


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
    # secret 形态字面量仅允许出现在 SECRET_PATTERNS 黑名单定义行（wrapper
    # 校验的检测目标），其余任何行出现即失败
    for line in source.splitlines():
        for pattern in SECRET_PATTERNS:
            if pattern in line:
                assert "SECRET_PATTERNS" in line, (pattern, line)
    for token in ("environ", "getenv", "docker", "urlopen", "requests"):
        assert token not in source
    # 两个 subprocess 执行点（run / run_raw）均无 shell=
    assert source.count("subprocess.run(") == 2
    for match in re.finditer(r"subprocess\.run\(", source):
        assert "shell" not in source[match.start():match.start() + 260]
    assert "CREATE_NO_WINDOW" in source


def test_parser_defaults_and_choices() -> None:
    parser = sched.build_parser()
    args = parser.parse_args(["status"])
    assert args.command == "status" and args.confirm == ""
    with pytest.raises(SystemExit):
        parser.parse_args(["run"])  # 不存在 run 子命令（设计即拒绝）
