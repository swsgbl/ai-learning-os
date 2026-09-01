"""M5-06 Paper extractor：试卷题目抽取（页码/题型/分值保留，不虚报）。

- 域层：段标题识别（误判防护）、题型映射、分值（段默认/题干覆盖/None 不虚报）、
  页码归属（跨 chunk）、选项归并、无题空列表、确定性；
- API：201 抽取闭环（页码/题型/分值经 API 透传）、404 资源/草稿不存在、
  409 未解析带原因、409 终态重复审核、503 无 DB。
"""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.domain.paper_extractor import extract_questions, match_section
from app.main import create_app
from app.parsing.chunking import Chunk
from app.repositories.chunks import ChunkRepository

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


# ---------- 域层 ----------


def _chunks_fixture() -> list[dict]:
    return [
        {
            "text": (
                "2026 春季学期期末试卷\n"
                "一、单项选择题（每小题 3 分，共 15 分）\n"
                "1. 极限 lim(x->0) sinx/x 的值是（ ）\n"
                "A. 1\n"
                "B. 0\n"
                "C. -1\n"
                "D. 不存在\n"
            ),
            "page_start": 1,
            "page_end": 1,
        },
        {
            "text": (
                "二、填空题（每空 4 分）\n"
                "3. 函数 f(x)=x^2 的导数是____（6分）\n"
                "4. 判断：数列 {an} 收敛的充分条件是____\n"
            ),
            "page_start": 2,
            "page_end": 3,
        },
    ]


def test_extract_full_paper_fields() -> None:
    """题号/题型/分值/页码/选项全链路断言。"""
    qs = extract_questions(_chunks_fixture())["questions"]
    assert len(qs) == 3
    q1 = qs[0]
    assert q1["question_no"] == 1
    assert q1["question_type"] == "mcq"
    assert q1["score"] == 3.0
    assert q1["page_start"] == 1 and q1["page_end"] == 1
    assert [o["label"] for o in q1["options"]] == ["A", "B", "C", "D"]
    assert q1["stem"].startswith("极限 lim")
    q2 = qs[1]
    assert q2["question_type"] == "fill_blank"
    assert q2["score"] == 6.0  # 题干内（6分）覆盖段默认 4
    assert q2["page_start"] == 2 and q2["page_end"] == 3
    q3 = qs[2]
    assert q3["score"] == 4.0  # 段默认分值
    assert q3["question_type"] == "fill_blank"


def test_match_section_rules() -> None:
    """段标题判定：中文序号长行 OK、短行 OK、题号行与长题干预线拒绝。"""
    assert match_section("一、单项选择题（每小题 3 分）") == ("mcq", 3.0)
    assert match_section("二、简答题") == ("solved", None)
    assert match_section("判断题") == ("true_false", None)
    assert match_section("本部分共包含多个填空小题，请考生在答题卡上作答并注意书写规范要求") is None
    assert match_section("1. 简述函数的定义并计算其极限值并证明单调性") is None
    assert match_section("") is None


def test_match_section_unknown_keywords() -> None:
    """无关键词匹配 -> None（不虚报题型）。"""
    assert match_section("五、材料阅读题") is None
    assert match_section("一、选择题（每小题 2 分）") == ("mcq", 2.0)


def test_no_questions_no_fabrication() -> None:
    """无题不虚报：空列表 + note 明示。"""
    result = extract_questions([{
        "text": "一段普通散文内容。\n另一段也没有题目。",
        "page_start": 1,
        "page_end": 1,
    }])
    assert result["questions"] == []
    assert "未识别出题目" in result["extraction_note"]


def test_unknown_type_and_missing_score() -> None:
    """无段标题：题型 unknown、分值 None（不虚报）。"""
    qs = extract_questions([{
        "text": "1. 谁写的《红楼梦》？\n2. 圆周率约为多少？",
        "page_start": 7,
        "page_end": 7,
    }])
    assert [q["question_type"] for q in qs["questions"]] == ["unknown", "unknown"]
    assert all(q["score"] is None for q in qs["questions"])
    assert qs["questions"][0]["page_start"] == 7


def test_deterministic() -> None:
    """同输入两次抽取结果一致。"""
    a = extract_questions(_chunks_fixture())
    b = extract_questions(_chunks_fixture())
    assert a == b


# ---------- API ----------


def _client():
    return TestClient(create_app(SQLITE_URL))


def _seed_paper_resource(client, rid: str) -> str:
    """直接灌库：parsed 资源 + 带 page 的 chunks（解耦 parser，页码可控）。"""
    from datetime import datetime, timezone

    from app.domain.resource import ResourceRecord
    from app.repositories.resources import ResourceRepository

    async def _run() -> None:
        sm = client.app.state.sessionmaker
        resources = ResourceRepository(sm)
        record = ResourceRecord(
            id=rid,
            media_type="text/plain",
            title="2026 高数期末试卷",
            content_hash=f"hash-{rid}",
            storage_key=f"objects/{rid}",
            size_bytes=1234,
            fetched_at=datetime.now(timezone.utc),
        )
        await resources.create(record)
        await resources.set_parse_status(rid, "parsed", parser_name="txt")
        chunks_repo = ChunkRepository(sm)
        chunks = [
            Chunk(
                text=(
                    "一、单项选择题（每小题 3 分）\n"
                    "1. 极限 lim(x->0) sinx/x 的值是（ ）\n"
                    "A. 1\nB. 0\nC. -1\nD. 不存在\n"
                ),
                chunk_hash=f"h-{rid}-1",
                page_start=1,
                page_end=1,
                slide=None,
                block_types=["paragraph"],
            ),
            Chunk(
                text=(
                    "二、填空题（每空 4 分）\n"
                    "2. 导数 d/dx e^x = ____（6分）\n"
                    "3. 数列收敛定义：\n对任意 epsilon>0，存在 N，当 n>N 时 |an-A|<epsilon\n"
                ),
                chunk_hash=f"h-{rid}-2",
                page_start=2,
                page_end=2,
                slide=None,
                block_types=["paragraph"],
            ),
        ]
        await chunks_repo.replace_chunks(
            rid, chunks, parser_name="txt", license_state="UNKNOWN"
        )

    asyncio.run(_run())
    return rid


def test_api_extract_flow_with_review() -> None:
    """201 -> 队列 -> 详情（页码/题型/分值透传）-> approve -> 409。"""
    with _client() as client:
        rid = _seed_paper_resource(client, "res_m506_flow")
        r = client.post(
            "/api/v1/papers/import-drafts", json={"resource_id": rid}
        )
        assert r.status_code == 201
        draft = r.json()
        assert draft["status"] == "pending_review"
        assert draft["question_count"] == 3
        assert draft["extraction_note"] == "识别出 3 题"
        questions = draft["questions"]
        assert questions[0]["question_type"] == "mcq"
        assert questions[0]["score"] == 3.0
        assert questions[0]["page_start"] == 1 and questions[0]["page_end"] == 1
        assert [o["label"] for o in questions[0]["options"]] == ["A", "B", "C", "D"]
        assert questions[1]["score"] == 6.0
        assert questions[2]["page_start"] == 2

        listed = client.get(
            "/api/v1/papers/import-drafts?status=pending_review"
        ).json()
        assert any(item["id"] == draft["id"] for item in listed)

        detail = client.get(f"/api/v1/papers/import-drafts/{draft['id']}").json()
        assert detail["id"] == draft["id"]

        ok = client.post(
            f"/api/v1/papers/import-drafts/{draft['id']}/approve",
            json={"note": "题目与页码核对无误"},
        )
        assert ok.status_code == 200
        assert ok.json()["status"] == "approved"

        again = client.post(
            f"/api/v1/papers/import-drafts/{draft['id']}/approve", json={}
        )
        assert again.status_code == 409
        assert "终态" in again.json()["detail"]


def test_api_resource_not_found() -> None:
    with _client() as client:
        r = client.post(
            "/api/v1/papers/import-drafts", json={"resource_id": "res_missing_m506"}
        )
        assert r.status_code == 404
        assert "资源不存在" in r.json()["detail"]


def test_api_unparsed_409() -> None:
    """未解析资源 409 带原因（不虚报可抽取）。"""
    from datetime import datetime, timezone

    from app.domain.resource import ResourceRecord
    from app.repositories.resources import ResourceRepository

    async def _run() -> None:
        sm = client.app.state.sessionmaker
        record = ResourceRecord(
            id="res_m506_unparsed",
            media_type="application/pdf",
            title="试卷（未解析）",
            content_hash="hash-unparsed",
            storage_key="objects/unparsed",
            size_bytes=9,
            fetched_at=datetime.now(timezone.utc),
        )
        await ResourceRepository(sm).create(record)

    with _client() as client:
        asyncio.run(_run())
        r = client.post(
            "/api/v1/papers/import-drafts", json={"resource_id": "res_m506_unparsed"}
        )
        assert r.status_code == 409
        assert "未解析" in r.json()["detail"]
        assert "parse_status=pending" in r.json()["detail"]


def test_api_draft_not_found() -> None:
    with _client() as client:
        r = client.get("/api/v1/papers/import-drafts/pqd_missing")
        assert r.status_code == 404
        assert "草稿不存在" in r.json()["detail"]

        missing = client.post(
            "/api/v1/papers/import-drafts/pqd_missing/approve", json={"note": None}
        )
        assert missing.status_code == 404


def test_api_no_db_503() -> None:
    """无 DB：抽取与审核端点均 503（不虚报可用）。"""
    with TestClient(create_app(None)) as client:
        r = client.post(
            "/api/v1/papers/import-drafts", json={"resource_id": "res_x"}
        )
        assert r.status_code == 503
        list_r = client.get("/api/v1/papers/import-drafts")
        assert list_r.status_code == 503
