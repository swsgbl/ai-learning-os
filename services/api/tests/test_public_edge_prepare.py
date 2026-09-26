"""M14-154 边缘部署准备/渲染（tools/ops/public_edge_prepare.py）fail-closed 契约测试。

覆盖矩阵（任务书第 4 条）：
1. manifest 校验：schema/环境枚举/ack/缺键/重复键/非 UTF-8（错误只含类别，
   不回显路径或字节）/超体积；
2. 脱敏：secret 文件错误无路径无字节；内联 secret 拒绝消息不含被拒值；
3. secret 文件处理：缺失（类别 only）/弱(<32)/非 UTF-8（无标记字节）/
   占位形态/目录而非文件/URL 引用；合法文件通过；
4. 端点校验：私网/回环/链路本地/保留域/query/path/userinfo/非 443 端口/
   重复 origin/IP 字面量 origin/畸形主机/TURN host-only/VPS IP
   （私网/RFC5737/CGNAT/IPv6 全拒）；邮箱/家机端口对（越界/相同）/输出目录
   （仓库内拒绝）/vps_secrets_dir（相对路径拒绝）；
5. 渲染确定性：同 manifest 两次渲染字节一致；同目录覆写幂等；
6. 输出遏制：产物只落显式仓库外目录、恰为五个文件；仓库零新增文件；
7. 无 secret 内容持久化：产物不含任一 secret 值（audit_rendered 对注入
   场景必须抛错）；.env 的 TURN secret 是显式回填标记不是值；
8. 示例 manifest 本身被校验拒绝（占位值），且文件内无 ≥32 hex 材料；
9. CLI：动作互斥必选（缺省 exit 2）；--check-only 零写入 exit 0；
   --render 写五文件；--dns-check 打桩解析不匹配 FAIL / 匹配 PASS（无真实 DNS）。
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "ops"))

import public_edge_prepare as prepare

EXAMPLE_MANIFEST = REPO / "tools" / "ops" / "public_edge_prepare.example.json"

VALID_ORIGINS = {
    "app": "https://app.acme-public.org",
    "api": "https://api.acme-public.org",
    "livekit": "https://livekit.acme-public.org",
    "download": "https://download.acme-public.org",
}
VALID_VPS_IP = "8.8.8.8"  # 公测 DNS 作为测试夹具（非真实运营 IP）
SECRET_CONTENT = "s" * 48 + "-not-a-real-secret"  # 测试值，仅驻 tmp/内存


def _write_secret(directory: Path, name: str, content: str = SECRET_CONTENT) -> Path:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def _valid_manifest(tmp_path: Path) -> dict:
    secrets = tmp_path / "secrets"
    secrets.mkdir(exist_ok=True)
    return {
        "schema": "aios-public-edge-prepare/1",
        "environment": "hk-beta",
        "acknowledge_real_inputs": True,
        "vps_public_ip": VALID_VPS_IP,
        "origins": dict(VALID_ORIGINS),
        "turn_host": "turn.acme-public.org",
        "contact_email": "ops@acme-public.org",
        "home": {"web_port": 3011, "api_port": 8000},
        "output_dir": str(tmp_path / "render-out"),
        "vps_secrets_dir": "/opt/aios-edge/secrets",
        "local_secret_files": {
            "frps_token": str(_write_secret(secrets, "frps_token.txt")),
            "turn_secret": str(_write_secret(secrets, "turn.secret")),
            "livekit_keys": str(_write_secret(secrets, "livekit_keys")),
            "frpc_token": str(_write_secret(secrets, "frpc_token.txt")),
        },
    }


def _validated(tmp_path: Path, **overrides) -> dict:
    manifest = _valid_manifest(tmp_path)
    manifest.update(overrides)
    return prepare.validate_manifest(manifest)


# ---------------------------------------------------------------- manifest 装载


def test_load_manifest_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text('{"schema": "a", "schema": "b"}', encoding="utf-8")
    with pytest.raises(prepare.PrepareError, match="重复键"):
        prepare.load_manifest(str(path))


def test_load_manifest_rejects_non_utf8_without_echo(tmp_path: Path) -> None:
    path = tmp_path / "m.bin"
    path.write_bytes(b"\xff\xfeMANIFEST-BYTES-MARKER")
    with pytest.raises(prepare.PrepareError) as excinfo:
        prepare.load_manifest(str(path))
    message = str(excinfo.value)
    assert "UnicodeDecodeError" in message
    assert str(path) not in message and "MANIFEST-BYTES-MARKER" not in message


def test_load_manifest_rejects_oversized(tmp_path: Path) -> None:
    path = tmp_path / "big.json"
    path.write_text('{"x": "' + "a" * (prepare.MANIFEST_MAX_BYTES + 10) + '"}', encoding="utf-8")
    with pytest.raises(prepare.PrepareError, match="上限"):
        prepare.load_manifest(str(path))


# ---------------------------------------------------------------- 顶层结构与确认


@pytest.mark.parametrize(
    ("overrides", "keyword"),
    [
        ({"schema": "wrong/1"}, "schema"),
        ({"environment": "us-production"}, "environment"),
        ({"environment": None}, "environment"),
        ({"acknowledge_real_inputs": False}, "acknowledge_real_inputs"),
        ({"acknowledge_real_inputs": "yes"}, "acknowledge_real_inputs"),
        ({"vps_public_ip": "192.168.1.5"}, "公网"),
        ({"vps_public_ip": "203.0.113.9"}, "公网"),  # RFC 5737
        ({"vps_public_ip": "100.64.0.1"}, "公网"),  # CGNAT
        ({"vps_public_ip": "::1"}, "IPv4"),
        ({"vps_public_ip": "not-an-ip"}, "非法"),
        ({"contact_email": "not-an-email"}, "邮箱"),
        ({"contact_email": "a @b.io"}, "邮箱"),
        ({"vps_secrets_dir": "relative/path"}, "绝对路径"),
        ({"vps_secrets_dir": "/has space/x"}, "绝对路径"),
    ],
)
def test_top_level_validation_rejects(tmp_path: Path, overrides: dict, keyword: str) -> None:
    manifest = _valid_manifest(tmp_path)
    manifest.update(overrides)
    with pytest.raises(prepare.PrepareError, match=keyword):
        prepare.validate_manifest(manifest)


def test_missing_required_keys_rejected(tmp_path: Path) -> None:
    manifest = _valid_manifest(tmp_path)
    del manifest["turn_host"]
    del manifest["output_dir"]
    with pytest.raises(prepare.PrepareError, match="缺必填键"):
        prepare.validate_manifest(manifest)


# ---------------------------------------------------------------- 内联 secret 拒绝


def test_inline_secret_key_rejected_without_value_echo(tmp_path: Path) -> None:
    manifest = _valid_manifest(tmp_path)
    manifest["api_token"] = "SUPERINLINESECRETVALUE"
    with pytest.raises(prepare.PrepareError) as excinfo:
        prepare.reject_inline_secrets(manifest)
    message = str(excinfo.value)
    assert "内联 secret" in message and "api_token" in message
    assert "SUPERINLINESECRETVALUE" not in message, "拒绝消息不得回显被拒值"


def test_high_entropy_value_rejected(tmp_path: Path) -> None:
    manifest = _valid_manifest(tmp_path)
    manifest["notes"] = "a" * 40 + "AAAA" * 12  # 40+ base64-ish 形态
    with pytest.raises(prepare.PrepareError, match="高熵"):
        prepare.reject_inline_secrets(manifest)


def test_secret_file_references_are_permitted(tmp_path: Path) -> None:
    """local_secret_files.* 是唯一允许的 secret 形态（路径引用）。"""
    manifest = _valid_manifest(tmp_path)
    prepare.reject_inline_secrets(manifest)  # 不抛即通过


# ---------------------------------------------------------------- origin/turn 校验


@pytest.mark.parametrize(
    "origin",
    [
        "http://app.acme-public.org",  # 非 https
        "https://app.acme-public.org:8443",  # 非 443（不安全端口组合）
        "https://user:p@app.acme-public.org",  # userinfo
        "https://app.acme-public.org/path",  # path
        "https://app.acme-public.org?q=1",  # query
        "https://127.0.0.1",  # 回环
        "https://192.168.0.10",  # 私网
        "https://169.254.1.1",  # 链路本地
        "https://app.example.com",  # 保留域
        "https://8.8.8.8",  # IP 字面量 origin（ACME 需域名）
        "https://app.acme_public.org",  # 非法字符（下划线）
        "not a url",
    ],
)
def test_origins_rejected(origin: str) -> None:
    with pytest.raises(prepare.PrepareError):
        prepare.validate_origins({"app": origin, "api": VALID_ORIGINS["api"],
                                  "livekit": VALID_ORIGINS["livekit"],
                                  "download": VALID_ORIGINS["download"]})


def test_duplicate_origin_hosts_rejected() -> None:
    duplicated = dict(VALID_ORIGINS)
    duplicated["api"] = VALID_ORIGINS["app"]  # 与 app 同主机
    with pytest.raises(prepare.PrepareError, match="重复"):
        prepare.validate_origins(duplicated)


def test_origins_valid_pass(tmp_path: Path) -> None:
    view = _validated(tmp_path)
    assert {key: view["endpoints"][key].host for key in prepare.ORIGIN_KEYS} == {
        "app": "app.acme-public.org",
        "api": "api.acme-public.org",
        "livekit": "livekit.acme-public.org",
        "download": "download.acme-public.org",
    }


@pytest.mark.parametrize(
    "turn",
    [
        "https://turn.acme-public.org",  # 带 scheme
        "turn.acme-public.org/path",
        "user@turn.acme-public.org",
        "turn.acme-public.org?q=1",
        "8.8.8.8",  # IP 字面量（证书 CN 需域名）
        "turn.example.com",  # 保留域
        "127.0.0.1",
        "",
    ],
)
def test_turn_host_rejected(turn: str) -> None:
    with pytest.raises(prepare.PrepareError):
        prepare.validate_turn_host(turn)


def test_turn_host_valid() -> None:
    assert prepare.validate_turn_host("turn.acme-public.org") == "turn.acme-public.org"


# ---------------------------------------------------------------- 家机端口/输出目录


@pytest.mark.parametrize(
    "home",
    [
        {"web_port": 3011, "api_port": 3011},  # 相同
        {"web_port": 80, "api_port": 8000},  # 特权端口
        {"web_port": 3011, "api_port": 70000},  # 越界
        {"web_port": "3011", "api_port": 8000},  # 非整数
        {"web_port": 3011},  # 缺键
        {"web_port": 3011, "api_port": 8000, "extra": 1},  # 多键
    ],
)
def test_home_ports_rejected(home: dict) -> None:
    with pytest.raises(prepare.PrepareError):
        prepare.validate_home_ports(home)


def test_output_dir_inside_repository_rejected() -> None:
    with pytest.raises(prepare.PrepareError, match="仓库内"):
        prepare.validate_output_dir(str(REPO / "out"))
    with pytest.raises(prepare.PrepareError, match="仓库内"):
        prepare.validate_output_dir(str(REPO / "infra" / "edge" / "out"))
    with pytest.raises(prepare.PrepareError, match="仓库内"):
        prepare.validate_output_dir(".")


def test_output_dir_outside_repository_accepted(tmp_path: Path) -> None:
    resolved = prepare.validate_output_dir(str(tmp_path / "out"))
    assert resolved == (tmp_path / "out").resolve()


# ---------------------------------------------------------------- secret 文件


def test_secret_file_missing_reports_class_only(tmp_path: Path) -> None:
    with pytest.raises(prepare.PrepareError) as excinfo:
        prepare.validate_secret_file(str(tmp_path / "no-such-secret.txt"), "frps_token")
    message = str(excinfo.value)
    assert "FileNotFoundError" in message or "不可读" in message
    assert "no-such-secret" not in message and str(tmp_path) not in message


def test_secret_file_binary_reports_class_only_no_bytes(tmp_path: Path) -> None:
    binary = tmp_path / "secret.bin"
    binary.write_bytes(b"\xff\xfeSECRET-BYTES-MARKER\x80")
    with pytest.raises(prepare.PrepareError) as excinfo:
        prepare.validate_secret_file(str(binary), "turn_secret")
    message = str(excinfo.value)
    assert "UnicodeDecodeError" in message
    assert "SECRET-BYTES-MARKER" not in message and str(binary) not in message


@pytest.mark.parametrize("content", ["short", "x" * 31, "<placeholder of a secret>", "change-me-value-1234567890"])
def test_weak_or_placeholder_secret_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "weak.txt"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(prepare.PrepareError):
        prepare.validate_secret_file(str(path), "livekit_keys")


def test_secret_file_directory_rejected(tmp_path: Path) -> None:
    with pytest.raises(prepare.PrepareError):
        prepare.validate_secret_file(str(tmp_path), "frpc_token")


def test_secret_file_url_reference_rejected() -> None:
    with pytest.raises(prepare.PrepareError, match="本地文件路径"):
        prepare.validate_secret_file("https://example.com/token.txt", "frps_token")


def test_valid_secret_file_passes(tmp_path: Path) -> None:
    path = _write_secret(tmp_path, "good.txt")
    assert prepare.validate_secret_file(str(path), "frps_token") == SECRET_CONTENT


# ---------------------------------------------------------------- 渲染：确定性/遏制/无 secret 持久化


def test_render_is_deterministic(tmp_path: Path) -> None:
    view = _validated(tmp_path)
    first = prepare.render_artifacts(view)
    second = prepare.render_artifacts(view)
    assert {name: content for name, (content, _m) in first.items()} == {
        name: content for name, (content, _m) in second.items()
    }
    assert set(first) == {"Caddyfile", "frps.toml", "frpc.windows.toml", "dot-env", "PREFLIGHT.md"}


def test_render_substitutes_real_values_without_secrets(tmp_path: Path) -> None:
    view = _validated(tmp_path)
    artifacts = prepare.render_artifacts(view)
    caddy = artifacts["Caddyfile"][0]
    assert "app.acme-public.org" in caddy and "download.acme-public.org" in caddy
    assert "app.example.com" not in caddy, "占位域必须全部被替换"
    assert "ops@acme-public.org" in caddy
    frpc = artifacts["frpc.windows.toml"][0]
    assert f'serverAddr = "{VALID_VPS_IP}"' in frpc
    env = artifacts["dot-env"][0]
    assert "AIOS_EDGE_COTURN_TURN_SECRET=" in env
    assert "<<<" in env, "TURN secret 必须是显式回填标记，绝不落值"
    preflight_doc = artifacts["PREFLIGHT.md"][0]
    assert "--turn-host turn.acme-public.org" in preflight_doc
    assert "mobile-4g5g-open" in preflight_doc  # 人工清单条目在场
    # 渲染产物不含任何 secret 值（audit_rendered 作为断言器复用）
    prepare.audit_rendered(artifacts, view["secret_values"])


def test_audit_rendered_raises_on_secret_injection() -> None:
    artifacts = {"Caddyfile": (f"leak: {SECRET_CONTENT}", 0o644)}
    with pytest.raises(prepare.PrepareError, match="泄漏 secret 内容"):
        prepare.audit_rendered(artifacts, {"frps_token": SECRET_CONTENT})
    with pytest.raises(prepare.PrepareError, match="高熵"):
        prepare.audit_rendered({"x": ("hex " + "f" * 40, 0o644)}, {})


def test_render_to_directory_containment_and_idempotence(tmp_path: Path) -> None:
    view = _validated(tmp_path)
    written = prepare.render_to_directory(view)
    assert written == ["Caddyfile", "frps.toml", "frpc.windows.toml", ".env", "PREFLIGHT.md"]
    out = view["output_dir"]
    assert {p.name for p in out.iterdir()} == set(written)
    # 同目录二次渲染（覆写）幂等：字节一致
    snapshot = {p.name: p.read_bytes() for p in out.iterdir()}
    prepare.render_to_directory(view)
    assert {p.name: p.read_bytes() for p in out.iterdir()} == snapshot
    # 输出目录在仓库外（遏制）
    try:
        out.resolve().relative_to(REPO)
        raised = False
    except ValueError:
        raised = True
    assert raised, "输出目录必须在仓库外"
    # 落盘产物同样不含 secret 值
    for path in out.iterdir():
        assert SECRET_CONTENT not in path.read_text(encoding="utf-8")


def test_rendered_repo_untouched(tmp_path: Path) -> None:
    """渲染只写输出目录：仓库 tracked 树零变化（git status 干净）。"""
    before = subprocess.run(
        ["git", "-C", str(REPO), "status", "--porcelain"],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=True,
    ).stdout
    view = _validated(tmp_path)
    prepare.render_to_directory(view)
    after = subprocess.run(
        ["git", "-C", str(REPO), "status", "--porcelain"],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=True,
    ).stdout
    assert before == after, "渲染不得改动仓库工作树"


# ---------------------------------------------------------------- 示例 manifest 契约


def test_example_manifest_is_rejected_by_validation() -> None:
    """示例是格式样板（占位值）：真实校验必须拒绝它（诚实边界）。"""
    manifest = json.loads(EXAMPLE_MANIFEST.read_text(encoding="utf-8"))
    with pytest.raises(prepare.PrepareError):
        prepare.validate_manifest(manifest)  # 占位域/RFC5737 IP/缺席 secret 文件必被拒


def test_example_manifest_has_no_secret_material() -> None:
    text = EXAMPLE_MANIFEST.read_text(encoding="utf-8")
    assert not re.findall(r"[0-9a-fA-F]{32,}", text), "示例 manifest 不得含 secret 形态材料"


# ---------------------------------------------------------------- CLI 行为


def _run_cli(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "public_edge_prepare", *argv],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        cwd=str(REPO / "tools" / "ops"), check=False,
    )


def test_cli_requires_action_flag(tmp_path: Path) -> None:
    result = _run_cli(["--manifest", str(EXAMPLE_MANIFEST)])
    assert result.returncode == 2  # argparse：--check-only/--render 必选互斥


def test_cli_check_only_zero_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_valid_manifest(tmp_path)), encoding="utf-8")
    marker = tmp_path / "render-out"
    code = prepare.main(["--manifest", str(manifest_path), "--check-only"])
    assert code == prepare.EXIT_OK
    assert not marker.exists(), "--check-only 绝不创建输出目录"


def test_cli_render_writes_five_artifacts(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_valid_manifest(tmp_path)), encoding="utf-8")
    code = prepare.main(["--manifest", str(manifest_path), "--render"])
    assert code == prepare.EXIT_OK
    out = tmp_path / "render-out"
    assert {p.name for p in out.iterdir()} == {
        "Caddyfile", "frps.toml", "frpc.windows.toml", ".env", "PREFLIGHT.md",
    }


def test_cli_invalid_manifest_exit_failure_no_leak(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manifest = _valid_manifest(tmp_path)
    manifest["local_secret_files"]["frps_token"] = str(tmp_path / "missing-secret.bin")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    code = prepare.main(["--manifest", str(manifest_path), "--check-only"])
    assert code == prepare.EXIT_FAILURE
    captured = capsys.readouterr()
    assert "missing-secret" not in captured.err + captured.out, "错误不得回显 secret 路径"
    assert not (tmp_path / "render-out").exists()


def test_dns_check_pass_and_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    view = _validated(tmp_path)

    def _fake_resolve(host: str, *_args: Any, **_kwargs: Any):
        address = VALID_VPS_IP if host.endswith("acme-public.org") else "203.0.113.1"
        return [(socket.AF_INET, None, None, "", (address, 0))]

    monkeypatch.setattr(prepare.socket, "getaddrinfo", _fake_resolve)
    results = prepare.check_dns(view)
    assert all(item["status"] == "pass" for item in results)

    def _fake_mismatch(host: str, *_args: Any, **_kwargs: Any):
        return [(socket.AF_INET, None, None, "", ("203.0.113.1", 0))]

    monkeypatch.setattr(prepare.socket, "getaddrinfo", _fake_mismatch)
    results = prepare.check_dns(view)
    assert results and all(item["status"] == "fail" for item in results)


# ---------------------------------------------------------------- Round 2 缺口回归


def test_top_level_low_entropy_inline_secret_rejected(tmp_path: Path) -> None:
    """Round 2 缺口 1：顶层 frps_token="低熵值"（与合法文件引用并存）必须被拒，
    消息含键名但绝不回显值。"""
    manifest = _valid_manifest(tmp_path)
    manifest["frps_token"] = "short-low-entropy-inline"
    with pytest.raises(prepare.PrepareError) as excinfo:
        prepare.validate_manifest(manifest)
    message = str(excinfo.value)
    assert "frps_token" in message and "内联 secret" in message
    assert "short-low-entropy-inline" not in message, "拒绝消息不得回显被拒值"


def test_nested_secret_like_key_rejected(tmp_path: Path) -> None:
    """作用域感知：local_secret_files 之外的嵌套 secret 形态键同样拒绝。"""
    manifest = _valid_manifest(tmp_path)
    manifest["deploy"] = {"api_key": "whatever"}
    with pytest.raises(prepare.PrepareError, match="deploy.api_key"):
        prepare.reject_inline_secrets(manifest)
    nested_home = _valid_manifest(tmp_path)
    nested_home["home"] = {**nested_home["home"], "api_key": "x"}
    with pytest.raises(prepare.PrepareError, match="home.api_key"):
        prepare.validate_manifest(nested_home)


def test_unknown_top_level_key_rejected(tmp_path: Path) -> None:
    """schema 白名单：未知顶层键拒绝；文档化可选 notes 放行。"""
    manifest = _valid_manifest(tmp_path)
    manifest["extra_field"] = 1
    with pytest.raises(prepare.PrepareError, match="未知顶层键"):
        prepare.validate_manifest(manifest)
    manifest = _valid_manifest(tmp_path)
    manifest["notes"] = "计划周四上线"
    prepare.validate_manifest(manifest)  # notes 是唯一可选白名单键——通过


def test_render_target_rejects_unexpected_entries(tmp_path: Path) -> None:
    view = _validated(tmp_path)
    out = tmp_path / "render-out"
    out.mkdir()
    (out / "operator-notes.txt").write_text("unrelated", encoding="utf-8")
    with pytest.raises(prepare.PrepareError, match="非渲染产物条目"):
        prepare.render_to_directory(view)


def _symlink_available(target: Path, link: Path) -> bool:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return False
    return True


def test_render_target_rejects_symlinked_directory(tmp_path: Path) -> None:
    view = _validated(tmp_path)
    real = tmp_path / "real-out"
    real.mkdir()
    link = tmp_path / "link-out"
    if not _symlink_available(real, link):
        pytest.skip("本机无法创建符号链接（权限）")
    symlinked_view = dict(view)
    symlinked_view["output_dir"] = link
    with pytest.raises(prepare.PrepareError, match="符号链接"):
        prepare.render_to_directory(symlinked_view)


def test_render_target_rejects_symlinked_artifact(tmp_path: Path) -> None:
    view = _validated(tmp_path)
    out = tmp_path / "render-out"
    out.mkdir()
    victim = tmp_path / "elsewhere.txt"
    victim.write_text("x", encoding="utf-8")
    link = out / ".env"
    if not _symlink_available(victim, link):
        pytest.skip("本机无法创建符号链接（权限）")
    with pytest.raises(prepare.PrepareError, match="符号链接"):
        prepare.render_to_directory(view)


def test_output_dir_rejects_filesystem_root(tmp_path: Path) -> None:
    root = str(Path(tmp_path.anchor))  # 例如 C:\
    with pytest.raises(prepare.PrepareError, match="文件系统根"):
        prepare.validate_output_dir(root)
    with pytest.raises(prepare.PrepareError, match="文件系统根"):
        prepare.validate_render_target(Path(root))
    with pytest.raises(prepare.PrepareError, match="仓库"):
        prepare.validate_render_target(prepare.REPO_ROOT)


def test_local_secret_path_injection_rejected(tmp_path: Path) -> None:
    """Round 2 缺口 3：引号/$/#/反引号/控制字符/.. 的本地 secret 路径全部拒绝。

    字符集检查先于文件存在性——直接传字符串即可（Windows 也不允许建含
    引号的文件名）。合法路径仍通过。
    """
    for bad in (
        'D:/secrets/bad"quote.txt',
        "D:/secrets/bad$dollar.txt",
        "D:/secrets/bad#hash.txt",
        "D:/secrets/bad`tick.txt",
        "D:/secrets/bad%percent.txt",
        "D:/secrets/bad;semi.txt",
    ):
        with pytest.raises(prepare.PrepareError, match="不安全字符"):
            prepare.validate_secret_file(bad, "frps_token")
    with pytest.raises(prepare.PrepareError, match=r"\.\."):
        prepare.validate_secret_file("D:/secrets/../good.txt", "frps_token")
    good = _write_secret(tmp_path, "good.txt")
    assert prepare.validate_secret_file(str(good), "frps_token") == SECRET_CONTENT


@pytest.mark.parametrize("bad_dir", ["/opt/x#y", "/opt/$HOME/x", "/opt/../etc", "/opt/a b"])
def test_vps_secrets_dir_injection_rejected(bad_dir: str) -> None:
    with pytest.raises(prepare.PrepareError):
        prepare.validate_vps_secrets_dir(bad_dir)


def test_valid_render_produces_parseable_toml(tmp_path: Path) -> None:
    """合法路径渲染后 frpc.windows.toml 必须可被 tomllib 解析（自保障路径）。"""
    import tomllib

    artifacts = prepare.render_artifacts(_validated(tmp_path))
    model = tomllib.loads(artifacts["frpc.windows.toml"][0])
    assert model["serverAddr"] == VALID_VPS_IP
    assert model["auth"]["tokenSource"]["file"]["path"].endswith("frpc_token.txt")
    assert [p["customDomains"] for p in model["proxies"]] == [
        ["app.acme-public.org"], ["api.acme-public.org"],
    ]


def test_audit_rendered_catches_base64_like_leak() -> None:
    """Round 2 缺口 4：40+ base64 形态泄漏（精确匹配兜不住）必须被高熵策略拦下。

    夹具刻意含 g-z 字母：不构成 32+ hex 连跑（hex 规则测不到），只能由
    base64 形态规则兜住——正是本缺口要证明的路径。
    """
    base64_like = "Zg9Zh4Jk6lM8nOpQrStUv1Wx3Yy5Za7bc9de0fg2hi4"  # 42 字符 base64 形态
    assert not re.search(r"[0-9a-fA-F]{32,}", base64_like)  # 自证：非 hex 形态
    artifacts = {"Caddyfile": (f"header X-Leak {base64_like}", 0o644)}
    with pytest.raises(prepare.PrepareError, match="高熵"):
        prepare.audit_rendered(artifacts, {"frps_token": "completely-different-value"})


def test_secret_file_upper_size_boundary(tmp_path: Path) -> None:
    """Round 2 紧固：恰好 SECRET_MAX_BYTES 通过；超 1 字节显式拒绝（无静默截断）。"""
    exact = tmp_path / "exact.txt"
    exact.write_text("a" * prepare.SECRET_MAX_BYTES, encoding="utf-8")
    assert prepare.validate_secret_file(str(exact), "turn_secret") == "a" * prepare.SECRET_MAX_BYTES
    over = tmp_path / "over.txt"
    over.write_text("a" * (prepare.SECRET_MAX_BYTES + 1), encoding="utf-8")
    with pytest.raises(prepare.PrepareError, match="上限"):
        prepare.validate_secret_file(str(over), "turn_secret")
