"""M5-08 Variant question generator：变式题生成（解保持变换 + 概念映射/evidence 保留）。

- 域层：语境替换（全部命中实体、数值不动、选项不动）、数值 x2（相同数字同映射、
  选项同步、小数精确）、0 变式题明示、确定性、数量上限拒绝；
- API：201 生成闭环（variants 全结构断言 -> approve 终态 -> 409）、0 变式 422、
  未知题型 422、空 questions 422、404、无 DB 503。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.variant_generator import generate_variant_draft, generate_variants
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"

Q1 = {
    "question_no": 1, "stem": "小明有 5 个苹果，给了同学 2 个，还剩几个？",
    "question_type": "mcq", "score": 3.0, "page_start": 1, "page_end": 1,
    "resource_id": "res_x",
    "options": [
        {"label": "A", "text": "3"}, {"label": "B", "text": "5"},
        {"label": "C", "text": "7"}, {"label": "D", "text": "10"},
    ],
    "concept_ids": ["c_sub"],
}

Q3 = {
    "question_no": 3, "stem": "什么是微积分？", "question_type": "solved",
    "score": 10.0, "page_start": None, "page_end": None, "options": [],
}


# ---------- 域层 ----------


def test_context_swap_keeps_solution() -> None:
    """语境替换：全部命中实体都换、数值/选项不动（解不变）。"""
    variants, _ = generate_variants(dict(Q1))
    ctx = next(v for v in variants if v["transform"] == "context_swap")
    assert ctx["stem"] == "小红有 5 个橙子，给了同学 2 个，还剩几个？"
    assert ctx["options"] == Q1["options"]
    assert "苹果->橙子" in ctx["transform_detail"]
    assert "小明->小红" in ctx["transform_detail"]
    assert ctx["solvable_note"] == "语境实体替换，数值与数学结构未变，解不变"


def test_numeric_scale_consistent_mapping() -> None:
    """数值 x2：相同数字同映射（5->10 两处一致）、选项同步、可逆映射可审计。"""
    variants, _ = generate_variants(dict(Q1))
    num = next(v for v in variants if v["transform"] == "numeric_scale")
    assert num["stem"] == "小明有 10 个苹果，给了同学 4 个，还剩几个？"
    assert [o["text"] for o in num["options"]] == ["6", "10", "14", "20"]


def test_concept_ids_and_evidence_preserved() -> None:
    """概念映射逐题继承 + evidence 完整（题号/页码/资源/stem 摘录）。"""
    variants, _ = generate_variants(dict(Q1))
    for v in variants:
        assert v["concept_ids"] == ["c_sub"]
        ev = v["evidence"]
        assert ev["source_question_no"] == 1
        assert ev["page_start"] == 1 and ev["page_end"] == 1
        assert ev["resource_id"] == "res_x"
        assert ev["source_stem_excerpt"].startswith("小明有 5 个苹果")


def test_zero_variants_disclosed() -> None:
    """无实体命中且无数值 -> 0 变式 + note 明示（不硬凑）。"""
    variants, note = generate_variants(dict(Q3))
    assert variants == []
    assert "题 3 无法生成解保持变式" in note


def test_deterministic() -> None:
    """同输入两次生成恒同。"""
    a = generate_variant_draft([dict(Q1), dict(Q3)])
    b = generate_variant_draft([dict(Q1), dict(Q3)])
    assert a == b


def test_empty_questions_rejected() -> None:
    """空 questions 拒绝（域层 ValueError -> API 422）。"""
    try:
        generate_variant_draft([])
    except ValueError as cause:
        assert "1..20" in str(cause)
    else:
        raise AssertionError


def test_decimal_precise_scaling() -> None:
    """小数 x2 精确无舍入（0.5->1、1.5->3）。"""
    q = {"question_no": 4, "stem": "0.5 米的 1.5 倍是多少米？", "question_type": "numeric",
         "score": 2.0, "page_start": 1, "page_end": 1, "options": []}
    variants, _ = generate_variants(q)
    scaled = next(v for v in variants if v["transform"] == "numeric_scale")
    assert scaled["stem"] == "1 米的 3 倍是多少米？"


# ---------- API ----------

API = "/api/v1/questions/variant-drafts"


def _client():
    return TestClient(create_app(SQLITE_URL))


def test_api_full_flow() -> None:
    """201 生成（variants 全断言）-> 队列 -> 详情 -> approve -> 409 终态。"""
    with _client() as client:
        r = client.post(API, json={"questions": [dict(Q1), dict(Q3)]})
        assert r.status_code == 201, r.text
        draft = r.json()
        assert draft["status"] == "pending_review"
        assert draft["variant_count"] == 2
        assert "题 3 无法生成解保持变式" in draft["generation_note"]
        variants = draft["variants"]
        assert [v["variant_no"] for v in variants] == [1, 2]
        ctx = variants[0]
        assert ctx["transform"] == "context_swap"
        assert ctx["stem"] == "小红有 5 个橙子，给了同学 2 个，还剩几个？"
        assert ctx["concept_ids"] == ["c_sub"]
        assert ctx["evidence"]["source_question_no"] == 1
        num = variants[1]
        assert num["stem"] == "小明有 10 个苹果，给了同学 4 个，还剩几个？"
        assert [o["text"] for o in num["options"]] == ["6", "10", "14", "20"]

        listed = client.get(API + "?status=pending_review").json()
        assert any(item["id"] == draft["id"] for item in listed)

        detail = client.get(f"{API}/{draft['id']}").json()
        assert detail["id"] == draft["id"]

        ok = client.post(f"{API}/{draft['id']}/approve", json={"note": "变式可解性复核通过"})
        assert ok.status_code == 200 and ok.json()["status"] == "approved"

        again = client.post(f"{API}/{draft['id']}/approve", json={})
        assert again.status_code == 409
        assert "终态" in again.json()["detail"]


def test_api_all_unsolvable_422() -> None:
    """全部源题 0 变式 -> 422（不虚报产出空草稿）。"""
    with _client() as client:
        r = client.post(API, json={"questions": [dict(Q3)]})
        assert r.status_code == 422
        assert "无法生成" in r.json()["detail"]


def test_api_unknown_qtype_422() -> None:
    """未知题型 422。"""
    with _client() as client:
        bad = dict(Q1)
        bad["question_type"] = "mystery"
        r = client.post(API, json={"questions": [bad]})
        assert r.status_code == 422


def test_api_empty_questions_422() -> None:
    """空 questions 422（pydantic min_length=1）。"""
    with _client() as client:
        assert client.post(API, json={"questions": []}).status_code == 422


def test_api_404() -> None:
    """草稿不存在：GET 404；approve MISSING 404。"""
    with _client() as client:
        assert client.get(API + "/vqd_nonexist").status_code == 404
        assert client.post(API + "/vqd_nonexist/approve", json={}).status_code == 404


def test_api_no_db_503() -> None:
    """无 DB：POST/GET 均 503（不虚报可用）。"""
    with TestClient(create_app(None)) as client:
        assert client.post(API, json={"questions": [dict(Q1)]}).status_code == 503
        assert client.get(API).status_code == 503
