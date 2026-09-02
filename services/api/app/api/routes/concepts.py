"""M3-02 Concept DAG API：版本化概念图发布与查询（04 号文档 §2.3）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.routes.auth import audit_from_request, require_admin
from app.domain.concept_dag import (
    ConceptDag,
    ConceptEdge,
    ConceptNode,
    DagValidationError,
)
from app.repositories.concept_dag import ConceptDagRepository

router = APIRouter(prefix="/api/v1/concept-dag", tags=["concepts"])


class ConceptNodeIn(BaseModel):
    id: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_:\-]+$")
    canonical_name: str = Field(min_length=1, max_length=256)
    aliases: list[str] = Field(default_factory=list, max_length=32)
    subject: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=2048)
    difficulty: int = Field(default=3, ge=1, le=5)
    parent_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=64)


class ConceptEdgeIn(BaseModel):
    prerequisite_id: str = Field(min_length=2, max_length=64)
    concept_id: str = Field(min_length=2, max_length=64)


class PublishDagRequest(BaseModel):
    """全量快照发布：nodes + edges 一起提交，生成新的不可变版本。"""

    nodes: list[ConceptNodeIn] = Field(min_length=1, max_length=500)
    edges: list[ConceptEdgeIn] = Field(default_factory=list, max_length=2000)
    note: str = Field(default="", max_length=512)


class ConceptNodeOut(ConceptNodeIn):
    pass


class ConceptDagOut(BaseModel):
    version: int
    note: str
    created_at: str
    nodes: list[ConceptNodeOut]
    edges: list[ConceptEdgeIn]


class DagVersionOut(BaseModel):
    version: int
    note: str
    created_at: str


def _repo(request: Request) -> ConceptDagRepository:
    repo = getattr(request.app.state, "concept_dag", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Concept DAG requires a database")
    return repo


def _dag_out(dag: ConceptDag) -> ConceptDagOut:
    return ConceptDagOut(
        version=dag.version,
        note=dag.note,
        created_at=dag.created_at,
        nodes=[
            ConceptNodeOut(
                id=node.id,
                canonical_name=node.canonical_name,
                aliases=list(node.aliases),
                subject=node.subject,
                description=node.description,
                difficulty=node.difficulty,
                parent_id=node.parent_id,
                evidence_ids=list(node.evidence_ids),
            )
            for node in dag.nodes
        ],
        edges=[
            ConceptEdgeIn(prerequisite_id=edge.prerequisite_id, concept_id=edge.concept_id)
            for edge in dag.edges
        ],
    )


@router.get("", response_model=ConceptDagOut)
async def get_latest_dag(request: Request) -> ConceptDagOut:
    """最新版本概念图；尚未发布任何版本时 404。"""
    dag = await _repo(request).latest()
    if dag is None:
        raise HTTPException(status_code=404, detail="尚未发布概念图版本")
    return _dag_out(dag)


@router.get("/versions", response_model=list[DagVersionOut])
async def list_dag_versions(request: Request) -> list[DagVersionOut]:
    return [
        DagVersionOut(version=item["version"], note=item["note"], created_at=item["created_at"])
        for item in await _repo(request).list_versions()
    ]


@router.get("/versions/{version}", response_model=ConceptDagOut)
async def get_dag_version(version: int, request: Request) -> ConceptDagOut:
    """历史版本回溯（不可变快照）。"""
    dag = await _repo(request).get_version(version)
    if dag is None:
        raise HTTPException(status_code=404, detail=f"概念图版本 {version} 不存在")
    return _dag_out(dag)


@router.post("/versions", response_model=ConceptDagOut, status_code=201)
async def publish_dag(payload: PublishDagRequest, request: Request) -> ConceptDagOut:
    """发布新版本：校验（引用完整/无环/难度边界）后原子落库。"""
    await require_admin(request)  # M9-04: 概念图发布是全局治理动作
    nodes = tuple(
        ConceptNode(
            id=node.id,
            canonical_name=node.canonical_name,
            aliases=tuple(node.aliases),
            subject=node.subject,
            description=node.description,
            difficulty=node.difficulty,
            parent_id=node.parent_id,
            evidence_ids=tuple(node.evidence_ids),
        )
        for node in payload.nodes
    )
    edges = tuple(
        ConceptEdge(prerequisite_id=edge.prerequisite_id, concept_id=edge.concept_id)
        for edge in payload.edges
    )
    try:
        dag = await _repo(request).publish(nodes, edges, payload.note)
    except DagValidationError as cause:
        raise HTTPException(status_code=422, detail=str(cause)) from cause
    await audit_from_request(
        request, action="dag.publish", target_type="concept_dag",
        target_id=f"v{dag.version}",
        after={"version": dag.version, "nodes": len(dag.nodes), "edges": len(dag.edges)},
    )
    return _dag_out(dag)
