"""M14-53 audit_archive_task.py 聚焦契约测试(零真实 schtasks——全部经注入
FakeSchtasks;与 test_monitoring_pipeline_task.py 同款注入模式,但为独立精简版)。

覆盖:任务身份/XML 契约/转义/归一化认可/foreign-malformed 拒绝/四字节形态解码/
UTF-16 BOM 磁盘往返/确认短语门禁(零调用)/plan-status-generate 零 mutation/
install happy+拒配/uninstall 五态/白名单结构拒绝/VBS 调用契约/源码契约
(无网络无 /Run 无 --python)/readiness CLI cross-pin。
"""

from __future__ import annotations

import importlib.util
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


TASK = _load_module("aios_audit_archive_task", REPO_ROOT / "tools" / "ops" / "audit_archive_task.py")
READINESS = _load_module("aios_audit_archive_readiness",
                         REPO_ROOT / "tools" / "ops" / "audit_archive_readiness.py")
VBS_TEXT = (REPO_ROOT / "tools" / "ops" / "run_audit_archive_readiness_silent.vbs").read_text(
    encoding="utf-8")
TASK_SOURCE = (REPO_ROOT / "tools" / "ops" / "audit_archive_task.py").read_text(encoding="utf-8")
READINESS_SOURCE = (REPO_ROOT / "tools" / "ops" / "audit_archive_readiness.py").read_text(
    encoding="utf-8")

ET.register_namespace("", "http://schemas.microsoft.com/windows/2004/02/mit/task")


# ---------------------------------------------------------------- FakeSchtasks


def _normalize_registered_xml(text: str) -> str:
    """模拟 Task Scheduler 注册归一化:URI 重写为路径形态 + 省略三个默认值元素
    (输出保留 ``<?xml`` prolog——真实 schtasks /XML 输出恒带 prolog)。"""
    root = ET.fromstring(text)
    uri = root.find("t:RegistrationInfo/t:URI", TASK.TASK_NS)
    if uri is not None:
        uri.text = TASK.NORMALIZED_TASK_URI
    for xpath in ("t:Triggers/t:TimeTrigger/t:Enabled",
                  "t:Settings/t:Enabled",
                  "t:Principals/t:Principal/t:RunLevel"):
        node = root.find(xpath, TASK.TASK_NS)
        if node is not None:
            parent = root.find(xpath.rsplit("/", 1)[0], TASK.TASK_NS)
            parent.remove(node)
    return '<?xml version="1.0" encoding="UTF-16"?>\n' + ET.tostring(root, encoding="unicode")


class FakeSchtasks:
    """内存版 schtasks:任务表 + 全调用记录;/XML 明细以 UTF-16LE 无 BOM(管道
    观测形态)返回;normalize_on_create=True 模拟注册侧归一化。"""

    def __init__(self, tasks=None, *, list_fail=False, normalize_on_create=False):
        self.tasks: dict[str, bytes] = dict(tasks or {})
        self.list_fail = list_fail
        self.normalize_on_create = normalize_on_create
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout=60.0):
        tokens = tuple(str(item) for item in argv)
        self.calls.append(tokens)
        if tokens == ("schtasks.exe", "/Query", "/FO", "CSV", "/NH"):
            if self.list_fail:
                return TASK.CommandResult(tokens, 1, "", "ERROR: list failed")
            lines = [rf'"\{name}","{name}","Ready"' for name in sorted(self.tasks)]
            return TASK.CommandResult(tokens, 0, "\r\n".join(lines), "")
        if (len(tokens) == 6 and tokens[:4] == ("schtasks.exe", "/Create", "/TN", TASK.TASK_NAME)
                and tokens[4] == "/XML"):
            text = TASK.decode_schtasks_xml(Path(tokens[5]).read_bytes())
            if self.normalize_on_create:
                text = _normalize_registered_xml(text)
            self.tasks[TASK.TASK_NAME] = text.encode("utf-16-le")
            return TASK.CommandResult(tokens, 0, "", "")
        if tokens == ("schtasks.exe", "/Delete", "/TN", TASK.TASK_NAME, "/F"):
            self.tasks.pop(TASK.TASK_NAME, None)
            return TASK.CommandResult(tokens, 0, "", "")
        raise AssertionError(f"FakeSchtasks 收到非预期命令形态: {tokens}")

    def run_raw(self, argv, *, timeout=30.0):
        tokens = tuple(str(item) for item in argv)
        self.calls.append(tokens)
        if tokens == ("schtasks.exe", "/Query", "/TN", TASK.TASK_NAME, "/XML"):
            return TASK.RawCommandResult(tokens, 0, self.tasks[TASK.TASK_NAME])
        raise AssertionError(f"FakeSchtasks 收到非预期原始查询: {tokens}")


@pytest.fixture()
def repo(tmp_path):
    """最小伪仓库:preflight 所需四项(repo 根 + readiness CLI + VBS + venv python);
    刻意无 state/policy 输入文件(plan 契约:不要求它们存在)。"""
    root = tmp_path / "aios-repo"
    for rel in ("tools/ops/audit_archive_readiness.py",
                "tools/ops/run_audit_archive_readiness_silent.vbs",
                ".venv/Scripts/python.exe"):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")
    return root


def _fake_with(repo_root, *, transform=None, normalize=False):
    text = TASK.build_task_xml(repo_root)
    if transform:
        text = transform(text)
    if normalize:
        text = _normalize_registered_xml(text)
    return FakeSchtasks({TASK.TASK_NAME: text.encode("utf-16-le")})


def _mutating(fake):
    return TASK.GatedSchtasks(fake, allow_mutation=True)


def _readonly(fake):
    return TASK.GatedSchtasks(fake, allow_mutation=False)


def _nolog(message):
    pass


def _mutation_calls(fake):
    return [c for c in fake.calls
            if c[:1] == ("schtasks.exe",) and c[1:2] in (("/Create",), ("/Delete",))]


# ---------------------------------------------------------------- 身份与 XML 契约


def test_identity_contract():
    assert TASK.TASK_NAME == "AIOS-Audit-Archive-Readiness"
    assert TASK.TASK_URI == "urn:aios:m14-53:audit-archive-readiness"
    assert TASK.NORMALIZED_TASK_URI == f"\\{TASK.TASK_NAME}"
    assert TASK.OWNED_TASK_URIS == frozenset({TASK.TASK_URI, TASK.NORMALIZED_TASK_URI})
    # 归属标记唯一且点名 M14-53 与管理文件
    assert "M14-53" in TASK.TASK_DESCRIPTION
    assert "tools/ops/audit_archive_task.py" in TASK.TASK_DESCRIPTION
    assert TASK.REPETITION_INTERVAL == "P1D"  # 每日一次就绪报告
    assert TASK.EXECUTION_TIME_LIMIT == "PT30M"
    assert TASK.WSCRIPT == "wscript.exe"
    assert TASK.TASK_CONFIRM_PHRASE == "EXECUTE AUDIT ARCHIVE READINESS SCHEDULER CHANGE"


def test_build_task_xml_contract(repo):
    xml_text = TASK.build_task_xml(repo)
    assert xml_text.startswith('<?xml version="1.0" encoding="UTF-16"?>')
    root = ET.fromstring(xml_text)
    ns = TASK.TASK_NS

    def text_of(path):
        node = root.find(path, ns)
        assert node is not None and node.text, path
        return node.text.strip()

    assert text_of("t:RegistrationInfo/t:URI") == TASK.TASK_URI
    assert text_of("t:RegistrationInfo/t:Description") == TASK.TASK_DESCRIPTION
    assert text_of("t:Actions/t:Exec/t:Command") == "wscript.exe"
    vbs = (repo / "tools" / "ops" / "run_audit_archive_readiness_silent.vbs").resolve()
    assert text_of("t:Actions/t:Exec/t:Arguments") == f'//B //Nologo "{vbs}"'
    assert Path(text_of("t:Actions/t:Exec/t:WorkingDirectory")) == repo.resolve()
    assert text_of("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval") == "P1D"
    assert root.find("t:Triggers/t:TimeTrigger/t:Repetition/t:Duration", ns) is None  # 无限重复
    assert text_of("t:Triggers/t:TimeTrigger/t:Enabled") == "true"
    assert text_of("t:Settings/t:Hidden") == "true"
    assert text_of("t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew"
    assert text_of("t:Settings/t:StartWhenAvailable") == "true"
    assert text_of("t:Settings/t:ExecutionTimeLimit") == "PT30M"
    assert text_of("t:Settings/t:DisallowStartIfOnBatteries") == "false"
    assert text_of("t:Settings/t:StopIfGoingOnBatteries") == "false"
    assert text_of("t:Principals/t:Principal/t:LogonType") == "InteractiveToken"
    assert text_of("t:Principals/t:Principal/t:RunLevel") == "LeastPrivilege"
    # 纯函数往返:刚生成的 XML 即为 exact-owned
    assert TASK.verify_task_xml(xml_text, repo) == (TASK.STATE_INSTALLED, [])


def test_xml_path_escaping(tmp_path):
    root = tmp_path / "repo & tags 'daily' (x64)"
    root.mkdir()
    xml_text = TASK.build_task_xml(root)
    assert "&amp;" in xml_text  # 特殊字符经 escape 写入
    state, _ = TASK.verify_task_xml(xml_text, root)
    assert state == TASK.STATE_INSTALLED  # 解析侧还原后仍精确匹配


# ---------------------------------------------------------------- 归一化 / 拒绝 / 解码


def test_verify_accepts_scheduler_normalization(repo):
    normalized = _normalize_registered_xml(TASK.build_task_xml(repo))
    assert TASK.verify_task_xml(normalized, repo) == (TASK.STATE_INSTALLED, [])


@pytest.mark.parametrize("transform,expected_state", [
    (lambda t: t.replace(TASK.TASK_URI, "urn:example:foreign"), TASK.STATE_FOREIGN),
    (lambda t: t.replace(f"<Command>{TASK.WSCRIPT}</Command>", "<Command>evil.exe</Command>"),
     TASK.STATE_FOREIGN),
    (lambda t: t.replace(f"<Interval>{TASK.REPETITION_INTERVAL}</Interval>",
                         "<Interval>PT15M</Interval>"), TASK.STATE_MALFORMED),
    (lambda t: "<!DOCTYPE Task [<!ENTITY x 'y'>]>\n" + t, TASK.STATE_MALFORMED),
    (lambda t: t + "</Task>", TASK.STATE_MALFORMED),
])
def test_verify_rejects_foreign_and_malformed(repo, transform, expected_state):
    state, issues = TASK.verify_task_xml(transform(TASK.build_task_xml(repo)), repo)
    assert state == expected_state
    if expected_state == TASK.STATE_MALFORMED:
        assert issues


def test_malformed_reports_field_names(repo):
    state, issues = TASK.verify_task_xml(
        TASK.build_task_xml(repo).replace(f"<Interval>{TASK.REPETITION_INTERVAL}</Interval>",
                                          "<Interval>PT15M</Interval>"), repo)
    assert state == TASK.STATE_MALFORMED
    assert "Triggers/TimeTrigger/Repetition/Interval" in issues


def test_defaulted_omission_requires_all_others_exact(repo):
    text = _normalize_registered_xml(TASK.build_task_xml(repo).replace(
        "<Hidden>true</Hidden>", "<Hidden>false</Hidden>"))
    state, issues = TASK.verify_task_xml(text, repo)
    assert state == TASK.STATE_MALFORMED
    assert "Settings/Hidden" in issues
    # 省略豁免仅在其余字段全精确时成立——此处连带按默认值不认可
    assert "Settings/Enabled" in issues


def test_explicit_non_default_runlevel_rejected(repo):
    state, issues = TASK.verify_task_xml(
        TASK.build_task_xml(repo).replace("<RunLevel>LeastPrivilege</RunLevel>",
                                          "<RunLevel>HighestAvailable</RunLevel>"), repo)
    assert state == TASK.STATE_MALFORMED
    assert "Principals/Principal/RunLevel" in issues


def _with_extra_element(xml_text: str, container_xpath: str, fragment: str) -> str:
    root = ET.fromstring(xml_text)
    container = root.find(container_xpath, TASK.TASK_NS)
    container.append(ET.fromstring(fragment))
    return '<?xml version="1.0" encoding="UTF-16"?>\n' + ET.tostring(root, encoding="unicode")


def test_structural_additions_rejected(repo):
    """监督修正:归属字段全对也不得多挂结构条目——多一个 Exec / 触发器 /
    Principal(可夹带第二个动作、另一套调度或另一身份)一律 malformed。"""
    base = TASK.build_task_xml(repo)
    ns = "http://schemas.microsoft.com/windows/2004/02/mit/task"
    cases = [
        (_with_extra_element(base, "t:Actions",
                             f'<Exec xmlns="{ns}"><Command>cmd.exe</Command></Exec>'),
         "Actions/Exec 恰好一个"),
        (_with_extra_element(base, "t:Triggers",
                             f'<CalendarTrigger xmlns="{ns}"><Enabled>true</Enabled></CalendarTrigger>'),
         "Triggers 恰好一个 TimeTrigger"),
        (_with_extra_element(base, "t:Principals",
                             f'<Principal xmlns="{ns}"><RunLevel>HighestAvailable</RunLevel></Principal>'),
         "Principals/Principal 恰好一个"),
    ]
    for text, issue_marker in cases:
        state, issues = TASK.verify_task_xml(text, repo)
        assert state == TASK.STATE_MALFORMED
        assert any(issue_marker in issue for issue in issues)


def test_wrong_task_root_rejected(repo):
    root = ET.fromstring(TASK.build_task_xml(repo))
    root.tag = f"{{{TASK.TASK_NS['t']}}}TaskAlias"  # 同命名空间、可解析、根标签漂移
    text = '<?xml version="1.0" encoding="UTF-16"?>\n' + ET.tostring(root, encoding="unicode")
    state, issues = TASK.verify_task_xml(text, repo)
    assert state == TASK.STATE_MALFORMED
    assert any("根元素" in issue for issue in issues)


def test_decode_schtasks_xml_byte_forms(repo):
    text = TASK.build_task_xml(repo)
    for form, data in [
        ("bom_le", text.encode("utf-16")),
        ("bom_be", b"\xfe\xff" + text.encode("utf-16-be")),
        ("utf16le_no_bom", text.encode("utf-16-le")),
        ("utf8", text.encode("utf-8")),
    ]:
        assert TASK.decode_schtasks_xml(data) == text.strip(), form
    with pytest.raises(ValueError):
        TASK.decode_schtasks_xml(b"\x00\x01garbage-not-xml")


# ---------------------------------------------------------------- 确认短语门禁


@pytest.mark.parametrize("argv", [["install"], ["uninstall"]])
def test_confirmation_phrase_gate_zero_calls(argv):
    for confirm in (None, "WRONG PHRASE", "execute audit archive readiness scheduler change"):
        args = list(argv)
        if confirm is not None:
            args += ["--confirm", confirm]
        fake = FakeSchtasks()
        assert TASK.main(args, runner=fake, log=_nolog) == TASK.EXIT_ERROR
        assert fake.calls == []  # 短语不匹配 → 零 schtasks 调用(fail-closed)


def test_confirmation_phrase_exact_form_accepted_by_parser():
    args = TASK.build_parser().parse_args(
        ["install", "--confirm", TASK.TASK_CONFIRM_PHRASE])
    assert args.confirm == TASK.TASK_CONFIRM_PHRASE
    assert args.command == "install"


# ---------------------------------------------------------------- plan / status / generate


def test_plan_missing_ok_and_zero_mutation(repo):
    fake = FakeSchtasks()
    assert TASK.cmd_plan(runner=_readonly(fake), repo_root=repo, log=_nolog) == TASK.EXIT_OK
    assert fake.calls == [("schtasks.exe", "/Query", "/FO", "CSV", "/NH")]
    assert not (repo / ".verify").exists()  # plan 不要求也不创建 state/policy 工件目录


@pytest.mark.parametrize("fake_builder,expected", [
    (lambda repo: _fake_with(repo), TASK.EXIT_ERROR),  # installed:幂等拒绝
    (lambda repo: _fake_with(repo, transform=lambda t: t.replace(
        TASK.TASK_URI, "urn:example:foreign")), TASK.EXIT_ERROR),
    (lambda repo: FakeSchtasks(list_fail=True), TASK.EXIT_ERROR),  # unknown fail-closed
])
def test_plan_refuses_when_task_exists_or_unknown(repo, fake_builder, expected):
    fake = fake_builder(repo)
    assert TASK.cmd_plan(runner=_readonly(fake), repo_root=repo, log=_nolog) == expected
    assert _mutation_calls(fake) == []


def test_plan_preflight_failure(repo, tmp_path):
    bare = tmp_path / "bare-repo"  # 无 readiness CLI/VBS/venv python
    bare.mkdir()
    fake = FakeSchtasks()
    assert TASK.cmd_plan(runner=_readonly(fake), repo_root=bare, log=_nolog) == TASK.EXIT_ERROR
    assert _mutation_calls(fake) == []


def test_status_five_states(repo):
    cases = [
        (_fake_with(repo), TASK.EXIT_OK),
        (FakeSchtasks(), TASK.EXIT_MISSING),
        (_fake_with(repo, transform=lambda t: t.replace(
            TASK.TASK_URI, "urn:example:foreign")), TASK.EXIT_FOREIGN),
        (_fake_with(repo, transform=lambda t: t.replace(
            f"<Interval>{TASK.REPETITION_INTERVAL}</Interval>", "<Interval>PT2D</Interval>")),
         TASK.EXIT_MALFORMED),
        (FakeSchtasks(list_fail=True), TASK.EXIT_ERROR),  # unknown
    ]
    for fake, expected in cases:
        runner = _readonly(fake)
        assert TASK.cmd_status(runner=runner, repo_root=repo, log=_nolog) == expected
        assert _mutation_calls(fake) == []


def test_generate_utf16_bom_disk_roundtrip(repo, tmp_path):
    out_dir = tmp_path / "export"
    assert TASK.cmd_generate(repo_root=repo, log=_nolog, out_dir=out_dir) == TASK.EXIT_OK
    out_path = out_dir / "scheduled-task.xml"
    payload = out_path.read_bytes()
    assert payload.startswith(b"\xff\xfe")  # UTF-16 with BOM,与 XML 声明一致
    state, issues = TASK.verify_task_xml(TASK.decode_schtasks_xml(payload), repo)
    assert state == TASK.STATE_INSTALLED and issues == []
    assert sorted(p.name for p in out_dir.iterdir()) == ["scheduled-task.xml"]  # 临时件已清


# ---------------------------------------------------------------- install / uninstall


@pytest.mark.parametrize("normalize", [False, True])
def test_install_happy_path_and_post_verify(repo, normalize):
    fake = FakeSchtasks(normalize_on_create=normalize)
    assert TASK.cmd_install(runner=_mutating(fake), repo_root=repo, log=_nolog) == TASK.EXIT_OK
    creates = [c for c in fake.calls if c[1:2] == ("/Create",)]
    assert len(creates) == 1 and creates[0][4] == "/XML"
    assert creates[0][5].endswith(".xml")
    assert not Path(creates[0][5]).exists()  # 临时 XML 用后即删
    assert TASK.TASK_NAME in fake.tasks
    assert TASK.cmd_status(runner=_readonly(fake), repo_root=repo, log=_nolog) == TASK.EXIT_OK


@pytest.mark.parametrize("fake_builder", [
    lambda repo: _fake_with(repo),  # installed 同名:绝不覆盖
    lambda repo: _fake_with(repo, transform=lambda t: t.replace(
        TASK.TASK_URI, "urn:example:foreign")),
    lambda repo: _fake_with(repo, transform=lambda t: t.replace(
        f"<Interval>{TASK.REPETITION_INTERVAL}</Interval>", "<Interval>PT2D</Interval>")),
    lambda repo: FakeSchtasks(list_fail=True),  # unknown fail-closed
])
def test_install_refusals(repo, fake_builder):
    fake = fake_builder(repo)
    assert TASK.cmd_install(runner=_mutating(fake), repo_root=repo, log=_nolog) == TASK.EXIT_ERROR
    assert _mutation_calls(fake) == []


def test_install_preflight_failure_zero_calls(repo, tmp_path):
    bare = tmp_path / "bare-repo"
    bare.mkdir()
    fake = FakeSchtasks()
    assert TASK.cmd_install(runner=_mutating(fake), repo_root=bare, log=_nolog) == TASK.EXIT_ERROR
    assert fake.calls == []  # preflight 在任何 schtasks 查询之前拒绝


def test_uninstall_idempotent_missing():
    fake = FakeSchtasks()
    assert TASK.cmd_uninstall(runner=_mutating(fake), repo_root=Path("unused-repo"),
                              log=_nolog) == TASK.EXIT_OK
    assert fake.calls == [("schtasks.exe", "/Query", "/FO", "CSV", "/NH")]


@pytest.mark.parametrize("fake_builder,expected", [
    (lambda repo: _fake_with(repo, transform=lambda t: t.replace(
        TASK.TASK_URI, "urn:example:foreign")), TASK.EXIT_FOREIGN),
    (lambda repo: _fake_with(repo, transform=lambda t: t.replace(
        f"<Interval>{TASK.REPETITION_INTERVAL}</Interval>", "<Interval>PT2D</Interval>")),
     TASK.EXIT_MALFORMED),
    (lambda repo: FakeSchtasks(list_fail=True), TASK.EXIT_ERROR),  # unknown
])
def test_uninstall_refuses_non_owned(repo, fake_builder, expected):
    fake = fake_builder(repo)
    assert TASK.cmd_uninstall(runner=_mutating(fake), repo_root=repo, log=_nolog) == expected
    assert _mutation_calls(fake) == []  # 零 /Delete


def test_uninstall_deletes_only_exact_owned(repo):
    fake = _fake_with(repo)
    assert TASK.cmd_uninstall(runner=_mutating(fake), repo_root=repo, log=_nolog) == TASK.EXIT_OK
    deletes = [c for c in fake.calls if c[1:2] == ("/Delete",)]
    assert deletes == [("schtasks.exe", "/Delete", "/TN", TASK.TASK_NAME, "/F")]
    assert TASK.TASK_NAME not in fake.tasks


# ---------------------------------------------------------------- 白名单结构拒绝


class _RecordingRunner:
    def __init__(self):
        self.calls = []

    def run(self, argv, *, timeout=60.0):
        tokens = tuple(str(item) for item in argv)
        self.calls.append(tokens)
        return TASK.CommandResult(tokens, 0, "", "")

    def run_raw(self, argv, *, timeout=30.0):
        tokens = tuple(str(item) for item in argv)
        self.calls.append(tokens)
        return TASK.RawCommandResult(tokens, 0, b"<?xml")


def test_gated_schtasks_whitelist_shapes():
    list_query = ("schtasks.exe", "/Query", "/FO", "CSV", "/NH")
    xml_query = ("schtasks.exe", "/Query", "/TN", TASK.TASK_NAME, "/XML")
    create = ("schtasks.exe", "/Create", "/TN", TASK.TASK_NAME, "/XML", "fixture-x.xml")
    delete = ("schtasks.exe", "/Delete", "/TN", TASK.TASK_NAME, "/F")
    inner = _RecordingRunner()

    gated_readonly = TASK.GatedSchtasks(inner, allow_mutation=False)
    gated_readonly.run(list_query)
    gated_readonly.run_raw(xml_query)
    with pytest.raises(TASK.RunnerError):
        gated_readonly.run(create)  # 读路径结构性禁止 mutation
    with pytest.raises(TASK.RunnerError):
        gated_readonly.run(delete)

    gated_mutating = TASK.GatedSchtasks(inner, allow_mutation=True)
    gated_mutating.run(create)
    gated_mutating.run(delete)


@pytest.mark.parametrize("argv", [
    ("schtasks.exe", "/Run", "/TN", TASK.TASK_NAME),
    ("schtasks.exe", "/Change", "/TN", TASK.TASK_NAME, "/ENABLE"),
    ("schtasks.exe", "/End", "/TN", TASK.TASK_NAME),
    ("schtasks.exe", "/Query", "/TN", "Other-Task", "/XML"),
    ("schtasks.exe", "/Create", "/TN", TASK.TASK_NAME, "/XML", "fixture-x.xml", "/F"),
    ("schtasks.exe", "/Create", "/TN", "Other-Task", "/XML", "fixture-x.xml"),
    ("schtasks.exe", "/Create", "/TN", TASK.TASK_NAME, "/XML", "-flag"),
    ("schtasks.exe", "/Delete", "/TN", TASK.TASK_NAME),
    ("schtasks.exe", "/Delete", "/TN", "Other-Task", "/F"),
    ("schtasks.exe",),
])
def test_gated_schtasks_rejects_non_whitelisted(argv):
    gated = TASK.GatedSchtasks(_RecordingRunner(), allow_mutation=True)
    with pytest.raises(TASK.RunnerError):
        gated.run(argv)


# ---------------------------------------------------------------- VBS wrapper 契约


def test_vbs_source_ascii_only():
    """真实 supervisor cscript 干净仓库语法探针(BLOCKER 回合)锁定:VBS 中
    UTF-8 非 ASCII 注释令 cscript 在默认代码页下编译失败(missing
    statement,任何预检前即失败)——提交的 VBS 内容/注释必须纯 ASCII
    (ASCII + LF 行尾在该探针下解析安全)。"""
    assert VBS_TEXT.isascii()


def test_vbs_wrapper_invocation_contract():
    assert VBS_TEXT.count("GetParentFolderName") == 3  # 文件 → ops → tools → repo
    assert ".venv" in VBS_TEXT and "python.exe" in VBS_TEXT  # 恒 repo venv Python
    assert "audit_archive_readiness.py" in VBS_TEXT  # 恒 repo readiness CLI
    assert "shell.CurrentDirectory = repoRoot" in VBS_TEXT  # cwd = repo 根
    assert "shell.Run(command, 0, True)" in VBS_TEXT  # 隐藏窗口 + 等待
    assert "WScript.Quit exitCode" in VBS_TEXT  # 退出码透传
    assert "D:" not in VBS_TEXT and "C:" not in VBS_TEXT  # 无硬编码盘符
    # canonical gitignored state/policy/output 路径
    for token in (".verify", "artifacts", "m14-53-audit-archive-readiness",
                  "state.json", "policy.json", "readiness.json"):
        assert token in VBS_TEXT, token
    for flag in ("--state", "--policy", "--output"):
        assert flag in VBS_TEXT, flag
    assert "--now" not in VBS_TEXT.split("command = ", 1)[1]  # 拼接的命令行不含 --now
    # 预检专用退出码段:4=repo 根缺失;2=venv python;3=readiness CLI;5=工件目录创建失败
    for code in (2, 3, 4, 5):
        assert f"WScript.Quit {code}" in VBS_TEXT, code
    # wrapper 不提供 Python/脚本覆盖面:无环境变量、无第二 Python 形态
    assert "WScript.Arguments" not in VBS_TEXT and "Environment" not in VBS_TEXT


def test_vbs_artifacts_dir_chain_preflight():
    """监督修正:干净 checkout 下 .verify / .verify\\artifacts 均可能缺席,
    CreateFolder 不递归——wrapper 必须逐级确保且仅确保这条链(叶子目录为
    第三级),绝不触碰其它路径。真实 cscript 探针后进一步收紧:artifactsDir
    恰好三级 stepwise BuildPath 构建(旧形态多余一层嵌套 BuildPath 在运行
    时报 invalid BuildPath arguments)。"""
    stepwise = (
        'verifyDir = fso.BuildPath(repoRoot, ".verify")\n'
        'artifactsParentDir = fso.BuildPath(verifyDir, "artifacts")\n'
        'artifactsDir = fso.BuildPath(artifactsParentDir, '
        '"m14-53-audit-archive-readiness")\n'
    )
    assert stepwise in VBS_TEXT  # 恰三级 stepwise 构建,每级恰一次 BuildPath
    assert VBS_TEXT.count('BuildPath(repoRoot, ".verify")') == 1  # 旧嵌套重复形态已消除
    assert "artifactsChain = Array(verifyDir, artifactsParentDir, artifactsDir)" in VBS_TEXT
    chain_section = VBS_TEXT.split("artifactsChain", 1)[1]
    assert "For Each chainItem In artifactsChain" in VBS_TEXT  # 逐级循环确保
    assert chain_section.count("CreateFolder(chainItem)") == 1  # 每级仅此一次创建
    # 每一级已存在则零操作;任何一级失败即专用退出码 5(不级联触碰其它路径)
    assert "FolderExists(chainItem)" in chain_section
    assert "WScript.Quit 5" in chain_section


# ---------------------------------------------------------------- 源码契约 + cross-pin


def test_source_contract_no_network_no_mutation_commands_no_python_override():
    forbidden_imports = re.findall(
        r"^\s*(?:import|from)\s+(requests|urllib|socket|boto3|docker|http)\b",
        TASK_SOURCE, re.MULTILINE)
    assert forbidden_imports == []
    assert "environ" not in TASK_SOURCE  # 零 env 读取
    # 绝不构造 /Run、/Change、/End、/Start 等执行/变更形态(文档文本亦不作为字面量)
    assert re.search(r'"/(?:Run|Change|End|Start)"', TASK_SOURCE) is None
    assert TASK.TASK_CONFIRM_PHRASE in TASK_SOURCE
    option_flags = [flag for action in TASK.build_parser()._actions
                    for flag in action.option_strings]
    assert "--python" not in option_flags  # 无 Python 覆盖
    command_action = next(a for a in TASK.build_parser()._actions if a.dest == "command")
    assert set(command_action.choices) == {"plan", "generate", "status", "install", "uninstall"}


def test_readiness_cli_crosspin():
    parser = READINESS._build_parser()
    args = parser.parse_args(["--state", "state.json", "--policy", "policy.json",
                              "--output", "readiness.json"])
    assert (args.state, args.policy, args.output) == (
        "state.json", "policy.json", "readiness.json")
    assert args.now is None  # --now 可选且默认缺席(VBS 刻意不传)
    parser.parse_args(["--state", "s", "--policy", "p", "--output", "o",
                       "--now", "2026-01-01T00:00:00+00:00"])  # 接受 --now 形态
    with pytest.raises(SystemExit):  # state/policy/output 必填
        parser.parse_args(["--state", "s.json"])
    # readiness CLI 不建目录 → canonical 工件目录由 VBS wrapper 唯一负责创建
    assert "mkdir" not in READINESS_SOURCE and "makedirs" not in READINESS_SOURCE
    # canonical 路径三方一致:VBS 传给 CLI 的正是固定 gitignored 工件路径
    assert "m14-53-audit-archive-readiness" in VBS_TEXT
