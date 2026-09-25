r"""M14-135 production_drift_watch_alert_dispatch 回环运行时闭环集成
测试（M14-136 测试切片）：补上 M14-135 契约测试（注入 FakeTransport/
FakeClock）留下的「真实 HTTP 交换未实证」边界——**仅在本机 ephemeral
回环接收器上**（127.0.0.1 动态端口）以真实 CLI 子进程完成一次真实出站
HTTP 往返。模板与断言口径对齐 M14-121
（``test_monitoring_alert_dispatch_runtime.py``）。

覆盖（黑盒：子进程调用真实 CLI，不 import 工具模块）：

- **真实 2xx 交换**：临时 loopback-only HTTP 接收器（绑定 127.0.0.1
  动态端口）+ 合成 drift=true M14-127 execute 模式报告 + 临时 secret
  JSON（``http://127.0.0.1:<动态端口>/hook``）→ 以
  ``--allow-loopback-http --execute --confirm "EXECUTE PRODUCTION
  DRIFT WATCH ALERT DISPATCH"`` 调真实 CLI → exit 0；接收器恰收到
  一次 POST（零重试契约的运行时面）：方法/路径/Authorization Bearer/
  Content-Type/Accept/User-Agent 头契约在内存中逐项核对；收到 body 的
  SHA-256 与字节数和工具落盘 dispatch 报告登记的
  payload.sha256/bytes **逐字节对账**；报告 SHA-256 独立重算对账；
  payload 顶层/报告块键集精确（固定词汇）；源报告 detail 投毒文本
  绝不上线。
- **sanitized 报告/台账行为**：drift-dispatch-ledger.jsonl 恰一行、
  16 键精确、值与接收器实测一致；dispatch-*.json/.md 恰各一份、
  dispatch_status=sent、url_validation=validated-loopback-http；
  **token / 完整 URL / 127.0.0.1 字面量 / 接收器路径 / 绝对本地
  路径 / 投毒 report detail 绝不出现在任何落盘工件或子进程
  stdout/stderr**（接收器只在内存中断言头契约，记录面仅布尔/哈希/
  固定词汇）。
- **幂等重复拒绝**：同报告二次 execute → exit 2、stdout 含
  duplicate-dispatch、接收器仍恰一次请求（零重发）、台账仍一行、
  工件仍一份（拒绝路径零追加、零新增 sent-success 工件）。
- **fail-closed 旗标门（运行时）**：同一回环 URL 不加
  ``--allow-loopback-http`` → exit 2、scheme-not-https、接收器零
  请求、零工件落盘——http 豁免门在真实 CLI 路径上依然收紧。
- **drift=false 无告警路径**：合法 execute 报告 drift=false →
  exit 0、stdout 含 skipped-no-alerts、接收器零请求、台账文件不
  存在（零触碰）——分发判定确实只依据报告自身布尔。

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
SCRIPT = (REPO_ROOT / "tools" / "ops"
          / "production_drift_watch_alert_dispatch.py")

#: 合成 secret token（仅测试内存 + pytest 临时目录；绝不入任何提交物）
RUNTIME_TOKEN = "tk-m14-136-loopback-runtime-8e2d"
#: 合成投毒 detail（源报告 fail check 的 detail 文本；断言绝不上线/入档）
POISON_DETAIL = "POISON-m14-136-drift-detail-c31f"
HOOK_PATH = "/hook"
CONFIRM_PHRASE = "EXECUTE PRODUCTION DRIFT WATCH ALERT DISPATCH"
EXPECTED_USER_AGENT = "aios-m14-135-drift-watch-alert-dispatch/1.0"

#: 合成源报告身份（文件名 stamp 与 started 交叉校验 [stamp, stamp+2s]）
STEM_DRIFT = "drift-watch-20260925-060101"
STARTED_DRIFT = "2026-09-25T06:01:02Z"
ENDED_DRIFT = "2026-09-25T06:01:03Z"
STEM_CLEAN = "drift-watch-20260925-070202"
STARTED_CLEAN = "2026-09-25T07:02:02Z"
ENDED_CLEAN = "2026-09-25T07:02:03Z"
PROJECT = "aios-m14-136-runtime-check"
API_DIGEST = "sha256:" + "11" * 32   # 合成锚点 digest（形态合法即可）
WEB_DIGEST = "sha256:" + "22" * 32
REASON_DRIFT = "container-not-healthy:api"
COUNTS_DRIFT = {"pass": 3, "fail": 1}
COUNTS_CLEAN = {"pass": 4, "fail": 0}

#: 出站 payload / 台账行 / dispatch 报告 的固定词汇键集（与工具契约一致）
PAYLOAD_TOP_KEYS = {"schema_version", "tool", "milestone", "event",
                    "dispatched_at", "report"}
PAYLOAD_REPORT_KEYS = {"stem", "sha256", "started_at_utc", "ended_at_utc",
                       "project", "drift", "counts", "drift_reasons",
                       "drift_reasons_truncated_count"}
LEDGER_ROW_KEYS = {"schema_version", "tool", "milestone", "dispatch_id",
                   "dispatched_at", "report_stem", "report_sha256",
                   "report_started_at_utc", "drift", "counts",
                   "drift_reasons_count", "dispatch_status", "sink",
                   "http_status", "payload_sha256", "payload_bytes"}
RECORD_KEYS = {"schema_version", "tool", "milestone", "mode", "generated_at",
               "report", "dispatch_decision", "dispatch_status", "sink",
               "http_status", "transport_error", "payload", "ledger",
               "boundaries"}
RECORD_REPORT_KEYS = {"stem", "sha256", "started_at_utc", "ended_at_utc",
                      "project", "drift", "counts"}
RECORD_PAYLOAD_KEYS = {"sha256", "bytes", "drift_reasons_count",
                       "drift_reasons_truncated_count"}
LEDGER_NAME = "drift-dispatch-ledger.jsonl"
DISPATCH_ID_RE_TEXT = r"^\d{8}-\d{6}-[0-9a-f]{8}$"
MAX_PAYLOAD_BYTES = 16384


# ---------------------------------------------------------------- 合成 fixture


def write_drift_report(directory: Path, *, drift: bool) -> tuple[Path, str]:
    """最小自洽 M14-127 execute 模式 drift-watch 报告（drift 布尔自选）
    → 落盘并返回 (路径, 字节 SHA-256)。fail check 的 detail 携带投毒
    marker（校验只看 check_id/subject/status——detail 文本必须被工具
    的固定词汇 payload 边界挡在门外）。"""
    if drift:
        stem, started, ended = STEM_DRIFT, STARTED_DRIFT, ENDED_DRIFT
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
        stem, started, ended = STEM_CLEAN, STARTED_CLEAN, ENDED_CLEAN
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
        "ended_at_utc": ended,
        "config": {
            "project": PROJECT,
            "profiles": ["local", "search"],
            "compose_file": "docker-compose.yml",
            "services": ["postgres", "redis", "minio", "api", "web",
                         "livekit", "searxng"],
            "anchors": {
                "api": {"expected_tag": "aios/api:m14-136-synthetic",
                        "expected_digest": API_DIGEST},
                "web": {"expected_tag": "aios/web:m14-136-synthetic",
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
    raw = json.dumps(report, ensure_ascii=False).encode("utf-8")
    path = directory / f"{stem}.json"
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
                    "accept_ok": (self.headers.get("Accept")
                                  == "application/json"),
                    "user_agent_ok": (self.headers.get("User-Agent")
                                      == EXPECTED_USER_AGENT),
                    "body_ascii_only": all(b < 128 for b in body),
                    "poison_in_body": POISON_DETAIL.encode() in body,
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


def assert_no_secrets(artifact_dir: Path, tmp_path: Path,
                      *captured: str) -> None:
    """token / 完整 URL / 127.0.0.1 字面量 / 接收器路径 / 绝对本地
    路径 / 投毒 detail 绝不入任何工件或子进程输出。"""
    loopback_host = "127.0.0.1"
    local_paths = {str(tmp_path), str(tmp_path).replace("\\", "/")}
    texts = list(captured)
    assert artifact_dir.exists()
    for path in sorted(artifact_dir.rglob("*")):
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8"))
    for text in texts:
        assert RUNTIME_TOKEN not in text
        assert loopback_host not in text
        assert HOOK_PATH not in text
        assert POISON_DETAIL not in text
        for local_path in local_paths:
            assert local_path not in text


def read_ledger(artifact_dir: Path) -> list[dict]:
    ledger = artifact_dir / LEDGER_NAME
    lines = [line for line in ledger.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    return [json.loads(line) for line in lines]


# ---------------------------------------------------------------- 测试


def test_loopback_execute_real_exchange_and_sanitized_artifacts(
        tmp_path: Path, receiver: LoopbackReceiver) -> None:
    """真实 2xx 交换 + sanitized 报告/台账（真实 CLI 子进程 + 回环接收器）。"""
    report_path, report_sha = write_drift_report(tmp_path, drift=True)
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
    assert seen["poison_in_body"] is False, "投毒 report detail 上了线"
    assert 0 < seen["body_bytes"] <= MAX_PAYLOAD_BYTES
    # 线上 payload = 固定词汇；报告身份与测试独立重算的 SHA-256 对账
    payload = seen["body_json"]
    assert isinstance(payload, dict)
    assert set(payload) == PAYLOAD_TOP_KEYS
    assert payload["schema_version"] == 1
    assert payload["tool"] == "tools/ops/production_drift_watch_alert_dispatch.py"
    assert payload["milestone"] == "M14-135"
    assert payload["event"] == "drift-watch-alert"
    block = payload["report"]
    assert isinstance(block, dict)
    assert set(block) == PAYLOAD_REPORT_KEYS
    assert block["stem"] == STEM_DRIFT
    assert block["sha256"] == report_sha
    assert block["started_at_utc"] == STARTED_DRIFT
    assert block["ended_at_utc"] == ENDED_DRIFT
    assert block["project"] == PROJECT
    assert block["drift"] is True
    assert block["counts"] == COUNTS_DRIFT
    assert block["drift_reasons"] == [REASON_DRIFT]
    assert block["drift_reasons_truncated_count"] == 0
    # dispatch 报告恰一份（零 plan 工件）；登记的 payload 指纹与接收器
    # 实测逐字节对账（真实线上的 payload 就是报告登记的那份）
    dispatch_jsons = sorted(artifacts.glob("dispatch-*.json"))
    dispatch_mds = sorted(artifacts.glob("dispatch-*.md"))
    assert len(dispatch_jsons) == 1
    assert len(dispatch_mds) == 1
    assert sorted(artifacts.glob("plan-*.json")) == []
    record = json.loads(dispatch_jsons[0].read_text(encoding="utf-8"))
    assert set(record) == RECORD_KEYS
    assert record["mode"] == "execute"
    assert record["dispatch_decision"] == "dispatch"
    assert record["dispatch_status"] == "sent"
    assert record["http_status"] == 200
    assert record["transport_error"] is None
    assert record["sink"] == {"kind": "generic-https-webhook",
                              "url_validation": "validated-loopback-http"}
    report_block = record["report"]
    assert isinstance(report_block, dict)
    assert set(report_block) == RECORD_REPORT_KEYS
    assert report_block["stem"] == STEM_DRIFT
    assert report_block["sha256"] == report_sha  # 报告 SHA-256 独立重算对账
    assert report_block["drift"] is True
    assert report_block["counts"] == COUNTS_DRIFT
    payload_block = record["payload"]
    assert isinstance(payload_block, dict)
    assert set(payload_block) == RECORD_PAYLOAD_KEYS
    assert payload_block["sha256"] == seen["body_sha256"]
    assert payload_block["bytes"] == seen["body_bytes"]
    assert payload_block["drift_reasons_count"] == 1
    assert payload_block["drift_reasons_truncated_count"] == 0
    assert record["ledger"] == {"status": "appended", "entries": 1}
    # 台账恰一行、16 键精确、值与实测一致
    rows = read_ledger(artifacts)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == LEDGER_ROW_KEYS
    assert row["tool"] == "tools/ops/production_drift_watch_alert_dispatch.py"
    assert row["milestone"] == "M14-135"
    assert row["dispatch_status"] == "sent"
    assert row["report_sha256"] == report_sha
    assert row["report_stem"] == STEM_DRIFT
    assert row["report_started_at_utc"] == STARTED_DRIFT
    assert row["drift"] is True
    assert row["counts"] == COUNTS_DRIFT
    assert row["drift_reasons_count"] == 1
    assert row["sink"] == "generic-https-webhook"
    assert row["http_status"] == 200
    assert row["payload_sha256"] == seen["body_sha256"]
    assert row["payload_bytes"] == seen["body_bytes"]
    # 脱敏：token/URL/回环字面量/绝对路径/投毒 detail 绝不入工件或输出
    assert_no_secrets(artifacts, tmp_path, result.stdout, result.stderr)


def test_loopback_execute_duplicate_report_rejected_zero_resend(
        tmp_path: Path, receiver: LoopbackReceiver) -> None:
    """幂等重复拒绝：同报告二次 execute → exit 2、零重发、零追加。"""
    report_path, report_sha = write_drift_report(tmp_path, drift=True)
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
    # 零重发：接收器仍恰一次请求；台账仍一行；sent 工件仍恰一份
    assert len(receiver.requests) == 1
    rows = read_ledger(artifacts)
    assert len(rows) == 1
    assert rows[0]["report_sha256"] == report_sha
    assert len(sorted(artifacts.glob("dispatch-*.json"))) == 1
    assert len(sorted(artifacts.glob("dispatch-*.md"))) == 1
    assert_no_secrets(artifacts, tmp_path, first.stdout, first.stderr,
                      second.stdout, second.stderr)


def test_loopback_http_requires_explicit_flag(
        tmp_path: Path, receiver: LoopbackReceiver) -> None:
    """fail-closed 门（运行时）：不加 --allow-loopback-http → 拒绝且
    零请求零工件。"""
    report_path, _report_sha = write_drift_report(tmp_path, drift=True)
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


def test_loopback_execute_drift_false_skipped_no_alerts(
        tmp_path: Path, receiver: LoopbackReceiver) -> None:
    """drift=false execute：exit 0 + skipped-no-alerts + 接收器零请求 +
    台账零触碰（分发判定只依据权威报告布尔）。"""
    report_path, report_sha = write_drift_report(tmp_path, drift=False)
    secret_path = write_secret(tmp_path, receiver.port)
    artifacts = tmp_path / "artifacts"

    result = run_cli(report_path, secret_path, artifacts,
                     allow_loopback_http=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipped-no-alerts" in result.stdout
    assert receiver.requests == []          # 零出站尝试
    assert not (artifacts / LEDGER_NAME).exists()  # 台账零写入
    dispatch_jsons = sorted(artifacts.glob("dispatch-*.json"))
    dispatch_mds = sorted(artifacts.glob("dispatch-*.md"))
    assert len(dispatch_jsons) == 1
    assert len(dispatch_mds) == 1
    record = json.loads(dispatch_jsons[0].read_text(encoding="utf-8"))
    assert set(record) == RECORD_KEYS
    assert record["mode"] == "execute"
    assert record["dispatch_decision"] == "skip"
    assert record["dispatch_status"] == "skipped-no-alerts"
    assert record["http_status"] is None
    assert record["transport_error"] is None
    assert record["report"]["stem"] == STEM_CLEAN   # type: ignore[index]
    assert record["report"]["sha256"] == report_sha  # type: ignore[index]
    assert record["report"]["drift"] is False        # type: ignore[index]
    assert record["payload"]["sha256"] is None       # type: ignore[index]
    assert record["ledger"] == {"status": "not-applicable", "entries": None}
    assert_no_secrets(artifacts, tmp_path, result.stdout, result.stderr)
