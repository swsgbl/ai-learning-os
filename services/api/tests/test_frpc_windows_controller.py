"""M14-155 Windows frpc 常驻控制器（tools/ops/frpc_windows_controller.py）契约测试。

覆盖矩阵（Round 1 后）：
1. preflight：真实渲染配置 + 假 frpc.exe 通过；失败矩阵（exe 名错/缺失/
   符号链接、**相对路径拒绝**、serverAddr 占位/私网、inline token、
   非 loopback 后端、TLS 关闭、占位 customDomains、token 文件缺失/
   符号链接/路径形态错误）全拒；**token 内容不读取**（非 UTF-8/极短
   内容的 token 文件 preflight 仍通过——弱值归 prepare+frpc verify 管）；
2. plan：打印 verify/install/uninstall/query 计划命令且零执行；install
   计划说明与 XML 安装方式一致（不展示不执行的 /SC ONSTART）；
3. install 纪律：缺确认短语拒绝（零 runner 调用）；短语正确但缺
   --execute → dry-run 零写入；任务名已存在（owned/foreign/malformed）
   一律拒绝覆盖；短语+execute+缺失 → 经临时 XML 创建 + **装后只读验证**
   （query 被篡改 → 验证失败报告差异；query 正确 → 通过）；
4. XML 模板不变量：BootTrigger（+延迟）/ RestartOnFailure / RunLevel=
   LeastPrivilege / LogonType=S4U / 绝对路径入 Command+Arguments；
5. status：missing/installed/foreign/malformed（DOCTYPE 实体防护）分类；
6. uninstall 纪律：foreign 绝不删除；owned 缺 --execute 只打印计划；
   owned+execute 只删精确任务名；
7. 输出纪律：stdout/stderr 不含 token 文件内容；CLI 用法错误 exit 2。

全部经注入 Fake runner——绝不触碰真实 schtasks；零网络零服务操作。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "ops"))

import frpc_windows_controller as ctrl
import public_edge_prepare as prepare

SECRET_CONTENT = "s" * 48 + "-ctrl-test-not-secret"


def _render_config(tmp_path: Path) -> Path:
    secrets = tmp_path / "secrets"
    secrets.mkdir(exist_ok=True)
    files = {"frps_token": "frps_token.txt", "turn_secret": "turn.secret",
             "livekit_keys": "livekit_keys", "frpc_token": "frpc_token.txt"}
    for name in files.values():
        path = secrets / name
        path.write_text(SECRET_CONTENT, encoding="utf-8")
        path.chmod(0o600)
    manifest = {
        "schema": "aios-public-edge-prepare/1", "environment": "hk-beta",
        "acknowledge_real_inputs": True, "vps_public_ip": "8.8.8.8",
        "origins": {"app": "https://app.acme-public.org", "api": "https://api.acme-public.org",
                    "livekit": "https://livekit.acme-public.org",
                    "download": "https://download.acme-public.org"},
        "turn_host": "turn.acme-public.org", "contact_email": "ops@acme-public.org",
        "home": {"web_port": 3011, "api_port": 8000},
        "output_dir": str(tmp_path / "pkg"), "vps_secrets_dir": "/opt/aios-edge/secrets",
        "local_secret_files": {k: str(secrets / v) for k, v in files.items()},
    }
    prepare.render_to_directory(prepare.validate_manifest(manifest))
    return tmp_path / "pkg" / "frpc.windows.toml"


def _fake_exe(tmp_path: Path, name: str = "frpc.exe") -> Path:
    exe = tmp_path / name
    exe.write_bytes(b"MZ-fake-frpc-binary")
    return exe


class FakeRunner:
    """注入 Runner：记录命令；按预设脚本应答（缺省查询=不存在，写=成功）。"""

    def __init__(self, *, query_result: tuple[int, str] | None = None,
                 post_create_query: tuple[int, str] | None = None) -> None:
        self.commands: list[list[str]] = []
        self.query_result = query_result or (1, "ERROR: The system cannot find the file specified.")
        self.post_create_query = post_create_query  # 装后验证用的第二次查询应答
        self._created = False

    def run(self, args: list[str]) -> tuple[int, str]:
        self.commands.append(list(args))
        if args[:3] == ["schtasks", "/Query", "/TN"]:
            if self._created and self.post_create_query is not None:
                return self.post_create_query
            return self.query_result
        if args[1] == "/Create":
            self._created = True
        return 0, "SUCCESS"


def _owned_xml(exe: str = "C:/x/frpc.exe", config: str = "C:/c.toml") -> str:
    return ctrl.INSTALL_XML_TEMPLATE.replace("__FRPC_EXE__", exe).replace(
        "__FRPC_CONFIG__", config)


# ---------------------------------------------------------------- preflight


def test_preflight_pass_reports_facts(tmp_path: Path) -> None:
    facts = ctrl.preflight_checks(_fake_exe(tmp_path), _render_config(tmp_path))
    assert facts["server_addr"] == "8.8.8.8"
    assert facts["proxies"] == ["aios-web", "aios-api"]
    assert facts["token_file_present"] is True
    assert Path(facts["frpc_exe"]).is_absolute()
    assert Path(facts["config"]).is_absolute()


def test_preflight_rejects_relative_exe(tmp_path: Path) -> None:
    config = _render_config(tmp_path)
    relative = Path("relative/frpc.exe")
    with pytest.raises(ctrl.ControllerError, match="绝对路径"):
        ctrl.preflight_checks(relative, config)


def test_preflight_rejects_relative_config(tmp_path: Path) -> None:
    exe = _fake_exe(tmp_path)
    _render_config(tmp_path)  # 渲染产物落地（供相对路径指向）
    import os

    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with pytest.raises(ctrl.ControllerError, match="绝对路径"):
            ctrl.preflight_checks(exe, Path("pkg/frpc.windows.toml"))
    finally:
        os.chdir(original_cwd)


def test_preflight_rejects_wrong_exe_name(tmp_path: Path) -> None:
    exe = _fake_exe(tmp_path, "not-frpc.exe")
    with pytest.raises(ctrl.ControllerError, match="frpc.exe"):
        ctrl.preflight_checks(exe, _render_config(tmp_path))


def test_preflight_rejects_missing_exe(tmp_path: Path) -> None:
    # 名字合规但文件缺席——命中的是常规文件判定而非命名判定
    with pytest.raises(ctrl.ControllerError, match="常规文件"):
        ctrl.preflight_checks(tmp_path / "frpc.exe", _render_config(tmp_path))


@pytest.mark.parametrize(
    ("rewrite", "keyword"),
    [
        ('serverAddr = "8.8.8.8"', 'serverAddr = "<VPS 公网 IPv4>"'),
        ('serverAddr = "8.8.8.8"', 'serverAddr = "192.168.0.5"'),
        ("transport.tls.enable = true", "transport.tls.enable = false"),
        ('localIP = "127.0.0.1"', 'localIP = "0.0.0.0"'),
        ('customDomains = ["app.acme-public.org"]', 'customDomains = ["app.example.com"]'),
    ],
)
def test_preflight_rejects_config_invariants(tmp_path: Path, rewrite: str, keyword: str) -> None:
    config = _render_config(tmp_path)
    config.write_text(config.read_text(encoding="utf-8").replace(rewrite, keyword), encoding="utf-8")
    with pytest.raises(ctrl.ControllerError):
        ctrl.preflight_checks(_fake_exe(tmp_path), config)


def test_preflight_rejects_inline_token(tmp_path: Path) -> None:
    config = _render_config(tmp_path)
    text = config.read_text(encoding="utf-8")
    text = text.replace('auth.tokenSource.type = "file"',
                        'auth.token = "inline"\nauth.tokenSource.type = "file"')
    config.write_text(text, encoding="utf-8")
    with pytest.raises(ctrl.ControllerError, match="inline"):
        ctrl.preflight_checks(_fake_exe(tmp_path), config)


# ---------------------------------------------------------------- token 元数据校验（Round 1 缺口 1）


def test_token_file_invalid_utf8_content_still_passes(tmp_path: Path) -> None:
    """Round 1：token 内容**不读取**——非 UTF-8 文件 preflight 仍通过。"""
    config = _render_config(tmp_path)
    import tomllib

    model = tomllib.loads(config.read_text(encoding="utf-8"))
    token_path = Path(model["auth"]["tokenSource"]["file"]["path"])
    token_path.write_bytes(b"\xff\xfeRAW-TOKEN-BYTES\x80")
    token_path.chmod(0o600)
    facts = ctrl.preflight_checks(_fake_exe(tmp_path), config)  # 不抛即通过
    assert facts["token_file_present"] is True


def test_token_file_very_short_content_still_passes(tmp_path: Path) -> None:
    """Round 1：token 内容**不读取**——极短内容 preflight 仍通过（弱值归
    prepare 渲染时担保 + plan 的 frpc.exe verify 运行期复核）。"""
    config = _render_config(tmp_path)
    import tomllib

    model = tomllib.loads(config.read_text(encoding="utf-8"))
    token_path = Path(model["auth"]["tokenSource"]["file"]["path"])
    token_path.write_text("x", encoding="utf-8")
    token_path.chmod(0o600)
    facts = ctrl.preflight_checks(_fake_exe(tmp_path), config)
    assert facts["token_file_present"] is True


def test_token_file_missing_rejected(tmp_path: Path) -> None:
    config = _render_config(tmp_path)
    import tomllib

    model = tomllib.loads(config.read_text(encoding="utf-8"))
    Path(model["auth"]["tokenSource"]["file"]["path"]).unlink()
    with pytest.raises(ctrl.ControllerError, match="元数据校验"):
        ctrl.preflight_checks(_fake_exe(tmp_path), config)


def test_token_file_symlink_rejected(tmp_path: Path) -> None:
    config = _render_config(tmp_path)
    import tomllib

    model = tomllib.loads(config.read_text(encoding="utf-8"))
    token_path = Path(model["auth"]["tokenSource"]["file"]["path"])
    real = token_path.parent / "real-token.txt"
    real.write_text(SECRET_CONTENT, encoding="utf-8")
    real.chmod(0o600)
    token_path.unlink()
    try:
        token_path.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("本机无法创建符号链接（权限）")
    with pytest.raises(ctrl.ControllerError, match="元数据校验"):
        ctrl.preflight_checks(_fake_exe(tmp_path), config)


# ---------------------------------------------------------------- plan / install


def test_plan_prints_planned_commands_without_running(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = ctrl.main(["plan", "--frpc-exe", str(_fake_exe(tmp_path)),
                      "--config", str(_render_config(tmp_path))])
    assert code == ctrl.EXIT_OK
    out = capsys.readouterr().out
    assert 'verify -c' in out and "/Create" in out and "/Delete" in out
    assert "ONSTART" not in out, "计划说明不得展示不执行的 /SC ONSTART（与 XML 安装不一致）"
    assert SECRET_CONTENT not in out


def test_install_without_phrase_refuses_and_runs_nothing(tmp_path: Path) -> None:
    runner = FakeRunner()
    code = ctrl.main(["install", "--frpc-exe", str(_fake_exe(tmp_path)),
                      "--config", str(_render_config(tmp_path))], runner=runner)
    assert code == ctrl.EXIT_FAILURE
    assert runner.commands == []


def test_install_with_phrase_but_no_execute_is_dry_run(tmp_path: Path) -> None:
    runner = FakeRunner()
    code = ctrl.main(["install", "--frpc-exe", str(_fake_exe(tmp_path)),
                      "--config", str(_render_config(tmp_path)),
                      "--confirm-phrase", ctrl.CONFIRM_PHRASE], runner=runner)
    assert code == ctrl.EXIT_OK
    writes = [c for c in runner.commands if c[1] in ("/Create", "/Delete")]
    assert not writes
    assert runner.commands and runner.commands[0][1] == "/Query"


def test_install_with_execute_creates_and_verifies(tmp_path: Path) -> None:
    """装后验证通过路径：Create → 查询返回正确 XML → 验证通过。"""
    exe, config = _fake_exe(tmp_path), _render_config(tmp_path)
    facts = ctrl.preflight_checks(exe, config)
    correct_xml = ctrl._install_xml(facts)
    runner = FakeRunner(post_create_query=(0, correct_xml))
    code = ctrl.main(["install", "--frpc-exe", str(exe), "--config", str(config),
                      "--confirm-phrase", ctrl.CONFIRM_PHRASE, "--execute"], runner=runner)
    assert code == ctrl.EXIT_OK
    creates = [c for c in runner.commands if c[1] == "/Create"]
    assert len(creates) == 1 and creates[0][3] == ctrl.TASK_NAME
    assert creates[0][4] == "/XML"


def test_install_with_execute_tampered_query_reports_failure(tmp_path: Path) -> None:
    """Round 1 缺口 3：装后验证失败路径——Create 返回 0 但查询返回被篡改的
    XML（Command 改为其他值）→ 验证失败，不做自动删除。"""
    exe, config = _fake_exe(tmp_path), _render_config(tmp_path)
    facts = ctrl.preflight_checks(exe, config)
    tampered_xml = ctrl._install_xml(facts).replace(
        facts["frpc_exe"], "C:/malicious/replaced.exe")
    runner = FakeRunner(post_create_query=(0, tampered_xml))
    code = ctrl.main(["install", "--frpc-exe", str(exe), "--config", str(config),
                      "--confirm-phrase", ctrl.CONFIRM_PHRASE, "--execute"], runner=runner)
    assert code == ctrl.EXIT_FAILURE, "装后验证失败必须非零退出"
    deletes = [c for c in runner.commands if c[1] == "/Delete"]
    assert not deletes, "验证失败不做自动删除（留 supervisor 处置）"


def test_install_with_execute_query_missing_after_create(tmp_path: Path) -> None:
    """装后查询返回不存在 → 验证失败。"""
    exe, config = _fake_exe(tmp_path), _render_config(tmp_path)
    runner = FakeRunner(post_create_query=(1, "ERROR: cannot find"))
    code = ctrl.main(["install", "--frpc-exe", str(exe), "--config", str(config),
                      "--confirm-phrase", ctrl.CONFIRM_PHRASE, "--execute"], runner=runner)
    assert code == ctrl.EXIT_FAILURE


@pytest.mark.parametrize("query", [
    (0, _owned_xml()),
    (0, _owned_xml().replace(ctrl.TASK_URI_MARKER, "someone-else")),
    (0, "<?xml version='1.0'?><broken>"),
])
def test_install_never_overwrites_existing_task(tmp_path: Path, query: tuple[int, str]) -> None:
    runner = FakeRunner(query_result=query)
    code = ctrl.main(["install", "--frpc-exe", str(_fake_exe(tmp_path)),
                      "--config", str(_render_config(tmp_path)),
                      "--confirm-phrase", ctrl.CONFIRM_PHRASE, "--execute"], runner=runner)
    assert code == ctrl.EXIT_FAILURE
    writes = [c for c in runner.commands if c[1] in ("/Create", "/Delete")]
    assert not writes


# ---------------------------------------------------------------- XML 模板不变量（Round 1 缺口 2 + Round 2）


def test_install_xml_template_contains_boot_restart_s4u_leastprivilege() -> None:
    xml = ctrl.INSTALL_XML_TEMPLATE
    assert "<BootTrigger>" in xml, "必须 BootTrigger（开机启动，非 LogonTrigger）"
    assert "<Delay>PT30S</Delay>" in xml, "BootTrigger 需短延迟（等网络栈就绪）"
    assert "<RestartOnFailure>" in xml and "<Count>3</Count>" in xml, "失败自动重启 ×3"
    assert "<LogonType>S4U</LogonType>" in xml, "S4U（无需存储密码）"
    assert "<RunLevel>LeastPrivilege</RunLevel>" in xml, "最小权限"
    assert "<LogonTrigger>" not in xml, "不得保留 LogonTrigger（改为 BootTrigger）"
    assert "/SC ONSTART" not in xml, "XML 安装不走 /SC 参数"


def test_install_xml_no_unresolved_placeholders_and_single_principal(tmp_path: Path) -> None:
    """Round 2：生成后 XML 无未解析占位符 + 恰一个 S4U/LeastPrivilege Principal
    （无 UserId——Task Scheduler 默认解析为注册用户，跨平台安全）。"""
    exe, config = _fake_exe(tmp_path), _render_config(tmp_path)
    facts = ctrl.preflight_checks(exe, config)
    generated = ctrl._install_xml(facts)  # 生成后的 XML（占位符已全部替换）
    for placeholder in ("__FRPC_EXE__", "__FRPC_CONFIG__", "__USER_SID__", "__"):
        assert placeholder not in generated, f"生成 XML 残留未解析占位符: {placeholder}"
    # 模板结构检查：不写 UserId 元素（Task Scheduler 默认=注册用户）
    assert "__USER_SID__" not in ctrl.INSTALL_XML_TEMPLATE, "模板不得含 UserId 占位符"
    import re as _re

    principals = _re.findall(r"<Principal\b[^>]*>.*?</Principal>", generated, _re.DOTALL)
    assert len(principals) == 1, "恰一个 Principal"
    assert "<LogonType>S4U</LogonType>" in principals[0]
    assert "<RunLevel>LeastPrivilege</RunLevel>" in principals[0]
    assert "<UserId>" not in principals[0], "不写 UserId（默认=注册用户）"


def test_install_xml_resolves_absolute_paths_and_quotes_config(tmp_path: Path) -> None:
    """Round 2：config 路径含空格时 Arguments 必须 `-c "<绝对路径>"` 引号包裹。"""
    exe, config = _fake_exe(tmp_path), _render_config(tmp_path)
    facts = ctrl.preflight_checks(exe, config)
    xml = ctrl._install_xml(facts)
    assert Path(facts["frpc_exe"]).is_absolute()
    assert Path(facts["config"]).is_absolute()
    assert facts["frpc_exe"] in xml
    # Arguments 行 = `-c "<config>"`（双引号包裹）
    assert f'-c "{facts["config"]}"' in xml, "config 路径必须双引号包裹（空格安全）"
    # 即使 config 路径不含空格，也验证引用形态（防模板回归）
    assert 'Arguments>-c "' in xml


def test_install_xml_with_spaced_config_path(tmp_path: Path) -> None:
    """Round 2：空格路径回归——渲染目录路径含空格时，XML 中 config 以
    引号形式出现且验证通过。"""
    spaced_dir = tmp_path / "AI Learning OS spaced"
    spaced_dir.mkdir()
    exe = spaced_dir / "frpc.exe"
    exe.write_bytes(b"MZ-fake-frpc-binary")
    # 构造含空格的 config 路径（复用渲染产物但放到空格目录）
    config = _render_config(tmp_path)
    spaced_config = spaced_dir / "frpc.windows.toml"
    spaced_config.write_text(config.read_text(encoding="utf-8"), encoding="utf-8")
    # 替换 token 路径引用（渲染产物里的 token 路径仍指向原 tmp，无需改）
    facts = ctrl.preflight_checks(exe, spaced_config)
    xml = ctrl._install_xml(facts)
    assert f'-c "{facts["config"]}' in xml and " spaced" in facts["config"]
    # 装后验证预期 Arguments 也是带引号形式
    ok, discrepancy = ctrl._verify_installed_task(
        _OwnedQueryRunner(xml), facts)
    assert ok, f"空格路径的装后验证应通过: {discrepancy}"


class _OwnedQueryRunner:
    """辅助：单次查询返回给定 XML。"""

    def __init__(self, xml: str) -> None:
        self.xml = xml

    def run(self, args: list[str]) -> tuple[int, str]:
        return (0, self.xml) if args[1] == "/Query" else (0, "SUCCESS")


# ---------------------------------------------------------------- status / uninstall


def test_status_classification() -> None:
    assert ctrl.main(["status"], runner=FakeRunner()) == ctrl.STATUS_MISSING
    assert ctrl.main(["status"], runner=FakeRunner(query_result=(0, _owned_xml()))) == ctrl.STATUS_INSTALLED
    foreign = FakeRunner(query_result=(0, _owned_xml().replace(ctrl.TASK_URI_MARKER, "other")))
    assert ctrl.main(["status"], runner=foreign) == ctrl.STATUS_FOREIGN
    doctype = FakeRunner(query_result=(
        0, "<?xml version='1.0'?><!DOCTYPE Task [<!ENTITY x 'y'>]><Task/>"))
    assert ctrl.main(["status"], runner=doctype) == ctrl.STATUS_MALFORMED


def test_uninstall_foreign_refused_without_delete() -> None:
    runner = FakeRunner(query_result=(0, _owned_xml().replace(ctrl.TASK_URI_MARKER, "other")))
    code = ctrl.main(["uninstall", "--confirm-phrase", ctrl.CONFIRM_PHRASE,
                      "--execute"], runner=runner)
    assert code == ctrl.EXIT_FAILURE
    assert not [c for c in runner.commands if c[1] == "/Delete"]


def test_uninstall_owned_dry_run_then_execute() -> None:
    owned_xml = (0, _owned_xml())
    dry = FakeRunner(query_result=owned_xml)
    assert ctrl.main(["uninstall", "--confirm-phrase", ctrl.CONFIRM_PHRASE], runner=dry) == ctrl.EXIT_OK
    assert not [c for c in dry.commands if c[1] == "/Delete"]
    real = FakeRunner(query_result=owned_xml)
    assert ctrl.main(["uninstall", "--confirm-phrase", ctrl.CONFIRM_PHRASE,
                      "--execute"], runner=real) == ctrl.EXIT_OK
    deletes = [c for c in real.commands if c[1] == "/Delete"]
    assert deletes == [["schtasks", "/Delete", "/TN", ctrl.TASK_NAME, "/F"]]


def test_uninstall_missing_is_idempotent() -> None:
    assert ctrl.main(["uninstall", "--confirm-phrase", ctrl.CONFIRM_PHRASE,
                      "--execute"], runner=FakeRunner()) == ctrl.EXIT_OK


# ---------------------------------------------------------------- CLI


def test_cli_requires_exe_and_config() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "frpc_windows_controller", "preflight"],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        cwd=str(REPO / "tools" / "ops"), check=False,
    )
    assert result.returncode == 2
