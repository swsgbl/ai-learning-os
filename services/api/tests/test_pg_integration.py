"""真实 PostgreSQL 集成测试。

设置 AIOS_PG_TEST_URL（如 docker compose 里的 postgres）才运行，否则跳过：
    AIOS_PG_TEST_URL=postgresql+asyncpg://aios:aios@127.0.0.1:5432/ai_learning_os \
        .venv/Scripts/python -m pytest services/api/tests/test_pg_integration.py
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

PG_URL = os.environ.get("AIOS_PG_TEST_URL")

pytestmark = pytest.mark.skipif(not PG_URL, reason="需要 AIOS_PG_TEST_URL 指向真实 PostgreSQL")


def test_exam_flow_against_real_postgres() -> None:
    with TestClient(create_app(PG_URL)) as client:  # type: ignore[arg-type]
        papers = client.get("/api/v1/papers").json()
        assert papers

        started = client.post("/api/v1/papers/functions-basics/exams", json={"mode": "exam"})
        assert started.status_code == 201
        exam = started.json()
        raw = str(exam).lower()
        assert '"answer"' not in raw and "explanation" not in raw

        exam_id = exam["exam_id"]
        for index, question in enumerate(exam["questions"], start=1):
            assert (
                client.put(
                    f"/api/v1/exams/{exam_id}/answers",
                    json={"sequence": index, "question_id": question["id"], "answer": "B"},
                ).status_code
                == 200
            )

        first = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
        second = client.post(f"/api/v1/exams/{exam_id}/submit", json={})
        assert first.json() == second.json()
        assert client.get(f"/api/v1/exams/{exam_id}/submission").status_code == 200


def test_essay_rubric_flow_against_real_postgres() -> None:
    """M2-10：真实 PG 下 essay 走 rubric 管线（evidence 表为空时 keyword judge 不引用 evidence，可落分）。"""
    paper = {
        "title": "PG rubric 验证卷",
        "duration_seconds": 600,
        "questions": [
            {
                "question": {
                    "question_type": "essay",
                    "stem": "论述归并排序",
                    "answer": {"rubric_points": ["分治", "合并"]},
                    "explanation": "分治 + 合并",
                },
                "score": 1.0,
            },
        ],
    }
    with TestClient(create_app(PG_URL)) as client:  # type: ignore[arg-type]
        (paper_id,) = client.post("/api/v1/papers/import", json=[paper]).json()["imported"]
        started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
        assert started.status_code == 201
        exam_id = started.json()["exam_id"]
        (question,) = started.json()["questions"]
        assert (
            client.put(
                f"/api/v1/exams/{exam_id}/answers",
                json={"sequence": 1, "question_id": question["id"], "answer": "归并排序是分治算法，逐层合并"},
            ).status_code
            == 200
        )
        report = client.post(f"/api/v1/exams/{exam_id}/submit", json={}).json()
        (item,) = report["items"]
        assert item["correct"] is True
        assert item["rubric"]["rule_version"] == "rubric-v1"
        assert item["rubric"]["score_ratio"] == 1.0
        assert {c["point"] for c in item["rubric"]["criteria"]} == {"分治", "合并"}


def test_exam_report_flow_against_real_postgres() -> None:
    """M2-11：真实 PG 下报告聚合端到端——题分/概念分/错题/补救任务从落库数据构建。"""
    paper = {
        "title": "PG 报告验证卷",
        "duration_seconds": 600,
        "questions": [
            {
                "question": {
                    "question_type": "mcq",
                    "stem": "1+1=?",
                    "options": ["1", "2"],
                    "answer": {"option_index": 1},
                    "explanation": "加法",
                    "concept_ids": ["arithmetic"],
                },
                "score": 2.0,
            },
            {
                "question": {
                    "question_type": "numeric",
                    "stem": "圆周率保留两位",
                    "answer": {"value": 3.14, "tolerance": 0.01},
                    "explanation": "pi",
                    "concept_ids": ["geometry"],
                },
                "score": 3.0,
            },
        ],
    }
    with TestClient(create_app(PG_URL)) as client:  # type: ignore[arg-type]
        (paper_id,) = client.post("/api/v1/papers/import", json=[paper]).json()["imported"]
        started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
        exam_id = started.json()["exam_id"]
        questions = started.json()["questions"]
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": questions[0]["id"], "answer": "B"},
        )
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 2, "question_id": questions[1]["id"], "answer": "3.2"},
        )
        client.post(f"/api/v1/exams/{exam_id}/submit", json={})

        report = client.get(f"/api/v1/exams/{exam_id}/report").json()
        assert report["score_max"] == 5.0
        assert report["score_earned"] == 2.0
        concepts = {c["concept"]: c for c in report["concepts"]}
        assert concepts["arithmetic"]["ratio"] == 1.0
        assert concepts["geometry"]["ratio"] == 0.0
        (mistake,) = report["mistakes"]
        assert mistake["question_id"] == questions[1]["id"]
        (task_id,) = mistake["remediation_task_ids"]
        assert report["remediation_tasks"][task_id]["detail"] == "geometry"


def test_learning_events_flow_against_real_postgres() -> None:
    """M3-01：真实 PG 下标准化学习事件端到端——事件源重放、attempt、判分一致。"""
    paper = {
        "title": "PG 学习事件验证卷",
        "duration_seconds": 600,
        "questions": [
            {
                "question": {
                    "question_type": "mcq",
                    "stem": "1+1=?",
                    "options": ["1", "2"],
                    "answer": {"option_index": 1},
                    "explanation": "加法",
                    "concept_ids": ["arithmetic"],
                    "difficulty": 5,
                },
                "score": 1.0,
            },
        ],
    }
    with TestClient(create_app(PG_URL)) as client:  # type: ignore[arg-type]
        (paper_id,) = client.post("/api/v1/papers/import", json=[paper]).json()["imported"]
        started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
        exam_id = started.json()["exam_id"]
        (question,) = started.json()["questions"]
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": question["id"], "answer": "A"},
        )
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 2, "question_id": question["id"], "answer": "B"},
        )

        stream = client.get(f"/api/v1/exams/{exam_id}/learning-events").json()
        (first, second) = stream["events"]
        assert (first["attempt_number"], first["correctness"]) == (1, False)
        assert (second["attempt_number"], second["correctness"]) == (2, True)
        assert first["concept_ids"] == ["arithmetic"]
        assert first["difficulty"] == 5  # QuestionSpec.difficulty 落库并透传到学习事件
        assert first["event_type"] == "answer"
        assert first["hint_used"] is False
        assert second["latency_ms"] >= 0

        replay = client.get(f"/api/v1/exams/{exam_id}/learning-events").json()
        assert replay == stream


def test_concept_dag_versioning_against_real_postgres() -> None:
    """M3-02：真实 PG 下概念图版本化——新版本发布、历史版本不可变回溯。"""
    with TestClient(create_app(PG_URL)) as client:  # type: ignore[arg-type]
        v1 = client.post(
            "/api/v1/concept-dag/versions",
            json={
                "nodes": [
                    {"id": "pg_loop", "canonical_name": "循环", "subject": "cs", "difficulty": 2},
                    {"id": "pg_recur", "canonical_name": "递归", "subject": "cs", "difficulty": 3},
                ],
                "edges": [{"prerequisite_id": "pg_loop", "concept_id": "pg_recur"}],
                "note": "PG v1",
            },
        )
        assert v1.status_code == 201
        version = v1.json()["version"]

        v2 = client.post(
            "/api/v1/concept-dag/versions",
            json={
                "nodes": [
                    {"id": "pg_loop", "canonical_name": "循环", "subject": "cs", "difficulty": 2},
                    {"id": "pg_recur", "canonical_name": "递归", "subject": "cs", "difficulty": 3, "parent_id": "pg_loop"},
                ],
                "note": "PG v2",
            },
        )
        assert v2.status_code == 201
        assert v2.json()["version"] == version + 1

        reread = client.get(f"/api/v1/concept-dag/versions/{version}").json()
        assert reread == v1.json()  # 历史版本不可变
        latest = client.get("/api/v1/concept-dag").json()
        assert latest["version"] == version + 1


def test_student_state_recompute_idempotent_against_real_postgres() -> None:
    """M3-03：真实 PG 下概念状态全量重算——同事件同 now 两次重算结果逐字段全等。

    PG 主库跨运行持久（历史考试事件累积），概念 ID 带运行级唯一后缀做绝对断言。
    """
    from uuid import uuid4

    suffix = uuid4().hex[:8]
    arith, geo = f"pg_arith_{suffix}", f"pg_geo_{suffix}"
    paper = {
        "title": "PG 学生状态验证卷",
        "duration_seconds": 600,
        "questions": [
            {
                "question": {
                    "question_type": "mcq",
                    "stem": "1+1=?",
                    "options": ["1", "2"],
                    "answer": {"option_index": 1},
                    "explanation": "加法",
                    "concept_ids": [arith],
                    "difficulty": 2,
                },
                "score": 1.0,
            },
            {
                "question": {
                    "question_type": "numeric",
                    "stem": "圆周率保留两位",
                    "answer": {"value": 3.14, "tolerance": 0.01},
                    "explanation": "pi",
                    "concept_ids": [geo],
                    "difficulty": 4,
                },
                "score": 1.0,
            },
        ],
    }
    fixed_now = "2030-01-01T00:00:00+00:00"
    with TestClient(create_app(PG_URL)) as client:  # type: ignore[arg-type]
        (paper_id,) = client.post("/api/v1/papers/import", json=[paper]).json()["imported"]
        started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
        exam_id = started.json()["exam_id"]
        questions = started.json()["questions"]
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": questions[0]["id"], "answer": "B"},
        )
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 2, "question_id": questions[1]["id"], "answer": "9.9"},
        )
        assert client.post(f"/api/v1/exams/{exam_id}/submit", json={}).status_code == 200

        first = client.post("/api/v1/student/states/recompute", params={"now": fixed_now}).json()
        assert first["concept_count"] >= 2
        by_concept = {state["concept_id"]: state for state in first["states"]}
        assert by_concept[arith]["correct_count"] == 1
        assert by_concept[geo]["wrong_count"] == 1
        assert by_concept[arith]["mastery"] > by_concept[geo]["mastery"]
        assert geo in first["weak_concepts"]  # 零掌握进薄弱列表

        second = client.post("/api/v1/student/states/recompute", params={"now": fixed_now}).json()
        assert second == first  # 更新幂等
        assert client.get("/api/v1/student/states").json() == first


def test_misconception_candidate_lifecycle_against_real_postgres() -> None:
    """M3-04：真实 PG 下误解候选——单次错误 candidate，独立题证据 ×3 升 confirmed，重算幂等。

    PG 主库跨运行持久，概念/答案带运行级唯一后缀做绝对断言（错误答案后缀保证 pattern 唯一）。
    """
    from uuid import uuid4

    suffix = uuid4().hex[:8]
    concept = f"pg_mis_{suffix}"
    wrong = f"W{suffix}"  # 错误答案签名（mcq 无此选项，必判错）
    paper = {
        "title": "PG 误解候选验证卷",
        "duration_seconds": 1800,
        "questions": [
            {
                "question": {
                    "question_type": "mcq",
                    "stem": f"题{i}",
                    "options": ["A", "B"],
                    "answer": {"option_index": 1},
                    "explanation": "B 对",
                    "concept_ids": [concept],
                    "difficulty": 2,
                },
                "score": 1.0,
            }
            for i in (1, 2, 3)
        ],
    }
    fixed_now = "2030-01-01T00:00:00+00:00"
    with TestClient(create_app(PG_URL)) as client:  # type: ignore[arg-type]
        (paper_id,) = client.post("/api/v1/papers/import", json=[paper]).json()["imported"]

        started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
        exam_id, questions = started.json()["exam_id"], started.json()["questions"]
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": questions[0]["id"], "answer": wrong},
        )
        assert client.post(f"/api/v1/exams/{exam_id}/submit", json={}).status_code == 200

        first = client.post(
            "/api/v1/student/misconceptions/recompute", params={"now": fixed_now}
        ).json()
        (item,) = [c for c in first["candidates"] if c["concept_id"] == concept]
        assert item["pattern"] == wrong.lower()
        assert item["status"] == "candidate" and item["independent_count"] == 1

        for question in questions[1:]:
            started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
            exam_id = started.json()["exam_id"]
            client.put(
                f"/api/v1/exams/{exam_id}/answers",
                json={"sequence": 1, "question_id": question["id"], "answer": wrong},
            )
            assert client.post(f"/api/v1/exams/{exam_id}/submit", json={}).status_code == 200

        second = client.post(
            "/api/v1/student/misconceptions/recompute", params={"now": fixed_now}
        ).json()
        (item,) = [c for c in second["candidates"] if c["concept_id"] == concept]
        assert item["status"] == "confirmed" and item["independent_count"] == 3
        again = client.post(
            "/api/v1/student/misconceptions/recompute", params={"now": fixed_now}
        ).json()
        assert again == second  # 更新幂等
        concept_only = {
            "candidate_count": 1,
            "candidates": [c for c in second["candidates"] if c["concept_id"] == concept],
        }
        assert client.get(f"/api/v1/student/misconceptions/{concept}").json() == concept_only


def test_review_queue_projection_against_real_postgres() -> None:
    """M3-05：真实 PG 下复习队列为事件实时投影——错题生成 next_review_at，重放幂等。"""
    from uuid import uuid4

    suffix = uuid4().hex[:8]
    concept = f"pg_rev_{suffix}"
    paper = {
        "title": "PG 复习调度验证卷",
        "duration_seconds": 1800,
        "questions": [
            {
                "question": {
                    "question_type": "mcq",
                    "stem": f"题{i}",
                    "options": ["A", "B"],
                    "answer": {"option_index": 1},
                    "explanation": "B 对",
                    "concept_ids": [concept],
                    "difficulty": 2,
                },
                "score": 1.0,
            }
            for i in (1, 2)
        ],
    }
    fixed_now = "2030-01-01T00:00:00+00:00"
    with TestClient(create_app(PG_URL)) as client:  # type: ignore[arg-type]
        (paper_id,) = client.post("/api/v1/papers/import", json=[paper]).json()["imported"]
        started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
        exam_id = started.json()["exam_id"]
        questions = started.json()["questions"]
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": questions[0]["id"], "answer": "A"},  # 错
        )
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 2, "question_id": questions[1]["id"], "answer": "B"},  # 对
        )
        assert client.post(f"/api/v1/exams/{exam_id}/submit", json={}).status_code == 200

        first = client.get("/api/v1/student/review-queue", params={"now": fixed_now}).json()
        target = [item for item in first["items"] if item["concept_ids"] == [concept]]
        assert len(target) == 1  # 只有答错题入队（历史运行同概念已被唯一后缀隔离）
        (item,) = target
        assert item["status"] == "retry"
        assert item["question_id"] == questions[0]["id"]
        assert item["next_review_at"] > item["last_seen_at"]

        again = client.get("/api/v1/student/review-queue", params={"now": fixed_now}).json()
        assert again == first  # 实时投影重放幂等


def test_daily_plan_against_real_postgres() -> None:
    """M3-06：真实 PG 下今日计划聚合三投影——结构完整、每任务带 reason、重放幂等、now 非法 422。

    共享主库存在大量历史错题（overdue_ratio 远高于本次运行新错题），
    quota 内任务几乎必然被历史题占据，故本测试验证端到端结构与幂等，
    「错题进入次日复习」的语义由干净库的 SQLite 端到端用例覆盖。
    """
    from uuid import uuid4

    suffix = uuid4().hex[:8]
    concept = f"pg_plan_{suffix}"
    paper = {
        "title": "PG 今日计划验证卷",
        "duration_seconds": 1800,
        "questions": [
            {
                "question": {
                    "question_type": "mcq",
                    "stem": f"计划题{i}",
                    "options": ["A", "B"],
                    "answer": {"option_index": 1},
                    "explanation": "B 对",
                    "concept_ids": [concept],
                    "difficulty": 3,
                },
                "score": 1.0,
            }
            for i in (1, 2)
        ],
    }
    fixed_now = "2030-01-01T00:00:00+00:00"
    with TestClient(create_app(PG_URL)) as client:  # type: ignore[arg-type]
        (paper_id,) = client.post("/api/v1/papers/import", json=[paper]).json()["imported"]
        started = client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"})
        exam_id = started.json()["exam_id"]
        questions = started.json()["questions"]
        client.put(
            f"/api/v1/exams/{exam_id}/answers",
            json={"sequence": 1, "question_id": questions[0]["id"], "answer": "A"},  # 错
        )
        assert client.post(f"/api/v1/exams/{exam_id}/submit", json={}).status_code == 200

        plan = client.get("/api/v1/student/daily-plan", params={"now": fixed_now}).json()
        kinds = [task["kind"] for task in plan["tasks"]]
        assert set(kinds) <= {"review", "mistake_retry", "new_learning"}
        assert plan["task_count"] == len(plan["tasks"])
        assert plan["task_count"] == (
            plan["review_count"] + plan["mistake_retry_count"] + plan["new_learning_count"]
        )
        assert kinds.count("review") == plan["review_count"]
        question_ids = [task["question_id"] for task in plan["tasks"]]
        assert len(question_ids) == len(set(question_ids))  # 同一题不重复入选
        for task in plan["tasks"]:
            assert task["reason"].strip()  # 解释为什么被选中
            assert task["concept_ids"] or task["kind"] == "new_learning"

        # 本次运行的错题（唯一后缀概念）若被配额选中，必须以 review 形态出现且概念正确
        by_id = {task["question_id"]: task for task in plan["tasks"]}
        if questions[0]["id"] in by_id:
            assert by_id[questions[0]["id"]]["kind"] == "review"
            assert by_id[questions[0]["id"]]["concept_ids"] == [concept]

        again = client.get("/api/v1/student/daily-plan", params={"now": fixed_now}).json()
        assert again == plan  # 实时投影重放幂等

        assert (
            client.get("/api/v1/student/daily-plan", params={"now": "bad"}).status_code == 422
        )


def test_concurrent_answer_writers_get_explicit_outcome() -> None:
    """M2-05 并发同 sequence 写入：一个成功一个明确拒绝，绝无未处理 IntegrityError。"""
    import asyncio

    from app.db.session import create_engine, make_sessionmaker, prepare_database
    from app.repositories.postgres import PostgresRepository as Repo
    from app.repositories.seed import seed_papers

    async def _flow() -> None:
        engine = create_engine(PG_URL)  # type: ignore[arg-type]
        await prepare_database(engine, PG_URL)  # type: ignore[arg-type]
        repo = Repo(make_sessionmaker(engine))
        paper = next(p for p in seed_papers() if p.id == "functions-basics")
        record = await repo.create_exam(paper, "exam")
        question = paper.questions[0]

        results = await asyncio.gather(
            repo.save_answer(record.exam_id, 1, question.id, "A"),
            repo.save_answer(record.exam_id, 1, question.id, "B"),
            return_exceptions=True,
        )
        outcomes = sorted(type(result).__name__ for result in results)
        assert outcomes == ["ExamSessionRecord", "ValueError"], (
            f"并发结果必须是一个成功一个明确拒绝，实际: {outcomes}"
        )
        await engine.dispose()

    asyncio.run(_flow())
