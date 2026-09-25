r"""M14-140 production_drift_watch_alert_task 真实子进程回环运行时集成
测试（M14-140 测试切片）：补上 M14-137 契约测试（注入 FakeRunner）留下
的「真实 M14-135 dispatch 子进程移交未实证」边界——以真实 CLI 子进程
驱动任务桥本体，在仅本机 ephemeral 回环接收器可观测的面上实证三条关键
运行时路径。模板与断言口径对齐 M14-136
（``test_production_drift_watch_alert_dispatch_runtime.py``）。

覆盖（黑盒：子进程调用真实任务桥 CLI，不 import 工具模块、零
monkeypatch——被测面就是真实 subprocess/网络构造面）：

- **drift=true execute → 真实移交 + 子进程 fail-closed 透传**：临时
  工件目录（older drift=false + newest drift=true 两份合成 execute
  模式报告）+ 临时 secret JSON（URL 指向 ephemeral 回环接收器）+
  M14-137 精确 execute/confirm 门 + 临时 dispatch 工件目录 → 任务桥
  必须把移交真实落到 M14-135 dispatch CLI 子进程。因 M14-137 白名单
  刻意**不转发** ``--allow-loopback-http``，子进程对 http webhook 必须
  fail-closed（``scheme-not-https``——该词汇只存在于 M14-135 移交
  目标族，其出现即真实子进程已运行的运行时证据）且零 HTTP 请求；任务
  桥退出码 2（子进程拒绝原样透传）。断言接收器零请求、零 ledger/
  dispatch 工件落盘、stdout 报告 newest stem 与独立重算的精确
  SHA-256、真实目录扫描枚举全部候选，且 stdout/stderr 绝无 token /
  完整 URL / 127.0.0.1 字面量 / 接收器路径 / 绝对本地路径 / 投毒
  report detail。
- **drift=true plan**：真实任务桥 plan（零 secret、零 execute）→
  exit 3 + ``dispatch-would-be-required`` + 精确 SHA-256；接收器零
  请求；pytest 临时目录树前后快照不变（plan 零写入的运行时面——
  socket/subprocess 零构造经结果观测：零请求 + 零新文件）。
- **drift=false execute**：older drift=true + newest drift=false →
  exit 0 + ``skipped-no-alerts`` + 「零 dispatch 子进程」；接收器零
  请求；dispatch 工件目录不存在（若子进程曾被构造，M14-135 即便
  skip 路径也会写 dispatch-*.json/.md 工件——目录不存在即零子进程
  副作用的运行时证明）；drift 判定确实只依据最新报告布尔（older
  drift=true 绝不触发移交，stdout 亦无其痕迹）。

诚实边界：本套件仅联系本机 127.0.0.1 回环接收器（动态端口、用毕即
关），且三条路径的期望结局**全部是零出站请求**——接收器只是「零请求」
断言的可观测面，本切片不存在任何真实 HTTP 交换。绝不联系任何外部
端点/DNS 主机名/生产服务；secret 与 URL 只存在于 pytest 临时目录与
测试进程内存，绝不进入任何提交物；不安装/修改任何计划任务。环境无法
绑定回环（如受限沙箱）时整套 skip，而非假通过。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TASK_SCRIPT = (REPO_ROOT / "tools" / "ops"
               / "production_drift_watch_alert_task.py")

#: 任务桥（M14-137）execute 确认短语——与 M14-135 dispatch 短语互不通用
TASK_CONFIRM_PHRASE = "EXECUTE PRODUCTION DRIFT WATCH ALERT TASK"
#: 合成 secret token（仅测试内存 + pytest 临时目录；绝不入任何提交物）
RUNTIME_TOKEN = "tk-m14-140-task-runtime-4b9e"
#: 合成投毒 detail（源报告 fail check 的 detail 文本；断言绝不上 stdout）
POISON_DETAIL = "POISON-m14-140-drift-detail-97c2"
HOOK_PATH = "/hook"

#: 合成源报告身份（文件名 stamp 与 started 交叉校验 [stamp, stamp+2s]）
#: 案例 1/2：older drift=false + newest drift=true
STEM_OLDER_CLEAN = "drift-watch-20260925-100101"
STARTED_OLDER_CLEAN = "2026-09-25T10:01:02Z"
STEM_NEWEST_DRIFT = "drift-watch-20260925-110202"
STARTED_NEWEST_DRIFT = "2026-09-25T11:02:03Z"
#: 案例 3：older drift=true + newest drift=false（older 绝不触发移交）
STEM_OLDER_DRIFT = "drift-watch-20260925-120404"
STARTED_OLDER_DRIFT = "2026-09-25T12:04:05Z"
STEM_NEWEST_CLEAN = "drift-watch-20260925-130505"
STARTED_NEWEST_CLEAN = "2026-09-25T13:05:06Z"

PROJECT = "aios-m14-140-runtime-check"
API_DIGEST = "sha256:" + "33" * 32   # 合成锚点 digest（形态合法即可）
WEB_DIGEST = "sha256:" + "44" * 32
REASON_DRIFT = "container-not-healthy:api"
COUNTS_DRIFT = {"pass": 3, "fail": 1}
COUNTS_CLEAN = {"pass": 4, "fail": 0}


# ---------------------------------------------------------------- 合成 fixture


def write_report(directory: Path, *, stem: str, started: str,
                 drift: bool) -> tuple[Path, str]:
    """最小自洽 M14-127 execute 模式 drift-watch 报告（drift 布尔自选）
    → 落盘并返回 (路径, 字节 SHA-256)。fail check 的 detail 携带投毒
    marker（报告校验只看 check_id/subject/status——detail 文本绝不该
    出现在任何 stdout 面）。"""
    if drift:
        checks = [
            {"check_id": "compose-service-present", "subject": "postgres",
             "status": "pass", "detail": "present"},
            {"check_id": "compose-service-present", "subject": "web",
             "status": "pass", "detail": "present"},
            {"check_id": "anchor-image-digest", "subject": "web",
             "status": "pass", "detail": "digest matches expected"},
            {"check_id": "container-health", "subject": "api",
             "status": "fail", "detail": POISON_DETAIL},
        ]
        reasons = [REASON_DRIFT]
    else:
        checks = [
            {"check_id": "compose-service-present", "subject": "postgres",
             "status": "pass", "detail": "present"},
            {"check_id": "compose-service-present", "subject": "web",
             "status": "pass", "detail": "present"},
            {"check_id": "anchor-image-digest", "subject": "web",
             "status": "pass", "detail": "digest matches expected"},
            {"check_id": "container-health", "subject": "api",
             "status": "pass", "detail": "healthy"},
        ]
        reasons = []
    counts = {"pass": sum(1 for c in checks if c["status"] == "pass"),
              "fail": sum(1 for c in checks if c["status"] == "fail")}
    report = {
        "schema_version": 1,
        "tool": "tools/ops/production_drift_watch.py",
        "milestone": "M14-127",
        "mode": "execute",
        "started_at_utc": started,
        "ended_at_utc": _ended_after(started),
        "config": {
            "project": PROJECT,
            "profiles": ["local", "search"],
            "compose_file": "docker-compose.yml",
            "services": ["postgres", "redis", "minio", "api", "web",
                         "livekit", "searxng"],
            "anchors": {
                "api": {"expected_tag": "aios/api:m14-140-synthetic",
                        "expected_digest": API_DIGEST},
                "web": {"expected_tag": "aios/web:m14-140-synthetic",
                        "expected_digest": WEB_DIGEST},
            },
        },
        "boundaries": ["read-only collection only"],
        "collectors": {"compose_ps": {"status": "ok"},
                       "containers": {"per_service": {}},
                       "image_refs": {}},
        "checks": checks,
        "counts": counts,
        "drift": drift,
        "drift_reasons": reasons,
    }
    report["ended_at_utc"] = _ended_after(started)
    raw = json.dumps(report, ensure_ascii=False).encode("utf-8")
    path = directory / f"{stem}.json"
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def _ended_after(started: str) -> str:
    """ended = started + 1s（保持严格 UTC 形态；ended>=started 自洽）。"""
    moment = datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)
    return (moment + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_secret(directory: Path, port: int) -> Path:
    path = directory / "secret.json"
    path.write_text(json.dumps({"url": f"http://127.0.0.1:{port}{HOOK_PATH}",
                                "token": RUNTIME_TOKEN}), encoding="utf-8")
    return path


def seed_newest_drift(tmp_path: Path) -> tuple[Path, str]:
    """older drift=false + newest drift=true → 返回 (工件目录, 最新报告
    字节 SHA-256)。"""
    directory = tmp_path / "artifacts"
    directory.mkdir()
    write_report(directory, stem=STEM_OLDER_CLEAN,
                 started=STARTED_OLDER_CLEAN, drift=False)
    _, newest_sha = write_report(directory, stem=STEM_NEWEST_DRIFT,
                                 started=STARTED_NEWEST_DRIFT, drift=True)
    return directory, newest_sha


def seed_newest_clean(tmp_path: Path) -> tuple[Path, str]:
    """older drift=true + newest drift=false → 返回 (工件目录, 最新报告
    字节 SHA-256)。older drift=true 绝不触发移交（drift 只看最新）。"""
    directory = tmp_path / "artifacts"
    directory.mkdir()
    write_report(directory, stem=STEM_OLDER_DRIFT,
                 started=STARTED_OLDER_DRIFT, drift=True)
    _, newest_sha = write_report(directory, stem=STEM_NEWEST_CLEAN,
                                 started=STARTED_NEWEST_CLEAN, drift=False)
    return directory, newest_sha


# ---------------------------------------------------------------- 回环接收器


class LoopbackReceiver:
    """ephemeral loopback-only 接收器：127.0.0.1 + 动态端口 + 用毕即关。

    本套件三条路径的期望结局全部是零请求——接收器只是「零出站尝试」
    断言的可观测面；若意外收到请求，失败信息里保留方法/路径/字节数
    （绝不把 token/URL 写进任何文件）。"""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        handler = self._make_handler()
        try:
            self._server: ThreadingHTTPServer = ThreadingHTTPServer(
                ("127.0.0.1", 0), handler)
        except OSError as exc:  # 无回环绑定面（受限沙箱）→ 整套 skip
            pytest.skip(f"loopback bind unavailable ({exc.__class__.__name__})")
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self.port: int = self._server.server_address[1]

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # http.server 约定名
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                with receiver.lock:
                    receiver.requests.append(
                        {"method": self.command, "path": self.path,
                         "body_bytes": len(body)})
                response = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *args: object) -> None:  # 静默 stderr
                return

        return Handler


@pytest.fixture()
def receiver():
    server = LoopbackReceiver()
    server.start()
    try:
        yield server
    finally:
        server.close()


# ---------------------------------------------------------------- CLI 运行


def run_task_cli(*argv: str) -> subprocess.CompletedProcess:
    """真实子进程运行任务桥 CLI（PYTHONIOENCODING=utf-8 确定化中文
    stdout；env 原样继承给 M14-135 孙进程）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, str(TASK_SCRIPT), *argv],
                          capture_output=True, encoding="utf-8",
                          errors="replace", env=env, timeout=180,
                          check=False)


def assert_no_leaks(tmp_path: Path, *captured: str) -> None:
    """token / 完整 URL / 127.0.0.1 字面量 / 接收器路径 / 绝对本地
    路径 / 投毒 detail 绝不入任务桥子进程 stdout/stderr。"""
    local_paths = {str(tmp_path), str(tmp_path).replace("\\", "/")}
    for text in captured:
        assert RUNTIME_TOKEN not in text
        assert "127.0.0.1" not in text          # 完整 URL 的必要成分
        assert HOOK_PATH not in text
        assert POISON_DETAIL not in text
        for local_path in local_paths:
            assert local_path not in text


def tree_snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in sorted(root.rglob("*"))}


# ---------------------------------------------------------------- 测试


def test_execute_drift_true_real_handoff_child_fails_closed(
        tmp_path: Path, receiver: LoopbackReceiver) -> None:
    """drift=true execute：真实移交到 M14-135 子进程；白名单不转发
    --allow-loopback-http → 子进程 scheme-not-https fail-closed、零请求、
    退出码 2 透传、零工件。"""
    directory, newest_sha = seed_newest_drift(tmp_path)
    secret = write_secret(tmp_path, receiver.port)
    dispatch_out = tmp_path / "dispatch-out"

    result = run_task_cli(
        "--artifacts-dir", str(directory),
        "--secret-file", str(secret),
        "--dispatch-artifact-dir", str(dispatch_out),
        "--execute", "--confirm", TASK_CONFIRM_PHRASE)

    assert result.returncode == 2, result.stdout + result.stderr
    # 移交确实发生（桥侧移交行）且真实子进程已运行（scheme-not-https
    # 是 M14-135 移交目标族自有词汇，经桥回显——FakeRunner 打不出来）
    assert "移交" in result.stdout
    assert "scheme-not-https" in result.stdout
    # 真实目录扫描：newest 精确选中（stem + 独立重算 SHA-256），older
    # 报告零痕迹，候选计数如实
    assert STEM_NEWEST_DRIFT in result.stdout
    assert STEM_OLDER_CLEAN not in result.stdout
    assert newest_sha in result.stdout
    assert "候选 2 份" in result.stdout
    # 子进程 fail-closed：零出站请求 + 零 ledger/dispatch 工件落盘
    #（拒绝发生在 URL 校验，先于 M14-135 任何目录/工件写入）
    assert receiver.requests == []
    assert not dispatch_out.exists()
    assert_no_leaks(tmp_path, result.stdout, result.stderr)


def test_plan_drift_true_exit3_zero_requests_zero_writes(
        tmp_path: Path, receiver: LoopbackReceiver) -> None:
    """drift=true plan：exit 3 + dispatch-would-be-required + 精确
    SHA-256；零请求、零写入（目录树快照不变）。"""
    directory, newest_sha = seed_newest_drift(tmp_path)
    before = tree_snapshot(tmp_path)

    result = run_task_cli("--artifacts-dir", str(directory))

    assert result.returncode == 3, result.stdout + result.stderr
    assert "dispatch-would-be-required" in result.stdout
    assert STEM_NEWEST_DRIFT in result.stdout
    assert newest_sha in result.stdout
    # plan 零网络零子进程的运行时观测：接收器零请求 + 零新文件
    assert receiver.requests == []
    assert tree_snapshot(tmp_path) == before
    assert_no_leaks(tmp_path, result.stdout, result.stderr)


def test_execute_drift_false_skips_dispatch_child_zero_requests(
        tmp_path: Path, receiver: LoopbackReceiver) -> None:
    """drift=false execute：exit 0 + skipped-no-alerts + 零 dispatch
    子进程；older drift=true 绝不触发移交；零请求、零 dispatch 工件。"""
    directory, newest_sha = seed_newest_clean(tmp_path)
    secret = write_secret(tmp_path, receiver.port)
    dispatch_out = tmp_path / "dispatch-out"
    before = tree_snapshot(tmp_path)

    result = run_task_cli(
        "--artifacts-dir", str(directory),
        "--secret-file", str(secret),
        "--dispatch-artifact-dir", str(dispatch_out),
        "--execute", "--confirm", TASK_CONFIRM_PHRASE)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipped-no-alerts" in result.stdout
    assert "零 dispatch 子进程" in result.stdout
    # drift 判定只依据最新报告：newest drift=false 精确选中，older
    # drift=true 报告（stem 与结论）零痕迹
    assert STEM_NEWEST_CLEAN in result.stdout
    assert newest_sha in result.stdout
    assert STEM_OLDER_DRIFT not in result.stdout
    assert "drift=true" not in result.stdout
    # 零子进程副作用的运行时证明：M14-135 即便 skip 路径也会写
    # dispatch-*.json/.md（会创建工件目录）——目录不存在即子进程从未
    # 被构造；零出站请求；任务桥自身零写入
    assert receiver.requests == []
    assert not dispatch_out.exists()
    assert tree_snapshot(tmp_path) == before
    assert_no_leaks(tmp_path, result.stdout, result.stderr)
