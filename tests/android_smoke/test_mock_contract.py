"""ReadOnlyMockContract 测试：路由、脱敏日志与请求统计（不启动服务器）。"""
from __future__ import annotations

import json

from tools.android_smoke.mock_contract import (
    _PAPERS,
    AUDIT,
    OPS_SNAPSHOT,
    PROVIDERS,
    VERSION,
    VOICE_PROVIDERS,
    ReadOnlyMockContract,
)


def _post(contract: ReadOnlyMockContract, path: str, payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return contract.handle("POST", path, body)


# ---------- 路由 ----------

def test_get_health():
    c = ReadOnlyMockContract()
    status, payload = c.handle("GET", "/health")
    assert status == 200
    assert payload["status"] == "ok"


def test_get_auth_status():
    c = ReadOnlyMockContract()
    status, payload = c.handle("GET", "/api/v1/auth/status")
    assert status == 200
    assert payload == {"auth_enabled": False}


def test_get_privacy():
    c = ReadOnlyMockContract()
    status, payload = c.handle("GET", "/api/v1/system/privacy")
    assert status == 200
    assert payload["store_audio"] is False
    assert payload["send_context_to_cloud"] is False


def test_get_search_providers():
    c = ReadOnlyMockContract()
    status, payload = c.handle("GET", "/api/v1/search/providers")
    assert status == 200
    assert payload == PROVIDERS


def test_post_search_plan_echoes_query():
    c = ReadOnlyMockContract()
    status, payload = _post(c, "/api/v1/search/plan", {"query": "2024 高等数学 选择题"})
    assert status == 200
    assert payload["query"] == "2024 高等数学 选择题"
    assert payload["slots"]["school"] == "清华大学"


def test_post_search_queries_fixed_id_and_results():
    c = ReadOnlyMockContract()
    status, payload = _post(c, "/api/v1/search/queries", {"query": "whatever"})
    assert status == 200
    assert payload["query_id"] == 9001
    assert payload["result_count"] == len(payload["results"]) == 2
    assert payload["results"][0]["url"].startswith("https://localhost/m12-04/")


def test_get_search_query_9001():
    c = ReadOnlyMockContract()
    status, payload = c.handle("GET", "/api/v1/search/queries/9001")
    assert status == 200
    assert payload["id"] == 9001
    assert payload["result_count"] == 2


def test_get_version_and_ops_snapshot():
    c = ReadOnlyMockContract()
    assert c.handle("GET", "/api/v1/version") == (200, VERSION)
    assert c.handle("GET", "/api/v1/system/ops-snapshot") == (200, OPS_SNAPSHOT)


def test_get_audit_requires_limit_100():
    c = ReadOnlyMockContract()
    status, payload = c.handle("GET", "/api/v1/audit?limit=100")
    assert status == 200
    assert payload == AUDIT
    # 非 limit=100 一律 404
    assert c.handle("GET", "/api/v1/audit?limit=50")[0] == 404
    assert c.handle("GET", "/api/v1/audit")[0] == 404


# ---------- M13-05 papers 端点 ----------

def test_get_papers_returns_array():
    c = ReadOnlyMockContract()
    status, payload = c.handle("GET", "/api/v1/papers")
    assert status == 200
    assert isinstance(payload, list)
    assert len(payload) == 3
    first = payload[0]
    assert first["id"] == "paper-001"
    assert first["title"] == "Attention Is All You Need"
    assert first["subtitle"] == "Vaswani et al., NeurIPS 2017"
    assert first["source"] == "NeurIPS"
    assert first["subject"] == "Machine Learning"
    assert first["difficulty"] == "medium"
    assert first["duration_minutes"] == 45
    assert first["tags"] == ["transformer", "attention", "NLP"]
    assert first["origin_url"] == "https://arxiv.org/abs/1706.03762"
    # nullable fields present
    assert first["university"] is None
    assert first["year"] == 2017


def test_post_papers_404():
    """papers 端点只读：POST 应返回 404。"""
    c = ReadOnlyMockContract()
    assert c.handle("POST", "/api/v1/papers", b"{}")[0] == 404


# ---------- M13-07 voice providers 端点 ----------

def test_get_voice_providers_exact_fixture():
    """GET /api/v1/voice/providers 返回与 Android VoiceProvidersResponse
    逐字段精确相等的确定性快照（不多不少）。"""
    c = ReadOnlyMockContract()
    status, payload = c.handle("GET", "/api/v1/voice/providers")
    assert status == 200
    assert payload == VOICE_PROVIDERS
    assert payload == {
        "voice_mode": "hybrid",
        "asr": {"requested": None, "provider": "fake", "fallback": False},
        "tts": {
            "requested": "cloud-openai-tts",
            "provider": "tone",
            "fallback": True,
        },
        "privacy_store_audio": False,
        "privacy_send_context_to_cloud": True,
    }


def test_voice_providers_write_methods_404():
    """voice providers 端点只读：写方法一律 404。"""
    c = ReadOnlyMockContract()
    assert c.handle("POST", "/api/v1/voice/providers", b"{}")[0] == 404
    assert c.handle("PUT", "/api/v1/voice/providers", b"{}")[0] == 404
    assert c.handle("PATCH", "/api/v1/voice/providers", b"{}")[0] == 404
    assert c.handle("DELETE", "/api/v1/voice/providers")[0] == 404


def test_other_voice_paths_404():
    """其余 voice 端点（token/sessions/transcribe/synthesize/trace）
    不在共享契约内，写方法与 GET 形式一律 404。"""
    c = ReadOnlyMockContract()
    for method, path in [
        ("POST", "/api/v1/voice/token"),
        ("POST", "/api/v1/voice/sessions"),
        ("GET", "/api/v1/voice/sessions"),
        ("GET", "/api/v1/voice/sessions/vs-mock-001"),
        ("POST", "/api/v1/voice/transcribe"),
        ("POST", "/api/v1/voice/synthesize"),
        ("POST", "/api/v1/voice/trace"),
        ("GET", "/api/v1/voice/trace/summary"),
    ]:
        assert c.handle(method, path, b"{}")[0] == 404, (method, path)


# ---------- query 忽略值（非 audit 端点查询串不影响路由） ----------

def test_query_string_ignored_on_fixed_endpoints():
    c = ReadOnlyMockContract()
    assert c.handle("GET", "/health?x=1&limit=50")[0] == 200
    assert c.handle("GET", "/api/v1/version?t=abc")[0] == 200
    assert c.handle("GET", "/api/v1/search/queries/9001?refresh=1")[0] == 200


# ---------- 未知路径 / 写方法 404 ----------

def test_unknown_path_404():
    c = ReadOnlyMockContract()
    assert c.handle("GET", "/api/v1/unknown")[0] == 404


def test_write_methods_404():
    c = ReadOnlyMockContract()
    assert c.handle("POST", "/api/v1/version", b"{}")[0] == 404
    assert c.handle("PUT", "/api/v1/system/ops-snapshot", b"{}")[0] == 404
    assert c.handle("DELETE", "/api/v1/audit/1001")[0] == 404
    assert c.handle("PATCH", "/health", b"{}")[0] == 404
    # 搜索写端点之外的 POST 也 404
    assert c.handle("POST", "/health", b"{}")[0] == 404


# ---------- 日志脱敏：不含 body / header / token ----------

def test_requests_log_contains_no_body_header_or_token():
    c = ReadOnlyMockContract()
    secret_body = json.dumps({"query": "tok_sk-SECRET_QUERY", "token": "tok_sk-SECRET"}).encode()
    c.handle("POST", "/api/v1/search/queries", secret_body)
    c.handle("GET", "/api/v1/auth/status")
    entries = c.requests
    assert len(entries) == 2
    for entry in entries:
        assert set(entry) == {"timestamp_utc", "method", "path", "query_keys", "body_len"}
        dumped = json.dumps(entry, ensure_ascii=False)
        assert "SECRET" not in dumped
        assert "tok_sk" not in dumped
        assert "header" not in dumped
        assert "Authorization" not in dumped
    assert entries[0]["body_len"] == len(secret_body)
    assert entries[1]["body_len"] == 0


# ---------- query_keys：只留参数名，不留取值 ----------

def test_query_keys_recorded_without_values():
    c = ReadOnlyMockContract()
    c.handle("GET", "/api/v1/audit?limit=100")
    c.handle("GET", "/health")
    entries = c.requests
    assert entries[0]["query_keys"] == ("limit",)
    assert entries[1]["query_keys"] == ()
    # path 已去 query，取值不出现在任何字段
    assert entries[0]["path"] == "/api/v1/audit"
    assert "limit=100" not in json.dumps(entries[0], ensure_ascii=False)


def test_requests_property_returns_copy():
    c = ReadOnlyMockContract()
    c.handle("GET", "/health")
    snapshot = c.requests
    snapshot.append({"injected": True})
    assert len(c.requests) == 1


# ---------- stats() ----------

def test_stats_counts():
    c = ReadOnlyMockContract()
    body = b'{"query": "q"}'
    c.handle("GET", "/health")
    c.handle("GET", "/health")
    c.handle("POST", "/api/v1/search/queries", body)
    c.handle("GET", "/api/v1/audit?limit=100")
    c.handle("GET", "/health?x=1&limit=50")
    stats = c.stats()
    assert stats["total"] == 5
    assert stats["body_len_total"] == len(body)
    # 统计键使用去 query 后的 path，query 取值不得进入键
    assert stats["by_method_path"]["GET /health"] == 3
    assert stats["by_method_path"]["POST /api/v1/search/queries"] == 1
    assert stats["by_method_path"]["GET /api/v1/audit"] == 1
    assert "GET /api/v1/audit?limit=100" not in stats["by_method_path"]
    for key in stats["by_method_path"]:
        assert "?" not in key
        assert "=" not in key
