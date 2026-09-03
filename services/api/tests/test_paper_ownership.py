"""M10-03 试卷归属与可见性：系统公共卷 + 自有卷，旁路不放大（planner/selection）。

语义矩阵（M10-03 第一段）：
- seed 卷 owner_id=NULL = 系统公共卷，所有用户可见；
- auth on 导入卷归属 current_owner_id：本人可见，其他 learner 不可见，
  admin 在试卷列表同样不可见、直接开考 404（admin 不放大可见性）；
- auth off 导入保持 NULL（本地调试零破坏）；
- planner/selection 题库池与 /papers 同规则——不得经旁路读到他人私有卷的题；
- memory/postgres 双仓库可见性语义一致。
"""
from __future__ import annotations

import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domain.models import Angles, Option, Paper, Question
from app.main import create_app
from app.repositories.memory import MemoryRepository

SQLITE_URL = "sqlite+aiosqlite:///:memory:"
SECRET = "m10-03-paper-owner-secret-0123456789"
PG_URL = os.environ.get("AIOS_PG_TEST_URL")


@pytest.fixture()
def auth_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    get_settings.cache_clear()


@pytest.fixture()
def auth_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AUTH_SECRET", raising=False)
    get_settings.cache_clear()
    yield


class User:
    def __init__(self, client: TestClient, name: str) -> None:
        self.name = name
        self.headers = self._login(client, name)

    @staticmethod
    def _login(client: TestClient, name: str) -> dict[str, str]:
        body = {"username": name, "password": "password-123"}
        client.post("/api/v1/auth/register", json=body)
        token = client.post("/api/v1/auth/login", json=body).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}


PAPER = {
    "title": "M10-03 私有卷",
    "duration_seconds": 600,
    "questions": [
        {
            "question": {
                "question_type": "mcq",
                "stem": "私有题 1",
                "options": ["a", "b"],
                "answer": {"option_index": 1},
                "explanation": "x",
                "concept_ids": ["m10_03_private_concept"],
                "difficulty": 1,
            },
            "score": 1.0,
        }
    ],
}


def _import(client: TestClient, headers: dict, paper=None) -> str:
    result = client.post("/api/v1/papers/import", json=[paper or PAPER], headers=headers)
    assert result.status_code == 201, result.text
    return result.json()["imported"][0]


def _question_ids(client: TestClient, headers: dict, paper_id: str) -> list:
    exam = client.post(
        f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"}, headers=headers
    )
    assert exam.status_code == 201, exam.text
    return [question["id"] for question in exam.json()["questions"]]


def _sqlite_client() -> TestClient:
    return TestClient(create_app(SQLITE_URL))


# --- 可见性主链路（SQLite 替身 = PostgresRepository 同一路径）---


def test_imported_paper_is_private_to_owner(auth_on) -> None:
    with _sqlite_client() as client:
        alice = User(client, "alice_paper_m1003")
        bob = User(client, "bob_paper_m1003")
        admin = User(client, "admin_paper_m1003")

        paper_id = _import(client, alice.headers)
        bob_list = client.get("/api/v1/papers", headers=bob.headers).json()

        # 本人可见
        alice_list = client.get("/api/v1/papers", headers=alice.headers).json()
        assert paper_id in {paper["id"] for paper in alice_list}
        # 其他 learner 不可见
        assert paper_id not in {paper["id"] for paper in bob_list}
        # admin 在列表同样不可见（不放大）
        admin_list = client.get("/api/v1/papers", headers=admin.headers).json()
        assert paper_id not in {paper["id"] for paper in admin_list}
        # seed 系统公共卷对所有人可见
        assert "functions-basics" in {paper["id"] for paper in bob_list}
        # admin 的列表与 bob 一致（均为公共卷 + 各自为空的自有卷）
        assert {paper["id"] for paper in admin_list} == {paper["id"] for paper in bob_list}


def test_exam_creation_on_foreign_private_paper_is_404(auth_on) -> None:
    with _sqlite_client() as client:
        alice = User(client, "alice_exam_m1003")
        bob = User(client, "bob_exam_m1003")
        admin = User(client, "admin_exam_m1003")
        paper_id = _import(client, alice.headers)

        for headers in (bob.headers, admin.headers):
            response = client.post(
                f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"}, headers=headers
            )
            assert response.status_code == 404, headers
        owner = client.post(
            f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"}, headers=alice.headers
        )
        assert owner.status_code == 201


def test_planner_and_selection_do_not_amplify_visibility(auth_on) -> None:
    """旁路回归：planner/selection 题库池不得包含他人私有卷的题。"""
    with _sqlite_client() as client:
        alice = User(client, "alice_plan_m1003")
        bob = User(client, "bob_plan_m1003")
        paper_id = _import(client, alice.headers)
        alice_question_ids = _question_ids(client, alice.headers, paper_id)

        bob_plan = client.get("/api/v1/student/daily-plan", headers=bob.headers).json()
        bob_selection = client.get("/api/v1/student/selection", headers=bob.headers).json()
        plan_ids = {task["question_id"] for task in bob_plan["tasks"]}
        selection_ids = {item["question_id"] for item in bob_selection["items"]}
        assert not (plan_ids & set(alice_question_ids)), plan_ids & set(alice_question_ids)
        assert not (selection_ids & set(alice_question_ids)), selection_ids & set(alice_question_ids)

        # 本人旁路仍可见自己的私有卷（不缩小）
        alice_plan = client.get("/api/v1/student/daily-plan", headers=alice.headers).json()
        alice_ids = {task["question_id"] for task in alice_plan["tasks"]}
        assert set(alice_question_ids) <= alice_ids


def test_seed_papers_are_public_and_import_auth_off_is_null(auth_off) -> None:
    """auth off：导入保持 NULL（公共语义），存量行为零破坏。"""
    with _sqlite_client() as client:
        paper_id = _import(client, {})
        listed = client.get("/api/v1/papers").json()
        ids = {paper["id"] for paper in listed}
        assert paper_id in ids
        # seed 卷（系统公共卷）仍在
        assert "functions-basics" in ids
        assert client.post(f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"}).status_code == 201


# --- memory 仓库可见性（与 PostgresRepository 同语义）---


def _private_paper(paper_id: str) -> Paper:
    return Paper(
        id=paper_id,
        title="Memory 私有卷",
        subtitle="",
        source="imported",
        university=None,
        year=None,
        subject="imported",
        difficulty="unknown",
        duration_minutes=10,
        tags=("import",),
        origin_url=None,
        license="UNKNOWN",
        questions=(
            Question(
                id="q_mem_private",
                type="mcq",
                stem="mem",
                options=(Option(key="A", text="a"), Option(key="B", text="b")),
                answer="B",
                explanation="",
                angles=Angles(concept="", method="", mistake="", variant=""),
                knowledge=(),
                score=1.0,
                difficulty=1,
            ),
        ),
    )


def test_memory_repository_visibility_matches_postgres_semantics() -> None:
    async def run() -> None:
        repo = MemoryRepository()
        repo.add_paper(_private_paper("pap_mem_alice"), owner_id="user-alice")
        repo.add_paper(_private_paper("pap_mem_open"), owner_id=None)

        # 不过滤（auth off / 内部读取）：seed + 2 卷全见
        unfiltered = await repo.list_papers()
        assert {"pap_mem_alice", "pap_mem_open"} <= {paper.id for paper in unfiltered}

        # alice：公共卷 + 自有卷
        alice_visible = {paper.id for paper in await repo.list_papers(owner_id="user-alice")}
        assert "pap_mem_alice" in alice_visible
        assert "pap_mem_open" in alice_visible
        # bob：只见公共卷
        bob_visible = {paper.id for paper in await repo.list_papers(owner_id="user-bob")}
        assert "pap_mem_alice" not in bob_visible
        assert "pap_mem_open" in bob_visible

        assert await repo.get_paper("pap_mem_alice", owner_id="user-alice") is not None
        assert await repo.get_paper("pap_mem_alice", owner_id="user-bob") is None
        assert await repo.get_paper("pap_mem_open", owner_id="user-bob") is not None
        assert await repo.get_paper("pap_mem_alice") is not None  # 无过滤读取保持

    asyncio.run(run())


# --- 真实 PostgreSQL 集成（AIOS_PG_TEST_URL 门控）---


@pytest.mark.skipif(not PG_URL, reason="需要 AIOS_PG_TEST_URL 指向真实 PostgreSQL")
def test_paper_visibility_against_real_postgres(auth_on) -> None:
    """真实 PG：迁移后的 papers.owner_id 列 + 跨用户隔离与 SQLite 同语义。"""
    import uuid

    suffix = uuid.uuid4().hex[:8]
    with TestClient(create_app(PG_URL)) as client:  # type: ignore[arg-type]
        alice = User(client, f"alice_pg_{suffix}")
        bob = User(client, f"bob_pg_{suffix}")
        paper = dict(PAPER)
        paper["title"] = f"M10-03 PG 私有卷 {suffix}"
        paper_id = _import(client, alice.headers, paper)

        alice_ids = {p["id"] for p in client.get("/api/v1/papers", headers=alice.headers).json()}
        bob_ids = {p["id"] for p in client.get("/api/v1/papers", headers=bob.headers).json()}
        assert paper_id in alice_ids
        assert paper_id not in bob_ids
        assert client.post(
            f"/api/v1/papers/{paper_id}/exams", json={"mode": "exam"}, headers=bob.headers
        ).status_code == 404
