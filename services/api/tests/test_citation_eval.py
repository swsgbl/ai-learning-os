"""M6-03 Citation eval：引用抽取/确定性抽样/有效率判定 + API。

- 域层：outline->concept 映射、样本内全取、等步长确定性、
  有效/缺资源/陈旧 snippet 三态判定、0.9 阈值边界、空集拒绝、invalid 透出；
- API：种子草稿+chunk -> runs 落库回查、report 现算、无草稿 422、无 DB 503。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.domain.citation_eval import (
    extract_citations,
    run_citation_eval,
    sample_citations,
)
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _c(n: int) -> dict:
    return {
        "chapter_no": n, "concept_id": f"c{n:02d}",
        "resource_id": f"res_{n:03d}", "snippet": f"正文片段 {n}",
    }


def test_extract_maps_concept_from_outline() -> None:
    plan = {
        "outline": [{"chapter_no": 1, "concept_id": "c_deriv"}],
        "lessons": [{"chapter_no": 1, "resources": [
            {"resource_id": "res_1", "snippet": "导数是变化率"},
        ]}],
    }
    cites = extract_citations([plan])
    assert cites == [
        {"chapter_no": 1, "concept_id": "c_deriv",
         "resource_id": "res_1", "snippet": "导数是变化率"},
    ]


def test_sample_all_when_under_limit() -> None:
    cites = [_c(i) for i in range(5)]
    assert sample_citations(cites, 20) == sorted(
        cites, key=lambda c: (c["chapter_no"], c["concept_id"], c["resource_id"], c["snippet"]),
    )


def test_sample_stride_deterministic() -> None:
    cites = [_c(i) for i in range(100)]
    assert len(sample_citations(cites, 20)) == 20
    assert sample_citations(cites, 20) == sample_citations(cites, 20)


def test_check_three_states() -> None:
    from app.domain.citation_eval import check_citation

    def lookup(rid: str) -> list[str]:
        return {
            "res_ok": ["导数是变化率 极限是基础"],
            "res_stale": ["已被重解析的新正文"],
        }.get(rid, [])

    def wrap(rid: str, snippet: str) -> dict:
        return check_citation(
            {"chapter_no": 1, "concept_id": "c", "resource_id": rid, "snippet": snippet}, lookup)

    ok = wrap("res_ok", "导数是变化率")
    stale = wrap("res_stale", "导数是变化率")
    missing = wrap("res_none", "x")
    assert ok["valid"] is True and ok["reason"] == "OK"
    assert stale["valid"] is False and "重解析" in stale["reason"]
    assert missing["valid"] is False and "不存在" in missing["reason"]


def test_run_rate_boundary_and_invalid_transparent() -> None:
    cites = [_c(i) for i in range(10)]
    texts = {f"res_{i:03d}": [f"正文片段 {i}"] for i in range(9)}
    report = run_citation_eval(cites, texts.get, sample_size=20)
    assert report["valid_count"] == 9 and report["sampled_count"] == 10
    assert report["validity_rate"] == pytest.approx(0.9)
    assert report["passed"] is True  # 0.9 边界达标
    assert report["invalid"] == [
        {"chapter_no": 9, "concept_id": "c09", "resource_id": "res_009",
         "reason": "资源不存在或无 chunk"},
    ]


def test_run_below_target_fails() -> None:
    cites = [_c(i) for i in range(10)]
    texts = {f"res_{i:03d}": [f"正文片段 {i}"] for i in range(7)}
    report = run_citation_eval(cites, texts.get, sample_size=20)
    assert report["validity_rate"] == 0.7 and report["passed"] is False


def test_run_empty_rejected() -> None:
    with pytest.raises(ValueError):
        run_citation_eval([], lambda rid: [], sample_size=20)


def _seed_citation_corpus(client) -> str:
    """parsed 资源 + 可检索 chunk + 一条含资源挂接的课程草稿。"""
    from datetime import datetime, timezone

    from app.domain.resource import ResourceRecord
    from app.parsing.chunking import Chunk
    from app.repositories.chunks import ChunkRepository
    from app.repositories.course_generation_drafts import (
        CourseGenerationDraftRepository,
    )
    from app.repositories.resources import ResourceRepository

    async def _run() -> str:
        sm = client.app.state.sessionmaker
        rid = "res_cit_1"
        resources = ResourceRepository(sm)
        await resources.create(ResourceRecord(
            id=rid, media_type="text/plain", title="seed",
            content_hash=f"hash-{rid}", storage_key=f"objects/{rid}",
            size_bytes=100, fetched_at=datetime.now(timezone.utc),
        ))
        await resources.set_parse_status(rid, "parsed", parser_name="txt")
        await ChunkRepository(sm).replace_chunks(
            rid,
            [Chunk(text="导数是变化率，极限是基础。", chunk_hash="h-1", page_start=1,
                   page_end=1, slide=None, block_types=["paragraph"])],
            parser_name="txt", license_state="UNKNOWN",
        )
        plan = {
            "goal": "学导数", "matched_competencies": ["c_deriv"],
            "ordered_concepts": ["c_deriv"], "dag_version": "v1",
            "outline": [{"chapter_no": 1, "concept_id": "c_deriv", "title": "导数", "difficulty": 3}],
            "lessons": [{"chapter_no": 1, "objectives": "理解导数", "resources": [
                {"resource_id": rid, "title": "seed", "snippet": "导数是变化率，极限是基础。"},
            ]}],
            "assessments": [], "remediation": [], "generation_note": "n",
        }
        draft = await CourseGenerationDraftRepository(sm).create({
            "goal": "学导数", "dag_version": "v1", "plan": plan,
            "chapter_count": 1, "generation_note": "n",
        })
        return draft["id"]

    return asyncio.run(_run())


def test_api_run_and_report_roundtrip() -> None:
    """种子草稿+chunk -> runs 201(rate=1.0) -> report 一致 -> list -> detail -> 404。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        _seed_citation_corpus(client)
        r = client.post("/api/v1/eval/citation/runs")
        assert r.status_code == 201, r.text
        run = r.json()
        assert run["kind"] == "citation"
        assert run["validity_rate"] == 1.0 and run["passed"] is True
        assert run["sampled_count"] == 1 and run["total_citations"] == 1
        assert run["rule_version"] == "citation-eval-v1"

        rep = client.get("/api/v1/eval/citation/report").json()
        assert rep["validity_rate"] == 1.0

        listed = client.get("/api/v1/eval/citation/runs").json()
        assert any(item["id"] == run["id"] for item in listed)

        detail = client.get(f"/api/v1/eval/citation/runs/{run['id']}").json()
        assert detail["report"]["passed"] is True

        assert client.get("/api/v1/eval/citation/runs/99999").status_code == 404


def test_api_no_citations_422() -> None:
    """有 DB 无草稿：无引用可抽样 -> 422（不虚报有效率）。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        assert client.post("/api/v1/eval/citation/runs").status_code == 422
        assert client.get("/api/v1/eval/citation/report").status_code == 422


def test_api_no_db_503() -> None:
    with TestClient(create_app(None)) as client:
        assert client.post("/api/v1/eval/citation/runs").status_code == 503
        assert client.get("/api/v1/eval/citation/report").status_code == 503
        assert client.get("/api/v1/eval/citation/runs").status_code == 503
