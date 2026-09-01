"""M5-01 Search provider abstraction：多搜索源可插拔 + 计划/结果/弃用原因可记录。

- 域层：registry 构成与可用性（local 恒可用、cloud-web 未配置即 unavailable）、
  确定性；
- API：providers 视图、本地检索命中、执行记录回查、skipped 弃用原因、
  未知搜索源、空白/长度/limit 边界 422、记录 404、无 DB 503。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.search.providers import build_search_registry

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _fake_settings(**overrides: object):
    class S:
        search_provider = "auto"
        search_cloud_endpoint = None
        search_cloud_api_key = None
    s = S()
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


# ---------- 域层 ----------


def test_registry_has_local_corpus_enabled() -> None:
    """local-corpus 恒可用：enabled=True、kind=local-corpus。"""
    registry = build_search_registry(None, _fake_settings())
    names = [e.name for e in registry]
    assert names == ["local-corpus", "cloud-web"]  # 可插拔：追加源即改这一处
    local = registry[0]
    assert local.enabled is True
    assert local.kind == "local-corpus"
    assert local.unavailable_reason is None


def test_cloud_web_unavailable_without_config() -> None:
    """未配置 cloud-web：enabled=False 且 unavailable_reason 非空（不虚报可用）。"""
    registry = build_search_registry(None, _fake_settings())
    cloud = next(e for e in registry if e.name == "cloud-web")
    assert cloud.enabled is False
    assert cloud.kind == "web"
    assert cloud.unavailable_reason  # 弃用原因可见


def test_registry_deterministic() -> None:
    """同输入恒同 registry 配置面（provider 实例每次新建，故比较投影）。"""
    def view(registry):
        return [(e.name, e.kind, e.enabled, e.unavailable_reason) for e in registry]
    assert view(build_search_registry(None, _fake_settings())) == view(build_search_registry(None, _fake_settings()))


# ---------- API ----------


def _client():
    return TestClient(create_app(SQLITE_URL))


def _seed_corpus(client) -> None:
    """上传 JSON 语料并解析落 chunks，供本地检索命中。"""
    payload = '[{"question": "正弦定理的内容", "answer": "a/sinA=b/sinB"}]'.encode()
    uploaded = client.post("/api/v1/resources/upload", files={"file": ("doc.json", payload, "application/json")}).json()
    parsed = client.post(f"/api/v1/resources/{uploaded['id']}/parse")
    assert parsed.status_code == 200


def test_list_providers() -> None:
    """providers 视图：local enabled；cloud-web 未配置带原因。"""
    with _client() as client:
        body = client.get("/api/v1/search/providers").json()
        assert [e["name"] for e in body["items"]] == ["local-corpus", "cloud-web"]
        local = body["items"][0]
        assert local["enabled"] is True and local["kind"] == "local-corpus"
        cloud = body["items"][1]
        assert cloud["enabled"] is False and cloud["unavailable_reason"]


def test_local_search_hit_and_record() -> None:
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
            {"provider": "cloud-web", "reason": "SEARCH_CLOUD_ENDPOINT/SEARCH_CLOUD_API_KEY 未配置"}
        ]
        # 记录回查：query_id 全字段可审计
        record = client.get(f"/api/v1/search/queries/{body['query_id']}").json()
        assert record["query"] == "正弦定理"
        assert record["providers_requested"] == ["local-corpus", "cloud-web"]
        assert record["result_count"] == body["result_count"]
        assert record["results"] == body["results"]
        assert record["providers_skipped"] == body["skipped"]
        assert record["created_at"]


def test_unknown_provider_skipped() -> None:
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


def test_limit_truncates_results() -> None:
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


def test_limit_bounds_422() -> None:
    """limit 0 与超上限 422；空白查询 422。"""
    with _client() as client:
        assert client.post("/api/v1/search/queries", json={"query": "词", "limit": 0}).status_code == 422
        assert client.post("/api/v1/search/queries", json={"query": "词", "limit": 51}).status_code == 422
        assert client.post("/api/v1/search/queries", json={"query": "   "}).status_code == 422


def test_record_404() -> None:
    with _client() as client:
        assert client.get("/api/v1/search/queries/99999").status_code == 404


def test_no_db_503() -> None:
    with TestClient(create_app(None)) as client:
        assert client.get("/api/v1/search/providers").status_code == 503
        assert client.post("/api/v1/search/queries", json={"query": "词"}).status_code == 503
        assert client.get("/api/v1/search/queries/1").status_code == 503
