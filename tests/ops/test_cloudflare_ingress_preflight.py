"""Tests for tools.ops.cloudflare_ingress_preflight (M14-205 read-only CF preflight).

All Cloudflare API behavior is faked at the Transport injection point — zero
external network. The fake mirrors the real contract: it answers scripted
TransportResponse items in order, hard-asserts every call is a GET against the
path/query whitelist, and raises on any call beyond the script (which
structurally proves "single attempt, no hidden retries"). Output sanitization
is asserted by planting recognizable secret markers (token / zone id / record
content) and asserting they never appear in stdout JSON or the --output file.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Self

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.ops import cloudflare_ingress_preflight as cf

# 假域名/假标识均带可识别标记：断言"绝不出现"即脱敏证据（无真实数据）。
ZONE = "probe-zone.dev"
TOKEN = "cf-token-SECRET-marker-0001"
ZONE_ID = "zone-id-SECRET-marker-0002"
RECORD_CONTENT = "RECORD-CONTENT-SECRET-marker-0003"
ACCOUNT_ID = "account-id-SECRET-marker-0004"
CONFIRM = cf.CONFIRM_PHRASE

BASE_PAYLOAD: dict = {"schema_version": 1, "zone_name": ZONE, "api_token": TOKEN}


# ---------------------------------------------------------------- fakes / helpers


def write_config(
    tmp_path: Path, payload: dict | None = None, raw: str | None = None
) -> str:
    path = tmp_path / "cf-config.json"
    text = (
        raw
        if raw is not None
        else json.dumps(payload if payload is not None else BASE_PAYLOAD)
    )
    path.write_text(text, encoding="utf-8")
    return str(path)


def ok_verify(status: str = "active") -> dict:
    return {"status": 200, "body": {"success": True, "result": {"status": status}}}


def ok_zones(zone_ids: list[str] | None = None) -> dict:
    ids = zone_ids if zone_ids is not None else [ZONE_ID]
    return {
        "status": 200,
        "body": {
            "success": True,
            "result": [
                {"id": zid, "name": ZONE, "account": {"id": ACCOUNT_ID}} for zid in ids
            ],
        },
    }


def ok_ssl(value: str = "strict", status: int = 200) -> dict:
    body: dict = {"result": {"id": "ssl", "value": value}}
    if status != 200:
        body = {
            "success": False,
            "errors": [{"code": 9109, "message": "secret message body"}],
            "result": None,
        }
    return {"status": status, "body": body}


def ok_dns(names: tuple[str, ...] = ("www",), total: int | None = None) -> dict:
    records = [{"name": name, "type": "A", "content": RECORD_CONTENT} for name in names]
    return {
        "status": 200,
        "body": {
            "success": True,
            "result": records,
            "result_info": {"total_count": total if total is not None else len(names)},
        },
    }


def full_ok_script() -> list[dict]:
    return [ok_verify(), ok_zones(), ok_ssl(), ok_dns()]


class FakeTransport:
    """按脚本逐次应答；逐请求断言 GET + path/query 白名单；额外调用抛错。"""

    PATH_WHITELIST = (
        re.compile(r"^/user/tokens/verify$"),
        re.compile(r"^/zones$"),
        re.compile(r"^/zones/[^/]+/settings/ssl$"),
        re.compile(r"^/zones/[^/]+/dns_records$"),
    )

    def __init__(self, script: list[dict | Exception]) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, str, tuple[tuple[str, str], ...]]] = []

    def request(
        self, method: str, path: str, query: list[tuple[str, str]] | None = None
    ) -> cf.TransportResponse:
        if method != "GET":
            raise AssertionError(f"non-GET method leaked into transport: {method}")
        if not any(pattern.match(path) for pattern in self.PATH_WHITELIST):
            raise AssertionError(f"path outside GET whitelist: {path}")
        normalized = tuple(query or ())
        if path == "/zones":
            expected = (("name", ZONE),)
            if normalized != expected:
                raise AssertionError(
                    f"/zones query must be name=<zone_name>, got {normalized}"
                )
        elif path.endswith("/dns_records"):
            if normalized != (("per_page", "100"),):
                raise AssertionError(
                    f"dns_records query must be per_page=100, got {normalized}"
                )
        else:
            if normalized:
                raise AssertionError(f"query not allowed for {path}: {normalized}")
        if len(self.calls) >= len(self.script):
            raise AssertionError(
                "unexpected request beyond scripted sequence (retry leak?)"
            )
        self.calls.append((method, path, normalized))
        item = self.script[len(self.calls) - 1]
        if isinstance(item, Exception):
            raise item
        return cf.TransportResponse(**item)


def boom_factory():
    """plan/blocked 路径的哨兵工厂：被调用即证明违规构造了 Transport。"""
    raise AssertionError("transport must not be constructed in this mode")


def run(argv: list[str], factory=None) -> tuple[int, dict]:
    """直接调 main（不经子进程）：返回 (exit_code, stdout JSON 报告)。"""
    code = cf.main(argv, transport_factory=factory)
    return code


def last_report(capsys) -> dict:
    out = capsys.readouterr().out
    lines = [line for line in out.strip().splitlines() if line.strip()]
    assert lines, "expected a JSON report line on stdout"
    return json.loads(lines[-1])


def execute_argv(config_path: str, extra: list[str] | None = None) -> list[str]:
    return ["--config", config_path, "--execute", "--confirm", CONFIRM, *(extra or [])]


# ---------------------------------------------------------------- 名称校验（纯函数）


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("1.2.3.4", "IPv4 字面量"),
        ("::1", "IPv6 字面量"),
        ("example.com", "保留域"),
        ("sub.example.invalid", "保留域子孙域"),
        ("localhost", "单 label 保留"),
        ("probe-zone", "单 label 无 TLD"),
        ("under_score.probe-zone.dev", "下划线 label"),
        ("-leading.probe-zone.dev", "前导连字符"),
        ("https://probe-zone.dev", "scheme"),
        ("probe-zone.dev/path", "path"),
        ("probe-zone.dev?x=1", "query"),
        ("user@probe-zone.dev", "userinfo"),
        ("a" * 300 + ".dev", "超长"),
        ("", "空"),
        (None, "非字符串"),
        (12345, "数字"),
    ],
)
def test_validate_public_dns_name_rejects(value, why):
    with pytest.raises(cf.PreflightError):
        cf.validate_public_dns_name(value)


def test_validate_public_dns_name_accepts_and_normalizes():
    assert cf.validate_public_dns_name("Probe-Zone.DEV.") == "probe-zone.dev"


# ---------------------------------------------------------------- 配置 schema 与路径安全


def test_plan_ready_with_valid_config(tmp_path, capsys):
    path = write_config(tmp_path)
    assert run(["--config", path], factory=boom_factory) == 0
    report = last_report(capsys)
    assert report["status"] == "ready-to-execute"
    assert report["mode"] == "plan"
    assert report["config_present"] is True
    assert report["config_source"] == "cli"
    assert report["zone_name"] == ZONE
    assert report["zone_name_shape"] == "valid-public-dns"
    assert report["api_token_present"] is True


def test_plan_without_config_blocks(capsys):
    assert run([], factory=boom_factory) == 2
    report = last_report(capsys)
    assert report["status"] == "blocked"
    assert report["blocked_reasons"] == [cf.REASON_MISSING_CONFIG]
    assert report["config_present"] is False
    assert report["config_source"] is None


def test_plan_config_from_env(tmp_path, capsys, monkeypatch):
    path = write_config(tmp_path)
    monkeypatch.setenv(cf.CONFIG_ENV_VAR, path)
    assert run([], factory=boom_factory) == 0
    report = last_report(capsys)
    assert report["config_source"] == "env"
    assert report["status"] == "ready-to-execute"


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (
            {"zone_name": ZONE, "api_token": TOKEN},
            cf.REASON_SCHEMA_INVALID,
        ),  # 缺 schema_version
        (
            {"schema_version": 2, "zone_name": ZONE, "api_token": TOKEN},
            cf.REASON_SCHEMA_INVALID,
        ),
        (
            {"schema_version": 1, "api_token": TOKEN},
            cf.REASON_SCHEMA_INVALID,
        ),  # 缺 zone_name
        (
            {"schema_version": 1, "zone_name": ZONE},
            cf.REASON_SCHEMA_INVALID,
        ),  # 缺 api_token
        (
            {"schema_version": 1, "zone_name": ZONE, "api_token": ""},
            cf.REASON_SCHEMA_INVALID,
        ),
        (
            {"schema_version": 1, "zone_name": ZONE, "api_token": TOKEN, "extra": 1},
            cf.REASON_SCHEMA_INVALID,
        ),
        (
            {"schema_version": "1", "zone_name": ZONE, "api_token": TOKEN},
            cf.REASON_SCHEMA_INVALID,
        ),
        (
            {
                "schema_version": 1,
                "zone_name": ZONE,
                "api_token": TOKEN,
                "execute_timeout_s": 0.5,
            },
            cf.REASON_SCHEMA_INVALID,
        ),
        (
            {
                "schema_version": 1,
                "zone_name": ZONE,
                "api_token": TOKEN,
                "user_agent": "",
            },
            cf.REASON_SCHEMA_INVALID,
        ),
        (
            {"schema_version": 1, "zone_name": "1.2.3.4", "api_token": TOKEN},
            cf.REASON_ZONE_NAME_INVALID,
        ),
        (
            {"schema_version": 1, "zone_name": "example.com", "api_token": TOKEN},
            cf.REASON_ZONE_NAME_INVALID,
        ),
    ],
)
def test_plan_blocks_on_schema(tmp_path, capsys, payload, reason):
    path = write_config(tmp_path, payload=payload)
    assert run(["--config", path], factory=boom_factory) == 2
    report = last_report(capsys)
    assert report["status"] == "blocked"
    assert reason in report["blocked_reasons"]


def test_plan_accepts_optional_fields(tmp_path, capsys):
    payload = {**BASE_PAYLOAD, "execute_timeout_s": 15, "user_agent": "ops-check/1"}
    assert (
        run(["--config", write_config(tmp_path, payload=payload)], factory=boom_factory)
        == 0
    )
    assert last_report(capsys)["status"] == "ready-to-execute"


def test_plan_blocks_invalid_json(tmp_path, capsys):
    assert (
        run(["--config", write_config(tmp_path, raw="{not json")], factory=boom_factory)
        == 2
    )
    assert cf.REASON_INVALID_JSON in last_report(capsys)["blocked_reasons"]


def test_plan_blocks_duplicate_keys(tmp_path, capsys):
    raw = json.dumps(BASE_PAYLOAD)[:-1] + ', "zone_name": "other.dev"}'
    assert run(["--config", write_config(tmp_path, raw=raw)], factory=boom_factory) == 2
    assert cf.REASON_DUPLICATE_KEY in last_report(capsys)["blocked_reasons"]


def test_plan_blocks_oversized_config(tmp_path, capsys):
    payload = {**BASE_PAYLOAD, "padding": "x" * (cf.MAX_CONFIG_BYTES + 100)}
    assert (
        run(["--config", write_config(tmp_path, payload=payload)], factory=boom_factory)
        == 2
    )
    assert cf.REASON_TOO_LARGE in last_report(capsys)["blocked_reasons"]


def test_plan_blocks_directory_as_config(tmp_path, capsys):
    assert run(["--config", str(tmp_path)], factory=boom_factory) == 2
    assert cf.REASON_NOT_REGULAR_FILE in last_report(capsys)["blocked_reasons"]


def test_plan_blocks_symlink_config(tmp_path, capsys):
    real = tmp_path / "real.json"
    real.write_text(json.dumps(BASE_PAYLOAD), encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlink unavailable on this platform/privilege")
    assert run(["--config", str(link)], factory=boom_factory) == 2
    assert cf.REASON_NOT_REGULAR_FILE in last_report(capsys)["blocked_reasons"]


# ---------------------------------------------------------------- execute 确认门（零网络）


def test_execute_without_confirm_blocks_zero_network(tmp_path, capsys):
    path = write_config(tmp_path)
    for bad_confirm in (None, "wrong phrase", CONFIRM.lower()):
        argv = ["--config", path, "--execute"]
        if bad_confirm is not None:
            argv += ["--confirm", bad_confirm]
        assert run(argv, factory=boom_factory) == 2
        report = last_report(capsys)
        assert report["status"] == "blocked"
        assert cf.REASON_MISSING_CONFIRM in report["blocked_reasons"]


def test_execute_confirm_without_config_blocks(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(cf.CONFIG_ENV_VAR, raising=False)
    assert run(["--execute", "--confirm", CONFIRM], factory=boom_factory) == 2
    report = last_report(capsys)
    assert cf.REASON_MISSING_CONFIG in report["blocked_reasons"]
    assert cf.REASON_MISSING_CONFIRM not in report["blocked_reasons"]


def test_execute_with_invalid_config_blocks_zero_network(tmp_path, capsys):
    path = write_config(
        tmp_path,
        payload={"schema_version": 1, "zone_name": "example.com", "api_token": TOKEN},
    )
    assert run(execute_argv(path), factory=boom_factory) == 2
    assert cf.REASON_ZONE_NAME_INVALID in last_report(capsys)["blocked_reasons"]


def test_invalid_hostname_argument_rejected(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        cf.main(["--config", write_config(tmp_path), "--hostname", "1.2.3.4"])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------- execute：FakeTransport 行为


def test_execute_all_pass(tmp_path, capsys):
    fake = FakeTransport(full_ok_script())
    assert run(execute_argv(write_config(tmp_path)), factory=lambda: fake) == 0
    report = last_report(capsys)
    assert report["status"] == "pass"
    assert report["requests_made"] == [
        "user/tokens/verify",
        "zones/lookup",
        "zones/settings/ssl",
        "zones/dns_records",
    ]
    by_name = {check["name"]: check for check in report["checks"]}
    assert by_name["token-verify"]["status"] == "pass"
    assert by_name["zone-lookup"]["status"] == "pass"
    assert by_name["ssl-mode"]["ssl_mode"] == "full_strict"  # API 值 strict 归一
    assert by_name["dns-records"]["dns_record_count"] == 1
    assert "candidate_hostname_present" not in by_name["dns-records"]
    # 零重试行为证据：恰好 4 次调用，无第 5 次
    assert len(fake.calls) == 4


def test_execute_token_inactive_fails_but_completes_all_checks(tmp_path, capsys):
    fake = FakeTransport([ok_verify(status="disabled"), ok_zones(), ok_ssl(), ok_dns()])
    assert run(execute_argv(write_config(tmp_path)), factory=lambda: fake) == 1
    report = last_report(capsys)
    assert report["status"] == "fail"
    by_name = {check["name"]: check for check in report["checks"]}
    assert by_name["token-verify"]["status"] == "fail"
    assert len(fake.calls) == 4  # 失败不触发重试


def test_execute_zone_zero_matches_skips_dependents(tmp_path, capsys):
    fake = FakeTransport([ok_verify(), ok_zones(zone_ids=[]), ok_ssl(), ok_dns()])
    assert run(execute_argv(write_config(tmp_path)), factory=lambda: fake) == 1
    report = last_report(capsys)
    by_name = {check["name"]: check for check in report["checks"]}
    assert by_name["zone-lookup"]["status"] == "fail"
    assert by_name["ssl-mode"]["status"] == "fail"
    assert "skipped" in by_name["ssl-mode"]["detail"]
    assert by_name["dns-records"]["status"] == "fail"
    assert len(fake.calls) == 2  # zone 查找失败后 ssl/dns 不发请求（skipped 非静默）


def test_execute_zone_ambiguous_matches(tmp_path, capsys):
    fake = FakeTransport(
        [ok_verify(), ok_zones(zone_ids=["z1", "z2"]), ok_ssl(), ok_dns()]
    )
    assert run(execute_argv(write_config(tmp_path)), factory=lambda: fake) == 1
    by_name = {check["name"]: check for check in last_report(capsys)["checks"]}
    assert by_name["zone-lookup"]["status"] == "fail"


def test_execute_ssl_http_error_records_status_and_codes_only(tmp_path, capsys):
    fake = FakeTransport([ok_verify(), ok_zones(), ok_ssl(status=403), ok_dns()])
    assert run(execute_argv(write_config(tmp_path)), factory=lambda: fake) == 1
    by_name = {check["name"]: check for check in last_report(capsys)["checks"]}
    ssl_check = by_name["ssl-mode"]
    assert ssl_check["status"] == "fail"
    assert ssl_check["http_status"] == 403
    assert ssl_check["api_error_codes"] == [9109]
    assert ssl_check["ssl_mode"] == cf.SSL_MODE_UNKNOWN


def test_execute_ssl_unknown_value_records_unknown(tmp_path, capsys):
    fake = FakeTransport(
        [ok_verify(), ok_zones(), ok_ssl(value="brand-new-mode"), ok_dns()]
    )
    assert run(execute_argv(write_config(tmp_path)), factory=lambda: fake) == 1
    by_name = {check["name"]: check for check in last_report(capsys)["checks"]}
    assert by_name["ssl-mode"]["ssl_mode"] == cf.SSL_MODE_UNKNOWN
    assert by_name["ssl-mode"]["status"] == "fail"


@pytest.mark.parametrize("ssl_value", ["off", "flexible", "full"])
def test_execute_ssl_known_values_pass(tmp_path, capsys, ssl_value):
    fake = FakeTransport([ok_verify(), ok_zones(), ok_ssl(value=ssl_value), ok_dns()])
    assert run(execute_argv(write_config(tmp_path)), factory=lambda: fake) == 0
    by_name = {check["name"]: check for check in last_report(capsys)["checks"]}
    assert by_name["ssl-mode"]["ssl_mode"] == ssl_value


def test_execute_dns_transport_error_fails(tmp_path, capsys):
    fake = FakeTransport(
        [
            ok_verify(),
            ok_zones(),
            ok_ssl(),
            cf.TransportError("URLError <url> timed out"),
        ]
    )
    assert run(execute_argv(write_config(tmp_path)), factory=lambda: fake) == 1
    by_name = {check["name"]: check for check in last_report(capsys)["checks"]}
    assert by_name["dns-records"]["status"] == "fail"
    assert "transport-error" in by_name["dns-records"]["detail"]


def test_execute_dns_http_error_records_status_only(tmp_path, capsys):
    fake = FakeTransport(
        [
            ok_verify(),
            ok_zones(),
            ok_ssl(),
            {"status": 403, "body": {"errors": [{"code": 9109, "message": "secret"}]}},
        ]
    )
    assert run(execute_argv(write_config(tmp_path)), factory=lambda: fake) == 1
    by_name = {check["name"]: check for check in last_report(capsys)["checks"]}
    assert by_name["dns-records"]["http_status"] == 403
    assert by_name["dns-records"]["api_error_codes"] == [9109]


def test_execute_first_request_transport_error_reports_class(tmp_path, capsys):
    fake = FakeTransport([cf.TransportError("URLError <url> connection refused")])
    assert run(execute_argv(write_config(tmp_path)), factory=lambda: fake) == 1
    report = last_report(capsys)
    assert report["status"] == "fail"
    assert report["failure_class"] == "transport-error"


def test_execute_candidate_hostname_present_and_absent(tmp_path, capsys):
    candidate = "phase3." + ZONE
    # 存在（首页命中）
    fake = FakeTransport(full_ok_script())
    ok_dns_patch = ok_dns(names=("www", candidate))
    fake.script[3] = ok_dns_patch
    assert (
        run(
            execute_argv(write_config(tmp_path), extra=["--hostname", candidate]),
            factory=lambda: fake,
        )
        == 0
    )
    by_name = {check["name"]: check for check in last_report(capsys)["checks"]}
    assert by_name["dns-records"]["candidate_hostname_present"] is True
    # 不存在
    fake2 = FakeTransport(full_ok_script())
    assert (
        run(
            execute_argv(write_config(tmp_path), extra=["--hostname", candidate]),
            factory=lambda: fake2,
        )
        == 0
    )
    by_name2 = {check["name"]: check for check in last_report(capsys)["checks"]}
    assert by_name2["dns-records"]["candidate_hostname_present"] is False


def test_execute_dns_total_count_preferred_over_page(tmp_path, capsys):
    fake = FakeTransport(full_ok_script())
    fake.script[3] = ok_dns(names=("www",), total=250)
    assert run(execute_argv(write_config(tmp_path)), factory=lambda: fake) == 0
    by_name = {check["name"]: check for check in last_report(capsys)["checks"]}
    assert by_name["dns-records"]["dns_record_count"] == 250
    assert by_name["dns-records"]["dns_records_scanned"] == 1


# ---------------------------------------------------------------- 输出脱敏与确定性


def _assert_no_secrets(report_text: str) -> None:
    for marker in (TOKEN, ZONE_ID, RECORD_CONTENT, ACCOUNT_ID, "secret message body"):
        assert marker not in report_text, f"leaked marker in output: {marker!r}"


def test_execute_output_sanitized_stdout_and_file(tmp_path, capsys):
    fake = FakeTransport(full_ok_script())
    output_path = tmp_path / "report.json"
    argv = execute_argv(write_config(tmp_path)) + ["--output", str(output_path)]
    assert run(argv, factory=lambda: fake) == 0
    _assert_no_secrets(capsys.readouterr().out)
    file_text = output_path.read_text(encoding="utf-8")
    _assert_no_secrets(file_text)
    report = json.loads(file_text)
    assert report["status"] == "pass"
    # requests_made 只含 endpoint 标签，绝不含 zone id
    assert all(ZONE_ID not in item for item in report["requests_made"])


def test_blocked_output_sanitized(tmp_path, capsys):
    # unreadable 路径等 blocked 场景同样不回显任何敏感值
    path = write_config(
        tmp_path,
        payload={"schema_version": 1, "zone_name": "example.com", "api_token": TOKEN},
    )
    assert run(["--config", path], factory=boom_factory) == 2
    _assert_no_secrets(capsys.readouterr().out)


def test_report_is_deterministic_no_timestamps(tmp_path, capsys):
    path = write_config(tmp_path)
    assert run(["--config", path], factory=boom_factory) == 0
    first = capsys.readouterr().out
    assert run(["--config", path], factory=boom_factory) == 0
    second = capsys.readouterr().out
    assert first == second
    for time_key in ("generated_at", "timestamp", "time", "date"):
        assert time_key not in json.loads(first)


def test_plan_never_constructs_transport(tmp_path, capsys):
    # 哨兵工厂已在全部 plan 测试中注入；此处显式重申核心契约
    assert run(["--config", write_config(tmp_path)], factory=boom_factory) == 0
    assert last_report(capsys)["status"] == "ready-to-execute"


# ---------------------------------------------------------------- Transport 白名单单元


def test_real_transport_rejects_non_get(tmp_path):
    transport = cf.RealTransport(TOKEN, cf.DEFAULT_USER_AGENT, 5.0)
    with pytest.raises(cf.PreflightError):
        transport.request("POST", "/zones")
    with pytest.raises(cf.PreflightError):
        transport.request("DELETE", "/zones/" + ZONE_ID)


def test_load_config_unit_roundtrip(tmp_path):
    config = cf.load_config(write_config(tmp_path))
    assert config.zone_name == ZONE
    assert config.api_token == TOKEN
    assert config.execute_timeout_s == cf.DEFAULT_EXECUTE_TIMEOUT_S
    assert config.user_agent == cf.DEFAULT_USER_AGENT


# --------------------- RealTransport 2xx 解析边界（rework round 1）---------------------
#
# 缺陷回归：HTTP 2xx + 畸形 JSON 曾以 JSONDecodeError 裸逃逸（不在任何
# except 契约内）。以下测试在 urllib opener 边界打补丁（零外网），用
# 真实 RealTransport 锻炼解析边界，证明无异常逃逸且按 malformed 分类
# fail-closed。


class _FakeHTTPResponse:
    """urllib response 替身：context manager + status + 限额 read。"""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self, limit: int = -1) -> bytes:
        return self._body[:limit] if limit is not None and limit >= 0 else self._body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeOpener:
    """build_opener 替身：恒定返回脚本应答并计数 open() 调用（零重试证据）。"""

    def __init__(self, response: _FakeHTTPResponse) -> None:
        self._response = response
        self.open_calls = 0

    def open(self, request: object, timeout: float | None = None) -> _FakeHTTPResponse:
        self.open_calls += 1
        return self._response


MALFORMED_BODY = b'{"result": {"status": "acti'  # 截断 JSON


def test_real_transport_2xx_malformed_json_normalized_to_none(monkeypatch):
    transport = cf.RealTransport(TOKEN, cf.DEFAULT_USER_AGENT, 5.0)
    opener = _FakeOpener(_FakeHTTPResponse(200, MALFORMED_BODY))
    monkeypatch.setattr(cf, "build_opener", lambda: opener)
    resp = transport.request("GET", "/user/tokens/verify")
    assert resp.status == 200  # HTTP 状态不丢
    assert resp.body is None  # 畸形 JSON 归一为 None，绝无异常逃逸
    assert opener.open_calls == 1  # 单次，零重试


def test_real_transport_2xx_valid_json_still_parsed(monkeypatch):
    transport = cf.RealTransport(TOKEN, cf.DEFAULT_USER_AGENT, 5.0)
    opener = _FakeOpener(_FakeHTTPResponse(200, b'{"result": {"status": "active"}}'))
    monkeypatch.setattr(cf, "build_opener", lambda: opener)
    resp = transport.request("GET", "/user/tokens/verify")
    assert resp.status == 200
    assert resp.body == {"result": {"status": "active"}}


def test_real_transport_malformed_2xx_fail_closed_all_checks(tmp_path, monkeypatch):
    """端到端：4 个请求全部 200+畸形 JSON → 4 检查全 FAIL，无异常逃逸。"""
    opener = _FakeOpener(_FakeHTTPResponse(200, MALFORMED_BODY))
    monkeypatch.setattr(cf, "build_opener", lambda: opener)
    config = cf.load_config(write_config(tmp_path))
    transport = cf.RealTransport(TOKEN, cf.DEFAULT_USER_AGENT, 5.0)
    checks, requests = cf.run_execute_checks(config, transport, None)
    assert [check["status"] for check in checks] == [cf.STATUS_FAIL] * 4
    assert requests == [
        "user/tokens/verify",
        "zones/lookup",
        "zones/settings/ssl",
        "zones/dns_records",
    ]
    assert (
        opener.open_calls == 2
    )  # verify+zones 恰 2 次；ssl/dns 为 skipped 不发请求（既定契约），畸形不触发重试


def test_real_transport_malformed_2xx_main_emits_fail_json(
    tmp_path, capsys, monkeypatch
):
    """main 级：畸形 2xx 产出承诺的 fail JSON 报告（exit 1），而非 traceback。"""
    opener = _FakeOpener(_FakeHTTPResponse(200, MALFORMED_BODY))
    monkeypatch.setattr(cf, "build_opener", lambda: opener)
    transport = cf.RealTransport(TOKEN, cf.DEFAULT_USER_AGENT, 5.0)
    code = cf.main(
        execute_argv(write_config(tmp_path)), transport_factory=lambda: transport
    )
    assert code == 1
    report = last_report(capsys)
    assert report["status"] == "fail"
    assert all(check["status"] == cf.STATUS_FAIL for check in report["checks"])
