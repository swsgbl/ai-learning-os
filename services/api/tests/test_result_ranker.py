"""M5-04 Result rank：去重 + 官方>OER>平台>社区分层排序，理由可见。

- 域层：分层顺序、同层稳定（保 provider 原序）、去重保高层级/先出现、
  URL 规范化归并、未标注 community 兜底明示、空 URL 不归并、确定性；
- API：/queries 返回带 authority + rank_reason、排序后截断 limit、
  执行记录回查仍带排序理由。
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.domain.result_ranker import SOURCE_TIERS, normalize_url, rank_and_dedup
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _result(url: str, authority: str | None = None, title: str = "t") -> dict:
    item = {
        "title": title,
        "url": url,
        "snippet": "s",
        "source": "prov-x",
        "provider": "prov-x",
    }
    if authority is not None:
        item["authority"] = authority
    return item


def test_tier_ordering() -> None:
    """四层乱序输入 → 输出 official > oer > platform > community，理由含标注。"""
    results = [
        _result("https://a.example/1", "community"),
        _result("https://a.example/2", "official"),
        _result("https://a.example/3", "oer"),
        _result("https://a.example/4", "platform"),
    ]
    ranked = rank_and_dedup(results)
    assert [r["url"] for r in ranked] == [
        "https://a.example/2",
        "https://a.example/3",
        "https://a.example/4",
        "https://a.example/1",
    ]
    assert ranked[0]["rank_reason"] == "来源标注 official(1)"
    assert ranked[1]["rank_reason"] == "来源标注 oer(2)"
    assert ranked[2]["rank_reason"] == "来源标注 platform(3)"
    assert ranked[3]["rank_reason"] == "来源标注 community(4)"


def test_same_tier_stable() -> None:
    """同层内保持输入顺序（稳定排序，确定性）。"""
    results = [
        _result("https://a.example/c", "community", "first"),
        _result("https://a.example/a", "community", "second"),
        _result("https://a.example/b", "community", "third"),
    ]
    ranked = rank_and_dedup(results)
    assert [r["title"] for r in ranked] == ["first", "second", "third"]


def test_dedup_higher_tier_wins() -> None:
    """同 URL 双条（community 先、official 后）→ 保留 official，理由含去重说明。"""
    results = [
        _result("https://dup.example/x", "community", "lower"),
        _result("https://dup.example/x", "official", "higher"),
    ]
    ranked = rank_and_dedup(results)
    assert len(ranked) == 1
    assert ranked[0]["title"] == "higher"
    assert "重复" in ranked[0]["rank_reason"]


def test_dedup_same_tier_first_wins() -> None:
    """同层同 URL：保留先出现者，理由含去重说明。"""
    results = [
        _result("https://dup.example/x", "oer", "first"),
        _result("https://dup.example/x", "oer", "second"),
    ]
    ranked = rank_and_dedup(results)
    assert len(ranked) == 1
    assert ranked[0]["title"] == "first"
    assert "重复" in ranked[0]["rank_reason"]
def test_normalize_url() -> None:
    """规范化：小写 scheme/host、去 fragment、去末尾斜杠、保留 query。"""
    assert normalize_url("HTTPS://Example.COM/Path/#frag") == "https://example.com/Path"
    assert normalize_url("https://a.com/x?y=1") == "https://a.com/x?y=1"
    assert normalize_url("https://a.com/x/") == "https://a.com/x"
    assert normalize_url("/api/v1/resources/1/chunks/0") == ":///api/v1/resources/1/chunks/0"


def test_unannotated_fallback_community() -> None:
    """无 authority 无层级 source：community 兜底，理由明示未标注（不虚报）。"""
    ranked = rank_and_dedup([_result("https://plain.example/z")])
    assert len(ranked) == 1
    assert ranked[0]["rank_reason"] == "未标注权威层级，按 community(4) 兜底排序"
    assert "authority" not in ranked[0]


def test_unknown_authority_tag_falls_back() -> None:
    """未知 authority 标签（不在四层内）：同样 community 兜底，不猜测。"""
    ranked = rank_and_dedup([_result("https://x.example/a", "WIKI")])
    assert ranked[0]["rank_reason"].startswith("未标注权威层级")


def test_empty_url_not_merged() -> None:
    """空 URL 条目不参与去重（每条独立保留）。"""
    results = [_result("", "official"), _result("", "official")]
    ranked = rank_and_dedup(results)
    assert len(ranked) == 2


def test_all_rank_reasons_nonempty() -> None:
    """任意混合输入：每条 rank_reason 非空且含层级名。"""
    results = [
        _result("https://a.example/1", "official"),
        _result("https://a.example/2"),
        _result("https://a.example/3", "oer"),
        _result("", "platform"),
    ]
    ranked = rank_and_dedup(results)
    for item in ranked:
        assert item["rank_reason"]
        assert any(tier in item["rank_reason"] for tier in SOURCE_TIERS)


def test_deterministic() -> None:
    """同输入两次排序结果完全一致（确定性）。"""
    results = [
        _result("https://a.example/1", "community"),
        _result("https://a.example/2", "official"),
        _result("https://a.example/1", "oer"),
    ]
    first = rank_and_dedup(list(results))
    second = rank_and_dedup(list(results))
    assert first == second


# ---------- API ----------


def _client():
    return TestClient(create_app(SQLITE_URL))


def _seed_corpus(client) -> None:
    """上传两个 JSON 文档（各 1 chunk，均含关键词），轮询等 worker 落 chunks。"""
    for name in ("doc1.json", "doc2.json"):
        payload = (
            '[{"question": "极限的' + name.replace(".json", "") + '的性质", "answer": "yes"}]'
        ).encode()

        uploaded = client.post(
            "/api/v1/resources/upload", files={"file": (name, payload, "application/json")}
        ).json()
        parsed = client.post(f"/api/v1/resources/{uploaded['id']}/parse")
        assert parsed.status_code == 200
    for _ in range(50):
        body = client.post(
            "/api/v1/search/queries", json={"query": "极限", "providers": ["local-corpus"]}
        ).json()
        if body["result_count"] >= 2:
            return
        time.sleep(0.1)
    pytest.fail("worker 未在限时内落齐 2 个 chunk")


def test_api_results_carry_authority_and_reason() -> None:
    """/queries 返回：结果带 authority=platform + rank_reason（理由可见）。"""
    with _client() as client:
        _seed_corpus(client)
        body = client.post(
            "/api/v1/search/queries", json={"query": "极限", "providers": ["local-corpus"]}
        ).json()
        assert body["result_count"] == 2
        assert body["results"][0]["authority"] == "platform"
        assert body["results"][0]["rank_reason"] == "来源标注 platform(3)"


def test_api_ranked_truncation_after_rank() -> None:
    """limit=1：先排序再去重后截断——2 条候选截为 1 条。"""
    with _client() as client:
        _seed_corpus(client)
        body = client.post(
            "/api/v1/search/queries", json={"query": "极限", "limit": 1}
        ).json()
        assert body["result_count"] == 1
        assert all(r["authority"] == "platform" for r in body["results"])
        assert all(r["rank_reason"] for r in body["results"])


def test_api_record_keeps_rank_reason() -> None:
    """执行记录回查：results 仍带 authority + rank_reason（审计面）。"""
    with _client() as client:
        _seed_corpus(client)
        first = client.post(
            "/api/v1/search/queries", json={"query": "极限", "providers": ["local-corpus"]}
        ).json()
        record = client.get(f"/api/v1/search/queries/{first['query_id']}").json()
        assert record["results"][0]["authority"] == "platform"
        assert record["results"][0]["rank_reason"] == "来源标注 platform(3)"
