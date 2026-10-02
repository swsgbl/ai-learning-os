"""M14-153 公网 preflight（tools/ops/public_edge_preflight.py）fail-closed 契约测试。

覆盖矩阵（supervisor Round 1 反馈第 7 条）：
1. 公网主机拒绝：保留域（含子孙域 foo.example.com / foo.invalid 等——
   Round 1 第 1 条回归）、loopback/私网/链路本地/RFC 5737、非 https、
   userinfo、fragment 一律 PreflightError，不发任何请求；
2. 判定逻辑：安全响应头（HSTS 强度）、CORS（精确回显 + 凭据 + 拒 *）、
   Set-Cookie（Secure/SameSite=None/HttpOnly，值绝不进入结论）；
3. STUN：Allocate 请求构造（magic/txid）、应答解析（401 ERROR-CODE、
   magic/txid 不匹配拒绝、短包拒绝、TLV 对齐遍历）；
4. 人工签认：全项覆盖通过、缺项/未知项/缺签认人/非对象一律拒绝；
5. exit codes：无端点拒跑（argparse 2）；自动检查全过但人工清单未签认
   → 3；签认齐全 → 0；任一 FAIL → 1（run_checks 打桩，零网络）；
6. 源码契约：不内置默认端点（ast 常量扫描——带 :// 的字符串常量只允许
   出现在 argparse help 的占位示例里）、无硬编码 IP；
7. 本机回环行为面（不触外网）：直连 opener 对本机临时 HTTP 服务可达；
   tls_probe 对非 TLS 端口必须抛错（证书验证链路真实生效）；
8. M14-161 base path 契约：端点接受 origin 或 origin + 精确 `/aios`
   （url=canonical origin+base、origin 永不含 base——CORS 语义）；
   白名单外 path（尾斜杠 /aios/、深路径、dot segments、编码斜杠/
   反斜杠、双斜杠、任意前缀）一律拒绝且不回显原文；run_checks 探测
   ——app base 拓扑探测精确 canonical /aios（非 /）、api base 拓扑
   探测 /aios/health、登录走 /aios/api/v1/auth/login、CORS Origin 用
   endpoint origin（无 base）；根 origin 行为逐项不变（探测 /、/health）；
9. M14-162 证书 DN 格式化：ssl.getpeercert 的 subject/issuer 是三层
   嵌套（DN→RDN→(key,value)），旧 `"=".join(part)` 对真实形状抛
   TypeError（Codex 在 https://ndtool.cn/aios 实测崩溃）；新
   _format_cert_dn 按 RDN 集合→RDN→key/value 展示，覆盖单字段、
   多 RDN、多属性 RDN、空/缺失与畸形条目，TLS 验证语义不变。
"""
from __future__ import annotations

import ast
import json
import os
import re
import socket
import ssl
import struct
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Self

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "ops"))

import public_edge_preflight as preflight

PASS_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


# ---------------------------------------------------------------- 公网主机/端点拒绝


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        # Round 1 第 1 条回归：保留域的子孙域一律拒绝
        ("foo.example.com", False),
        ("app.example.com", False),
        ("deep.sub.example.com", False),
        ("example.com", False),
        ("foo.invalid", False),
        ("bar.test", False),
        ("qux.localhost", False),
        ("anything.example", False),
        # 回环/私网/保留段
        ("localhost", False),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("172.16.0.9", False),
        ("192.168.1.1", False),
        ("169.254.7.7", False),
        ("100.64.0.1", False),  # CGNAT
        ("192.0.2.1", False),  # RFC 5737 TEST-NET-1
        ("198.51.100.7", False),  # RFC 5737 TEST-NET-2
        ("203.0.113.9", False),  # RFC 5737 TEST-NET-3
        ("::1", False),
        ("fd00::1", False),
        ("", False),
        # 公网可通过（只做静态判定，不发请求）
        ("8.8.8.8", True),
        ("1.1.1.1", True),
        ("edge.acme-public.org", True),
        ("app.example.com.cn", True),  # 真实 cn 域名形态，非保留域
        ("notexample.com", True),  # 前缀相同但非保留域
    ],
)
def test_is_public_host(host: str, expected: bool) -> None:
    assert preflight.is_public_host(host) is expected


@pytest.mark.parametrize(
    "url",
    [
        "http://edge.acme-public.org",  # 非 https
        "https://user:pass@edge.acme-public.org",  # userinfo
        "https://edge.acme-public.org/#frag",  # fragment
        "https://edge.acme-public.org/callback/x",  # path（Round 4 origin-only）
        "https://edge.acme-public.org?token=SUPERSECRET",  # query（Round 4）
        "https://edge.acme-public.org/rtc?access_token=tok",  # path+query
        "https://app.example.com",  # 保留域
        "https://127.0.0.1:8443",  # loopback
        "https://192.168.1.5",  # 私网
        "https://edge.acme-public.org:0",  # 非法端口
        "",
        "   ",
    ],
)
def test_parse_public_https_url_rejects(url: str) -> None:
    with pytest.raises(preflight.PreflightError):
        preflight.parse_public_https_url(url)


def test_parse_public_https_url_accepts_public_origin_only() -> None:
    """Round 4：合法形态只有 origin（裸/尾斜杠），url=canonical 重构值。"""
    endpoint = preflight.parse_public_https_url("https://edge.acme-public.org")
    assert endpoint.host == "edge.acme-public.org"
    assert endpoint.port == 443
    assert endpoint.url == "https://edge.acme-public.org"
    assert endpoint.origin == "https://edge.acme-public.org"
    trailing = preflight.parse_public_https_url("https://edge.acme-public.org/")
    assert trailing.url == "https://edge.acme-public.org", "尾斜杠归一为 canonical origin"
    with_port = preflight.parse_public_https_url("https://edge.acme-public.org:8443")
    assert with_port.url == "https://edge.acme-public.org:8443"


# ------------------------------------------------- M14-161 受控 base path 契约


def test_parse_public_https_url_accepts_exact_aios_base_path() -> None:
    """M14-161：精确 `/aios` base path 受控放行；url=canonical origin+base，
    origin 永不含 base path（CORS 语义），base_path 字段携带归一值。"""
    endpoint = preflight.parse_public_https_url("https://edge.acme-public.org/aios")
    assert endpoint.base_path == "/aios"
    assert endpoint.host == "edge.acme-public.org"
    assert endpoint.url == "https://edge.acme-public.org/aios", "url 是 canonical 重构值"
    assert endpoint.origin == "https://edge.acme-public.org", "origin 不含 base path"
    with_port = preflight.parse_public_https_url("https://edge.acme-public.org:8443/aios")
    assert with_port.url == "https://edge.acme-public.org:8443/aios"
    assert with_port.origin == "https://edge.acme-public.org:8443"
    assert with_port.base_path == "/aios"


def test_parse_public_https_url_root_forms_have_empty_base_path() -> None:
    """M14-161：根 origin 两种形态（裸/尾斜杠）base_path 归一为空串。"""
    assert preflight.parse_public_https_url("https://edge.acme-public.org").base_path == ""
    assert preflight.parse_public_https_url("https://edge.acme-public.org/").base_path == ""


@pytest.mark.parametrize(
    "url",
    [
        "https://edge.acme-public.org/aios/",  # 尾斜杠（canonical 是无尾斜杠）
        "https://edge.acme-public.org/aios/login",  # 深路径
        "https://edge.acme-public.org/AIOS",  # 大小写敏感：/AIOS 非白名单
        "https://edge.acme-public.org//aios",  # 双斜杠
        "https://edge.acme-public.org/./aios",  # dot segment
        "https://edge.acme-public.org/aios/..",  # dot segment
        "https://edge.acme-public.org/../aios",  # dot segment（越顶）
        "https://edge.acme-public.org/%2e%2e",  # 编码 dot segment
        "https://edge.acme-public.org/aios%2fapi",  # 编码斜杠
        "https://edge.acme-public.org/aios%5c",  # 编码反斜杠
        "https://edge.acme-public.org/aios\\",  # 裸反斜杠
        "https://edge.acme-public.org/api",  # 任意其他前缀
        "https://edge.acme-public.org/aios%20",  # 编码空格
        "https://edge.acme-public.org/aios?q=1",  # base+query 组合（query 亦拒绝）
        "https://edge.acme-public.org/aios#f",  # base+fragment 组合
        "https://app.example.com/aios",  # 保留域 + 合法 base 仍整体拒绝
    ],
)
def test_parse_public_https_url_rejects_non_whitelisted_base_paths(url: str) -> None:
    """M14-161：白名单外 path 一律入口拒绝——绝不解码/归一化尝试后放行。"""
    with pytest.raises(preflight.PreflightError):
        preflight.parse_public_https_url(url)


def test_rejected_base_path_never_echoes_raw_path() -> None:
    """M14-161：base path 拒绝消息只含 canonical origin，绝不回显 path 原文。"""
    with pytest.raises(preflight.PreflightError) as excinfo:
        preflight.parse_public_https_url("https://edge.acme-public.org/SECRET-BASE/piece")
    message = str(excinfo.value)
    assert "SECRET-BASE" not in message and "piece" not in message
    assert "edge.acme-public.org" in message, "安全展示仍应包含 host 便于排障"


# ---------------------------------------------------------------- Round 4 泄漏回归


def test_rejected_url_secrets_never_appear_in_error_messages() -> None:
    """Round 4：userinfo 密码 / query token / path 绝不出现在异常文本。"""
    secret_pairs = (
        ("https://user:SuperSecretPass@edge.acme-public.org", "SuperSecretPass"),
        ("https://edge.acme-public.org?access_token=TOKEN-LEAK-CANDIDATE", "TOKEN-LEAK-CANDIDATE"),
        ("https://edge.acme-public.org/SECRET-PATH-SEGMENT", "SECRET-PATH-SEGMENT"),
    )
    for url, secret in secret_pairs:
        with pytest.raises(preflight.PreflightError) as excinfo:
            preflight.parse_public_https_url(url)
        message = str(excinfo.value)
        assert secret not in message, f"拒绝消息泄漏了输入敏感段: {message}"
        assert url not in message, f"拒绝消息回显了原始 URL: {message}"
        assert "edge.acme-public.org" in message, "安全展示仍应包含 host 便于排障"


def test_safe_endpoint_display_reconstructs_not_echoes() -> None:
    """集中脱敏助手：输出是 canonical 重构，绝不包含 userinfo/query/fragment。"""
    assert preflight._safe_endpoint_display("https://u:p@host.acme-public.org/x?k=v#f") == "https://host.acme-public.org"
    assert preflight._safe_endpoint_display("https://host.acme-public.org:8443/?q=1") == "https://host.acme-public.org:8443"
    assert preflight._safe_endpoint_display("") == "<invalid-endpoint>"
    assert preflight._safe_endpoint_display("::::") == "<invalid-endpoint>"


def test_network_error_never_contains_raw_url() -> None:
    """Round 4：网络错误只含 canonical origin+path + 错误类别，不含原始串。"""
    dead = preflight.Endpoint(url="http://127.0.0.1", host="127.0.0.1", port=1)  # 端口 1 必拒连
    with pytest.raises(preflight.PreflightError) as excinfo:
        preflight._http_request(dead, "/health", timeout=2.0)
    message = str(excinfo.value)
    assert "http://127.0.0.1/health" in message, "canonical origin+path 是安全展示应有部分"
    assert "?" not in message and "@" not in message, "错误消息不得含 query/userinfo 形态"


def test_main_report_persists_canonical_endpoints_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 4：报告 endpoints 只存 canonical 值（尾斜杠归一），不存原始参数。"""
    monkeypatch.setattr(
        preflight, "run_checks",
        lambda *args, **kwargs: [{"name": "app-https:x", "status": "pass", "detail": "ok"}],
    )
    report_path = tmp_path / "report.json"
    code = preflight.main([
        "--app-url", "https://edge.acme-public.org/",
        "--turn-host", "turn.acme-public.org.",
        "--output", str(report_path),
    ])
    assert code == preflight.EXIT_MANUAL_PENDING
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["endpoints"]["app_url"] == "https://edge.acme-public.org"
    assert report["endpoints"]["turn_host"] == "turn.acme-public.org"
    dumped = json.dumps(report)
    assert "edge.acme-public.org/" not in dumped, "报告不得保留原始（带斜杠）参数形态"


def test_main_rejects_credential_bearing_url_without_echo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Round 4：带凭据端点直接 FAIL，stderr/报告不落密码，也不产出报告。"""
    report_path = tmp_path / "report.json"
    code = preflight.main([
        "--app-url", "https://user:LeakedPassword@edge.acme-public.org",
        "--output", str(report_path),
    ])
    assert code == preflight.EXIT_FAILURE
    captured = capsys.readouterr()
    assert "LeakedPassword" not in captured.err + captured.out
    assert not report_path.exists(), "端点非法时不得产出报告"


def test_turn_host_is_host_only_public_value() -> None:
    """Round 4：TURN 主机 host-only——scheme/path/userinfo/query/非公网全拒。"""
    assert preflight.parse_public_turn_host("turn.acme-public.org") == "turn.acme-public.org"
    assert preflight.parse_public_turn_host("Turn.Acme-Public.org.") == "turn.acme-public.org"
    for bad in (
        "https://turn.acme-public.org",
        "turn.acme-public.org/path",
        "user@turn.acme-public.org",
        "turn.acme-public.org?x=1",
        "turn.acme-public.org#f",
        "turn.acme-public.org extra",
        "127.0.0.1",
        "app.example.com",
        "",
    ):
        with pytest.raises(preflight.PreflightError):
            preflight.parse_public_turn_host(bad)


def test_secret_file_read_error_reports_class_only(tmp_path: Path) -> None:
    """Round 4：secret 文件读取失败只透出错误类别，不回显文件路径。"""
    secret_path = tmp_path / "definitely-missing-token-file.txt"
    with pytest.raises(preflight.PreflightError) as excinfo:
        preflight._read_secret_file(str(secret_path), "LiveKit token")
    message = str(excinfo.value)
    assert "definitely-missing-token-file" not in message and str(tmp_path) not in message
    assert "FileNotFoundError" in message or "无法读取" in message


# ---------------------------------------------------------------- Round 5 非 UTF-8 文件泄漏回归


def _write_binary_marker_file(directory: Path, name: str) -> Path:
    """写一个含可见标记字节的非 UTF-8 文件（标记用于断言"绝不出现"）。"""
    binary_path = directory / name
    binary_path.write_bytes(b"\xff\xfeRAW-BYTES-MARKER\x80\xff")
    return binary_path


def test_binary_secret_file_error_leaks_no_path_or_bytes(tmp_path: Path) -> None:
    """Round 5：非 UTF-8 secret 文件——异常只含错误类别，无路径/无字节片段。"""
    binary_path = _write_binary_marker_file(tmp_path, "binary-token.bin")
    with pytest.raises(preflight.PreflightError) as excinfo:
        preflight._read_secret_file(str(binary_path), "LiveKit token")
    message = str(excinfo.value)
    assert "UnicodeDecodeError" in message
    assert str(binary_path) not in message and str(tmp_path) not in message
    assert "RAW-BYTES-MARKER" not in message, "异常文本不得含原始字节片段"


def test_binary_credentials_file_error_leaks_no_path_or_bytes(tmp_path: Path) -> None:
    """Round 5：非 UTF-8 凭据文件——同款只透出错误类别的语义。"""
    binary_path = _write_binary_marker_file(tmp_path, "binary-credentials.bin")
    with pytest.raises(preflight.PreflightError) as excinfo:
        preflight._read_credentials_file(str(binary_path))
    message = str(excinfo.value)
    assert "UnicodeDecodeError" in message
    assert str(binary_path) not in message and str(tmp_path) not in message
    assert "RAW-BYTES-MARKER" not in message


def test_main_binary_secret_file_fails_without_report_or_leak(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Round 5：main 消费非 UTF-8 secret 文件——exit 1、无报告、stderr 无路径/字节。"""
    binary_path = _write_binary_marker_file(tmp_path, "token.bin")
    report_path = tmp_path / "report.json"
    code = preflight.main([
        "--app-url", "https://edge.acme-public.org",
        "--livekit-token-file", str(binary_path),
        "--output", str(report_path),
    ])
    assert code == preflight.EXIT_FAILURE
    captured = capsys.readouterr()
    assert "RAW-BYTES-MARKER" not in captured.err + captured.out
    assert str(binary_path) not in captured.err + captured.out
    assert str(tmp_path) not in captured.err + captured.out
    assert not report_path.exists(), "secret 文件非法时不得产出报告"


def test_run_checks_without_endpoints_makes_no_requests() -> None:
    """零端点 → 零检查零网络（run_checks 直接返回空清单）。"""
    assert preflight.run_checks(None, None, None, None, 5349, None, None, 5.0) == []


def test_run_checks_rejects_placeholder_endpoint_before_any_request() -> None:
    """保留域端点在发请求前即被拒绝（fail-closed 参数防线）。"""
    with pytest.raises(preflight.PreflightError):
        preflight.run_checks("https://app.example.com", None, None, None, 5349, None, None, 5.0)


# ------------------------------------- M14-161 base path 探测契约（打桩，零网络）


class _ProbeRecorder:
    """打桩 _http_request：记录完整探测 URL（endpoint.url+path）与请求头。"""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.origin_headers: list[str | None] = []

    def __call__(
        self,
        endpoint: preflight.Endpoint,
        path: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        timeout: float = 10.0,
    ) -> tuple[int, dict[str, str]]:
        self.urls.append(f"{endpoint.url}{path}")
        self.origin_headers.append((headers or {}).get("Origin"))
        response_headers = {
            "Strict-Transport-Security": "max-age=31536000",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Access-Control-Allow-Origin": "https://edge.acme-public.org",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Credentials": "true",
        }
        return 200, response_headers


def _stub_network(monkeypatch: pytest.MonkeyPatch, recorder: _ProbeRecorder) -> None:
    monkeypatch.setattr(preflight, "_http_request", recorder)
    monkeypatch.setattr(preflight, "_cert_check", lambda *args, **kwargs: None)


def test_app_base_path_probes_exact_canonical_aios_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M14-161：app base /aios 拓扑探测精确 canonical `/aios`（无尾斜杠），
    绝不探测根路径 `/`（basePath 构建下 `/` 是 404）。"""
    recorder = _ProbeRecorder()
    _stub_network(monkeypatch, recorder)
    checks = preflight.run_checks(
        "https://edge.acme-public.org/aios", None, None, None, 5349, None, None, 5.0
    )
    assert "https://edge.acme-public.org/aios" in recorder.urls
    assert "https://edge.acme-public.org/" not in recorder.urls, "base 拓扑不得探测根 /"
    assert all(check["status"] == preflight.STATUS_PASS for check in checks)


def test_app_root_origin_still_probes_root_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    """M14-161 回归锚：根 origin app 探测 `/`（与 M14-161 之前逐字一致）。"""
    recorder = _ProbeRecorder()
    _stub_network(monkeypatch, recorder)
    preflight.run_checks(
        "https://edge.acme-public.org", None, None, None, 5349, None, None, 5.0
    )
    assert "https://edge.acme-public.org/" in recorder.urls
    assert all(url == "https://edge.acme-public.org/" for url in recorder.urls)


def test_api_base_path_probes_aios_health(monkeypatch: pytest.MonkeyPatch) -> None:
    """M14-161：api base /aios 拓扑健康探测 canonical `/aios/health`（边缘
    精确路由），CORS preflight 与兜底 GET 同路径；绝不探测根 `/health`。"""
    recorder = _ProbeRecorder()
    _stub_network(monkeypatch, recorder)
    checks = preflight.run_checks(
        None, "https://edge.acme-public.org/aios", None, None, 5349, None, None, 5.0
    )
    assert recorder.urls.count("https://edge.acme-public.org/aios/health") >= 1
    assert "https://edge.acme-public.org/health" not in recorder.urls, "base 拓扑不得探测根 /health"
    health = next(c for c in checks if c["name"].startswith("api-health"))
    assert health["status"] == preflight.STATUS_PASS
    assert "/aios/health" in health["detail"], "检查详情用 canonical 完整探测路径"


def test_api_root_origin_still_probes_root_health(monkeypatch: pytest.MonkeyPatch) -> None:
    """M14-161 回归锚：根 origin api 探测 `/health`（与 M14-161 之前一致）。"""
    recorder = _ProbeRecorder()
    _stub_network(monkeypatch, recorder)
    checks = preflight.run_checks(
        None, "https://edge.acme-public.org", None, None, 5349, None, None, 5.0
    )
    assert recorder.urls.count("https://edge.acme-public.org/health") >= 1
    assert all(not url.endswith("/aios/health") for url in recorder.urls)
    health = next(c for c in checks if c["name"].startswith("api-health"))
    assert health["status"] == preflight.STATUS_PASS


def test_base_path_login_probes_public_login_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """M14-161：base 拓扑登录探测拼出公共登录路径 /aios/api/v1/auth/login
    （边缘 ^~ /aios/api/ 剥 /aios 后即上游 /api/v1/auth/login）。"""
    recorder = _ProbeRecorder()
    _stub_network(monkeypatch, recorder)
    preflight.run_checks(
        None,
        "https://edge.acme-public.org/aios",
        None,
        None,
        5349,
        None,
        {"username": "u", "password": "p"},
        5.0,
    )
    assert "https://edge.acme-public.org/aios/api/v1/auth/login" in recorder.urls


def test_base_path_cors_origin_is_origin_without_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M14-161：CORS Origin 头恒为 endpoint origin（无 base path）——同源
    路径制拓扑下页面 origin 是 https://edge.acme-public.org。"""
    recorder = _ProbeRecorder()
    _stub_network(monkeypatch, recorder)
    checks = preflight.run_checks(
        "https://edge.acme-public.org/aios",
        "https://edge.acme-public.org/aios",
        None,
        None,
        5349,
        None,
        None,
        5.0,
    )
    sent = [origin for origin in recorder.origin_headers if origin is not None]
    assert sent and set(sent) == {"https://edge.acme-public.org"}, (
        "Origin 头必须是 endpoint origin（无 base path）"
    )
    cors = next(c for c in checks if c["name"].startswith("api-cors"))
    assert cors["status"] == preflight.STATUS_PASS


def test_main_report_persists_canonical_base_path_endpoints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """M14-161：报告 endpoints 存 canonical origin+/aios 值（重构非原文）。"""
    monkeypatch.setattr(
        preflight, "run_checks",
        lambda *args, **kwargs: [{"name": "app-https:x", "status": "pass", "detail": "ok"}],
    )
    report_path = tmp_path / "report.json"
    code = preflight.main([
        "--app-url", "https://edge.acme-public.org/aios",
        "--api-url", "https://edge.acme-public.org/aios",
        "--output", str(report_path),
    ])
    assert code == preflight.EXIT_MANUAL_PENDING
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["endpoints"]["app_url"] == "https://edge.acme-public.org/aios"
    assert report["endpoints"]["api_url"] == "https://edge.acme-public.org/aios"


# ---------------------------------------------------------------- 判定逻辑


def test_evaluate_security_headers_pass_and_failures() -> None:
    assert preflight.evaluate_security_headers(PASS_HEADERS) == []
    problems = preflight.evaluate_security_headers({})
    assert any("Strict-Transport-Security" in p for p in problems)
    assert any("X-Content-Type-Options" in p for p in problems)
    assert any("Referrer-Policy" in p for p in problems)
    weak_hsts = {**PASS_HEADERS, "Strict-Transport-Security": "max-age=86400"}
    problems = preflight.evaluate_security_headers(weak_hsts)
    assert any("max-age=86400" in p for p in problems), "HSTS 强度不足必须拦截"


def test_evaluate_cors_headers_exact_origin_only() -> None:
    origin = "https://app.example.com"
    good = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Credentials": "true",
    }
    assert preflight.evaluate_cors_headers(origin, good) == []
    # 通配符拒绝（带凭据拓扑）
    assert any("*" in p for p in preflight.evaluate_cors_headers(
        origin, {**good, "Access-Control-Allow-Origin": "*"}))
    # 源不匹配拒绝
    assert any("不一致" in p for p in preflight.evaluate_cors_headers(
        origin, {**good, "Access-Control-Allow-Origin": "https://other.example.com"}))
    # 缺凭据头拒绝
    missing_cred = {k: v for k, v in good.items() if k != "Access-Control-Allow-Credentials"}
    assert any("Allow-Credentials" in p for p in preflight.evaluate_cors_headers(origin, missing_cred))
    # 全空拒绝
    assert preflight.evaluate_cors_headers(origin, {})


def test_evaluate_set_cookie_flags() -> None:
    problems, attrs = preflight.evaluate_set_cookie(
        "session=SECRETVALUE; Path=/; Secure; SameSite=None; HttpOnly"
    )
    assert problems == []
    assert attrs["name"] == "session" and attrs["samesite"] == "none"
    assert "SECRETVALUE" not in json.dumps(attrs), "cookie 值绝不进入结论结构"
    assert any("Secure" in p for p in preflight.evaluate_set_cookie(
        "session=x; SameSite=None; HttpOnly")[0])
    assert any("SameSite" in p for p in preflight.evaluate_set_cookie(
        "session=x; Secure; SameSite=Lax; HttpOnly")[0])
    assert any("HttpOnly" in p for p in preflight.evaluate_set_cookie(
        "session=x; Secure; SameSite=None")[0])
    assert preflight.evaluate_set_cookie("")[0], "无 Set-Cookie 也必须报告为不可证明"


# ---------------------------------------------------------------- STUN 构造/解析


def test_build_stun_allocate_layout() -> None:
    txid = bytes(range(12))
    packet = preflight.build_stun_allocate(txid)
    msg_type, length, magic = struct.unpack("!HHI", packet[:8])
    assert msg_type == 0x0003 and length == 0 and magic == 0x2112A442
    assert packet[8:20] == txid
    with pytest.raises(ValueError):
        preflight.build_stun_allocate(b"short")


def _stun_error_response(txid: bytes, code: int = 401) -> bytes:
    """构造 coturn 风格的 Allocate Error 401（带 ERROR-CODE 属性，4 字节对齐）。"""
    value = bytes([0, 0, code // 100, code % 100])
    padded = value + b"\x00" * ((4 - len(value) % 4) % 4)
    attr = struct.pack("!HH", 0x0009, len(value)) + padded
    return struct.pack("!HHI", 0x0113, len(attr), 0x2112A442) + txid + attr


def test_parse_stun_response_extracts_401() -> None:
    txid = bytes(range(12))
    msg_type, code = preflight.parse_stun_response(_stun_error_response(txid, 401), txid)
    assert msg_type == 0x0113 and code == 401
    # 非 401（如 403）可解析出来——是否放行由调用方按期望判定
    _, code403 = preflight.parse_stun_response(_stun_error_response(txid, 403), txid)
    assert code403 == 403


def test_parse_stun_response_rejects_malformed() -> None:
    txid = bytes(range(12))
    with pytest.raises(ValueError):
        preflight.parse_stun_response(b"\x01", txid)  # 短包
    with pytest.raises(ValueError):
        preflight.parse_stun_response(_stun_error_response(b"X" * 12, 401), txid)  # txid 不匹配
    bad_magic = bytearray(_stun_error_response(txid, 401))
    bad_magic[4:8] = b"\x00\x00\x00\x00"
    with pytest.raises(ValueError):
        preflight.parse_stun_response(bytes(bad_magic), txid)  # magic 不匹配


# ---------------------------------------------------------------- 人工签认


def _full_attestation() -> dict:
    return {
        "attested_items": [item["id"] for item in preflight.MANUAL_CHECKLIST],
        "attested_by": "operator",
        "attested_at": "2026-09-26T00:00:00+00:00",
    }


def test_validate_mobile_attestation_full_coverage_passes() -> None:
    assert preflight.validate_mobile_attestation(_full_attestation()) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"attested_items": ["nonexistent-id"], "attested_by": "operator"},  # 未知项
        {"attested_items": ["mobile-4g5g-open"], "attested_by": ""},  # 缺签认人
        ["not", "a", "dict"],  # 非对象
        {"attested_items": "mobile-4g5g-open", "attested_by": "operator"},  # 非数组
    ],
)
def test_validate_mobile_attestation_rejects_malformed(payload: object) -> None:
    """结构性违法（未知项/缺签认人/非对象/非数组）：直接拒绝（异常路径）。"""
    with pytest.raises(preflight.PreflightError):
        preflight.validate_mobile_attestation(payload)


@pytest.mark.parametrize(
    "items",
    [
        [],
        ["mobile-4g5g-open"],
        ["mobile-4g5g-open", "mobile-exam-flow"],
    ],
)
def test_validate_mobile_attestation_reports_missing_coverage(items: list[str]) -> None:
    """覆盖不全：返回缺失清单（调用方据此判 incomplete，不放行）。"""
    missing = preflight.validate_mobile_attestation(
        {"attested_items": items, "attested_by": "operator"}
    )
    assert missing, "覆盖不全必须产生非空缺失清单"
    required = {item["id"] for item in preflight.MANUAL_CHECKLIST}
    assert set(missing) == required - set(items)


def test_build_manual_checklist_returns_copies() -> None:
    first = preflight.build_manual_checklist()
    first[0]["title"] = "mutated"
    assert preflight.build_manual_checklist()[0]["title"] != "mutated", "清单必须返回副本"


# ---------------------------------------------------------------- exit codes（run_checks 打桩，零网络）


def test_main_requires_explicit_endpoint(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        preflight.main([])
    assert excinfo.value.code == 2  # argparse usage error
    assert "必须至少显式提供一个端点" in capsys.readouterr().err


def test_main_exit_3_when_manual_attestation_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """自动检查全过但人工清单未签认 → exit 3（不宣称生产可用）。"""
    monkeypatch.setattr(
        preflight, "run_checks",
        lambda *args, **kwargs: [{"name": "app-https:x", "status": "pass", "detail": "ok"}],
    )
    report_path = tmp_path / "report.json"
    code = preflight.main([
        "--app-url", "https://edge.acme-public.org", "--output", str(report_path),
    ])
    assert code == preflight.EXIT_MANUAL_PENDING == 3
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mobile_attestation"]["status"] == "pending"
    assert report["summary"] == {"total": 1, "passed": 1, "failed": 0}
    assert "manual_pending" not in capsys.readouterr().out  # exit code 表达状态


def test_main_exit_0_with_full_attestation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        preflight, "run_checks",
        lambda *args, **kwargs: [{"name": "app-https:x", "status": "pass", "detail": "ok"}],
    )
    attested = tmp_path / "attested.json"
    attested.write_text(json.dumps(_full_attestation()), encoding="utf-8")
    code = preflight.main([
        "--app-url", "https://edge.acme-public.org",
        "--mobile-attested-file", str(attested),
    ])
    assert code == preflight.EXIT_OK == 0


def test_main_exit_1_on_failed_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        preflight, "run_checks",
        lambda *args, **kwargs: [
            {"name": "app-https:x", "status": "pass", "detail": "ok"},
            {"name": "api-cors:x", "status": "fail", "detail": "ACAO=*"},
        ],
    )
    attested = tmp_path / "attested.json"
    attested.write_text(json.dumps(_full_attestation()), encoding="utf-8")
    code = preflight.main([
        "--api-url", "https://edge.acme-public.org",
        "--mobile-attested-file", str(attested),
    ])
    assert code == preflight.EXIT_FAILURE == 1


def test_main_exit_1_on_incomplete_attestation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        preflight, "run_checks",
        lambda *args, **kwargs: [{"name": "app-https:x", "status": "pass", "detail": "ok"}],
    )
    attested = tmp_path / "attested.json"
    partial = _full_attestation()
    partial["attested_items"] = partial["attested_items"][:2]
    attested.write_text(json.dumps(partial), encoding="utf-8")
    code = preflight.main([
        "--app-url", "https://edge.acme-public.org",
        "--mobile-attested-file", str(attested),
    ])
    assert code == preflight.EXIT_FAILURE == 1


def test_main_exit_1_on_invalid_endpoint(capsys: pytest.CaptureFixture[str]) -> None:
    code = preflight.main(["--app-url", "https://app.example.com"])
    assert code == preflight.EXIT_FAILURE == 1
    assert "FAIL" in capsys.readouterr().err


# ---------------------------------------------------------------- 源码契约


def test_source_embeds_no_default_endpoints() -> None:
    """ast 常量扫描：带 :// 的字符串常量只允许出现在 argparse help 占位示例；
    模块级赋值/默认参数不得内置任何端点（无默认目标是硬契约）。"""
    source = (REPO / "tools" / "ops" / "public_edge_preflight.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    def _strip_docstrings(body: list[ast.stmt]) -> list[ast.stmt]:
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            return body[1:]
        return body

    offenders: list[str] = []
    # 豁免集合：f-string 字面片段 + 各级（含嵌套函数的）首部 docstring 节点
    exempt_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    exempt_ids.add(id(sub))
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = _strip_docstrings(node.body)
            if body is not node.body:  # docstring 已被识别——该常量节点记入豁免
                exempt_ids.add(id(node.body[0].value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "://" in node.value
            and id(node) not in exempt_ids
        ):
            offenders.append(node.value)
    # 豁免三类：argparse help 的占位示例（https://app.example.com）、
    # 人工清单占位模板（wss://<livekit 域名>）、代码里的 scheme 存在性
    # 检测标记（裸 "://" 字面量——不是端点）
    assert offenders, "扫描器应至少命中 help 文本（否则扫描逻辑失效）"
    for text in offenders:
        assert text.strip() == "://" or ".example.com" in text or "<" in text, (
            f"源码含非占位 URL 常量: {text!r}"
        )


def test_source_has_no_hardcoded_ip_targets() -> None:
    source = (REPO / "tools" / "ops" / "public_edge_preflight.py").read_text(encoding="utf-8")
    # 剥离 RFC 5737 文档段注释后不允许任何 IP 字面量（无默认目标契约）
    scrubbed = re.sub(r"192\.0\.2\.0/24|198\.51\.100\.0/24|203\.0\.113\.0/24", "", source)
    assert not re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", scrubbed)


# ---------------------------------------------------------------- 本机回环行为面（不触外网）


def test_direct_opener_reaches_loopback_http_server() -> None:
    """直连 opener 真实可达（本机临时 HTTP 服务验证请求链路，非外部网络）。"""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        endpoint = preflight.Endpoint(url=f"http://127.0.0.1:{port}", host="127.0.0.1", port=port)
        status, headers = preflight._http_request(endpoint, "/health", timeout=5)
        assert status == 200 and headers.get("Content-Type") == "application/json"
        status, _ = preflight._http_request(endpoint, "/nope", timeout=5)
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_tls_probe_fails_closed_against_plain_tcp() -> None:
    """tls_probe 对非 TLS 端口必须抛错——证书验证链路真实生效（fail-closed）。"""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    thread = threading.Thread(target=listener.accept, daemon=True)
    thread.start()
    try:
        endpoint = preflight.Endpoint(url="https://127.0.0.1", host="127.0.0.1", port=port)
        with pytest.raises((ssl.SSLError, OSError)):
            preflight.tls_probe(endpoint, timeout=5)
    finally:
        listener.close()
        thread.join(timeout=2)


# --------------------------------------------- M14-162 证书 DN 格式化（零网络）


# Codex 在 https://ndtool.cn/aios 实测崩溃的真实 ssl.getpeercert 形态：
# subject/issuer 是三层嵌套 tuple——DN（RDN 集合）→ RDN → (key, value)。
REAL_CERT_SUBJECT = ((("commonName", "ndtool.cn"),),)
REAL_CERT_ISSUER = (
    (("countryName", "US"),),
    (("organizationName", "Let's Encrypt"),),  # 真实值含撇号
    (("commonName", "YE1"),),
)


def test_format_cert_dn_real_nested_shapes() -> None:
    """M14-162：真实三层嵌套（DN→RDN→key/value）格式化不再 TypeError。"""
    assert preflight._format_cert_dn(REAL_CERT_SUBJECT) == "commonName=ndtool.cn"
    assert preflight._format_cert_dn(REAL_CERT_ISSUER) == (
        "countryName=US, organizationName=Let's Encrypt, commonName=YE1"
    )


def test_format_cert_dn_multivalue_empty_missing_and_malformed() -> None:
    """多属性 RDN（RFC 4514 `+` 连接）、空/缺失 DN → 空串、畸形条目跳过。"""
    multi_attribute_rdn = ((("countryName", "US"), ("organizationName", "Example Org"),),)
    assert (
        preflight._format_cert_dn(multi_attribute_rdn)
        == "countryName=US+organizationName=Example Org"
    ), "同一 RDN 内多属性以 + 连接"
    assert preflight._format_cert_dn(()) == "", "空 DN → 空串"
    assert preflight._format_cert_dn(None) == "", "缺失 DN（None）→ 空串"
    mixed = ((("commonName", "ndtool.cn"),), ("malformed",), (("organizationalUnitName", "edge"),))
    assert (
        preflight._format_cert_dn(mixed) == "commonName=ndtool.cn, organizationalUnitName=edge"
    ), "非二元组条目防御性跳过，合法 RDN 照常展示"


def test_legacy_join_expression_crashed_on_real_shape() -> None:
    """回归锚：旧实现的 `"=".join(part)` 对真实 RDN 结构确实抛
    TypeError——证明本修复针对真实崩溃面（非臆测形状）。"""
    with pytest.raises(TypeError):
        ", ".join("=".join(part) for part in REAL_CERT_ISSUER)


def test_tls_probe_formats_real_cert_dn_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tls_probe 端到端（打桩 socket/ssl，零网络）：真实嵌套证书结构
    产出可读 subject/issuer 与剩余有效期，不再在 DN 格式化处崩溃。"""

    real_cert = {
        "subject": REAL_CERT_SUBJECT,
        "issuer": REAL_CERT_ISSUER,
        "notAfter": "Sep 28 12:00:00 2027 GMT",
    }

    class _FakeTLS:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def getpeercert(self) -> dict:
            return real_cert

    class _FakeContext:
        def wrap_socket(self, sock: object, server_hostname: str | None = None) -> _FakeTLS:
            return _FakeTLS()

    class _FakeSock:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(
        preflight.socket, "create_connection", lambda address, timeout=None: _FakeSock()
    )
    monkeypatch.setattr(preflight.ssl, "create_default_context", lambda: _FakeContext())
    endpoint = preflight.Endpoint(url="https://edge.acme-public.org", host="edge.acme-public.org", port=443)
    facts = preflight.tls_probe(endpoint, timeout=5)
    assert facts["subject"] == "commonName=ndtool.cn"
    assert facts["issuer"] == "countryName=US, organizationName=Let's Encrypt, commonName=YE1"
    assert facts["not_after"] == "Sep 28 12:00:00 2027 GMT"
    assert isinstance(facts["remain_days"], int) and facts["remain_days"] > 300, (
        "notAfter 解析链路随 DN 修复一并回归"
    )


def test_cli_entrypoint_module_compatible() -> None:
    """python -m 可执行且无端点时拒绝（子进程冒烟，exit 2）。"""
    result = subprocess.run(
        [sys.executable, "-m", "public_edge_preflight"],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        cwd=str(REPO / "tools" / "ops"), check=False,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 2
    assert "必须至少显式提供一个端点" in result.stderr


def test_ws_upgrade_request_shape() -> None:
    """升级请求构造：RFC 6455 必需头 + livekit 子协议 + 随机 Key（纯函数直测）。"""
    request = preflight._build_ws_upgrade_request("edge.acme-public.org", "/rtc?access_token=tok")
    lines = request.split("\r\n")
    assert lines[0] == "GET /rtc?access_token=tok HTTP/1.1"
    assert "Host: edge.acme-public.org" in lines
    assert "Upgrade: websocket" in lines and "Connection: Upgrade" in lines
    assert "Sec-WebSocket-Version: 13" in lines
    assert "Sec-WebSocket-Protocol: livekit" in lines
    keys = [line for line in lines if line.startswith("Sec-WebSocket-Key: ")]
    assert len(keys) == 1 and len(keys[0].split(": ", 1)[1]) == 24  # 16 字节 base64
    # 随机性：两次构造 Key 不同
    again = preflight._build_ws_upgrade_request("edge.acme-public.org", "/rtc")
    assert again != request


def test_ws_upgrade_probe_enforces_tls() -> None:
    """WSS 探测对非 TLS 端口必须失败——握手恒走证书验证（不做降级探测）。"""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    threading.Thread(target=listener.accept, daemon=True).start()
    try:
        endpoint = preflight.Endpoint(url="https://127.0.0.1", host="127.0.0.1", port=port)
        with pytest.raises((ssl.SSLError, OSError)):
            preflight.ws_upgrade_probe(endpoint, "/rtc?access_token=tok", timeout=5)
    finally:
        listener.close()
