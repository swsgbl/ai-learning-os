"""M6-01 Eval API：golden set agreement report（可重复生成）+ 运行记录落库回查。

- GET  /api/v1/eval/grading/report：现算报告（确定性，无 DB 依赖，恒可用）；
- POST /api/v1/eval/grading/runs：运行并落库（rule_version/agreement/report 留痕）；
- GET  /api/v1/eval/grading/runs / runs/{id}：回查评测运行。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.domain.eval_framework import run_grading_eval

router = APIRouter(prefix="/api/v1/eval/grading", tags=["eval"])


class ReportOut(BaseModel):
    rule_versions: dict
    case_count: int
    covered_types: list[str]
    coverage_complete: bool
    agreement: float | None
    per_type: list[dict]
    mismatches: list[dict]


class RunOut(BaseModel):
    id: int
    kind: str
    rule_version: str
    case_count: int
    agreement: float | None
    report: dict
    created_at: str


def _repo(request: Request):
    repo = getattr(request.app.state, "eval_runs", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Eval run recording requires a database")
    return repo


@router.get("/report", response_model=ReportOut)
async def grading_report() -> ReportOut:
    """agreement report 可重复生成（确定性现算，无时间戳/随机）。"""
    return ReportOut(**run_grading_eval())


@router.post("/runs", response_model=RunOut, status_code=201)
async def record_grading_run(request: Request) -> RunOut:
    """运行评测并落库留痕；无 DB 503。"""
    report = run_grading_eval()
    record = await _repo(request).record(kind="grading", report=report)
    return RunOut(**record)


@router.get("/runs", response_model=list[RunOut])
async def list_grading_runs(request: Request, kind: str | None = None) -> list[RunOut]:
    return [RunOut(**item) for item in await _repo(request).list_runs(kind)]


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_grading_run(run_id: int, request: Request) -> RunOut:
    record = await _repo(request).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="评测运行不存在")
    return RunOut(**record)
