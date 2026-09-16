"""M14-36 verify_web_livekit_client.py 进程生命周期契约测试（全 mock，零真实杀进程）。

背景缺陷（M14-36，Windows 实测）：旧版 start_web 以 npm 包装器（mise/npm.cmd
shim 链）作为 Popen 对象，main 的 finally 只对该包装器 PID terminate/kill——
Windows 上进程终止不级联子进程，next start 的 node.exe 与中间 shim 存活为孤儿；
跨次验收累计 26 个 node.exe/mise.exe 孤儿进程，锁死 worktree 文件句柄并占用
资源（缺陷由监督者在既往验收后盘点发现）。

本套件锁定修复后的契约（全部 monkeypatch subprocess/os，绝不真正执行
taskkill/killpg/Popen，不触网、不装依赖、不读 secret）：
1  resolve_node_executable：以 `node -p process.execPath` 解析真实 node
   可执行文件（mise/npm shim 不再是长命父进程）；任何失败（非零退出码/空
   输出/找不到 node/探测超时）→ SystemExit fail-closed；
2  start_web：直接以 [真实node, node_modules/next/dist/bin/next, start,
   -p, <port>] 启动（不经 npm run start 包装链）；POSIX 以
   start_new_session=True 自成进程组，Windows 不设；
3  stop_process_tree：
   - 进程已退出 → 不发起任何 kill；
   - Windows → 恰好一次 taskkill，argv 精确为 [/PID <pid>, /T, /F]（只针对
     Popen PID 的进程树，绝不按端口/进程名扫描），有界超时 + check=False，
     随后有界 wait 回收 Popen 句柄；
   - POSIX → 对 pgid==Popen pid 先 SIGTERM(15)、有界等待超时后 SIGKILL(9)，
     组已消亡（ProcessLookupError）被容忍并以 kill() 兜底；
4  文本契约：源码含直启/树杀锚点，且不含按名/端口扫杀模式（/IM、pkill、
   killall、netstat、Get-Process、wmic 等）。

真实浏览器验收（对生产栈零重启）是运维显式动作，本套件不发任何网络请求。
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "infra" / "verify_web_livekit_client.py"
FAKE_NODE = "C:/Tools/node-v22/node.exe"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_web_livekit_client", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def vwlc():
    return _load_module()


SOURCE = SCRIPT.read_text(encoding="utf-8")


class _FakePopen:
    """最小 Popen 替身：记录 argv/kwargs/wait/kill 调用，wait 结果可编排。"""

    def __init__(self, pid=4242, poll_result=None, wait_outcomes=None):
        self.pid = pid
        self.poll_result = poll_result
        self.wait_outcomes = list(wait_outcomes or [])
        self.wait_timeouts: list[float | None] = []
        self.kill_called = False
        self.terminate_called = False
        self.argv = None
        self.kwargs = None

    def capture(self, argv, kwargs):
        self.argv = argv
        self.kwargs = kwargs

    def poll(self):
        return self.poll_result

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if self.wait_outcomes:
            outcome = self.wait_outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return 0

    def kill(self):
        self.kill_called = True

    def terminate(self):
        self.terminate_called = True


def _timeout() -> subprocess.TimeoutExpired:
    return subprocess.TimeoutExpired(cmd="next start", timeout=15)


# ---------- resolve_node_executable：真实 node 解析（去 mise/npm shim） ----------


def test_resolve_node_uses_execpath_probe(vwlc, monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=f"  {FAKE_NODE}\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert vwlc.resolve_node_executable() == FAKE_NODE
    argv, kwargs = calls[0]
    assert argv == ["node", "-p", "process.execPath"]
    assert kwargs.get("check") is False
    assert kwargs.get("capture_output") is True
    assert 0 < kwargs.get("timeout", 0) <= 120  # 有界探测


@pytest.mark.parametrize("stdout,returncode", [("", 0), ("node.exe", 1), ("  \n", 0)])
def test_resolve_node_fail_closed_on_bad_output(vwlc, monkeypatch, stdout, returncode):
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, returncode, stdout=stdout),
    )
    with pytest.raises(SystemExit):
        vwlc.resolve_node_executable()


def test_resolve_node_fail_closed_on_missing_or_hung(vwlc, monkeypatch):
    def raise_missing(argv, **kwargs):
        raise FileNotFoundError("node")

    def raise_timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd="node", timeout=60)

    for fake in (raise_missing, raise_timeout):
        monkeypatch.setattr(subprocess, "run", fake)
        with pytest.raises(SystemExit):
            vwlc.resolve_node_executable()


# ---------- start_web：真实 node 直启 next CLI（不经 npm 包装链） ----------


def _patch_start_env(vwlc, monkeypatch, popen, next_bin):
    next_bin.parent.mkdir(parents=True, exist_ok=True)
    next_bin.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    monkeypatch.setattr(vwlc, "next_bin", lambda: next_bin)
    monkeypatch.setattr(vwlc, "resolve_node_executable", lambda: FAKE_NODE)
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: (popen.capture(argv, kw), popen)[1])
    monkeypatch.setattr(vwlc, "http_status", lambda url, timeout=5.0: 200)


def test_next_bin_candidates_cover_workspace_and_root(vwlc):
    # 本仓库为 npm workspaces：依赖提升到仓库根；同时兼容 workspace 本地
    assert vwlc.NEXT_BIN_CANDIDATES == (
        vwlc.REPO_ROOT / "node_modules" / "next" / "dist" / "bin" / "next",
        vwlc.REPO_ROOT / "apps" / "web" / "node_modules" / "next" / "dist" / "bin" / "next",
    )


def test_next_bin_prefers_first_existing_candidate(vwlc, monkeypatch, tmp_path):
    first, second = tmp_path / "root-next", tmp_path / "local-next"
    second.write_text("", encoding="utf-8")  # 只有第二个存在
    monkeypatch.setattr(vwlc, "NEXT_BIN_CANDIDATES", (first, second))
    assert vwlc.next_bin() == second
    first.write_text("", encoding="utf-8")  # 两个都在 → 取第一个
    assert vwlc.next_bin() == first


def test_next_bin_fail_closed_when_missing(vwlc, monkeypatch, tmp_path):
    monkeypatch.setattr(vwlc, "NEXT_BIN_CANDIDATES", (tmp_path / "a", tmp_path / "b"))
    with pytest.raises(SystemExit):
        vwlc.next_bin()


def test_start_web_starts_next_directly_with_real_node(vwlc, monkeypatch, tmp_path):
    popen = _FakePopen()
    _patch_start_env(vwlc, monkeypatch, popen, tmp_path / "next")
    assert vwlc.start_web(3987) is popen
    argv = popen.argv
    assert argv[0] == FAKE_NODE  # 真实 node（非 mise/npm shim / .cmd 包装）
    assert not argv[0].lower().endswith((".cmd", ".bat", "mise.exe"))
    assert argv[1].replace("\\", "/").endswith("next/dist/bin/next") or argv[1].endswith(
        str(tmp_path / "next")
    )  # next CLI（node 脚本；测试注入路径或真实候选）
    assert argv[2:] == ["start", "-p", "3987"]
    assert "npm" not in " ".join(argv).lower()  # 启动链不经 npm
    assert popen.kwargs["cwd"] == vwlc.REPO_ROOT / "apps" / "web"


def test_start_web_posix_starts_new_session(vwlc, monkeypatch, tmp_path):
    popen = _FakePopen()
    _patch_start_env(vwlc, monkeypatch, popen, tmp_path / "next")
    monkeypatch.setattr(vwlc, "_is_windows", lambda: False)
    vwlc.start_web(3987)
    assert popen.kwargs.get("start_new_session") is True  # 自成进程组（pgid==pid）


def test_start_web_windows_no_new_session(vwlc, monkeypatch, tmp_path):
    popen = _FakePopen()
    _patch_start_env(vwlc, monkeypatch, popen, tmp_path / "next")
    monkeypatch.setattr(vwlc, "_is_windows", lambda: True)
    vwlc.start_web(3987)
    assert not popen.kwargs.get("start_new_session")  # Windows 无进程组语义


# ---------- stop_process_tree：仅 Popen PID 树的有界精确回收 ----------


def test_stop_tree_windows_taskkill_exact_pid_tree(vwlc, monkeypatch):
    monkeypatch.setattr(vwlc, "_is_windows", lambda: True)
    run_calls = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, **kw: (run_calls.append((argv, kw)), subprocess.CompletedProcess(argv, 0))[1],
    )
    killpg_calls = []
    monkeypatch.setattr(vwlc.os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)), raising=False)
    proc = _FakePopen(pid=4242, poll_result=None)
    vwlc.stop_process_tree(proc)
    assert len(run_calls) == 1
    argv, kwargs = run_calls[0]
    assert argv == ["taskkill", "/PID", "4242", "/T", "/F"]  # 仅该 PID 的树
    assert kwargs.get("check") is False
    assert kwargs.get("capture_output") is True
    assert 0 < kwargs.get("timeout", 0) <= 60  # 有界
    assert killpg_calls == []  # Windows 不走 POSIX 组杀
    assert proc.wait_timeouts  # Popen 句柄有界回收
    assert not proc.kill_called and not proc.terminate_called


@pytest.mark.parametrize("is_windows", [True, False])
def test_stop_tree_already_exited_touches_nothing(vwlc, monkeypatch, is_windows):
    monkeypatch.setattr(vwlc, "_is_windows", lambda: is_windows)
    run_calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: run_calls.append((argv, kw)))
    killpg_calls = []
    monkeypatch.setattr(vwlc.os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)), raising=False)
    proc = _FakePopen(poll_result=0)
    vwlc.stop_process_tree(proc)
    assert run_calls == []
    assert killpg_calls == []
    assert proc.wait_timeouts == []
    assert not proc.kill_called


def test_stop_tree_posix_sigterm_then_sigkill_on_timeout(vwlc, monkeypatch):
    monkeypatch.setattr(vwlc, "_is_windows", lambda: False)
    killpg_calls = []
    monkeypatch.setattr(vwlc.os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)), raising=False)
    run_calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: run_calls.append((argv, kw)))
    proc = _FakePopen(pid=5151, wait_outcomes=[_timeout()])
    vwlc.stop_process_tree(proc)
    # pgid == Popen pid；先 SIGTERM(15)，宽限超时后 SIGKILL(9)
    assert killpg_calls == [(5151, 15), (5151, 9)]
    assert run_calls == []  # POSIX 不走 taskkill
    assert proc.wait_timeouts
    assert not proc.kill_called


def test_stop_tree_posix_graceful_term_needs_no_sigkill(vwlc, monkeypatch):
    monkeypatch.setattr(vwlc, "_is_windows", lambda: False)
    killpg_calls = []
    monkeypatch.setattr(vwlc.os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)), raising=False)
    proc = _FakePopen(pid=5152, wait_outcomes=[0])
    vwlc.stop_process_tree(proc)
    assert killpg_calls == [(5152, 15)]


def test_stop_tree_posix_group_gone_is_tolerated(vwlc, monkeypatch):
    monkeypatch.setattr(vwlc, "_is_windows", lambda: False)

    def killpg_gone(pgid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(vwlc.os, "killpg", killpg_gone, raising=False)
    proc = _FakePopen(pid=5153, wait_outcomes=[_timeout(), _timeout()])
    vwlc.stop_process_tree(proc)  # 不抛异常；组已消亡时 kill() 兜底
    assert proc.kill_called


# ---------- 文本契约：直启/树杀锚点存在，按名/端口扫杀模式不存在 ----------


def test_source_has_lifecycle_anchors():
    for anchor in (
        "process.execPath",
        "start_new_session",
        "taskkill",
        "/T",
        "/F",
        "killpg",
        "stop_process_tree(proc)",  # main 的 finally 必须走树回收
    ):
        assert anchor in SOURCE, anchor


def test_source_forbids_port_or_name_based_kills():
    for forbidden in (
        "taskkill /IM",
        "pkill",
        "killall",
        "netstat",
        "Get-Process",
        "Stop-Process",
        "Get-NetTCPConnection",
        "wmic",
    ):
        assert forbidden not in SOURCE, forbidden


def test_source_start_not_via_npm_wrapper():
    assert 'npm(), "run", "start"' not in SOURCE  # 旧缺陷形态必须消失


# ---------- M14-37：AIOS_LIVEKIT_BROWSER_LOOPBACK 严格开关（默认/受控拓扑） ----------
# 背景阻塞（M14-35）：默认浏览器（无 Chromium loopback 受控 flag）连本机
# loopback LiveKit 存在间歇性 ICE 失败。本开关把「受控验收拓扑」（加 flag）
# 与「默认拓扑」（绝不加 flag，代表生产用户浏览器）拆成显式模式：
#   AIOS_LIVEKIT_BROWSER_LOOPBACK 未设/0 → default（绝不注入 loopback flag）
#   AIOS_LIVEKIT_BROWSER_LOOPBACK=1      → controlled（注入，复现 M14-35 受控验收）
#   其他任何值                            → ENV-BLOCKED fail-closed（拒绝执行）
# results.json 必须记录模式与 Chromium argv 摘要（可审计），且绝不泄露 JWT/token。

LOOPBACK_FLAG = "--allow-loopback-in-peer-connection"


def test_loopback_switch_default_off_without_env(vwlc, monkeypatch):
    """未设置 env → 默认模式（False）：默认拓扑即生产用户浏览器语义。"""
    monkeypatch.delenv(vwlc.LOOPBACK_ENV, raising=False)
    assert vwlc.browser_loopback_enabled() is False
    assert LOOPBACK_FLAG not in vwlc.chromium_launch_args(False)


def test_loopback_switch_explicit_zero_still_off(vwlc, monkeypatch):
    monkeypatch.setenv(vwlc.LOOPBACK_ENV, "0")
    assert vwlc.browser_loopback_enabled() is False
    assert LOOPBACK_FLAG not in vwlc.chromium_launch_args(
        vwlc.browser_loopback_enabled()
    )


def test_loopback_switch_one_is_controlled_mode(vwlc, monkeypatch):
    monkeypatch.setenv(vwlc.LOOPBACK_ENV, "1")
    assert vwlc.browser_loopback_enabled() is True
    args = vwlc.chromium_launch_args(True)
    assert LOOPBACK_FLAG in args
    assert args.count(LOOPBACK_FLAG) == 1  # 恰好一次，不重复注入
    # 受控模式只是叠加 flag，基础 fake 麦克风参数保留
    assert "--use-fake-device-for-media-stream" in args
    assert "--use-fake-ui-for-media-stream" in args


@pytest.mark.parametrize(
    "raw", ["true", "TRUE", "yes", "on", "", " ", "2", "01", "0 ", "1\n"]
)
def test_loopback_switch_illegal_value_fails_closed(vwlc, monkeypatch, raw):
    """非 0/1 的一律拒绝执行（fail-closed），绝不静默当默认值继续跑。"""
    monkeypatch.setenv(vwlc.LOOPBACK_ENV, raw)
    with pytest.raises(SystemExit):
        vwlc.browser_loopback_enabled()
    # env 映射显式传入路径同样 fail-closed（同一条解析逻辑）
    with pytest.raises(SystemExit):
        vwlc.browser_loopback_enabled({vwlc.LOOPBACK_ENV: raw})


def test_chromium_launch_args_pure_and_ordered(vwlc):
    """参数构造是纯函数：默认两条 + 受控模式追加第三条。"""
    assert vwlc.chromium_launch_args(False) == [
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
    ]
    assert vwlc.chromium_launch_args(True) == [
        "--use-fake-device-for-media-stream",
        "--use-fake-ui-for-media-stream",
        LOOPBACK_FLAG,
    ]


def test_browser_report_records_mode_and_argv_summary(vwlc):
    """results.json 的 browser 段：模式名（default/controlled）+ argv 摘要。"""
    default_report = vwlc.browser_report(False)
    assert default_report["mode"] == "default"
    assert default_report["loopback_flag_present"] is False
    assert LOOPBACK_FLAG not in default_report["chromium_launch_args"]
    controlled = vwlc.browser_report(True)
    assert controlled["mode"] == "controlled"
    assert controlled["loopback_flag_present"] is True
    assert LOOPBACK_FLAG in controlled["chromium_launch_args"]
    assert controlled["loopback_env_var"] == "AIOS_LIVEKIT_BROWSER_LOOPBACK"


def test_browser_report_never_leaks_jwt_or_token(vwlc):
    """证据记录不泄露 JWT/token：browser 段只含模式与 argv，无凭据形态串。"""
    import json

    for report in (vwlc.browser_report(False), vwlc.browser_report(True)):
        blob = json.dumps(report, ensure_ascii=False)
        assert vwlc.JWT_RE.search(blob) is None
        # 真实否定断言（替换原「or report 占位恒真」写法）：键与值全序列化后
        # 不得出现 token/JWT/secret 字样——锁死报告结构不得新增凭据类字段
        assert "token" not in blob.lower()
        assert "jwt" not in blob.lower()
        assert "secret" not in blob.lower()


def test_sanitize_redacts_jwt_forms(vwlc):
    """落盘脱敏：JWT 三段形态串必须被占位符替换（含中文上下文）。"""
    dirty = (
        "connect failed at eyJhbGciOiJIUzI1NiJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c step"
    )
    cleaned = vwlc.sanitize(dirty)
    assert "eyJhbGci" not in cleaned
    assert "[REDACTED-JWT]" in cleaned
    assert "connect failed at" in cleaned  # 上下文保留，仅凭据替换


def test_source_loopback_flag_literal_only_in_constant():
    """flag 字面量全源码只出现一次（常量定义处）：launch 调用只引用常量，
    杜绝任何旁路硬编码再次把受控 flag 带进默认模式。"""
    assert SOURCE.count(LOOPBACK_FLAG) == 1, "loopback flag 字面量必须唯一定义于常量"


def test_source_results_records_browser_section():
    """main 落盘 results.json 前必须并入 browser 段（模式可审计）。"""
    assert 'results["browser"] = browser_report(loopback)' in SOURCE
    assert "browser_loopback_enabled()" in SOURCE  # main 显式解析模式（fail-closed 入口）


# ---------- M14-38：LiveKit 信令面探测（契约派生 + 显式 override + fail-closed） ----------
# 旧版 preflight 硬编码探测本机 loopback 信令端口——LAN cutover（媒体面绑本机
# LAN IP）拓扑下探测目标错误。新契约：
#   AIOS_PUBLIC_LIVEKIT_URL 未设置 → 登录后 POST /api/v1/voice/token 响应 ws_url 契约派生；
#   AIOS_PUBLIC_LIVEKIT_URL 已设置 → 严格校验（ws/wss + 非空主机名）后 override；
#   空串/非法 scheme/缺主机名/契约失败/探测不可达 → ENV-BLOCKED fail-closed；
#   access_token/房间 JWT 绝不落日志/报告——livekit_probe 段只含
#   source/ws_url/health_url/http_status（派生事实可审计）。

LAN_IP = "192.168.8.3"
FAKE_API_TOKEN = "access-token-ZX-preflight-0123456789"


def test_ws_url_to_health_url_loopback_and_lan(vwlc):
    """纯函数：ws→http、wss→https；保留 netloc/path，空路径补 /。"""
    assert vwlc.ws_url_to_health_url("ws://127.0.0.1:7880") == "http://127.0.0.1:7880/"
    assert vwlc.ws_url_to_health_url(f"ws://{LAN_IP}:7880") == f"http://{LAN_IP}:7880/"
    assert vwlc.ws_url_to_health_url(f"wss://{LAN_IP}:7880/rtc") == f"https://{LAN_IP}:7880/rtc"
    assert vwlc.ws_url_to_health_url("wss://lk.example.com") == "https://lk.example.com/"


def test_ws_url_to_health_url_drops_query_and_fragment(vwlc):
    assert vwlc.ws_url_to_health_url("ws://h.example:1700/p?x=1#frag") == "http://h.example:1700/p"


@pytest.mark.parametrize("bad", [
    "http://192.168.8.3:7880",  # 非 ws/wss scheme
    "192.168.8.3:7880",         # 无 scheme
    "",                          # 空串
    "ws://",                     # 无主机名
    "wss:///",                   # 无主机名（带斜杠）
    "ftp://h/p",                 # 其他 scheme
    "ws:/onlyone",               # 单斜杠 → netloc 为空
])
def test_ws_url_to_health_url_illegal_fails_closed(vwlc, bad):
    with pytest.raises(SystemExit):
        vwlc.ws_url_to_health_url(bad)


def test_override_url_unset_returns_none(vwlc, monkeypatch):
    """未设置 → None（走契约派生）——默认路径零配置即可覆盖两类拓扑。"""
    monkeypatch.delenv(vwlc.LIVEKIT_OVERRIDE_ENV, raising=False)
    assert vwlc.livekit_override_url() is None
    assert vwlc.livekit_override_url({}) is None


def test_override_url_valid_ws_wss_returned_verbatim(vwlc, monkeypatch):
    monkeypatch.setenv(vwlc.LIVEKIT_OVERRIDE_ENV, f"ws://{LAN_IP}:7880")
    assert vwlc.livekit_override_url() == f"ws://{LAN_IP}:7880"
    assert vwlc.livekit_override_url(
        {vwlc.LIVEKIT_OVERRIDE_ENV: "wss://lk.example.com/rtc"}
    ) == "wss://lk.example.com/rtc"


@pytest.mark.parametrize("bad", ["", " ", "http://192.168.8.3:7880",
                                 "wss:/onlyone", "192.168.8.3:7880", "ws://"])
def test_override_url_illegal_fails_closed(vwlc, monkeypatch, bad):
    """显式 override 写错（空串/非法 scheme/缺主机名）→ 拒绝执行，
    绝不静默回退契约派生路径（显式配置必须当场暴露）。"""
    monkeypatch.setenv(vwlc.LIVEKIT_OVERRIDE_ENV, bad)
    with pytest.raises(SystemExit):
        vwlc.livekit_override_url()
    with pytest.raises(SystemExit):
        vwlc.livekit_override_url({vwlc.LIVEKIT_OVERRIDE_ENV: bad})


def test_api_call_applies_headers_to_request(vwlc, monkeypatch):
    """api_call 可带鉴权头（登录后的 Bearer）——只进请求，绝不落日志。"""
    captured = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(req, data=None):
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        captured["data"] = data
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    vwlc.api_call("POST", "/api/v1/voice/token", body={"room": "room-x"},
                  headers={"Authorization": f"Bearer {FAKE_API_TOKEN}"})
    assert captured["headers"]["authorization"] == f"Bearer {FAKE_API_TOKEN}"
    assert captured["headers"]["content-type"] == "application/json"
    assert json.loads(captured["data"])["room"] == "room-x"


def test_ensure_user_returns_access_token(vwlc, monkeypatch):
    """登录成功 → access_token 返回调用方（只进内存）；幂等注册在前。"""
    calls = []

    def fake_api_call(method, path, body=None, headers=None):
        calls.append((method, path))
        if path.endswith("/login"):
            return 200, {"access_token": FAKE_API_TOKEN}
        return 201, {}

    monkeypatch.setattr(vwlc, "api_call", fake_api_call)
    assert vwlc.ensure_user() == FAKE_API_TOKEN
    assert calls[0] == ("POST", "/api/v1/auth/register")
    assert calls[1] == ("POST", "/api/v1/auth/login")


@pytest.mark.parametrize("status,payload", [
    (401, {"detail": "bad credentials"}),
    (200, {}),  # 200 但无 access_token → 同样 fail-closed
])
def test_ensure_user_login_failure_fails_closed(vwlc, monkeypatch, status, payload):
    monkeypatch.setattr(vwlc, "api_call",
                        lambda method, path, body=None, headers=None: (status, payload))
    with pytest.raises(SystemExit) as exc:
        vwlc.ensure_user()
    assert str(status) in str(exc.value)  # 仅状态码；响应体不回显


def test_fetch_contract_ws_url_uses_bearer_and_room_contract(vwlc, monkeypatch):
    """契约派生：POST /api/v1/voice/token + Bearer；room 满足业务正则。"""
    captured = {}

    def fake_api_call(method, path, body=None, headers=None):
        captured.update(method=method, path=path, body=body, headers=headers)
        return 200, {"ws_url": f"ws://{LAN_IP}:7880", "token": "eyJfake.payload.value"}

    monkeypatch.setattr(vwlc, "api_call", fake_api_call)
    assert vwlc.fetch_contract_ws_url(FAKE_API_TOKEN) == f"ws://{LAN_IP}:7880"
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/voice/token"
    assert captured["headers"] == {"Authorization": f"Bearer {FAKE_API_TOKEN}"}
    # room 满足业务契约 ^[A-Za-z0-9_-]{3,64}$（否则 API 422，探测自伤）
    assert re.fullmatch(r"[A-Za-z0-9_-]{3,64}", captured["body"]["room"])
    assert captured["body"]["room"].startswith("preflight-")
    assert 0 < captured["body"]["ttl_seconds"] <= 300  # 短命 token，探测即弃


@pytest.mark.parametrize("status,payload", [
    (503, {"detail": "Voice requires LiveKit configuration"}),
    (422, {"detail": "room 需为 3-64 位字母数字/-/_"}),
    (200, {"token": "eyJx.y.z"}),  # 200 但无 ws_url
])
def test_fetch_contract_ws_url_failure_reports_status_only(vwlc, monkeypatch, status, payload):
    """契约失败仅报 HTTP 状态码——响应体（含房间 JWT/服务端 detail）绝不回显。"""
    monkeypatch.setattr(vwlc, "api_call",
                        lambda method, path, body=None, headers=None: (status, payload))
    with pytest.raises(SystemExit) as exc:
        vwlc.fetch_contract_ws_url(FAKE_API_TOKEN)
    message = str(exc.value)
    assert str(status) in message
    for body_fragment in ("Voice requires", "eyJ", "room 需为"):
        assert body_fragment not in message


def test_resolve_livekit_health_contract_derivation_lan(vwlc, monkeypatch):
    """默认（无 override）：契约派生 LAN ws_url → 同源 http 探测。"""
    monkeypatch.delenv(vwlc.LIVEKIT_OVERRIDE_ENV, raising=False)
    probed = {}

    def fake_api_call(method, path, body=None, headers=None):
        return 200, {"ws_url": f"ws://{LAN_IP}:7880", "token": "eyJfake.room.jwt"}

    monkeypatch.setattr(vwlc, "api_call", fake_api_call)
    monkeypatch.setattr(vwlc, "http_status",
                        lambda url, timeout=5.0: (probed.setdefault("url", url), 200)[1])
    report = vwlc.resolve_livekit_health(FAKE_API_TOKEN)
    assert report == {
        "source": "voice-token-contract",
        "ws_url": f"ws://{LAN_IP}:7880",
        "health_url": f"http://{LAN_IP}:7880/",
        "http_status": 200,
    }
    assert probed["url"] == f"http://{LAN_IP}:7880/"  # 探测目标 = 契约地址同源


def test_resolve_livekit_health_contract_derivation_loopback(vwlc, monkeypatch):
    """默认路径同码零改动覆盖 loopback 拓扑（ws_url 来自契约，非硬编码）。"""
    monkeypatch.delenv(vwlc.LIVEKIT_OVERRIDE_ENV, raising=False)
    monkeypatch.setattr(vwlc, "api_call",
                        lambda method, path, body=None, headers=None:
                        (200, {"ws_url": "ws://127.0.0.1:7880"}))
    monkeypatch.setattr(vwlc, "http_status", lambda url, timeout=5.0: 200)
    report = vwlc.resolve_livekit_health(FAKE_API_TOKEN)
    assert report["health_url"] == "http://127.0.0.1:7880/"
    assert report["source"] == "voice-token-contract"


def test_resolve_livekit_health_override_wins_without_contract_call(vwlc, monkeypatch):
    """显式 override 优先且不再调用契约端点（探测目标与业务契约解耦的场景）。"""
    monkeypatch.setenv(vwlc.LIVEKIT_OVERRIDE_ENV, f"wss://{LAN_IP}:7880/rtc")
    monkeypatch.setattr(vwlc, "api_call",
                        lambda *a, **k: pytest.fail("override 模式不得调用契约端点"))
    monkeypatch.setattr(vwlc, "http_status", lambda url, timeout=5.0: 200)
    report = vwlc.resolve_livekit_health(FAKE_API_TOKEN)
    assert report == {
        "source": "env-override",
        "ws_url": f"wss://{LAN_IP}:7880/rtc",
        "health_url": f"https://{LAN_IP}:7880/rtc",
        "http_status": 200,
    }


def test_resolve_livekit_health_unreachable_fails_closed(vwlc, monkeypatch):
    """派生地址探测不可达 → ENV-BLOCKED（禁止自行启动/重启服务）。"""
    monkeypatch.setenv(vwlc.LIVEKIT_OVERRIDE_ENV, f"ws://{LAN_IP}:7880")
    monkeypatch.setattr(vwlc, "http_status", lambda url, timeout=5.0: None)
    with pytest.raises(SystemExit) as exc:
        vwlc.resolve_livekit_health(FAKE_API_TOKEN)
    message = str(exc.value)
    assert "ENV-BLOCKED" in message and f"http://{LAN_IP}:7880/" in message
    assert FAKE_API_TOKEN not in message


def test_livekit_probe_report_never_leaks_credentials(vwlc, monkeypatch):
    """livekit_probe 段只含派生事实：无 JWT 形态、无注入 token 值、无鉴权头字样。"""
    monkeypatch.delenv(vwlc.LIVEKIT_OVERRIDE_ENV, raising=False)

    def fake_api_call(method, path, body=None, headers=None):
        # 模拟真实响应：含房间 JWT 与 token 字段（泄漏面最大化的假想载荷）
        return 200, {
            "token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0."
                     "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
            "ws_url": f"ws://{LAN_IP}:7880",
        }

    monkeypatch.setattr(vwlc, "api_call", fake_api_call)
    monkeypatch.setattr(vwlc, "http_status", lambda url, timeout=5.0: 200)
    report = vwlc.resolve_livekit_health(FAKE_API_TOKEN)
    blob = json.dumps(report, ensure_ascii=False)
    assert vwlc.JWT_RE.search(blob) is None
    assert FAKE_API_TOKEN not in blob
    assert "Authorization" not in blob and "Bearer" not in blob
    assert set(report) == {"source", "ws_url", "health_url", "http_status"}


def test_source_no_hardcoded_livekit_signaling_port():
    """硬编码信令端口必须绝迹（含 docstring）：探测地址一律由契约/override 派生，
    loopback 与 LAN 两类拓扑零改动覆盖。"""
    assert "7880" not in SOURCE


def test_source_preflight_order_contract():
    """main 顺序契约：preflight（API 面）→ 登录取 token → LiveKit 派生探测——
    全部在 ENV-BLOCKED fail-closed（return 2）的 try 块内。"""
    assert SOURCE.index("        preflight()") < SOURCE.index("        api_token = ensure_user()")
    assert SOURCE.index("        api_token = ensure_user()") < SOURCE.index(
        "        livekit_probe = resolve_livekit_health(api_token)")


def test_source_results_records_livekit_probe_section():
    """results.json 必须并入 livekit_probe 段（探测来源/地址可审计）。"""
    assert 'results["livekit_probe"] = livekit_probe' in SOURCE
    assert "resolve_livekit_health(api_token)" in SOURCE
