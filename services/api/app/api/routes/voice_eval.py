"""M6-02 Voice eval API：语音评测集报告与运行记录。

- GET  /api/v1/eval/voice/report：现算语音评测报告（无 DB 恒可用）；
- POST /api/v1/eval/voice/runs：现算并落库（kind="voice"，agreement 列
  承载 accuracy——列语义为「本次评测的头部分数」）；无 DB 503；
- GET  /runs：kind=voice 运行列表；GET /runs/{id}：详情含完整 report；404。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.domain.voice_eval import run_voice_eval

router = APIRouter(prefix="/api/v1/eval/voice", tags=["eval"])


class VoiceReportOut(BaseModel):
    rule_versions: dict
    case_count: int
    categories_covered: list[str]
    coverage_complete: bool
    accuracy: float
    per_category: dict
    mismatches: list[dict]


class VoiceRunOut(BaseModel):
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
        raise HTTPException(status_code=503, detail="Voice eval requires a database")
    return repo


@router.get("/report", response_model=VoiceReportOut)
async def voice_report() -> VoiceReportOut:
    """现算语音评测报告：30 案六类覆盖，accuracy 可计算；无 DB 依赖。"""
    return VoiceReportOut(**run_voice_eval())


@router.post("/runs", response_model=VoiceRunOut, status_code=201)
async def create_voice_run(request: Request) -> VoiceRunOut:
    """现算并落库 kind="voice"；agreement 列承载 accuracy（不虚报）。"""
    report = run_voice_eval()
    record = await _repo(request).record(
        kind="voice", report=report, agreement=report["accuracy"]
    )
    return VoiceRunOut(**record)


@router.get("/runs", response_model=list[VoiceRunOut])
async def list_voice_runs(request: Request) -> list[VoiceRunOut]:
    """kind=voice 运行列表（最新在前）。"""
    rows = await _repo(request).list_runs(kind="voice")
    return [VoiceRunOut(**row) for row in rows]


@router.get("/runs/{run_id}", response_model=VoiceRunOut)
async def get_voice_run(run_id: int, request: Request) -> VoiceRunOut:
    """运行详情：完整 report 可回查；404。"""
    record = await _repo(request).get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="评测运行记录不存在")
    return VoiceRunOut(**record)
