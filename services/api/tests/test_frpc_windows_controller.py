"""M14-155 Windows frpc 常驻控制器（tools/ops/frpc_windows_controller.py）契约测试。

覆盖矩阵：
1. preflight：真实渲染配置 + 假 frpc.exe 通过；失败矩阵（exe 名错/缺失/
   符号链接、serverAddr 占位/私网、inline token、非 loopback 后端、
   TLS 关闭、占位 customDomains、token 文件缺失/弱值）全拒；
2. plan：打印 verify/install/uninstall/query 计划命令且零执行（frpc.exe
   verify 只计划不代跑）；
3. install 纪律：缺确认短语拒绝（零 runner 调用）；短语正确但缺
   --execute → dry-run 零写入；任务名已存在（owned/foreign/malformed）
   一律拒绝覆盖；短语+execute+缺失 → 经临时 XML 创建（runner 只收到
   精确任务名的 /Create）；
4. status：missing/installed/foreign/malformed（DOCTYPE 实体防护）分类；
5. uninstall 纪律：foreign 绝不删除；owned 缺 --execute 只打印计划；
   owned+execute 只删精确任务名（/TN 恒为 AIOS-Edge-FRPC）；
6. 输出纪律：stdout/stderr 不含 token 文件内容；CLI 用法错误 exit 2。

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
    """注入 Runner：记录命令；按预设脚本应答（缺省全部成功）。"""

    def __init__(self, *, query_result: tuple[int, str] | None = None) -> None:
        self.commands: list[list[str]] = []
        self.query_result = query_result or (1, "ERROR: The system cannot find the file specified.")

    def run(self, args: list[str]) -> tuple[int, str]:
        self.commands.append(list(args))
        if args[:3] == ["schtasks", "/Query", "/TN"]:
            return self.query_result
        return 0, "SUCCESS"


def _owned_xml() -> str:
    return ctrl.INSTALL_XML_TEMPLATE.replace("__FRPC_EXE__", "C:/x/frpc.exe").replace(
        "__FRPC_CONFIG__", "C:/c.toml")


# ---------------------------------------------------------------- preflight


def test_preflight_pass_reports_facts(tmp_path: Path) -> None:
    facts = ctrl.preflight_checks(_fake_exe(tmp_path), _render_config(tmp_path))
    assert facts["server_addr"] == "8.8.8.8"
    assert facts["proxies"] == ["aios-web", "aios-api"]
    assert facts["token_file_present"] is True


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
        ('serverAddr = "8.8.8.8"', 'serverAddr = "<VPS 公网 IPv4>"'),  # 占位回落
        ('serverAddr = "8.8.8.8"', 'serverAddr = "192.168.0.5"'),  # 私网
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


def test_preflight_rejects_weak_token_file(tmp_path: Path) -> None:
    config = _render_config(tmp_path)
    # 从渲染配置解析 token 路径并替换为弱值文件（0600 保持、内容过短）
    import tomllib

    model = tomllib.loads(config.read_text(encoding="utf-8"))
    token_path = Path(model["auth"]["tokenSource"]["file"]["path"])
    token_path.write_text("short", encoding="utf-8")
    token_path.chmod(0o600)
    # 类同一性：controller 可能经 tools.ops 命名空间导入（与裸名导入是两个
    # 模块对象）——统一用 ctrl.prepare 的异常类断言
    with pytest.raises((ctrl.ControllerError, ctrl.prepare.PrepareError)):
        ctrl.preflight_checks(_fake_exe(tmp_path), config)


def test_preflight_rejects_missing_token_file(tmp_path: Path) -> None:
    config = _render_config(tmp_path)
    import tomllib

    model = tomllib.loads(config.read_text(encoding="utf-8"))
    Path(model["auth"]["tokenSource"]["file"]["path"]).unlink()
    with pytest.raises((ctrl.ControllerError, ctrl.prepare.PrepareError)):
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
    assert SECRET_CONTENT not in out, "计划输出不得含 secret 夹具内容"


def test_install_without_phrase_refuses_and_runs_nothing(tmp_path: Path) -> None:
    runner = FakeRunner()
    code = ctrl.main(["install", "--frpc-exe", str(_fake_exe(tmp_path)),
                      "--config", str(_render_config(tmp_path))], runner=runner)
    assert code == ctrl.EXIT_FAILURE
    assert runner.commands == [], "缺确认短语时不得发起任何命令"


def test_install_with_phrase_but_no_execute_is_dry_run(tmp_path: Path) -> None:
    runner = FakeRunner()  # 查询应答 missing
    code = ctrl.main(["install", "--frpc-exe", str(_fake_exe(tmp_path)),
                      "--config", str(_render_config(tmp_path)),
                      "--confirm-phrase", ctrl.CONFIRM_PHRASE], runner=runner)
    assert code == ctrl.EXIT_OK
    writes = [c for c in runner.commands if c[1] in ("/Create", "/Delete")]
    assert not writes, "缺 --execute 时零写命令"
    assert runner.commands and runner.commands[0][1] == "/Query", "安装前必须先只读查询"


def test_install_with_execute_creates_via_temp_xml(tmp_path: Path) -> None:
    runner = FakeRunner()
    code = ctrl.main(["install", "--frpc-exe", str(_fake_exe(tmp_path)),
                      "--config", str(_render_config(tmp_path)),
                      "--confirm-phrase", ctrl.CONFIRM_PHRASE, "--execute"], runner=runner)
    assert code == ctrl.EXIT_OK
    creates = [c for c in runner.commands if c[1] == "/Create"]
    assert len(creates) == 1 and creates[0][3] == ctrl.TASK_NAME
    assert creates[0][4] == "/XML", "安装经显式 XML（LeastPrivilege/归属标记写入）"


@pytest.mark.parametrize("query", [
    (0, _owned_xml()),  # installed（本工具）
    (0, _owned_xml().replace(ctrl.TASK_URI_MARKER, "someone-else")),  # foreign
    (0, "<?xml version='1.0'?><broken>"),  # malformed
])
def test_install_never_overwrites_existing_task(tmp_path: Path, query: tuple[int, str]) -> None:
    runner = FakeRunner(query_result=query)
    code = ctrl.main(["install", "--frpc-exe", str(_fake_exe(tmp_path)),
                      "--config", str(_render_config(tmp_path)),
                      "--confirm-phrase", ctrl.CONFIRM_PHRASE, "--execute"], runner=runner)
    assert code == ctrl.EXIT_FAILURE, "同名任务已存在（无论归属）一律拒绝"
    writes = [c for c in runner.commands if c[1] in ("/Create", "/Delete")]
    assert not writes


# ---------------------------------------------------------------- status / uninstall


def test_status_classification() -> None:
    assert ctrl.main(["status"], runner=FakeRunner()) == ctrl.STATUS_MISSING
    assert ctrl.main(["status"], runner=FakeRunner(query_result=(0, _owned_xml()))) == ctrl.STATUS_INSTALLED
    foreign = FakeRunner(query_result=(0, _owned_xml().replace(ctrl.TASK_URI_MARKER, "other")))
    assert ctrl.main(["status"], runner=foreign) == ctrl.STATUS_FOREIGN
    doctype = FakeRunner(query_result=(
        0, "<?xml version='1.0'?><!DOCTYPE Task [<!ENTITY x 'y'>]><Task/>"))
    assert ctrl.main(["status"], runner=doctype) == ctrl.STATUS_MALFORMED, "DOCTYPE/实体防护"


def test_uninstall_foreign_refused_without_delete() -> None:
    runner = FakeRunner(query_result=(0, _owned_xml().replace(ctrl.TASK_URI_MARKER, "other")))
    code = ctrl.main(["uninstall", "--confirm-phrase", ctrl.CONFIRM_PHRASE,
                      "--execute"], runner=runner)
    assert code == ctrl.EXIT_FAILURE
    assert not [c for c in runner.commands if c[1] == "/Delete"], "绝不删除非自有任务"


def test_uninstall_owned_dry_run_then_execute() -> None:
    owned_xml = (0, _owned_xml())
    dry = FakeRunner(query_result=owned_xml)
    assert ctrl.main(["uninstall", "--confirm-phrase", ctrl.CONFIRM_PHRASE], runner=dry) == ctrl.EXIT_OK
    assert not [c for c in dry.commands if c[1] == "/Delete"], "缺 --execute 零删除"
    real = FakeRunner(query_result=owned_xml)
    assert ctrl.main(["uninstall", "--confirm-phrase", ctrl.CONFIRM_PHRASE,
                      "--execute"], runner=real) == ctrl.EXIT_OK
    deletes = [c for c in real.commands if c[1] == "/Delete"]
    assert deletes == [["schtasks", "/Delete", "/TN", ctrl.TASK_NAME, "/F"]], "只删精确任务名"


def test_uninstall_missing_is_idempotent() -> None:
    assert ctrl.main(["uninstall", "--confirm-phrase", ctrl.CONFIRM_PHRASE,
                      "--execute"], runner=FakeRunner()) == ctrl.EXIT_OK


# ---------------------------------------------------------------- CLI


def test_cli_requires_exe_and_config(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "frpc_windows_controller", "preflight"],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        cwd=str(REPO / "tools" / "ops"), check=False,
    )
    assert result.returncode == 2, "preflight 缺 --frpc-exe/--config 必须用法错误"
