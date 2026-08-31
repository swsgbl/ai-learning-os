"""M3-05 FSRS-like scheduler API：错题复习队列（04 号文档 StudentConceptState.next_review_at）。

GET /review-queue 实时从学习事件投影（不落库，天然幂等，ADR 29）；
now 显式指定可做确定性投影（提前/延迟策略测试），省略则取服务器当前时刻。
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from app.api.routes.student import all_learning_streams
from app.api.schemas import ReviewItemOut, ReviewQueueOut
from app.domain.review_scheduler import ReviewItem, derive_review_queue

router = APIRouter(prefix="/api/v1/student/review-queue", tags=["student"])


def _item_out(item: ReviewItem) -> ReviewItemOut:
    return ReviewItemOut(
        question_id=item.question_id,
        concept_ids=list(item.concept_ids),
        difficulty=item.difficulty,
        status=item.status,
        interval_days=item.interval_days,
        last_correctness=item.last_correctness,
        last_seen_at=item.last_seen_at.isoformat(),
        next_review_at=item.next_review_at.isoformat(),
        overdue_ratio=item.overdue_ratio,
    )


@router.get("", response_model=ReviewQueueOut)
async def get_review_queue(request: Request, now: str | None = None) -> ReviewQueueOut:
    """复习队列：错题生成 next_review_at，overdue 降序（最紧急在前）。"""
    if getattr(request.app.state, "student_state", None) is None:
        raise HTTPException(status_code=503, detail="Review queue requires a database")
    if now is not None:
        try:
            anchor = datetime.fromisoformat(now)
        except ValueError as cause:
            raise HTTPException(status_code=422, detail=f"now 不是合法 ISO 时间: {now}") from cause
    else:
        anchor = datetime.now(UTC)
    queue = derive_review_queue(await all_learning_streams(request), now=anchor)
    return ReviewQueueOut(
        generated_at=queue.generated_at.isoformat(),
        item_count=len(queue.items),
        items=[_item_out(item) for item in queue.items],
    )
