"""Tests for tools.ops.public_edge_stability_probe (M14-180 read-only edge probe).

All curl behavior is faked at the CurlRunner injection point — zero external
network, zero subprocess. The fake mirrors the real contract: it parses argv,
writes body/header dumps exactly where the real curl would (-o/-D paths), and
returns the write-out JSON line. Any probe call beyond the scripted sequence
raises, which structurally proves "no hidden retries".
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.ops import public_edge_stability_probe as probe

MANIFEST_URL = "https://ndtool.cn/aios/download-manifest.json"
APK_URL = "https://ndtool.cn/android/ai-learning-os-0.1.0-release-signed.apk"
APK_SHA = "12" * 32
APK_SIZE = 1000


# ---------------------------------------------------------------- fakes


def ok_spec(
    *,
    body: bytes = b'{"android": {"available": true}}',
    status: int = 200,
    http_version: str = "1.1",
    content_type: str = "application/json",
    remote_ip: str = "203.0.113.10",
    time_total: float = 0.5,
    ttfb: float = 0.4,
    size_download: int | None = None,
) -> dict:
    return {
        "exit": 0,
        "body": body,
        "status": status,
        "http_version": http_version,
        "content_type": content_type,
        "remote_ip": remote_ip,
        "time_total": time_total,
        "ttfb": ttfb,
        "size_download": size_download,
    }


def fail_spec(exit_code: int, **overrides: object) -> dict:
    spec = {"exit": exit_code, "body": None, "status": 0, "http_version": "0",
            "content_type": None, "remote_ip": "", "time_total": 0.2, "ttfb": 0.2}
    spec.update(overrides)
    return spec


def timeout_partial_spec(partial: bytes, **overrides: object) -> dict:
    """curl exit 28（超时）且 -o 已落部分字节：write-out 的 size_download
    等于实际接收到的部分字节数（curl 对中断传输的语义）。"""
    spec = fail_spec(
        28,
        body=partial,
        size_download=len(partial),
        time_total=15.0,
        ttfb=0.25,
        time_namelookup=0.05,
        time_connect=0.1,
        time_appconnect=0.2,
    )
    spec.update(overrides)
    return spec


class FakeCurl:
    """CurlRunner fake：按脚本逐次应答，额外调用直接抛错（证明零重试）。"""

    DEFAULT_VERSION_TEXT = (
        "curl 8.21.0 (fake) libcurl/8.21.0 Schannel\n"
        "Features: alt-svc AsynchDNS HSTS HTTP2 HTTPS-proxy IPv6 Largefile\n"
    )

    def __init__(self, script: list[dict], version_text: str | None = None) -> None:
        self.script = script
        self.version_text = version_text if version_text is not None else self.DEFAULT_VERSION_TEXT
        self.calls: list[list[str]] = []

    def version(self) -> str:
        return self.version_text

    def probe(self, argv: list[str], *, timeout: float) -> tuple[int, str]:
        if len(self.calls) >= len(self.script):
            raise AssertionError("unexpected curl call beyond scripted samples (retry leak?)")
        self.calls.append(list(argv))
        spec = self.script[len(self.calls) - 1]
        exit_code = spec["exit"]
        body_path = argv[argv.index("-o") + 1]
        header_path = argv[argv.index("-D") + 1]
        body: bytes = spec.get("body") or b""
        if exit_code == 0:
            Path(body_path).write_bytes(body)
            headers = f"HTTP/2 {spec['status']}\r\nContent-Type: {spec['content_type']}\r\n"
            Path(header_path).write_text(headers, encoding="utf-8")
        elif body:
            # 非零退出（如超时）：curl 可能已把部分字节写进 -o 文件
            Path(body_path).write_bytes(body)
        declared_size = spec.get("size_download")
        payload = {
            "response_code": spec["status"],
            "http_version": spec["http_version"],
            "exitcode": spec.get("writeout_exitcode", exit_code),
            "time_namelookup": spec.get("time_namelookup", 0.05),
            "time_connect": spec.get("time_connect", 0.1),
            "time_appconnect": spec.get("time_appconnect", 0.3),
            "time_starttransfer": spec.get("ttfb", 0.0),
            "time_total": spec.get("time_total", 0.0),
            "size_download": declared_size if declared_size is not None else (len(body) if exit_code == 0 else 0),
            "num_redirects": spec.get("num_redirects", 0),
            "remote_ip": spec.get("remote_ip", ""),
        }
        return exit_code, json.dumps(payload)


class Recorder:
    def __init__(self) -> None:
        self.values: list[float] = []

    def __call__(self, value: float) -> None:
        self.values.append(value)


def make_config(**overrides: object) -> probe.ProbeConfig:
    values: dict = {
        "url": probe.parse_public_asset_url(MANIFEST_URL),
        "proxy": None,
        "http_version": probe.HTTP11,  # 与 CLI 新默认一致：可靠 HTTP/1.1
        "samples": 2,
        "interval_s": probe.MIN_INTERVAL_S,
        "timeout_s": 5.0,
        "expect_content_type": None,
        "large_asset_url": None,
        "large_asset_sha256": None,
        "large_asset_size": None,
    }
    values.update(overrides)
    return probe.ProbeConfig(**values)


def run_with_fake(script: list[dict], config: probe.ProbeConfig):
    fake = FakeCurl(script)
    sleeper = Recorder()

    def clock() -> str:
        return "2026-09-29T00:00:00+00:00"

    report = probe.run_probe(config, fake, sleeper, clock)
    return report, fake, sleeper


# ---------------------------------------------------------------- 入口校验（纯函数）


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "http://ndtool.cn/aios/download-manifest.json",
        "https://user:pass@ndtool.cn/aios/download-manifest.json",
        "https://ndtool.cn/aios/download-manifest.json?x=1",
        "https://ndtool.cn/aios/download-manifest.json#frag",
        "https://example.com/manifest.json",
        "https://sub.invalid/manifest.json",
        "https://127.0.0.1/manifest.json",
        "https://[::1]/manifest.json",  # IPv6 loopback：非全局地址拒绝
        "https://[fe80::1]/manifest.json",  # IPv6 link-local：拒绝
        "https://10.0.0.5/manifest.json",
        "https://192.0.2.10/manifest.json",
        "https://ndtool.cn",
        "https://ndtool.cn/",
        "https://ndtool.cn/aios/../manifest.json",
        "https://ndtool.cn/aios//manifest.json",
        "https://ndtool.cn/aios/manifest%2Ejson",
        "https://ndtool.cn\\aios\\manifest.json",
    ],
)
def test_parse_public_asset_url_rejects(raw: str) -> None:
    with pytest.raises(probe.ProbeError):
        probe.parse_public_asset_url(raw)


def test_parse_public_asset_url_canonical() -> None:
    asset = probe.parse_public_asset_url("HTTPS://NDTOOL.CN/aios/download-manifest.json")
    assert asset.url == MANIFEST_URL
    assert asset.host == "ndtool.cn" and asset.port == 443
    custom = probe.parse_public_asset_url("https://ndtool.cn:8443/a.json")
    assert custom.url == "https://ndtool.cn:8443/a.json"


def test_parse_public_asset_url_ipv6_bracketed_canonical() -> None:
    # 全局 IPv6 资产：canonical url 必须保留方括号（冒号歧义），裸 host
    # 存 hostname 语义值；默认 443 省略、自定义端口保留。
    asset = probe.parse_public_asset_url("https://[2606:4700::6810:85e5]/a.json")
    assert asset.url == "https://[2606:4700::6810:85e5]/a.json"
    assert asset.host == "2606:4700::6810:85e5"
    assert asset.port == 443
    custom = probe.parse_public_asset_url("https://[2606:4700::6810:85e5]:8443/a.json")
    assert custom.url == "https://[2606:4700::6810:85e5]:8443/a.json"
    assert custom.port == 8443


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "socks5://127.0.0.1:1080",
        "http://user:pass@127.0.0.1:7892",
        "http://127.0.0.1",
        "http://127.0.0.1:7892/path",
        "http://127.0.0.1:7892?q=1",
        "http://127.0.0.1:99999",
    ],
)
def test_parse_proxy_url_rejects(raw: str) -> None:
    with pytest.raises(probe.ProbeError):
        probe.parse_proxy_url(raw)


def test_parse_proxy_url_accepts_loopback_with_explicit_port() -> None:
    assert probe.parse_proxy_url("http://127.0.0.1:7892").display == "http://127.0.0.1:7892"


def test_parse_proxy_url_ipv6_bracketed_display() -> None:
    # IPv6 代理（loopback 合法）：display 必须保留方括号，端口恒显式
    assert probe.parse_proxy_url("http://[::1]:7892").display == "http://[::1]:7892"
    # IPv4/域名行为回归不变
    assert probe.parse_proxy_url("http://127.0.0.1:7892").display == "http://127.0.0.1:7892"
    assert probe.parse_proxy_url("https://proxy.internal:8080").display == "https://proxy.internal:8080"


def test_format_canonical_host_brackets_ipv6_only() -> None:
    # 共享格式化器：冒号（IPv6）才加括号；443 省略仅对 default_port=443 生效
    assert probe.format_canonical_host("https", "2606:4700::6810:85e5", 443, default_port=443) == "https://[2606:4700::6810:85e5]"
    assert probe.format_canonical_host("https", "ndtool.cn", 443, default_port=443) == "https://ndtool.cn"
    assert probe.format_canonical_host("https", "1.2.3.4", 8443, default_port=443) == "https://1.2.3.4:8443"
    assert probe.format_canonical_host("http", "::1", 7892, default_port=0) == "http://[::1]:7892"


# ---------------------------------------------------------------- 失败类别（纯函数）


@pytest.mark.parametrize(
    ("exit_code", "category"),
    [
        (6, "dns-error"),
        (7, "connect-error"),
        (16, "http2-error"),
        (28, "timeout"),
        (35, "tls-error"),
        (92, "http2-error"),
        (5, "proxy-dns-error"),
        (97, "proxy-error"),
        (99, "curl-exit-99"),
    ],
)
def test_categorize_curl_exit(exit_code: int, category: str) -> None:
    assert probe.categorize_curl_exit(exit_code) == category


def test_derive_failure_category_order() -> None:
    kw = {"exit_code": 0, "status": 200, "content_type": "application/json",
          "expect_content_type": None, "body_size": 3, "reported_size": 3,
          "expected_size": None, "sha256": None, "expected_sha256": None}
    assert probe.derive_failure_category(**kw) is None
    assert probe.derive_failure_category(**{**kw, "status": 404}) == "http-error-status"
    assert probe.derive_failure_category(**{**kw, "status": 301}) == "http-redirect"
    assert probe.derive_failure_category(**{**kw, "status": None}) == "no-response"
    assert probe.derive_failure_category(
        **{**kw, "content_type": "text/html", "expect_content_type": "application/json"}
    ) == "content-type-mismatch"
    assert probe.derive_failure_category(**{**kw, "body_size": 3, "reported_size": 9}) == "byte-count-mismatch"
    assert probe.derive_failure_category(**{**kw, "expected_size": 8}) == "size-mismatch"
    assert probe.derive_failure_category(
        **{**kw, "sha256": "aa" * 32, "expected_sha256": "bb" * 32}
    ) == "checksum-mismatch"


def test_media_type_match_ignores_charset_parameters() -> None:
    assert probe.media_type_matches("application/json; charset=utf-8", "application/json")
    assert probe.media_type_matches("APPLICATION/JSON", "application/json")
    assert not probe.media_type_matches("text/html", "application/json")
    assert not probe.media_type_matches(None, "application/json")


def test_parse_content_type_case_insensitive() -> None:
    headers = "HTTP/1.1 200\r\ncontent-TYPE: application/json\r\nX: y\r\n"
    assert probe.parse_content_type(headers) == "application/json"
    assert probe.parse_content_type("HTTP/1.1 200\r\nX: y\r\n") is None


# ---------------------------------------------------------------- 有界采样与 argv 形状


def test_bounded_sampling_counts_and_intervals() -> None:
    config = make_config(samples=4, interval_s=2.0)
    report, fake, sleeper = run_with_fake([ok_spec() for _ in range(4)], config)
    assert len(fake.calls) == 4  # 恰 N 次调用，无隐藏重试
    assert sleeper.values == [2.0, 2.0, 2.0]  # 样本间强制间隔（N-1 次）
    assert [r["asset"] for r in report["samples"]] == ["primary"] * 4


def test_direct_mode_bypasses_env_proxy() -> None:
    _, fake, _ = run_with_fake([ok_spec()], make_config(samples=1))
    argv = fake.calls[0]
    assert argv[argv.index("--noproxy") + 1] == "*"
    assert "-x" not in argv
    assert argv[-1] == MANIFEST_URL


def test_proxy_mode_pins_explicit_proxy() -> None:
    config = make_config(samples=1, proxy=probe.parse_proxy_url("http://127.0.0.1:7892"))
    report, fake, _ = run_with_fake([ok_spec()], config)
    argv = fake.calls[0]
    assert argv[argv.index("-x") + 1] == "http://127.0.0.1:7892"
    assert "--noproxy" not in argv
    assert report["config"]["probe_mode"] == "proxy"
    assert report["samples"][0]["probe_mode"] == "proxy"


@pytest.mark.parametrize(
    ("requested", "flag"),
    [(probe.HTTP11, "--http1.1"), (probe.HTTP2, "--http2")],
)
def test_http_version_flag_and_actual_recorded(requested: str, flag: str) -> None:
    config = make_config(samples=1, http_version=requested)
    report, fake, _ = run_with_fake([ok_spec(http_version="1.1")], config)
    assert flag in fake.calls[0]
    record = report["samples"][0]
    assert record["http_version_requested"] == requested
    assert record["http_version"] == "1.1"  # 实际版本逐样本回读（协商结果可能是 1.1）


def test_timeout_flag_bounds_request() -> None:
    _, fake, _ = run_with_fake([ok_spec()], make_config(samples=1, timeout_s=7.5))
    argv = fake.calls[0]
    assert argv[argv.index("--max-time") + 1] == "7.5"


# ---------------------------------------------------------------- HTTP/2 能力门与诊断模式


def test_parse_curl_features() -> None:
    text = "curl 8.21.0 (x) libcurl/8.21.0 Schannel\nFeatures: alt-svc HSTS HTTP2 IPv6\n"
    assert probe.parse_curl_features(text) == {"alt-svc", "HSTS", "HTTP2", "IPv6"}
    assert probe.parse_curl_features("curl 8.21.0 (x)\n") == set()  # 无 Features 行


def test_http2_capability_gate_fails_fast_without_http2_feature() -> None:
    no_http2 = "curl 7.80.0 (fake) libcurl/7.80.0 Schannel\nFeatures: AsynchDNS IPv6\n"
    fake = FakeCurl([ok_spec()], version_text=no_http2)
    with pytest.raises(probe.ProbeError):
        probe.run_probe(make_config(samples=3, http_version=probe.HTTP2), fake, Recorder(), lambda: "t")
    assert fake.calls == []  # 采样前拦截：零请求，绝不产生 curl-exit-2 样本


def test_http2_capability_gate_allows_when_feature_present() -> None:
    report, fake, _ = run_with_fake([ok_spec(http_version="2")], make_config(samples=1, http_version=probe.HTTP2))
    assert "--http2" in fake.calls[0]
    assert report["curl_features_has_http2"] is True


def test_http1_default_and_reported() -> None:
    report, fake, _ = run_with_fake([ok_spec()], make_config(samples=1))
    assert "--http1.1" in fake.calls[0]
    assert report["config"]["http_version_requested"] == probe.HTTP11


def test_ssl_no_revoke_diagnostic_mode_flag_and_recorded() -> None:
    config = make_config(samples=1, ssl_no_revoke=True)
    report, fake, _ = run_with_fake([ok_spec()], config)
    assert "--ssl-no-revoke" in fake.calls[0]
    assert report["config"]["ssl_no_revoke"] is True
    assert report["samples"][0]["ssl_no_revoke"] is True


def test_ssl_no_revoke_off_by_default() -> None:
    _, fake, _ = run_with_fake([ok_spec()], make_config(samples=1))
    assert "--ssl-no-revoke" not in fake.calls[0]


# ---------------------------------------------------------------- 计时派生与部分传输记账


def test_phase_timing_derivation_subtracts_cumulative_stamps() -> None:
    # curl 计时全为累计：namelookup=0.1, connect=0.3(含DNS), appconnect=0.8
    # (含TCP), starttransfer=1.1(含TLS) → 阶段值必须相减。
    phases = probe.derive_phase_timings(
        namelookup_s=0.1, connect_s=0.3, appconnect_s=0.8, starttransfer_s=1.1
    )
    assert phases["dns_ms"] == pytest.approx(100.0)
    assert phases["tcp_ms"] == pytest.approx(200.0)
    assert phases["tls_ms"] == pytest.approx(500.0)
    assert phases["server_wait_ms"] == pytest.approx(300.0)


def test_phase_timing_derivation_degrades_without_tls() -> None:
    # 失败样本 appconnect=0（未完成 TLS）：tls 与 server_wait 均 None——
    # server_wait 绝不回退为累计 starttransfer（那会与 ttfb_ms 重复且
    # 混入 DNS/TCP）；累计 TTFB 由 ttfb_ms 字段单独承载。
    phases = probe.derive_phase_timings(
        namelookup_s=0.05, connect_s=0.1, appconnect_s=0.0, starttransfer_s=0.25
    )
    assert phases["dns_ms"] == pytest.approx(50.0)
    assert phases["tcp_ms"] == pytest.approx(50.0)
    assert phases["tls_ms"] is None
    assert phases["server_wait_ms"] is None
    empty = probe.derive_phase_timings(
        namelookup_s=None, connect_s=None, appconnect_s=None, starttransfer_s=None
    )
    assert empty == {"dns_ms": None, "tcp_ms": None, "tls_ms": None, "server_wait_ms": None}


def test_phase_timing_derivation_rejects_disordered_stamps() -> None:
    # 时钟乱序（appconnect < connect）：阶段为负 = 事实不可信 → None
    phases = probe.derive_phase_timings(
        namelookup_s=0.1, connect_s=0.5, appconnect_s=0.3, starttransfer_s=0.6
    )
    assert phases["tls_ms"] is None  # 0.3-0.5 < 0 拒绝
    assert phases["server_wait_ms"] == pytest.approx(300.0)  # 0.6-0.3 仍有效


def test_attempt_records_derived_phases_not_cumulative() -> None:
    spec = ok_spec(time_total=1.5, ttfb=1.1)
    spec.update({"time_namelookup": 0.1, "time_connect": 0.3, "time_appconnect": 0.8})
    report, _, _ = run_with_fake([spec], make_config(samples=1))
    record = report["samples"][0]
    assert record["elapsed_ms"] == pytest.approx(1500.0)  # 累计保留：总耗时
    assert record["ttfb_ms"] == pytest.approx(1100.0)  # 累计保留：TTFB
    assert record["dns_ms"] == pytest.approx(100.0)
    assert record["tcp_ms"] == pytest.approx(200.0)
    assert record["tls_ms"] == pytest.approx(500.0)  # appconnect−connect，非累计 appconnect
    assert record["server_wait_ms"] == pytest.approx(300.0)


def test_partial_body_accounted_on_transport_failure() -> None:
    partial = b"PK\x03\x04partial-apk-bytes"
    spec = timeout_partial_spec(partial)
    report, _, _ = run_with_fake([spec], make_config(samples=1))
    record = report["samples"][0]
    assert record["failure_category"] == "timeout"  # 传输失败仍为权威类别
    assert record["size_bytes"] == len(partial)  # 部分字节数如实记账
    assert record["sha256"] == hashlib.sha256(partial).hexdigest()
    assert record["body_complete"] is False


def test_complete_body_flagged_on_success() -> None:
    report, _, _ = run_with_fake([ok_spec()], make_config(samples=1))
    record = report["samples"][0]
    assert record["body_complete"] is True
    assert record["failure_category"] is None


# ---------------------------------------------------------------- curl 版本解析 fail-closed


def test_unrecognized_version_line_fails_closed_run_probe() -> None:
    fake = FakeCurl([ok_spec()], version_text="weird-binary v1 (no curl prefix)\n")
    with pytest.raises(probe.ProbeError):
        probe.run_probe(make_config(samples=2), fake, Recorder(), lambda: "t")
    assert fake.calls == []  # 版本不可识别：零请求


def test_unrecognized_version_line_fails_closed_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeCurl([ok_spec()], version_text="weird-binary v1\n")
    _install_fake_curl(monkeypatch, fake)
    code = probe.main(["--url", MANIFEST_URL, "--samples", "2"])
    assert code == 2  # ProbeError → exit 2，不是未捕获的 ValueError
    assert fake.calls == []


# ---------------------------------------------------------------- 样本记录与失败类别


def test_ok_attempt_records_full_facts() -> None:
    body = b'{"android": {"available": true}}'
    report, _, _ = run_with_fake(
        [ok_spec(body=body, time_total=0.5919, ttfb=0.40)], make_config(samples=1)
    )
    record = report["samples"][0]
    assert record["failure_category"] is None
    assert record["status"] == 200
    assert record["size_bytes"] == len(body)
    assert record["sha256"] == hashlib.sha256(body).hexdigest()
    assert record["content_type"] == "application/json"
    assert record["remote_ip"] == "203.0.113.10"
    assert record["elapsed_ms"] == pytest.approx(591.9, abs=0.1)
    assert record["ttfb_ms"] == pytest.approx(400.0, abs=0.1)


@pytest.mark.parametrize(
    ("exit_code", "category"),
    [(28, "timeout"), (6, "dns-error"), (16, "http2-error"), (35, "tls-error")],
)
def test_transport_failure_recorded_not_retried(exit_code: int, category: str) -> None:
    script = [ok_spec(), fail_spec(exit_code), ok_spec()]
    report, fake, _ = run_with_fake(script, make_config(samples=3))
    assert len(fake.calls) == 3  # 失败样本不重试
    assert report["samples"][1]["failure_category"] == category
    assert report["samples"][1]["status"] is None  # curl 哨兵 0 归一为 None
    assert report["samples"][1]["http_version"] is None
    assert report["samples"][0]["failure_category"] is None
    assert report["summary"]["failure_categories"] == {category: 1}


def test_writeout_exitcode_mismatch_distrusts_facts() -> None:
    # write-out 自称 exitcode=0 但进程退出码=6 → 事实整体不可信 → 只信退出码
    spec = fail_spec(6, writeout_exitcode=0, status=200, http_version="2")
    report, _, _ = run_with_fake([spec], make_config(samples=1))
    record = report["samples"][0]
    assert record["failure_category"] == "dns-error"
    assert record["status"] is None and record["http_version"] is None


def test_unparsable_writeout_is_no_response() -> None:
    fake = FakeCurl([ok_spec()])

    def broken_probe(argv, *, timeout):
        fake.calls.append(list(argv))
        return 0, "not-json-at-all"

    fake.probe = broken_probe  # type: ignore[method-assign]
    report = probe.run_probe(make_config(samples=1), fake, Recorder(), lambda: "t")
    assert report["samples"][0]["failure_category"] == "no-response"


def test_derived_status_categories() -> None:
    script = [ok_spec(status=404), ok_spec(status=301, body=b"")]
    report, _, _ = run_with_fake(script, make_config(samples=2))
    assert report["samples"][0]["failure_category"] == "http-error-status"
    assert report["samples"][1]["failure_category"] == "http-redirect"


def test_content_type_expectation_enforced() -> None:
    config = make_config(samples=1, expect_content_type="application/json")
    report, _, _ = run_with_fake([ok_spec(content_type="text/html")], config)
    assert report["samples"][0]["failure_category"] == "content-type-mismatch"


def test_byte_count_mismatch_detected() -> None:
    spec = ok_spec(size_download=999)  # curl 报告 999 字节，实际 body 更少
    report, _, _ = run_with_fake([spec], make_config(samples=1))
    assert report["samples"][0]["failure_category"] == "byte-count-mismatch"


# ---------------------------------------------------------------- 大资产至多一次


def test_large_asset_downloaded_exactly_once_and_verified() -> None:
    body = b"x" * APK_SIZE
    config = make_config(
        samples=3,
        large_asset_url=probe.parse_public_asset_url(APK_URL),
        large_asset_sha256=hashlib.sha256(body).hexdigest(),
        large_asset_size=APK_SIZE,
    )
    script = [ok_spec() for _ in range(3)] + [ok_spec(body=body, content_type="application/vnd.android.package-archive")]
    report, fake, _sleeper = run_with_fake(script, config)
    assert len(fake.calls) == 4  # 3 小资产 + 恰 1 次大资产
    assert fake.calls[3][-1] == APK_URL  # 大资产从不进入重复采样循环
    large = report["samples"][3]
    assert large["asset"] == "large" and large["failure_category"] is None
    assert report["summary"]["distinct_primary_sha256"] == [
        hashlib.sha256(script[0]["body"]).hexdigest()
    ]


def test_large_asset_checksum_mismatch_single_attempt() -> None:
    config = make_config(
        samples=1,
        large_asset_url=probe.parse_public_asset_url(APK_URL),
        large_asset_sha256="00" * 32,  # 必不匹配
        large_asset_size=APK_SIZE,
    )
    report, fake, _ = run_with_fake([ok_spec(), ok_spec(body=b"y" * APK_SIZE)], config)
    assert len(fake.calls) == 2  # 校验失败也不重试：仍然恰一次
    assert report["samples"][1]["failure_category"] == "checksum-mismatch"


def test_large_asset_size_mismatch() -> None:
    config = make_config(
        samples=1,
        large_asset_url=probe.parse_public_asset_url(APK_URL),
        large_asset_sha256=hashlib.sha256(b"y" * 10).hexdigest(),
        large_asset_size=APK_SIZE,
    )
    report, _, _ = run_with_fake([ok_spec(), ok_spec(body=b"y" * 10)], config)
    assert report["samples"][1]["failure_category"] == "size-mismatch"


# ---------------------------------------------------------------- 汇总与序列化


def test_summary_statistics() -> None:
    script = [ok_spec(time_total=0.2, ttfb=0.1), ok_spec(time_total=0.4, ttfb=0.3), fail_spec(28)]
    report, _, _ = run_with_fake(script, make_config(samples=3))
    summary = report["summary"]
    assert summary["records_total"] == 3
    assert summary["ok"] == 2 and summary["failed"] == 1
    assert summary["failure_categories"] == {"timeout": 1}
    assert summary["statuses"] == {"200": 2}
    assert summary["http_versions"] == {"1.1": 2}
    assert summary["elapsed_ms_min"] == pytest.approx(200.0, abs=0.1)
    assert summary["elapsed_ms_max"] == pytest.approx(400.0, abs=0.1)
    assert summary["ttfb_ms_max"] == pytest.approx(300.0, abs=0.1)
    assert len(summary["distinct_primary_sha256"]) == 1


def test_sha_drift_across_samples_visible() -> None:
    script = [ok_spec(body=b"v1"), ok_spec(body=b"v2")]
    report, _, _ = run_with_fake(script, make_config(samples=2))
    assert len(report["summary"]["distinct_primary_sha256"]) == 2  # 漂移被记录而非掩盖


def test_report_serialization_atomic_and_leak_free(tmp_path: Path) -> None:
    report, _, _ = run_with_fake([ok_spec()], make_config(samples=1))
    output = tmp_path / "probe-report.json"
    probe._write_report_atomic(str(output), report)
    text = output.read_text(encoding="utf-8")
    parsed = json.loads(text)
    assert parsed["schema"] == probe.SCHEMA
    assert parsed["tool"] == probe.TOOL_NAME
    assert parsed["config"]["url"] == MANIFEST_URL
    assert parsed["samples"][0]["sha256"] == hashlib.sha256(ok_spec()["body"]).hexdigest()
    assert text == json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    # 报告绝不泄漏本机临时目录 / body/header 转储路径
    assert "aios-edge-probe-" not in text
    assert "body-0" not in text and "headers-0" not in text
    assert str(tmp_path) not in text


def test_curl_version_floor_enforced() -> None:
    fake = FakeCurl([ok_spec()], version_text="curl 7.74.0 (fake) libcurl/7.74.0\nFeatures: HTTP2\n")
    with pytest.raises(probe.ProbeError):
        probe.run_probe(make_config(samples=1), fake, Recorder(), lambda: "t")
    assert fake.calls == []  # 版本不达标：零请求


# ---------------------------------------------------------------- CLI


def test_cli_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        probe.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for needle in ("--url", "--samples", "--interval", "--proxy", "--http-version", "--large-asset-url"):
        assert needle in out


def _bomb_run_probe(*args: object, **kwargs: object):
    raise AssertionError("run_probe must not be reached for invalid input")


@pytest.mark.parametrize(
    "argv",
    [
        [],  # 缺 --url
        ["--url", "http://ndtool.cn/aios/download-manifest.json"],
        ["--url", "https://user:pass@ndtool.cn/aios/download-manifest.json"],
        ["--url", "https://example.com/manifest.json"],
        ["--url", "https://ndtool.cn/aios/download-manifest.json?x=1"],
        ["--url", MANIFEST_URL, "--samples", "0"],
        ["--url", MANIFEST_URL, "--samples", "51"],
        ["--url", MANIFEST_URL, "--interval", "0.1"],
        ["--url", MANIFEST_URL, "--timeout", "0.5"],
        ["--url", MANIFEST_URL, "--http-version", "3"],
        ["--url", MANIFEST_URL, "--proxy", "http://user:pass@127.0.0.1:7892"],
        ["--url", MANIFEST_URL, "--proxy", "http://127.0.0.1"],
        ["--url", MANIFEST_URL, "--large-asset-url", APK_URL],
        ["--url", MANIFEST_URL, "--large-asset-url", APK_URL, "--large-asset-sha256", "zz"],
        ["--url", MANIFEST_URL, "--large-asset-url", MANIFEST_URL, "--large-asset-sha256", APK_SHA,
         "--large-asset-size", "1000"],
    ],
)
def test_cli_invalid_input_never_probes(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    monkeypatch.setattr(probe, "run_probe", _bomb_run_probe)
    monkeypatch.setattr(probe.shutil, "which", lambda _: "X:/fake/curl.exe")
    try:
        code = probe.main(argv)
    except SystemExit as exc:  # argparse 校验错误（choices/type/required）走 sys.exit(2)
        code = int(exc.code)
    assert code == 2


def test_cli_missing_curl_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe.shutil, "which", lambda _: None)
    assert probe.main(["--url", MANIFEST_URL]) == 2


def test_cli_http2_without_capability_exits_2_zero_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    no_http2 = "curl 8.21.0 (fake) libcurl/8.21.0 Schannel\nFeatures: AsynchDNS IPv6\n"
    fake = FakeCurl([ok_spec()], version_text=no_http2)
    _install_fake_curl(monkeypatch, fake)
    code = probe.main(["--url", MANIFEST_URL, "--http-version", "2"])
    assert code == 2  # 能力门：采样前 fail-fast
    assert fake.calls == []


def test_cli_ssl_no_revoke_recorded_in_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeCurl([ok_spec()])
    _install_fake_curl(monkeypatch, fake)
    output = tmp_path / "report.json"
    code = probe.main(["--url", MANIFEST_URL, "--samples", "1", "--ssl-no-revoke", "--output", str(output)])
    assert code == 0
    assert "--ssl-no-revoke" in fake.calls[0]
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["config"]["ssl_no_revoke"] is True
    assert report["samples"][0]["ssl_no_revoke"] is True


def _install_fake_curl(monkeypatch: pytest.MonkeyPatch, fake: FakeCurl) -> None:
    monkeypatch.setattr(probe.shutil, "which", lambda _: "X:/fake/curl.exe")
    monkeypatch.setattr(probe, "RealCurlRunner", lambda path: fake)
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)


def test_cli_happy_path_exit_zero_with_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeCurl([ok_spec() for _ in range(2)])
    _install_fake_curl(monkeypatch, fake)
    output = tmp_path / "report.json"
    code = probe.main(
        ["--url", MANIFEST_URL, "--samples", "2", "--interval", "0.5",
         "--expect-content-type", "application/json", "--output", str(output)]
    )
    assert code == 0
    assert output.exists()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["summary"]["failed"] == 0 and report["exit_code"] == 0
    assert report["config"]["expect_content_type"] == "application/json"
    out = capsys.readouterr().out
    assert "samples=2ok/0failed" in out and "exit 0" in out


def test_cli_failure_samples_exit_one_with_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeCurl([ok_spec(), fail_spec(28)])
    _install_fake_curl(monkeypatch, fake)
    output = tmp_path / "report.json"
    code = probe.main(["--url", MANIFEST_URL, "--samples", "2", "--output", str(output)])
    assert code == 1  # 存在失败类别：非零退出（fail-closed 可见），证据照常写出
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["summary"]["failure_categories"] == {"timeout": 1}
    assert report["exit_code"] == 1


def test_cli_large_asset_single_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = b"x" * APK_SIZE
    fake = FakeCurl([ok_spec() for _ in range(2)] + [ok_spec(body=body)])
    _install_fake_curl(monkeypatch, fake)
    output = tmp_path / "report.json"
    code = probe.main(
        ["--url", MANIFEST_URL, "--samples", "2",
         "--large-asset-url", APK_URL, "--large-asset-sha256",
         hashlib.sha256(body).hexdigest().upper(),  # 大写 hex 也接受（归一小写比较）
         "--large-asset-size", str(APK_SIZE), "--output", str(output)]
    )
    assert code == 0
    assert len(fake.calls) == 3
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["config"]["large_asset"]["download_policy"] == "at-most-once"
    assert report["samples"][2]["asset"] == "large"
