"""M3-07 选题策略 API：下一次练习的题目选择（历史错题/弱概念/难度三因素）。

GET /selection 实时聚合学生模型投影与题库池（不落库，ADR 29/30 同款）；
now 显式指定可做确定性投影，省略则取服务器当前时刻。
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from app.api.routes.student import all_learning_streams
from app.api.schemas import SelectionItemOut, SelectionOut
from app.domain.selection import (
    KIND_ADVANCED,
    KIND_RETRY,
    KIND_WEAK,
    build_selection,
)

router = APIRouter(prefix="/api/v1/student/selection", tags=["student"])


@router.get("", response_model=SelectionOut)
async def get_selection(request: Request, now: str | None = None) -> SelectionOut:
    """选题：retry → weak → advanced 分组有序，每项带 reason。"""
    if getattr(request.app.state, "student_state", None) is None:
        raise HTTPException(status_code=503, detail="Selection requires a database")
    if now is not None:
        try:
            anchor = datetime.fromisoformat(now)
        except ValueError as cause:
            raise HTTPException(status_code=422, detail=f"now 不是合法 ISO 时间: {now}") from cause
    else:
        anchor = datetime.now(UTC)
    papers = await request.app.state.repository.list_papers()
    selection = build_selection(await all_learning_streams(request), papers, now=anchor)
    items = [
        SelectionItemOut(
            kind=item.kind,
            question_id=item.question_id,
            concept_ids=list(item.concept_ids),
            reason=item.reason,
            priority=item.priority,
        )
        for item in selection.items
    ]
    return SelectionOut(
        generated_at=selection.generated_at.isoformat(),
        item_count=len(items),
        retry_count=sum(1 for item in items if item.kind == KIND_RETRY),
        weak_concept_count=sum(1 for item in items if item.kind == KIND_WEAK),
        advanced_count=sum(1 for item in items if item.kind == KIND_ADVANCED),
        items=items,
    )
