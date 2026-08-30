"""M2-02 试卷 JSON 导入：逐行报错；成功后顺序/分值/答案/解析不变。"""
from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.orm import QuestionRow
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _paper(title: str) -> dict:
    return {
        "title": title,
        "duration_seconds": 1500,
        "policy": "exam",
        "questions": [
            {
                "question": {
                    "question_type": "mcq",
                    "stem": "第一题：进程与程序的区别是？",
                    "options": ["选项一", "选项二"],
                    "answer": {"option_index": 1},
                    "explanation": "解析一",
                },
                "score": 2.5,
            },
            {
                "question": {
                    "question_type": "true_false",
                    "stem": "第二题：TCP 是面向连接的协议。",
                    "answer": {"value": True},
                    "explanation": "解析二",
                },
                "score": 1.0,
            },
            {
                "question": {
                    "question_type": "short_answer",
                    "stem": "第三题：写出快速排序的平均时间复杂度。",
                    "answer": {"accepted": ["O(n log n)"]},
                    "explanation": "解析三",
                },
                "score": 3.0,
            },
        ],
    }


def _db_questions(client: TestClient, paper_id: str) -> list[QuestionRow]:
    async def _fetch() -> list[QuestionRow]:
        async with client.app.state.sessionmaker() as session:
            rows = (
                await session.execute(
                    select(QuestionRow)
                    .where(QuestionRow.paper_id == paper_id)
                    .order_by(QuestionRow.sort_order)
                )
            ).scalars().all()
        return list(rows)

    return asyncio.run(_fetch())


def test_import_success_preserves_order_score_answer_explanation() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        response = client.post("/api/v1/papers/import", json=[_paper("导入卷·数据结构")])
        assert response.status_code == 201
        (paper_id,) = response.json()["imported"]

        # 试卷出现在列表，时长按分钟向上取整
        papers = {p["id"]: p for p in client.get("/api/v1/papers").json()}
        imported = papers[paper_id]
        assert imported["title"] == "导入卷·数据结构"
        assert imported["duration_minutes"] == 25

        # 既有考试流可读取导入卷：顺序与选项原样
        started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
        assert started.status_code == 201
        questions = started.json()["questions"]
        assert [q["stem"] for q in questions] == [
            "第一题：进程与程序的区别是？",
            "第二题：TCP 是面向连接的协议。",
            "第三题：写出快速排序的平均时间复杂度。",
        ]
        assert questions[0]["options"] == [
            {"key": "A", "text": "选项一"},
            {"key": "B", "text": "选项二"},
        ]
        assert all("answer" not in q for q in questions)

        # 交卷后：答案与解析逐字保留
        exam_id = started.json()["exam_id"]
        for index, question in enumerate(questions, start=1):
            client.put(
                f"/api/v1/exams/{exam_id}/answers",
                json={"sequence": index, "question_id": question["id"], "answer": "X"},
            )
        report = client.post(f"/api/v1/exams/{exam_id}/submit", json={}).json()
        assert [item["expected"] for item in report["items"]] == [
            "B",
            "T",
            json.dumps({"accepted": ["O(n log n)"]}, ensure_ascii=False),
        ]
        assert [item["explanation"] for item in report["items"]] == ["解析一", "解析二", "解析三"]

        # 分值与排序直接查库断言
        rows = _db_questions(client, paper_id)
        assert [row.sort_order for row in rows] == [1, 2, 3]
        assert [row.score for row in rows] == [2.5, 1.0, 3.0]


def test_import_rejects_all_or_nothing_with_line_errors() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        good = _paper("合法卷")
        bad = _paper("非法卷")
        bad["questions"][0]["question"]["options"] = []  # mcq 至少 2 选项

        response = client.post("/api/v1/papers/import", json=[good, bad])
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["imported"] == []
        assert len(detail["errors"]) == 1
        line = detail["errors"][0]
        assert line["index"] == 1
        first_error = line["errors"][0]
        assert "选项" in first_error["msg"] or "options" in str(first_error["loc"])

        # 全或无：合法卷也不得落库
        titles = {p["title"] for p in client.get("/api/v1/papers").json()}
        assert "合法卷" not in titles
        assert "非法卷" not in titles
