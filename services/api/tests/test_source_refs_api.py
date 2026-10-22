"""M16-008 来源证据 API 测试（升级方案 Phase 5 验收）。

覆盖：stub provider 检索入库（url/snippet/hash）；非法 url 过滤；
概念过滤列表；registry 缺失 503 如实。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


class _StubEntry:
    """registry 条目替身（enabled + stub provider）。"""

    def __init__(self, results: list[dict] | None = None, fail: bool = False) -> None:
        self.name = "stub-web"
        self.enabled = True
        self.unavailable_reason = None
        self._results = results if results is not None else [
            {"title": "SELECT 语法", "url": "https://docs.example.com/select", "snippet": "SELECT 用于查询数据", "provider": "stub-web"},
            {"title": "JOIN 图解", "url": "https://docs.example.com/join", "snippet": "JOIN 连接两表", "provider": "stub-web"},
        ]
        self._fail = fail

    class provider:
        @staticmethod
        async def search(query: str, limit: int, owner: str | None = None) -> list[dict]:
            raise NotImplementedError


class _StubProvider:
    def __init__(self, results: list[dict], fail: bool) -> None:
        self._results = results
        self._fail = fail

    async def search(self, query: str, limit: int, owner: str | None = None) -> list[dict]:
        if self._fail:
            raise RuntimeError("provider down")
        return self._results[:limit]


def _install_registry(app, entry) -> None:
    entry.provider = _StubProvider(entry._results, entry._fail)
    app.state.search_registry = [entry]


def _client() -> TestClient:
    return TestClient(create_app(SQLITE_URL))


def _contract_id(client: TestClient) -> str:
    created = client.post(
        "/api/v1/learning/contracts",
        json={
            "title": "来源证据测试",
            "target_outcomes": [{"statement": "能写 SELECT", "criteria": "通过"}],
            "time_budget_hours": 8,
            "pass_criteria": "后测≥80",
        },
    )
    assert created.status_code == 201
    return created.json()["id"]


def test_source_search_persists_and_lists() -> None:
    with _client() as client:
        contract_id = _contract_id(client)
        _install_registry(client.app, _StubEntry())
        found = client.post(
            f"/api/v1/learning/contracts/{contract_id}/source-refs/search",
            json={"concept_id": "sql:select-basics", "limit": 2},
        )
        assert found.status_code == 201, found.text
        refs = found.json()
        assert len(refs) == 2
        assert refs[0]["url"].startswith("https://")
        assert refs[0]["license_hint"] == "unknown"  # 许可不明如实标注

        listed = client.get(
            f"/api/v1/learning/contracts/{contract_id}/source-refs",
            params={"concept_id": "sql:select-basics"},
        )
        assert listed.status_code == 200
        assert len(listed.json()) == 2


def test_invalid_urls_filtered() -> None:
    with _client() as client:
        contract_id = _contract_id(client)
        _install_registry(
            client.app,
            _StubEntry(
                results=[
                    {"title": "javascript 伪协议", "url": "javascript:alert(1)", "snippet": "x", "provider": "s"},
                    {"title": "空片段", "url": "https://ok.example.com/a", "snippet": "  ", "provider": "s"},
                    {"title": "合法", "url": "https://ok.example.com/b", "snippet": "有用片段", "provider": "s"},
                ]
            ),
        )
        found = client.post(
            f"/api/v1/learning/contracts/{contract_id}/source-refs/search",
            json={"concept_id": "sql:join"},
        )
        assert found.status_code == 201
        urls = [r["url"] for r in found.json()]
        assert urls == ["https://ok.example.com/b"]  # 非法 url/空片段过滤


def test_no_registry_503_honest() -> None:
    with _client() as client:
        contract_id = _contract_id(client)
        client.app.state.search_registry = None
        res = client.post(
            f"/api/v1/learning/contracts/{contract_id}/source-refs/search",
            json={"concept_id": "sql:join"},
        )
        assert res.status_code == 503


def test_all_providers_fail_503() -> None:
    with _client() as client:
        contract_id = _contract_id(client)
        _install_registry(client.app, _StubEntry(fail=True))
        res = client.post(
            f"/api/v1/learning/contracts/{contract_id}/source-refs/search",
            json={"concept_id": "sql:join"},
        )
        assert res.status_code == 503
