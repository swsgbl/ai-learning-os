r"""M14-12 tools/ops/production_monitor.py 契约测试：只读监控采集 + 阈值判定 +
证据报告的安全边界，零真实生产读取。

覆盖（绝不调用真实 Docker/HTTP/计划任务；唯一真实 socket 流量是测试自起的
127.0.0.1 临时假服务器/假代理，验证直连与代理旁路；全部采集行为经
FakeRunner/FakeTransport 注入）：
- plan 默认零副作用（socket 与 subprocess 双阻断下照常出计划与 plan 报告，
  报告无 overall_status/monitoring_ready 状态宣称）；
- execute 门禁 fail-closed：缺旗标/短语不精确/阈值超硬顶（含 execute +
  正确短语时超顶同样拒绝）→ EXIT 2 且零采集（Runner/Transport 零构造）；
- loopback 纪律：固定五端点画像校验矩阵（拒绝非字面 loopback IP、主机名、
  query/fragment/userinfo、非 http scheme、缺显式端口）；
- 子进程白名单门：仅 docker compose ps / inspect --format / logs --tail
  三只读形态放行；stop/rm/kill/restart/down/exec/up/logs -f/非 docker
  程序一律拒绝（拒绝发生在任何执行之前）；源码契约（无 proxy-aware 网络
  路径、无第三方 HTTP 库、仅 GET、仅 UA/Accept 头、无 https/Cookie/
  Authorization 面）；
- 采集器语义：compose ps 三形态解析（数组/单对象/JSONL）+ 垃圾拒绝；
  inspect 五事实解析与失败类别（rc≠0/字段数不对/restart 非整数）；端点
  GET 注入 + 异常仅类别+类名；日志摘要只计数（级别词边界、原文绝不入档）；
  部分失败 partial=true + 缺失≠healthy；
- 阈值边界（纯函数）：五端点 200、延迟 warn/critical 含边界、restart、
  日志错误、容器 health（unhealthy→critical / none·starting→warn）、
  compose 6/6、precedence（incomplete > critical > warn）、计数自洽、
  monitoring_ready 仅完整且零 alert；
- 报告：schema 键、UTC 时间戳、边界注记、防御性脱敏（标记值绝不落入
  JSON/Markdown）、proxy env 仅键名存在性；原子写（tmp+os.replace、无
  残留、同 stem 覆盖干净）与 symlink/越界 stem 拒绝；
- CLI：退出码矩阵（plan 0 / warn 0 但 warn 恒可见 / incomplete·critical·
  报告写失败 2）、旗标默认值注册、README 文档化；
- 真实 transport（本机假服务器）：GET-only + 仅 UA/Accept 头、环境代理被
  无视（恶意假代理零连接实证）、不可达目标安全归类；
- R1 修正（supervisor 评审，2026-09-12）：非有限浮点（nan/inf/-inf）在
  plan 与 fully-confirmed-execute 双路径 fail-closed（零 plan 产物/零采集/
  零执行报告）；`--project` 严格白名单（接受矩阵/拒绝矩阵/固定词汇拒绝
  原因/CLI 双路径/被拒值零回显）；`--artifact-dir` 口径修正（默认目录
  gitignored vs 操作者显式自选——REPORT_BOUNDARIES/CLI help/运行时注记
  三面锁定）；状态文档 commit 措辞 sweep（supervisor 审查与 remote 发布）。
- M14-23 restart 语义修复：RestartCount 累计值 vs 当轮新增增量——基线
  解析（最新合法 prior 完整工件；纯本地只读 fail-safe，零 shell/零网络；
  非法工件显式计数绝不静默当零基线）、增量阈值（含边界 1/5）、无基线
  一次性可见告警、count 不变恢复 ok、started_at 变化=重建与 count 下降
  =重置的一轮可见 warn（负增量绝不静默映射为零）、schema 加法字段
  threshold_results.restart_evaluation（v1 旧工件/旧记录保持有效）、
  e2e 两轮恢复与零额外生产读取。
"""
from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "production_monitor.py"
OPS_README = REPO_ROOT / "tools" / "ops" / "README.md"

#: 标记值（仅出现在测试注入面，断言其绝不进入任何文件/日志）
MARK_TOKEN = "sk-ZXmarker0123456789"
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


pm = _load_module(SCRIPT, "production_monitor_under_test")

PROJECT = pm.DEFAULT_PROJECT
COMPOSE = "infra/docker-compose.yml"


# ---------------------------------------------------------------- fakes


class FakeRunner:
    """伪 Docker 命令面：按 argv 形态回放预制结果，记录全部调用。

    inspect_facts/logs_lines 键为 service；值为 None 表示该命令 rc≠0。
    """

    def __init__(self, *, project: str = PROJECT,
                 ps_rows: list[dict[str, str]] | None = None,
                 inspect_facts: dict[str, str | None] | None = None,
                 logs_lines: dict[str, list[str] | None] | None = None) -> None:
        self.project = project
        self.ps_rows = ps_rows if ps_rows is not None else [
            {"Service": svc, "Health": "healthy", "State": "running"} for svc in pm.STACK_SERVICES
        ]
        self.inspect_facts = inspect_facts if inspect_facts is not None else {
            svc: f"running\thealthy\t0\taios/{svc}:m14-03-prod-rehearsal\t2026-09-11T00:00:00Z"
            for svc in pm.STACK_SERVICES
        }
        self.logs_lines = logs_lines if logs_lines is not None else {
            svc: ["info: request ok", "info: done"] for svc in pm.STACK_SERVICES
        }
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout: float = 60.0, encoding: str | None = None) -> pm.CommandResult:
        argv = tuple(str(item) for item in argv)
        self.calls.append(argv)
        if argv[0] != "docker":
            return pm.CommandResult(argv, 127, "", "not-found")
        if argv[1] == "compose":
            stdout = json.dumps(self.ps_rows)
            return pm.CommandResult(argv, 0, stdout, "")
        service = argv[-1][len(self.project) + 1:].rsplit("-", 1)[0]
        if argv[1] == "inspect":
            facts = self.inspect_facts.get(service, None)
            if facts is None:
                return pm.CommandResult(argv, 1, "", "no such object")
            return pm.CommandResult(argv, 0, facts, "")
        if argv[1] == "logs":
            lines = self.logs_lines.get(service, None)
            if lines is None:
                return pm.CommandResult(argv, 1, "", "no such object")
            return pm.CommandResult(argv, 0, "\n".join(lines), "")
        return pm.CommandResult(argv, 125, "", "unsupported")


class FakeTransport:
    """可配置伪 transport：记录调用（host/port/path/timeout）；可注入异常。"""

    def __init__(self, *, status: int | None = 200, elapsed_ms: float = 10.0,
                 raise_exc: Exception | None = None) -> None:
        self.calls: list[tuple[str, int, str, float]] = []
        self.status = status
        self.elapsed_ms = elapsed_ms
        self.raise_exc = raise_exc

    def get(self, host: str, port: int, path: str, *, timeout: float) -> pm.HttpResult:
        self.calls.append((host, port, path, timeout))
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.status is None:
            return pm.HttpResult(None, 1.0, "connection-refused", "ConnectionRefusedError")
        return pm.HttpResult(self.status, self.elapsed_ms, None, None)


def _block_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network is forbidden in this test")

    monkeypatch.setattr(socket, "socket", _forbidden)


def _block_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess as subprocess_module

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("subprocess is forbidden in this test")

    monkeypatch.setattr(subprocess_module, "run", _forbidden)


def _patch_gate(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner,
                transport: FakeTransport) -> dict[str, int]:
    """把 main 的 Runner/Transport 构造替换为计数工厂（零真实执行）。"""
    constructions = {"runner": 0, "transport": 0}

    def make_runner() -> FakeRunner:
        constructions["runner"] += 1
        return runner

    def make_transport() -> FakeTransport:
        constructions["transport"] += 1
        return transport

    monkeypatch.setattr(pm, "RealRunner", make_runner)
    monkeypatch.setattr(pm, "RealTransport", make_transport)
    return constructions


def _plan_report() -> dict[str, object]:
    config = pm.build_config(
        project=PROJECT, profile=pm.DEFAULT_PROFILE, compose_file=Path(COMPOSE),
        endpoints=list(pm.ENDPOINTS), log_tail=pm.DEFAULT_LOG_TAIL,
        request_timeout_seconds=pm.DEFAULT_REQUEST_TIMEOUT_SECONDS,
        thresholds=pm.Thresholds(
            latency_warn_ms=1000.0, latency_critical_ms=5000.0, restart_warn=1,
            restart_critical=5, log_error_warn=5, log_error_critical=20,
        ),
    )
    return pm.build_report(mode="plan", started_utc="2026-09-11T00:00:00Z",
                           ended_utc="2026-09-11T00:00:01Z", config=config,
                           collectors=None, threshold_results=None,
                           proxy_env_keys_present=False)


# ---------------------------------------------------------------- plan / 门禁


def test_plan_mode_zero_side_effects(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    _block_subprocess(monkeypatch)
    rc = pm.main(["--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_OK
    plan_files = sorted(p.name for p in tmp_path.glob("plan-*"))
    assert len(plan_files) == 2  # json + md
    report = json.loads((tmp_path / plan_files[0]).read_text(encoding="utf-8"))
    assert report["mode"] == "plan"
    assert report["collectors"]["status"] == "planned"


def test_plan_report_makes_no_status_claims(monkeypatch, tmp_path) -> None:
    """plan 零采集——不得出现 overall_status/monitoring_ready 状态宣称。"""
    _block_sockets(monkeypatch)
    rc = pm.main(["--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_OK
    text = next(tmp_path.glob("plan-*.json")).read_text(encoding="utf-8")
    assert '"overall_status"' not in text
    assert '"monitoring_ready"' not in text


def test_plan_lists_fixed_profile(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    rc = pm.main(["--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_OK
    report = json.loads(next(tmp_path.glob("plan-*.json")).read_text(encoding="utf-8"))
    config = report["config"]
    assert config["project"] == PROJECT
    assert config["services"] == list(pm.STACK_SERVICES)
    assert [e["endpoint_id"] for e in config["endpoints"]] == [
        "web-root", "web-login", "api-health", "funasr-health", "cosyvoice-health",
    ]


def test_execute_without_confirm_refused_zero_collection(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    runner, transport = FakeRunner(), FakeTransport()
    constructions = _patch_gate(monkeypatch, runner, transport)
    rc = pm.main(["--execute", "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_USAGE
    assert runner.calls == [] and transport.calls == []
    assert constructions == {"runner": 0, "transport": 0}  # 门禁前零构造
    assert not list(tmp_path.glob("monitor-*"))  # 零执行、零执行报告


@pytest.mark.parametrize("phrase", [
    "", "execute read-only production monitoring",
    "EXECUTE READ-ONLY PRODUCTION MONITORING ",
    " EXECUTE READ-ONLY PRODUCTION MONITORING",
    "EXECUTE READONLY PRODUCTION MONITORING",
    "EXECUTE READ-ONLY PRODUCTION MONITORING!",
])
def test_execute_wrong_phrase_refused(monkeypatch, tmp_path, phrase: str) -> None:
    _block_sockets(monkeypatch)
    runner, transport = FakeRunner(), FakeTransport()
    constructions = _patch_gate(monkeypatch, runner, transport)
    rc = pm.main(["--execute", "--confirm", phrase, "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_USAGE
    assert runner.calls == [] and constructions["runner"] == 0


@pytest.mark.parametrize("args", [
    ["--request-timeout-seconds", "0.4"],
    ["--request-timeout-seconds", "10.5"],
    ["--latency-warn-ms", "49"],
    ["--latency-warn-ms", "600001"],
    ["--latency-critical-ms", "49"],
    ["--latency-warn-ms", "5000", "--latency-critical-ms", "5000"],
    ["--restart-warn", "-1"],
    ["--restart-warn", "1001"],
    ["--restart-critical", "1001"],
    ["--restart-warn", "6", "--restart-critical", "5"],
    ["--log-error-warn", "-1"],
    ["--log-error-warn", "100001"],
    ["--log-error-critical", "100001"],
    ["--log-error-warn", "21", "--log-error-critical", "20"],
    ["--log-tail", "9"],
    ["--log-tail", "2001"],
])
def test_threshold_bounds_fail_closed_even_in_plan(monkeypatch, tmp_path, args: list[str]) -> None:
    _block_sockets(monkeypatch)
    rc = pm.main([*args, "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_USAGE
    assert not list(tmp_path.iterdir())  # 零报告残留


def test_threshold_bounds_fail_closed_even_with_correct_confirm(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    runner, transport = FakeRunner(), FakeTransport()
    constructions = _patch_gate(monkeypatch, runner, transport)
    rc = pm.main(["--execute", "--confirm", pm.CONFIRM_PHRASE,
                  "--latency-warn-ms", "600001", "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_USAGE  # 超硬顶：即使旗标+短语齐备也零采集拒绝
    assert constructions["runner"] == 0


def test_only_unknown_endpoint_refused(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    rc = pm.main(["--only", "evil-endpoint", "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_USAGE


def test_only_known_endpoint_subset_in_plan(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    rc = pm.main(["--only", "api-health", "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_OK
    report = json.loads(next(tmp_path.glob("plan-*.json")).read_text(encoding="utf-8"))
    assert [e["endpoint_id"] for e in report["config"]["endpoints"]] == ["api-health"]


# ---------------------------------------------------------------- 目标面 / loopback


@pytest.mark.parametrize("url", [e.url for e in pm.ENDPOINTS]
                         + ["http://[::1]:8000/health", "http://127.200.0.1:3011/"])
def test_validate_accepts_loopback_literal_profile(url: str) -> None:
    assert pm.validate_target_url(url) is None


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
    assert pm.validate_target_url(url) is not None


def test_endpoint_profile_is_five_fixed_loopback_targets() -> None:
    assert len(pm.ENDPOINTS) == 5
    ports = set()
    for endpoint in pm.ENDPOINTS:
        assert pm.validate_target_url(endpoint.url) is None
        host_port = endpoint.url.split("//", 1)[1].split("/", 1)[0]
        ports.add(host_port.rsplit(":", 1)[1])
        if endpoint.group == "voice":
            assert endpoint.url.endswith("/health")
    assert ports == {"3011", "8000", "8010", "8011"}


def test_select_endpoints_unknown_refused() -> None:
    picked, error = pm.select_endpoints(None)
    assert error is None and len(picked) == 5
    picked, error = pm.select_endpoints(["web-root", "api-health"])
    assert error is None and [e.endpoint_id for e in picked] == ["web-root", "api-health"]
    _, error = pm.select_endpoints(["web-root", "nope"])
    assert error is not None


# ---------------------------------------------------------------- 子进程白名单门


@pytest.mark.parametrize("argv", [
    ["docker", "compose", "-f", "infra/docker-compose.yml", "-p", PROJECT,
     "--profile", "local", "ps", "--format", "json"],
    ["docker", "inspect", "--format", pm.INSPECT_FORMAT, f"{PROJECT}-api-1"],
    ["docker", "logs", "--tail", "200", f"{PROJECT}-api-1"],
])
def test_whitelist_accepts_three_readonly_shapes(argv: list[str]) -> None:
    assert pm.is_readonly_docker_command(argv) is True


@pytest.mark.parametrize("argv", [
    ["docker", "stop", "x"],
    ["docker", "rm", "x"],
    ["docker", "kill", "x"],
    ["docker", "restart", "x"],
    ["docker", "exec", "x", "ls"],
    ["docker", "logs", "-f", "x"],                       # 跟随/长驻形态
    ["docker", "logs", "x"],                             # 无 --tail
    ["docker", "logs", "--since", "1h", "x"],            # 未白名单旗标
    ["docker", "logs", "--tail", "100", "x", "y"],       # 多容器名
    ["docker", "inspect", "x"],                          # 无 --format
    ["docker", "inspect", "--size", "--format", "f", "x"],
    ["docker", "compose", "-f", "c", "-p", "p", "--profile", "local", "up", "-d"],
    ["docker", "compose", "-f", "c", "-p", "p", "--profile", "local", "down"],
    ["docker", "compose", "-f", "c", "-p", "p", "--profile", "local",
     "ps", "--format", "json", "--all"],                 # ps 后仅 --format json
    ["docker", "version"],
    ["curl", "http://127.0.0.1:8000/health"],
    ["powershell", "-Command", "Get-Thing"],
    ["cmd", "/c", "dir"],
])
def test_whitelist_rejects_everything_else(argv: list[str]) -> None:
    assert pm.is_readonly_docker_command(argv) is False


def test_readonly_runner_refuses_before_execution() -> None:
    inner = FakeRunner()
    runner = pm.ReadonlyRunner(inner)
    with pytest.raises(pm.CommandNotAllowedError):
        runner.run(["docker", "stop", f"{PROJECT}-api-1"])
    assert inner.calls == []  # 拒绝发生在任何执行之前
    # 白名单命令照常透传
    result = runner.run(["docker", "inspect", "--format", pm.INSPECT_FORMAT,
                         f"{PROJECT}-api-1"])
    assert result.returncode == 0
    assert len(inner.calls) == 1


def test_command_failure_categories_are_class_names_only() -> None:
    assert pm.categorize_runner_exception(pm.CommandNotAllowedError("x")) == (
        "command-not-whitelisted", "CommandNotAllowedError")
    assert pm.categorize_runner_exception(pm.RunnerError("boom " + MARK_TOKEN))[0] == "command-exec-error"
    assert pm.categorize_runner_exception(pm.RunnerError("x"))[1] == "RunnerError"
    import subprocess as subprocess_module

    assert pm.categorize_runner_exception(subprocess_module.TimeoutExpired("docker", 1)) == (
        "command-timeout", "TimeoutExpired")
    assert pm.categorize_runner_exception(OSError("never logged"))[0] == "command-exec-error"
    assert pm.categorize_runner_exception(ValueError())[0] == "internal-error"


def test_http_exception_categories_are_class_names_only() -> None:
    import http.client

    assert pm.categorize_http_exception(ConnectionRefusedError())[0] == "connection-refused"
    assert pm.categorize_http_exception(TimeoutError())[0] == "timeout"
    assert pm.categorize_http_exception(http.client.BadStatusLine("garbage"))[0] == "http-protocol-error"
    assert pm.categorize_http_exception(OSError("never logged " + MARK_TOKEN)) == ("connection-error", "OSError")
    assert pm.categorize_http_exception(ValueError())[0] == "internal-error"


# ---------------------------------------------------------------- 源码契约


def test_source_contract_forbidden_tokens() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for token in (
        "urllib.request", "urlopen", "getproxies", "ProxyHandler",
        "import requests", "aiohttp", "httpx", "https://",
        "Cookie", "Authorization", "Bearer",
        '"POST"', '"PUT"', '"PATCH"', '"DELETE"',
    ):
        assert token not in source, f"禁止出现的字面量: {token}"
    # 仅一处发请求点，且恒为 GET、仅 UA/Accept 两个固定头
    assert source.count('connection.request("GET"') == 1
    assert '"User-Agent"' in source and '"Accept"' in source
    # 唯一 subprocess 执行点（RealRunner.run），Windows 侧恒 CREATE_NO_WINDOW
    assert source.count("subprocess.run(") == 1
    assert "CREATE_NO_WINDOW" in source


# ---------------------------------------------------------------- 采集器单元


def test_parse_compose_ps_accepts_array_jsonl_and_single_object() -> None:
    rows = pm.parse_compose_ps_rows(
        json.dumps([{"Service": "api", "Health": "healthy", "State": "running"}])
    )
    assert rows == [{"service": "api", "health": "healthy", "state": "running"}]
    rows = pm.parse_compose_ps_rows(
        '{"Service": "api", "Health": "healthy", "State": "running"}'
    )
    assert len(rows) == 1 and rows[0]["service"] == "api"
    rows = pm.parse_compose_ps_rows(
        '{"Service": "api", "Health": "healthy"}\n{"Service": "web", "Health": "healthy"}\n'
    )
    assert [r["service"] for r in rows] == ["api", "web"]


def test_parse_compose_ps_rejects_garbage_and_serviceless_rows() -> None:
    assert pm.parse_compose_ps_rows("not json at all\n") == []
    assert pm.parse_compose_ps_rows("") == []
    # 无 Service 键（Name 是全容器名，不可靠映射）→ fail-closed 跳过
    assert pm.parse_compose_ps_rows(json.dumps([{"Name": "proj-api-1", "State": "running"}])) == []


def test_collect_compose_ps_failures_categorized() -> None:
    runner = FakeRunner(ps_rows=[{"Service": "api", "Health": "healthy", "State": "running"}])
    services, failure = pm.collect_compose_ps(
        runner, compose_file=Path(COMPOSE), project=PROJECT, profile=pm.DEFAULT_PROFILE)
    assert failure is None and services is not None
    assert "api" in services
    # 单服务不全（缺五服务）→ 行仍在，阈值判定按缺失处理（见阈值测试）
    empty = FakeRunner(ps_rows=[])
    _, failure = pm.collect_compose_ps(empty, compose_file=Path(COMPOSE),
                                       project=PROJECT, profile=pm.DEFAULT_PROFILE)
    assert failure is not None and failure.category == "compose-ps-unparseable"


def test_collect_container_facts_parses_five_fields() -> None:
    runner = FakeRunner()
    facts, failure = pm.collect_container_facts(runner, project=PROJECT, service="api")
    assert failure is None and facts is not None
    assert facts.name == f"{PROJECT}-api-1"
    assert (facts.state, facts.health, facts.restart_count) == ("running", "healthy", 0)
    assert facts.image == "aios/api:m14-03-prod-rehearsal"
    assert facts.started_at == "2026-09-11T00:00:00Z"


def test_collect_container_facts_failure_categories() -> None:
    missing = FakeRunner(inspect_facts={"api": None})
    _, failure = pm.collect_container_facts(missing, project=PROJECT, service="api")
    assert failure is not None and failure.category == "inspect-nonzero-exit"
    short = FakeRunner(inspect_facts={"api": "running\thealthy\t0\timg"})
    _, failure = pm.collect_container_facts(short, project=PROJECT, service="api")
    assert failure is not None and failure.category == "inspect-unparseable"
    bad_int = FakeRunner(inspect_facts={"api": "running\thealthy\tmany\timg\t2026"})
    _, failure = pm.collect_container_facts(bad_int, project=PROJECT, service="api")
    assert failure is not None and failure.category == "inspect-unparseable"
    assert failure.error_class == "ValueError"


def test_collect_endpoint_passes_profile_and_timeout() -> None:
    transport = FakeTransport(status=200, elapsed_ms=12.5)
    endpoint = pm.ENDPOINTS[0]
    result = pm.collect_endpoint(transport, endpoint, timeout_seconds=4.0)
    assert result.status == 200 and result.error_category is None
    assert transport.calls == [("127.0.0.1", 3011, "/", 4.0)]


def test_collect_endpoint_exception_categorized_without_text() -> None:
    transport = FakeTransport(raise_exc=RuntimeError("leak " + MARK_TOKEN))
    result = pm.collect_endpoint(transport, pm.ENDPOINTS[2], timeout_seconds=1.0)
    assert result.status is None
    assert result.error_category == "internal-error"
    assert result.error_class == "RuntimeError"
    assert MARK_TOKEN not in json.dumps(result.__dict__)


def test_summarize_log_lines_counts_levels_only() -> None:
    lines = [
        "INFO request ok status=200",
        "ERROR database " + MARK_TOKEN,
        "error: retry failed",
        "WARN slow query",
        "warning: deprecated",
        "Traceback (most recent call last):",
        "panic: runtime error",
        "FATAL: cannot start",
        "CRITICAL: disk full",
        "debug fine noErrorWordBoundary",  # "noErrorWord" 内嵌不匹配 \berror\b？——"noErrorWord" 含 error 但无边界
    ]
    summary = pm.summarize_log_lines(lines)
    assert summary["lines_scanned"] == 10
    # error 命中 3 行：ERROR database / error: retry / panic: runtime **error**
    # （"noErrorWordBoundary" 内嵌无词边界，不匹配）
    assert summary["levels"]["error"] == 3
    assert summary["levels"]["warning"] == 2
    assert summary["levels"]["traceback"] == 1
    assert summary["levels"]["panic"] == 1
    assert summary["levels"]["fatal"] == 1
    assert summary["levels"]["critical"] == 1
    assert summary["error_total"] == 5  # fatal+error+critical（warning 不计入）
    assert MARK_TOKEN not in json.dumps(summary)  # 原文绝不进入摘要


def test_collect_log_summary_failure_category() -> None:
    runner = FakeRunner(logs_lines={"api": None})
    summary, failure = pm.collect_log_summary(runner, project=PROJECT, service="api", tail=100)
    assert summary is None and failure is not None
    assert failure.category == "logs-nonzero-exit"


def test_collect_snapshot_happy_path_and_partial() -> None:
    runner, transport = FakeRunner(), FakeTransport()
    collectors = pm.collect_snapshot(
        runner=runner, transport=transport, endpoints=list(pm.ENDPOINTS), project=PROJECT,
        profile=pm.DEFAULT_PROFILE, compose_file=Path(COMPOSE),
        log_tail=100, request_timeout_seconds=5.0,
    )
    assert pm.snapshot_partial(collectors) is False
    for name in ("compose_ps", "containers", "endpoints", "logs"):
        assert collectors[name]["status"] == "ok"
    # 部分失败：单容器 inspect 失败 → partial=true，失败项类别入档
    broken = FakeRunner(inspect_facts={"api": None})
    collectors = pm.collect_snapshot(
        runner=broken, transport=FakeTransport(), endpoints=list(pm.ENDPOINTS),
        project=PROJECT, profile=pm.DEFAULT_PROFILE, compose_file=Path(COMPOSE),
        log_tail=100, request_timeout_seconds=5.0,
    )
    assert pm.snapshot_partial(collectors) is True
    assert collectors["containers"]["status"] == "failed"
    assert collectors["containers"]["per_service"]["api"]["status"] == "failed"
    assert collectors["containers"]["per_service"]["api"]["failure_category"] == "inspect-nonzero-exit"


# ---------------------------------------------------------------- 阈值（纯函数）


def _collectors(*, latency_ms: float = 10.0, http_status: int = 200,
                endpoint_failed: bool = False, restarts: int = 0,
                health: str = "healthy", state: str = "running",
                ps_health: str = "healthy", ps_missing: tuple[str, ...] = (),
                log_errors: int = 0, log_failed: bool = False,
                inspect_failed: tuple[str, ...] = (),
                started_at: str = "2026-09-11T00:00:00Z") -> dict[str, object]:
    ps_services = {
        svc: {"health": ps_health, "state": "running"}
        for svc in pm.STACK_SERVICES if svc not in ps_missing
    }
    per_service: dict[str, dict[str, object]] = {}
    for svc in pm.STACK_SERVICES:
        if svc in inspect_failed:
            per_service[svc] = {"status": "failed", "failure_category": "inspect-nonzero-exit",
                                "error_class": None}
        else:
            per_service[svc] = {
                "status": "ok", "failure_category": None, "error_class": None,
                "name": f"{PROJECT}-{svc}-1", "state": state, "health": health,
                "restart_count": restarts, "image": f"aios/{svc}:tag",
                "started_at": started_at,
            }
    per_endpoint: dict[str, dict[str, object]] = {}
    for endpoint in pm.ENDPOINTS:
        if endpoint_failed:
            per_endpoint[endpoint.endpoint_id] = {
                "status": "failed", "failure_category": "connection-refused",
                "error_class": "ConnectionRefusedError", "http_status": None, "latency_ms": None,
            }
        else:
            per_endpoint[endpoint.endpoint_id] = {
                "status": "ok", "failure_category": None, "error_class": None,
                "http_status": http_status, "latency_ms": latency_ms,
            }
    per_log: dict[str, dict[str, object]] = {}
    for svc in pm.STACK_SERVICES:
        if log_failed:
            per_log[svc] = {"status": "failed", "failure_category": "logs-nonzero-exit",
                            "error_class": None}
        else:
            per_log[svc] = {
                "status": "ok", "failure_category": None, "error_class": None,
                "lines_scanned": 50,
                "levels": {"fatal": 0, "error": log_errors, "critical": 0,
                           "warning": 0, "traceback": 0, "panic": 0},
                "error_total": log_errors,
            }
    return {
        # compose ps 命令本身成功（status=ok）；ps_missing 仅表示某服务未列出席位
        "compose_ps": {"status": "ok", "failure_category": None, "error_class": None,
                       "services": ps_services},
        "containers": {"status": "failed" if inspect_failed else "ok", "per_service": per_service},
        "endpoints": {"status": "failed" if endpoint_failed else "ok", "per_endpoint": per_endpoint},
        "logs": {"status": "failed" if log_failed else "ok", "per_service": per_log},
    }


def _thresholds() -> pm.Thresholds:
    return pm.Thresholds(latency_warn_ms=1000.0, latency_critical_ms=5000.0,
                         restart_warn=1, restart_critical=5,
                         log_error_warn=5, log_error_critical=20)


def test_evaluate_all_ok_full_check_inventory() -> None:
    results = pm.evaluate_thresholds(_collectors(), _thresholds())
    assert results["overall_status"] == "ok"
    assert results["monitoring_ready"] is True
    assert results["partial"] is False
    assert results["alerts"] == []
    # 计数自洽：34 = 6 compose + 6 容器 health + 6 restart + 5 端点状态
    #           + 5 延迟 + 6 日志
    assert len(results["checks"]) == 34
    assert sum(results["counts"].values()) == len(results["checks"])
    assert results["counts"] == {"ok": 34, "warn": 0, "critical": 0}


@pytest.mark.parametrize("latency,severity", [
    (999.999, "ok"), (1000.0, "warn"), (4999.9, "warn"), (5000.0, "critical"), (9000.0, "critical"),
])
def test_evaluate_latency_boundaries_inclusive(latency: float, severity: str) -> None:
    results = pm.evaluate_thresholds(_collectors(latency_ms=latency), _thresholds())
    latencies = [c for c in results["checks"] if c["check_id"] == "endpoint-latency"]
    assert all(check["severity"] == severity for check in latencies)
    if severity != "ok":
        assert results["overall_status"] == severity
        assert results["monitoring_ready"] is False


@pytest.mark.parametrize("restarts,severity", [
    (0, "ok"), (1, "warn"), (4, "warn"), (5, "critical"), (30, "critical"),
])
def test_evaluate_restart_boundaries_inclusive(restarts: int, severity: str) -> None:
    results = pm.evaluate_thresholds(_collectors(restarts=restarts), _thresholds())
    checks = [c for c in results["checks"] if c["check_id"] == "container-restarts"]
    assert all(check["severity"] == severity for check in checks)


@pytest.mark.parametrize("log_errors,severity", [
    (4, "ok"), (5, "warn"), (19, "warn"), (20, "critical"), (100, "critical"),
])
def test_evaluate_log_error_boundaries_inclusive(log_errors: int, severity: str) -> None:
    results = pm.evaluate_thresholds(_collectors(log_errors=log_errors), _thresholds())
    checks = [c for c in results["checks"] if c["check_id"] == "log-errors"]
    assert all(check["severity"] == severity for check in checks)


@pytest.mark.parametrize("health,state,severity", [
    ("healthy", "running", "ok"),
    ("unhealthy", "running", "critical"),
    ("starting", "running", "warn"),
    ("none", "running", "warn"),
    ("healthy", "exited", "critical"),
    ("healthy", "restarting", "critical"),
])
def test_evaluate_container_health_matrix(health: str, state: str, severity: str) -> None:
    results = pm.evaluate_thresholds(_collectors(health=health, state=state), _thresholds())
    checks = [c for c in results["checks"] if c["check_id"] == "container-health"]
    assert all(check["severity"] == severity for check in checks)


@pytest.mark.parametrize("http_status,severity", [
    (200, "ok"), (301, "critical"), (404, "critical"), (500, "critical"), (503, "critical"),
])
def test_evaluate_endpoint_requires_exactly_200(http_status: int, severity: str) -> None:
    results = pm.evaluate_thresholds(_collectors(http_status=http_status), _thresholds())
    checks = [c for c in results["checks"] if c["check_id"] == "endpoint-status"]
    assert all(check["severity"] == severity for check in checks)


def test_evaluate_compose_missing_service_is_critical() -> None:
    results = pm.evaluate_thresholds(_collectors(ps_missing=("livekit",)), _thresholds())
    subject = next(c for c in results["checks"]
                   if c["subject"] == "livekit" and c["check_id"] == "compose-service")
    assert subject["severity"] == "critical"
    assert subject["detail"] == "missing-from-compose-ps"
    assert results["overall_status"] == "critical"


def test_evaluate_compose_health_not_healthy_is_critical() -> None:
    results = pm.evaluate_thresholds(_collectors(ps_health="unhealthy"), _thresholds())
    compose_checks = [c for c in results["checks"] if c["check_id"] == "compose-service"]
    assert all(check["severity"] == "critical" for check in compose_checks)


def test_evaluate_partial_failure_missing_is_not_healthy() -> None:
    """采集器失败 → partial/incomplete；缺失项恒为可见 critical，绝不视为 healthy。"""
    results = pm.evaluate_thresholds(_collectors(inspect_failed=("api",)), _thresholds())
    assert results["partial"] is True
    assert results["overall_status"] == "incomplete"
    assert results["monitoring_ready"] is False
    api_health = next(c for c in results["checks"]
                      if c["subject"] == "api" and c["check_id"] == "container-health")
    assert api_health["severity"] == "critical" and api_health["detail"] == "facts-missing"


def test_evaluate_ps_collector_failure_adds_collector_alert() -> None:
    collectors = _collectors()
    collectors["compose_ps"]["status"] = "failed"
    collectors["compose_ps"]["failure_category"] = "compose-ps-nonzero-exit"
    collectors["compose_ps"]["services"] = {}
    results = pm.evaluate_thresholds(collectors, _thresholds())
    collector_alert = next(c for c in results["checks"] if c["check_id"] == "collector:compose-ps")
    assert collector_alert["severity"] == "critical"
    assert collector_alert["detail"] == "category=compose-ps-nonzero-exit"
    assert results["overall_status"] == "incomplete"


def test_evaluate_endpoint_failure_is_critical_and_incomplete() -> None:
    results = pm.evaluate_thresholds(_collectors(endpoint_failed=True), _thresholds())
    assert results["partial"] is True and results["overall_status"] == "incomplete"
    statuses = [c for c in results["checks"] if c["check_id"] == "endpoint-status"]
    assert all(check["severity"] == "critical" for check in statuses)


def test_evaluate_log_collector_failure_visible() -> None:
    results = pm.evaluate_thresholds(_collectors(log_failed=True), _thresholds())
    log_checks = [c for c in results["checks"] if c["check_id"] == "log-errors"]
    assert all(check["severity"] == "critical" for check in log_checks)
    assert results["overall_status"] == "incomplete"


def test_evaluate_precedence_incomplete_beats_critical() -> None:
    # 既有 critical（endpoint 500）又有 partial（inspect 失败）→ incomplete 优先
    results = pm.evaluate_thresholds(
        _collectors(http_status=500, inspect_failed=("api",)), _thresholds())
    assert results["overall_status"] == "incomplete"
    # critical 优先于 warn
    results = pm.evaluate_thresholds(
        _collectors(latency_ms=1000.0, restarts=99), _thresholds())
    assert results["overall_status"] == "critical"


def test_monitoring_ready_false_on_warn_and_counts_consistent() -> None:
    results = pm.evaluate_thresholds(_collectors(latency_ms=1000.0), _thresholds())
    assert results["overall_status"] == "warn"
    assert results["monitoring_ready"] is False  # 采集完整但存在 warn → false
    assert results["partial"] is False
    counts = results["counts"]
    assert counts["warn"] == 5  # 五端点延迟全触 warn
    assert len(results["alerts"]) == counts["warn"] + counts["critical"]
    assert sum(counts.values()) == len(results["checks"])


def test_evaluate_respects_only_subset_endpoints() -> None:
    collectors = _collectors(http_status=500)
    results = pm.evaluate_thresholds(collectors, _thresholds(),
                                     endpoints=[pm.ENDPOINTS[2]])
    status_checks = [c for c in results["checks"] if c["check_id"] == "endpoint-status"]
    assert len(status_checks) == 1 and status_checks[0]["subject"] == "api-health"
    assert sum(results["counts"].values()) == len(results["checks"])  # 子集计数仍自洽


# ---------------------------------------------------------------- 报告 / 脱敏 / 原子写


def _execute_report() -> dict[str, object]:
    config = pm.build_config(
        project=PROJECT, profile=pm.DEFAULT_PROFILE, compose_file=Path(COMPOSE),
        endpoints=list(pm.ENDPOINTS), log_tail=200,
        request_timeout_seconds=5.0, thresholds=_thresholds(),
    )
    results = pm.evaluate_thresholds(_collectors(), _thresholds())
    return pm.build_report(mode="execute", started_utc="2026-09-11T00:00:00Z",
                           ended_utc="2026-09-11T00:01:00Z", config=config,
                           collectors=_collectors(), threshold_results=results,
                           proxy_env_keys_present=False)


def test_execute_report_schema_keys() -> None:
    report = _execute_report()
    for key in ("schema_version", "tool", "milestone", "mode", "started_at_utc",
                "ended_at_utc", "config", "boundaries", "collectors", "partial",
                "threshold_results", "overall_status", "monitoring_ready"):
        assert key in report, f"缺报告键: {key}"
    assert report["schema_version"] == pm.REPORT_SCHEMA_VERSION
    assert report["milestone"] == "M14-12"
    for key in ("started_at_utc", "ended_at_utc"):
        value = report[key]
        assert isinstance(value, str) and value.endswith("Z") and "T" in value
    assert report["overall_status"] == "ok" and report["monitoring_ready"] is True


def test_report_boundaries_disclaim_readiness_and_alerting() -> None:
    report = _execute_report()
    joined = "\n".join(report["boundaries"])
    assert "never claims production ready" in joined
    assert "no external alerting" in joined
    assert "raw log lines never persisted" in joined
    assert "never treated as healthy" in joined


def test_report_markdown_contains_checks_and_status() -> None:
    markdown = pm.render_markdown(_execute_report())
    assert "mode=execute" in markdown
    assert "overall_status=**ok**" in markdown
    assert "| compose-service | postgres | ok |" in markdown
    assert "| endpoint-status | api-health | ok | http_status=200 |" in markdown
    plan_md = pm.render_markdown(_plan_report())
    assert "mode=plan" in plan_md and "overall_status" not in plan_md


def test_redaction_layer_strips_credential_shapes(tmp_path) -> None:
    poisoned = _execute_report()
    config = poisoned["config"]
    assert isinstance(config, dict)
    config["project"] = "leak " + MARK_TOKEN  # type: ignore[assignment]
    json_path, md_path = pm.write_reports_atomic(poisoned, tmp_path, "poison")
    for path in (json_path, md_path):
        text = path.read_text(encoding="utf-8")
        assert MARK_TOKEN not in text
        assert "[REDACTED:token]" in text
    assert pm.redact_secrets("password=ZXpassmarker12345678") == "[REDACTED:credential]"


def test_write_reports_atomic_no_tmp_leftover_and_overwrite(tmp_path) -> None:
    report = _execute_report()
    json_path, md_path = pm.write_reports_atomic(report, tmp_path, "monitor-x")
    assert json_path.name == "monitor-x.json" and md_path.name == "monitor-x.md"
    assert json.loads(json_path.read_text(encoding="utf-8"))["mode"] == "execute"
    assert not list(tmp_path.glob("*.tmp"))  # 原子写：无 tmp 残留
    # 同 stem 覆盖：替换干净、内容更新
    report["mode"] = "plan"
    pm.write_reports_atomic(report, tmp_path, "monitor-x")
    assert json.loads(json_path.read_text(encoding="utf-8"))["mode"] == "plan"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("stem", ["../evil", "a/b", "a\\b", ".hidden", "", "x y"])
def test_write_reports_rejects_out_of_bounds_stem(tmp_path, stem: str) -> None:
    with pytest.raises(pm.ReportPathError):
        pm.write_reports_atomic(_execute_report(), tmp_path, stem)
    assert list(tmp_path.glob("*.json")) == []  # 拒绝发生在零写入


def _maybe_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")


def test_write_reports_rejects_symlink_final_path(tmp_path) -> None:
    target = tmp_path / "real.json"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "linked.json"  # stem "linked" 渲染出的最终 JSON 路径
    _maybe_symlink(link, target)
    with pytest.raises(pm.ReportPathError):
        pm.write_reports_atomic(_execute_report(), tmp_path, "linked")
    assert not list(tmp_path.glob("*.tmp"))
    assert target.read_text(encoding="utf-8") == "x"  # symlink 目标零改写


def test_write_reports_rejects_symlinked_directory(tmp_path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "linked-dir"
    _maybe_symlink(link_dir, real_dir)
    with pytest.raises(pm.ReportPathError):
        pm.write_reports_atomic(_execute_report(), link_dir, "monitor-x")


def test_write_reports_symlink_refusal_via_monkeypatch(monkeypatch, tmp_path) -> None:
    """跨平台确定性锁定：任何 symlink 判真即拒绝（零写入）。"""
    monkeypatch.setattr(pm.Path, "is_symlink", lambda self: True)
    with pytest.raises(pm.ReportPathError):
        pm.write_reports_atomic(_execute_report(), tmp_path, "monitor-x")
    assert list(tmp_path.glob("**/*")) == []


def test_proxy_env_note_records_presence_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HTTP_PROXY", MARK_ENV_PROXY)
    rc = pm.main(["--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_OK
    report_text = next(tmp_path.glob("plan-*.json")).read_text(encoding="utf-8")
    assert "proxy_env_keys_present=True" in report_text
    assert MARK_ENV_PROXY not in report_text  # env 值绝不入档


def test_proxy_env_empty_value_key_still_present(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HTTP_PROXY", "")
    rc = pm.main(["--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_OK
    report_text = next(tmp_path.glob("plan-*.json")).read_text(encoding="utf-8")
    assert "proxy_env_keys_present=True" in report_text  # membership 语义


# ---------------------------------------------------------------- main execute 出口（全 fake，零生产流量）


def test_main_execute_happy_path_exit_ok(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    _block_subprocess(monkeypatch)
    runner = FakeRunner()
    transport = FakeTransport(status=200, elapsed_ms=11.0)
    _patch_gate(monkeypatch, runner, transport)
    rc = pm.main(["--execute", "--confirm", pm.CONFIRM_PHRASE,
                  "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_OK
    report = json.loads(next(tmp_path.glob("monitor-*.json")).read_text(encoding="utf-8"))
    assert report["mode"] == "execute"
    assert report["overall_status"] == "ok"
    assert report["monitoring_ready"] is True
    assert report["partial"] is False
    # 采集面确证：compose ps 1 次 + inspect 6 + logs 6；端点 GET 5 次
    assert sum(1 for c in runner.calls if c[1] == "compose") == 1
    assert sum(1 for c in runner.calls if c[1] == "inspect") == 6
    assert sum(1 for c in runner.calls if c[1] == "logs") == 6
    assert len(transport.calls) == 5


def test_main_execute_warn_visible_exit_zero(monkeypatch, tmp_path, capsys) -> None:
    """warn 不得隐藏：退出 0 但 alert 恒可见（stdout + 报告）。"""
    _block_sockets(monkeypatch)
    _patch_gate(monkeypatch, FakeRunner(), FakeTransport(status=200, elapsed_ms=1000.0))
    rc = pm.main(["--execute", "--confirm", pm.CONFIRM_PHRASE,
                  "--latency-warn-ms", "1000", "--latency-critical-ms", "5000",
                  "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_OK
    out = capsys.readouterr().out
    assert "severity=warn" in out
    assert "monitoring_ready=False" in out
    report = json.loads(next(tmp_path.glob("monitor-*.json")).read_text(encoding="utf-8"))
    assert report["overall_status"] == "warn"
    assert report["monitoring_ready"] is False
    assert len(report["threshold_results"]["alerts"]) == 5


def test_main_execute_incomplete_exit_two(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    _patch_gate(monkeypatch, FakeRunner(inspect_facts={"api": None}), FakeTransport())
    rc = pm.main(["--execute", "--confirm", pm.CONFIRM_PHRASE, "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_INCOMPLETE
    report = json.loads(next(tmp_path.glob("monitor-*.json")).read_text(encoding="utf-8"))
    assert report["overall_status"] == "incomplete"
    assert report["partial"] is True


def test_main_execute_critical_exit_two(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    unhealthy = {
        svc: f"running\t{('unhealthy' if svc == 'api' else 'healthy')}\t0\taios/{svc}:tag\t2026"
        for svc in pm.STACK_SERVICES
    }
    _patch_gate(monkeypatch, FakeRunner(inspect_facts=unhealthy), FakeTransport())
    rc = pm.main(["--execute", "--confirm", pm.CONFIRM_PHRASE, "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_CRITICAL
    report = json.loads(next(tmp_path.glob("monitor-*.json")).read_text(encoding="utf-8"))
    assert report["overall_status"] == "critical"


def test_main_execute_compose_ps_failure_exit_two(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)

    class PsBrokenRunner(FakeRunner):
        def run(self, argv, *, timeout: float = 60.0, encoding: str | None = None):
            result = super().run(argv, timeout=timeout, encoding=encoding)
            if argv[1] == "compose":
                return pm.CommandResult(result.argv, 1, "", "compose failed")
            return result

    _patch_gate(monkeypatch, PsBrokenRunner(), FakeTransport())
    rc = pm.main(["--execute", "--confirm", pm.CONFIRM_PHRASE, "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_USAGE  # 采集 incomplete → 2
    report = json.loads(next(tmp_path.glob("monitor-*.json")).read_text(encoding="utf-8"))
    assert report["collectors"]["compose_ps"]["status"] == "failed"
    assert report["overall_status"] == "incomplete"


def test_main_execute_endpoint_unreachable_exit_two(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    _patch_gate(monkeypatch, FakeRunner(), FakeTransport(status=None))
    rc = pm.main(["--execute", "--confirm", pm.CONFIRM_PHRASE, "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_USAGE
    report = json.loads(next(tmp_path.glob("monitor-*.json")).read_text(encoding="utf-8"))
    assert report["collectors"]["endpoints"]["per_endpoint"]["web-root"]["failure_category"] == "connection-refused"


def test_main_execute_log_marker_never_persisted(monkeypatch, tmp_path) -> None:
    """端到端脱敏：容器日志原文含标记值 → 报告只留计数，标记绝不入档。"""
    _block_sockets(monkeypatch)
    poisoned_logs = {
        svc: [f"ERROR leak {MARK_TOKEN}", "error again", "INFO fine"]
        for svc in pm.STACK_SERVICES
    }
    _patch_gate(monkeypatch, FakeRunner(logs_lines=poisoned_logs), FakeTransport())
    rc = pm.main(["--execute", "--confirm", pm.CONFIRM_PHRASE,
                  "--log-error-warn", "5", "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_OK  # error_total=2/容器 < warn 5 → ok
    for artifact in tmp_path.glob("monitor-*"):
        assert MARK_TOKEN not in artifact.read_text(encoding="utf-8")
    report = json.loads(next(tmp_path.glob("monitor-*.json")).read_text(encoding="utf-8"))
    assert report["collectors"]["logs"]["per_service"]["api"]["error_total"] == 2


def test_main_execute_report_write_failure_exit_two(monkeypatch, tmp_path) -> None:
    """证据不可失：报告写入失败（artifact-dir 指向文件）→ 拒绝口径退出 2。"""
    _block_sockets(monkeypatch)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    _patch_gate(monkeypatch, FakeRunner(), FakeTransport())
    rc = pm.main(["--execute", "--confirm", pm.CONFIRM_PHRASE,
                  "--artifact-dir", str(blocker)])
    assert rc == pm.EXIT_USAGE


# ---------------------------------------------------------------- CLI 注册


def test_cli_registration_defaults_and_flags() -> None:
    parser = pm.build_parser()
    assert parser.prog == "production_monitor.py"
    args = parser.parse_args([])
    assert args.execute is False            # 默认 plan（零采集）
    assert args.confirm == ""
    assert args.project == pm.DEFAULT_PROJECT
    assert args.artifact_dir == pm.ARTIFACT_DIR
    assert args.log_tail == pm.DEFAULT_LOG_TAIL
    assert args.request_timeout_seconds == pm.DEFAULT_REQUEST_TIMEOUT_SECONDS
    executed = parser.parse_args(["--execute", "--confirm", pm.CONFIRM_PHRASE])
    assert executed.execute is True and executed.confirm == pm.CONFIRM_PHRASE


def test_ops_readme_documents_tool() -> None:
    text = OPS_README.read_text(encoding="utf-8")
    assert "production_monitor.py" in text
    assert pm.CONFIRM_PHRASE in text


# ---------------------------------------------------------------- R1 修正（supervisor 评审，2026-09-12）


def test_validate_thresholds_rejects_non_finite_floats() -> None:
    """R1 修正 1：request timeout / latency 阈值的 nan/inf/-inf 显式拒绝。"""
    base: dict[str, object] = {"restart_warn": 1, "restart_critical": 5,
                               "log_error_warn": 5, "log_error_critical": 20, "log_tail": 200}
    for overrides in (
        {"request_timeout": float("nan")},
        {"request_timeout": float("inf")},
        {"request_timeout": float("-inf")},
        {"latency_warn": float("nan")},
        {"latency_warn": float("inf")},
        {"latency_critical": float("-inf")},
    ):
        values = {"request_timeout": 5.0, "latency_warn": 1000.0, "latency_critical": 5000.0}
        values.update(overrides)
        problems = pm.validate_thresholds(**values, **base)  # type: ignore[arg-type]
        assert any("有限数值" in problem for problem in problems), overrides
    problems = pm.validate_thresholds(request_timeout=5.0, latency_warn=1000.0,
                                      latency_critical=5000.0, **base)  # type: ignore[arg-type]
    assert not any("有限数值" in problem for problem in problems)  # 有限值不触发


@pytest.mark.parametrize("args", [
    ["--request-timeout-seconds", "nan"],
    ["--request-timeout-seconds", "inf"],
    # 注："-inf" 单独成 token 会被 argparse 当作旗标（SystemExit 2，仍零
    # 副作用）；受控拒绝路径经 "=" 形式覆盖
    ["--request-timeout-seconds=-inf"],
    ["--latency-warn-ms", "nan"],
    ["--latency-warn-ms", "inf"],
    ["--latency-critical-ms", "nan"],
    ["--latency-critical-ms=-inf"],
])
def test_nonfinite_floats_fail_closed_in_plan_zero_artifacts(monkeypatch, tmp_path, args: list[str]) -> None:
    """R1 修正 1：plan 报告写入之前即拒绝——零 plan 产物。"""
    _block_sockets(monkeypatch)
    rc = pm.main([*args, "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_USAGE
    assert list(tmp_path.iterdir()) == []


def test_nonfinite_floats_fail_closed_in_fully_confirmed_execute(monkeypatch, tmp_path) -> None:
    """R1 修正 1：旗标+精确短语齐备但阈值非有限 → 仍零采集、零执行报告。"""
    _block_sockets(monkeypatch)
    runner, transport = FakeRunner(), FakeTransport()
    constructions = _patch_gate(monkeypatch, runner, transport)
    rc = pm.main(["--execute", "--confirm", pm.CONFIRM_PHRASE,
                  "--latency-warn-ms", "nan", "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_USAGE
    assert runner.calls == [] and constructions["runner"] == 0  # 零采集
    assert not list(tmp_path.glob("monitor-*"))


@pytest.mark.parametrize("name", [pm.DEFAULT_PROJECT, "my-stack_2", "a", "A9-_x", "x" * 64])
def test_validate_project_name_accepts_strict_names(name: str) -> None:
    """R1 修正 2：ASCII 字母数字开头 + 字母数字/连字符/下划线 + ≤64（恰 64 合法）。"""
    assert pm.validate_project_name(name) is None


@pytest.mark.parametrize("name", [
    "",                                  # 空
    "proj\nname",                        # 换行
    "../evil",                           # 路径穿越
    "a/b", "a\\b",                       # 路径分隔
    "x" * 65,                            # 超长
    "生产栈",                            # 非 ASCII
    " lead", "a b",                      # 空白
    "a`b", "a|b", "a#b", "a>b",          # markdown/控制面字符
    "\x00x", "a\x00b",                   # 控制字符
])
def test_validate_project_name_rejects_bad_names(name: str) -> None:
    assert pm.validate_project_name(name) is not None


def test_validate_project_name_reasons_are_fixed_vocabulary() -> None:
    """R1 修正 2：拒绝原因为固定词汇（绝不回显被拒值）。"""
    assert pm.validate_project_name("") == "empty"
    assert pm.validate_project_name("x" * 65) == "too-long"
    assert pm.validate_project_name("../evil") == "first-char-not-alnum"
    assert pm.validate_project_name("a/b") == "invalid-character"
    assert pm.validate_project_name("生产栈") == "first-char-not-alnum"


def test_plan_accepts_alternate_valid_project(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    rc = pm.main(["--project", "my-stack_2", "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_OK
    report = json.loads(next(tmp_path.glob("plan-*.json")).read_text(encoding="utf-8"))
    assert report["config"]["project"] == "my-stack_2"


@pytest.mark.parametrize("bad", ["", "proj\nname", "../evil", "x" * 65, "生产栈"])
def test_invalid_project_fail_closed_in_plan_no_echo(monkeypatch, tmp_path, capsys, bad: str) -> None:
    """R1 修正 2：plan 报告写入之前即拒绝；被拒值绝不回显到日志。"""
    _block_sockets(monkeypatch)
    rc = pm.main(["--project", bad, "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_USAGE
    assert list(tmp_path.iterdir()) == []
    out = capsys.readouterr().out
    if bad:
        assert bad not in out  # 被拒值零回显（空串为任意串子串，跳过）


def test_invalid_project_fail_closed_in_fully_confirmed_execute(monkeypatch, tmp_path) -> None:
    """R1 修正 2：旗标+精确短语齐备但项目名非法 → 仍零采集、零执行报告。"""
    _block_sockets(monkeypatch)
    runner, transport = FakeRunner(), FakeTransport()
    constructions = _patch_gate(monkeypatch, runner, transport)
    rc = pm.main(["--execute", "--confirm", pm.CONFIRM_PHRASE,
                  "--project", "../evil", "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_USAGE
    assert runner.calls == [] and constructions["runner"] == 0
    assert not list(tmp_path.glob("monitor-*"))


def test_report_boundaries_artifact_dir_claim_corrected() -> None:
    """R1 修正 3：不再宣称「报告恒落 gitignored 目录」——默认目录 gitignored，
    --artifact-dir 自定义路径为操作者显式自选。"""
    joined = "\n".join(pm.REPORT_BOUNDARIES)
    assert "default artifact directory" in joined
    assert "explicit operator selection" in joined
    assert "operator's responsibility" in joined
    assert "reports land in a gitignored directory and never enter the repository" not in joined


def test_artifact_dir_help_and_runtime_notes(monkeypatch, tmp_path, capsys) -> None:
    """R1 修正 3：CLI help 与运行时注记区分默认目录（gitignored）与自定义目录。"""
    help_text = pm.build_parser().format_help()
    assert "操作者显式自选" in help_text
    monkeypatch.setattr(pm, "ARTIFACT_DIR", tmp_path / "default-artifacts")
    rc = pm.main([])  # 默认目录（ARTIFACT_DIR 已被指到 tmp 下）
    assert rc == pm.EXIT_OK
    assert "gitignored" in capsys.readouterr().out
    rc = pm.main(["--artifact-dir", str(tmp_path / "custom")])
    assert rc == pm.EXIT_OK
    out = capsys.readouterr().out
    assert "操作者显式自选" in out
    assert "gitignored" not in out  # 自定义路径不对其 gitignore 状态作任何宣称


def test_r1_docs_commit_wording_sweep() -> None:
    """R1 修正 4：状态文档不再含绝对化「本回合不 push/不开 PR/不合并」承诺；
    统一为「supervisor 审查与 remote 发布（push/PR/合并）在其后进行」。
    匹配前做空白归一化——中文长条目折行不构成措辞差异。"""
    docs = {
        "evidence": REPO_ROOT / "docs/evidence/m14-12-production-monitoring/README.md",
        "status": REPO_ROOT / "docs/PROJECT_STATUS.md",
        "changelog": REPO_ROOT / "docs/CHANGELOG.md",
        "roadmap": REPO_ROOT / "docs/ROADMAP.md",
    }

    def flatten(text: str) -> str:
        return "".join(text.split())

    for name, path in docs.items():
        flat = flatten(path.read_text(encoding="utf-8"))
        assert "supervisor审查与remote发布" in flat, name
        assert "只本地commit，不push、不开PR、不合并" not in flat, name
        assert "不push/不开PR/不合并" not in flat, name
        assert "不推送、不开PR、不合并" not in flat, name


# ---------------------------------------------------------------- 真实 transport（本机假服务器）


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


def test_real_transport_get_only_minimal_headers(fake_http_server) -> None:
    port = fake_http_server.server_address[1]
    result = pm.RealTransport().get("127.0.0.1", port, "/login", timeout=3.0)
    assert result.status == 200 and result.error_category is None
    command, path, headers = fake_http_server.requests_seen[0]
    assert command == "GET" and path == "/login"
    assert "Cookie" not in headers and "Authorization" not in headers
    assert headers.get("User-Agent") == pm.USER_AGENT


def test_real_transport_ignores_proxy_env(fake_http_server) -> None:
    """恶意假代理计数零连接——http.client 直连的结构性代理旁路实证。"""
    import os

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
        for key in pm.PROXY_ENV_KEYS:
            old_env[key] = os.environ.pop(key, None)
            os.environ[key] = f"http://127.0.0.1:{evil_port}"
        port = fake_http_server.server_address[1]
        result = pm.RealTransport().get("127.0.0.1", port, "/", timeout=3.0)
    finally:
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
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    try:
        result = pm.RealTransport().get("127.0.0.1", port, "/", timeout=2.0)
    finally:
        probe.close()
    assert result.status is None
    assert result.error_category in {"connection-refused", "connection-error", "timeout"}
    assert result.error_class in {"ConnectionRefusedError", "ConnectionError", "OSError", "TimeoutError"}
    assert result.elapsed_ms <= 2500.0  # 单请求超时上界被尊重


# ---------------------------------------------------------------- M14-23 restart 增量语义


def _baseline(counts: int | dict[str, int] = 0, *, status: str = "ok",
              started: str | dict[str, str] = "2026-09-11T00:00:00Z",
              source_stem: str = "monitor-20260911-000000",
              reason: str | None = None,
              invalid_skipped: int = 0) -> pm.RestartBaseline:
    """构造 RestartBaseline（counts/started 支持标量铺六服务或映射）。"""
    if isinstance(counts, int):
        counts = {svc: counts for svc in pm.STACK_SERVICES}
    if isinstance(started, str):
        started = {svc: started for svc in pm.STACK_SERVICES}
    return pm.RestartBaseline(
        status=status, reason=reason, source_stem=source_stem,
        collected_at="2026-09-11T00:00:00Z", restart_counts=dict(counts),
        started_ats=dict(started), invalid_skipped_count=invalid_skipped,
    )


def _prior_artifact_json(*, restarts: int | dict[str, int] = 0,
                         started: str = "2026-09-11T00:00:00Z",
                         collected: str = "2026-09-10T00:00:00Z") -> str:
    """旧 v1 monitor 工件形状（**无** restart_evaluation——基线兼容面）；
    身份四件套（schema/tool/milestone/mode）+ canonical started_at_utc。"""
    per_restart = (restarts if isinstance(restarts, dict)
                   else {svc: restarts for svc in pm.STACK_SERVICES})
    per_service = {
        svc: {"status": "ok", "restart_count": per_restart.get(svc, 0), "started_at": started}
        for svc in pm.STACK_SERVICES
    }
    return json.dumps({
        "schema_version": pm.REPORT_SCHEMA_VERSION, "tool": pm.TOOL_NAME,
        "milestone": pm.MILESTONE, "mode": "execute", "partial": False,
        "started_at_utc": collected, "ended_at_utc": collected,
        "collectors": {"containers": {"status": "ok", "per_service": per_service}},
    })


def _restart_checks(results: dict[str, object]) -> list[dict[str, str]]:
    return [c for c in results["checks"] if c["check_id"] == "container-restarts"]  # type: ignore[index]


# ---------------------------------------------------------------- 基线解析（纯本地只读 fail-safe）


def test_baseline_missing_when_no_prior_artifacts(tmp_path) -> None:
    empty = tmp_path / "artifacts"
    empty.mkdir()
    baseline = pm.resolve_restart_baseline(empty)
    assert baseline.status == "missing"
    assert baseline.reason == "no-prior-artifacts"
    assert baseline.source_stem is None and baseline.restart_counts == {}
    assert baseline.invalid_skipped_count == 0


def test_baseline_missing_when_artifact_dir_absent_or_unreadable(tmp_path) -> None:
    baseline = pm.resolve_restart_baseline(tmp_path / "no-such-dir")
    assert baseline.status == "missing"
    assert baseline.reason == "artifact-dir-missing"
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    baseline = pm.resolve_restart_baseline(blocker)
    assert baseline.status == "missing"
    assert baseline.reason == "artifact-dir-unreadable"  # fail-safe，绝不崩溃


def test_baseline_picks_latest_legal_prior_complete_artifact(tmp_path) -> None:
    (tmp_path / "monitor-20260910-000000.json").write_text(
        _prior_artifact_json(restarts=1), encoding="utf-8")
    (tmp_path / "monitor-20260911-010000.json").write_text(
        _prior_artifact_json(restarts=2, started="2026-09-11T08:00:00Z"),
        encoding="utf-8")
    baseline = pm.resolve_restart_baseline(tmp_path)
    assert baseline.status == "ok"
    assert baseline.source_stem == "monitor-20260911-010000"  # 最新合法者
    assert baseline.collected_at == "2026-09-10T00:00:00Z"
    assert baseline.restart_counts == {svc: 2 for svc in pm.STACK_SERVICES}
    assert baseline.started_ats["api"] == "2026-09-11T08:00:00Z"
    assert baseline.invalid_skipped_count == 0
    # 旧 v1 工件（无 restart_evaluation）合法充当基线——向后兼容


def test_baseline_skips_invalid_latest_and_counts_explicitly(tmp_path) -> None:
    (tmp_path / "monitor-20260910-000000.json").write_text(
        _prior_artifact_json(restarts=1), encoding="utf-8")
    (tmp_path / "monitor-20260911-010000.json").write_text("not json", encoding="utf-8")
    baseline = pm.resolve_restart_baseline(tmp_path)
    assert baseline.status == "ok"  # 回退到更早合法件
    assert baseline.source_stem == "monitor-20260910-000000"
    assert baseline.invalid_skipped_count == 1  # 非法件显式计数，绝不静默当零基线


def test_baseline_unusable_when_all_prior_artifacts_invalid(tmp_path) -> None:
    (tmp_path / "monitor-20260910-000000.json").write_text("not json", encoding="utf-8")
    (tmp_path / "monitor-20260911-010000.json").write_text("[]", encoding="utf-8")
    baseline = pm.resolve_restart_baseline(tmp_path)
    assert baseline.status == "unusable"
    assert baseline.reason == "no-usable-prior-artifacts"
    assert baseline.invalid_skipped_count == 2
    assert baseline.source_stem is None and baseline.restart_counts == {}


def test_baseline_requires_complete_execute_monitor_identity(tmp_path) -> None:
    # partial=true（采集不完整时期工件）与 mode=plan 均不可作基线
    partial = json.loads(_prior_artifact_json(restarts=3))
    partial["partial"] = True
    (tmp_path / "monitor-20260910-000000.json").write_text(
        json.dumps(partial), encoding="utf-8")
    plan_mode = json.loads(_prior_artifact_json(restarts=3))
    plan_mode["mode"] = "plan"
    (tmp_path / "monitor-20260911-010000.json").write_text(
        json.dumps(plan_mode), encoding="utf-8")
    baseline = pm.resolve_restart_baseline(tmp_path)
    assert baseline.status == "unusable"
    assert baseline.invalid_skipped_count == 2


@pytest.mark.parametrize("partial_value", [True, "false", 0],
                         ids=["partial-true", "partial-string", "partial-int"])
def test_baseline_rejects_non_boolean_partial_forms(tmp_path, partial_value) -> None:
    """supervisor 评审：基线必须来自**真正完整**的 execute monitor 工件——
    partial 恒须为布尔 False；True（采集不完整）、字符串/整数等畸形形态、
    以及**缺失** partial 键的工件一律不可作基线（显式计入 invalid，绝不
    静默放行）。"""
    malformed = json.loads(_prior_artifact_json(restarts=3))
    malformed["partial"] = partial_value
    (tmp_path / "monitor-20260910-000000.json").write_text(
        json.dumps(malformed), encoding="utf-8")
    missing_partial = json.loads(_prior_artifact_json(restarts=3))
    missing_partial.pop("partial")
    (tmp_path / "monitor-20260911-010000.json").write_text(
        json.dumps(missing_partial), encoding="utf-8")
    baseline = pm.resolve_restart_baseline(tmp_path)
    assert baseline.status == "unusable"
    assert baseline.reason == "no-usable-prior-artifacts"
    assert baseline.invalid_skipped_count == 2


def test_baseline_requires_milestone_identity(tmp_path) -> None:
    """supervisor R1：基线身份加 milestone 锚（与本工具 MILESTONE 常量一致）
    ——不符即不可作基线（显式计入 invalid）。"""
    artifact = json.loads(_prior_artifact_json(restarts=1))
    artifact["milestone"] = "M14-99"
    (tmp_path / "monitor-20260910-000000.json").write_text(
        json.dumps(artifact), encoding="utf-8")
    baseline = pm.resolve_restart_baseline(tmp_path)
    assert baseline.status == "unusable"
    assert baseline.invalid_skipped_count == 1


@pytest.mark.parametrize("collected", [
    "2026-09-10 00:00:00",          # 缺 T/Z
    "2026-09-10T00:00:00",          # 缺 Z
    "2026-13-10T00:00:00Z",         # 日历非法
    "2026-09-10T00:00:00.123Z",     # 非规范亚秒形态
    " 2026-09-10T00:00:00Z",        # 前导空白
    12345,                          # 非字符串
], ids=["no-tz-sep", "no-z", "bad-calendar", "subsecond", "leading-space", "not-str"])
def test_baseline_rejects_noncanonical_started_at_utc(tmp_path, collected) -> None:
    """supervisor R1：started_at_utc 必须为 canonical 形态（%Y-%m-%dT%H:%M:%SZ
    且日历合法）——畸形值绝不复制进 baseline.collected_at（整件不可作基线）。"""
    artifact = json.loads(_prior_artifact_json(restarts=1))
    artifact["started_at_utc"] = collected
    (tmp_path / "monitor-20260910-000000.json").write_text(
        json.dumps(artifact), encoding="utf-8")
    baseline = pm.resolve_restart_baseline(tmp_path)
    assert baseline.status == "unusable"
    assert baseline.invalid_skipped_count == 1
    assert baseline.collected_at is None  # 畸形值绝不外泄


def test_baseline_missing_started_at_utc_rejected(tmp_path) -> None:
    artifact = json.loads(_prior_artifact_json(restarts=1))
    artifact.pop("started_at_utc")
    (tmp_path / "monitor-20260910-000000.json").write_text(
        json.dumps(artifact), encoding="utf-8")
    baseline = pm.resolve_restart_baseline(tmp_path)
    assert baseline.status == "unusable"
    assert baseline.invalid_skipped_count == 1


def test_baseline_symlinked_artifact_dir_refused_real_fs(tmp_path) -> None:
    """supervisor R1 symlink 防御：symlinked 工件目录绝不跟随——固定词汇
    missing/artifact-dir-unreadable，无路径回显。"""
    real = tmp_path / "real"
    real.mkdir()
    (real / "monitor-20260910-000000.json").write_text(
        _prior_artifact_json(restarts=1), encoding="utf-8")
    link = tmp_path / "linked"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 不可用（权限/平台）")
    baseline = pm.resolve_restart_baseline(link)
    assert baseline.status == "missing"
    assert baseline.reason == "artifact-dir-unreadable"
    assert baseline.restart_counts == {}


def test_baseline_symlink_defense_via_monkeypatch(monkeypatch, tmp_path) -> None:
    """跨平台确定性锁定（supervisor R1）：目录 symlink 判真即零读取；候选
    文件 symlink 计 invalid_skipped 而不跟随（回退更早合法件）。"""
    (tmp_path / "monitor-20260910-000000.json").write_text(
        _prior_artifact_json(restarts=1), encoding="utf-8")
    (tmp_path / "monitor-20260911-010000.json").write_text(
        _prior_artifact_json(restarts=2), encoding="utf-8")

    def _fake_is_symlink(self: Path) -> bool:
        return self.name in {"monitor-20260911-010000.json", "symlinked-dir"}

    monkeypatch.setattr(pm.Path, "is_symlink", _fake_is_symlink)
    # 候选 symlink：跳过最新件、回退旧件、显式计数
    baseline = pm.resolve_restart_baseline(tmp_path)
    assert baseline.status == "ok"
    assert baseline.source_stem == "monitor-20260910-000000"
    assert baseline.invalid_skipped_count == 1
    # 目录自身 symlink：固定词汇 unreadable、零候选读取
    baseline = pm.resolve_restart_baseline(tmp_path / "symlinked-dir")
    assert baseline.status == "missing"
    assert baseline.reason == "artifact-dir-unreadable"
    assert baseline.restart_counts == {}


def test_baseline_ignores_non_monitor_names_and_bad_stems(tmp_path) -> None:
    (tmp_path / "monitor-20260910-000000.json").write_text(
        _prior_artifact_json(restarts=1), encoding="utf-8")
    for ignored in ("plan-20260911-010000.json", "monitor-20260911-010000.md",
                    ".monitor-20260911-010000.json.tmp", "README.md"):
        (tmp_path / ignored).write_text("x", encoding="utf-8")
    (tmp_path / "monitor-evil.json").write_text("{}", encoding="utf-8")  # stem 不合规
    baseline = pm.resolve_restart_baseline(tmp_path)
    assert baseline.status == "ok"
    assert baseline.source_stem == "monitor-20260910-000000"
    assert baseline.invalid_skipped_count == 1  # 仅 stem 不合规的 monitor-*.json 计入


def test_baseline_resolution_zero_shell_zero_network(monkeypatch, tmp_path) -> None:
    _block_sockets(monkeypatch)
    _block_subprocess(monkeypatch)
    (tmp_path / "monitor-20260910-000000.json").write_text(
        _prior_artifact_json(restarts=1), encoding="utf-8")
    baseline = pm.resolve_restart_baseline(tmp_path)
    assert baseline.status == "ok"  # 纯 Path/json 本地读取


# ---------------------------------------------------------------- 增量阈值判定（纯函数）


@pytest.mark.parametrize("base,current,severity,delta", [
    (0, 0, "ok", 0),
    (2, 2, "ok", 0),        # 累计值不变（含历史存量）→ 当轮无新增 → ok
    (1, 2, "warn", 1),      # 增量 1 恰达 warn（含边界）
    (0, 4, "warn", 4),
    (0, 5, "critical", 5),  # 增量 5 恰达 critical（含边界）
    (0, 30, "critical", 30),
])
def test_evaluate_restart_delta_boundaries_inclusive(
        base: int, current: int, severity: str, delta: int) -> None:
    results = pm.evaluate_thresholds(
        _collectors(restarts=current), _thresholds(), baseline=_baseline(counts=base))
    assert all(check["severity"] == severity for check in _restart_checks(results))
    evaluation = results["restart_evaluation"]["per_service"]["api"]  # type: ignore[index]
    assert evaluation == {"state": severity, "reason": "delta" if delta else "stable",
                          "delta": delta}


def test_evaluate_unchanged_cumulative_count_recovers_ok() -> None:
    """M14-23 核心修复：api restart_count=1 静态累计 warn → 基线同为 1 的
    下一轮稳定调度恢复 ok（不再永久 warn）。"""
    results = pm.evaluate_thresholds(
        _collectors(restarts=1), _thresholds(), baseline=_baseline(counts=1))
    assert results["overall_status"] == "ok"
    assert results["monitoring_ready"] is True
    assert results["alerts"] == []
    evaluation = results["restart_evaluation"]["per_service"]["api"]  # type: ignore[index]
    assert evaluation == {"state": "ok", "reason": "stable", "delta": 0}


@pytest.mark.parametrize("count,severity", [
    (0, "ok"), (1, "warn"), (4, "warn"), (5, "critical"),
])
def test_evaluate_baseline_missing_one_time_visible_semantics(
        count: int, severity: str) -> None:
    """无基线：count 0 → ok；count 达 warn/critical → 一次性可见告警
    （baseline-missing 语义，下一轮以本轮工件为基线即恢复）。"""
    results = pm.evaluate_thresholds(_collectors(restarts=count), _thresholds(),
                                     baseline=_baseline(status="missing",
                                                        reason="no-prior-artifacts"))
    assert all(check["severity"] == severity for check in _restart_checks(results))
    evaluation = results["restart_evaluation"]["per_service"]["api"]  # type: ignore[index]
    assert evaluation["state"] == severity
    assert evaluation["reason"] == "baseline-missing"
    assert evaluation["delta"] is None


def test_evaluate_started_at_change_is_recreated_warn_then_recovers() -> None:
    """started_at 变化 = 容器重建：一轮可见 warn（绝不静默）；以重建后
    工件为基线的下一稳定轮恢复 ok。"""
    results = pm.evaluate_thresholds(
        _collectors(restarts=0, started_at="2026-09-12T00:00:00Z"), _thresholds(),
        baseline=_baseline(counts=0, started="2026-09-11T00:00:00Z"))
    assert all(check["severity"] == "warn" for check in _restart_checks(results))
    evaluation = results["restart_evaluation"]["per_service"]["api"]  # type: ignore[index]
    assert evaluation == {"state": "warn", "reason": "container-recreated", "delta": None}
    assert results["overall_status"] == "warn"
    # 下一稳定轮：基线 = 重建后事实（新 started_at、同 count）→ ok
    stable = pm.evaluate_thresholds(
        _collectors(restarts=0, started_at="2026-09-12T00:00:00Z"), _thresholds(),
        baseline=_baseline(counts=0, started="2026-09-12T00:00:00Z",
                           source_stem="monitor-20260912-000000"))
    assert all(check["severity"] == "ok" for check in _restart_checks(stable))


def test_evaluate_count_decrease_is_counter_reset_never_silent_zero() -> None:
    """同 started_at 而 count 下降 = 计数重置：一轮可见 warn；负增量绝不
    静默映射为零。"""
    results = pm.evaluate_thresholds(
        _collectors(restarts=1), _thresholds(), baseline=_baseline(counts=3))
    assert all(check["severity"] == "warn" for check in _restart_checks(results))
    evaluation = results["restart_evaluation"]["per_service"]["api"]  # type: ignore[index]
    assert evaluation == {"state": "warn", "reason": "counter-reset", "delta": None}


def test_evaluate_inspect_failure_marks_facts_missing_in_evaluation() -> None:
    results = pm.evaluate_thresholds(_collectors(inspect_failed=("api",)), _thresholds(),
                                     baseline=_baseline(counts=0))
    evaluation = results["restart_evaluation"]["per_service"]["api"]  # type: ignore[index]
    assert evaluation == {"state": "critical", "reason": "facts-missing", "delta": None}


def _m1423_report(restarts: int, baseline: pm.RestartBaseline,
                  collectors: dict[str, object]) -> dict[str, object]:
    results = pm.evaluate_thresholds(collectors, _thresholds(), baseline=baseline)
    return pm.build_report(mode="execute", started_utc="2026-09-11T00:00:00Z",
                           ended_utc="2026-09-11T00:01:00Z",
                           config=pm.build_config(
                               project=PROJECT, profile=pm.DEFAULT_PROFILE,
                               compose_file=Path(COMPOSE), endpoints=list(pm.ENDPOINTS),
                               log_tail=200, request_timeout_seconds=5.0,
                               thresholds=_thresholds()),
                           collectors=collectors, threshold_results=results,
                           proxy_env_keys_present=False)


def test_restart_evaluation_additive_schema_in_results_and_report() -> None:
    results = pm.evaluate_thresholds(_collectors(), _thresholds(),
                                     baseline=_baseline(counts=0))
    evaluation = results["restart_evaluation"]
    assert isinstance(evaluation, dict)
    assert set(evaluation["per_service"]) == set(pm.STACK_SERVICES)  # type: ignore[index]
    baseline_meta = evaluation["baseline"]  # type: ignore[index]
    for key in ("status", "reason", "source_stem", "collected_at",
                "invalid_skipped_count"):
        assert key in baseline_meta  # type: ignore[operator]
    report = _m1423_report(0, _baseline(counts=0), _collectors())
    assert report["threshold_results"]["restart_evaluation"] == evaluation  # 加法嵌入
    # plan 报告零状态面：不出现 restart_evaluation
    assert "restart_evaluation" not in json.dumps(_plan_report(), ensure_ascii=False)


def test_restart_evaluation_visible_in_markdown() -> None:
    report = _m1423_report(1, _baseline(counts=1), _collectors(restarts=1))
    markdown = pm.render_markdown(report)
    assert "restart" in markdown
    assert "baseline=monitor-20260911-000000" in markdown


# ---------------------------------------------------------------- main execute 出口（两轮恢复）


class _FakeClock:
    """可注入 stamp 序列（每轮 main 构造一次）。"""

    def __init__(self, stamps: list[str]) -> None:
        self._stamps = list(stamps)

    def utc_now_iso(self) -> str:
        return "2026-09-13T01:00:00Z"

    def stamp(self) -> str:
        return self._stamps.pop(0)


def test_main_execute_recovers_stale_cumulative_warn_e2e(monkeypatch, tmp_path) -> None:
    """e2e：工件目录已有 restart_count=1 的旧 v1 工件（真实生产 17 连 warn
    形态）→ 本轮采集同 count 同 started_at → overall ok 恢复，且生产读取
    面零新增（仍 1 compose + 6 inspect + 6 logs）。"""
    _block_sockets(monkeypatch)
    _block_subprocess(monkeypatch)
    (tmp_path / "monitor-20260912-120000.json").write_text(
        _prior_artifact_json(restarts={"api": 1}, started="2026-09-11T00:00:00Z",
                             collected="2026-09-12T12:00:00Z"), encoding="utf-8")
    runner = FakeRunner()  # 默认 inspect：restart 0 / started 2026-09-11T00:00:00Z
    api_one = dict(runner.inspect_facts)
    api_one["api"] = "running\thealthy\t1\taios/api:tag\t2026-09-11T00:00:00Z"
    runner.inspect_facts = api_one
    _patch_gate(monkeypatch, runner, FakeTransport())
    rc = pm.main(["--execute", "--confirm", pm.CONFIRM_PHRASE,
                  "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_OK
    current = max(tmp_path.glob("monitor-*.json"))  # 最新 stem = 本轮报告
    report = json.loads(current.read_text("utf-8"))
    assert report["overall_status"] == "ok"  # 不再因存量 1 永久 warn
    evaluation = report["threshold_results"]["restart_evaluation"]
    assert evaluation["baseline"] == {
        "status": "ok", "reason": None, "source_stem": "monitor-20260912-120000",
        "collected_at": "2026-09-12T12:00:00Z", "invalid_skipped_count": 0,
    }
    assert evaluation["per_service"]["api"] == {"state": "ok", "reason": "stable",
                                                "delta": 0}
    # 生产读取面零新增：基线解析纯本地文件，不追加任何 docker 命令
    assert sum(1 for c in runner.calls if c[1] == "compose") == 1
    assert sum(1 for c in runner.calls if c[1] == "inspect") == 6
    assert sum(1 for c in runner.calls if c[1] == "logs") == 6


def test_main_execute_two_rounds_first_warns_then_recovers(monkeypatch, tmp_path) -> None:
    """空工件目录 + count=1：首轮 baseline-missing 一次性 warn（exit 0 恒
    可见）；第二轮以首轮工件为基线、count 不变 → ok。"""
    _block_sockets(monkeypatch)
    _block_subprocess(monkeypatch)
    runner = FakeRunner()
    api_one = dict(runner.inspect_facts)
    api_one["api"] = "running\thealthy\t1\taios/api:tag\t2026-09-11T00:00:00Z"
    runner.inspect_facts = api_one
    _patch_gate(monkeypatch, runner, FakeTransport())
    stamps = ["20260913-010000", "20260913-011500"]
    shared_clock = _FakeClock(stamps)  # 两轮共享同一序列（每轮 main 构造一次）
    monkeypatch.setattr(pm, "RealClock", lambda: shared_clock)
    # 第一轮：无基线 → warn（可见、exit 0）
    rc = pm.main(["--execute", "--confirm", pm.CONFIRM_PHRASE,
                  "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_OK
    first = json.loads((tmp_path / "monitor-20260913-010000.json").read_text("utf-8"))
    assert first["overall_status"] == "warn"
    first_eval = first["threshold_results"]["restart_evaluation"]
    assert first_eval["baseline"]["status"] == "missing"
    assert first_eval["per_service"]["api"] == {"state": "warn",
                                                "reason": "baseline-missing",
                                                "delta": None}
    # 第二轮：首轮工件已成基线，count 不变 → ok
    rc = pm.main(["--execute", "--confirm", pm.CONFIRM_PHRASE,
                  "--artifact-dir", str(tmp_path)])
    assert rc == pm.EXIT_OK
    second = json.loads((tmp_path / "monitor-20260913-011500.json").read_text("utf-8"))
    assert second["overall_status"] == "ok"
    second_eval = second["threshold_results"]["restart_evaluation"]
    assert second_eval["baseline"]["status"] == "ok"
    assert second_eval["baseline"]["source_stem"] == "monitor-20260913-010000"
    assert second_eval["per_service"]["api"] == {"state": "ok", "reason": "stable",
                                                 "delta": 0}
