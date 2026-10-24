"""M19-002 垂直切片全链测试（审计 02 §8 八步闭环）。

catalog entry → private import → parse(页锚点) → structure(章节) →
concepts(确认) → source-refs(from-textbook 桥) → generate(带教材引用)
→ publish → blueprint → sprint → 前测 → 掌握度 → 复习。
（stub LLM + MemoryObjectStore——live 版本在部署后跑。）
"""
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
    page.insert_text((72, 180), "真值表枚举所有可能的赋值组合。", fontsize=14, fontname="china-s")
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
                        {"id": "math:proposition", "canonical_name": "命题", "confidence": 0.95, "page": 1},
                        {"id": "math:truth-table", "canonical_name": "真值表", "confidence": 0.9, "page": 1},
                    ],
                    "edges": [],
                },
                ensure_ascii=False,
            )
        # 出题（显式传 concepts 时显示名=concept_id 全串——M17-006b）
        items = []
        for concept in ("math:proposition", "math:truth-table"):
            for index in range(4):
                items.append(
                    {
                        "concept": concept,
                        "stem": f"{concept} 第 {index} 题",
                        "options": ["甲", "乙", "丙", "丁"],
                        "answer_index": index % 4,
                        "difficulty": 2,
                        "why": "解析",
                    }
                )
        return json.dumps({"items": items}, ensure_ascii=False)


@pytest.fixture()
def slice_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from app.core.config import get_settings

    url = f"sqlite+aiosqlite:///{tmp_path / 'vs.db'}"
    import app.api.routes.catalog as catalog_route
    import app.api.routes.library as library_route
    import app.domain.item_generator as ig

    monkeypatch.setattr(
        catalog_route, "_fetch_tree", lambda *a, **k: ("sha-vs", [("教材/数学/逻辑.pdf", "b1", 1)])
    )
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


def test_textbook_to_mastery_vertical_slice(slice_env, monkeypatch: pytest.MonkeyPatch) -> None:
    url = slice_env
    # 步 1：目录 → 私有导入
    with TestClient(create_app(url)) as anon:
        seed = anon.post("/api/v1/catalog/sync-runs", json={})
        assert seed.status_code == 201
        rid = anon.get("/api/v1/catalog/search", params={"q": "逻辑"}).json()[0]["id"]

    monkeypatch.setenv("AUTH_SECRET", "m19-002-vs-secret-0123456789abcdef")
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
            assert job["status"] == "ready"

            # 步 2：解析（页锚点）
            parsed = client.post(f"/api/v1/library/imports/{job['id']}/parse", headers=h).json()
            assert parsed["status"] == "parsed" and parsed["page_count"] == 1

            # 步 3：章节树
            structure = client.post(f"/api/v1/library/imports/{job['id']}/structure", headers=h).json()
            assert structure["unit_count"] == 1

            # 步 4：概念候选 → 确认
            concepts = client.post(f"/api/v1/library/imports/{job['id']}/concepts", headers=h).json()
            assert concepts["concept_count"] == 2
            listed = client.get(f"/api/v1/library/imports/{job['id']}/concepts", headers=h).json()
            for c in listed["concepts"]:
                confirmed = client.post(
                    f"/api/v1/library/imports/{job['id']}/concepts/{c['id']}/confirm", headers=h
                )
                assert confirmed.json()["status"] == "confirmed"

            # 步 5：建学习合同（教材概念作目标域）
            contract = client.post(
                "/api/v1/learning/contracts",
                json={
                    "title": "命题逻辑教材冲刺",
                    "mode": "exam_sprint",
                    "target_outcomes": [
                        {"statement": "能判断命题真值并使用真值表", "criteria": "教材第1章"}
                    ],
                    "time_budget_hours": 48,
                    "pass_criteria": "后测≥80",
                },
                headers=h,
            ).json()
            client.post(f"/api/v1/learning/contracts/{contract['id']}/confirm", headers=h)

            # 步 6：教材 chunks → 来源证据桥
            refs = client.post(
                f"/api/v1/learning/contracts/{contract['id']}/source-refs/from-textbook",
                json={"job_id": job["id"]},
                headers=h,
            )
            assert refs.status_code == 201, refs.text
            ref_rows = refs.json()
            assert len(ref_rows) == 2  # 两个概念各命中教材文本
            assert any("教材" in r["title"] or "第1页" in r["title"] for r in ref_rows)
            assert all(r["license_hint"] == "user_private_import" for r in ref_rows)

            # 步 7：生成题（带教材引用）→ 发布 → 蓝图
            gen = client.post(
                f"/api/v1/learning/contracts/{contract['id']}/generate-items",
                json={"concepts": ["math:proposition", "math:truth-table"], "per_concept": 4},
                headers=h,
            )
            assert gen.status_code == 201, gen.text
            item_ids = [i["item_id"] for i in gen.json()["items"]]
            assert len(item_ids) == 8
            for iid in item_ids:
                pub = client.post(f"/api/v1/learning/questions/{iid}/publish", headers=h)
                assert pub.status_code == 200
            bp = client.post(
                f"/api/v1/learning/contracts/{contract['id']}/blueprint/build",
                json={"concepts": ["math:proposition", "math:truth-table"]},
                headers=h,
            )
            assert bp.status_code == 201 and bp.json()["complete"] is True

            # 步 8：冲刺 → 前测 → 掌握度 → 复习排期
            sprint = client.post(
                f"/api/v1/learning/contracts/{contract['id']}/sprints", headers=h
            ).json()
            for phase in ("contract_confirmed", "baseline_assessment"):
                client.post(
                    f"/api/v1/learning/sprints/{sprint['id']}/advance",
                    json={"to_phase": phase},
                    headers=h,
                )
            baseline = client.get(
                f"/api/v1/learning/sprints/{sprint['id']}/assessment",
                params={"phase": "baseline"},
                headers=h,
            ).json()
            # 黄金答案全 A（stub 第 0 题答案是 A）
            submitted = client.post(
                f"/api/v1/learning/sprints/{sprint['id']}/assessment/baseline/submit",
                json={"responses": [{"item_id": i["item_id"], "response": "A"} for i in baseline["items"]]},
                headers=h,
            )
            assert submitted.status_code == 200
            mastery = client.get("/api/v1/learning/mastery", headers=h).json()
            by_concept = {m["concept_id"]: m["state"] for m in mastery}
            # 全 A 作答下不同题对错不同——掌握态可低至 emerging；链路断言
            # 是「证据已从教材前测流入掌握计算」（两概念都有态且非 unknown）
            valid_states = {"emerging", "practicing", "provisional"}
            assert by_concept.get("math:proposition") in valid_states
            assert by_concept.get("math:truth-table") in valid_states
    finally:
        monkeypatch.delenv("AUTH_SECRET", raising=False)
        get_settings.cache_clear()
