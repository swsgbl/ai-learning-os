"""M6-03 Citation eval API：抽样课程草稿引用的 evidence 有效率。

- POST /api/v1/eval/citation/runs：载入课程生成草稿 -> 提取引用 ->
  确定性抽样 -> 逐条对 chunk 语料判定 -> 落库 kind="citation"（201）；
  无草稿/无引用 422（不虚报有效率）；无 DB 503；
- GET /report：现算同款报告（不落库，无 DB 503）；
- GET /runs、GET /runs/{id}：kind=citation 运行回查；404。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.domain.citation_eval import (
    DEFAULT_SAMPLE_SIZE,
    extract_citations,
    run_citation_eval,
)

router = APIRouter(prefix="/api/v1/eval/citation", tags=["eval"])


class CitationReportOut(BaseModel):
    rule_versions: dict
    total_citations: int
    sampled_count: int
    valid_count: int
    validity_rate: float
    target: float
    passed: bool
    invalid: list[dict]


class CitationRunOut(CitationReportOut):
    id: int
    kind: str
    rule_version: str
    case_count: int
    agreement: float | None
    report: dict
    created_at: str


def _run_out(row: dict) -> CitationRunOut:
    """repo 视图 + 扁平化 report 组装运行视图（list/detail 复用）。"""
    return CitationRunOut(
        **row["report"],
        id=row["id"],
        kind=row["kind"],
        rule_version=row["rule_version"],
        case_count=row["case_count"],
        agreement=row["agreement"],
        report=row["report"],
        created_at=row["created_at"],
    )


def _deps(request: Request):
    chunks = request.app.state.chunks
    drafts = request.app.state.course_generation_drafts
    if chunks is None or drafts is None:
        raise HTTPException(status_code=503, detail="Citation eval requires a database")
    return chunks, drafts


async def _compute_report(request: Request, sample_size: int) -> dict:
    chunks, drafts = _deps(request)
    draft_rows = await drafts.list_by_status(None)
    plans = [row["plan"] for row in draft_rows]
    citations = extract_citations(plans)
    if not citations:
        raise HTTPException(status_code=422, detail="无可抽样的引用（课程草稿没有挂接任何资源）")
    resource_ids = sorted({c["resource_id"] for c in citations})
    chunk_texts: dict[str, list[str]] = {}
    for rid in resource_ids:
        rows = await chunks.list_chunks(rid)
        chunk_texts[rid] = [row["text"] for row in rows]
    return run_citation_eval(citations, chunk_texts.get, sample_size)


@router.post("/runs", response_model=CitationRunOut, status_code=201)
async def create_citation_run(request: Request, sample_size: int = DEFAULT_SAMPLE_SIZE) -> CitationRunOut:
    """抽样评测并落库 kind="citation"；无引用 422；无 DB 503。"""
    if sample_size < 1 or sample_size > 200:
        raise HTTPException(status_code=422, detail="sample_size 必须 1..200")
    report = await _compute_report(request, sample_size)
    repo = getattr(request.app.state, "eval_runs", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Citation eval requires a database")
    record = await repo.record(
        kind="citation", report=report,
        agreement=report["validity_rate"], case_count=report["sampled_count"],
    )
    return CitationRunOut(
        **report,
        id=record["id"],
        kind=record["kind"],
        rule_version=record["rule_version"],
        case_count=record["case_count"],
        agreement=record["agreement"],
        report=record["report"],
        created_at=record["created_at"],
    )


@router.get("/report", response_model=CitationReportOut)
async def citation_report(request: Request, sample_size: int = DEFAULT_SAMPLE_SIZE) -> CitationReportOut:
    """现算抽样报告（不落库）；同库态确定性。"""
    if sample_size < 1 or sample_size > 200:
        raise HTTPException(status_code=422, detail="sample_size 必须 1..200")
    return CitationReportOut(**await _compute_report(request, sample_size))


@router.get("/runs", response_model=list[CitationRunOut])
async def list_citation_runs(request: Request) -> list[CitationRunOut]:
    """kind=citation 运行列表（最新在前）。"""
    repo = getattr(request.app.state, "eval_runs", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Citation eval requires a database")
    return [_run_out(row) for row in await repo.list_runs(kind="citation")]


@router.get("/runs/{run_id}", response_model=CitationRunOut)
async def get_citation_run(run_id: int, request: Request) -> CitationRunOut:
    """运行详情：完整 report 可回查；404。"""
    repo = getattr(request.app.state, "eval_runs", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Citation eval requires a database")
    record = await repo.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="评测运行记录不存在")
    return _run_out(record)
