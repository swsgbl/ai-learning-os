"""M3-06 Daily planner API：今日学习计划（新学/复习/错题重测 + 入选理由）。

GET /daily-plan 实时聚合三个学生模型投影与题库池（不落库，ADR 29 同款）；
now 显式指定可做确定性投影（验收「第一次考试后的错题会影响第二日学习任务」），
省略则取服务器当前时刻。
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from app.api.routes.auth import current_owner_id
from app.api.routes.student import all_learning_streams
from app.api.schemas import DailyPlanOut, PlanTaskOut
from app.domain.daily_planner import (
    KIND_MISTAKE_RETRY,
    KIND_NEW_LEARNING,
    KIND_REVIEW,
    build_daily_plan,
)

router = APIRouter(prefix="/api/v1/student/daily-plan", tags=["student"])


@router.get("", response_model=DailyPlanOut)
async def get_daily_plan(request: Request, now: str | None = None) -> DailyPlanOut:
    """今日计划：review → mistake_retry → new_learning 分组有序，每任务带 reason。"""
    if getattr(request.app.state, "student_state", None) is None:
        raise HTTPException(status_code=503, detail="Daily plan requires a database")
    if now is not None:
        try:
            anchor = datetime.fromisoformat(now)
        except ValueError as cause:
            raise HTTPException(status_code=422, detail=f"now 不是合法 ISO 时间: {now}") from cause
    else:
        anchor = datetime.now(UTC)
    # M10-03: 题库池可见性与 /papers 同规则——计划任务不得放大到他人私有卷
    papers = await request.app.state.repository.list_papers(owner_id=current_owner_id(request))
    plan = build_daily_plan(await all_learning_streams(request), papers, now=anchor)
    return DailyPlanOut(
        generated_at=plan.generated_at.isoformat(),
        plan_date=plan.plan_date,
        task_count=len(plan.tasks),
        review_count=sum(1 for task in plan.tasks if task.kind == KIND_REVIEW),
        mistake_retry_count=sum(1 for task in plan.tasks if task.kind == KIND_MISTAKE_RETRY),
        new_learning_count=sum(1 for task in plan.tasks if task.kind == KIND_NEW_LEARNING),
        tasks=[
            PlanTaskOut(
                kind=task.kind,
                question_id=task.question_id,
                concept_ids=list(task.concept_ids),
                title=task.title,
                reason=task.reason,
                priority=task.priority,
            )
            for task in plan.tasks
        ],
    )
