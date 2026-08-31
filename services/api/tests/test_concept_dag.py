"""M3-02 Concept DAG：课程概念、先修关系和能力要求可版本化。

核心验收：发布修改后的新版本时，历史版本不可变且可回溯。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain.concept_dag import (
    ConceptDag,
    ConceptEdge,
    ConceptNode,
    DagValidationError,
    direct_prerequisites,
    validate_dag,
)
from app.main import create_app

SQLITE_URL = "sqlite+aiosqlite:///:memory:"

SORTING_DAG = {
    "nodes": [
        {"id": "loop", "canonical_name": "循环与递归", "subject": "cs", "difficulty": 2},
        {"id": "divide", "canonical_name": "分治", "subject": "cs", "difficulty": 3},
        {"id": "quicksort", "canonical_name": "快速排序", "aliases": ["快排"], "subject": "cs", "difficulty": 4, "parent_id": "divide"},
    ],
    "edges": [
        {"prerequisite_id": "loop", "concept_id": "divide"},
        {"prerequisite_id": "divide", "concept_id": "quicksort"},
    ],
    "note": "排序入门图谱",
}


def _post(client: TestClient, payload: dict):
    return client.post("/api/v1/concept-dag/versions", json=payload)


def test_publish_and_read_latest_version() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        response = _post(client, SORTING_DAG)
        assert response.status_code == 201
        assert response.json()["version"] == 1

        latest = client.get("/api/v1/concept-dag").json()
        assert latest["version"] == 1
        assert latest["note"] == "排序入门图谱"
        assert {node["id"] for node in latest["nodes"]} == {"loop", "divide", "quicksort"}
        assert {"prerequisite_id": "divide", "concept_id": "quicksort"} in latest["edges"]
        assert client.get("/api/v1/concept-dag").json() == latest  # 幂等读


def test_new_version_is_immutable_and_retrievable() -> None:
    """可版本化核心：v2 发布后 v1 快照原样可回溯。"""
    with TestClient(create_app(SQLITE_URL)) as client:
        v1 = _post(client, SORTING_DAG).json()

        evolved = {
            "nodes": SORTING_DAG["nodes"]
            + [{"id": "heap", "canonical_name": "堆结构", "subject": "cs", "difficulty": 3}],
            "edges": SORTING_DAG["edges"]
            + [{"prerequisite_id": "loop", "concept_id": "heap"}],
            "note": "加入堆",
        }
        v2 = _post(client, evolved).json()
        assert v2["version"] == 2

        # v1 不可变：节点集、边、难度都与发布时一致
        reread_v1 = client.get("/api/v1/concept-dag/versions/1").json()
        assert reread_v1 == v1
        assert "heap" not in {node["id"] for node in reread_v1["nodes"]}
        # v2 含新增概念与新边
        assert client.get("/api/v1/concept-dag").json()["version"] == 2
        assert "heap" in {node["id"] for node in v2["nodes"]}

        versions = client.get("/api/v1/concept-dag/versions").json()
        assert [item["version"] for item in versions] == [2, 1]


def test_prerequisite_cycle_rejected() -> None:
    payload = {
        "nodes": [
            {"id": "ca", "canonical_name": "A"},
            {"id": "cb", "canonical_name": "B"},
        ],
        "edges": [
            {"prerequisite_id": "ca", "concept_id": "cb"},
            {"prerequisite_id": "cb", "concept_id": "ca"},
        ],
    }
    with TestClient(create_app(SQLITE_URL)) as client:
        response = _post(client, payload)
        assert response.status_code == 422
        assert "环" in response.json()["detail"]


def test_parent_cycle_rejected() -> None:
    payload = {
        "nodes": [
            {"id": "ca", "canonical_name": "A", "parent_id": "cb"},
            {"id": "cb", "canonical_name": "B", "parent_id": "ca"},
        ],
    }
    with TestClient(create_app(SQLITE_URL)) as client:
        assert _post(client, payload).status_code == 422


def test_dangling_edge_rejected() -> None:
    payload = {
        "nodes": [{"id": "ca", "canonical_name": "A"}],
        "edges": [{"prerequisite_id": "ca", "concept_id": "ghost"}],
    }
    with TestClient(create_app(SQLITE_URL)) as client:
        response = _post(client, payload)
        assert response.status_code == 422
        assert "未定义" in response.json()["detail"]


def test_difficulty_out_of_range_rejected() -> None:
    payload = {"nodes": [{"id": "ca", "canonical_name": "A", "difficulty": 6}]}
    with TestClient(create_app(SQLITE_URL)) as client:
        assert _post(client, payload).status_code == 422  # pydantic ge/le


def test_unknown_version_returns_404() -> None:
    with TestClient(create_app(SQLITE_URL)) as client:
        assert client.get("/api/v1/concept-dag").status_code == 404
        assert client.get("/api/v1/concept-dag/versions/99").status_code == 404


def test_validate_dag_rejects_duplicate_ids_and_empty_name() -> None:
    nodes = (
        ConceptNode(id="a", canonical_name="A"),
        ConceptNode(id="a", canonical_name="A2"),
    )
    with pytest.raises(DagValidationError, match="重复"):
        validate_dag(nodes, ())
    with pytest.raises(DagValidationError, match="canonical_name"):
        validate_dag((ConceptNode(id="a", canonical_name="  "),), ())


def test_direct_prerequisites_queries_snapshot() -> None:
    dag = ConceptDag(
        version=1,
        note="",
        nodes=(),
        edges=(
            ConceptEdge("loop", "divide"),
            ConceptEdge("divide", "quicksort"),
            ConceptEdge("loop", "quicksort"),
        ),
    )
    assert set(direct_prerequisites(dag, "quicksort")) == {"divide", "loop"}
    assert direct_prerequisites(dag, "loop") == ()


def test_concept_difficulty_is_ability_requirement_granularity() -> None:
    """难度即能力要求粒度（1-5，与 QuestionSpec 同语义）。"""
    node = ConceptNode(id="sorting_quicksort", canonical_name="快速排序", difficulty=4)
    validate_dag((node,), ())
    assert node.difficulty == 4
