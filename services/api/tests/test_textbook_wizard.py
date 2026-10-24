"""M19-003 教材→冲刺一键向导测试（stub 全链 + 幂等复用）。"""
from __future__ import annotations

import json

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def _make_textbook() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 120), "命题逻辑研究命题及其真值。", fontsize=14, fontname="china-s")
    page.insert_text((72, 150), "复合命题由逻辑联结词构成。", fontsize=14, fontname="china-s")
    doc.set_toc([[1, "第一章 命题逻辑", 1]])
    data = doc.tobytes()
    doc.close()
    return data


class _StubGateway:
    def __init__(self, **kwargs: object) -> None:
        pass

    def chat(self, messages, temperature=0.0, max_tokens=1024):
        content = messages[0].content if isinstance(messages, tuple) else messages[0]["content"]
        if "课程设计师" in content and "概念" in content:
            return json.dumps(
                {
                    "concepts": [
                        {"id": "math:proposition", "canonical_name": "命题", "confidence": 0.95, "page": 1}
                    ],
                    "edges": [],
                },
                ensure_ascii=False,
            )
        items = [
            {"concept": "math:proposition", "stem": f"命题第 {i} 题", "options": ["甲", "乙", "丙", "丁"],
             "answer_index": i % 4, "difficulty": 2, "why": "解析"}
            for i in range(4)
        ]
        return json.dumps({"items": items}, ensure_ascii=False)


@pytest.fixture()
def wizard_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from app.core.config import get_settings

    url = f"sqlite+aiosqlite:///{tmp_path / 'wiz.db'}"
    import app.api.routes.catalog as catalog_route
    import app.api.routes.library as library_route
    import app.domain.item_generator as ig

    monkeypatch.setattr(catalog_route, "_fetch_tree", lambda *a, **k: ("sha-w", [("教材/数学/逻辑.pdf", "b1", 1)]))
    monkeypatch.setattr(library_route, "_download_parts", lambda c, m, **k: [_make_textbook()])
    monkeypatch.setattr(ig, "LlmGateway", _StubGateway)
    monkeypatch.setattr(library_route, "LlmGateway", _StubGateway)
    monkeypatch.setenv("LLM_ENDPOINT", "http://stub.local/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-stub-0123456789")
    monkeypatch.setenv("LLM_MODEL", "stub-model")
    get_settings.cache_clear()
    yield url
    monkeypatch.delenv("LLM_ENDPOINT", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    get_settings.cache_clear()


def test_wizard_creates_full_sprint_idempotent(wizard_env, monkeypatch: pytest.MonkeyPatch) -> None:
    url = wizard_env
    with TestClient(create_app(url)) as anon:
        anon.post("/api/v1/catalog/sync-runs", json={})
        rid = anon.get("/api/v1/catalog/search", params={"q": "逻辑"}).json()[0]["id"]

    monkeypatch.setenv("AUTH_SECRET", "m19-003-wiz-secret-0123456789abcdef")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        with TestClient(create_app(url)) as client:
            client.post("/api/v1/auth/register", json={"username": "alice", "password": "password-123"})
            token = client.post(
                "/api/v1/auth/login", json={"username": "alice", "password": "password-123"}
            ).json()["access_token"]
            h = {"Authorization": f"Bearer {token}"}

            job = client.post(
                "/api/v1/library/imports",
                json={"resource_id": rid, "permission_statement": True},
                headers=h,
            ).json()
            client.post(f"/api/v1/library/imports/{job['id']}/parse", headers=h)
            extracted = client.post(f"/api/v1/library/imports/{job['id']}/concepts", headers=h)
            assert extracted.status_code == 201, extracted.text
            listed = client.get(f"/api/v1/library/imports/{job['id']}/concepts", headers=h).json()
            for c in listed["concepts"]:
                client.post(
                    f"/api/v1/library/imports/{job['id']}/concepts/{c['id']}/confirm", headers=h
                )

            wizard = client.post(
                f"/api/v1/library/imports/{job['id']}/create-sprint", headers=h
            )
            assert wizard.status_code == 201, wizard.text
            result = wizard.json()
            assert result["concept_ids"] == ["math:proposition"]
            assert result["generated"] == 4
            assert result["published"] == 4
            assert result["blueprint_complete"] is True
            assert result["sprint_phase"] == "created"

            # 幂等：重跑复用合同与冲刺（不再翻倍）
            again = client.post(f"/api/v1/library/imports/{job['id']}/create-sprint", headers=h)
            assert again.status_code == 201
            repeat = again.json()
            assert repeat["contract_id"] == result["contract_id"]
            assert repeat["sprint_id"] == result["sprint_id"]
            contracts = client.get("/api/v1/learning/contracts", headers=h).json()
            mine = [c for c in contracts if c["title"].startswith("教材冲刺")]
            assert len(mine) == 1
    finally:
        monkeypatch.delenv("AUTH_SECRET", raising=False)
        get_settings.cache_clear()


def test_wizard_requires_confirmed_concepts(wizard_env, monkeypatch: pytest.MonkeyPatch) -> None:
    url = wizard_env
    with TestClient(create_app(url)) as anon:
        anon.post("/api/v1/catalog/sync-runs", json={})
        rid = anon.get("/api/v1/catalog/search", params={"q": "逻辑"}).json()[0]["id"]
    monkeypatch.setenv("AUTH_SECRET", "m19-003-wiz2-secret-0123456789abcdef")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        with TestClient(create_app(url)) as client:
            client.post("/api/v1/auth/register", json={"username": "alice", "password": "password-123"})
            token = client.post(
                "/api/v1/auth/login", json={"username": "alice", "password": "password-123"}
            ).json()["access_token"]
            h = {"Authorization": f"Bearer {token}"}
            job = client.post(
                "/api/v1/library/imports",
                json={"resource_id": rid, "permission_statement": True},
                headers=h,
            ).json()
            client.post(f"/api/v1/library/imports/{job['id']}/parse", headers=h)
            denied = client.post(f"/api/v1/library/imports/{job['id']}/create-sprint", headers=h)
            assert denied.status_code == 409
            assert "确认" in denied.json()["detail"]
    finally:
        monkeypatch.delenv("AUTH_SECRET", raising=False)
        get_settings.cache_clear()
