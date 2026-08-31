"""M3-04 Misconception candidate API：误解候选的重算与查询（04 号文档 §2.12）。

- POST /misconceptions/recompute 从全部学习事件重建候选（幂等：同事件同 now 恒等输出）。
- GET /misconceptions 列表（confidence 降序——最像长期画像的排最前）、
  GET /misconceptions/{concept_id} 单概念的候选。
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from app.api.routes.student import all_learning_streams
from app.api.schemas import MisconceptionOut, MisconceptionsOut
from app.domain.misconceptions import MisconceptionCandidate, derive_misconceptions
from app.repositories.misconceptions import MisconceptionRepository

router = APIRouter(prefix="/api/v1/student/misconceptions", tags=["student"])


def _repo(request: Request) -> MisconceptionRepository:
    repo = getattr(request.app.state, "misconceptions", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Misconceptions require a database")
    return repo


def _candidate_out(item: MisconceptionCandidate) -> MisconceptionOut:
    return MisconceptionOut(
        concept_id=item.concept_id,
        pattern=item.pattern,
        status=item.status,
        confidence=item.confidence,
        independent_count=item.independent_count,
        occurrence_count=item.occurrence_count,
        question_ids=list(item.question_ids),
        first_seen_at=item.first_seen_at.isoformat(),
        last_seen_at=item.last_seen_at.isoformat(),
        updated_at=item.updated_at.isoformat(),
    )


def _candidates_out(items: list[MisconceptionCandidate]) -> MisconceptionsOut:
    return MisconceptionsOut(
        candidate_count=len(items),
        candidates=[_candidate_out(item) for item in items],
    )


@router.post("/recompute", response_model=MisconceptionsOut)
async def recompute_misconceptions(request: Request, now: str | None = None) -> MisconceptionsOut:
    """M3-04 验收入口：单次错误生成 candidate，多独立题证据提升置信度并升级 confirmed。"""
    if now is not None:
        try:
            anchor = datetime.fromisoformat(now)
        except ValueError as cause:
            raise HTTPException(status_code=422, detail=f"now 不是合法 ISO 时间: {now}") from cause
    else:
        anchor = datetime.now(UTC)
    repo = _repo(request)  # 先守卫 503，再做事件流重建
    candidates = derive_misconceptions(await all_learning_streams(request), now=anchor)
    await repo.recompute(candidates.values())
    return _candidates_out(await repo.list_candidates())


@router.get("", response_model=MisconceptionsOut)
async def list_misconceptions(request: Request) -> MisconceptionsOut:
    return _candidates_out(await _repo(request).list_candidates())


@router.get("/{concept_id}", response_model=MisconceptionsOut)
async def list_concept_misconceptions(concept_id: str, request: Request) -> MisconceptionsOut:
    """单概念的误解候选；该概念无任何候选时 404。"""
    items = await _repo(request).list_by_concept(concept_id)
    if not items:
        raise HTTPException(status_code=404, detail=f"概念 {concept_id} 无误解候选")
    return _candidates_out(items)
