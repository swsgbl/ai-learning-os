"""M0-07 验收：隐私模式可配置（local/cloud/hybrid），非法值拒绝；/privacy 端点无凭据。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app


def test_privacy_modes_default_to_runbook_values() -> None:
    settings = Settings(_env_file=None)
    assert settings.model_route == "hybrid"
    assert settings.voice_mode == "local"
    assert settings.search_mode == "local"
    assert settings.privacy_store_audio is False
    assert settings.privacy_send_context_to_cloud is True


def test_privacy_modes_accept_all_three_tiers() -> None:
    for mode in ("local", "cloud", "hybrid"):
        settings = Settings(_env_file=None, model_route=mode, voice_mode=mode, search_mode=mode)
        assert (settings.model_route, settings.voice_mode, settings.search_mode) == (mode,) * 3


def test_privacy_mode_rejects_invalid_value() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, model_route="evaluate")


def test_privacy_endpoint_exposes_modes_without_secrets() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/system/privacy")
        assert response.status_code == 200
        body = response.json()
        assert body["model_route"] == "hybrid"
        assert body["store_audio"] is False
        raw = str(response.text).lower()
        assert "secret" not in raw and "key" not in raw
