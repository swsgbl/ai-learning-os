r"""M14-119 tools/ops/monitoring_alert_dispatch.py 契约测试：既有
production_monitor JSON 报告的最小外部告警分发闭环（fail-closed、可审计）。

覆盖（全部 I/O 经真实临时目录或 FakeStore/FakeTransport 注入——**构造上
零真实网络**：出站 HTTP 仅经注入 Transport，真实 Transport 在测试中绝不
构造；socket+subprocess 双阻断下 plan 端到端照常成功）：

- 结构契约：源码 import 白名单（纯标准库 + 同仓监控模块）、零 env 值
  读取 token、plan 模式零 Transport 构造（计数工厂）、双阻断零网络、
  ops README 文档化；
- 门禁 fail-closed：默认 plan（零网络零分发）、--execute 缺确认短语/
  近似短语拒绝且零 Transport 构造；
- 报告校验（malformed 一律固定词汇拒绝、零分发）：not-json、非对象、
  schema_version/tool/milestone/mode/时间戳/partial/threshold_results/
  counts/alerts 字段非法、counts 与 alerts 不自洽、overall_status 与
  counts/partial 不自洽、stem 非法；
- SSRF/unsafe URL：http 拒（未豁免）、http+loopback 旗标+字面回环 IP
  放行、http+旗标+非回环拒、字面私网/链路本地/未指定/组播/保留 IP 拒、
  localhost 名称恒拒、userinfo/query/fragment/坏端口、无 host；
- secret 文件：缺失/symlink/超大/invalid JSON/非对象/缺 url/未知键/
  token 空串或超长——固定词汇拒绝且值绝不回显；
- 成功路径：2xx → sent、ledger 恰追加一行、payload 结构/固定词汇/有界、
  exit 0；
- no-alert：ok 且零告警 → skipped-no-alerts、零 Transport 调用、exit 0；
- HTTP 失败：500/302/timeout/connection refused → failed、ledger 零追加、
  exit 2（失败绝不报告为 sent）；
- 幂等/ledger：同 report sha256 二次 execute → duplicate-dispatch 拒绝且
  零 Transport 调用；ledger malformed 行/字段非法 → fail-closed 拒绝；
- 原子写/输出路径：报告文件名防碰撞（绝不覆盖既有文件）、输出 symlink
  拒绝；
- 脱敏：报告投毒 detail 携带 marker token 绝不进入 payload/ledger/
  dispatch 报告/stdout；secret url 与 token 绝不出现在任何输出面。
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess as subprocess_module
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "monitoring_alert_dispatch.py"
OPS_README = REPO_ROOT / "tools" / "ops" / "README.md"

#: 投毒 marker（注入报告 detail / secret，断言绝不进入任何输出面）
MARK_TOKEN = "sk-ZXmarker0123456789"
SECRET_URL = "https://hooks.example.invalid/T000/B000/xyz"
SECRET_TOKEN = "tk-secret-value-0123456789"

STEM = "monitor-20260923-130002"
STARTED = "2026-09-23T13:00:02Z"


def _load_module():
    spec = importlib.util.spec_from_file_location("monitoring_alert_dispatch", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


mad = _load_module()


# ---------------------------------------------------------------- fixtures


def make_alert(check_id: str = "container-restarts", subject: str = "api",
               severity: str = "warn", detail: str = "restart_count=1 delta=1") -> dict:
    return {"check_id": check_id, "subject": subject, "severity": severity,
            "detail": detail}


def make_report(*, overall: str = "warn", partial: bool = False,
                alerts: list[dict] | None = None,
                counts: dict | None = None,
                started: str = STARTED,
                schema_version: int = 1,
                tool: str = "tools/ops/production_monitor.py",
                milestone: str = "M14-12",
                mode: str = "execute") -> dict:
    if alerts is None:
        alerts = [make_alert()]
    if counts is None:
        counts = {
            "ok": 30,
            "warn": sum(1 for a in alerts if a.get("severity") == "warn"),
            "critical": sum(1 for a in alerts if a.get("severity") == "critical"),
        }
    return {
        "schema_version": schema_version,
        "tool": tool,
        "milestone": milestone,
        "mode": mode,
        "started_at_utc": started,
        "ended_at_utc": started,
        "config": {"project": "aios-m14-03-production-rehearsal"},
        "collectors": {},
        "partial": partial,
        "overall_status": overall,
        "monitoring_ready": False,
        "threshold_results": {"counts": counts, "alerts": alerts},
    }


def write_report(tmp_path: Path, report: dict, name: str = f"{STEM}.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return path


def write_secret(tmp_path: Path, payload: dict, name: str = "secret.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class FakeTransport:
    """注入 Transport：记录调用；可注入 HTTP 状态或异常。"""

    def __init__(self, *, status: int = 200, error: Exception | None = None) -> None:
        self.status = status
        self.error = error
        self.calls: list[dict] = []

    def post_json(self, host: str, port: int, path: str, *, body: bytes,
                  headers: dict[str, str], timeout: float):
        self.calls.append({"host": host, "port": port, "path": path,
                           "body": body, "headers": dict(headers),
                           "timeout": timeout})
        if self.error is not None:
            raise self.error
        return SimpleNamespace(status=self.status, error_category=None,
                               error_class=None)


class FakeStore:
    """注入 Store（monitoring_history.Store 协议形状 + symlink 面）。"""

    def __init__(self, *, files: dict[str, bytes] | None = None,
                 symlinks: tuple[str, ...] = ()) -> None:
        self.files: dict[str, bytes] = dict(files or {})
        self.symlinks: set[str] = set(symlinks)
        self.written: dict[str, str] = {}

    def exists(self, path: Path) -> bool:
        return path.name in self.files or path.name in self.written

    def is_symlink(self, path: Path) -> bool:
        return path.name in self.symlinks

    def list_dir(self, directory: Path) -> list[str]:
        return sorted(self.files)

    def read_bytes(self, path: Path) -> bytes:
        if path.name in self.symlinks:
            raise OSError("symlink")
        return self.files[path.name]

    def mkdirs(self, directory: Path) -> None:
        return None

    def write_atomic(self, path: Path, text: str) -> None:
        self.written[path.name] = text


def _patch(monkeypatch: pytest.MonkeyPatch, *, store=None, transport=None,
           clock=None):
    monkeypatch.setattr(mad, "RealStore", lambda: store if store is not None else mad._history.RealStore())
    if transport is not None:
        monkeypatch.setattr(mad, "RealTransport", lambda scheme: transport)
    if clock is not None:
        monkeypatch.setattr(mad, "RealClock", lambda: clock)


class FakeClock:
    def __init__(self) -> None:
        self.n = 0

    def utc_now_iso(self) -> str:
        return "2026-09-24T02:00:00Z"

    def stamp(self) -> str:
        return "20260924-020000"

    def token_hex(self, nibbles: int) -> str:
        self.n += 1
        return f"{self.n:032x}"[:nibbles * 2]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dispatch_reports(directory: Path) -> list[Path]:
    return sorted(directory.glob("dispatch-*.json"))


def _ledger_rows(directory: Path) -> list[dict]:
    ledger = directory / "dispatch-ledger.jsonl"
    if not ledger.exists():
        return []
    return [json.loads(line) for line in
            ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------- 结构契约


def test_source_import_whitelist() -> None:
    """纯标准库 + 同仓监控模块；禁 requests/urllib/httpx/环境值读取。"""
    source = SCRIPT.read_text(encoding="utf-8")
    # urllib.parse（纯 URL 解析，与 production_monitor 同款）允许；
    # urllib.request / urlopen / 第三方 HTTP 库 / env 值读取一律禁止
    for banned in ("import requests", "import urllib.request",
                   "from urllib.request", "urlopen", "import httpx",
                   "os.environ[", "os.getenv", "os.environ.get"):
        assert banned not in source, f"banned token in source: {banned}"
    assert "import monitoring_history" in source
    assert "import production_monitor" in source


def test_constants_single_source() -> None:
    """schema 身份常量复用 monitoring_history 单一事实源。"""
    assert mad.EXPECTED_MONITOR_SCHEMA_VERSION == 1
    assert mad.MONITOR_TOOL_NAME == "tools/ops/production_monitor.py"
    assert mad.EXPECTED_MILESTONE == "M14-12"
    assert mad.ARTIFACT_STEM_RE.pattern.startswith("^monitor-")
    assert mad.CONFIRM_PHRASE == "EXECUTE MONITOR ALERT DISPATCH"
    assert mad.SINK_KIND == "generic-https-webhook"


def test_plan_mode_never_constructs_transport(tmp_path, monkeypatch) -> None:
    """plan（默认）零 Transport 构造（计数工厂结构性证明）。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL, "token": SECRET_TOKEN})
    counter = {"n": 0}

    def factory(scheme):
        counter["n"] += 1
        raise AssertionError("plan mode must not construct RealTransport")

    monkeypatch.setattr(mad, "RealTransport", factory)
    rc = mad.main(["--report", str(report), "--secret-file", str(secret),
                   "--artifact-dir", str(tmp_path / "out")])
    assert rc == 0
    assert counter["n"] == 0


def test_plan_end_to_end_with_socket_and_subprocess_blocked(
        tmp_path, monkeypatch) -> None:
    """socket+subprocess 双阻断下 plan 照常成功（零网络构造证明）。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL, "token": SECRET_TOKEN})

    def _blocked(*args, **kwargs):
        raise AssertionError("network/subprocess blocked")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(subprocess_module, "Popen", _blocked)
    monkeypatch.setattr(subprocess_module, "run", _blocked)
    rc = mad.main(["--report", str(report), "--secret-file", str(secret),
                   "--artifact-dir", str(tmp_path / "out")])
    assert rc == 0
    assert (tmp_path / "out" / "dispatch-ledger.jsonl").exists() is False


def test_ops_readme_documents_tool() -> None:
    text = OPS_README.read_text(encoding="utf-8")
    assert "monitoring_alert_dispatch.py" in text
    assert "M14-119" in text


# ---------------------------------------------------------------- 门禁


def test_execute_requires_exact_confirm_phrase(tmp_path, monkeypatch) -> None:
    """--execute 缺短语/近似短语 → 拒绝且零 Transport 构造。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    counter = {"n": 0}

    def factory(scheme):
        counter["n"] += 1
        raise AssertionError("must not construct transport without confirm")

    monkeypatch.setattr(mad, "RealTransport", factory)
    for phrase in ("", "EXECUTE MONITOR ALERT DISPATCH ", "execute monitor alert dispatch"):
        rc = mad.main(["--report", str(report), "--secret-file", str(secret),
                       "--execute", "--confirm", phrase,
                       "--artifact-dir", str(tmp_path / "out")])
        assert rc == 2
    assert counter["n"] == 0


def test_timeout_bounds_validated_in_plan(tmp_path) -> None:
    report = write_report(tmp_path, make_report())
    for bad in ("0", "0.1", "31", "nan", "inf"):
        rc = mad.main(["--report", str(report),
                       "--timeout-seconds", bad,
                       "--artifact-dir", str(tmp_path / "out")])
        assert rc == 2, bad


# ---------------------------------------------------------------- 报告校验（malformed）


def test_missing_report_refused(tmp_path) -> None:
    rc = mad.main(["--report", str(tmp_path / "nope.json"),
                   "--artifact-dir", str(tmp_path / "out")])
    assert rc == 2


@pytest.mark.parametrize("name", ["plan-20260923-130002.json", "monitor-bad.json",
                                  "monitor-20260923-13.json", "notes.txt"])
def test_bad_report_stem_refused(tmp_path, name) -> None:
    (tmp_path / name).write_text("{}", encoding="utf-8")
    rc = mad.main(["--report", str(tmp_path / name),
                   "--artifact-dir", str(tmp_path / "out")])
    assert rc == 2


def test_report_not_json_refused(tmp_path) -> None:
    path = tmp_path / f"{STEM}.json"
    path.write_text("not json", encoding="utf-8")
    assert mad.main(["--report", str(path),
                     "--artifact-dir", str(tmp_path / "out")]) == 2


@pytest.mark.parametrize("kwargs,reason", [
    ({"schema_version": 2}, "report-schema-version"),
    ({"tool": "tools/ops/other.py"}, "report-tool"),
    ({"milestone": "M14-99"}, "report-milestone"),
    ({"mode": "plan"}, "report-mode"),
    ({"started": "2026-09-23 13:00:02"}, "report-timestamp"),
    ({"started": "garbage"}, "report-timestamp"),
])
def test_report_identity_refused(tmp_path, kwargs, reason) -> None:
    report = write_report(tmp_path, make_report(**kwargs))
    rc = mad.main(["--report", str(report),
                   "--artifact-dir", str(tmp_path / "out")])
    assert rc == 2


def test_report_partial_not_bool_refused(tmp_path) -> None:
    data = make_report()
    data["partial"] = "false"
    report = write_report(tmp_path, data)
    assert mad.main(["--report", str(report),
                     "--artifact-dir", str(tmp_path / "out")]) == 2


def test_report_missing_threshold_results_refused(tmp_path) -> None:
    data = make_report()
    del data["threshold_results"]
    report = write_report(tmp_path, data)
    assert mad.main(["--report", str(report),
                     "--artifact-dir", str(tmp_path / "out")]) == 2


def test_report_bad_overall_status_refused(tmp_path) -> None:
    data = make_report(overall="bogus")
    report = write_report(tmp_path, data)
    assert mad.main(["--report", str(report),
                     "--artifact-dir", str(tmp_path / "out")]) == 2


def test_report_counts_alerts_mismatch_refused(tmp_path) -> None:
    data = make_report(counts={"ok": 30, "warn": 5, "critical": 0})  # 1 条 warn alert 却记 5
    report = write_report(tmp_path, data)
    assert mad.main(["--report", str(report),
                     "--artifact-dir", str(tmp_path / "out")]) == 2


def test_report_overall_mismatch_refused(tmp_path) -> None:
    # counts warn=1、critical=0、partial=False → overall 必为 warn；谎报 ok 拒绝
    data = make_report(overall="ok")
    report = write_report(tmp_path, data)
    assert mad.main(["--report", str(report),
                     "--artifact-dir", str(tmp_path / "out")]) == 2


def test_report_alert_field_invalid_refused(tmp_path) -> None:
    for mutate in (
        {"check_id": ""}, {"subject": ""}, {"severity": "ok"},
        {"severity": "info"}, {"check_id": "x" * 200},
    ):
        data = make_report(alerts=[make_alert(**mutate)])
        report = write_report(tmp_path, data)
        assert mad.main(["--report", str(report),
                         "--artifact-dir", str(tmp_path / "out")]) == 2, mutate


def test_report_alerts_not_list_refused(tmp_path) -> None:
    data = make_report()
    data["threshold_results"]["alerts"] = {"severity": "warn"}
    report = write_report(tmp_path, data)
    assert mad.main(["--report", str(report),
                     "--artifact-dir", str(tmp_path / "out")]) == 2


# ---------------------------------------------------------------- SSRF / unsafe URL


def _url_probe(url: str, *, allow: bool = False):
    return mad.validate_webhook_url(url, allow_loopback_http=allow)


@pytest.mark.parametrize("url", [
    "http://example.invalid/hook",                    # 未豁免 http
    "http://127.0.0.1:9000/hook",                     # 未豁免回环
    "ftp://example.invalid/hook",                     # 非 http(s)
    "https://user:pw@example.invalid/hook",           # userinfo
    "https://example.invalid/hook?a=1",               # query
    "https://example.invalid/hook#f",                 # fragment
    "https://localhost/hook",                         # localhost 名称恒拒
    "https://127.0.0.1:9000/hook",                    # https 字面回环 IP
    "https://10.0.0.5/hook",                          # RFC1918
    "https://192.168.1.5/hook",                       # RFC1918
    "https://172.16.0.5/hook",                        # RFC1918
    "https://169.254.169.254/hook",                   # 链路本地元数据
    "https://0.0.0.0/hook",                           # 未指定地址
    "https://224.0.0.1/hook",                         # 组播
    "https://240.0.0.1/hook",                         # 保留
    "https://[::1]:9000/hook",                        # IPv6 回环
    "https://[fe80::1]/hook",                         # IPv6 链路本地
    "https://example.invalid:0/hook",                 # 坏端口
    "https://example.invalid:99999/hook",             # 坏端口
    "https:///hook",                                  # 无 host
])
def test_unsafe_urls_rejected(url) -> None:
    assert _url_probe(url) is not None, url


def test_loopback_http_only_with_explicit_flag() -> None:
    """http 仅经显式 --allow-loopback-http 且仅限字面回环 IP（test-only）。"""
    assert _url_probe("http://127.0.0.1:9000/hook", allow=True) is None
    assert _url_probe("http://localhost:9000/hook", allow=True) is not None
    assert _url_probe("http://10.0.0.5:9000/hook", allow=True) is not None
    assert _url_probe("http://192.168.1.5:9000/hook", allow=True) is not None
    assert _url_probe("http://example.invalid/hook", allow=True) is not None


def test_public_https_urls_accepted() -> None:
    assert _url_probe("https://hooks.example.invalid/T000/B000/xyz") is None
    assert _url_probe("https://hooks.example.invalid:8443/hook") is None
    assert _url_probe("https://93.184.216.34/hook") is None
    assert _url_probe("https://hooks.example.invalid") is None  # 空 path 视作 /


def test_execute_rejects_unsafe_secret_url(tmp_path, monkeypatch) -> None:
    """secret url 非法 → 拒绝且零 Transport 构造（fail-closed）。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": "http://169.254.169.254/latest/meta-data"})
    counter = {"n": 0}

    def factory(scheme):
        counter["n"] += 1
        raise AssertionError("unsafe url must fail before transport")

    monkeypatch.setattr(mad, "RealTransport", factory)
    rc = mad.main(["--report", str(report), "--secret-file", str(secret),
                   "--execute", "--confirm", mad.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path / "out")])
    assert rc == 2
    assert counter["n"] == 0


# ---------------------------------------------------------------- secret 文件


def _execute(tmp_path, monkeypatch, report_path, secret_path, transport,
             *extra: str, allow_loopback: bool = False) -> int:
    _patch(monkeypatch, transport=transport, clock=FakeClock())
    argv = ["--report", str(report_path), "--secret-file", str(secret_path),
            "--execute", "--confirm", mad.CONFIRM_PHRASE,
            "--timeout-seconds", "5",
            "--artifact-dir", str(tmp_path / "out")]
    if allow_loopback:
        argv.append("--allow-loopback-http")
    argv.extend(extra)
    return mad.main(argv)


def test_secret_file_missing_refused(tmp_path, monkeypatch) -> None:
    report = write_report(tmp_path, make_report())
    counter = {"n": 0}

    def factory(scheme):
        counter["n"] += 1
        raise AssertionError("missing secret must fail before transport")

    monkeypatch.setattr(mad, "RealTransport", factory)
    rc = mad.main(["--report", str(report), "--secret-file",
                   str(tmp_path / "absent.json"),
                   "--execute", "--confirm", mad.CONFIRM_PHRASE,
                   "--artifact-dir", str(tmp_path / "out")])
    assert rc == 2
    assert counter["n"] == 0


def test_secret_file_invalid_refused(tmp_path, monkeypatch) -> None:
    report = write_report(tmp_path, make_report())
    bad_payloads = [
        "not json",
        json.dumps(["not", "object"]),
        json.dumps({"token": "x"}),                       # 缺 url
        json.dumps({"url": "https://ok.example/hook", "extra": 1}),  # 未知键
        json.dumps({"url": ""}),                          # 空 url
        json.dumps({"url": "https://ok.example/hook", "token": ""}),  # 空 token
        json.dumps({"url": "https://ok.example/hook", "token": "x" * 5000}),
        "x" * 100000,
    ]
    counter = {"n": 0}

    def factory(scheme):
        counter["n"] += 1
        raise AssertionError("bad secret must fail before transport")

    monkeypatch.setattr(mad, "RealTransport", factory)
    for payload in bad_payloads:
        secret = tmp_path / "sec.json"
        secret.write_text(payload, encoding="utf-8")
        rc = mad.main(["--report", str(report), "--secret-file", str(secret),
                       "--execute", "--confirm", mad.CONFIRM_PHRASE,
                       "--artifact-dir", str(tmp_path / "out")])
        assert rc == 2, payload[:40]
    assert counter["n"] == 0


# ---------------------------------------------------------------- 成功路径


def test_execute_dispatch_success(tmp_path, monkeypatch) -> None:
    report = write_report(tmp_path, make_report())  # 1 warn alert
    secret = write_secret(tmp_path, {"url": SECRET_URL, "token": SECRET_TOKEN})
    transport = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, report, secret, transport)
    assert rc == 0

    # 恰一次 POST；Authorization 携带 secret token（只在请求头，绝无输出面）
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["host"] == "hooks.example.invalid"
    assert call["port"] == 443
    assert call["path"] == "/T000/B000/xyz"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["headers"]["Authorization"] == f"Bearer {SECRET_TOKEN}"
    assert call["timeout"] == 5.0

    # payload：版本化 + 固定词汇 + 有界
    payload = json.loads(call["body"].decode("utf-8"))
    assert payload["schema_version"] == 1
    assert payload["event"] == "monitor-alert"
    assert payload["report"]["stem"] == STEM
    assert payload["report"]["overall_status"] == "warn"
    assert payload["report"]["partial"] is False
    assert payload["report"]["counts"] == {"ok": 30, "warn": 1, "critical": 0}
    assert payload["report"]["alert_codes"] == ["container-restarts:api:warn"]
    assert len(call["body"]) <= mad.MAX_PAYLOAD_BYTES

    # ledger 恰一行 sent 记录
    rows = _ledger_rows(tmp_path / "out")
    assert len(rows) == 1
    assert rows[0]["dispatch_status"] == "sent"
    assert rows[0]["report_sha256"] == rows[0]["report_sha256"]
    assert rows[0]["sink"] == "generic-https-webhook"
    assert rows[0]["http_status"] == 200

    # dispatch 报告 sent + 不含 URL/token
    reports = _dispatch_reports(tmp_path / "out")
    assert len(reports) == 1
    record = _read_json(reports[0])
    assert record["dispatch_status"] == "sent"
    assert SECRET_URL not in reports[0].read_text(encoding="utf-8")
    assert SECRET_TOKEN not in reports[0].read_text(encoding="utf-8")


def test_execute_dispatch_incomplete_report(tmp_path, monkeypatch) -> None:
    """incomplete 报告（采集器失败）同样触发分发。"""
    data = make_report(overall="incomplete", partial=True,
                       alerts=[make_alert(severity="critical",
                                          detail="category=command-timeout")])
    report = write_report(tmp_path, data)
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(status=201)
    rc = _execute(tmp_path, monkeypatch, report, secret, transport)
    assert rc == 0
    payload = json.loads(transport.calls[0]["body"].decode("utf-8"))
    assert payload["report"]["overall_status"] == "incomplete"
    assert payload["report"]["partial"] is True


def test_no_alert_ok_report_skips_dispatch(tmp_path, monkeypatch) -> None:
    """ok 且零告警 → 不分发、零 Transport 调用、exit 0。"""
    data = make_report(overall="ok", alerts=[], counts={"ok": 34, "warn": 0,
                                                        "critical": 0})
    report = write_report(tmp_path, data)
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, report, secret, transport)
    assert rc == 0
    assert transport.calls == []
    assert _ledger_rows(tmp_path / "out") == []
    reports = _dispatch_reports(tmp_path / "out")
    assert len(reports) == 1
    assert _read_json(reports[0])["dispatch_status"] == "skipped-no-alerts"


def test_alert_codes_bounded_and_truncation_counted(tmp_path, monkeypatch) -> None:
    """告警超 payload 界 → 截断到固定界且截断计数显式（绝不静默丢弃语义）。"""
    alerts = [make_alert(subject=f"svc{i}") for i in range(mad.MAX_ALERT_CODES + 5)]
    data = make_report(alerts=alerts)
    report = write_report(tmp_path, data)
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, report, secret, transport)
    assert rc == 0
    payload = json.loads(transport.calls[0]["body"].decode("utf-8"))
    assert len(payload["report"]["alert_codes"]) == mad.MAX_ALERT_CODES
    assert payload["report"]["alert_codes_truncated_count"] == 5


def test_loopback_http_test_flag_end_to_end(tmp_path, monkeypatch) -> None:
    """显式 --allow-loopback-http + 字面回环 IP → http 端到端放行（test-only）。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": "http://127.0.0.1:9099/hook"})
    transport = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, report, secret, transport,
                  allow_loopback=True)
    assert rc == 0
    assert transport.calls[0]["port"] == 9099


# ---------------------------------------------------------------- HTTP 失败


@pytest.mark.parametrize("status", [500, 503, 404, 302, 301])
def test_non_2xx_dispatch_failed(tmp_path, monkeypatch, status) -> None:
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(status=status)
    rc = _execute(tmp_path, monkeypatch, report, secret, transport)
    assert rc == 2
    assert len(transport.calls) == 1
    assert _ledger_rows(tmp_path / "out") == []  # 失败绝不入 ledger
    reports = _dispatch_reports(tmp_path / "out")
    assert len(reports) == 1
    record = _read_json(reports[0])
    assert record["dispatch_status"] == "failed"
    assert record["http_status"] == status


@pytest.mark.parametrize("error", [
    TimeoutError("timed out"),
    ConnectionRefusedError("refused"),
    OSError("boom"),
])
def test_transport_exception_dispatch_failed(tmp_path, monkeypatch, error) -> None:
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(error=error)
    rc = _execute(tmp_path, monkeypatch, report, secret, transport)
    assert rc == 2
    assert _ledger_rows(tmp_path / "out") == []
    record = _read_json(_dispatch_reports(tmp_path / "out")[0])
    assert record["dispatch_status"] == "failed"
    assert record["http_status"] is None
    assert record["transport_error"]["category"]


# ---------------------------------------------------------------- 幂等 / ledger


def test_duplicate_dispatch_refused(tmp_path, monkeypatch) -> None:
    """同 report sha256 已 sent → duplicate 拒绝且零 Transport 调用。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    first = FakeTransport(status=200)
    assert _execute(tmp_path, monkeypatch, report, secret, first) == 0
    assert len(first.calls) == 1

    second = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, report, secret, second)
    assert rc == 2
    assert second.calls == []
    assert len(_ledger_rows(tmp_path / "out")) == 1  # ledger 未追加


def test_ledger_malformed_line_refused(tmp_path, monkeypatch) -> None:
    """ledger 行非法 → fail-closed 拒绝（绝不猜测/静默跳过）。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(status=200)
    assert _execute(tmp_path, monkeypatch, report, secret, transport) == 0

    ledger = tmp_path / "out" / "dispatch-ledger.jsonl"
    text = ledger.read_text(encoding="utf-8")
    ledger.write_text(text + "not json\n", encoding="utf-8")

    again = FakeTransport(status=200)
    other = write_report(tmp_path, make_report(alerts=[make_alert(subject="web")]),
                         name="monitor-20260923-131502.json")
    rc = _execute(tmp_path, monkeypatch, other, secret, again)
    assert rc == 2
    assert again.calls == []


def test_ledger_bad_field_refused(tmp_path, monkeypatch) -> None:
    """ledger 行字段非法（sha256 形态/dispatch_status 词汇）→ 拒绝。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(status=200)
    assert _execute(tmp_path, monkeypatch, report, secret, transport) == 0
    ledger = tmp_path / "out" / "dispatch-ledger.jsonl"
    rows = _ledger_rows(tmp_path / "out")
    rows[0]["report_sha256"] = "zz-not-hex"
    ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                      encoding="utf-8")
    other = write_report(tmp_path, make_report(alerts=[make_alert(subject="web")]),
                         name="monitor-20260923-131502.json")
    again = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, other, secret, again)
    assert rc == 2
    assert again.calls == []


def test_second_distinct_report_dispatches(tmp_path, monkeypatch) -> None:
    """不同报告（不同 sha256）在既有 ledger 上正常追加。"""
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    first = write_report(tmp_path, make_report())
    assert _execute(tmp_path, monkeypatch, first, secret,
                    FakeTransport(status=200)) == 0
    second = write_report(tmp_path, make_report(alerts=[make_alert(subject="web")]),
                          name="monitor-20260923-131502.json")
    assert _execute(tmp_path, monkeypatch, second, secret,
                    FakeTransport(status=200)) == 0
    rows = _ledger_rows(tmp_path / "out")
    assert len(rows) == 2
    assert rows[0]["report_sha256"] != rows[1]["report_sha256"]


# ---------------------------------------------------------------- 输出路径 / 原子写


def test_dispatch_report_never_overwrites_existing(tmp_path, monkeypatch) -> None:
    """同名输出工件已存在 → fail-closed 换名，绝不覆盖既有证据。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(status=200)
    assert _execute(tmp_path, monkeypatch, report, secret, transport) == 0
    first_reports = _dispatch_reports(tmp_path / "out")
    existing = first_reports[0]
    marker = json.dumps({"sentinel": True})
    existing.write_text(marker, encoding="utf-8")

    other = write_report(tmp_path, make_report(alerts=[make_alert(subject="web")]),
                         name="monitor-20260923-131502.json")
    # 同一 FakeClock stamp：强制与首轮同名碰撞
    rc = _execute(tmp_path, monkeypatch, other, secret, FakeTransport(status=200))
    assert rc == 0
    assert existing.read_text(encoding="utf-8") == marker  # 既有文件逐字节未动
    assert len(_dispatch_reports(tmp_path / "out")) == 2


def test_output_symlink_refused(tmp_path, monkeypatch) -> None:
    """输出目录为 symlink → 拒绝（零写入）。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    real_out = tmp_path / "real-out"
    real_out.mkdir()
    link = tmp_path / "linked-out"
    try:
        os.symlink(real_out, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")
    transport = FakeTransport(status=200)
    monkeypatch.setattr(mad, "RealTransport", lambda scheme: transport)
    monkeypatch.setattr(mad, "RealClock", lambda: FakeClock())
    rc = mad.main(["--report", str(report), "--secret-file", str(secret),
                   "--execute", "--confirm", mad.CONFIRM_PHRASE,
                   "--artifact-dir", str(link)])
    assert rc == 2
    assert transport.calls == []
    assert list(real_out.iterdir()) == []


def test_report_file_symlink_refused_real_fs(tmp_path) -> None:
    target = write_report(tmp_path, make_report())
    link = tmp_path / f"{STEM}.json"
    target.rename(tmp_path / "real.json")
    try:
        os.symlink(tmp_path / "real.json", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")
    assert mad.main(["--report", str(link),
                     "--artifact-dir", str(tmp_path / "out")]) == 2


# ---------------------------------------------------------------- 脱敏 / 输出卫生


def test_redaction_marker_never_leaks(tmp_path, monkeypatch, capsys) -> None:
    """报告投毒 detail 携带 secret 形态 marker → 绝不进入 payload/ledger/
    dispatch 报告/stdout。"""
    data = make_report(alerts=[make_alert(detail=f"token={MARK_TOKEN} boom")])
    report = write_report(tmp_path, data)
    secret = write_secret(tmp_path, {"url": SECRET_URL, "token": SECRET_TOKEN})
    transport = FakeTransport(status=200)
    rc = _execute(tmp_path, monkeypatch, report, secret, transport)
    assert rc == 0
    capsys.readouterr()

    payload_text = transport.calls[0]["body"].decode("utf-8")
    assert MARK_TOKEN not in payload_text
    assert "detail" not in payload_text  # payload 无 detail 字段（固定词汇）

    out_dir = tmp_path / "out"
    for path in out_dir.iterdir():
        text = path.read_text(encoding="utf-8")
        assert MARK_TOKEN not in text, path.name
        assert SECRET_TOKEN not in text, path.name
        assert SECRET_URL not in text, path.name

    # ledger 同样干净
    for row in _ledger_rows(out_dir):
        assert MARK_TOKEN not in json.dumps(row)


def test_secret_values_never_echoed_on_stdout(tmp_path, monkeypatch, capsys) -> None:
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL, "token": SECRET_TOKEN})
    transport = FakeTransport(status=200)
    _execute(tmp_path, monkeypatch, report, secret, transport)
    out = capsys.readouterr().out
    assert SECRET_URL not in out
    assert SECRET_TOKEN not in out


def test_payload_fixed_vocabulary(tmp_path, monkeypatch) -> None:
    """payload 键集精确（无任何自由文本字段）。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(status=200)
    assert _execute(tmp_path, monkeypatch, report, secret, transport) == 0
    payload = json.loads(transport.calls[0]["body"].decode("utf-8"))
    assert set(payload) == {"schema_version", "tool", "milestone", "event",
                            "dispatched_at", "report"}
    assert set(payload["report"]) == {
        "stem", "sha256", "collected_at", "overall_status", "partial",
        "counts", "alert_codes", "alert_codes_truncated_count",
    }
    # 报告身份 hash 是 64 位 hex
    assert len(payload["report"]["sha256"]) == 64
    int(payload["report"]["sha256"], 16)


def test_ledger_row_fixed_vocabulary(tmp_path, monkeypatch) -> None:
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    transport = FakeTransport(status=200)
    assert _execute(tmp_path, monkeypatch, report, secret, transport) == 0
    row = _ledger_rows(tmp_path / "out")[0]
    assert set(row) == {
        "schema_version", "tool", "milestone", "dispatch_id", "dispatched_at",
        "report_stem", "report_sha256", "overall_status", "counts",
        "alert_codes_count", "dispatch_status", "sink", "http_status",
        "payload_sha256", "payload_bytes",
    }
    assert row["dispatch_status"] == "sent"


def test_plan_writes_plan_report_and_no_ledger(tmp_path, monkeypatch, capsys) -> None:
    """plan 报告落盘（planned 状态）且 ledger 零写入。"""
    report = write_report(tmp_path, make_report())
    secret = write_secret(tmp_path, {"url": SECRET_URL})
    _patch(monkeypatch, clock=FakeClock())
    rc = mad.main(["--report", str(report), "--secret-file", str(secret),
                   "--artifact-dir", str(tmp_path / "out")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "plan" in out.lower()
    out_dir = tmp_path / "out"
    plans = sorted(out_dir.glob("plan-*.json"))
    assert len(plans) == 1
    record = _read_json(plans[0])
    assert record["mode"] == "plan"
    assert record["dispatch_status"] == "planned"
    assert not (out_dir / "dispatch-ledger.jsonl").exists()
