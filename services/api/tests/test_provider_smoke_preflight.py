"""M14-112 provider-smoke 前置只读预检：契约测试（零网络、零子进程）。

覆盖矩阵：
1. CLI 注册与分发：子命令注册进主 help、非法 --voice-mode argparse exit 2、
   main() 真实分发（monkeypatch 模块级 _httpx_get 替身 => SystemExit 0）、
   --json stdout 纯 JSON、--help 可用；注册区无 --yes 执行旗标；
2. 成功形态（local 拓扑全 ready）：overall pass/exit 0、providers 键序恒为
   SMOKE_PROVIDERS、voice.checks asr/tts ready、search results 计数与
   引擎归因空、llm model_resident；确定性（同输入两次运行 JSON 全等）；
3. 失败归因闭集（固定 reason->recommendation 映射）：
   - endpoint_absent（M14-104 Ollama 缺席形态）→ start_externally_then_rerun
   - endpoint_timeout → investigate_endpoint_externally_then_rerun
   - http_failure（403：SearXNG 未启用 json format）→ investigate
   - malformed_response（非 JSON / models 非 list）→ investigate
   - transport_error（其它 httpx 错误）→ investigate
   - upstream_failure（200+results=[]+unresponsive_engines 自报，新/旧两种
     形态）→ repair_upstream_network_externally（引擎名单透出）
   - empty_results（200+0 结果且无自报）→ investigate
   - model_absent（/api/ps 200 但无该模型）→ load_model_externally_then_rerun
   - not_configured（空白端点）/ malformed_url（非 http URL）→ fix_endpoint_config
   - cloud/hybrid 语音槽位 not_probed/external_credentials_not_inspected
     → verify_external_preconditions_then_run_smoke（不检凭据）；
4. 拓扑/provider-key 无漂移：PROVIDER_KEYS == release_readiness.SMOKE_PROVIDERS、
   VOICE_MODES 三方一致（provider_smoke_evidence/release_readiness/本模块）、
   providers 键序 == PROVIDER_KEYS；非法 voice_mode 先于一切探测
   ProviderSmokePreflightInputError（不做任何 HTTP 调用）；
5. loopback 代理边界：loopback 探测恒 trust_env=False（替身记录断言）、
   非 loopback search 端点 trust_env=True；ambient 压力观测（patch
   getproxies 注册表形态 + 清 NO_PROXY + proxy_bypass=False => 检出；
   proxy_bypass=True（NO_PROXY 豁免）=> 不检出）；代理值（可能内嵌凭据）
   绝不进入输出；
6. URL fail-closed 与远端字符串边界（supervisor 修正 Round 1/2）：
   - 含 userinfo（user:password 与 user-only 两形态）/query/fragment（含
     尾随裸 ?/# 分隔符）的 endpoint 一律先于探测拒绝
     （malformed_url/fix_endpoint_config、endpoint 不回显、凭据零泄漏）；
     合法校验后的 endpoint 如实呈现；
   - 远端自报名单（unresponsive 引擎名/错误类、驻留模型名）逐项剔除
     Unicode Cc 控制字符并截断到 128 码点（全控制条目丢弃）、条数上限
     32/16 保持、排序确定、匹配语义走原始名；敌意超大响应的报告体量
     有确定上界；
7. 只读守卫（源码级 ast 扫描）：模块零 subprocess/os.environ/getenv/
   open/write/mkdir/网络写面；CLI 处理函数零文件写入面；
8. 非门证据自声明：报告恒 production_ready=false、
   release_readiness_evidence=false；报告喂给 release-readiness 的
   provider-smoke 门评估器必须 MalformedEvidence 拒收；人类摘要含
   「不是 release-readiness 证据」「不代表 provider-smoke 已通过」边界；
9. 退出码：pass=0 / blocked=1 / partial=1（cloud 语音 not_probed）；
10. 默认端点：不传端点时探测 8878/8010/8011/11434 根（仓库本地部署契约）。

全部测试只用进程内替身（fake get / monkeypatch httpx.Client 构造记录）——
不调用任何真实 provider/外网端点、不连数据库、不发网络请求。
"""
from __future__ import annotations

import ast
import json
import socket
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.ops import cli as cli_module
from app.ops import provider_smoke_preflight as psp
from app.ops.evidence_kit import (
    MalformedEvidence,
    find_embedded_credential,
    find_sensitive_key,
)
from app.ops.provider_smoke_evidence import VOICE_MODES as EVIDENCE_VOICE_MODES
from app.ops.provider_smoke_preflight import (
    PROVIDER_KEYS,
    PROVIDER_STATUSES,
    REASON_TO_RECOMMENDATION,
    REASONS,
    RECOMMENDATIONS,
    PreflightTransportError,
    ProviderSmokePreflightInputError,
    format_preflight_summary,
    preflight_exit_code,
    run_provider_smoke_preflight,
)
from app.ops.release_readiness import (
    SMOKE_PROVIDERS,
    SMOKE_VOICE_MODES,
    _eval_provider_smoke,
)

FIXED_CLOCK = datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)


# ---------- 进程内替身传输层（记录调用，零网络） ----------


class FakeGet:
    """按 URL 路由响应的替身传输层：记录 (url, kwargs) 以断言 trust_env 语义。"""

    def __init__(self, routes: dict[str, Any]) -> None:
        # routes: url 后缀模式 -> (status, body) | PreflightTransportError
        self.routes = routes
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, **kwargs: Any) -> tuple[int, str]:
        self.calls.append((url, dict(kwargs)))
        for pattern, outcome in self.routes.items():
            if pattern in url:
                if isinstance(outcome, Exception):
                    raise outcome
                status, body = outcome
                return status, body
        raise AssertionError(f"FakeGet 未路由的 URL: {url}")


def _absent() -> PreflightTransportError:
    return PreflightTransportError("endpoint_absent", "ConnectError")


def _timeout() -> PreflightTransportError:
    return PreflightTransportError("endpoint_timeout", "ReadTimeout")


def _transport_error() -> PreflightTransportError:
    return PreflightTransportError("transport_error", "ConnectProtocolError")


SEARCH_OK = (200, json.dumps({"results": [{"url": "https://example.com/a"}] * 5}))
ASR_OK = (200, json.dumps({"status": "ok", "models_loaded": ["sensevoice"]}))
TTS_OK = (200, json.dumps({"status": "ok", "model": "Fun-CosyVoice3-0.5B-2512"}))
PS_OK = (
    200,
    json.dumps({"models": [{"name": "aios-qwen3.5-9b-4096:latest"}]}),
)


def _all_ok_get() -> FakeGet:
    return FakeGet(
        {
            "/search": SEARCH_OK,
            "8010/health": ASR_OK,
            "8011/health": TTS_OK,
            "/api/ps": PS_OK,
        }
    )


def _partial_get(overrides: dict[str, Any]) -> FakeGet:
    """覆盖部分路由、其余保持 ok 默认（单 provider 归因测试用）。

    override 模式与默认模式互为子串时（如 "health" 覆盖 "8010/health"），
    默认路由让位且 override 排在前面——匹配顺序即优先级。
    """
    routes: dict[str, Any] = dict(overrides)
    defaults: dict[str, Any] = {
        "/search": SEARCH_OK,
        "8010/health": ASR_OK,
        "8011/health": TTS_OK,
        "/api/ps": PS_OK,
    }
    for pattern, outcome in defaults.items():
        if any(o in pattern or pattern in o for o in overrides):
            continue
        routes[pattern] = outcome
    return FakeGet(routes)


def _run(get: Any, **kwargs: Any) -> dict[str, Any]:
    return run_provider_smoke_preflight(get=get, clock=lambda: FIXED_CLOCK, **kwargs)


# ---------- 1. CLI 注册与分发 ----------


def test_subcommand_registered_in_main_help(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["cli", "provider-smoke-preflight", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "provider-smoke-preflight" in out
    assert "--voice-mode" in out and "--search-endpoint" in out
    assert "--llm-model" in out


def test_cli_invalid_voice_mode_exit_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys, "argv", ["cli", "provider-smoke-preflight", "--voice-mode", "bogus"]
    )
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 2


def test_cli_dispatched_via_main(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["cli", "provider-smoke-preflight", "--json"])
    monkeypatch.setattr(psp, "_httpx_get", _all_ok_get())
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["overall_status"] == "pass"


def test_cli_source_has_no_yes_flag() -> None:
    """parser 注册区（p_ppf 块）不得有 --yes 旗标：预检是只读面，没有
    「额外授权执行」形态。"""
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    block = source.split("p_ppf = sub.add_parser(")[1].split(
        "p_pg = sub.add_parser("
    )[0]
    assert '"--yes"' not in block


# ---------- 2. 成功形态与确定性 ----------


def test_all_ready_local_topology_pass() -> None:
    report = _run(_all_ok_get())
    assert report["overall_status"] == "pass"
    assert preflight_exit_code(report) == 0
    assert list(report["providers"]) == list(PROVIDER_KEYS) == ["voice", "search", "llm"]
    voice = report["providers"]["voice"]
    assert voice["status"] == "ready" and voice["reason"] is None
    assert voice["checks"]["asr"]["status"] == "ready"
    assert voice["checks"]["asr"]["http_status"] == 200
    assert voice["checks"]["asr"]["json_body"] is True
    assert voice["checks"]["tts"]["status"] == "ready"
    search = report["providers"]["search"]
    assert search["status"] == "ready"
    assert search["results"] == 5
    assert search["unresponsive_engines"] == []
    llm = report["providers"]["llm"]
    assert llm["status"] == "ready" and llm["model_resident"] is True
    assert llm["resident_models"] == ["aios-qwen3.5-9b-4096:latest"]


def test_deterministic_output_same_input() -> None:
    first = _run(_all_ok_get())
    second = _run(_all_ok_get())
    assert json.dumps(first, sort_keys=False) == json.dumps(second, sort_keys=False)


def test_default_endpoints_are_repo_local_contract() -> None:
    get = _all_ok_get()
    _run(get)
    urls = [url for url, _ in get.calls]
    assert any(u.startswith("http://127.0.0.1:8878/search") for u in urls)
    assert any("127.0.0.1:8010/health" in u for u in urls)
    assert any("127.0.0.1:8011/health" in u for u in urls)
    assert any(u == "http://127.0.0.1:11434/api/ps" for u in urls)


# ---------- 3. 失败归因闭集 ----------


def test_endpoint_absent_attribution() -> None:
    report = _run(FakeGet({"search": _absent(), "health": _absent(), "ps": _absent()}))
    assert report["overall_status"] == "blocked"
    assert preflight_exit_code(report) == 1
    for name in PROVIDER_KEYS:
        entry = report["providers"][name]
        assert entry["status"] == "not_ready"
        assert entry["reason"] == "endpoint_absent"
        assert entry["recommendation"] == "start_externally_then_rerun"


def test_endpoint_timeout_attribution() -> None:
    report = _run(FakeGet({"search": _timeout(), "health": _timeout(), "ps": _timeout()}))
    for name in PROVIDER_KEYS:
        entry = report["providers"][name]
        assert entry["reason"] == "endpoint_timeout"
        assert (
            entry["recommendation"] == "investigate_endpoint_externally_then_rerun"
        )


def test_http_failure_attribution() -> None:
    report = _run(
        _partial_get(
            {
                "/search": (403, "json format not enabled"),
                "health": (503, "starting"),
                "/api/ps": (404, "not found"),
            }
        )
    )
    for name in PROVIDER_KEYS:
        entry = report["providers"][name]
        assert entry["status"] == "not_ready"
        assert entry["reason"] == "http_failure"
        if name == "voice":
            statuses = [c["http_status"] for c in entry["checks"].values()]
        else:
            statuses = [entry["http_status"]]
        assert all(s in (403, 503, 404) for s in statuses), statuses


def test_malformed_response_attribution() -> None:
    report = _run(
        FakeGet(
            {
                "/search": (200, "<html>not json</html>"),
                "health": (200, "ok"),  # voice 健康契约只看 200——仍 ready
                "/api/ps": (200, json.dumps({"models": "oops"})),
            }
        )
    )
    assert report["providers"]["search"]["reason"] == "malformed_response"
    assert report["providers"]["llm"]["reason"] == "malformed_response"
    # 语音 /health 是 200 => listener ready（body 非 JSON 只是观测 json_body=False）
    voice = report["providers"]["voice"]
    assert voice["status"] == "ready"
    assert voice["checks"]["asr"]["json_body"] is False


def test_search_results_not_list_malformed() -> None:
    report = _run(_partial_get({"/search": (200, json.dumps({"results": "nope"}))}))
    assert report["providers"]["search"]["reason"] == "malformed_response"


def test_transport_error_attribution() -> None:
    report = _run(
        FakeGet({"search": _transport_error(), "health": _transport_error(), "ps": _transport_error()})
    )
    for name in PROVIDER_KEYS:
        assert report["providers"][name]["reason"] == "transport_error"


def test_search_upstream_failure_modern_shape() -> None:
    body = json.dumps(
        {
            "results": [],
            "unresponsive_engines": [
                ["brave", "HTTP connection error"],
                ["duckduckgo", "HTTP connection error"],
                ["google", "timeout"],
            ],
        }
    )
    report = _run(_partial_get({"/search": (200, body)}))
    search = report["providers"]["search"]
    assert search["status"] == "not_ready"
    assert search["reason"] == "upstream_failure"
    assert search["recommendation"] == "repair_upstream_network_externally"
    assert search["unresponsive_engines"] == ["brave", "duckduckgo", "google"]
    assert search["unresponsive_error_classes"] == [
        "HTTP connection error",
        "timeout",
    ]


def test_search_upstream_failure_legacy_string_shape() -> None:
    body = json.dumps({"results": [], "unresponsive_engines": ["brave", "bing"]})
    report = _run(_partial_get({"/search": (200, body)}))
    search = report["providers"]["search"]
    assert search["reason"] == "upstream_failure"
    assert search["unresponsive_engines"] == ["bing", "brave"]
    assert search["unresponsive_error_classes"] == []


def test_search_results_with_upstream_report_still_ready() -> None:
    body = json.dumps(
        {
            "results": [{"url": "https://example.com/a"}],
            "unresponsive_engines": [["brave", "HTTP connection error"]],
        }
    )
    report = _run(_partial_get({"/search": (200, body)}))
    search = report["providers"]["search"]
    assert search["status"] == "ready"
    assert search["results"] == 1
    # 有结果但有部分引擎不可达：归因仍透出（观测），不影响 ready
    assert search["unresponsive_engines"] == ["brave"]


def test_search_empty_results_without_upstream_report() -> None:
    report = _run(_partial_get({"/search": (200, json.dumps({"results": []}))}))
    assert report["providers"]["search"]["reason"] == "empty_results"


def test_llm_model_absent_attribution() -> None:
    body = json.dumps({"models": [{"name": "qwen3.5:9b"}]})
    report = _run(_partial_get({"/api/ps": (200, body)}))
    llm = report["providers"]["llm"]
    assert llm["status"] == "not_ready"
    assert llm["reason"] == "model_absent"
    assert llm["recommendation"] == "load_model_externally_then_rerun"
    assert llm["model_resident"] is False
    assert llm["resident_models"] == ["qwen3.5:9b"]


def test_llm_model_match_ignores_tag_suffix() -> None:
    body = json.dumps({"models": [{"name": "aios-qwen3.5-9b-4096:latest"}]})
    report = _run(_partial_get({"/api/ps": (200, body)}))
    assert report["providers"]["llm"]["model_resident"] is True


def test_not_configured_and_malformed_url() -> None:
    report = _run(_all_ok_get(), search_endpoint="   ")
    assert report["providers"]["search"]["reason"] == "not_configured"
    assert report["providers"]["search"]["recommendation"] == "fix_endpoint_config"
    report = _run(_all_ok_get(), llm_endpoint="not-a-url")
    assert report["providers"]["llm"]["reason"] == "malformed_url"
    assert report["providers"]["llm"]["recommendation"] == "fix_endpoint_config"


# ---------- 4. 拓扑与 provider-key 无漂移 ----------


def test_provider_keys_match_readiness_and_voice_modes_cross_locked() -> None:
    assert PROVIDER_KEYS == SMOKE_PROVIDERS
    assert psp.VOICE_MODES == EVIDENCE_VOICE_MODES == SMOKE_VOICE_MODES
    assert set(REASON_TO_RECOMMENDATION) == set(REASONS)
    assert set(REASON_TO_RECOMMENDATION.values()) <= set(RECOMMENDATIONS)
    assert set(PROVIDER_STATUSES) == {"ready", "not_ready", "not_probed"}


@pytest.mark.parametrize("mode", ["cloud", "hybrid"])
def test_cloud_topology_voice_not_probed_without_credential_inspection(mode) -> None:
    report = _run(_all_ok_get(), voice_mode=mode)
    voice = report["providers"]["voice"]
    assert voice["status"] == "not_probed"
    assert voice["reason"] == "external_credentials_not_inspected"
    assert (
        voice["recommendation"]
        == "verify_external_preconditions_then_run_smoke"
    )
    assert "checks" not in voice
    # 可探测面（search/llm）ready 但语音面未观测 => partial 如实
    assert report["overall_status"] == "partial"
    assert preflight_exit_code(report) == 1
    assert report["topology"] == {"voice_mode": mode}


def test_invalid_voice_mode_rejected_before_any_probe() -> None:
    get = _all_ok_get()
    with pytest.raises(ProviderSmokePreflightInputError):
        run_provider_smoke_preflight(voice_mode="bogus", get=get)
    assert get.calls == []


# ---------- 5. loopback 代理边界 ----------


def test_loopback_probes_force_trust_env_false_non_loopback_default() -> None:
    get = _all_ok_get()
    _run(get)
    by_url = dict(get.calls)
    for url, kwargs in get.calls:
        assert kwargs["trust_env"] is False  # 默认端点全 loopback
    assert set(by_url)


def test_non_loopback_search_endpoint_keeps_default_trust_env() -> None:
    get = _all_ok_get()
    _run(get, search_endpoint="https://search.example.com")
    search_call = next(
        (kw for url, kw in get.calls if "search.example.com" in url), None
    )
    assert search_call is not None and search_call["trust_env"] is True


@pytest.fixture()
def registry_style_ambient_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """模拟 M14-104 的 Windows 注册表代理暴露：getproxies 双通道注入内嵌
    凭据的占位代理、清 NO_PROXY、proxy_bypass 不豁免 loopback。"""
    import urllib.request

    monkeypatch.setattr(
        urllib.request,
        "getproxies",
        lambda: {
            "http": "http://proxy-user:proxy-pass@198.51.100.1:7892",
            "https": "http://proxy-user:proxy-pass@198.51.100.1:7892",
        },
    )
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)


def test_ambient_proxy_pressure_detected_without_leaking_values(
    monkeypatch, registry_style_ambient_proxy
) -> None:
    get = _all_ok_get()
    report = _run(get)
    ambient = report["ambient_proxy"]
    assert ambient["pressure_detected"] is True
    assert set(ambient["pressured_hosts"]) == {"127.0.0.1"}
    assert set(ambient["loopback_hosts_checked"]) == {"127.0.0.1"}
    # 代理值（含内嵌凭据）绝不进入输出；探测仍全部完成且 trust_env=False
    dumped = json.dumps(report, ensure_ascii=False)
    assert "198.51.100.1" not in dumped
    assert "proxy-user" not in dumped and "proxy-pass" not in dumped
    assert report["overall_status"] == "pass"
    assert all(kwargs["trust_env"] is False for _, kwargs in get.calls)


def test_ambient_proxy_pressure_absent_when_bypassed(
    monkeypatch, registry_style_ambient_proxy
) -> None:
    import urllib.request

    # NO_PROXY 豁免 loopback（proxy_bypass=True）=> 压力观测为无
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: True)
    report = _run(_all_ok_get())
    assert report["ambient_proxy"]["pressure_detected"] is False
    assert report["ambient_proxy"]["pressured_hosts"] == []


def test_no_proxies_no_pressure(monkeypatch) -> None:
    import urllib.request

    monkeypatch.setattr(urllib.request, "getproxies", dict)
    report = _run(_all_ok_get())
    assert report["ambient_proxy"]["pressure_detected"] is False


def test_real_transport_loopback_client_uses_trust_env_false(monkeypatch) -> None:
    """真实 _httpx_get 的 client 构造契约：loopback => trust_env=False、
    follow_redirects=False、超时透传（MockTransport 零网络）。"""
    import httpx

    created: list[dict[str, Any]] = []
    real_client = httpx.Client

    class _RecordingClient(real_client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            created.append(dict(kwargs))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _RecordingClient)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text='{"models": []}')

    psp._httpx_get(
        "http://127.0.0.1:11434/api/ps",
        timeout_seconds=10.0,
        trust_env=False,
        transport=httpx.MockTransport(handler),
    )
    assert created, "client 未构造"
    assert created[0]["trust_env"] is False
    assert created[0]["follow_redirects"] is False
    assert created[0]["timeout"] == 10.0


def test_real_transport_maps_httpx_exceptions() -> None:
    import httpx

    import app.ops.provider_smoke_preflight as module

    def deny(request: httpx.Request) -> httpx.Response:
        raise AssertionError("禁止真实拨号")

    monkey_transport = httpx.MockTransport(deny)

    def _raise(exc: Exception):
        def handler(request: httpx.Request) -> httpx.Response:
            raise exc

        return httpx.MockTransport(handler)

    with pytest.raises(PreflightTransportError) as exc:
        module._httpx_get(
            "http://127.0.0.1:1/x",
            timeout_seconds=1.0,
            trust_env=False,
            transport=_raise(httpx.ConnectError("refused")),
        )
    assert exc.value.reason == "endpoint_absent"
    with pytest.raises(PreflightTransportError) as exc:
        module._httpx_get(
            "http://127.0.0.1:1/x",
            timeout_seconds=1.0,
            trust_env=False,
            transport=_raise(httpx.ReadTimeout("timed out")),
        )
    assert exc.value.reason == "endpoint_timeout"
    with pytest.raises(PreflightTransportError) as exc:
        module._httpx_get(
            "http://127.0.0.1:1/x",
            timeout_seconds=1.0,
            trust_env=False,
            transport=_raise(httpx.ProtocolError("boom")),
        )
    assert exc.value.reason == "transport_error"
    assert monkey_transport is not None


# ---------- 6. URL fail-closed 与远端字符串边界 ----------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://user:secret@127.0.0.1:8878",  # user:password userinfo
        "http://user@127.0.0.1:8878",  # user-only userinfo
        "http://127.0.0.1:8878?q=x",  # query
        "http://127.0.0.1:8878#frag",  # fragment
        "http://127.0.0.1:8878?",  # 尾随裸 ? 分隔符
        "http://127.0.0.1:8878#",  # 尾随裸 # 分隔符
    ],
)
def test_endpoint_userinfo_query_fragment_rejected_before_probe(bad_url) -> None:
    """supervisor 修正 Round 1：含 userinfo/query/fragment 的 endpoint 一律
    fail-closed 拒绝（malformed_url/fix_endpoint_config），先于一切探测——
    凭据绝不进入探测（不从 URL 构造 Basic Auth），未通过校验的输入也
    绝不回显进报告（endpoint 恒 None）。"""
    get = _all_ok_get()
    report = _run(get, search_endpoint=bad_url)
    search = report["providers"]["search"]
    assert search["status"] == "not_ready"
    assert search["reason"] == "malformed_url"
    assert search["recommendation"] == "fix_endpoint_config"
    assert search["endpoint"] is None
    # 该 provider 未发生任何探测（其余 provider 照常）
    assert all("8878" not in url for url, _ in get.calls)
    dumped = json.dumps(report, ensure_ascii=False)
    assert "user:secret" not in dumped and "user@" not in dumped
    assert find_sensitive_key(dumped) is None
    assert find_embedded_credential(dumped) is None


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://user:secret@127.0.0.1:11434/v1",
        "http://127.0.0.1:11434/v1?token=x",
        "http://127.0.0.1:11434/v1#f",
    ],
)
def test_llm_endpoint_rejected_before_probe(bad_url) -> None:
    get = _all_ok_get()
    report = _run(get, llm_endpoint=bad_url)
    llm = report["providers"]["llm"]
    assert llm["reason"] == "malformed_url"
    assert llm["endpoint"] is None
    assert llm["model"] == "aios-qwen3.5-9b-4096"  # 模型名照常呈现（非 URL）
    assert all("/api/ps" not in url for url, _ in get.calls)


def test_voice_endpoint_with_query_rejected_before_probe() -> None:
    get = _all_ok_get()
    report = _run(get, asr_endpoint="http://127.0.0.1:8010/v1?k=secret")
    voice = report["providers"]["voice"]
    assert voice["status"] == "not_ready"
    assert voice["reason"] == "malformed_url"
    asr = voice["checks"]["asr"]
    assert asr["reason"] == "malformed_url" and asr["endpoint"] is None
    # asr 未探测；tts 照常探测且 ready
    assert all("8010" not in url for url, _ in get.calls)
    assert voice["checks"]["tts"]["status"] == "ready"
    assert "k=secret" not in json.dumps(report, ensure_ascii=False)


def test_valid_endpoint_echoed_after_validation() -> None:
    """合法（无 userinfo/query/fragment）endpoint 如实呈现——校验通过后
    无需脱敏（loopback host:port 是非敏感配置值，M14-104 口径）。"""
    get = _all_ok_get()
    report = _run(get, search_endpoint="http://127.0.0.1:8878/")
    assert report["providers"]["search"]["endpoint"] == "http://127.0.0.1:8878"


# ---------- 6b. 远端自报字符串逐项边界（supervisor 修正 Round 2） ----------


def test_unresponsive_engine_names_bounded() -> None:
    """超长引擎名截断到 128 码点、控制字符剔除、全控制条目丢弃、条数
    上限 32 保持、排序确定。"""
    long_engine = "e" * 300
    body = json.dumps(
        {
            "results": [],
            "unresponsive_engines": [
                [long_engine, "HTTP connection error"],
                ["bra\x01ve", "HTTP connection error"],  # 内嵌控制字符
                ["\x01\x02\r\n"],  # 旧形态 + 全控制字符（净化后丢弃）
                *[[f"engine-{i:02d}", "timeout"] for i in range(40)],
            ],
        }
    )
    report = _run(_partial_get({"/search": (200, body)}))
    search = report["providers"]["search"]
    engines = search["unresponsive_engines"]
    assert search["reason"] == "upstream_failure"
    assert len(engines) <= psp.MAX_REPORTED_ENGINES == 32
    assert all(len(e) <= psp.MAX_REMOTE_ITEM_CODEPOINTS == 128 for e in engines)
    assert all(
        all(unicodedata.category(ch) != "Cc" for ch in e) for e in engines
    )
    assert long_engine[:128] in engines  # 截断形态进入名单
    assert "brave" in engines  # 控制字符被剔除
    assert sorted(engines) == engines  # 排序确定
    # 全控制字符条目被丢弃：名单里没有任何空串/不可见项
    assert all(e.strip() for e in engines)


def test_unresponsive_error_classes_bounded() -> None:
    long_error = "x" * 500 + "\x7f\x1b[31m"
    body = json.dumps(
        {
            "results": [],
            "unresponsive_engines": [
                ["brave", long_error],
                ["bing", "HTTP connection error\r\n"],
                ["ddg", "\x00\x01"],
            ],
        }
    )
    report = _run(_partial_get({"/search": (200, body)}))
    errors = report["providers"]["search"]["unresponsive_error_classes"]
    assert all(len(e) <= 128 for e in errors)
    assert all(
        all(unicodedata.category(ch) != "Cc" for ch in e) for e in errors
    )
    assert long_error[:128] == "x" * 128
    assert "x" * 128 in errors  # 截断后形态（控制尾巴已剔除）
    assert "HTTP connection error" in errors
    assert sorted(errors) == errors
    # 全控制字符错误类丢弃（只剩两条）
    assert len(errors) == 2


def test_resident_model_names_bounded() -> None:
    """驻留模型名同样逐项净化：超长截断、控制剔除、全控制丢弃、上限 16
    保持、匹配语义走原始名（控制尾巴不影响 resident 判定）。"""
    long_model = "m" * 400
    body = json.dumps(
        {
            "models": [
                {"name": long_model},
                {"name": "qwen\x013.5:9b"},  # 内嵌控制字符
                {"name": "\x02\x03"},  # 全控制字符（净化后丢弃）
                {"name": "aios-qwen3.5-9b-4096:latest\x01"},  # 原始名匹配
                *[{"name": f"model-{i:02d}"} for i in range(10)],
            ]
        }
    )
    report = _run(_partial_get({"/api/ps": (200, body)}))
    llm = report["providers"]["llm"]
    assert llm["status"] == "ready"  # 原始名匹配语义不变
    assert llm["model_resident"] is True
    names = llm["resident_models"]
    # 13 个净化后条目（全控制条目已丢弃），低于 16 上限全员在场
    assert len(names) == 13
    assert len(names) <= psp.MAX_REPORTED_MODELS == 16
    assert all(len(n) <= 128 for n in names)
    assert all(
        all(unicodedata.category(ch) != "Cc" for ch in n) for n in names
    )
    assert long_model[:128] in names
    assert "qwen3.5:9b" in names
    assert "aios-qwen3.5-9b-4096:latest" in names  # 控制尾巴剔除后的净化名
    assert sorted(names) == names


def test_resident_models_count_cap_drops_tail() -> None:
    """条数上限语义保持：净化后超过 16 个驻留名时按排序取前 16（尾部
    丢弃），上限不受净化影响。"""
    body = json.dumps(
        {"models": [{"name": f"z-model-{i:02d}"} for i in range(20)]}
    )
    report = _run(_partial_get({"/api/ps": (200, body)}))
    names = report["providers"]["llm"]["resident_models"]
    assert len(names) == 16
    assert sorted(names) == names
    assert names[-1] == "z-model-15"  # 排序尾部（z-model-16..19）被丢弃


def test_report_size_bounded_against_hostile_response() -> None:
    """任意外形/体量的 provider 响应都有确定报告体量上界：名单条数 ×
    每项 128 码点。"""
    hostile_engines = [
        ["Ａ" * 10_000 + f"-{i}", "Ｂ" * 10_000] for i in range(500)
    ]
    body = json.dumps(
        {"results": [], "unresponsive_engines": hostile_engines}
    )
    report = _run(_partial_get({"/search": (200, body)}))
    search = report["providers"]["search"]
    assert len(search["unresponsive_engines"]) <= 32
    assert len(search["unresponsive_error_classes"]) <= 32
    total_chars = sum(
        len(s)
        for s in search["unresponsive_engines"]
        + search["unresponsive_error_classes"]
    )
    assert total_chars <= 64 * psp.MAX_REMOTE_ITEM_CODEPOINTS
    dumped = json.dumps(report, ensure_ascii=False)
    assert len(dumped) < 20_000  # 10 万码点级响应 -> KB 级报告


# ---------- 7. 只读守卫（源码级） ----------


def test_module_source_is_read_only() -> None:
    """ast 扫描：模块零子进程/零环境凭据读取/零文件写入/零 shell。"""
    tree = ast.parse(
        Path(psp.__file__).read_text(encoding="utf-8"), filename="provider_smoke_preflight.py"
    )
    banned_calls = {
        "subprocess",
        "os.environ",
        "getenv",
        "popen",
        "system",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "subprocess", "模块不得 import subprocess"
        if isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            assert name not in {"popen", "system", "mkdir", "write_text", "write_bytes"}, (
                f"只读模块禁止文件写入/子进程调用: {name}"
            )
    source = Path(psp.__file__).read_text(encoding="utf-8")
    for banned in banned_calls - {"subprocess"}:
        assert banned not in source, f"源码不得出现 {banned}"


def test_cli_handler_writes_no_files(monkeypatch, tmp_path) -> None:
    """CLI 处理函数不产生任何文件系统写入（stdout-only 契约）。"""
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}
    monkeypatch.setattr(psp, "_httpx_get", _all_ok_get())
    monkeypatch.chdir(tmp_path)
    exit_code = cli_module._run_provider_smoke_preflight(
        type(
            "Args",
            (),
            {
                "voice_mode": "local",
                "search_endpoint": None,
                "asr_endpoint": None,
                "tts_endpoint": None,
                "llm_endpoint": None,
                "llm_model": None,
                "as_json": False,
            },
        )()
    )
    assert exit_code == 0
    after = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}
    assert before == after and not list(tmp_path.rglob("*"))


# ---------- 8. 非门证据自声明 ----------


def test_report_is_not_release_readiness_evidence(tmp_path) -> None:
    report = _run(_all_ok_get())
    assert report["production_ready"] is False
    assert report["release_readiness_evidence"] is False
    assert report["tool"] == "provider-smoke-preflight"
    # 喂给 release-readiness 的 provider-smoke 门评估器：必须 fail-closed 拒收
    with pytest.raises(MalformedEvidence):
        _eval_provider_smoke(dict(report), tmp_path, {})


def test_summary_states_boundary_semantics() -> None:
    report = _run(_all_ok_get())
    summary = format_preflight_summary(report)
    assert "不是 release-readiness 证据" in summary
    assert "不代表 provider-smoke 已通过" in summary
    assert "production_ready=false" in summary
    blocked = _run(FakeGet({"search": _absent(), "health": _absent(), "ps": _absent()}))
    blocked_summary = format_preflight_summary(blocked)
    assert "RESULT: BLOCKED" in blocked_summary
    assert blocked_summary.count("start_externally_then_rerun") >= 1
    partial = _run(_all_ok_get(), voice_mode="cloud")
    assert "RESULT: PARTIAL" in format_preflight_summary(partial)


# ---------- 9. 退出码汇总 ----------


def test_exit_code_vocabulary() -> None:
    assert preflight_exit_code({"overall_status": "pass"}) == 0
    assert preflight_exit_code({"overall_status": "blocked"}) == 1
    assert preflight_exit_code({"overall_status": "partial"}) == 1


def test_voice_partial_failure_keeps_both_check_details() -> None:
    get = _partial_get({"8011/health": _absent()})
    report = _run(get)
    voice = report["providers"]["voice"]
    assert voice["status"] == "not_ready"
    assert voice["reason"] == "endpoint_absent"  # asr ready、tts 缺席
    assert voice["checks"]["asr"]["status"] == "ready"
    assert voice["checks"]["tts"]["reason"] == "endpoint_absent"
    assert report["overall_status"] == "blocked"


def test_no_real_network_in_test_run(monkeypatch) -> None:
    """防回归：任何真实 socket 拨号即失败（本套件只允许进程内替身）。"""
    def _forbid(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("契约测试禁止真实网络拨号")

    monkeypatch.setattr(socket, "create_connection", _forbid)
    _run(_all_ok_get())
