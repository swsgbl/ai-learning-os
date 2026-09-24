r"""M14-121 monitoring_alert_dispatch 回环运行时闭环集成测试：补上
M14-119 契约测试（FakeTransport 注入）留下的「真实 HTTP 交换未实证」
边界——**仅在本机 ephemeral 回环接收器上**（127.0.0.1 动态端口）以
真实 CLI 子进程完成一次真实出站 HTTP 往返。

覆盖（黑盒：子进程调用真实 CLI，不 import 工具模块）：

- **真实 2xx 交换**：临时 loopback-only HTTP 接收器（绑定 127.0.0.1
  动态端口）+ 合成 monitor 报告 + 临时 secret JSON
  （``http://127.0.0.1:<动态端口>/hook``）→ 以
  ``--allow-loopback-http --execute --confirm "EXECUTE MONITOR ALERT
  DISPATCH"`` 调真实 CLI → exit 0；接收器恰收到一次 POST：路径/
  Authorization Bearer/Content-Type/Accept/User-Agent 头契约在内存中
  逐项核对；收到 body 的 SHA-256 与字节数和工具落盘 dispatch 报告
  登记的 payload.sha256/bytes **逐字节对账**（真实线上的 payload 就是
  报告登记的那份）；报告 SHA-256 独立重算对账；payload 顶层/报告块
  键集精确（固定词汇）。
- **sanitized 报告/台账行为**：dispatch-ledger.jsonl 恰一行、行键集
  精确、值与接收器实测一致；dispatch-*.json/.md 恰各一份、
  dispatch_status=sent、url_validation=validated-loopback-http；
  **token / 完整 URL / 127.0.0.1 字面量绝不出现在任何落盘工件或
  子进程 stdout/stderr**（接收器只在内存中断言头契约，记录面仅
  布尔/哈希/固定词汇）。
- **幂等重复拒绝**：同报告二次 execute → exit 2、stdout 含
  duplicate-dispatch、接收器仍恰一次请求（零重发）、台账仍一行、
  工件仍一份（拒绝路径零追加）。
- **fail-closed 旗标门（运行时）**：同一回环 URL 不加
  ``--allow-loopback-http`` → exit 2、scheme-not-https、接收器零请求、
  零工件落盘——http 豁免门在真实 CLI 路径上依然收紧。

诚实边界：本套件**仅**联系本机 127.0.0.1 回环接收器（动态端口、
用毕即关），绝不联系任何外部端点/DNS 主机名/生产服务；secret 与
URL 只存在于 pytest 临时目录与测试进程内存，绝不进入任何提交物。
环境无法绑定回环（如受限沙箱）时整套 skip，而非假通过。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_alert_dispatch.py"

#: 合成 secret token（仅测试内存 + pytest 临时目录；绝不入任何提交物）
RUNTIME_TOKEN = "tk-m14-121-loopback-runtime-4a7f"
HOOK_PATH = "/hook"
CONFIRM_PHRASE = "EXECUTE MONITOR ALERT DISPATCH"
EXPECTED_USER_AGENT = "aios-m14-119-monitor-alert-dispatch/1.0"

#: 报告身份常量（与 monitoring_history 单一事实源同值；黑盒测试故硬编码）
STEM = "monitor-20260924-093000"
STARTED = "2026-09-24T09:30:00Z"
REPORT_TOOL = "tools/ops/production_monitor.py"
REPORT_MILESTONE = "M14-12"
ALERT_CODE = "container-restarts:api:warn"
COUNTS = {"ok": 30, "warn": 1, "critical": 0}

#: 出站 payload / 台账行 / dispatch 报告 的固定词汇键集（与工具契约一致）
PAYLOAD_TOP_KEYS = {"schema_version", "tool", "milestone", "event",
                    "dispatched_at", "report"}
PAYLOAD_REPORT_KEYS = {"stem", "sha256", "collected_at", "overall_status",
                       "partial", "counts", "alert_codes",
                       "alert_codes_truncated_count"}
LEDGER_ROW_KEYS = {"schema_version", "tool", "milestone", "dispatch_id",
                   "dispatched_at", "report_stem", "report_sha256",
                   "overall_status", "counts", "alert_codes_count",
                   "dispatch_status", "sink", "http_status",
                   "payload_sha256", "payload_bytes"}
RECORD_KEYS = {"schema_version", "tool", "milestone", "mode", "generated_at",
               "report", "dispatch_status", "sink", "http_status",
               "transport_error", "payload", "ledger", "boundaries"}
MAX_PAYLOAD_BYTES = 16384


# ---------------------------------------------------------------- 合成 fixture


def write_report(directory: Path) -> tuple[Path, str]:
    """最小自洽 production_monitor 报告（warn ×1）→ 落盘并返回 (路径, 字节 SHA-256)。"""
    report = {
        "schema_version": 1,
        "tool": REPORT_TOOL,
        "milestone": REPORT_MILESTONE,
        "mode": "execute",
        "started_at_utc": STARTED,
        "ended_at_utc": STARTED,
        "partial": False,
        "overall_status": "warn",
        "threshold_results": {
            "counts": dict(COUNTS),
            "alerts": [{"check_id": "container-restarts", "subject": "api",
                        "severity": "warn",
                        "detail": "restart_count=1 delta=1"}],
        },
    }
    raw = json.dumps(report, ensure_ascii=False).encode("utf-8")
    path = directory / f"{STEM}.json"
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def write_secret(directory: Path, port: int) -> Path:
    path = directory / "secret.json"
    path.write_text(json.dumps({"url": f"http://127.0.0.1:{port}{HOOK_PATH}",
                                "token": RUNTIME_TOKEN}), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 回环接收器


class LoopbackReceiver:
    """ephemeral loopback-only 接收器：127.0.0.1 + 动态端口 + 用毕即关。

    头契约仅在内存断言；记录面只留布尔/哈希/字节数/解析后的固定词汇
    payload（绝不把 token/URL 写进任何文件）。"""

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
                try:
                    parsed = json.loads(body.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    parsed = None
                record = {
                    "method_post": self.command == "POST",
                    "path_ok": self.path == HOOK_PATH,
                    "auth_ok": (self.headers.get("Authorization")
                                == f"Bearer {RUNTIME_TOKEN}"),
                    "content_type_ok": (self.headers.get("Content-Type")
                                        == "application/json"),
                    "accept_ok": self.headers.get("Accept") == "application/json",
                    "user_agent_ok": (self.headers.get("User-Agent")
                                      == EXPECTED_USER_AGENT),
                    "body_ascii_only": all(b < 128 for b in body),
                    "body_bytes": len(body),
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                    "body_json": parsed,
                }
                with receiver.lock:
                    receiver.requests.append(record)
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


def run_cli(report: Path, secret: Path, artifact_dir: Path, *,
            allow_loopback_http: bool) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(SCRIPT),
            "--report", str(report),
            "--secret-file", str(secret),
            "--artifact-dir", str(artifact_dir),
            "--timeout-seconds", "5"]
    if allow_loopback_http:
        argv.append("--allow-loopback-http")
    argv += ["--execute", "--confirm", CONFIRM_PHRASE]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"  # 子进程 stdout 编码确定化
    return subprocess.run(argv, capture_output=True, encoding="utf-8",
                          errors="replace", env=env, timeout=120, check=False)


def assert_no_secrets(artifact_dir: Path, *captured: str) -> None:
    """token / 完整 URL / 127.0.0.1 字面量绝不入任何工件或子进程输出。"""
    url_host = "127.0.0.1"
    texts = list(captured)
    assert artifact_dir.exists()
    for path in sorted(artifact_dir.rglob("*")):
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8"))
    for text in texts:
        assert RUNTIME_TOKEN not in text
        assert url_host not in text
        assert "/hook" not in text


def read_ledger(artifact_dir: Path) -> list[dict]:
    ledger = artifact_dir / "dispatch-ledger.jsonl"
    lines = [line for line in ledger.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    return [json.loads(line) for line in lines]


# ---------------------------------------------------------------- 测试


def test_loopback_execute_real_exchange_and_sanitized_artifacts(
        tmp_path: Path, receiver: LoopbackReceiver) -> None:
    """真实 2xx 交换 + sanitized 报告/台账（真实 CLI 子进程 + 回环接收器）。"""
    report_path, report_sha = write_report(tmp_path)
    secret_path = write_secret(tmp_path, receiver.port)
    artifacts = tmp_path / "artifacts"

    result = run_cli(report_path, secret_path, artifacts,
                     allow_loopback_http=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "http_status=200" in result.stdout
    # 接收器恰一次请求（零重试契约的运行时面）；头契约逐项核对
    assert len(receiver.requests) == 1
    seen = receiver.requests[0]
    assert seen["method_post"] is True
    assert seen["path_ok"] is True
    assert seen["auth_ok"] is True, "Authorization Bearer 头不匹配"
    assert seen["content_type_ok"] is True
    assert seen["accept_ok"] is True
    assert seen["user_agent_ok"] is True
    assert seen["body_ascii_only"] is True
    assert 0 < seen["body_bytes"] <= MAX_PAYLOAD_BYTES
    # 线上 payload = 固定词汇；报告身份与测试独立重算的 SHA-256 对账
    payload = seen["body_json"]
    assert isinstance(payload, dict)
    assert set(payload) == PAYLOAD_TOP_KEYS
    assert payload["schema_version"] == 1
    assert payload["tool"] == "tools/ops/monitoring_alert_dispatch.py"
    assert payload["milestone"] == "M14-119"
    assert payload["event"] == "monitor-alert"
    block = payload["report"]
    assert isinstance(block, dict)
    assert set(block) == PAYLOAD_REPORT_KEYS
    assert block["stem"] == STEM
    assert block["sha256"] == report_sha
    assert block["collected_at"] == STARTED
    assert block["overall_status"] == "warn"
    assert block["partial"] is False
    assert block["counts"] == COUNTS
    assert block["alert_codes"] == [ALERT_CODE]
    assert block["alert_codes_truncated_count"] == 0
    # dispatch 报告恰一份；登记的 payload 指纹与接收器实测逐字节对账
    dispatch_jsons = sorted(artifacts.glob("dispatch-*.json"))
    dispatch_mds = sorted(artifacts.glob("dispatch-*.md"))
    assert len(dispatch_jsons) == 1
    assert len(dispatch_mds) == 1
    record = json.loads(dispatch_jsons[0].read_text(encoding="utf-8"))
    assert set(record) == RECORD_KEYS
    assert record["mode"] == "execute"
    assert record["dispatch_status"] == "sent"
    assert record["http_status"] == 200
    assert record["transport_error"] is None
    assert record["sink"] == {"kind": "generic-https-webhook",
                              "url_validation": "validated-loopback-http"}
    assert record["report"]["sha256"] == report_sha
    assert record["report"]["stem"] == STEM
    assert record["payload"]["sha256"] == seen["body_sha256"]
    assert record["payload"]["bytes"] == seen["body_bytes"]
    assert record["payload"]["alert_codes_count"] == 1
    assert record["payload"]["alert_codes_truncated_count"] == 0
    assert record["ledger"] == {"status": "appended", "entries": 1}
    # 台账恰一行、键集精确、值与实测一致
    rows = read_ledger(artifacts)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == LEDGER_ROW_KEYS
    assert row["dispatch_status"] == "sent"
    assert row["report_sha256"] == report_sha
    assert row["report_stem"] == STEM
    assert row["http_status"] == 200
    assert row["payload_sha256"] == seen["body_sha256"]
    assert row["payload_bytes"] == seen["body_bytes"]
    assert row["alert_codes_count"] == 1
    assert row["counts"] == COUNTS
    # 脱敏：token/URL/回环字面量绝不入任何工件或子进程输出
    assert_no_secrets(artifacts, result.stdout, result.stderr)


def test_loopback_execute_duplicate_report_rejected_zero_resend(
        tmp_path: Path, receiver: LoopbackReceiver) -> None:
    """幂等重复拒绝：同报告二次 execute → exit 2、零重发、台账/工件零追加。"""
    report_path, report_sha = write_report(tmp_path)
    secret_path = write_secret(tmp_path, receiver.port)
    artifacts = tmp_path / "artifacts"

    first = run_cli(report_path, secret_path, artifacts,
                    allow_loopback_http=True)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "http_status=200" in first.stdout
    assert len(receiver.requests) == 1

    second = run_cli(report_path, secret_path, artifacts,
                     allow_loopback_http=True)
    assert second.returncode == 2, second.stdout + second.stderr
    assert "duplicate-dispatch" in second.stdout
    # 零重发：接收器仍恰一次请求；台账仍一行；工件仍恰一份
    assert len(receiver.requests) == 1
    rows = read_ledger(artifacts)
    assert len(rows) == 1
    assert rows[0]["report_sha256"] == report_sha
    assert len(sorted(artifacts.glob("dispatch-*.json"))) == 1
    assert len(sorted(artifacts.glob("dispatch-*.md"))) == 1
    assert_no_secrets(artifacts, first.stdout, first.stderr,
                      second.stdout, second.stderr)


def test_loopback_http_requires_explicit_flag(
        tmp_path: Path, receiver: LoopbackReceiver) -> None:
    """fail-closed 门（运行时）：不加 --allow-loopback-http → 拒绝且零请求零工件。"""
    report_path, _report_sha = write_report(tmp_path)
    secret_path = write_secret(tmp_path, receiver.port)
    artifacts = tmp_path / "artifacts"

    result = run_cli(report_path, secret_path, artifacts,
                     allow_loopback_http=False)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "scheme-not-https" in result.stdout
    assert receiver.requests == []  # 零出站尝试
    assert not artifacts.exists()   # 拒绝路径零落盘
    assert RUNTIME_TOKEN not in result.stdout + result.stderr
    assert "127.0.0.1" not in result.stdout + result.stderr
