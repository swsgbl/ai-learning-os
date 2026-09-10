r"""M14-04 tools/voice/voice_service_control.py 契约测试：受控生命周期，零真实引擎。

覆盖（全部不启动真实引擎、不占用 8010/8011、不发起网络下载）：
- 状态分类：managed-running / managed-starting / unmanaged-running（区分
  manifest 归属与端口已有但不受管进程，含 ss 属主探测与属主未知降级）/
  stopped / unknown（/proc 不可探测时如实报告，不臆断）；
- stale manifest：PID 已死 / 被无关进程复用（cmdline 不含启动标记）→ 清理并
  明确报告，绝不发送信号；损坏 / 引擎不符同理；
- start 幂等：已在跑（健康或启动中）不重复 spawn；端口被不受管进程占用 →
  拒绝（不 spawn 注定绑不上端口的实例）；并发锁（O_EXCL，陈旧锁回收）；
  launcher 内容契约（PID 落档 → pwd 落档 → exec bootstrap；端口 env）；
  spawn 立即退出 / pidfile 未落 → 可见失败；
- stop 拒绝误杀：无 manifest 不停；不受管监听拒绝；cmdline 匹配但 cwd 指向
  别的工作区（疑似另一检出实例）拒绝且 manifest 保留；TERM→KILL 兜底语义；
  terminate 失败 manifest 保留以便重试；
- restart = stop→start 受控序列（stop 拒绝则中止，不启动第二实例）；
- 端口/路径配置：--port 与 FUNASR_PORT/COSYVOICE_PORT 优先级；默认服务目录
  repo-relative 派生（无机器绝对路径）；
- 真实回环（仅本机 127.0.0.1 空闲端口）：stdlib http.server 假 health 端点
  验证真实 HTTP 探测与 503 分支；TCP 探测空闲端口为 False；
- CLI 子进程：--help 与 status（空闲端口 + 空 manifest 目录 = 纯只读路径）；
- 源码安全契约：无 pkill/killall/fuser/按端口杀；kill 只出现在受控 terminate
  命令内；无 secret；无机器特定绝对路径；不 import 仓库 app 代码。

平台操作全部经注入的 FakeOps（真实探测仅 tcp/http 回环与一次只读 ss）；
生产 8010/8011 不被本套件触碰（唯一真实端口断言见 test_real_*）。
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shlex
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests._subprocess_utf8 import run_utf8

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "voice" / "voice_service_control.py"
SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb-", "-----BEGIN")
#: 测试恒不使用的端口（生产引擎所在；断言防回归）
PRODUCTION_PORTS = {8010, 8011}
FAKE_WORKDIR = "/wsl-fake/repo-root"


def _load_module():
    spec = importlib.util.spec_from_file_location("voice_service_control", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass + from __future__ import annotations：字符串注解解析经
    # sys.modules[cls.__module__]——exec 前必须注册（否则 dataclasses 抛
    # AttributeError NoneType.__dict__）
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


voice_module = _load_module()  # 模块级单例：FakeOps._real 与下方 fixture 同源


@pytest.fixture(scope="module")
def voice():
    return voice_module


class FakeOps:
    """注入的生命周期世界模型：进程表/端口表/健康表 + 记录 spawn/terminate。

    real_probes=True 时 tcp/http 探测走真实回环实现（供假 health 端点测试），
    进程面操作仍为 fake——测试零真实进程副作用。
    """

    def __init__(self, *, real_probes: bool = False) -> None:
        self.processes: dict[int, str] = {}          # pid -> cmdline
        self.cwds: dict[int, str] = {}               # pid -> cwd（缺项 = 不可读）
        self.unprobeable: set[int] = set()           # pid_alive -> None 的 PID
        self.listeners: dict[int, list[int]] = {}    # 端口 -> ss 视角属主
        self.local_tcp: set[int] = set()             # 脚本侧回环 TCP 探测结果
        self.health: dict[int, tuple[int | None, str]] = {}
        self.ss_unavailable = False
        self.spawn_calls: list[tuple[str, str]] = []
        self.terminate_calls: list[tuple[int, int]] = []
        self.terminate_outcome = "terminated"
        self.next_pid = 41000
        self.spawn_dead = False        # spawn 的进程立即退出（spawn.pid 已落但进程不在）
        self.spawn_no_pidfile = False  # launcher 未落 spawn.pid
        self.real_probes = real_probes
        self._base = None  # 延迟绑定真实探测基类

    def _real(self):
        if self._base is None:
            self._base = voice_module.ServiceOps()
        return self._base



    # ---- 探测面 ----

    def tcp_listening(self, port: int, host: str = "127.0.0.1") -> bool:
        if self.real_probes:
            return self._real().tcp_listening(port, host)
        return port in self.local_tcp

    def http_health(self, port: int, host: str = "127.0.0.1") -> tuple[int | None, str]:
        if self.real_probes:
            return self._real().http_health(port, host)
        return self.health.get(port, (None, "ConnectionRefusedError"))

    # ---- 进程面 ----

    def pid_alive(self, pid: int) -> bool | None:
        if pid in self.unprobeable:
            return None
        return pid in self.processes

    def pid_cmdline(self, pid: int) -> str | None:
        return self.processes.get(pid)

    def pid_cwd(self, pid: int) -> str | None:
        return self.cwds.get(pid)

    def shell_port_state(self, port: int) -> tuple[bool, list[int]] | None:
        if self.ss_unavailable:
            return None
        return port in self.listeners, list(self.listeners.get(port, []))

    def spawn_detached(self, launcher: str, log: str) -> None:
        self.spawn_calls.append((launcher, log))
        if self.spawn_no_pidfile:
            return
        pid = self.next_pid
        self.next_pid += 1
        service_dir = Path(launcher).parent
        (service_dir / "spawn.pid").write_text(f"{pid}\n", encoding="utf-8")
        (service_dir / "workdir.txt").write_text(f"{FAKE_WORKDIR}\n", encoding="utf-8")
        if not self.spawn_dead:
            # launcher 末行 exec bash <bootstrap>：cmdline 与 manifest 标记同源
            text = Path(launcher).read_text(encoding="utf-8")
            exec_line = next(line for line in text.splitlines() if line.startswith("exec bash "))
            bootstrap = shlex.split(exec_line[len("exec bash "):])[0]
            self.processes[pid] = f"bash {bootstrap}"
            self.cwds[pid] = FAKE_WORKDIR

    def terminate(self, pid: int, grace_seconds: int) -> str:
        self.terminate_calls.append((pid, grace_seconds))
        if self.terminate_outcome in {"terminated", "killed", "already-dead"}:
            self.processes.pop(pid, None)
            # 进程死亡 → 其监听 socket 随之关闭：释放端口属主与回环监听事实
            for port, owners in self.listeners.items():
                if pid in owners:
                    owners.remove(pid)
            for port in [p for p, owners in self.listeners.items() if not owners]:
                self.listeners.pop(port, None)
                self.local_tcp.discard(port)
        return self.terminate_outcome


@pytest.fixture()
def ops():
    return FakeOps()


@pytest.fixture()
def tmp_svc(tmp_path):
    return tmp_path / "service"


def spec_of(voice, name: str):
    return next(s for s in voice.ENGINE_SPECS if s.name == name)


def make_manifest(voice, *, engine: str = "funasr", pid: int = 41001, port: int = 18010,
                  workdir: str | None = FAKE_WORKDIR) -> voice_module.Manifest:
    spec = spec_of(voice, engine)
    return voice_module.Manifest(
        engine=spec.name, pid=pid, port=port,
        started_at="2026-09-10T15:00:00+08:00",
        bootstrap=spec.bootstrap_script, cmd_markers=list(spec.cmd_markers),
        workdir=workdir, log="artifacts/voice/funasr/service/service.log",
    )


def managed_running(voice, ops: FakeOps, *, port: int = 18010, pid: int = 41001,
                    cmdline: str = "bash tools/voice/bootstrap_funasr_wsl.sh") -> None:
    """构造「manifest 归属成立 + 端口监听 + 健康 200」的世界。"""
    ops.processes[pid] = cmdline
    ops.cwds[pid] = FAKE_WORKDIR
    ops.local_tcp.add(port)
    ops.listeners[port] = [pid]
    ops.health[port] = (200, json.dumps({"status": "ok", "model": "fake-model"}))


def run_cli(voice, argv: list[str], ops: FakeOps) -> tuple[int, str]:
    buf = io.StringIO()
    rc = voice.main(argv, ops=ops, stdout=buf)
    return rc, buf.getvalue()


def argv_for(command: str, tmp_svc: Path, *, engine: str = "funasr",
             port: int = 18010, extra: list[str] | None = None) -> list[str]:
    assert port not in PRODUCTION_PORTS
    args = [command, "--engine", engine, "--port", str(port), "--service-dir", str(tmp_svc)]
    if extra:
        args.extend(extra)
    return args


# ---------- 语法 / 源码安全契约 ----------


def test_python_script_compiles() -> None:
    import py_compile

    py_compile.compile(str(SCRIPT), doraise=True)


def test_script_text_contract(voice) -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    for anchor in (
        "setsid nohup",          # 脱离 wsl.exe 会话存活（M14-02 实证）
        "< /dev/null &",         # spawn 命令形态（后台 + 完整重定向）
        " sleep 1",              # settle：裸 & 会被 wsl.exe 会话拆除连带杀死
                                 # （2026-09-10 实证 spawn.pid/日志均未产生）
        "kill -TERM {pid}",      # 受控终止：先优雅（{pid} = Python 内插 int）
        "kill -KILL {pid}",      # 宽限超时才 KILL 兜底（同上）
        "[ -d {proc} ]",         # 存活判定只读 /proc（零 bash 变量，见下）
        "ss -tlnp",              # 端口属主探测（只读）
        "os.O_EXCL",             # 控制锁原子创建（并发 start/stop 串行化）
        "manifest.json",         # 事实源 manifest
        "wsl.exe",               # Windows 侧编排经 --cd（零路径转换）
        "拒绝停止",               # stop 安全拒绝必须显式
        "拒绝启动",               # start 端口竞争拒绝必须显式
        "拒绝认定归属",            # cwd 不符（疑似另一检出实例）拒绝
        "已被无关进程复用",         # PID 复用 stale 报告
        "stale manifest 已清理",   # stale 清理明确报告
        "FUNASR_PORT",
        "COSYVOICE_PORT",
        "8010",
        "8011",
    ):
        assert anchor in text, anchor
    for pattern in SECRET_PATTERNS:
        assert pattern not in text, pattern


def _script_code_text() -> str:
    """脚本源码去掉模块 docstring（设计说明里「绝不 pkill/killall」属禁令陈述，
    非生效代码——复审惯例：注释/文档提及不算，生效代码才算）。"""
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index('"""')
    end = text.index('"""', start + 3) + 3
    return text[:start] + text[end:]


def test_script_never_broad_kills() -> None:
    """硬边界：生效代码绝无 pkill/killall/fuser；kill 只允许出现在受控 terminate
    命令模板内（TERM/KILL 目标恒为 manifest 核验过的单个 PID 变量 $p）。"""
    text = _script_code_text()
    for banned in ("pkill", "killall", "fuser", "kill -9"):
        assert banned not in text, banned
    kill_lines = [line for line in text.splitlines() if re.search(r"kill -", line)]
    assert kill_lines, "应有受控 kill 实现"
    for line in kill_lines:
        # kill 目标恒为 Python 内插的 manifest PID（int 校验），绝无变量/端口/
        # 命令名派生目标
        assert "kill -TERM {pid}" in line or "kill -KILL {pid}" in line, f"越界 kill: {line}"
    # 不存在按端口/按命令名派生 kill 目标的代码路径
    assert "kill $(" not in text
    assert "kill `ss" not in text


def test_script_commands_are_variable_free() -> None:
    """BashOps 命令纪律回归：bash 命令串零 $var 展开——wsl.exe --cd 形态会
    剥掉 $var（2026-09-10 实证：$p 成空串 → [ -d /proc/ ] 恒真、kill 无参，
    terminate 对活进程恒报 failed 且从不发信号）。唯一例外是 launcher 文件
    内容里的 $$（WSL 内 bash 从文件执行，不经 wsl.exe 参数面）。"""
    text = _script_code_text()
    offenders = [line for line in text.splitlines() if re.search(r"\$\w", line)]
    assert offenders == [], f"命令串出现 $var（会被 wsl.exe --cd 剥掉）: {offenders}"
    # launcher 的 $$ 是合法例外（落档 exec 前的自身 PID；源码中经转义引号出现）
    assert "$$" in text


def test_script_no_machine_specific_paths() -> None:
    """可移植性：脚本源码无机器特定绝对路径（盘符/家目录/WSL 挂载点）——
    Windows→WSL 编排经 wsl.exe --cd 完成，无任何路径手工转换。"""
    text = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("D:\\", "C:\\", "/home/", "/mnt/", "/root/"):
        assert forbidden not in text, forbidden


def test_script_does_not_import_repo_app_modules() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "from app." not in text
    assert "import app" not in text


def test_engine_specs_defaults(voice) -> None:
    funasr = spec_of(voice, "funasr")
    cosyvoice = spec_of(voice, "cosyvoice")
    assert (funasr.default_port, funasr.port_env) == (8010, "FUNASR_PORT")
    assert (cosyvoice.default_port, cosyvoice.port_env) == (8011, "COSYVOICE_PORT")
    assert funasr.bootstrap_script == "tools/voice/bootstrap_funasr_wsl.sh"
    assert cosyvoice.bootstrap_script == "tools/voice/bootstrap_cosyvoice_wsl.sh"
    # exec 链各阶段标记齐全：bootstrap 名 + cosyvoice 快照 re-exec + 服务进程
    assert "funasr-server" in funasr.cmd_markers
    for marker in ("bootstrap_cosyvoice_wsl.sh", "bootstrap_cosyvoice_wsl.snapshot.sh",
                   "cosyvoice_openai_bridge.py"):
        assert marker in cosyvoice.cmd_markers, marker


# ---------- status：状态分类 ----------


def test_status_stopped_when_no_manifest_and_port_free(voice, ops, tmp_svc) -> None:
    rc, out = run_cli(voice, argv_for("status", tmp_svc), ops)
    assert rc == 0
    assert "state: stopped" in out
    assert "no listener" in out
    assert ops.terminate_calls == []


def test_status_managed_running_healthy_with_model(voice, ops, tmp_svc) -> None:
    managed_running(voice, ops)
    voice.write_manifest(make_manifest(voice), tmp_svc)
    rc, out = run_cli(voice, argv_for("status", tmp_svc), ops)
    assert rc == 0
    assert "state: managed-running" in out
    assert "pid: 41001" in out
    assert "started_at: 2026-09-10T15:00:00+08:00" in out
    assert "HTTP 200 model=fake-model" in out
    assert "属主 PID: [41001]" in out
    assert "log: artifacts/voice/funasr/service/service.log" in out


def test_status_managed_starting_when_port_not_listening(voice, ops, tmp_svc) -> None:
    # 进程存活且归属成立，但端口未监听 = bootstrap 阶段（依赖安装/模型加载）
    ops.processes[41001] = "bash tools/voice/bootstrap_funasr_wsl.sh"
    ops.cwds[41001] = FAKE_WORKDIR
    voice.write_manifest(make_manifest(voice), tmp_svc)
    rc, out = run_cli(voice, argv_for("status", tmp_svc), ops)
    assert rc == 0
    assert "state: managed-starting" in out
    assert "bootstrap 阶段" in out


def test_status_unmanaged_running_reports_owner_and_refusal_hint(voice, ops, tmp_svc) -> None:
    # 端口已有监听但无 manifest（如 M14-02 手工前台启动的生产引擎）→ 不受管
    ops.local_tcp.add(18010)
    ops.listeners[18010] = [779]
    ops.health[18010] = (200, json.dumps({"status": "ok", "models_loaded": ["sensevoice"]}))
    rc, out = run_cli(voice, argv_for("status", tmp_svc), ops)
    assert rc == 0
    assert "state: unmanaged-running" in out
    assert "属主 PID: [779]" in out
    assert "不受本工具管辖" in out
    assert "拒绝误杀" in out


def test_status_unmanaged_owner_unknown_when_ss_unavailable(voice, ops, tmp_svc) -> None:
    ops.local_tcp.add(18010)
    ops.ss_unavailable = True
    ops.health[18010] = (503, json.dumps({"detail": "cosyvoice model is loading"}))
    rc, out = run_cli(voice, argv_for("status", tmp_svc), ops)
    assert rc == 0
    assert "state: unmanaged-running" in out
    assert "未知（ss 不可用）" in out  # 属主未知如实降级，不影响分类
    assert "HTTP 503" in out


def test_status_parses_funasr_models_loaded_field(voice, ops, tmp_svc) -> None:
    managed_running(voice, ops)
    ops.health[18010] = (200, json.dumps({"status": "ok", "device": "cpu",
                                          "models_loaded": ["sensevoice"]}))
    voice.write_manifest(make_manifest(voice), tmp_svc)
    _, out = run_cli(voice, argv_for("status", tmp_svc), ops)
    assert "model=sensevoice" in out  # funasr-server 的 /health 形状（models_loaded）


def test_status_stale_manifest_pid_dead_cleaned(voice, ops, tmp_svc) -> None:
    voice.write_manifest(make_manifest(voice, pid=41099), tmp_svc)  # PID 不在进程表 = 已死
    rc, out = run_cli(voice, argv_for("status", tmp_svc), ops)
    assert rc == 0
    assert "stale manifest 已清理（PID 41099 已退出）" in out
    assert not (tmp_svc / "manifest.json").exists()
    assert "state: stopped" in out
    assert ops.terminate_calls == []


def test_status_pid_reused_by_unrelated_process(voice, ops, tmp_svc) -> None:
    ops.processes[41001] = "vim notes.txt"  # PID 存活但 cmdline 与语音栈无关
    voice.write_manifest(make_manifest(voice), tmp_svc)
    rc, out = run_cli(voice, argv_for("status", tmp_svc), ops)
    assert rc == 0
    assert "已被无关进程复用" in out
    assert not (tmp_svc / "manifest.json").exists()
    assert ops.terminate_calls == []  # 绝不发信号


def test_status_corrupt_manifest_removed(voice, ops, tmp_svc) -> None:
    tmp_svc.mkdir(parents=True, exist_ok=True)
    (tmp_svc / "manifest.json").write_text("{ not json", encoding="utf-8")
    _, out = run_cli(voice, argv_for("status", tmp_svc), ops)
    assert "manifest 损坏或引擎不符" in out
    assert not (tmp_svc / "manifest.json").exists()


def test_status_manifest_wrong_engine_removed(voice, ops, tmp_svc) -> None:
    voice.write_manifest(make_manifest(voice, engine="cosyvoice"), tmp_svc)  # funasr 目录里的外来 manifest
    _, out = run_cli(voice, argv_for("status", tmp_svc), ops)
    assert "manifest 损坏或引擎不符" in out
    assert not (tmp_svc / "manifest.json").exists()


def test_status_pid_unprobeable_reports_unknown(voice, ops, tmp_svc) -> None:
    ops.processes[41001] = "bash tools/voice/bootstrap_funasr_wsl.sh"
    ops.unprobeable.add(41001)  # /proc 探测不可用 → 不臆断
    voice.write_manifest(make_manifest(voice), tmp_svc)
    rc, out = run_cli(voice, argv_for("status", tmp_svc), ops)
    assert rc == 0
    assert "state: unknown" in out
    assert "不可探测" in out
    assert ops.terminate_calls == []


# ---------- start：幂等 / 三重校验 / 端口竞争 / 锁 ----------


def test_start_idempotent_when_managed_healthy(voice, ops, tmp_svc) -> None:
    managed_running(voice, ops)
    voice.write_manifest(make_manifest(voice), tmp_svc)
    rc, out = run_cli(voice, argv_for("start", tmp_svc), ops)
    assert rc == 0
    assert "已在运行" in out and "幂等跳过" in out
    assert ops.spawn_calls == []


def test_start_idempotent_when_managed_starting(voice, ops, tmp_svc) -> None:
    ops.processes[41001] = "bash tools/voice/bootstrap_funasr_wsl.sh"
    ops.cwds[41001] = FAKE_WORKDIR
    voice.write_manifest(make_manifest(voice), tmp_svc)
    rc, out = run_cli(voice, argv_for("start", tmp_svc), ops)
    assert rc == 0
    assert "已在运行" in out and "managed-starting" in out
    assert ops.spawn_calls == []  # bootstrap 阶段（端口未起）同样不重复启动


def test_start_fresh_spawns_and_writes_manifest(voice, ops, tmp_svc) -> None:
    rc, out = run_cli(voice, argv_for("start", tmp_svc), ops)
    assert rc == 0, out
    assert "已启动（PID 41000" in out
    assert len(ops.spawn_calls) == 1
    manifest = json.loads((tmp_svc / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["engine"] == "funasr"
    assert manifest["pid"] == 41000
    assert manifest["port"] == 18010
    assert manifest["workdir"] == FAKE_WORKDIR
    assert manifest["started_at"]
    assert "funasr-server" in manifest["cmd_markers"]
    assert (tmp_svc / "spawn.pid").read_text(encoding="utf-8").strip() == "41000"
    assert not (tmp_svc / "control.lock").exists()  # 锁已释放


def test_start_launcher_contract(voice, ops, tmp_svc) -> None:
    rc, _ = run_cli(voice, argv_for("start", tmp_svc), ops)
    assert rc == 0
    launcher = (tmp_svc / "launcher.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in launcher
    assert 'export FUNASR_PORT="18010"' in launcher
    # PID 先落档再 exec（exec 链保 PID → spawn.pid 恒等于服务进程 PID）
    assert launcher.index("spawn.pid") < launcher.index("exec bash")
    assert launcher.index("workdir.txt") < launcher.index("exec bash")
    assert "exec bash tools/voice/bootstrap_funasr_wsl.sh" in launcher
    assert "exec bash " in launcher.splitlines()[-1]  # 末行恒为 exec
    # LF-only 回归锁定：Windows 默认换行翻译会让 WSL bash 把
    # 「set -euo pipefail\r」当非法选项（真实 spawn 实证）
    assert "\r" not in launcher
    assert "\r" not in (tmp_svc / "manifest.json").read_text(encoding="utf-8")


def test_start_cleans_stale_manifest_then_spawns(voice, ops, tmp_svc) -> None:
    voice.write_manifest(make_manifest(voice, pid=41099), tmp_svc)  # stale（PID 已死）
    rc, out = run_cli(voice, argv_for("start", tmp_svc), ops)
    assert rc == 0, out
    assert "stale manifest 已清理" in out
    assert len(ops.spawn_calls) == 1
    assert json.loads((tmp_svc / "manifest.json").read_text(encoding="utf-8"))["pid"] == 41000


def test_start_refuses_when_port_held_by_unmanaged_process(voice, ops, tmp_svc) -> None:
    ops.local_tcp.add(18010)
    ops.listeners[18010] = [7777]
    rc, out = run_cli(voice, argv_for("start", tmp_svc), ops)
    assert rc == 3  # EXIT_REFUSED：端口竞争拒绝
    assert "拒绝启动" in out
    assert "属主 PID: [7777]" in out
    assert ops.spawn_calls == []  # 不 spawn 注定绑不上端口的实例


def test_start_refuses_when_ownership_unverifiable(voice, ops, tmp_svc) -> None:
    ops.processes[41001] = "bash tools/voice/bootstrap_funasr_wsl.sh"
    ops.unprobeable.add(41001)
    voice.write_manifest(make_manifest(voice), tmp_svc)
    rc, out = run_cli(voice, argv_for("start", tmp_svc), ops)
    assert rc == 3
    assert "拒绝启动第二实例" in out
    assert ops.spawn_calls == []


def test_start_refuses_foreign_workdir_instance(voice, ops, tmp_svc) -> None:
    ops.processes[41001] = "bash tools/voice/bootstrap_funasr_wsl.sh"
    ops.cwds[41001] = "/wsl-fake/another-checkout"  # cmdline 匹配但工作区不符
    voice.write_manifest(make_manifest(voice), tmp_svc)
    rc, out = run_cli(voice, argv_for("start", tmp_svc), ops)
    assert rc == 3
    assert "疑似另一检出实例" in out
    assert ops.spawn_calls == []


def test_start_lock_contention_and_stale_lock_recovery(voice, ops, tmp_svc) -> None:
    tmp_svc.mkdir(parents=True, exist_ok=True)
    lock = tmp_svc / "control.lock"
    lock.write_text("holder\n", encoding="utf-8")
    rc, out = run_cli(voice, argv_for("start", tmp_svc), ops)
    assert rc == 3
    assert "另一 start/stop 操作进行中" in out
    assert ops.spawn_calls == []
    # 陈旧锁（超过 TTL）被安全回收后放行
    old = time.time() - 7000
    os.utime(lock, (old, old))
    rc, out = run_cli(voice, argv_for("start", tmp_svc), ops)
    assert rc == 0, out
    assert "陈旧控制锁已回收" in out
    assert len(ops.spawn_calls) == 1


def test_start_fails_visible_when_process_exits_immediately(voice, ops, tmp_svc) -> None:
    ops.spawn_dead = True
    rc, out = run_cli(voice, argv_for("start", tmp_svc), ops)
    assert rc == 1
    assert "立即退出" in out
    assert not (tmp_svc / "manifest.json").exists()  # 失败不写 manifest


def test_start_fails_visible_when_pidfile_missing(voice, ops, tmp_svc, monkeypatch) -> None:
    monkeypatch.setattr(voice_module, "SPAWN_PID_TIMEOUT_SECONDS", 0.3)
    ops.spawn_no_pidfile = True
    rc, out = run_cli(voice, argv_for("start", tmp_svc), ops)
    assert rc == 1
    assert "未落 spawn.pid" in out
    assert not (tmp_svc / "manifest.json").exists()


def test_start_port_env_variable_respected(voice, ops, tmp_svc, monkeypatch) -> None:
    monkeypatch.setenv("FUNASR_PORT", "18055")
    rc, out = run_cli(voice, ["start", "--engine", "funasr", "--service-dir", str(tmp_svc)], ops)
    assert rc == 0, out
    manifest = json.loads((tmp_svc / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["port"] == 18055
    assert 'export FUNASR_PORT="18055"' in (tmp_svc / "launcher.sh").read_text(encoding="utf-8")


def test_start_cli_port_flag_overrides_env(voice, ops, tmp_svc, monkeypatch) -> None:
    monkeypatch.setenv("FUNASR_PORT", "18055")
    rc, _ = run_cli(voice, argv_for("start", tmp_svc, port=18066), ops)
    assert rc == 0
    manifest = json.loads((tmp_svc / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["port"] == 18066


def test_start_clears_stale_spawn_pid_before_respawn(voice, ops, tmp_svc) -> None:
    """restart 竞态回归：上轮残留 spawn.pid 不得被当作新进程 PID 记档。"""
    tmp_svc.mkdir(parents=True, exist_ok=True)
    (tmp_svc / "spawn.pid").write_text("99999\n", encoding="utf-8")  # 上轮残留
    rc, out = run_cli(voice, argv_for("start", tmp_svc), ops)
    assert rc == 0, out
    manifest = json.loads((tmp_svc / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pid"] == 41000  # 新 launcher 落的 PID，而非残留 99999


# ---------- stop：只停 manifest 归属 / 拒绝误杀 ----------


def test_stop_noop_when_not_managed(voice, ops, tmp_svc) -> None:
    rc, out = run_cli(voice, argv_for("stop", tmp_svc), ops)
    assert rc == 0
    assert "无需停止" in out
    assert ops.terminate_calls == []


def test_stop_refuses_unmanaged_listener(voice, ops, tmp_svc) -> None:
    ops.local_tcp.add(18010)
    ops.listeners[18010] = [779]
    rc, out = run_cli(voice, argv_for("stop", tmp_svc), ops)
    assert rc == 3
    assert "拒绝停止" in out
    assert "不受管监听" in out and "779" in out
    assert ops.terminate_calls == []  # 绝不碰无 manifest 进程


def test_stop_managed_terminates_and_cleans_manifest(voice, ops, tmp_svc) -> None:
    managed_running(voice, ops)
    voice.write_manifest(make_manifest(voice), tmp_svc)
    rc, out = run_cli(voice, argv_for("stop", tmp_svc), ops)
    assert rc == 0
    assert ops.terminate_calls == [(41001, voice_module.TERMINATE_GRACE_SECONDS)]
    assert "已停止（PID 41001，已优雅退出（TERM））" in out
    assert not (tmp_svc / "manifest.json").exists()
    assert not (tmp_svc / "control.lock").exists()


def test_stop_stale_manifest_cleaned_without_signal(voice, ops, tmp_svc) -> None:
    voice.write_manifest(make_manifest(voice, pid=41099), tmp_svc)
    rc, out = run_cli(voice, argv_for("stop", tmp_svc), ops)
    assert rc == 0
    assert "stale manifest 已清理" in out
    assert ops.terminate_calls == []
    assert not (tmp_svc / "manifest.json").exists()


def test_stop_pid_reuse_reports_and_never_signals(voice, ops, tmp_svc) -> None:
    ops.processes[41001] = "python unrelated_worker.py"
    voice.write_manifest(make_manifest(voice), tmp_svc)
    rc, out = run_cli(voice, argv_for("stop", tmp_svc), ops)
    assert rc == 0  # 归属已失（manifest 清理）→ 无可停目标，幂等成功
    assert "已被无关进程复用" in out
    assert ops.terminate_calls == []
    assert not (tmp_svc / "manifest.json").exists()
    assert 41001 in ops.processes  # 无关进程毫发无损


def test_stop_refuses_foreign_cwd_and_keeps_manifest(voice, ops, tmp_svc) -> None:
    ops.processes[41001] = "bash tools/voice/bootstrap_funasr_wsl.sh"
    ops.cwds[41001] = "/wsl-fake/another-checkout"
    voice.write_manifest(make_manifest(voice), tmp_svc)
    rc, out = run_cli(voice, argv_for("stop", tmp_svc), ops)
    assert rc == 3
    assert "疑似另一检出实例" in out
    assert ops.terminate_calls == []
    assert (tmp_svc / "manifest.json").exists()  # manifest 保留供人工核实


def test_stop_reports_kill_escalation_outcome(voice, ops, tmp_svc) -> None:
    managed_running(voice, ops)
    voice.write_manifest(make_manifest(voice), tmp_svc)
    ops.terminate_outcome = "killed"
    rc, out = run_cli(voice, argv_for("stop", tmp_svc), ops)
    assert rc == 0
    assert "KILL 兜底" in out
    assert not (tmp_svc / "manifest.json").exists()


def test_stop_failure_keeps_manifest_for_retry(voice, ops, tmp_svc) -> None:
    managed_running(voice, ops)
    voice.write_manifest(make_manifest(voice), tmp_svc)
    ops.terminate_outcome = "failed"
    rc, out = run_cli(voice, argv_for("stop", tmp_svc), ops)
    assert rc == 1
    assert "manifest 保留以便重试" in out
    assert (tmp_svc / "manifest.json").exists()


def test_stop_skips_cwd_check_when_unreadable(voice, ops, tmp_svc) -> None:
    # cwd 不可读（权限）→ 跳过工作区核验，cmdline 匹配即归属成立（防御纵深：
    # PID + cmdline 双事实已足够拒绝绝大多数误杀）
    managed_running(voice, ops)
    ops.cwds.pop(41001)
    voice.write_manifest(make_manifest(voice), tmp_svc)
    rc, _ = run_cli(voice, argv_for("stop", tmp_svc), ops)
    assert rc == 0
    assert ops.terminate_calls == [(41001, voice_module.TERMINATE_GRACE_SECONDS)]


# ---------- restart：stop → start 受控序列 ----------


def test_restart_managed_stops_then_starts(voice, ops, tmp_svc) -> None:
    managed_running(voice, ops)
    voice.write_manifest(make_manifest(voice), tmp_svc)
    rc, out = run_cli(voice, argv_for("restart", tmp_svc), ops)
    assert rc == 0, out
    assert ops.terminate_calls == [(41001, voice_module.TERMINATE_GRACE_SECONDS)]
    assert len(ops.spawn_calls) == 1
    manifest = json.loads((tmp_svc / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["pid"] == 41000  # 新实例 PID 已记档
    assert manifest["port"] == 18010


def test_restart_aborts_when_stop_refused(voice, ops, tmp_svc) -> None:
    # 端口被不受管进程占用（即生产现状）：stop 拒绝 → restart 中止，不 spawn
    ops.local_tcp.add(18010)
    ops.listeners[18010] = [779]
    rc, out = run_cli(voice, argv_for("restart", tmp_svc), ops)
    assert rc == 3
    assert "restart 中止" in out
    assert ops.spawn_calls == [] and ops.terminate_calls == []


def test_restart_stopped_goes_straight_to_start(voice, ops, tmp_svc) -> None:
    rc, out = run_cli(voice, argv_for("restart", tmp_svc), ops)
    assert rc == 0, out
    assert ops.terminate_calls == []
    assert len(ops.spawn_calls) == 1


# ---------- 路径解析 / 多引擎聚合 / CLI 防呆 ----------


def test_default_service_dir_repo_relative(voice) -> None:
    spec = spec_of(voice, "funasr")
    expected = voice.REPO_ROOT / "artifacts" / "voice" / "funasr" / "service"
    assert voice.default_service_dir(spec) == expected
    # REPO_ROOT 自脚本位置派生（本测试文件同仓库 → 同根）
    assert voice.REPO_ROOT == REPO_ROOT
    # 派生链各段均无机器特定绝对路径
    assert "artifacts/voice" in voice.default_service_dir(spec).as_posix()


def test_repo_relative_outside_repo_uses_absolute_posix(voice, tmp_path) -> None:
    assert voice._repo_relative(tmp_path) == tmp_path.as_posix()


def test_engine_all_aggregates_refused_exit_code(voice, ops, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(voice, "default_service_dir",
                        lambda spec: tmp_path / spec.artifacts_subdir)
    # funasr：端口被占 → 拒绝；cosyvoice：全新 → 成功；聚合取最差
    ops.local_tcp.add(8010)
    ops.listeners[8010] = [779]
    rc, out = run_cli(voice, ["restart", "--engine", "all"], ops)
    assert rc == 3
    assert "拒绝" in out
    assert "=== restart funasr" in out and "=== restart cosyvoice" in out
    assert len(ops.spawn_calls) == 1  # 仅 cosyvoice spawn


def test_cli_rejects_port_with_engine_all(voice, ops) -> None:
    rc, out = run_cli(voice, ["status", "--engine", "all", "--port", "9999"], ops)
    assert rc == 2
    assert "--port 只能配合单引擎" in out


def test_cli_rejects_service_dir_with_engine_all(voice, ops, tmp_svc) -> None:
    rc, out = run_cli(voice, ["status", "--engine", "all", "--service-dir", str(tmp_svc)], ops)
    assert rc == 2
    assert "--service-dir 只能配合单引擎" in out


# ---------- 真实回环探测（仅 127.0.0.1 空闲端口；不触 8010/8011） ----------


class _FakeHealthHandler(BaseHTTPRequestHandler):
    status_code = 200
    body = json.dumps({"status": "ok", "model": "loopback-fake-model"})

    def do_GET(self) -> None:  # 大写方法名为 BaseHTTPRequestHandler 分发约定
        payload = self.body.encode("utf-8")
        self.send_response(self.status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:  # 测试静默
        return


@pytest.fixture()
def fake_health_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHealthHandler)
    port = server.server_address[1]
    assert port not in PRODUCTION_PORTS, "假 health 端点不得占用生产端口"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_real_http_health_200_against_local_fake_server(voice, fake_health_server, tmp_svc) -> None:
    port = fake_health_server.server_address[1]
    ops = FakeOps(real_probes=True)
    ops.processes[41001] = "bash tools/voice/bootstrap_funasr_wsl.sh"
    ops.cwds[41001] = FAKE_WORKDIR
    voice.write_manifest(make_manifest(voice, port=port), tmp_svc)
    rc, out = run_cli(voice, ["status", "--engine", "funasr", "--port", str(port),
                              "--service-dir", str(tmp_svc)], ops)
    assert rc == 0
    assert "state: managed-running" in out  # 真实 TCP 探测命中
    assert "HTTP 200 model=loopback-fake-model" in out  # 真实 HTTP 探测 + 模型解析


def test_real_http_health_503_loading(voice, fake_health_server, tmp_svc) -> None:
    port = fake_health_server.server_address[1]
    _FakeHealthHandler.status_code = 503
    _FakeHealthHandler.body = json.dumps({"detail": "cosyvoice model is loading"})
    try:
        ops = FakeOps(real_probes=True)
        ops.processes[41001] = "bash tools/voice/bootstrap_cosyvoice_wsl.sh"
        ops.cwds[41001] = FAKE_WORKDIR
        voice.write_manifest(make_manifest(voice, engine="cosyvoice", port=port), tmp_svc)
        rc, out = run_cli(voice, ["status", "--engine", "cosyvoice", "--port", str(port),
                                  "--service-dir", str(tmp_svc)], ops)
        assert rc == 0
        assert "state: managed-running" in out
        assert "HTTP 503" in out and "cosyvoice model is loading" in out
    finally:
        _FakeHealthHandler.status_code = 200
        _FakeHealthHandler.body = json.dumps({"status": "ok", "model": "loopback-fake-model"})


def test_real_tcp_probe_free_port_is_false(voice) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # 已释放的临时端口视为空闲（与测试自身端口选择同一策略）
    assert voice_module.ServiceOps().tcp_listening(port) is False


# ---------- CLI 子进程（--help 与只读 status 路径） ----------


def test_cli_help_subprocess() -> None:
    result = run_utf8([sys.executable, str(SCRIPT), "--help"], timeout=60)
    assert result.returncode == 0
    assert "status/start/stop/restart" in (result.stdout or "")


def test_cli_status_stopped_subprocess(tmp_path) -> None:
    """真实 CLI 子进程 × 空闲端口 × 空 manifest 目录：只读路径全程零写零杀
    （端口探测经真实 BashOps 的 ss——只读；无 manifest 则无任何 /proc 访问）。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert port not in PRODUCTION_PORTS
    result = run_utf8(
        [sys.executable, str(SCRIPT), "status", "--engine", "funasr",
         "--port", str(port), "--service-dir", str(tmp_path / "svc")],
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "state: stopped" in (result.stdout or "")
    # 只读语义：status 不创建服务目录（无 manifest、无监听时零副作用）
    assert not (tmp_path / "svc").exists()
