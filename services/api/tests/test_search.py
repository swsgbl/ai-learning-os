"""M5-01 Search provider abstraction：多搜索源可插拔 + 计划/结果/弃用原因可记录。

M10-12 追加：cloud-web 真实 SearXNG-compatible 实现（MockTransport，不触网）——
- 域层：registry 构成与可用性矩阵（local 恒可用；cloud-web 受
  PRIVACY_SEND_CONTEXT_TO_CLOUD 总闸 / SEARCH_MODE 路由 / endpoint 合法性三门，
  key 可选不参与判定）；
- provider：成功响应归一化（snippet=content、authority 白名单透传、默认
  community）、limit 截断、缺失/非法 URL 过滤、非 2xx / 非法 JSON / results
  非列表 / 网络/超时 fail-closed、Authorization 仅 key 存在时添加、
  错误与 provider 视图零敏感泄漏；
- API：providers 视图、本地检索命中、执行记录回查、skipped 弃用原因、
  未知搜索源、空白/长度/limit 边界 422、记录 404、无 DB 503。
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.search.providers import (
    CloudWebProvider,
    ProviderUnavailable,
    build_search_registry,
)

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _fake_settings(**overrides: object):
    class S:
        search_provider = "auto"
        search_mode = "local"  # 与 Settings 默认一致
        privacy_send_context_to_cloud = True
        search_cloud_endpoint = None
        search_cloud_api_key = None

    s = S()
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


# ---------- 域层：registry 可用性矩阵 ----------


def test_registry_has_local_corpus_enabled() -> None:
    """local-corpus 恒可用：enabled=True、kind=local-corpus。"""
    registry = build_search_registry(None, _fake_settings())
    names = [e.name for e in registry]
    assert names == ["local-corpus", "cloud-web"]  # 可插拔：追加源即改这一处
    local = registry[0]
    assert local.enabled is True
    assert local.kind == "local-corpus"
    assert local.unavailable_reason is None


def test_cloud_web_disabled_in_local_mode_even_when_configured() -> None:
    """SEARCH_MODE=local：即使 endpoint/key 配齐也禁用（本地路由不出站）。"""
    registry = build_search_registry(
        None,
        _fake_settings(
            search_cloud_endpoint="https://searxng.example.invalid",
            search_cloud_api_key="some-key",
        ),
    )
    cloud = next(e for e in registry if e.name == "cloud-web")
    assert cloud.enabled is False
    assert cloud.kind == "web"
    assert "SEARCH_MODE=local" in cloud.unavailable_reason


def test_cloud_web_enabled_when_configured() -> None:
    """SEARCH_MODE=cloud + 隐私总闸放行 + endpoint 合法：enabled（key 可选）。"""
    for key in (None, "some-key"):
        registry = build_search_registry(
            None,
            _fake_settings(
                search_mode="cloud",
                search_cloud_endpoint="https://searxng.example.invalid",
                search_cloud_api_key=key,
            ),
        )
        cloud = next(e for e in registry if e.name == "cloud-web")
        assert cloud.enabled is True, "key 可选：无 key 也应启用"
        assert cloud.unavailable_reason is None


def test_cloud_web_disabled_by_privacy_gate() -> None:
    """PRIVACY_SEND_CONTEXT_TO_CLOUD=false：隐私总闸关闭，endpoint 配齐也不出站。"""
    registry = build_search_registry(
        None,
        _fake_settings(
            search_mode="cloud",
            privacy_send_context_to_cloud=False,
            search_cloud_endpoint="https://searxng.example.invalid",
            search_cloud_api_key="some-key",
        ),
    )
    cloud = next(e for e in registry if e.name == "cloud-web")
    assert cloud.enabled is False
    assert "PRIVACY_SEND_CONTEXT_TO_CLOUD" in cloud.unavailable_reason


def test_cloud_web_disabled_when_endpoint_missing_or_invalid() -> None:
    """endpoint 缺失/非法：禁用且原因不含 endpoint 值。"""
    missing = build_search_registry(None, _fake_settings(search_mode="cloud"))
    cloud = next(e for e in missing if e.name == "cloud-web")
    assert cloud.enabled is False
    assert cloud.unavailable_reason == "SEARCH_CLOUD_ENDPOINT 未配置"

    # (非法 endpoint 值, 断言原因不得出现的子串)
    invalid_cases = [
        ("not-a-url-marker-12345", "marker-12345"),
        ("ftp://searxng.example.invalid", "searxng.example.invalid"),
        ("http://", None),
        ("  ", None),
    ]
    for endpoint, forbidden in invalid_cases:
        registry = build_search_registry(
            None, _fake_settings(search_mode="cloud", search_cloud_endpoint=endpoint)
        )
        cloud = next(e for e in registry if e.name == "cloud-web")
        assert cloud.enabled is False, f"endpoint={endpoint!r} 应判非法"
        assert "SEARCH_CLOUD_ENDPOINT" in cloud.unavailable_reason
        if forbidden:
            assert forbidden not in (cloud.unavailable_reason or "")


def test_registry_deterministic() -> None:
    """同输入恒同 registry 配置面（provider 实例每次新建，故比较投影）。"""

    def view(registry):
        return [(e.name, e.kind, e.enabled, e.unavailable_reason) for e in registry]

    assert view(build_search_registry(None, _fake_settings())) == view(
        build_search_registry(None, _fake_settings())
    )


# ---------- 域层：CloudWebProvider 真实链路（MockTransport，不触网）----------


def _provider(transport: httpx.BaseTransport, **kwargs) -> CloudWebProvider:
    return CloudWebProvider(
        endpoint=kwargs.pop("endpoint", "https://searxng.example.invalid"),
        transport=transport,
        **kwargs,
    )


def test_cloud_web_search_success_normalizes() -> None:
    """成功响应归一化：q/format=json 请求、snippet=content、authority 白名单。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "Result A", "url": "https://a.example/page", "content": "正文摘要"},
                    {"title": "Result B", "url": "https://b.example/x", "content": "内容 B", "authority": "official"},
                    {"title": "Result C", "url": "https://c.example/x", "content": "内容 C", "authority": "bogus-tier"},
                ]
            },
        )

    async def body():
        return await _provider(httpx.MockTransport(handler)).search("极限", 10, owner="user-1")

    results = asyncio.run(body())
    # 请求形态：GET {base}/search，query 参数 q 与 format=json
    assert captured["request"].method == "GET"
    assert captured["request"].url.path == "/search"
    assert captured["request"].url.params["q"] == "极限"
    assert captured["request"].url.params["format"] == "json"
    assert "Authorization" not in captured["request"].headers  # 无 key 不带鉴权头
    # 归一化：SearchResultItem 契约（rank_reason 由 rank_and_dedup 补）
    assert results == [
        {
            "title": "Result A",
            "url": "https://a.example/page",
            "snippet": "正文摘要",
            "source": "cloud-web",
            "provider": "cloud-web",
            "authority": "community",  # 未标注兜底
        },
        {
            "title": "Result B",
            "url": "https://b.example/x",
            "snippet": "内容 B",
            "source": "cloud-web",
            "provider": "cloud-web",
            "authority": "official",  # 受控白名单标注透传
        },
        {
            "title": "Result C",
            "url": "https://c.example/x",
            "snippet": "内容 C",
            "source": "cloud-web",
            "provider": "cloud-web",
            "authority": "community",  # 未知标注兜底，不猜测
        },
    ]


def test_cloud_web_search_limit_truncates() -> None:
    """limit 截断：返回条数不超过 limit（取前 limit 条）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": f"R{i}", "url": f"https://x.example/{i}", "content": "c"}
                    for i in range(5)
                ]
            },
        )

    async def body():
        return await _provider(httpx.MockTransport(handler)).search("词", 3)

    results = asyncio.run(body())
    assert [r["title"] for r in results] == ["R0", "R1", "R2"]


def test_cloud_web_filters_invalid_result_urls() -> None:
    """缺失/非法 URL 的结果被过滤；合法 http/https 保留。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "ok-http", "url": "http://plain.example/x", "content": "c"},
                    {"title": "missing-url", "content": "c"},
                    {"title": "empty-url", "url": "", "content": "c"},
                    {"title": "non-string-url", "url": 42, "content": "c"},
                    {"title": "ftp-url", "url": "ftp://x.example/f", "content": "c"},
                    {"title": "js-url", "url": "javascript:alert(1)", "content": "c"},
                    {"title": "no-host", "url": "https:///path-only", "content": "c"},
                    {"title": "ok-https", "url": "https://ok.example/y", "content": "c"},
                    "not-a-dict",
                ]
            },
        )

    async def body():
        return await _provider(httpx.MockTransport(handler)).search("词", 10)

    results = asyncio.run(body())
    assert [r["url"] for r in results] == [
        "http://plain.example/x",
        "https://ok.example/y",
    ]


def test_cloud_web_http_error_fails_closed() -> None:
    """HTTP 非 2xx：ProviderUnavailable（固定脱敏文案，含状态码）。"""
    for status in (500, 404, 302):
        def handler(request: httpx.Request, status=status) -> httpx.Response:
            return httpx.Response(status)

        async def body():
            return await _provider(httpx.MockTransport(handler)).search("词", 5)

        with pytest.raises(ProviderUnavailable, match=f"HTTP {status}"):
            asyncio.run(body())


def test_cloud_web_malformed_json_fails_closed() -> None:
    """响应非合法 JSON：ProviderUnavailable。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json{{{", headers={"Content-Type": "application/json"})

    async def body():
        return await _provider(httpx.MockTransport(handler)).search("词", 5)

    with pytest.raises(ProviderUnavailable, match="不是合法 JSON"):
        asyncio.run(body())


def test_cloud_web_results_not_list_fails_closed() -> None:
    """results 非列表 / 缺失 / 顶层非对象：ProviderUnavailable。"""
    for payload in ({"results": {"a": 1}}, {"unrelated": []}, [1, 2], "string"):
        def handler(request: httpx.Request, payload=payload) -> httpx.Response:
            return httpx.Response(200, json=payload)

        async def body():
            return await _provider(httpx.MockTransport(handler)).search("词", 5)

        with pytest.raises(ProviderUnavailable, match="results"):
            asyncio.run(body())


def test_cloud_web_network_and_timeout_fail_closed() -> None:
    """网络错误 / 超时：ProviderUnavailable。"""
    for error in (httpx.ConnectError("boom"), httpx.ConnectTimeout("t"), httpx.ReadTimeout("t")):
        def handler(request: httpx.Request, error=error) -> httpx.Response:
            raise error

        async def body():
            return await _provider(httpx.MockTransport(handler)).search("词", 5)

        with pytest.raises(ProviderUnavailable, match="网络错误或超时"):
            asyncio.run(body())


def test_cloud_web_authorization_header_only_when_key_present() -> None:
    """Authorization: Bearer 只在 key 存在时添加；无 key 支持无鉴权调用。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.setdefault("auth", []).append(request.headers.get("Authorization"))
        return httpx.Response(200, json={"results": []})

    async def with_key() -> None:
        await _provider(httpx.MockTransport(handler), api_key="test-key-123").search("词", 5)

    async def without_key() -> None:
        await _provider(httpx.MockTransport(handler)).search("词", 5)

    asyncio.run(with_key())
    asyncio.run(without_key())
    assert captured["auth"] == ["Bearer test-key-123", None]


# 敏感 marker：endpoint / key 值绝不出现在错误消息与 provider 视图里
SECRET_ENDPOINT = "https://secret-endpoint-marker.invalid/search?token=marker-endpoint-token"
SECRET_KEY = "sk-marker-key-1234567890abcdef"
SENSITIVE_MARKERS = (
    "secret-endpoint-marker",
    "marker-endpoint-token",
    "sk-marker-key-1234567890abcdef",
    "Authorization",
    "Bearer",
)


def test_cloud_web_failures_leak_no_sensitive_values() -> None:
    """全部失败形态的错误消息零敏感泄漏（不含 endpoint/key/鉴权头形态）。"""
    def network_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connect failed for {SECRET_ENDPOINT}")

    cases: dict[str, httpx.MockTransport] = {
        "http-500": httpx.MockTransport(lambda request: httpx.Response(500)),
        "malformed-json": httpx.MockTransport(lambda request: httpx.Response(200, text="not-json")),
        "results-not-list": httpx.MockTransport(lambda request: httpx.Response(200, json={"results": 3})),
        "network-error": httpx.MockTransport(network_error),
    }
    for name, transport in cases.items():
        async def body(transport=transport):
            return await CloudWebProvider(
                endpoint=SECRET_ENDPOINT, api_key=SECRET_KEY, transport=transport
            ).search("词", 5)

        with pytest.raises(ProviderUnavailable) as excinfo:
            asyncio.run(body())
        message = str(excinfo.value)
        for marker in SENSITIVE_MARKERS:
            assert marker not in message, f"{name} 失败消息泄漏 {marker!r}: {message}"


def test_registry_reason_leaks_no_sensitive_values() -> None:
    """endpoint 非法（含 marker 值）时 registry 视图原因零敏感泄漏。"""
    registry = build_search_registry(
        None,
        _fake_settings(
            search_mode="cloud",
            privacy_send_context_to_cloud=False,
            search_cloud_endpoint="https://secret-endpoint-marker.invalid",
            search_cloud_api_key=SECRET_KEY,
        ),
    )
    cloud = next(e for e in registry if e.name == "cloud-web")
    assert cloud.enabled is False
    for marker in SENSITIVE_MARKERS:
        assert marker not in (cloud.unavailable_reason or "")


def test_cloud_web_unconfigured_search_fails_closed() -> None:
    """endpoint 缺失时直接 search（绕过 registry）：防御性 fail-closed。"""

    async def body():
        return await CloudWebProvider(endpoint=None).search("词", 5)

    with pytest.raises(ProviderUnavailable, match="SEARCH_CLOUD_ENDPOINT"):
        asyncio.run(body())


# ---------- API ----------


@pytest.fixture()
def local_search_env(monkeypatch):
    """固化本地检索路由环境：SEARCH_MODE=local、cloud 槽位清空。"""
    monkeypatch.delenv("SEARCH_CLOUD_ENDPOINT", raising=False)
    monkeypatch.delenv("SEARCH_CLOUD_API_KEY", raising=False)
    monkeypatch.setenv("SEARCH_MODE", "local")
    monkeypatch.setenv("PRIVACY_SEND_CONTEXT_TO_CLOUD", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def cloud_search_env(monkeypatch):
    """cloud 路由 + 合法 endpoint（.invalid TDS 保证永不解析）：视图/计划不触网。"""
    monkeypatch.setenv("SEARCH_MODE", "cloud")
    monkeypatch.setenv("PRIVACY_SEND_CONTEXT_TO_CLOUD", "true")
    monkeypatch.setenv("SEARCH_CLOUD_ENDPOINT", "https://searxng.example.invalid")
    monkeypatch.delenv("SEARCH_CLOUD_API_KEY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client():
    return TestClient(create_app(SQLITE_URL))


def _seed_corpus(client) -> None:
    """上传 JSON 语料并解析落 chunks，供本地检索命中。"""
    payload = '[{"question": "正弦定理的内容", "answer": "a/sinA=b/sinB"}]'.encode()
    uploaded = client.post("/api/v1/resources/upload", files={"file": ("doc.json", payload, "application/json")}).json()
    parsed = client.post(f"/api/v1/resources/{uploaded['id']}/parse")
    assert parsed.status_code == 200


def test_list_providers(local_search_env) -> None:
    """providers 视图：local enabled；SEARCH_MODE=local 下 cloud-web 禁用带原因。"""
    with _client() as client:
        body = client.get("/api/v1/search/providers").json()
        assert [e["name"] for e in body["items"]] == ["local-corpus", "cloud-web"]
        local = body["items"][0]
        assert local["enabled"] is True and local["kind"] == "local-corpus"
        cloud = body["items"][1]
        assert cloud["enabled"] is False and "SEARCH_MODE=local" in cloud["unavailable_reason"]


def test_cloud_web_enabled_view_when_configured(cloud_search_env) -> None:
    """cloud 路由 + endpoint 配置：视图与计划如实 enabled（不虚报禁用）。"""
    with _client() as client:
        body = client.get("/api/v1/search/providers").json()
        cloud = body["items"][1]
        assert cloud["enabled"] is True
        assert cloud["unavailable_reason"] is None
        # plan 只读 registry（不执行查询，不触网）：enabled 源计划不带原因
        plan = client.post("/api/v1/search/plan", json={"query": "极限"}).json()
        cloud_plan = next(p for p in plan["plan"] if p["provider"] == "cloud-web")
        assert cloud_plan["enabled"] is True
        assert cloud_plan["unavailable_reason"] is None


def test_local_search_hit_and_record(local_search_env) -> None:
    """本地检索命中：结果字段齐全、provider 标注、执行记录落盘可回查。"""
    with _client() as client:
        _seed_corpus(client)
        r = client.post("/api/v1/search/queries", json={"query": "正弦定理"})
        assert r.status_code == 200
        body = r.json()
        assert body["providers_requested"] == ["local-corpus", "cloud-web"]
        assert body["result_count"] >= 1
        assert body["results"][0]["provider"] == "local-corpus"
        assert "正弦定理" in body["results"][0]["snippet"]
        assert body["skipped"] == [
            {"provider": "cloud-web", "reason": "SEARCH_MODE=local：检索路由本地语料，cloud-web 不出站"}
        ]
        # 记录回查：query_id 全字段可审计
        record = client.get(f"/api/v1/search/queries/{body['query_id']}").json()
        assert record["query"] == "正弦定理"
        assert record["providers_requested"] == ["local-corpus", "cloud-web"]
        assert record["result_count"] == body["result_count"]
        assert record["results"] == body["results"]
        assert record["providers_skipped"] == body["skipped"]
        assert record["created_at"]


def test_unknown_provider_skipped(local_search_env) -> None:
    """未知搜索源进 skipped 记原因，记录仍落盘（不虚报）。"""
    with _client() as client:
        r = client.post(
            "/api/v1/search/queries",
            json={"query": "任意", "providers": ["no-such"]},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["skipped"] == [{"provider": "no-such", "reason": "未知搜索源"}]
        assert body["result_count"] == 0
        record = client.get(f"/api/v1/search/queries/{body['query_id']}").json()
        assert record["result_count"] == 0


def test_limit_truncates_results(local_search_env) -> None:
    """limit 截断：结果数不超过 limit。"""
    with _client() as client:
        for _ in range(4):
            _seed_corpus(client)
        r = client.post(
            "/api/v1/search/queries",
            json={"query": "正弦定理", "limit": 2},
        )
        assert r.status_code == 200
        assert len(r.json()["results"]) <= 2


def test_limit_bounds_422(local_search_env) -> None:
    """limit 0 与超上限 422；空白查询 422。"""
    with _client() as client:
        assert client.post("/api/v1/search/queries", json={"query": "词", "limit": 0}).status_code == 422
        assert client.post("/api/v1/search/queries", json={"query": "词", "limit": 51}).status_code == 422
        assert client.post("/api/v1/search/queries", json={"query": "   "}).status_code == 422


def test_record_404(local_search_env) -> None:
    with _client() as client:
        assert client.get("/api/v1/search/queries/99999").status_code == 404


def test_no_db_503() -> None:
    with TestClient(create_app(None)) as client:
        assert client.get("/api/v1/search/providers").status_code == 503
        assert client.post("/api/v1/search/queries", json={"query": "词"}).status_code == 503
        assert client.get("/api/v1/search/queries/1").status_code == 503
