"""M3-02 Concept DAG：课程概念、先修关系和能力要求的可版本化建模（04 号文档 §2.3）。

- 概念本体（canonical_name/aliases/subject/difficulty 能力要求/parent 层级）存 concepts 表，
  跨版本 upsert 不删除；先修关系存 concept_edges 表（版本隔离）；每次发布生成不可变
  新版本（concept_dag_versions.version 递增），历史版本永远可回溯——「可版本化」验收。
- 校验为纯函数：先修引用必须落在节点集内（不悬空）、图必须无环（Kahn 拓扑）、
  difficulty ∈ [1,5]、parent 必须在节点集内、canonical_name 非空。
- 图表达用关系表（定版 §2.3：MVP 不引入图数据库）；课程生成 workflow（03 §4.4
  CONCEPT_DAG 阶段）与 M3-07 选题的前置查询都从版本快照消费。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

MAX_DIFFICULTY: Final = 5
MIN_DIFFICULTY: Final = 1


@dataclass(frozen=True, slots=True)
class ConceptNode:
    """概念节点：difficulty 即该概念的能力要求粒度（1-5）。"""

    id: str
    canonical_name: str
    aliases: tuple[str, ...] = ()
    subject: str = ""
    description: str = ""
    difficulty: int = 3
    parent_id: str | None = None
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConceptEdge:
    """先修关系：prerequisite_id 是 concept_id 的先修概念。"""

    prerequisite_id: str
    concept_id: str


@dataclass(frozen=True, slots=True)
class ConceptDag:
    """一个版本的完整概念图快照（不可变）。"""

    version: int
    note: str
    nodes: tuple[ConceptNode, ...]
    edges: tuple[ConceptEdge, ...]
    created_at: str = ""


class DagValidationError(ValueError):
    """概念图非法：消息面向 API 422 透出。"""


def validate_dag(nodes: tuple[ConceptNode, ...], edges: tuple[ConceptEdge, ...]) -> None:
    """发布前校验：引用完整性 + 无环 + 字段边界；失败抛 DagValidationError。"""
    if not nodes:
        raise DagValidationError("概念图至少需要一个概念")
    by_id = {node.id: node for node in nodes}
    if len(by_id) != len(nodes):
        raise DagValidationError("概念 id 重复")

    for node in nodes:
        if not node.canonical_name.strip():
            raise DagValidationError(f"概念 {node.id} 缺少 canonical_name")
        if not MIN_DIFFICULTY <= node.difficulty <= MAX_DIFFICULTY:
            raise DagValidationError(
                f"概念 {node.id} 的 difficulty 必须在 {MIN_DIFFICULTY}-{MAX_DIFFICULTY} 之间"
            )
        if node.parent_id is not None and node.parent_id not in by_id:
            raise DagValidationError(f"概念 {node.id} 的 parent {node.parent_id} 不在节点集内")
        if node.parent_id == node.id:
            raise DagValidationError(f"概念 {node.id} 不能以自己为父")

    for edge in edges:
        if edge.concept_id not in by_id or edge.prerequisite_id not in by_id:
            raise DagValidationError(
                f"先修边 {edge.prerequisite_id}->{edge.concept_id} 引用了未定义的概念"
            )
        if edge.prerequisite_id == edge.concept_id:
            raise DagValidationError(f"概念 {edge.concept_id} 不能以自己为先修")

    _assert_acyclic(by_id, edges)


def _assert_acyclic(by_id: dict[str, ConceptNode], edges: tuple[ConceptEdge, ...]) -> None:
    """Kahn 拓扑排序：无法消费完全部节点 = 存在环。先修边与 parent 层级一并纳入。"""
    indegree: dict[str, int] = {node_id: 0 for node_id in by_id}
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    for edge in edges:
        adjacency[edge.prerequisite_id].append(edge.concept_id)
        indegree[edge.concept_id] += 1
    for node in by_id.values():
        if node.parent_id is not None:
            adjacency[node.parent_id].append(node.id)
            indegree[node.id] += 1

    queue = [node_id for node_id, degree in indegree.items() if degree == 0]
    consumed = 0
    while queue:
        current = queue.pop()
        consumed += 1
        for successor in adjacency[current]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)
    if consumed != len(by_id):
        cyclic = sorted(node_id for node_id, degree in indegree.items() if degree > 0)
        raise DagValidationError(f"概念图存在环，涉及: {', '.join(cyclic)}")


def direct_prerequisites(dag: ConceptDag, concept_id: str) -> tuple[str, ...]:
    """某概念的直接先修（M3-07 选题/M5-07 课程生成的查询入口）。"""
    return tuple(
        edge.prerequisite_id for edge in dag.edges if edge.concept_id == concept_id
    )
