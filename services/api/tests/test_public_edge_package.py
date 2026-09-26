"""M14-155 边缘部署操作包工具（tools/ops/public_edge_package.py）fail-closed 契约测试。

覆盖矩阵：
1. inspect：真实渲染目录通过并产出结构事实；目录边界（仓库内/符号链接/
   多余条目/缺件/非常规文件）与内容不变量（占位域回落、私网 IP、TURN
   secret 落值、frps/frpc 安全不变量、PREFLIGHT 清单完整性）全部拒绝；
2. seal：恰追加 SHA256SUMS 与 DEPLOYMENT_PACKAGE.md（共七件），五产物
   全覆盖；双目录渲染 seal 字节确定；
3. verify：哈希漂移/条目漂移/SHA256SUMS 未知名或重复条目/交接文档漂移
   全部 fail-closed；
4. 报告纪律：stdout 事实不含 secret 值（.env 落值场景在 inspect 即被拦）；
5. CLI：缺 --dir exit 2；inspect 成功 exit 0。

零网络零服务操作；secret 夹具仅驻 tmp/内存。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "ops"))

import public_edge_package as pkg
import public_edge_prepare as prepare

SECRET_CONTENT = "s" * 48 + "-pkg-test-not-secret"


def _render_package(tmp_path: Path, subdir: str = "pkg") -> Path:
    """经 prepare 真实渲染一个五产物目录（复用生产路径，零 mock）。"""
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
        "output_dir": str(tmp_path / subdir), "vps_secrets_dir": "/opt/aios-edge/secrets",
        "local_secret_files": {k: str(secrets / v) for k, v in files.items()},
    }
    prepare.render_to_directory(prepare.validate_manifest(manifest))
    return tmp_path / subdir


# ---------------------------------------------------------------- inspect


def test_inspect_ok_reports_structural_facts(tmp_path: Path) -> None:
    facts = pkg.cmd_inspect(_render_package(tmp_path))
    assert facts["status"] == "inspect-ok"
    assert facts["turn_host"] == "turn.acme-public.org"
    assert facts["frpc_server"] == "8.8.8.8"
    assert facts["caddy_hosts"] == sorted([
        "app.acme-public.org", "api.acme-public.org",
        "livekit.acme-public.org", "download.acme-public.org",
    ])


def test_inspect_rejects_unexpected_entry(tmp_path: Path) -> None:
    directory = _render_package(tmp_path)
    (directory / "EXTRA.txt").write_text("x", encoding="utf-8")
    with pytest.raises(pkg.PackageError, match="条目集不符"):
        pkg.cmd_inspect(directory)


def test_inspect_rejects_missing_artifact(tmp_path: Path) -> None:
    directory = _render_package(tmp_path)
    (directory / "PREFLIGHT.md").unlink()
    with pytest.raises(pkg.PackageError, match="条目集不符"):
        pkg.cmd_inspect(directory)


def test_inspect_rejects_symlinked_directory(tmp_path: Path) -> None:
    real = _render_package(tmp_path)
    link = tmp_path / "pkg-link"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("本机无法创建符号链接（权限）")
    with pytest.raises(pkg.PackageError, match="符号链接"):
        pkg.cmd_inspect(link)


def test_inspect_rejects_repository_internal_directory() -> None:
    with pytest.raises(pkg.PackageError, match="仓库内"):
        pkg._check_package_dir(REPO / "infra" / "edge", pkg.EXPECTED_RENDERED)


def test_inspect_rejects_placeholder_domain_fallback(tmp_path: Path) -> None:
    directory = _render_package(tmp_path)
    caddy = directory / "Caddyfile"
    caddy.write_text(
        caddy.read_text(encoding="utf-8").replace("app.acme-public.org", "app.example.com"),
        encoding="utf-8",
    )
    with pytest.raises(pkg.PackageError, match="占位"):
        pkg.cmd_inspect(directory)


def test_inspect_rejects_private_vps_ip(tmp_path: Path) -> None:
    directory = _render_package(tmp_path)
    env = directory / ".env"
    env.write_text(
        env.read_text(encoding="utf-8").replace("AIOS_EDGE_VPS_PUBLIC_IP=8.8.8.8",
                                                "AIOS_EDGE_VPS_PUBLIC_IP=192.168.1.9"),
        encoding="utf-8",
    )
    with pytest.raises(pkg.PackageError, match="公网 IPv4"):
        pkg.cmd_inspect(directory)


def test_inspect_rejects_secret_value_in_env(tmp_path: Path) -> None:
    directory = _render_package(tmp_path)
    env = directory / ".env"
    text = env.read_text(encoding="utf-8")
    text = re.sub(r"AIOS_EDGE_COTURN_TURN_SECRET=.*", "AIOS_EDGE_COTURN_TURN_SECRET=" + "f" * 48, text)
    env.write_text(text, encoding="utf-8")
    with pytest.raises(pkg.PackageError, match="回填标记|高熵"):
        pkg.cmd_inspect(directory)


def test_inspect_rejects_frpc_private_server(tmp_path: Path) -> None:
    directory = _render_package(tmp_path)
    frpc = directory / "frpc.windows.toml"
    frpc.write_text(
        frpc.read_text(encoding="utf-8").replace('serverAddr = "8.8.8.8"',
                                                 'serverAddr = "10.0.0.5"'),
        encoding="utf-8",
    )
    with pytest.raises(pkg.PackageError, match="公网 IPv4"):
        pkg.cmd_inspect(directory)


def test_inspect_rejects_frpc_inline_token(tmp_path: Path) -> None:
    directory = _render_package(tmp_path)
    frpc = directory / "frpc.windows.toml"
    text = frpc.read_text(encoding="utf-8")
    # TOML 点键合并进同一 auth 表——inline token 与 tokenSource 并存即违约
    text = text.replace('auth.tokenSource.type = "file"',
                        'auth.token = "inline-secret-value"\nauth.tokenSource.type = "file"')
    frpc.write_text(text, encoding="utf-8")
    with pytest.raises(pkg.PackageError, match="inline token"):
        pkg.cmd_inspect(directory)


# ---------------------------------------------------------------- seal / verify


def test_seal_adds_two_artifacts_and_verify_ok(tmp_path: Path) -> None:
    directory = _render_package(tmp_path)
    facts = pkg.cmd_seal(directory)
    assert facts["status"] == "sealed"
    assert {p.name for p in directory.iterdir()} == pkg.EXPECTED_SEALED
    sums = (directory / "SHA256SUMS").read_text(encoding="utf-8")
    for name in pkg.RENDER_ARTIFACTS:
        assert any(line.endswith(f"  {name}") for line in sums.splitlines())
    assert pkg.cmd_verify(directory)["status"] == "verify-ok"


def test_seal_is_deterministic_across_identical_renders(tmp_path: Path) -> None:
    first, second = _render_package(tmp_path, "pkg-a"), _render_package(tmp_path, "pkg-b")
    pkg.cmd_seal(first)
    pkg.cmd_seal(second)
    assert (first / "SHA256SUMS").read_bytes() == (second / "SHA256SUMS").read_bytes()
    assert (first / "DEPLOYMENT_PACKAGE.md").read_bytes() == (second / "DEPLOYMENT_PACKAGE.md").read_bytes()


def test_verify_fails_on_content_tamper(tmp_path: Path) -> None:
    directory = _render_package(tmp_path)
    pkg.cmd_seal(directory)
    (directory / ".env").write_text("tampered", encoding="utf-8")
    with pytest.raises(pkg.PackageError, match="哈希不匹配"):
        pkg.cmd_verify(directory)


def test_verify_fails_on_unexpected_post_seal_entry(tmp_path: Path) -> None:
    directory = _render_package(tmp_path)
    pkg.cmd_seal(directory)
    (directory / "notes.txt").write_text("x", encoding="utf-8")
    with pytest.raises(pkg.PackageError, match="条目集不符"):
        pkg.cmd_verify(directory)


def test_verify_fails_on_forged_sums_entry(tmp_path: Path) -> None:
    directory = _render_package(tmp_path)
    pkg.cmd_seal(directory)
    sums = directory / "SHA256SUMS"
    forged = "\n" + "0" * 64 + "  ../outside.txt\n"
    sums.write_text(sums.read_text(encoding="utf-8") + forged, encoding="utf-8")
    with pytest.raises(pkg.PackageError, match="未知名条目"):
        pkg.cmd_verify(directory)


def test_verify_fails_on_doc_drift(tmp_path: Path) -> None:
    directory = _render_package(tmp_path)
    pkg.cmd_seal(directory)
    doc = directory / "DEPLOYMENT_PACKAGE.md"
    doc.write_text(doc.read_text(encoding="utf-8") + "\n<!-- drifted -->\n", encoding="utf-8")
    with pytest.raises(pkg.PackageError, match="不一致"):
        pkg.cmd_verify(directory)


def test_seal_doc_states_honest_boundary_and_supervisor_framing(tmp_path: Path) -> None:
    directory = _render_package(tmp_path)
    pkg.cmd_seal(directory)
    doc = (directory / "DEPLOYMENT_PACKAGE.md").read_text(encoding="utf-8")
    for phrase in ("不含任何 secret 值", "supervisor", "没有发生任何真实部署",
                   "不得宣称公网生产上线", "sha256sum -c SHA256SUMS",
                   "frpc_windows_controller.py preflight"):
        assert phrase in doc, f"交接文档缺关键语句: {phrase}"
    assert SECRET_CONTENT not in doc, "交接文档不得含 secret 夹具内容"


# ---------------------------------------------------------------- CLI


def _run_cli(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "public_edge_package", *argv],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        cwd=str(REPO / "tools" / "ops"), check=False,
    )


def test_cli_requires_dir() -> None:
    assert _run_cli(["inspect"]).returncode == 2


def test_cli_inspect_exit_zero_without_secret_echo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = _render_package(tmp_path)
    code = pkg.main(["inspect", "--dir", str(directory)])
    assert code == pkg.EXIT_OK
    out = capsys.readouterr().out
    assert "turn.acme-public.org" in out and "inspect-ok" in out
    assert SECRET_CONTENT not in out, "CLI 输出不得含 secret 夹具内容"
