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
import subprocess
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
