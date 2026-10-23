"""M18-001 课程目录 API 契约测试（审计 Prompt 1：幂等/差量/许可默认/检索）。

GitHub tree 抓取打桩（不真实外网）；auth-off 模式（require_admin 放行）
覆盖同步语义，auth-on 覆盖权限边界。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///./test_catalog_api.db"

_TREE_V1: list[tuple[str, str, int | None]] = [
    ("初中/数学/人教版/七年级上册.pdf", "blob-a1", 2048),
    ("初中/数学/人教版/七年级上册.pdf.1", "blob-a2", 35_000_000),
    ("初中/数学/人教版/七年级上册.pdf.2", "blob-a3", 35_000_000),
    ("大学/高等数学/同济第七版.pdf", "blob-b1", 8_000_000),
    ("README.md", "blob-r", 100),
]
_TREE_V2: list[tuple[str, str, int | None]] = [
    # 七年级上册消失；新增线性代数；高数 blob 更新
    ("大学/高等数学/同济第七版.pdf", "blob-b1-new", 8_100_000),
    ("大学/线性代数/清华版.pdf", "blob-c1", 5_000_000),
]


@pytest.fixture()
def app_client(monkeypatch: pytest.MonkeyPatch, tmp_path):
    import app.api.routes.catalog as catalog_route
    from app.core.config import get_settings

    url = f"sqlite+aiosqlite:///{tmp_path / 'catalog.db'}"
    state = {"sha": "sha-v1", "tree": _TREE_V1}

    def _fake_fetch(branch: str = "main", **_kw):
        return state["sha"], state["tree"]

    monkeypatch.setattr(catalog_route, "_fetch_tree", _fake_fetch)
    get_settings.cache_clear()

    with TestClient(create_app(url)) as client:
        yield client, state
    get_settings.cache_clear()


def test_sync_creates_records_with_metadata_only_permission(app_client) -> None:
    """首次同步：归一化条目入库 + 许可默认 metadata_only（审计 02 §3）。"""
    client, _ = app_client
    run = client.post("/api/v1/catalog/sync-runs", json={})
    assert run.status_code == 201, run.text
    counters = run.json()["counters"]
    # 3 blob → 归组 2 条目（分片组 + 单文件）：created=2
    assert counters["created"] == 2
    assert counters["unchanged"] == 0

    results = client.get("/api/v1/catalog/search", params={"q": "同济"}).json()
    assert len(results) == 1
    entry = results[0]
    assert entry["education_stage"] == "大学"
    assert entry["subject"] == "高等数学"
    assert entry["permission_status"] == "metadata_only"
    assert entry["source_url"].startswith("https://raw.githubusercontent.com/")
    assert "sha-v1" in entry["source_url"]


def test_parts_grouped_into_one_entry(app_client) -> None:
    """.pdf.1/.2 归组为单条目（parts_count=2——逻辑文件引用）。"""
    client, _ = app_client
    client.post("/api/v1/catalog/sync-runs", json={})
    results = client.get("/api/v1/catalog/search", params={"q": "七年级"}).json()
    assert len(results) == 1
    assert results[0]["parts_count"] == 2
    assert results[0]["source_path"].endswith("七年级上册.pdf")


def test_sync_idempotent_same_commit(app_client) -> None:
    """同 commit 重跑：全 unchanged（幂等——审计 02 §4.1 验收）。"""
    client, _ = app_client
    client.post("/api/v1/catalog/sync-runs", json={})
    again = client.post("/api/v1/catalog/sync-runs", json={})
    assert again.status_code == 201
    counters = again.json()["counters"]
    assert counters["created"] == 0
    assert counters["updated"] == 0
    assert counters["unchanged"] == 2
    # 不重复（provider+source_path 唯一）
    assert len(client.get("/api/v1/catalog/search").json()) == 2


def test_sync_diff_on_new_commit(app_client) -> None:
    """新 commit：新增 created / 变更 updated / 消失 unavailable（差量如实）。"""
    client, state = app_client
    client.post("/api/v1/catalog/sync-runs", json={})
    state["sha"] = "sha-v2"
    state["tree"] = _TREE_V2
    run = client.post("/api/v1/catalog/sync-runs", json={})
    counters = run.json()["counters"]
    assert counters["created"] == 1  # 线性代数
    assert counters["updated"] == 1  # 高数 blob 变
    assert counters["unchanged"] == 0
    assert counters["unavailable"] == 1  # 七年级上册组消失（不删历史）
    # 历史记录仍在（引用稳定）
    results = client.get("/api/v1/catalog/search", params={"q": "七年级"}).json()
    assert len(results) == 1


def test_search_filters(app_client) -> None:
    """学段/学科过滤。"""
    client, _ = app_client
    client.post("/api/v1/catalog/sync-runs", json={})
    uni = client.get(
        "/api/v1/catalog/search", params={"stage": "大学", "subject": "高等数学"}
    ).json()
    assert [r["title"] for r in uni] == ["同济第七版.pdf"]
    mid = client.get("/api/v1/catalog/search", params={"stage": "初中"}).json()
    assert len(mid) == 1


def test_resource_detail_404(app_client) -> None:
    client, _ = app_client
    assert client.get("/api/v1/catalog/resources/rr_missing").status_code == 404


def test_sync_requires_admin_when_auth_on(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """auth-on：普通用户触发同步 403（目录事实源是管理操作）。"""
    from app.core.config import get_settings

    monkeypatch.setenv("AUTH_SECRET", "m18-001-catalog-secret-0123456789abcdef")
    get_settings.cache_clear()
    url = f"sqlite+aiosqlite:///{tmp_path / 'catalog_auth.db'}"
    try:
        with TestClient(create_app(url)) as client:
            client.post(
                "/api/v1/auth/register",
                json={"username": "alice", "password": "password-123"},
            )
            token = client.post(
                "/api/v1/auth/login",
                json={"username": "alice", "password": "password-123"},
            ).json()["access_token"]
            denied = client.post(
                "/api/v1/catalog/sync-runs",
                json={},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert denied.status_code == 403
            # 搜索可用（登录用户只读）
            ok = client.get(
                "/api/v1/catalog/search", headers={"Authorization": f"Bearer {token}"}
            )
            assert ok.status_code == 200
    finally:
        monkeypatch.delenv("AUTH_SECRET", raising=False)
        get_settings.cache_clear()


def test_sync_failure_records_failed_run(app_client) -> None:
    """抓取失败：run 落 failed + 502 如实（不虚构目录）。"""
    client, state = app_client

    import httpx

    def _boom(**_kw):
        raise httpx.ConnectError("network down")

    import app.api.routes.catalog as catalog_route

    original = catalog_route._fetch_tree
    catalog_route._fetch_tree = _boom
    try:
        failed = client.post("/api/v1/catalog/sync-runs", json={})
        assert failed.status_code == 502
    finally:
        catalog_route._fetch_tree = original
    _ = state
