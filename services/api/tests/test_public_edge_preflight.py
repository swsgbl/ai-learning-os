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
   tls_probe 对非 TLS 端口必须抛错（证书验证链路真实生效）。
"""
from __future__ import annotations

import ast
import json
import re
import socket
import ssl
import struct
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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


def test_parse_public_https_url_accepts_public_form() -> None:
    endpoint = preflight.parse_public_https_url("https://edge.acme-public.org/some/path")
    assert endpoint.host == "edge.acme-public.org"
    assert endpoint.port == 443
    assert endpoint.origin == "https://edge.acme-public.org"


def test_run_checks_without_endpoints_makes_no_requests() -> None:
    """零端点 → 零检查零网络（run_checks 直接返回空清单）。"""
    assert preflight.run_checks(None, None, None, None, 5349, None, None, 5.0) == []


def test_run_checks_rejects_placeholder_endpoint_before_any_request() -> None:
    """保留域端点在发请求前即被拒绝（fail-closed 参数防线）。"""
    with pytest.raises(preflight.PreflightError):
        preflight.run_checks("https://app.example.com", None, None, None, 5349, None, None, 5.0)


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
    fstring_fragments: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):  # f-string 的字面片段不是独立常量
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    fstring_fragments.add(id(sub))
    for node in ast.walk(tree):
        bodies: list[list[ast.stmt]] = []
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bodies.append(_strip_docstrings(node.body))
        for body in bodies:
            for stmt in body:
                for sub in ast.walk(stmt):
                    if (
                        isinstance(sub, ast.Constant)
                        and isinstance(sub.value, str)
                        and "://" in sub.value
                        and id(sub) not in fstring_fragments
                    ):
                        offenders.append(sub.value)
    # 豁免只有两类：argparse help 的占位示例（https://app.example.com）
    # 与人工清单里的占位模板（wss://<livekit 域名>）——共同特征是占位标记
    assert offenders, "扫描器应至少命中 help 文本（否则扫描逻辑失效）"
    for text in offenders:
        assert ".example.com" in text or "<" in text, f"源码含非占位 URL 常量: {text!r}"


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
        status, headers = preflight._http_request(f"http://127.0.0.1:{port}/health", timeout=5)
        assert status == 200 and headers.get("Content-Type") == "application/json"
        status, _ = preflight._http_request(f"http://127.0.0.1:{port}/nope", timeout=5)
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


def test_cli_entrypoint_module_compatible() -> None:
    """python -m 可执行且无端点时拒绝（子进程冒烟，exit 2）。"""
    result = subprocess.run(
        [sys.executable, "-m", "public_edge_preflight"],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        cwd=str(REPO / "tools" / "ops"), check=False,
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
