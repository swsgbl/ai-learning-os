"""M5-02 Query planner：槽位识别 + 多源查询计划生成。

- 域层：6 槽位识别（学科/学校/年份/课程/题型/公开范围）、未识别为 None（不虚报）、
  课程优先不误标学科、确定性恒同；计划逐源生成、不可用源带原因、全空回退原词；
- API：POST /search/plan 槽位+计划、指定 providers 未知 422、空白 422、
  无 DB 503、计划查询词可执行（与 POST /search/queries 闭环）。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.query_planner import build_query_plan, parse_query_slots
from app.main import create_app
from app.search.providers import build_search_registry

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _fake_settings():
    class S:
        search_provider = "auto"
        search_cloud_endpoint = None
        search_cloud_api_key = None
    return S()


# ---------- 域层：槽位识别 ----------


def test_parse_full_slots() -> None:
    """全槽位识别：年份/学校/课程/题型/公开范围；课程含学科词时不误标学科。"""
    slots = parse_query_slots("2024年清华大学高等数学公开课多项选择题")
    assert slots["year"] == "2024"
    assert slots["school"] == "清华大学"
    assert slots["course"] == "高等数学"
    assert slots["question_type"] == "多项选择题"
    assert slots["publicity"] == "公开课"
    assert slots["subject"] is None  # 「高等数学」含「数学」→ 课程优先


def test_parse_subject_and_type() -> None:
    """学科 + 题型识别。"""
    slots = parse_query_slots("高中物理填空题练习")
    assert slots["subject"] == "物理"
    assert slots["question_type"] == "填空题"
    assert slots["year"] is None and slots["school"] is None


def test_parse_no_slots_returns_none() -> None:
    """无槽位：全部 None（不虚报识别结果）。"""
    slots = parse_query_slots("帮我找点复习资料")
    assert all(v is None for v in slots.values())


def test_parse_school_and_year() -> None:
    """学校正则 + 年份识别。"""
    slots = parse_query_slots("北京大学2023期末试卷")
    assert slots["school"] == "北京大学"
    assert slots["year"] == "2023"


def test_parse_slots_deterministic() -> None:
    """同输入恒同输出（幂等语义）。"""
    text = "2024清华大学线性代数计算题内部"
    assert parse_query_slots(text) == parse_query_slots(text)


# ---------- 域层：计划生成 ----------


def test_plan_one_entry_per_provider() -> None:
    """逐源出计划：2 源 2 条；不可用源同样出计划且带 unavailable_reason。"""
    registry = build_search_registry(None, _fake_settings())
    slots = parse_query_slots("高等数学选择题")
    plan = build_query_plan(slots, "高等数学选择题", registry)
    assert [p["provider"] for p in plan] == ["local-corpus", "cloud-web"]
    assert plan[0]["enabled"] is True and plan[0]["unavailable_reason"] is None
    assert plan[1]["enabled"] is False and plan[1]["unavailable_reason"]


def test_plan_query_contains_slot_terms() -> None:
    """计划查询词 = 槽位词固定顺序拼接。"""
    registry = build_search_registry(None, _fake_settings())
    slots = parse_query_slots("2024清华大学高等数学公开课多项选择题")
    plan = build_query_plan(slots, "原文", registry)
    assert plan[0]["query"] == "高等数学 多项选择题 公开课 清华大学 2024"


def test_plan_fallback_raw_query() -> None:
    """全空槽位：回退原始查询词。"""
    registry = build_search_registry(None, _fake_settings())
    slots = parse_query_slots("微分方程")
    plan = build_query_plan(slots, "微分方程", registry)
    assert plan[0]["query"] == "微分方程"


def test_plan_deterministic() -> None:
    """同 (slots, raw, registry) 恒同计划。"""
    registry = build_search_registry(None, _fake_settings())
    slots = parse_query_slots("线性代数判断题")
    assert build_query_plan(slots, "线性代数判断题", registry) == build_query_plan(
        slots, "线性代数判断题", registry
    )


# ---------- API ----------


def _client():
    return TestClient(create_app(SQLITE_URL))


def test_plan_endpoint() -> None:
    """POST /search/plan：返回槽位 + 多源计划。"""
    with _client() as client:
        r = client.post("/api/v1/search/plan", json={"query": "2024清华大学高等数学选择题"})
        assert r.status_code == 200
        body = r.json()
        assert body["slots"]["course"] == "高等数学"
        assert body["slots"]["school"] == "清华大学"
        assert body["slots"]["year"] == "2024"
        assert [p["provider"] for p in body["plan"]] == ["local-corpus", "cloud-web"]
        assert body["plan"][0]["enabled"] is True
        assert body["plan"][1]["enabled"] is False


def test_plan_endpoint_unknown_provider_422() -> None:
    """指定未知源：预览接口直接 422（与执行接口 skipped 语义区分）。"""
    with _client() as client:
        r = client.post("/api/v1/search/plan", json={"query": "词", "providers": ["no-such"]})
        assert r.status_code == 422
        assert "未知搜索源" in r.json()["detail"]


def test_plan_endpoint_blank_422() -> None:
    with _client() as client:
        assert client.post("/api/v1/search/plan", json={"query": "  "}).status_code == 422


def test_plan_no_db_503() -> None:
    with TestClient(create_app(None)) as client:
        assert client.post("/api/v1/search/plan", json={"query": "词"}).status_code == 503


def test_plan_query_executable_end_to_end() -> None:
    """闭环：计划产出的查询词可执行——上传语料→plan→取 enabled 计划词→queries 命中。"""
    with _client() as client:
        payload = '[{"question": "正弦定理计划闭环题面"}]'.encode()
        uploaded = client.post(
            "/api/v1/resources/upload", files={"file": ("doc.json", payload, "application/json")}
        ).json()
        assert client.post(f"/api/v1/resources/{uploaded['id']}/parse").status_code == 200

        planned = client.post("/api/v1/search/plan", json={"query": "正弦定理计划闭环题面"}).json()
        executable = next(p["query"] for p in planned["plan"] if p["enabled"])
        result = client.post("/api/v1/search/queries", json={"query": executable}).json()
        assert result["result_count"] >= 1
        assert "正弦定理计划闭环题面" in result["results"][0]["snippet"]
