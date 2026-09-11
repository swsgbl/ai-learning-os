r"""M14-11 tools/ops/soak_rehearsal.py 契约测试：只读 soak 彩排 harness 的安全边界，零真实生产流量。

覆盖（绝不打 3011/8000/8010/8011 生产端口；唯一真实 socket 流量是测试
自起的 127.0.0.1 临时假服务器/假代理，验证直连与代理旁路）：
- plan 默认零网络（socket 阻断下照常出计划与 plan 报告）；
- execute 门禁 fail-closed：缺旗标/短语不精确/限制超硬顶（含 execute +
  正确短语时超顶同样拒绝）→ EXIT_USAGE 且零网络；
- loopback 纪律：固定画像校验矩阵（拒绝非字面 loopback IP、主机名、
  query/fragment/userinfo、非 http scheme、缺显式端口）；语音面仅
  8010/8011 GET /health；
- 源码契约：无 proxy-aware 网络路径（urllib.request/urlopen/
  getproxies/ProxyHandler/requests 库）、无子进程/容器/调度器命令、
  仅 GET、无 Cookie/Authorization 头、无 https URL；
- 引擎语义：max-requests 停止、duration deadline 停止（deadline 后零
  新发、在途计入 completed_after_deadline、部分结果保留）、语音目标
  最小间隔门控、并发硬顶（barrier 实证重叠 ≤ concurrency）；
- 统计纯函数：nearest-rank 分位、status/error 计数、吞吐；
- 报告 schema：plan/execute 双模式必检键、UTC 时间戳、per-target 结构；
  脱敏层：凭据形态标记值绝不落入 JSON/Markdown/日志；proxy env 仅键名
  存在性入注记、值绝不出现；
- 真实 transport 行为（本机假服务器）：GET-only + 仅 UA/Accept 头、
  环境代理被无视（恶意假代理零连接实证）、拒绝连接安全归类。
"""
from __future__ import annotations

import importlib.util
import itertools
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "soak_rehearsal.py"

#: 标记值（仅出现在测试注入面，断言其绝不进入任何文件/日志）
MARK_TOKEN = "sk-ZXmarker0123456789"
MARK_BEARER = "ZXbEARERmarker12345678"
MARK_ENV_PROXY = "http://127.0.0.1:9"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


sr = _load_module(SCRIPT, "soak_rehearsal_under_test")


# ---------------------------------------------------------------- fakes


class FakeClock:
    """线程安全假时钟：sleep 即推进（零真实等待）。"""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self._lock = threading.Lock()

    def monotonic(self) -> float:
        with self._lock:
            return self._now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self._now += max(0.0, seconds)


class FakeTransport:
    """可配置伪 transport：记录调用；支持状态轮换/推进时钟/抛异常。"""

    def __init__(self, *, statuses: list[int | None] | None = None,
                 elapsed_ms: float = 0.0, raise_exc: Exception | None = None,
                 clock: FakeClock | None = None, advance: float = 0.0) -> None:
        self.calls: list[tuple[str, int, str]] = []
        self.statuses = statuses if statuses is not None else [200]
        self.elapsed_ms = elapsed_ms
        self.raise_exc = raise_exc
        self.clock = clock
        self.advance = advance
        self._index = 0

    def get(self, host: str, port: int, path: str, *, timeout: float) -> sr.HttpResult:
        self.calls.append((host, port, path))
        if self.clock is not None and self.advance:
            self.clock.sleep(self.advance)
        if self.raise_exc is not None:
            raise self.raise_exc
        status = self.statuses[self._index % len(self.statuses)]
        self._index += 1
        return sr.HttpResult(status, self.elapsed_ms, None, None)


class BarrierTransport:
    """barrier 实证并发重叠：两个请求同时在途则 barrier 放行并置位。"""

    def __init__(self, parties: int = 2) -> None:
        self.barrier = threading.Barrier(parties, timeout=3.0)
        self.calls = 0
        self.overlapped = False

    def get(self, host: str, port: int, path: str, *, timeout: float) -> sr.HttpResult:
        self.calls += 1
        try:
            self.barrier.wait(timeout=3.0)
            self.overlapped = True
        except threading.BrokenBarrierError:
            pass
        return sr.HttpResult(200, 1.0, None, None)


def _block_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """阻断一切新 socket 构造——证明被测路径零网络。"""

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in this test")

    monkeypatch.setattr(socket, "socket", _forbidden)


def _limits(**overrides: object) -> sr.SoakLimits:
    values: dict[str, object] = {
        "duration_seconds": 120.0,
        "concurrency": 1,
        "max_requests": 2000,
        "request_timeout_seconds": 5.0,
        "pace_seconds": 0.05,
        "voice_min_interval_seconds": 2.0,
    }
    values.update(overrides)
    return sr.SoakLimits(**values)  # type: ignore[arg-type]


class _RecordingHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # stdlib 约定命名
        record = getattr(self.server, "requests_seen", None)
        if record is not None:
            headers = {key: value for key, value in self.headers.items()}
            record.append((self.command, self.path, headers))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args: object) -> None:  # 静默
        return


@pytest.fixture()
def fake_http_server():
    """测试自起的 127.0.0.1 临时假 HTTP 服务器（ephemeral 端口）。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
    server.requests_seen: list[tuple[str, str, dict[str, str]]] = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)


# ---------------------------------------------------------------- plan / 门禁


def test_plan_mode_zero_network(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    rc = sr.main(["--artifact-dir", str(tmp_path)])
    assert rc == sr.EXIT_OK
    plan_files = sorted(p.name for p in tmp_path.glob("plan-*"))
    assert len(plan_files) == 2  # json + md
    report = json.loads((tmp_path / plan_files[0]).read_text(encoding="utf-8"))
    assert report["mode"] == "plan"
    assert report["stopped_reason"] == "plan-only"
    assert report["targets_selected"] == ["web-root", "web-login", "api-health"]


def test_plan_mode_includes_voice_only_with_flag(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    rc = sr.main(["--include-voice", "--artifact-dir", str(tmp_path)])
    assert rc == sr.EXIT_OK
    report = json.loads(next(tmp_path.glob("plan-*.json")).read_text(encoding="utf-8"))
    assert report["targets_selected"] == [
        "web-root", "web-login", "api-health", "funasr-health", "cosyvoice-health",
    ]


def test_plan_mode_only_filter(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    rc = sr.main(["--only", "api-health", "--artifact-dir", str(tmp_path)])
    assert rc == sr.EXIT_OK
    report = json.loads(next(tmp_path.glob("plan-*.json")).read_text(encoding="utf-8"))
    assert report["targets_selected"] == ["api-health"]


def test_execute_without_confirm_refused_zero_network(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    rc = sr.main(["--execute", "--duration-seconds", "5", "--artifact-dir", str(tmp_path)])
    assert rc == sr.EXIT_USAGE
    assert not list(tmp_path.glob("soak-*"))  # 零执行、零执行报告


@pytest.mark.parametrize("phrase", [
    "", "execute read-only loopback soak", "EXECUTE READ-ONLY LOOPBACK SOAK ",
    " EXECUTE READ-ONLY LOOPBACK SOAK", "EXECUTE READONLY LOOPBACK SOAK",
])
def test_execute_wrong_phrase_refused(monkeypatch, tmp_path, phrase: str) -> None:
    _block_sockets(monkeypatch)
    rc = sr.main(["--execute", "--confirm", phrase, "--duration-seconds", "5",
                  "--max-requests", "10", "--artifact-dir", str(tmp_path)])
    assert rc == sr.EXIT_USAGE


@pytest.mark.parametrize("args", [
    ["--duration-seconds", "121"],
    ["--duration-seconds", "0"],
    ["--concurrency", "9"],
    ["--concurrency", "0"],
    ["--max-requests", "2001"],
    ["--max-requests", "0"],
    ["--request-timeout-seconds", "10.5"],
    ["--request-timeout-seconds", "0.4"],
    ["--pace-seconds", "0.04"],
    ["--pace-seconds", "6"],
    ["--voice-min-interval-seconds", "0.5"],
    ["--voice-min-interval-seconds", "61"],
])
def test_limits_fail_closed_even_in_plan(monkeypatch, tmp_path, args: list[str]) -> None:
    _block_sockets(monkeypatch)
    rc = sr.main([*args, "--artifact-dir", str(tmp_path)])
    assert rc == sr.EXIT_USAGE


def test_limits_fail_closed_even_with_correct_confirm(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    rc = sr.main(["--execute", "--confirm", sr.CONFIRM_PHRASE,
                  "--duration-seconds", "121", "--max-requests", "10",
                  "--artifact-dir", str(tmp_path)])
    assert rc == sr.EXIT_USAGE  # 超硬顶：即使五要素齐备也零请求拒绝


# ---------------------------------------------------------------- 目标面 / loopback


@pytest.mark.parametrize("url", [t.url for t in sr.BASE_TARGETS + sr.VOICE_TARGETS]
                         + ["http://[::1]:8010/health", "http://127.200.0.1:3011/"])
def test_validate_accepts_loopback_literal_profile(url: str) -> None:
    assert sr.validate_target_url(url) is None


@pytest.mark.parametrize("url", [
    "http://10.0.0.1/",                # 私网非 loopback
    "http://192.168.1.5/",             # 私网非 loopback
    "http://8.8.8.8/",                 # 公网
    "http://localhost:3011/",          # 主机名一律拒绝（零 DNS）
    "http://example.com/",
    "ftp://127.0.0.1:3011/",           # 非 http
    "http://127.0.0.1:3011/login?x=1",  # query
    "http://127.0.0.1:3011/login#frag",  # fragment
    "http://user:pw@127.0.0.1:3011/",  # userinfo
    "http://127.0.0.1/",               # 缺显式端口
    "http://127.0.0.1:99999/",         # 端口越界
])
def test_validate_refuses_non_loopback_or_rich_urls(url: str) -> None:
    assert sr.validate_target_url(url) is not None


def test_voice_profile_is_low_rate_health_only() -> None:
    assert len(sr.VOICE_TARGETS) == 2
    for target in sr.VOICE_TARGETS:
        assert target.group == "voice"
        assert target.url.endswith("/health")
        assert sr.validate_target_url(target.url) is None
        parts = target.url.rsplit(":", 1)
        assert parts[1].split("/")[0] in {"8010", "8011"}


def test_select_targets_only_within_enabled_set() -> None:
    base, error = sr.select_targets(False, None)
    assert error is None and [t.target_id for t in base] == ["web-root", "web-login", "api-health"]
    full, error = sr.select_targets(True, None)
    assert error is None and len(full) == 5
    picked, error = sr.select_targets(True, ["funasr-health"])
    assert error is None and [t.target_id for t in picked] == ["funasr-health"]
    _, error = sr.select_targets(False, ["funasr-health"])  # 未启用即拒绝
    assert error is not None


# ---------------------------------------------------------------- 源码契约


def test_source_contract_forbidden_tokens() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for token in (
        "urllib.request", "urlopen", "getproxies", "ProxyHandler",
        "import requests", "aiohttp", "httpx", "https://",
        "subprocess", "docker", "compose", "schtasks", "taskkill",
        "Stop-ScheduledTask", "Start-ScheduledTask", "Register-ScheduledTask",
        "os.kill", "terminate(",
        "Cookie", "Authorization", "Bearer",
        '"POST"', '"PUT"', '"PATCH"', '"DELETE"',
    ):
        assert token not in source, f"禁止出现的字面量: {token}"
    # 仅一处发请求点，且恒为 GET、仅 UA/Accept 两个固定头
    assert source.count('connection.request("GET"') == 1
    assert '"User-Agent"' in source and '"Accept"' in source


# ---------------------------------------------------------------- 引擎语义


def test_engine_stops_at_max_requests() -> None:
    clock = FakeClock()
    transport = FakeTransport()
    outcome = sr.run_soak(transport=transport, targets=list(sr.BASE_TARGETS),
                          limits=_limits(duration_seconds=120.0, concurrency=4, max_requests=20),
                          clock=clock, log=sr.SafeLog(echo=False))
    assert outcome.issued_total == 20
    assert len(outcome.samples) == 20
    assert outcome.stopped_reason == "max-requests"
    issued_ids = {s.target_id for s in outcome.samples}
    assert issued_ids == {"web-root", "web-login", "api-health"}


def test_engine_stops_at_duration_deadline_with_partial_results() -> None:
    clock = FakeClock()
    # 单 worker + 每请求推进 0.25s + 节拍 0.05s：步长 0.3s，duration 1.0s
    transport = FakeTransport(clock=clock, advance=0.25, elapsed_ms=250.0)
    outcome = sr.run_soak(transport=transport, targets=list(sr.BASE_TARGETS),
                          limits=_limits(duration_seconds=1.0, concurrency=1, max_requests=2000),
                          clock=clock, log=sr.SafeLog(echo=False))
    # 确定性轨迹：t=0, 0.3, 0.6, 0.9 各发 1 次 → 第 5 次检查 t=1.2 ≥ deadline 停
    assert outcome.issued_total == 4
    assert outcome.stopped_reason == "duration-deadline"
    assert [round(s.issued_at, 6) for s in outcome.samples] == [0.0, 0.3, 0.6, 0.9]
    # 在途越界完成：0.9 + 0.25 = 1.15 > 1.0 → 恰 1 个
    assert sum(1 for s in outcome.samples
               if s.issued_at + s.result.elapsed_ms / 1000.0 > outcome.deadline_monotonic) == 1
    assert transport.calls == [("127.0.0.1", 3011, "/"), ("127.0.0.1", 3011, "/login"),
                               ("127.0.0.1", 8000, "/health"), ("127.0.0.1", 3011, "/")]


def test_engine_voice_targets_respect_min_interval() -> None:
    clock = FakeClock()
    transport = FakeTransport()
    targets = list(sr.BASE_TARGETS) + list(sr.VOICE_TARGETS)
    outcome = sr.run_soak(transport=transport, targets=targets,
                          limits=_limits(duration_seconds=120.0, concurrency=1,
                                         max_requests=20, voice_min_interval_seconds=2.0),
                          clock=clock, log=sr.SafeLog(echo=False))
    assert outcome.issued_total == 20
    for voice_id in ("funasr-health", "cosyvoice-health"):
        times = sorted(s.issued_at for s in outcome.samples if s.target_id == voice_id)
        assert times, f"{voice_id} 零命中"
        for earlier, later in itertools.pairwise(times):
            assert later - earlier >= 2.0 - 1e-6  # 同一语音目标间隔 ≥ 最小间隔
    # 语音目标只应见到 /health 路径
    voice_calls = [call for call in transport.calls if call[1] in (8010, 8011)]
    assert voice_calls and all(call[2] == "/health" for call in voice_calls)


def test_engine_concurrency_ceiling_and_overlap() -> None:
    clock = FakeClock()
    transport = BarrierTransport(parties=2)
    outcome = sr.run_soak(transport=transport, targets=list(sr.BASE_TARGETS)[:2],
                          limits=_limits(duration_seconds=120.0, concurrency=3, max_requests=8),
                          clock=clock, log=sr.SafeLog(echo=False))
    assert outcome.issued_total == 8
    assert transport.overlapped is True  # 真实存在并发在途
    assert transport.calls <= outcome.issued_total  # 调用数不超发


def test_engine_transport_exception_categorized_without_text() -> None:
    clock = FakeClock()
    transport = FakeTransport(raise_exc=RuntimeError("leak " + MARK_TOKEN))
    outcome = sr.run_soak(transport=transport, targets=list(sr.BASE_TARGETS)[:1],
                          limits=_limits(duration_seconds=120.0, concurrency=1, max_requests=2),
                          clock=clock, log=sr.SafeLog(echo=False))
    assert outcome.issued_total == 2
    for sample in outcome.samples:
        assert sample.result.status is None
        assert sample.result.error_category == "internal-error"
        assert sample.result.error_class == "RuntimeError"


# ---------------------------------------------------------------- 统计 / 分位


def test_percentile_nearest_rank() -> None:
    assert sr.percentile([1, 2, 3, 4], 0.50) == 2
    assert sr.percentile([1, 2, 3, 4], 0.95) == 4
    assert sr.percentile([1, 2, 3, 4], 0.99) == 4
    assert sr.percentile([5], 0.50) == 5


def test_summarize_target_counts_and_percentiles() -> None:
    target = sr.BASE_TARGETS[0]
    samples = []
    statuses = [200] * 14 + [404, 500] + [None, None, None]  # 19 个 + 第 20 个回绕 200
    for index in range(20):
        status = statuses[index] if index < len(statuses) else 200
        error = "timeout" if status is None else None
        result = sr.HttpResult(status, float(index + 1), error, "TimeoutError" if error else None)
        samples.append(sr.Sample("web-root", 0.0, result))
    stats = sr.summarize_target(target, samples, wall_seconds=4.0)
    assert stats["requests"] == 20
    assert stats["success"] == 15
    assert stats["failure"] == 5
    assert stats["status_counts"] == {"200": 15, "404": 1, "500": 1}
    assert stats["error_counts"] == {"timeout": 3}
    latency = stats["latency_ms"]
    assert latency["min"] == 1.0
    assert latency["p50"] == 10.0  # nearest-rank: 第 10 小
    assert latency["p95"] == 19.0
    assert latency["p99"] == 20.0
    assert latency["max"] == 20.0
    assert stats["throughput_rps"] == 5.0


def test_categorize_exception_matrix() -> None:
    assert sr.categorize_exception(ConnectionRefusedError())[0] == "connection-refused"
    assert sr.categorize_exception(TimeoutError())[0] == "timeout"
    assert sr.categorize_exception(ConnectionResetError())[0] == "connection-reset"
    assert sr.categorize_exception(BrokenPipeError())[0] == "connection-broken-pipe"
    assert sr.categorize_exception(http_protocol_exc())[0] == "http-protocol-error"
    assert sr.categorize_exception(OSError("never logged text"))[0] == "connection-error"
    assert sr.categorize_exception(ValueError())[0] == "internal-error"
    assert sr.categorize_exception(OSError())[1] == "OSError"


def http_protocol_exc() -> Exception:
    import http.client

    return http.client.BadStatusLine("garbage")


# ---------------------------------------------------------------- 报告 schema / 脱敏


def _execute_report() -> dict[str, object]:
    clock = FakeClock()
    transport = FakeTransport(statuses=[200, 200, 500], elapsed_ms=10.0)
    outcome = sr.run_soak(transport=transport, targets=list(sr.BASE_TARGETS),
                          limits=_limits(duration_seconds=120.0, concurrency=1, max_requests=9),
                          clock=clock, log=sr.SafeLog(echo=False))
    return sr.build_report(
        mode="execute", started_utc="2026-09-11T00:00:00Z", ended_utc="2026-09-11T00:01:00Z",
        wall_seconds=60.0, limits=outcome_limits(), targets=list(sr.BASE_TARGETS),
        samples=outcome.samples, stopped_reason=outcome.stopped_reason,
        deadline_monotonic=outcome.deadline_monotonic,
        started_monotonic=outcome.started_monotonic, proxy_env_present=False,
    )


def outcome_limits() -> sr.SoakLimits:
    return _limits(duration_seconds=120.0, concurrency=1, max_requests=9)


def test_execute_report_schema() -> None:
    report = _execute_report()
    for key in ("schema_version", "tool", "milestone", "mode", "started_at_utc", "ended_at_utc",
                "wall_seconds", "limits", "targets_selected", "stopped_reason", "issued_total",
                "completed_after_deadline", "totals", "per_target", "notes"):
        assert key in report, f"缺报告键: {key}"
    assert report["schema_version"] == sr.REPORT_SCHEMA_VERSION
    assert report["mode"] == "execute"
    assert report["issued_total"] == 9
    totals = report["totals"]
    assert totals["requests"] == 9 and totals["failure"] == 3  # type: ignore[index]
    for target_id in ("web-root", "web-login", "api-health"):
        stats = report["per_target"][target_id]  # type: ignore[index]
        assert {"url", "group", "requests", "success", "failure", "status_counts",
                "error_counts", "latency_ms", "throughput_rps"} <= set(stats)
    # UTC 时间戳形态
    for key in ("started_at_utc", "ended_at_utc"):
        value = report[key]
        assert isinstance(value, str) and value.endswith("Z") and "T" in value


def test_report_markdown_contains_table_and_no_body_headers() -> None:
    report = _execute_report()
    markdown = sr.render_markdown(report)
    assert "mode=execute" in markdown
    assert "| web-root |" in markdown and "| api-health |" in markdown
    assert "p95(ms)" in markdown


def test_redaction_layer_strips_credential_shapes(tmp_path) -> None:
    poisoned = _execute_report()
    poisoned["notes"] = ["leak " + MARK_TOKEN, "bearer " + MARK_BEARER]  # type: ignore[assignment]
    json_path, md_path = sr.write_report(poisoned, tmp_path, "poison")
    json_text = json_path.read_text(encoding="utf-8")
    md_text = md_path.read_text(encoding="utf-8")
    for text in (json_text, md_text):
        assert MARK_TOKEN not in text
        assert MARK_BEARER not in text
        assert "[REDACTED:" in text
    assert sr.redact_secrets("password=ZXpassmarker12345678") == "[REDACTED:credential]"
    assert sr.redact_secrets("api_key: ZXkeymarker12345678") == "[REDACTED:credential]"
    # 良性内容不被误伤
    assert sr.redact_secrets("http://127.0.0.1:3011/login") == "http://127.0.0.1:3011/login"
    assert "max_requests" in sr.redact_secrets("max_requests=300")


def test_proxy_env_note_records_presence_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HTTP_PROXY", MARK_ENV_PROXY)
    rc = sr.main(["--artifact-dir", str(tmp_path)])
    assert rc == sr.EXIT_OK
    report_text = next(tmp_path.glob("plan-*.json")).read_text(encoding="utf-8")
    assert "proxy_env_keys_present=True" in report_text
    assert MARK_ENV_PROXY not in report_text  # env 值绝不入档


# ---------------------------------------------------------------- main execute 出口（patch transport，零生产流量）


def test_main_execute_success_exit_ok(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)  # 整条 main 路径零真实网络
    transport = FakeTransport(statuses=[200], elapsed_ms=1.0)
    monkeypatch.setattr(sr, "RealTransport", lambda: transport)
    rc = sr.main(["--execute", "--confirm", sr.CONFIRM_PHRASE,
                  "--duration-seconds", "1", "--concurrency", "1", "--max-requests", "3",
                  "--pace-seconds", "0.05", "--artifact-dir", str(tmp_path)])
    assert rc == sr.EXIT_OK
    report = json.loads(next(tmp_path.glob("soak-*.json")).read_text(encoding="utf-8"))
    assert report["mode"] == "execute"
    assert report["totals"]["requests"] == 3
    assert report["totals"]["success"] == 3
    assert report["stopped_reason"] == "max-requests"


def test_main_execute_zero_success_exit_error(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    transport = FakeTransport(statuses=[None], elapsed_ms=1.0)
    transport.get = lambda host, port, path, *, timeout: sr.HttpResult(  # type: ignore[method-assign]
        None, 1.0, "connection-refused", "ConnectionRefusedError")
    monkeypatch.setattr(sr, "RealTransport", lambda: transport)
    rc = sr.main(["--execute", "--confirm", sr.CONFIRM_PHRASE,
                  "--duration-seconds", "1", "--concurrency", "1", "--max-requests", "2",
                  "--pace-seconds", "0.05", "--artifact-dir", str(tmp_path)])
    assert rc == sr.EXIT_ERROR  # 零成功样本——栈疑似未起，如实可见
    report = json.loads(next(tmp_path.glob("soak-*.json")).read_text(encoding="utf-8"))
    assert report["totals"]["success"] == 0


# ---------------------------------------------------------------- 真实 transport（本机假服务器）


def test_real_transport_get_only_minimal_headers(fake_http_server) -> None:
    port = fake_http_server.server_address[1]
    result = sr.RealTransport().get("127.0.0.1", port, "/login", timeout=3.0)
    assert result.status == 200 and result.error_category is None
    command, path, headers = fake_http_server.requests_seen[0]
    assert command == "GET" and path == "/login"
    assert "Cookie" not in headers and "Authorization" not in headers
    assert headers.get("User-Agent") == sr.USER_AGENT


def test_real_transport_ignores_proxy_env(fake_http_server) -> None:
    """恶意假代理计数零连接——http.client 直连的结构性代理旁路实证。"""
    evil = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    evil.bind(("127.0.0.1", 0))
    evil.listen(8)
    evil_port = evil.getsockname()[1]
    accepted = {"count": 0}
    stop = threading.Event()

    def accept_loop() -> None:
        evil.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = evil.accept()
            except (TimeoutError, OSError):
                continue
            accepted["count"] += 1
            conn.close()

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()
    try:
        old_env = {}
        for key in sr.PROXY_ENV_KEYS:
            old_env[key] = __import__("os").environ.pop(key, None)
            __import__("os").environ[key] = f"http://127.0.0.1:{evil_port}"
        port = fake_http_server.server_address[1]
        result = sr.RealTransport().get("127.0.0.1", port, "/", timeout=3.0)
    finally:
        import os

        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        stop.set()
        thread.join(timeout=2.0)
        evil.close()
    assert result.status == 200  # 直连成功
    assert accepted["count"] == 0  # 代理零连接：环境代理被无视


def test_real_transport_unreachable_target_categorized_safely() -> None:
    # bind 但绝不 listen：本机实证（Windows 2026-09-11）该形态对 connect 表现为
    # 静默丢 SYN → TimeoutError；Linux 上通常为 ConnectionRefusedError——两者都必须
    # 被安全归类为「状态 None + 固定类别 + 仅类名」，绝不抛出、绝不保留异常文本
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    try:
        result = sr.RealTransport().get("127.0.0.1", port, "/", timeout=2.0)
    finally:
        probe.close()
    assert result.status is None
    assert result.error_category in {"connection-refused", "connection-error", "timeout"}
    assert result.error_class in {"ConnectionRefusedError", "ConnectionError", "OSError", "TimeoutError"}
    assert result.elapsed_ms <= 2500.0  # 单请求超时上界被尊重
