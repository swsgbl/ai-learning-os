"""M15-006 /api/v1/meta/latest-app 契约测试。

覆盖：未配置 404 / 三项齐全透出 / 部分配置仍 404（不虚报）/
platform 白名单 422 / 认证开启时豁免（登录前可查）。

Settings 注入沿用既有模式：monkeypatch.setenv + get_settings.cache_clear。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture()
def client_factory(monkeypatch):
    def _make(**env: str) -> TestClient:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        return TestClient(create_app())

    yield _make
    get_settings.cache_clear()


def test_latest_app_unconfigured_returns_404(client_factory):
    client = client_factory()
    resp = client.get("/api/v1/meta/latest-app?platform=android")
    assert resp.status_code == 404


def test_latest_app_configured_transparent(client_factory):
    url = "https://github.com/swsgbl/ai-learning-os/releases/download/v0.2.0/app-release.apk"
    client = client_factory(
        LATEST_ANDROID_VERSION="0.2.0",
        LATEST_ANDROID_VERSION_CODE="2",
        LATEST_ANDROID_DOWNLOAD_URL=url,
    )
    resp = client.get("/api/v1/meta/latest-app?platform=android")
    assert resp.status_code == 200
    assert resp.json() == {
        "platform": "android",
        "version": "0.2.0",
        "version_code": 2,
        "download_url": url,
    }


def test_latest_app_partial_config_still_404(client_factory):
    # 只配两项（缺 download_url）——缺任一 = 未配置，绝不透出半成品
    client = client_factory(
        LATEST_ANDROID_VERSION="0.2.0",
        LATEST_ANDROID_VERSION_CODE="2",
    )
    resp = client.get("/api/v1/meta/latest-app?platform=android")
    assert resp.status_code == 404


def test_latest_app_harmony_unconfigured_404_even_if_android_set(client_factory):
    client = client_factory(
        LATEST_ANDROID_VERSION="0.2.0",
        LATEST_ANDROID_VERSION_CODE="2",
        LATEST_ANDROID_DOWNLOAD_URL="https://example.com/a.apk",
    )
    resp = client.get("/api/v1/meta/latest-app?platform=harmony")
    assert resp.status_code == 404


def test_latest_app_platform_whitelist(client_factory):
    client = client_factory()
    assert client.get("/api/v1/meta/latest-app?platform=ios").status_code == 422
    # 默认 platform=android 合法（未配置 → 404，不是 422）
    assert client.get("/api/v1/meta/latest-app").status_code == 404


def test_latest_app_exempt_from_auth_gate(client_factory):
    # 认证开启时更新检查仍可用（登录前场景）——豁免列表契约
    client = client_factory(
        AUTH_SECRET="x" * 32,
        LATEST_ANDROID_VERSION="0.2.0",
        LATEST_ANDROID_VERSION_CODE="2",
        LATEST_ANDROID_DOWNLOAD_URL="https://example.com/a.apk",
    )
    resp = client.get("/api/v1/meta/latest-app?platform=android")
    assert resp.status_code == 200
