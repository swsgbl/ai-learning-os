r"""M14-26 voice-health sidecar 首片契约测试：双端口只读窄代理 + 安全生命周期。

背景（M14-25 生产实证）：WSL 中转端口经 Windows 侧转发不稳定，Windows
侧监控直连 WSL 健康端点超时频发。sidecar 方案：WSL 内绑 RFC1918 地址、
双端口（18010 FunASR / 18011 CosyVoice）精确 GET allowlist 只读代理，
Windows 侧控制器以固定 argv 零 shell 编排 start/probe/stop。

本套件锁定首片契约（全部 fake/临时文件，零 WSL、零网络、零真实进程）：
- 常量事实源：PORT_ROUTES 精确形状、上游恒 127.0.0.1、控制器 EXPECTED_PORTS
  /PROTECTED_PIDS/IDENTITY_MARKERS/PROTECTED_MARKERS 同源；
- HTTP 面精确 allowlist：GET 成功透传、上游 503 如实透传、超时→504、
  连接失败→502、未知异常→502 安全降级、查询串/未知路径/非 GET 一律拒绝；
- 绑定地址唯一合法域：/proc/net/route 样本解析 + RFC1918 + 默认路由接口
  网段归属核验（0.0.0.0/回环/链路本地/公网/接口外/缺探测/缺路由全拒绝）；
- 状态文件：仓库根路径containment、符号链接/非普通文件目标拒绝、
  O_EXCL+os.replace 原子替换、schema 字段齐备；
- 控制器安全：固定 argv 形状（逐元素锁定）、subprocess 调用零 shell=
  （AST 扫描双文件）、Windows CREATE_NO_WINDOW、PROTECTED_PIDS/markers
  身份核验（own/reused/protected/dead/runner-error）、safe_join 越界与
  符号链接拒绝、atomic_write_json 原子性与目标拒绝。

第二片（lifecycle，fake Runner/Health/Popen，零 WSL、零网络、零真实进程；
时延常量 monkeypatch 至毫秒级保持快速）：
- cmd_start：健康/降级幂等（不重复 spawn）、stale/reused/protected/损坏
  manifest 的清理与拒绝、全新 spawn 成功路径、status 超时回收、
  status 事实异常回收、spawn 后身份核验失败回收、WSL RunnerError
  分类（spawn 前 / status 等待中 / 身份核验时）；
- cmd_stop：TERM 优雅退出、TERM 宽限超时→单次 KILL 兜底、KILL 送达失败
  与 KILL 后仍存活（manifest 保留）、protected PID 867/26008 绝不发信号、
  stale 清理、reused/protected/runner-error 拒绝、无 manifest 幂等；
- cmd_status：只读零清理（stopped/stale/pid-reused/protected-target/
  unknown/损坏/running-healthy/running-degraded）；
- ControlLock：并发锁拒绝 + 陈旧锁（>TTL）回收。

第三片（生产修复红转绿）：status 等待中 RunnerError 分类回收（rc 3）+
Linux 子进程泄漏评估——kill wsl.exe 不保证 WSL 内子进程终止，仅当
「本次落档 PID + 探活 + 无保护标记 + 身份标记全过」才补发单次 SIGTERM
（零宽限、零 KILL、零重试）；核验不可用（WSL 面失败）/保护 PID/
身份不符一律放弃回收并记录——绝不扩大终止面、绝不违反保护边界。
"""
from __future__ import annotations

import ast
import importlib.util
import io
import ipaddress
import json
import os
import subprocess
import sys
import types
import urllib.parse
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SIDECAR_PATH = REPO_ROOT / "tools" / "voice" / "voice_health_sidecar.py"
CTL_PATH = REPO_ROOT / "tools" / "voice" / "voice_health_sidecar_control.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # 先注册再 exec：dataclasses 解析字符串注解需要 sys.modules 命中本模块
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SIDECAR = _load("voice_health_sidecar_m1426", SIDECAR_PATH)
CTL = _load("voice_health_sidecar_control_m1426", CTL_PATH)


def _make_symlink_or_skip(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("平台权限不允许创建符号链接")


# ---------- 常量事实源：双端口路由表 + 控制器同源常量 ----------


def test_port_routes_exact_shape_and_loopback_only_upstreams() -> None:
    assert SIDECAR.PORT_ROUTES == {
        18010: {"/health": "http://127.0.0.1:8010/health"},
        18011: {
            "/health": "http://127.0.0.1:8011/health",
            "/health/live": "http://127.0.0.1:8011/health/live",
        },
    }
    for routes in SIDECAR.PORT_ROUTES.values():
        for url in routes.values():
            parsed = urllib.parse.urlsplit(url)
            assert parsed.scheme == "http"
            assert parsed.hostname == "127.0.0.1"
            assert parsed.query == "" and parsed.fragment == ""
    assert SIDECAR.UPSTREAM_TIMEOUT_SECONDS == 5.0
    assert SIDECAR.MAX_UPSTREAM_BODY_BYTES == 65536


def test_status_and_controller_constants_shared_facts() -> None:
    assert SIDECAR.STATUS_SCHEMA_VERSION == 1
    assert SIDECAR.STATUS_SERVICE_NAME == "voice-health-sidecar"
    assert SIDECAR.DEFAULT_STATUS_RELPATH == (
        ".verify/artifacts/m14-26-voice-health-sidecar/sidecar-status.json"
    )
    assert CTL.EXPECTED_PORTS == (18010, 18011)
    assert CTL.DISTRO == "Ubuntu"
    assert CTL.SIDECAR_SCRIPT == "tools/voice/voice_health_sidecar.py"
    assert CTL.PROTECTED_PIDS == frozenset({867, 26008})
    assert CTL.IDENTITY_MARKERS == ("python3", "voice_health_sidecar.py")
    assert set(CTL.ALLOWED_SIGNAL_NAMES) == {"SIGTERM", "SIGKILL"}
    for marker in ("wslrelay", "wsl.exe", "wslservice", "vmmem", "docker",
                   "funasr", "cosyvoice", "systemd"):
        assert marker in CTL.PROTECTED_MARKERS


# ---------- HTTP 面：精确 GET allowlist（fake transport，零网络） ----------


class _FakeTransport:
    """fake 上游：记录调用；返回固定 (状态, 类型, 体) 或抛固定异常。"""

    def __init__(self, result=None, error=None) -> None:
        self._result = result
        self._error = error
        self.calls: list[str] = []

    def fetch(self, url: str, timeout: float = 0.0):
        self.calls.append(url)
        if self._error is not None:
            raise self._error
        return self._result


def _make_handler(path: str, command: str, routes: dict, transport):
    """object.__new__ 直构处理器：不进 socket，仅驱动被测方法。"""
    handler = object.__new__(SIDECAR.SidecarRequestHandler)
    handler.command = command
    handler.path = path
    handler.requestline = f"{command} {path} HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.client_address = ("127.0.0.1", 41000)
    handler.server = types.SimpleNamespace(routes=routes, transport=transport)
    handler.wfile = io.BytesIO()
    handler.rfile = io.BytesIO()
    handler._headers_buffer = []
    return handler


def _respond(handler) -> tuple[int, dict, bytes]:
    method = getattr(handler, f"do_{handler.command}")
    method()
    raw = handler.wfile.getvalue()
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {}
    for line in lines[1:]:
        key, _, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()
    return status, headers, body


_FUNASR_ROUTES = {"/health": "http://127.0.0.1:8010/health"}


def test_get_success_passes_through_upstream_body() -> None:
    body = b'{"phase": "ready"}'
    transport = _FakeTransport(result=(200, "application/json", body))
    handler = _make_handler("/health", "GET", _FUNASR_ROUTES, transport)
    status, headers, resp_body = _respond(handler)
    assert status == 200
    assert resp_body == body
    assert headers["content-type"] == "application/json"
    assert transport.calls == ["http://127.0.0.1:8010/health"]


def test_get_upstream_503_passes_through_byte_exact() -> None:
    body = b'{"phase": "loading"}'
    transport = _FakeTransport(result=(503, "application/json", body))
    handler = _make_handler("/health", "GET", _FUNASR_ROUTES, transport)
    status, _, resp_body = _respond(handler)
    assert status == 503
    assert resp_body == body
    assert handler.close_connection is True  # 拒绝后不复用连接


def test_get_upstream_timeout_maps_to_504() -> None:
    transport = _FakeTransport(error=SIDECAR.UpstreamTimeout("timed out"))
    handler = _make_handler("/health", "GET", _FUNASR_ROUTES, transport)
    status, _, body = _respond(handler)
    assert status == 504
    assert body == b'{"detail": "upstream timeout"}'


def test_get_upstream_connection_error_maps_to_502() -> None:
    transport = _FakeTransport(error=SIDECAR.UpstreamConnectionError("ECONNREFUSED"))
    handler = _make_handler("/health", "GET", _FUNASR_ROUTES, transport)
    status, _, body = _respond(handler)
    assert status == 502
    assert body == b'{"detail": "upstream connection failed"}'


def test_get_unknown_upstream_exception_degrades_to_502() -> None:
    transport = _FakeTransport(error=RuntimeError("internal secret"))
    handler = _make_handler("/health", "GET", _FUNASR_ROUTES, transport)
    status, _, body = _respond(handler)
    assert status == 502
    assert body == b'{"detail": "upstream error"}'  # 不外泄异常细节


@pytest.mark.parametrize("path", ["/health?probe=1", "/health#frag"])
def test_get_rejects_query_and_fragment(path: str) -> None:
    transport = _FakeTransport(result=(200, "application/json", b"{}"))
    handler = _make_handler(path, "GET", _FUNASR_ROUTES, transport)
    status, _, body = _respond(handler)
    assert status == 400
    assert body == b'{"detail": "bad request"}'
    assert transport.calls == []  # allowlist 之外的请求不触达上游


@pytest.mark.parametrize("path", ["/metrics", "/health/deep", "/", "//health"])
def test_get_rejects_paths_outside_allowlist(path: str) -> None:
    transport = _FakeTransport(result=(200, "application/json", b"{}"))
    handler = _make_handler(path, "GET", _FUNASR_ROUTES, transport)
    status, _, body = _respond(handler)
    assert status == 404
    assert body == b'{"detail": "not found"}'
    assert transport.calls == []


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
def test_non_get_methods_rejected_with_allow_header(method: str) -> None:
    handler = _make_handler("/health", method, _FUNASR_ROUTES, _FakeTransport())
    status, headers, body = _respond(handler)
    assert status == 405
    assert headers.get("allow") == "GET"
    if method == "HEAD":  # HEAD 只发头部不发体（Content-Length 仍如实）
        assert body == b""
        assert headers["content-length"] == str(len(b'{"detail": "method not allowed"}'))
    else:
        assert body == b'{"detail": "method not allowed"}'


# ---------- 绑定地址唯一合法域：/proc/net/route 样本（零发包） ----------

_ROUTE_HEADER = (
    "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT"
)
# eth0 默认路由 + eth0 本地子网 192.168.65.0/24 + lo 127.0.0.0/8（小端十六进制）
_ROUTE_TABLE_SAMPLE = (
    f"{_ROUTE_HEADER}\n"
    "eth0\t00000000\t0141A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
    "eth0\t0041A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n"
    "lo\t0000007F\t00000000\t0001\t0\t0\t0\t000000FF\t0\t0\t0\n"
    "garbage line with no hex fields\n"
)

_ROUTE_NO_DEFAULT = (
    f"{_ROUTE_HEADER}\n"
    "eth0\t0041A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0\n"
)


def test_parse_route_table_skips_header_and_bad_lines() -> None:
    rows = SIDECAR.parse_route_table(_ROUTE_TABLE_SAMPLE)
    assert len(rows) == 3  # 表头 + 垃圾行跳过
    assert rows[0].iface == "eth0"
    assert rows[0].dest == ipaddress.ip_address("0.0.0.0")
    assert rows[1].dest == ipaddress.ip_address("192.168.65.0")
    assert rows[1].mask == ipaddress.ip_address("255.255.255.0")


def test_default_route_interface_prefers_lowest_metric() -> None:
    text = (
        f"{_ROUTE_HEADER}\n"
        "eth0\t00000000\t0141A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n"
        "wlan0\t00000000\t0141A8C0\t0003\t0\t0\t50\t00000000\t0\t0\t0\n"
    )
    assert SIDECAR.default_route_interface(SIDECAR.parse_route_table(text)) == "wlan0"


def test_interface_networks_returns_only_iface_subnets() -> None:
    rows = SIDECAR.parse_route_table(_ROUTE_TABLE_SAMPLE)
    assert SIDECAR.interface_networks(rows, "eth0") == [
        ipaddress.ip_network("192.168.65.0/24")
    ]
    assert SIDECAR.interface_networks(rows, "wlan0") == []


def test_resolve_bind_address_accepts_rfc1918_in_default_iface_subnet() -> None:
    assert SIDECAR.resolve_bind_address(_ROUTE_TABLE_SAMPLE, "192.168.65.5") == "192.168.65.5"


@pytest.mark.parametrize(
    "probed,reason",
    [
        (None, "source-probe-unavailable"),
        ("not-an-ip", "invalid-address"),
        ("::1", "invalid-address"),  # IPv6 不属于合法绑定域
        ("0.0.0.0", "unspecified-address"),
        ("127.0.0.1", "loopback-address"),
        ("169.254.7.7", "link-local-address"),
        ("8.8.8.8", "not-rfc1918-private"),
        ("172.17.20.5", "outside-default-interface"),  # RFC1918 但不在 eth0 网段
    ],
)
def test_validate_bind_address_rejects_unsafe_candidates(probed, reason) -> None:
    rows = SIDECAR.parse_route_table(_ROUTE_TABLE_SAMPLE)
    with pytest.raises(SIDECAR.BindAddressError) as excinfo:
        SIDECAR.validate_bind_address(probed, rows)
    assert excinfo.value.reason == reason


def test_resolve_rejects_missing_route_table_and_missing_default_route() -> None:
    with pytest.raises(SIDECAR.BindAddressError, match="route-table-unavailable"):
        SIDECAR.resolve_bind_address("", "192.168.65.5")
    with pytest.raises(SIDECAR.BindAddressError, match="no-default-route"):
        SIDECAR.resolve_bind_address(_ROUTE_NO_DEFAULT, "192.168.65.5")


# ---------- 状态文件：路径 containment + 原子写 + schema ----------


def test_resolve_status_path_accepts_repo_internal_path() -> None:
    raw = REPO_ROOT / ".verify/artifacts/m14-26-voice-health-sidecar/sidecar-status.json"
    resolved = SIDECAR.resolve_status_path(raw)
    assert resolved == raw.resolve()
    resolved.relative_to(REPO_ROOT)  # 不越界即不抛


def test_resolve_status_path_rejects_traversal_outside_repo() -> None:
    with pytest.raises(SIDECAR.StatusFileError, match="status-file-outside-repo"):
        SIDECAR.resolve_status_path(REPO_ROOT / ".." / "evil-status.json")


def test_resolve_status_path_resolves_relative_against_cwd(monkeypatch, tmp_path) -> None:
    # cwd 在仓库外：相对路径解析后越界 → 拒绝
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SIDECAR.StatusFileError, match="status-file-outside-repo"):
        SIDECAR.resolve_status_path(Path("sidecar-status.json"))
    # cwd 在仓库根（控制器 --cd 口径）：默认相对路径合法
    monkeypatch.chdir(REPO_ROOT)
    resolved = SIDECAR.resolve_status_path(Path(SIDECAR.DEFAULT_STATUS_RELPATH))
    resolved.relative_to(REPO_ROOT)


def test_write_status_file_schema_and_atomic_replacement(tmp_path) -> None:
    status = tmp_path / "sidecar-status.json"
    status.write_text("OLD", encoding="utf-8")
    SIDECAR.write_status_file(status, "192.168.65.5", [18010, 18011])
    text = status.read_text(encoding="utf-8")
    assert not text.startswith("OLD") and text.endswith("\n")
    payload = json.loads(text)
    assert payload["schema_version"] == 1
    assert payload["service"] == "voice-health-sidecar"
    assert payload["pid"] == os.getpid()
    assert payload["bind"] == "192.168.65.5"
    assert payload["ports"] == [18010, 18011]
    assert datetime.fromisoformat(payload["started_at"]).tzinfo is not None
    # 原子替换：不留 tmp 残留
    assert [p.name for p in tmp_path.iterdir()] == ["sidecar-status.json"]


def test_write_status_file_rejects_non_regular_target(tmp_path) -> None:
    directory = tmp_path / "status-dir"
    directory.mkdir()
    with pytest.raises(SIDECAR.StatusFileError, match="unsafe-status-target"):
        SIDECAR.write_status_file(directory, "192.168.65.5", [18010, 18011])


def test_write_status_file_rejects_symlink_target(tmp_path) -> None:
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "link-status.json"
    _make_symlink_or_skip(real, link)
    with pytest.raises(SIDECAR.StatusFileError, match="unsafe-status-target"):
        SIDECAR.write_status_file(link, "192.168.65.5", [18010, 18011])


# ---------- 控制器固定 argv：零 shell + CREATE_NO_WINDOW ----------


def test_no_shell_kwarg_in_any_subprocess_call() -> None:
    """AST 扫描两份草稿：Popen/run/call/check_* 一律不得带 shell= 关键字。"""
    subprocess_funcs = {"Popen", "run", "call", "check_call", "check_output"}
    for path in (SIDECAR_PATH, CTL_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else "")
            if name in subprocess_funcs:
                assert "shell" not in {kw.arg for kw in node.keywords}, (
                    f"{path.name}: subprocess 调用出现 shell= 关键字"
                )


def test_creation_flags_create_no_window_on_windows() -> None:
    if sys.platform == "win32":
        assert CTL.creation_flags() == subprocess.CREATE_NO_WINDOW
        assert CTL.creation_flags() != 0
    else:
        assert CTL.creation_flags() == 0


def test_build_start_argv_exact_shape() -> None:
    root = Path("R:/repo")
    argv = CTL.build_start_argv(root, "rel/status.json")
    assert argv == [
        "wsl.exe", "--distribution", "Ubuntu", "--cd", str(root),
        "--exec", "python3", "-u", "tools/voice/voice_health_sidecar.py",
        "--status-file", "rel/status.json",
    ]
    assert all(isinstance(item, str) for item in argv)  # 无 Path 泄漏进 argv


def test_build_probe_argv_exact_shape() -> None:
    root = Path("R:/repo")
    argv = CTL.build_probe_argv(root, "-", "rel/status.json")
    assert argv == [
        "wsl.exe", "--distribution", "Ubuntu", "--cd", str(root),
        "--exec", "python3", "-c", CTL.PROBE_CODE, "-", "rel/status.json",
    ]


def test_build_signal_argv_exact_shape_and_validation() -> None:
    root = Path("R:/repo")
    argv = CTL.build_signal_argv(root, 4242, "SIGTERM")
    assert argv == [
        "wsl.exe", "--distribution", "Ubuntu", "--cd", str(root),
        "--exec", "python3", "-c", CTL.SIGNAL_CODE, "4242", "SIGTERM",
    ]
    with pytest.raises(ValueError):  # 信号名不在白名单
        CTL.build_signal_argv(root, 4242, "SIGSEGV")
    for bad_pid in (0, -1, "4242"):  # 非正整数 PID 拒绝
        with pytest.raises(ValueError):
            CTL.build_signal_argv(root, bad_pid, "SIGTERM")


def test_spawn_sidecar_uses_argv_no_shell_and_create_no_window(
    monkeypatch, tmp_path
) -> None:
    recorded: dict = {}

    class _FakePopen:
        def __init__(self, argv, **kwargs) -> None:
            recorded["argv"] = argv
            recorded["kwargs"] = kwargs

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    argv = CTL.build_start_argv(tmp_path, "rel/status.json")
    proc = CTL.Runner(tmp_path).spawn_sidecar(argv, tmp_path / "log" / "ctl.log")
    assert isinstance(proc, _FakePopen)
    assert recorded["argv"] == argv  # argv 列表直传，无字符串命令
    assert "shell" not in recorded["kwargs"]
    assert recorded["kwargs"]["creationflags"] == CTL.creation_flags()
    assert recorded["kwargs"]["stdin"] == subprocess.DEVNULL


def test_probe_uses_fixed_argv_and_parses_json(monkeypatch, tmp_path) -> None:
    recorded: dict = {}

    class _FakeCompleted:
        returncode = 0
        stdout = '{"pid_alive": true, "cmdline": "python3 -u tools/voice/voice_health_sidecar.py"}'
        stderr = ""

    def _fake_run(argv, **kwargs):
        recorded["argv"] = argv
        recorded["kwargs"] = kwargs
        return _FakeCompleted()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    payload = CTL.Runner(tmp_path).probe("4242", "rel/status.json")
    assert payload["pid_alive"] is True
    assert recorded["argv"] == CTL.build_probe_argv(tmp_path, "4242", "rel/status.json")
    assert "shell" not in recorded["kwargs"]
    assert recorded["kwargs"]["creationflags"] == CTL.creation_flags()


# ---------- 归属核验：protected PID / marker / 身份标记 ----------


class _StaticRunner:
    def __init__(self, payload=None, error=None) -> None:
        self._payload = payload
        self._error = error
        self.calls: list[tuple[str, str]] = []

    def probe(self, pid_or_dash: str, status_relpath: str) -> dict:
        self.calls.append((pid_or_dash, status_relpath))
        if self._error is not None:
            raise self._error
        return self._payload


_OWN_CMDLINE = "python3 -u tools/voice/voice_health_sidecar.py --status-file rel"


def _manifest(pid: int = 4242) -> CTL.Manifest:
    return CTL.Manifest(
        pid=pid, bind="192.168.65.5", ports=[18010, 18011],
        started_at="2026-09-14T20:00:00+08:00", log="sidecar-control.log",
    )


def test_identity_and_protected_marker_predicates() -> None:
    assert CTL.identity_matches(_OWN_CMDLINE) is True
    assert CTL.identity_matches("python3 -u other_script.py") is False
    assert CTL.has_protected_marker("wslrelay --forward 8010") is True
    assert CTL.has_protected_marker("python3 /opt/CosyVoice/server.py") is True  # 大小写不敏感
    assert CTL.has_protected_marker(_OWN_CMDLINE) is False


@pytest.mark.parametrize(
    "pid,payload,kind",
    [
        (4242, {"pid_alive": True, "cmdline": _OWN_CMDLINE}, "own"),
        (4242, {"pid_alive": True, "cmdline": "bash -c unrelated"}, "reused"),
        (4242, {"pid_alive": True, "cmdline": "python3 /opt/cosyvoice/server.py"}, "protected"),
        (26008, {"pid_alive": True, "cmdline": _OWN_CMDLINE}, "protected"),  # PID 硬保护优先
        (4242, {"pid_alive": False, "cmdline": ""}, "dead"),
        (4242, {"pid_alive": True, "cmdline": ""}, "dead"),
    ],
)
def test_evaluate_target_classification(pid, payload, kind) -> None:
    runner = _StaticRunner(payload=payload)
    result = CTL.evaluate_target(_manifest(pid), runner, "rel/status.json")
    assert result.kind == kind
    assert runner.calls == [(str(pid), "rel/status.json")]


def test_evaluate_target_runner_error() -> None:
    error = CTL.RunnerError("wsl-management-unavailable", "probe rc=3")
    result = CTL.evaluate_target(_manifest(), _StaticRunner(error=error), "rel/status.json")
    assert result.kind == "runner-error"
    assert result.error is error


# ---------- safe_join / atomic_write_json / Manifest ----------


def test_safe_join_accepts_plain_names_only(tmp_path) -> None:
    assert CTL.safe_join(tmp_path, "sidecar-status.json") == tmp_path / "sidecar-status.json"
    for bad in ("", "..", ".", "sub/evil.json", "/etc/passwd", "C:/Windows/evil.json"):
        with pytest.raises(CTL.UnsafePathError):
            CTL.safe_join(tmp_path, bad)


def test_safe_join_rejects_symlink_escape(tmp_path) -> None:
    outside = tmp_path.parent / "outside-target.json"
    outside.write_text("{}", encoding="utf-8")
    link = tmp_path / "evil.json"
    _make_symlink_or_skip(outside, link)
    with pytest.raises(CTL.UnsafePathError):
        CTL.safe_join(tmp_path, "evil.json")


def test_atomic_write_json_writes_and_replaces_atomically(tmp_path) -> None:
    target = tmp_path / "sidecar-manifest.json"
    target.write_text("OLD", encoding="utf-8")
    payload = {"schema_version": 1, "service": "voice-health-sidecar", "pid": 4242}
    CTL.atomic_write_json(target, payload)
    text = target.read_text(encoding="utf-8")
    assert text.endswith("\n") and json.loads(text) == payload
    assert [p.name for p in tmp_path.iterdir()] == ["sidecar-manifest.json"]  # 无 tmp 残留


def test_atomic_write_json_rejects_unsafe_targets(tmp_path) -> None:
    directory = tmp_path / "dir-target"
    directory.mkdir()
    with pytest.raises(CTL.UnsafePathError):
        CTL.atomic_write_json(directory, {"pid": 1})
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    _make_symlink_or_skip(real, link)
    with pytest.raises(CTL.UnsafePathError):
        CTL.atomic_write_json(link, {"pid": 1})


def test_manifest_roundtrip_and_rejects_invalid_json() -> None:
    manifest = _manifest()
    assert CTL.Manifest.from_json(manifest.to_json()) == manifest
    payload = manifest.to_payload()
    assert set(payload) == {
        "schema_version", "service", "pid", "bind", "ports", "started_at", "log",
    }
    assert payload["schema_version"] == 1
    assert payload["service"] == "voice-health-sidecar"
    for bad in ("not json", "[1, 2]", "{}", '{"pid": 0, "bind": "b", "ports": [1]}',
                '{"pid": 5, "bind": "", "ports": [1]}', '{"pid": 5, "bind": "b", "ports": []}'):
        assert CTL.Manifest.from_json(bad) is None, bad


# ---------- 第二片：cmd_start / cmd_stop / cmd_status 生命周期（fake 编排） ----------


class _FakePopenHandle:
    """spawn 返回的句柄替身——终止只经 runner.kill_spawned 记录，零真实进程。"""


class _LifecycleRunner:
    """可编程编排替身：probe 世界观 + 信号驱动死亡状态机 + 全调用记录。

    - alive：bool（全体同状态）或 callable(pid_or_dash)（按目标区分，如
      「旧 PID 已死、新 PID 存活」）；
    - cmdline：str 或 callable(pid_or_dash)（如「旧 PID 为无关进程 cmdline」）；
    - status：probe('-') 载入的 sidecar status JSON（None = 未落档）；
    - die_after_signals：第 N 次信号后目标死亡（None = TERM/KILL 均不死）；
    - probe_error / probe_pid_error / probe_dash_error：分类注入 RunnerError
      （evaluate 前 / spawn 后身份核验 / status 等待轮询中）；
    - spawn_error / signal_errors：spawn 失败 / 指定信号送达失败。
    """

    def __init__(self, *, alive=True, cmdline=_OWN_CMDLINE, status=None,
                 probe_error=None, probe_pid_error=None, probe_dash_error=None,
                 spawn_error=None, signal_errors=None, die_after_signals=None):
        self._alive = alive
        self._cmdline = cmdline
        self._status = status
        self._probe_error = probe_error
        self._probe_pid_error = probe_pid_error
        self._probe_dash_error = probe_dash_error
        self._spawn_error = spawn_error
        self._signal_errors = dict(signal_errors or {})
        self._die_after = die_after_signals
        self._signals_sent = 0
        self.probe_calls: list[str] = []
        self.spawn_calls: list[list[str]] = []
        self.spawned: list[_FakePopenHandle] = []
        self.signal_calls: list[tuple[int, str]] = []
        self.kill_calls: list[_FakePopenHandle] = []

    def _is_alive(self, who: str) -> bool:
        if self._die_after is not None and self._signals_sent >= self._die_after:
            return False
        if callable(self._alive):
            return bool(self._alive(who))
        return bool(self._alive)

    def probe(self, pid_or_dash: str, status_relpath: str) -> dict:
        self.probe_calls.append(pid_or_dash)
        if pid_or_dash == "-":
            if self._probe_dash_error is not None:
                raise self._probe_dash_error
            return {"pid_alive": False, "cmdline": None, "status": self._status}
        if self._probe_error is not None:
            raise self._probe_error
        if self._probe_pid_error is not None:
            raise self._probe_pid_error
        alive = self._is_alive(pid_or_dash)
        cmdline = self._cmdline(pid_or_dash) if callable(self._cmdline) else self._cmdline
        return {"pid_alive": alive, "cmdline": cmdline if alive else ""}

    def spawn_sidecar(self, argv, log_path):
        self.spawn_calls.append(list(argv))
        if self._spawn_error is not None:
            raise self._spawn_error
        handle = _FakePopenHandle()
        self.spawned.append(handle)
        return handle

    def send_signal(self, pid: int, name: str) -> None:
        self.signal_calls.append((pid, name))
        self._signals_sent += 1
        error = self._signal_errors.get(name)
        if error is not None:
            raise error

    def kill_spawned(self, popen) -> None:
        self.kill_calls.append(popen)


class _FakeHealth:
    """健康探测替身：port → (状态码|None, body)；缺省 (200, ok)。"""

    def __init__(self, codes: dict[int, tuple[int | None, str]] | None = None):
        self._codes = dict(codes or {})
        self.calls: list[tuple[str, int]] = []

    def probe(self, bind: str, port: int) -> tuple[int | None, str]:
        self.calls.append((bind, port))
        return self._codes.get(port, (200, "ok"))


@pytest.fixture
def fast_timing(monkeypatch):
    """时延常量压至毫秒级：轮询/宽限/确认/落档超时全部有界且快速。"""
    monkeypatch.setattr(CTL, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(CTL, "START_STATUS_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(CTL, "STOP_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(CTL, "KILL_CONFIRM_SECONDS", 0.05)


def _artifacts_dir(tmp_path) -> Path:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    return artifacts


def _write_manifest(artifacts: Path, pid: int = 4242) -> None:
    CTL.atomic_write_json(
        CTL.safe_join(artifacts, CTL.MANIFEST_NAME), _manifest(pid).to_payload()
    )


def _fresh_status(pid: int = 5555) -> dict:
    """sidecar 落档 status JSON 的合法样本（新 PID 5555 + 双端口齐备）。"""
    return {
        "schema_version": 1, "service": "voice-health-sidecar", "pid": pid,
        "bind": "192.168.65.5", "ports": [18010, 18011],
        "started_at": "2026-09-14T21:00:00+08:00",
    }


def _fresh_start_runner() -> _LifecycleRunner:
    """全新启动成功脚本：旧世界无 manifest，spawn 后新 PID 5555 存活且身份相符。"""
    return _LifecycleRunner(
        alive=lambda who: who == "5555", cmdline=_OWN_CMDLINE, status=_fresh_status()
    )


# ---------- cmd_start：幂等 / 清理 / 拒绝 / 回收 ----------


def test_start_idempotent_skip_when_healthy_never_respawns(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)
    before = CTL.safe_join(artifacts, CTL.MANIFEST_NAME).read_text(encoding="utf-8")
    runner = _LifecycleRunner(alive=True, cmdline=_OWN_CMDLINE)
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_OK
    assert runner.spawn_calls == []  # 健康幂等：绝不重复 spawn
    assert runner.signal_calls == [] and runner.kill_calls == []
    assert CTL.safe_join(artifacts, CTL.MANIFEST_NAME).read_text(encoding="utf-8") == before
    assert "幂等跳过" in out.getvalue()


def test_start_idempotent_skip_when_alive_but_degraded(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)
    degraded = _FakeHealth({18010: (503, "loading"), 18011: (None, "timeout")})
    runner = _LifecycleRunner(alive=True, cmdline=_OWN_CMDLINE)
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, degraded, out)
    assert rc == CTL.EXIT_OK  # 存活但上游降级：视为启动中，同样幂等
    assert runner.spawn_calls == []
    assert len(degraded.calls) == 2  # 双端口都探测过才下结论
    assert "不重复 spawn" in out.getvalue()


def test_start_cleans_stale_manifest_then_fresh_start(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)  # 旧 PID 已死
    CTL.safe_join(artifacts, CTL.STATUS_NAME).write_text("OLD", encoding="utf-8")
    runner = _LifecycleRunner(  # 4242 已退出；spawn 后新 5555 存活
        alive=lambda who: who == "5555", cmdline=_OWN_CMDLINE, status=_fresh_status(),
    )
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_OK
    assert len(runner.spawn_calls) == 1
    manifest = CTL.read_manifest(CTL.safe_join(artifacts, CTL.MANIFEST_NAME))
    assert manifest is not None and manifest.pid == 5555  # manifest 已换新
    assert "stale manifest 已清理" in out.getvalue()


def test_start_cleans_reused_manifest_without_any_signal(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)
    CTL.safe_join(artifacts, CTL.STATUS_NAME).write_text("OLD", encoding="utf-8")
    runner = _LifecycleRunner(  # 4242 存活但 cmdline 为无关进程
        alive=True,
        cmdline=lambda who: "bash -c unrelated" if who == "4242" else _OWN_CMDLINE,
        status=_fresh_status(),
    )
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_OK
    assert runner.signal_calls == []  # 复用目标只清理文件，绝不发信号
    assert len(runner.spawn_calls) == 1
    manifest = CTL.read_manifest(CTL.safe_join(artifacts, CTL.MANIFEST_NAME))
    assert manifest is not None and manifest.pid == 5555
    assert "已被无关进程复用" in out.getvalue()


def test_start_corrupt_manifest_cleaned_then_fresh_start(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    CTL.safe_join(artifacts, CTL.MANIFEST_NAME).write_text("{ corrupt", encoding="utf-8")
    runner = _fresh_start_runner()
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_OK
    assert "manifest 损坏——已清理" in out.getvalue()
    manifest = CTL.read_manifest(CTL.safe_join(artifacts, CTL.MANIFEST_NAME))
    assert manifest is not None and manifest.pid == 5555


@pytest.mark.parametrize("protected_pid", [867, 26008])
def test_start_refuses_protected_pid_manifest_without_probing(
    tmp_path, protected_pid
) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=protected_pid)
    runner = _LifecycleRunner(alive=True, cmdline=_OWN_CMDLINE)
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_REFUSED
    assert runner.probe_calls == []  # 保护清单先于一切探测：零 spawn、零信号
    assert runner.spawn_calls == [] and runner.signal_calls == []
    assert CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()  # manifest 保留
    assert str(protected_pid) in out.getvalue()


def test_start_refuses_protected_marker_manifest(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)
    runner = _LifecycleRunner(alive=True, cmdline="python3 /opt/cosyvoice/server.py")
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_REFUSED
    assert runner.spawn_calls == [] and runner.signal_calls == []
    assert CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()  # 不 spawn、manifest 保留


def test_start_runner_error_before_spawn_is_classified_refused(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)
    runner = _LifecycleRunner(
        probe_error=CTL.RunnerError(CTL.WSL_MANAGEMENT_UNAVAILABLE, "probe rc=1")
    )
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_REFUSED
    assert CTL.WSL_MANAGEMENT_UNAVAILABLE in out.getvalue()  # 统一分类可见
    assert runner.spawn_calls == []
    assert CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()  # manifest 保留


def test_start_spawn_runner_error_refused_single_attempt(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    runner = _LifecycleRunner(
        spawn_error=CTL.RunnerError(CTL.WSL_MANAGEMENT_UNAVAILABLE, "wsl.exe 启动失败")
    )
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_REFUSED
    assert "不重试" in out.getvalue()
    assert not CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()


def test_start_fresh_success_writes_manifest_and_releases_lock(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    runner = _fresh_start_runner()
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_OK
    assert runner.kill_calls == []  # 成功路径不回收句柄
    rel = CTL.status_file_relpath(artifacts, CTL.REPO_ROOT)
    argv = runner.spawn_calls[0]
    assert argv == CTL.build_start_argv(CTL.REPO_ROOT, rel)  # 固定命令形态
    manifest = CTL.read_manifest(CTL.safe_join(artifacts, CTL.MANIFEST_NAME))
    assert manifest is not None
    assert (manifest.pid, manifest.bind, manifest.ports) == (
        5555, "192.168.65.5", [18010, 18011]
    )
    assert "sidecar 已启动" in out.getvalue()
    assert not CTL.safe_join(artifacts, CTL.LOCK_NAME).exists()  # 锁已释放


def test_start_status_timeout_kills_spawned_handle_only(tmp_path, fast_timing) -> None:
    artifacts = _artifacts_dir(tmp_path)
    runner = _LifecycleRunner(status=None)  # sidecar 始终未落 status
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_ERROR
    assert runner.kill_calls == runner.spawned  # 只回收本次 spawn 的句柄
    assert not CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()
    assert "未落 status 文件" in out.getvalue()


@pytest.mark.parametrize(
    "status,marker",
    [
        ({"pid": "abc", "bind": "192.168.65.5", "ports": [18010, 18011]},
         "status 文件结构异常"),
        (_fresh_status(pid=26008), "status 事实异常"),
        (_fresh_status(pid=867), "status 事实异常"),
        ({"pid": 5555, "bind": "192.168.65.5", "ports": [18010]},
         "status 事实异常"),
    ],
    ids=["pid-non-int", "protected-26008", "protected-867", "ports-mismatch"],
)
def test_start_invalid_status_facts_kill_spawned_handle(
    tmp_path, status, marker
) -> None:
    artifacts = _artifacts_dir(tmp_path)
    runner = _LifecycleRunner(status=status)
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_ERROR
    assert runner.kill_calls == runner.spawned
    assert marker in out.getvalue()
    assert not CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()


def test_start_identity_mismatch_after_spawn_kills_handle(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    runner = _LifecycleRunner(
        alive=True, cmdline="bash -c unrelated", status=_fresh_status()
    )
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_ERROR
    assert runner.kill_calls == runner.spawned  # 身份核验未通过 → 回收本次句柄
    assert runner.signal_calls == []  # 落档 PID 可能指向无关进程：绝不发信号
    assert "身份核验未通过" in out.getvalue()
    assert not CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()


def test_start_runner_error_at_identity_probe_refused_after_kill(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    runner = _LifecycleRunner(
        status=_fresh_status(),
        probe_pid_error=CTL.RunnerError(CTL.WSL_MANAGEMENT_UNAVAILABLE, "probe rc=3"),
    )
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_REFUSED  # spawn 后 WSL 管理面失败：分类拒绝
    assert runner.kill_calls == runner.spawned
    assert not CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()


def test_start_runner_error_during_status_wait_is_classified_not_raised(
    tmp_path, fast_timing
) -> None:
    """模块头契约：WSL 管理面失败统一分类 rc 3 拒绝（含 spawn 后 status 等待中）。

    spawn 后轮询 status 途中 wsl.exe 不可用：必须回收本次句柄并以
    EXIT_REFUSED 分类返回，绝不让 RunnerError 裸穿（否则句柄泄漏）。
    """
    artifacts = _artifacts_dir(tmp_path)
    runner = _LifecycleRunner(
        probe_dash_error=CTL.RunnerError(CTL.WSL_MANAGEMENT_UNAVAILABLE, "probe rc=1")
    )
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_REFUSED
    assert runner.kill_calls == runner.spawned
    assert CTL.WSL_MANAGEMENT_UNAVAILABLE in out.getvalue()
    assert "无法核验" in out.getvalue()  # 泄漏评估如实可见：不盲发信号


# ---------- 第三片：Linux 子进程有界补回收（身份核验通过才单次 TERM） ----------


def test_start_bad_facts_terms_identity_verified_linux_child(tmp_path) -> None:
    """Linux 子进程泄漏评估：kill wsl.exe 不保证 WSL 内子进程终止。

    status 事实异常分支已有可核验 PID：探活 + 身份标记全过 → 对本次
    启动落档的 Linux sidecar 补发**单次 SIGTERM**（零宽限、零 KILL、
    零重试），不扩大终止面。
    """
    artifacts = _artifacts_dir(tmp_path)
    runner = _LifecycleRunner(
        status={"pid": 5555, "bind": "192.168.65.5", "ports": [18010]}
    )
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_ERROR
    assert runner.kill_calls == runner.spawned  # wsl.exe 句柄照常回收
    assert runner.signal_calls == [(5555, "SIGTERM")]  # 有界补回收：单次 TERM
    assert "补发单次 SIGTERM" in out.getvalue()


def test_start_linux_child_term_skipped_when_verification_unavailable(
    tmp_path,
) -> None:
    """核验不可用即放弃 Linux 侧回收并记录：盲发信号比泄漏更危险。"""
    artifacts = _artifacts_dir(tmp_path)
    runner = _LifecycleRunner(
        status={"pid": 5555, "bind": "192.168.65.5", "ports": [18010]},
        probe_pid_error=CTL.RunnerError(CTL.WSL_MANAGEMENT_UNAVAILABLE, "probe rc=1"),
    )
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_ERROR
    assert runner.kill_calls == runner.spawned
    assert runner.signal_calls == []
    assert "无法核验" in out.getvalue()


def test_start_protected_pid_in_status_facts_gets_no_linux_signal(tmp_path) -> None:
    """保护清单在 Linux 侧回收中同样生效：落档 PID 867/26008 绝不发信号。"""
    artifacts = _artifacts_dir(tmp_path)
    runner = _LifecycleRunner(status=_fresh_status(pid=867))
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_ERROR
    assert runner.kill_calls == runner.spawned
    assert runner.signal_calls == []
    assert "生产保护清单" in out.getvalue()


def test_start_manifest_write_failure_terms_verified_child(
    tmp_path, monkeypatch
) -> None:
    """manifest 写入被拒分支：身份已核验 → 同样有界补发单次 SIGTERM。"""

    def _refuse(path, payload):
        raise CTL.UnsafePathError("拒绝写入非普通文件目标")

    monkeypatch.setattr(CTL, "atomic_write_json", _refuse)
    artifacts = _artifacts_dir(tmp_path)
    runner = _fresh_start_runner()
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_ERROR
    assert runner.kill_calls == runner.spawned
    assert runner.signal_calls == [(5555, "SIGTERM")]
    assert "manifest 写入被拒" in out.getvalue()


def test_start_linux_child_term_failure_keeps_exit_code(tmp_path) -> None:
    """补回收 TERM 送达失败：best-effort——不改退出码、不升级 KILL。"""
    artifacts = _artifacts_dir(tmp_path)
    runner = _LifecycleRunner(
        status={"pid": 5555, "bind": "192.168.65.5", "ports": [18010]},
        signal_errors={"SIGTERM": CTL.RunnerError(
            CTL.WSL_MANAGEMENT_UNAVAILABLE, "signal rc=1")},
    )
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_ERROR  # 原退出码不受 best-effort 回收影响
    assert runner.kill_calls == runner.spawned
    assert runner.signal_calls == [(5555, "SIGTERM")]  # 不升级 KILL、不重试
    assert "未送达" in out.getvalue()


# ---------- cmd_stop：TERM / KILL / 保护 / 拒绝 ----------


def test_stop_without_manifest_is_idempotent_ok(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    CTL.safe_join(artifacts, CTL.STATUS_NAME).write_text("{}", encoding="utf-8")
    runner = _LifecycleRunner(alive=True)
    out = io.StringIO()
    rc = CTL.cmd_stop(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_OK
    assert runner.signal_calls == []  # 无 manifest 绝不发信号
    assert not CTL.safe_join(artifacts, CTL.STATUS_NAME).exists()  # 残留清理
    assert "无需停止" in out.getvalue()


def test_stop_term_graceful_exit_cleans_artifacts(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)
    CTL.safe_join(artifacts, CTL.STATUS_NAME).write_text("{}", encoding="utf-8")
    runner = _LifecycleRunner(alive=True, cmdline=_OWN_CMDLINE, die_after_signals=1)
    out = io.StringIO()
    rc = CTL.cmd_stop(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_OK
    assert runner.signal_calls == [(4242, "SIGTERM")]  # 仅单次 TERM
    assert not CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()
    assert not CTL.safe_join(artifacts, CTL.STATUS_NAME).exists()
    assert "已优雅退出" in out.getvalue()


def test_stop_term_timeout_then_single_kill_fallback(tmp_path, fast_timing) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)
    runner = _LifecycleRunner(alive=True, cmdline=_OWN_CMDLINE, die_after_signals=2)
    out = io.StringIO()
    rc = CTL.cmd_stop(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_OK
    assert runner.signal_calls == [(4242, "SIGTERM"), (4242, "SIGKILL")]  # KILL 仅一次
    assert not CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()
    assert "单次 KILL 兜底" in out.getvalue()


def test_stop_survives_kill_keeps_manifest_for_retry(tmp_path, fast_timing) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)
    CTL.safe_join(artifacts, CTL.STATUS_NAME).write_text("{}", encoding="utf-8")
    runner = _LifecycleRunner(alive=True, cmdline=_OWN_CMDLINE)  # TERM/KILL 均不死
    out = io.StringIO()
    rc = CTL.cmd_stop(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_ERROR
    assert runner.signal_calls == [(4242, "SIGTERM"), (4242, "SIGKILL")]
    assert CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()  # 保留以便重试
    assert CTL.safe_join(artifacts, CTL.STATUS_NAME).exists()
    assert "仍存活" in out.getvalue()


def test_stop_term_delivery_failure_refused_keeps_manifest(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)
    runner = _LifecycleRunner(
        alive=True, cmdline=_OWN_CMDLINE,
        signal_errors={"SIGTERM": CTL.RunnerError(
            CTL.WSL_MANAGEMENT_UNAVAILABLE, "signal rc=1")},
    )
    out = io.StringIO()
    rc = CTL.cmd_stop(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_REFUSED
    assert runner.signal_calls == [(4242, "SIGTERM")]  # 不会升级发 KILL
    assert CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()
    assert "TERM 信号未确认送达" in out.getvalue()


def test_stop_kill_delivery_failure_refused_keeps_manifest(tmp_path, fast_timing) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)
    runner = _LifecycleRunner(
        alive=True, cmdline=_OWN_CMDLINE,  # TERM 不致死 → 走 KILL，KILL 送达失败
        signal_errors={"SIGKILL": CTL.RunnerError(
            CTL.WSL_MANAGEMENT_UNAVAILABLE, "signal rc=1")},
    )
    out = io.StringIO()
    rc = CTL.cmd_stop(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_REFUSED
    assert runner.signal_calls == [(4242, "SIGTERM"), (4242, "SIGKILL")]
    assert CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()
    assert "KILL 信号未确认送达" in out.getvalue()


@pytest.mark.parametrize("protected_pid", [867, 26008])
def test_stop_protected_pids_never_signaled(tmp_path, protected_pid) -> None:
    """生产保护硬边界：FunASR 867 / CosyVoice 26008 绝不成为终止目标。"""
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=protected_pid)
    CTL.safe_join(artifacts, CTL.STATUS_NAME).write_text("{}", encoding="utf-8")
    runner = _LifecycleRunner(alive=True, cmdline=_OWN_CMDLINE)
    out = io.StringIO()
    rc = CTL.cmd_stop(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_REFUSED
    assert runner.signal_calls == []  # 零信号（含零 TERM、零 KILL）
    assert runner.probe_calls == []  # 保护清单先于探测
    assert CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()
    assert "绝不成为终止目标" in out.getvalue()


def test_stop_protected_marker_cmdline_refused(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)
    runner = _LifecycleRunner(alive=True, cmdline="wslrelay --forward 8010")
    out = io.StringIO()
    rc = CTL.cmd_stop(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_REFUSED
    assert runner.signal_calls == []
    assert CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()


def test_stop_stale_manifest_cleans_without_signal(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)
    CTL.safe_join(artifacts, CTL.STATUS_NAME).write_text("{}", encoding="utf-8")
    runner = _LifecycleRunner(alive=False)  # PID 已退出
    out = io.StringIO()
    rc = CTL.cmd_stop(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_OK
    assert runner.signal_calls == []
    assert not CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()
    assert not CTL.safe_join(artifacts, CTL.STATUS_NAME).exists()
    assert "stale manifest 已清理" in out.getvalue()


def test_stop_reused_pid_refused_without_signal(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)
    runner = _LifecycleRunner(alive=True, cmdline="bash -c unrelated")
    out = io.StringIO()
    rc = CTL.cmd_stop(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_REFUSED
    assert runner.signal_calls == []  # 身份不符绝不发信号
    assert CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()


def test_stop_runner_error_refused_keeps_manifest(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)
    runner = _LifecycleRunner(
        probe_error=CTL.RunnerError(CTL.WSL_MANAGEMENT_UNAVAILABLE, "probe rc=1")
    )
    out = io.StringIO()
    rc = CTL.cmd_stop(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_REFUSED
    assert runner.signal_calls == []
    assert CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()  # 保留以便重试
    assert "WSL 管理面不可用" in out.getvalue()


# ---------- cmd_status：只读零清理 ----------


def test_status_without_manifest_reports_stopped_and_never_writes(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    CTL.safe_join(artifacts, CTL.STATUS_NAME).write_text("{}", encoding="utf-8")
    runner = _LifecycleRunner(alive=True)
    out = io.StringIO()
    rc = CTL.cmd_status(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_OK
    assert "state: stopped" in out.getvalue()
    assert CTL.safe_join(artifacts, CTL.STATUS_NAME).exists()  # 残留不清理
    assert not CTL.safe_join(artifacts, CTL.LOG_NAME).exists()  # 日志也不追加


@pytest.mark.parametrize("protected_pid", [867, 26008])
def test_status_protected_pid_reports_protected_without_probing(
    tmp_path, protected_pid
) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=protected_pid)
    runner = _LifecycleRunner(alive=True, cmdline=_OWN_CMDLINE)
    out = io.StringIO()
    rc = CTL.cmd_status(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_OK
    assert "protected-target" in out.getvalue()
    assert runner.probe_calls == []
    assert CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()


def test_status_corrupt_manifest_reports_corrupt(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    CTL.safe_join(artifacts, CTL.MANIFEST_NAME).write_text("{ corrupt", encoding="utf-8")
    runner = _LifecycleRunner(alive=True)
    out = io.StringIO()
    rc = CTL.cmd_status(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_OK
    assert "manifest-损坏" in out.getvalue()
    assert CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()  # 只读不清理


def test_status_symlink_manifest_reports_unsafe(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    outside = tmp_path / "outside-manifest.json"
    outside.write_text(_manifest().to_json(), encoding="utf-8")
    _make_symlink_or_skip(outside, CTL.safe_join(artifacts, CTL.MANIFEST_NAME))
    out = io.StringIO()
    rc = CTL.cmd_status(artifacts, _LifecycleRunner(), _FakeHealth(), out)
    assert rc == CTL.EXIT_OK
    assert "manifest-unsafe" in out.getvalue()


@pytest.mark.parametrize(
    "runner_factory,state",
    [
        (lambda: _LifecycleRunner(alive=False), "state: stale"),
        (lambda: _LifecycleRunner(alive=True, cmdline="bash -c unrelated"),
         "state: pid-reused"),
        (lambda: _LifecycleRunner(
            probe_error=CTL.RunnerError(CTL.WSL_MANAGEMENT_UNAVAILABLE, "rc=1")),
         "state: unknown"),
    ],
    ids=["dead", "reused", "runner-error"],
)
def test_status_classification_states_are_read_only(
    tmp_path, runner_factory, state
) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)
    out = io.StringIO()
    rc = CTL.cmd_status(artifacts, runner_factory(), _FakeHealth(), out)
    assert rc == CTL.EXIT_OK
    assert state in out.getvalue()
    assert CTL.safe_join(artifacts, CTL.MANIFEST_NAME).exists()  # 零清理


def test_status_own_healthy_and_degraded_report_codes(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    _write_manifest(artifacts, pid=4242)
    healthy_out = io.StringIO()
    healthy = _FakeHealth()
    assert CTL.cmd_status(artifacts, _LifecycleRunner(alive=True, cmdline=_OWN_CMDLINE),
                          healthy, healthy_out) == CTL.EXIT_OK
    assert "running-healthy" in healthy_out.getvalue()
    assert healthy.calls == [("192.168.65.5", 18010), ("192.168.65.5", 18011)]

    degraded_out = io.StringIO()
    degraded = _FakeHealth({18010: (503, "loading"), 18011: (None, "timeout")})
    assert CTL.cmd_status(artifacts, _LifecycleRunner(alive=True, cmdline=_OWN_CMDLINE),
                          degraded, degraded_out) == CTL.EXIT_OK
    assert "running-degraded" in degraded_out.getvalue()
    assert "503" in degraded_out.getvalue() and "None" in degraded_out.getvalue()


# ---------- ControlLock：并发拒绝 + 陈旧回收 ----------


def test_concurrent_lock_refuses_start_and_stop_without_side_effects(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    lock = CTL.safe_join(artifacts, CTL.LOCK_NAME)
    lock.write_text("2026-09-14T21:00:00+08:00 pid=999\n", encoding="utf-8")
    for command in (CTL.cmd_start, CTL.cmd_stop):
        runner = _LifecycleRunner(alive=True, cmdline=_OWN_CMDLINE)
        out = io.StringIO()
        rc = command(artifacts, runner, _FakeHealth(), out)
        assert rc == CTL.EXIT_REFUSED
        assert runner.probe_calls == [] and runner.spawn_calls == []
        assert runner.signal_calls == []  # 锁竞争下零副作用
        assert "拒绝并发" in out.getvalue()
    assert lock.read_text(encoding="utf-8").startswith("2026-09-14")  # 他人锁不动


def test_stale_lock_reclaimed_and_released(tmp_path) -> None:
    artifacts = _artifacts_dir(tmp_path)
    lock = CTL.safe_join(artifacts, CTL.LOCK_NAME)
    lock.write_text("2026-09-13T00:00:00+08:00 pid=999\n", encoding="utf-8")
    old = lock.stat().st_mtime - CTL.LOCK_STALE_SECONDS - 60  # 超过 TTL 的陈旧锁
    os.utime(lock, (old, old))
    runner = _fresh_start_runner()
    out = io.StringIO()
    rc = CTL.cmd_start(artifacts, runner, _FakeHealth(), out)
    assert rc == CTL.EXIT_OK  # 陈旧锁回收后正常放行
    assert "陈旧控制锁已回收" in out.getvalue()
    assert not lock.exists()  # 完成后释放
