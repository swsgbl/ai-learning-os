"""M5-07 Course generation workflow：Goal -> Competency -> DAG -> Resource ->
Outline -> Lessons -> Assessments -> Remediation（不虚报 + 确定性）。

- 域层：概念匹配（canonical/alias 子串）、先修闭包、拓扑稳定序、资源挂接与
  缺失汇总、模板习题（不虚报真题）、补救映射（直接先修章节）、无匹配拒绝、确定性；
- API：201 生成闭环（plan 结构全断言）、DAG 未发布 409、无匹配 422（拒绝空课程）、
  404 草稿不存在、409 终态重复审核、503 无 DB。
"""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.domain.concept_dag import ConceptDag, ConceptEdge, ConceptNode
from app.domain.course_workflow import (
    NoCompetencyMatch,
    generate_course_plan,
    match_competencies,
    prerequisite_closure,
)
from app.main import create_app
from app.parsing.chunking import Chunk
from app.repositories.chunks import ChunkRepository

SQLITE_URL = "sqlite+aiosqlite:///:memory:"

CALCULUS_DAG = {
    "nodes": [
        {"id": "c_limit", "canonical_name": "极限", "subject": "math", "difficulty": 2,
         "description": "极限描述函数或数列的趋近行为"},
        {"id": "c_deriv", "canonical_name": "导数", "aliases": ["微分"], "subject": "math",
         "difficulty": 3, "description": "导数刻画瞬时变化率"},
        {"id": "c_int", "canonical_name": "积分", "subject": "math", "difficulty": 4,
         "description": "积分是微分的逆运算"},
    ],
    "edges": [
        {"prerequisite_id": "c_limit", "concept_id": "c_deriv"},
        {"prerequisite_id": "c_deriv", "concept_id": "c_int"},
    ],
    "note": "微积分入门图谱",
}


def _dag_fixture() -> ConceptDag:
    return ConceptDag(
        version=7,
        note="域层测试",
        nodes=tuple(
            ConceptNode(
                id=n["id"],
                canonical_name=n["canonical_name"],
                aliases=tuple(n.get("aliases", ())),
                subject=n["subject"],
                description=n.get("description", ""),
                difficulty=n.get("difficulty", 3),
            )
            for n in CALCULUS_DAG["nodes"]
        ),
        edges=tuple(
            ConceptEdge(prerequisite_id=e["prerequisite_id"], concept_id=e["concept_id"])
            for e in CALCULUS_DAG["edges"]
        ),
        created_at="2026-01-01T00:00:00+00:00",
    )


def _resources_with_deriv() -> dict[str, list[dict]]:
    return {
        "c_deriv": [
            {"resource_id": "res_a", "title": "导数讲义", "snippet": "导数的定义与求导法则"},
        ],
    }


# ---------- 域层 ----------


def test_match_and_closure() -> None:
    """Goal 命中（canonical+alias）+ 传递先修闭包。"""
    dag = _dag_fixture()
    matched = match_competencies("我想学导数与积分", dag)
    assert matched == ["c_deriv", "c_int"]
    closure = prerequisite_closure(matched, dag)
    assert set(closure) == {"c_limit", "c_deriv", "c_int"}
    # alias 命中
    assert match_competencies("从微分开始", dag) == ["c_deriv"]
    assert match_competencies("量子力学", dag) == []


def test_generate_full_plan_structure() -> None:
    """八阶段全链路：拓扑序、章节号、资源挂接、模板习题、补救映射。"""
    plan = generate_course_plan("我想学导数与积分", _dag_fixture(), _resources_with_deriv())
    assert plan["matched_competencies"] == ["c_deriv", "c_int"]
    assert plan["ordered_concepts"] == ["c_limit", "c_deriv", "c_int"]
    assert plan["dag_version"] == 7
    assert [c["chapter_no"] for c in plan["outline"]]== [1, 2, 3]
    assert plan["outline"][0]["title"] == "极限"
    assert plan["outline"][2]["difficulty"] == 4
    # 资源：只有 c_deriv 有资源
    assert plan["lessons"][1]["resources"][0]["resource_id"] == "res_a"
    assert plan["lessons"][0]["resources"] == []
    assert plan["lessons"][2]["resources"] == []
    # 模板习题（不虚报真题）
    assert all(a["assessment_kind"] == "template" for a in plan["assessments"])
    assert "导数" in plan["assessments"][1]["stem"]
    # 补救映射：直接先修的章节号
    assert [r["review_chapters"] for r in plan["remediation"]] == [[], [1], [2]]
    # note 汇总缺失资源（不虚构）
    assert "极限、积分" in plan["generation_note"]
    assert "暂无命中的平台资源" in plan["generation_note"]


def test_no_match_rejects() -> None:
    """无匹配 -> NoCompetencyMatch（fail-closed，拒绝空课程）。"""
    try:
        generate_course_plan("量子纠缠", _dag_fixture(), {})
    except NoCompetencyMatch as cause:
        assert "拒绝生成空课程" in str(cause)
    else:
        raise AssertionError("应当拒绝生成")


def test_deterministic() -> None:
    """同输入两次生成结果完全一致（确定性管线）。"""
    a = generate_course_plan("我想学导数与积分", _dag_fixture(), _resources_with_deriv())
    b = generate_course_plan("我想学导数与积分", _dag_fixture(), _resources_with_deriv())
    assert a == b


# ---------- API ----------

API = "/api/v1/courses/generation-drafts"


def _client():
    return TestClient(create_app(SQLITE_URL))


def _seed_chunks(client, rid: str, text: str) -> None:
    """直接灌库：parsed 资源 + 可搜索 chunks（解耦 parser）。"""
    from datetime import datetime, timezone

    from app.domain.resource import ResourceRecord
    from app.repositories.resources import ResourceRepository

    async def _run() -> None:
        sm = client.app.state.sessionmaker
        resources = ResourceRepository(sm)
        await resources.create(ResourceRecord(
            id=rid,
            media_type="text/plain",
            title=f"seed-{rid}",
            content_hash=f"hash-{rid}",
            storage_key=f"objects/{rid}",
            size_bytes=100,
            fetched_at=datetime.now(timezone.utc),
        ))
        await resources.set_parse_status(rid, "parsed", parser_name="txt")
        chunks_repo = ChunkRepository(sm)
        await chunks_repo.replace_chunks(
            rid,
            [Chunk(text=text, chunk_hash=f"h-{rid}-1", page_start=1, page_end=1,
                   slide=None, block_types=["paragraph"])],
            parser_name="txt",
            license_state="UNKNOWN",
        )

    asyncio.run(_run())


def _publish_dag(client) -> dict:
    response = client.post("/api/v1/concept-dag/versions", json=CALCULUS_DAG)
    assert response.status_code == 201, response.text
    return response.json()


def test_api_full_flow() -> None:
    """DAG 发布 -> 201 生成 -> 队列 -> 详情 -> approve -> 409 终态。"""
    with _client() as client:
        _publish_dag(client)
        _seed_chunks(
            client, "res_deriv_notes",
            "导数与微分讲义：导数刻画瞬时变化率。",
        )
        _seed_chunks(
            client, "res_int_notes", "积分与不定积分：积分是微分的逆运算。",
        )

        r = client.post(API, json={"goal": "我想学导数与积分"})
        assert r.status_code == 201, r.text
        draft = r.json()
        assert draft["status"] == "pending_review"
        assert draft["chapter_count"] == 3
        assert draft["dag_version"] == 1
        plan = draft["plan"]
        assert plan["matched_competencies"] == ["c_deriv", "c_int"]
        assert plan["ordered_concepts"] == ["c_limit", "c_deriv", "c_int"]
        assert [c["chapter_no"] for c in plan["outline"]] == [1, 2, 3]
        lessons = plan["lessons"]
        by_chapter = {l["chapter_no"]: l for l in lessons}
        assert [i["resource_id"] for i in by_chapter[2]["resources"]] == ["res_deriv_notes"]
        assert by_chapter[3]["resources"][0]["resource_id"] == "res_int_notes"
        # 章节号与拓扑序一致
        assert [c["concept_id"] for c in plan["outline"]] == ["c_limit", "c_deriv", "c_int"]
        # note：极限无资源
        assert "极限" in draft["generation_note"]

        listed = client.get(API + "?status=pending_review").json()
        assert any(item["id"] == draft["id"] for item in listed)

        detail = client.get(f"{API}/{draft['id']}").json()
        assert detail["id"] == draft["id"] and detail["goal"] == "我想学导数与积分"

        ok = client.post(f"{API}/{draft['id']}/approve", json={"note": "大纲合理"})
        assert ok.status_code == 200
        assert ok.json()["status"] == "approved"

        again = client.post(f"{API}/{draft['id']}/approve", json={})
        assert again.status_code == 409
        assert "终态" in again.json()["detail"]


def test_api_no_dag_409() -> None:
    """DAG 未发布 -> 409（不虚报可生成）。"""
    with _client() as client:
        r = client.post(API, json={"goal": "我想学导数"})
        assert r.status_code == 409
        assert "尚未发布" in r.json()["detail"]


def test_api_no_match_422() -> None:
    """DAG 无相关概念 -> 422 拒绝空课程。"""
    with _client() as client:
        _publish_dag(client)
        r = client.post(API, json={"goal": "量子纠缠与薛定谔方程"})
        assert r.status_code == 422
        assert "拒绝生成空课程" in r.json()["detail"]


def test_api_blank_goal_422() -> None:
    """空白 goal -> pydantic 422。"""
    with _client() as client:
        assert client.post(API, json={"goal": "   "}).status_code == 422


def test_api_get_404_and_review_missing() -> None:
    """草稿不存在：GET 404；approve MISSING 404。"""
    with _client() as client:
        assert client.get(API + "/cgd_nonexist").status_code == 404
        r = client.post(API + "/cgd_nonexist/approve", json={})
        assert r.status_code == 404


def test_api_no_db_503() -> None:
    """无 DB：POST/GET 均 503（不虚报可用）。"""
    with TestClient(create_app(None)) as client:
        assert client.post(API, json={"goal": "我想学导数"}).status_code == 503
        assert client.get(API).status_code == 503
