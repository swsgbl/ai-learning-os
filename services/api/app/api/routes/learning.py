"""M16-001 学习合同 / Sprint 状态机 / 学习事件（升级方案 Phase 1 / Prompt 1）。

- POST /api/v1/learning/contracts：创建学习合同——目标必须是可观察能力
  （1-8 条，推荐 3-8：给定 X，能在 Z 标准下完成 Y），含范围/排除项/
  先验/时间预算/通过标准；LLM 不可代用户确认（confirmed_at 仅经
  /confirm 显式写入）。
- POST /api/v1/learning/contracts/{id}/sprints：从已确认合同创建 48h
  Sprint（started_at=now，ends_at=started+预算；到期由 report 如实
  汇总，状态机不虚构掌握）。
- POST /api/v1/learning/sprints/{id}/advance|pause|resume：状态机推进
  仅经领域层（sprint_state）——同状态幂等 no-op；暂停不丢证据。
- POST /api/v1/learning/sprints/{id}/events：学习事件幂等落库
  （idempotency_key 唯一；重复提交返回既有事件）。

归属门：auth on 严格 owner 过滤（写操作 401 未登录）；本地模式
（无 AUTH_SECRET）owner=None 无主可见（与考试读写一致）。
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.routes.auth import current_owner_id, require_user
from app.core.config import get_settings
from app.db.orm import (
    AssessmentBlueprintRow,
    LearningAttemptRow,
    LearningContractRow,
    LearningEventRow,
    LearningReviewRow,
    LearningSourceRefRow,
    LearningSprintRow,
    PaperRow,
    QuestionRow,
)
from app.domain.assessment_blueprint import (
    CandidateItem,
    build_blueprint,
)
from app.domain.grading import grade_answer
from app.domain.item_generator import (
    LlmItemGenerationUnavailable,
    generate_objective_items,
)
from app.domain.mastery import AttemptEvidence, derive_mastery
from app.domain.practice_loop import (
    AttemptSummary,
    ConceptForInterleave,
    interleave_queue,
    next_step,
)
from app.domain.review_schedule import (
    DEFAULT_INTERVALS_DAYS,
    ReviewOutcome,
    ReviewScheduleError,
    next_interval_days,
    review_status_transition,
)
from app.domain.sprint_state import (
    SPRINT_PHASES,
    IllegalSprintTransition,
    advance,
    check_pausable,
    check_resumable,
)

router = APIRouter(prefix="/api/v1/learning", tags=["learning"])

LEARNING_MODES = {"exam_sprint", "intro", "project", "research"}
EVENT_SOURCES = {"api", "llm", "voice", "system"}


class TargetOutcome(BaseModel):
    """可观察结果：给定 X（condition），完成 Y（behavior），达到 Z（standard）。"""

    statement: str = Field(min_length=4, max_length=500)
    criteria: str = Field(default="", max_length=500)


class ContractCreateRequest(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    domain: str = Field(default="", max_length=64)
    mode: str = Field(default="exam_sprint")
    target_outcomes: list[TargetOutcome] = Field(min_length=1, max_length=8)
    scope_included: list[str] = Field(default_factory=list)
    scope_excluded: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    time_budget_hours: float = Field(gt=0, le=200)
    deadline: datetime | None = None
    pass_criteria: str = Field(min_length=4, max_length=2000)

    @field_validator("mode")
    @classmethod
    def _known_mode(cls, value: str) -> str:
        if value not in LEARNING_MODES:
            raise ValueError(f"mode 必须是 {'/'.join(sorted(LEARNING_MODES))} 之一")
        return value

    @field_validator("scope_included", "scope_excluded", "prerequisites")
    @classmethod
    def _trim_items(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class ContractOut(BaseModel):
    id: str
    title: str
    domain: str
    mode: str
    target_outcomes: list[TargetOutcome]
    scope_included: list[str]
    scope_excluded: list[str]
    prerequisites: list[str]
    time_budget_hours: float
    deadline: datetime | None
    pass_criteria: str
    confirmed_at: datetime | None
    created_at: datetime


class SprintCreateRequest(BaseModel):
    pass


class AdvanceRequest(BaseModel):
    to_phase: str


class PauseRequest(BaseModel):
    reason: str = Field(default="", max_length=500)


class EventCreateRequest(BaseModel):
    event_type: str = Field(min_length=2, max_length=64)
    payload: dict = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=128)
    source: str = Field(default="api")
    trace_id: str | None = Field(default=None, max_length=64)
    privacy_class: str = Field(default="normal")

    @field_validator("source")
    @classmethod
    def _known_source(cls, value: str) -> str:
        if value not in EVENT_SOURCES:
            raise ValueError(f"source 必须是 {'/'.join(sorted(EVENT_SOURCES))} 之一")
        return value


class SprintOut(BaseModel):
    id: str
    contract_id: str
    phase: str
    paused: bool
    pause_reason: str
    plan_version: int
    started_at: datetime | None
    ends_at: datetime | None
    expired: bool = False
    created_at: datetime
    updated_at: datetime


class EventOut(BaseModel):
    id: str
    sprint_id: str | None
    contract_id: str | None
    event_type: str
    payload: dict
    idempotency_key: str | None
    source: str
    trace_id: str | None
    privacy_class: str
    occurred_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _contract_out(row: LearningContractRow) -> ContractOut:
    return ContractOut(
        id=row.id,
        title=row.title,
        domain=row.domain,
        mode=row.mode,
        target_outcomes=[TargetOutcome(**item) for item in row.target_outcomes],
        scope_included=row.scope_included,
        scope_excluded=row.scope_excluded,
        prerequisites=row.prerequisites,
        time_budget_hours=row.time_budget_hours,
        deadline=row.deadline,
        pass_criteria=row.pass_criteria,
        confirmed_at=row.confirmed_at,
        created_at=row.created_at,
    )


def _sprint_out(row: LearningSprintRow) -> SprintOut:
    expired = False
    if row.ends_at is not None:
        ends = row.ends_at
        if ends.tzinfo is None:  # SQLite 读回 naive——一律按 UTC 解释
            ends = ends.replace(tzinfo=timezone.utc)
        expired = _now() > ends
    return SprintOut(
        id=row.id,
        contract_id=row.contract_id,
        phase=row.phase,
        paused=row.paused,
        pause_reason=row.pause_reason,
        plan_version=row.plan_version,
        started_at=row.started_at,
        ends_at=row.ends_at,
        expired=expired,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _owned_contract(request: Request, contract_id: str) -> LearningContractRow:
    owner = current_owner_id(request)
    async with request.app.state.sessionmaker() as session:
        row = (
            await session.execute(
                select(LearningContractRow).where(LearningContractRow.id == contract_id)
            )
        ).scalar_one_or_none()
    if row is None or (owner is not None and row.owner_id != owner):
        raise HTTPException(status_code=404, detail="学习合同不存在")
    return row


async def _owned_sprint(request: Request, sprint_id: str) -> LearningSprintRow:
    owner = current_owner_id(request)
    async with request.app.state.sessionmaker() as session:
        row = (
            await session.execute(select(LearningSprintRow).where(LearningSprintRow.id == sprint_id))
        ).scalar_one_or_none()
    if row is None or (owner is not None and row.owner_id != owner):
        raise HTTPException(status_code=404, detail="学习 Sprint 不存在")
    return row


async def _record_event(
    request: Request,
    *,
    owner_id: str | None,
    event_type: str,
    payload: dict,
    sprint_id: str | None = None,
    contract_id: str | None = None,
    idempotency_key: str | None = None,
    source: str = "api",
) -> LearningEventRow | None:
    """落一条审计事件；带幂等键且**同租户**已存在 → 返回既有行（不重复）。

    M16-001b（安全审查修复）：幂等作用域 = 租户命名空间
    ``{owner_id or 'anon'}#{用户键}``——跨用户同键互不可见也互不压制
    （IDOR 与键预占两条审查项的唯一解）；idempotency_key 保留用户
    原值仅作展示。
    """
    scope_key = (
        f"{owner_id or 'anon'}#{idempotency_key}" if idempotency_key is not None else None
    )
    async with request.app.state.sessionmaker() as session:
        if scope_key is not None:
            existing = (
                await session.execute(
                    select(LearningEventRow).where(
                        LearningEventRow.idem_scope_key == scope_key
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing
        now = _now()
        row = LearningEventRow(
            id=f"lev_{uuid4().hex[:26]}",
            sprint_id=sprint_id,
            contract_id=contract_id,
            owner_id=owner_id,
            event_type=event_type,
            payload=payload,
            idempotency_key=idempotency_key,
            idem_scope_key=scope_key,
            source=source,
            privacy_class="normal",
            occurred_at=now,
            created_at=now,
        )
        session.add(row)
        await session.commit()
        return row


# ---------------------------------------------------------------- 合同


@router.post("/contracts", response_model=ContractOut, status_code=201)
async def create_contract(payload: ContractCreateRequest, request: Request) -> ContractOut:
    await require_user(request)
    # 本地模式（auth off）owner=None = 无主写（与课程草稿/考试既有语义一致）；
    # auth on 时 require_user 已保证 token 有效，owner 必非 None
    owner = current_owner_id(request)
    now = _now()
    async with request.app.state.sessionmaker() as session:
        row = LearningContractRow(
            id=f"lc_{uuid4().hex[:26]}",
            owner_id=owner,
            title=payload.title.strip(),
            domain=payload.domain.strip(),
            mode=payload.mode,
            target_outcomes=[item.model_dump() for item in payload.target_outcomes],
            scope_included=payload.scope_included,
            scope_excluded=payload.scope_excluded,
            prerequisites=payload.prerequisites,
            time_budget_hours=payload.time_budget_hours,
            deadline=payload.deadline,
            pass_criteria=payload.pass_criteria.strip(),
            created_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return _contract_out(row)


@router.get("/contracts", response_model=list[ContractOut])
async def list_contracts(request: Request) -> list[ContractOut]:
    owner = current_owner_id(request)
    async with request.app.state.sessionmaker() as session:
        query = select(LearningContractRow).order_by(LearningContractRow.created_at.desc()).limit(50)
        if owner is not None:
            query = query.where(LearningContractRow.owner_id == owner)
        rows = (await session.execute(query)).scalars().all()
    return [_contract_out(row) for row in rows]


@router.get("/contracts/{contract_id}", response_model=ContractOut)
async def get_contract(contract_id: str, request: Request) -> ContractOut:
    row = await _owned_contract(request, contract_id)
    return _contract_out(row)


@router.post("/contracts/{contract_id}/confirm", response_model=ContractOut)
async def confirm_contract(contract_id: str, request: Request) -> ContractOut:
    """用户显式确认合同（LLM 不可代确认——唯一写 confirmed_at 的入口）。"""
    await require_user(request)
    row = await _owned_contract(request, contract_id)
    if row.confirmed_at is not None:
        return _contract_out(row)  # 幂等：重复确认 no-op
    now = _now()
    async with request.app.state.sessionmaker() as session:
        db_row = await session.get(LearningContractRow, row.id)
        assert db_row is not None
        db_row.confirmed_at = now
        await session.commit()
        await session.refresh(db_row)
    await _record_event(
        request,
        owner_id=row.owner_id,
        contract_id=row.id,
        event_type="contract.confirmed",
        payload={"title": row.title},
        idempotency_key=f"contract:{row.id}:confirmed",
    )
    return _contract_out(db_row)


# ---------------------------------------------------------------- Sprint


@router.post("/contracts/{contract_id}/sprints", response_model=SprintOut, status_code=201)
async def create_sprint(contract_id: str, request: Request) -> SprintOut:
    await require_user(request)
    contract = await _owned_contract(request, contract_id)
    if contract.confirmed_at is None:
        raise HTTPException(status_code=409, detail="合同未确认：先调用 /confirm")
    now = _now()
    ends_at = now + timedelta(hours=contract.time_budget_hours)
    async with request.app.state.sessionmaker() as session:
        row = LearningSprintRow(
            id=f"ls_{uuid4().hex[:26]}",
            contract_id=contract.id,
            owner_id=contract.owner_id,
            phase="created",
            started_at=now,
            ends_at=ends_at,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    await _record_event(
        request,
        owner_id=contract.owner_id,
        contract_id=contract.id,
        sprint_id=row.id,
        event_type="sprint.created",
        payload={"ends_at": ends_at.isoformat()},
        idempotency_key=f"sprint:{row.id}:created",
    )
    return _sprint_out(row)


@router.get("/contracts/{contract_id}/sprints", response_model=list[SprintOut])
async def list_contract_sprints(contract_id: str, request: Request) -> list[SprintOut]:
    """该合同的 Sprint 列表（最新在前）——Web 工作台按合同恢复冲刺面板。"""
    await _owned_contract(request, contract_id)
    async with request.app.state.sessionmaker() as session:
        rows = (
            (
                await session.execute(
                    select(LearningSprintRow)
                    .where(LearningSprintRow.contract_id == contract_id)
                    .order_by(LearningSprintRow.created_at.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
    return [_sprint_out(row) for row in rows]


@router.get("/sprints/{sprint_id}", response_model=SprintOut)
async def get_sprint(sprint_id: str, request: Request) -> SprintOut:
    row = await _owned_sprint(request, sprint_id)
    return _sprint_out(row)


@router.post("/sprints/{sprint_id}/advance", response_model=SprintOut)
async def advance_sprint(sprint_id: str, payload: AdvanceRequest, request: Request) -> SprintOut:
    """推进主线（领域状态机独占；同状态幂等 no-op）。"""
    await require_user(request)
    row = await _owned_sprint(request, sprint_id)
    if payload.to_phase not in SPRINT_PHASES:
        raise HTTPException(status_code=422, detail=f"未知阶段: {payload.to_phase}")
    idempotent = payload.to_phase == row.phase
    try:
        next_phase = advance(row.phase, payload.to_phase)
    except IllegalSprintTransition as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    if not idempotent:
        now = _now()
        async with request.app.state.sessionmaker() as session:
            db_row = await session.get(LearningSprintRow, row.id)
            assert db_row is not None
            db_row.phase = next_phase
            db_row.updated_at = now
            await session.commit()
            await session.refresh(db_row)
        await _record_event(
            request,
            owner_id=row.owner_id,
            contract_id=row.contract_id,
            sprint_id=row.id,
            event_type="sprint.phase_advanced",
            payload={"from": row.phase, "to": next_phase},
            idempotency_key=f"sprint:{row.id}:phase:{next_phase}",
        )
        return _sprint_out(db_row)
    return _sprint_out(row)


@router.post("/sprints/{sprint_id}/pause", response_model=SprintOut)
async def pause_sprint(sprint_id: str, payload: PauseRequest, request: Request) -> SprintOut:
    await require_user(request)
    row = await _owned_sprint(request, sprint_id)
    try:
        check_pausable(row.phase, paused=row.paused)
    except IllegalSprintTransition as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    if row.paused:
        return _sprint_out(row)  # 幂等：已暂停再暂停 no-op
    now = _now()
    async with request.app.state.sessionmaker() as session:
        db_row = await session.get(LearningSprintRow, row.id)
        assert db_row is not None
        db_row.paused = True
        db_row.pause_reason = payload.reason
        db_row.updated_at = now
        await session.commit()
        await session.refresh(db_row)
    await _record_event(
        request,
        owner_id=row.owner_id,
        contract_id=row.contract_id,
        sprint_id=row.id,
        event_type="sprint.paused",
        payload={"phase": row.phase, "reason": payload.reason},
        idempotency_key=f"sprint:{row.id}:paused:{row.phase}",
    )
    return _sprint_out(db_row)


@router.post("/sprints/{sprint_id}/resume", response_model=SprintOut)
async def resume_sprint(sprint_id: str, request: Request) -> SprintOut:
    await require_user(request)
    row = await _owned_sprint(request, sprint_id)
    try:
        check_resumable(row.phase, paused=row.paused)
    except IllegalSprintTransition as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    now = _now()
    async with request.app.state.sessionmaker() as session:
        db_row = await session.get(LearningSprintRow, row.id)
        assert db_row is not None
        db_row.paused = False
        db_row.pause_reason = ""
        db_row.updated_at = now
        await session.commit()
        await session.refresh(db_row)
    await _record_event(
        request,
        owner_id=row.owner_id,
        contract_id=row.contract_id,
        sprint_id=row.id,
        event_type="sprint.resumed",
        payload={"phase": row.phase},
        idempotency_key=f"sprint:{row.id}:resumed:{row.phase}",
    )
    return _sprint_out(db_row)


# ---------------------------------------------------------------- 学习事件


@router.post("/sprints/{sprint_id}/events", response_model=EventOut, status_code=201)
async def create_sprint_event(sprint_id: str, payload: EventCreateRequest, request: Request) -> EventOut:
    await require_user(request)
    sprint = await _owned_sprint(request, sprint_id)
    row = await _record_event(
        request,
        owner_id=sprint.owner_id,
        contract_id=sprint.contract_id,
        sprint_id=sprint.id,
        event_type=payload.event_type,
        payload=payload.payload,
        idempotency_key=payload.idempotency_key,
        source=payload.source,
    )
    if row is None:  # pragma: no cover - _record_event 带键必返回行
        raise HTTPException(status_code=500, detail="事件写入失败")
    return EventOut(
        id=row.id,
        sprint_id=row.sprint_id,
        contract_id=row.contract_id,
        event_type=row.event_type,
        payload=row.payload,
        idempotency_key=row.idempotency_key,
        source=row.source,
        trace_id=row.trace_id,
        privacy_class=row.privacy_class,
        occurred_at=row.occurred_at,
    )


@router.get("/sprints/{sprint_id}/events", response_model=list[EventOut])
async def list_sprint_events(sprint_id: str, request: Request) -> list[EventOut]:
    sprint = await _owned_sprint(request, sprint_id)
    async with request.app.state.sessionmaker() as session:
        # 含合同级事件（contract.confirmed 等）：sprint 事件流 = 自身 + 同合同
        rows = (
            (
                await session.execute(
                    select(LearningEventRow)
                    .where(
                        (LearningEventRow.sprint_id == sprint_id)
                        | (LearningEventRow.contract_id == sprint.contract_id)
                    )
                    .order_by(LearningEventRow.occurred_at.asc(), LearningEventRow.created_at.asc())
                    .limit(500)
                )
            )
            .scalars()
            .all()
        )
    return [
        EventOut(
            id=row.id,
            sprint_id=row.sprint_id,
            contract_id=row.contract_id,
            event_type=row.event_type,
            payload=row.payload,
            idempotency_key=row.idempotency_key,
            source=row.source,
            trace_id=row.trace_id,
            privacy_class=row.privacy_class,
            occurred_at=row.occurred_at,
        )
        for row in rows
    ]


# ---------------------------------------------------------------- 作答证据与掌握度（Phase 2）


class AttemptCreateRequest(BaseModel):
    """作答证据（M16-002）：三态判分 + 信心/耗时/提示/独立/延迟/迁移。"""

    item_id: str = Field(min_length=1, max_length=64)
    concept_id: str = Field(min_length=1, max_length=64)
    response: str = Field(default="", max_length=8000)
    score: float | None = Field(default=None, ge=0, le=100)
    correct: bool | None = None
    confidence: int | None = Field(default=None, ge=1, le=5)
    response_time_ms: int | None = Field(default=None, ge=0)
    hints_used: int = Field(default=0, ge=0)
    independent: bool = True
    delayed: bool = False
    transfer: bool = False
    error_tags: list[str] = Field(default_factory=list)
    context: str = Field(default="practice", max_length=64)
    grading_method: str = Field(default="manual", max_length=32)
    grader_version: str | None = Field(default=None, max_length=64)
    sprint_id: str | None = None
    contract_id: str | None = None


class AttemptOut(BaseModel):
    id: str
    item_id: str
    concept_id: str
    response: str
    score: float | None
    correct: bool | None
    confidence: int | None
    response_time_ms: int | None
    hints_used: int
    independent: bool
    delayed: bool
    transfer: bool
    error_tags: list[str]
    context: str
    grading_method: str
    grader_version: str | None
    created_at: datetime


class MasteryOut(BaseModel):
    concept_id: str
    state: str
    evidence_count: int
    independent_correct: int
    distinct_contexts: int
    delayed_or_transfer_correct: int
    high_confidence_errors: int
    estimator_version: str


@router.post("/attempts", response_model=AttemptOut, status_code=201)
async def record_attempt(payload: AttemptCreateRequest, request: Request) -> AttemptOut:
    """记录一次作答证据（判分由服务端给出；correct 三态——未判分传 null）。"""
    await require_user(request)
    owner = current_owner_id(request)
    now = _now()
    async with request.app.state.sessionmaker() as session:
        row = LearningAttemptRow(
            id=f"lat_{uuid4().hex[:24]}",
            owner_id=owner,
            sprint_id=payload.sprint_id,
            contract_id=payload.contract_id,
            item_id=payload.item_id,
            concept_id=payload.concept_id,
            response=payload.response,
            score=payload.score,
            correct=payload.correct,
            confidence=payload.confidence,
            response_time_ms=payload.response_time_ms,
            hints_used=payload.hints_used,
            independent=payload.independent,
            delayed=payload.delayed,
            transfer=payload.transfer,
            error_tags=payload.error_tags,
            context=payload.context,
            grading_method=payload.grading_method,
            grader_version=payload.grader_version,
            created_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return _attempt_out(row)


@router.get("/attempts", response_model=list[AttemptOut])
async def list_attempts(request: Request, concept_id: str | None = None) -> list[AttemptOut]:
    owner = current_owner_id(request)
    async with request.app.state.sessionmaker() as session:
        query = (
            select(LearningAttemptRow)
            .order_by(LearningAttemptRow.created_at.asc())
            .limit(500)
        )
        if owner is not None:
            query = query.where(LearningAttemptRow.owner_id == owner)
        if concept_id is not None:
            query = query.where(LearningAttemptRow.concept_id == concept_id)
        rows = (await session.execute(query)).scalars().all()
    return [_attempt_out(row) for row in rows]


@router.get("/mastery", response_model=list[MasteryOut])
async def mastery_overview(request: Request) -> list[MasteryOut]:
    """全部概念掌握概览（从原始 attempts 实时重算——派生视图不落表）。"""
    owner = current_owner_id(request)
    async with request.app.state.sessionmaker() as session:
        query = select(LearningAttemptRow).order_by(LearningAttemptRow.created_at.asc())
        if owner is not None:
            query = query.where(LearningAttemptRow.owner_id == owner)
        rows = (await session.execute(query)).scalars().all()
    grouped: dict[str, list[LearningAttemptRow]] = {}
    for row in rows:
        grouped.setdefault(row.concept_id, []).append(row)
    return [_mastery_out(concept_id, evidence) for concept_id, evidence in grouped.items()]


@router.get("/mastery/{concept_id}", response_model=MasteryOut)
async def mastery_for_concept(concept_id: str, request: Request) -> MasteryOut:
    owner = current_owner_id(request)
    async with request.app.state.sessionmaker() as session:
        query = (
            select(LearningAttemptRow)
            .where(LearningAttemptRow.concept_id == concept_id)
            .order_by(LearningAttemptRow.created_at.asc())
        )
        if owner is not None:
            query = query.where(LearningAttemptRow.owner_id == owner)
        rows = (await session.execute(query)).scalars().all()
    return _mastery_out(concept_id, list(rows))


def _attempt_out(row: LearningAttemptRow) -> AttemptOut:
    return AttemptOut(
        id=row.id,
        item_id=row.item_id,
        concept_id=row.concept_id,
        response=row.response,
        score=row.score,
        correct=row.correct,
        confidence=row.confidence,
        response_time_ms=row.response_time_ms,
        hints_used=row.hints_used,
        independent=row.independent,
        delayed=row.delayed,
        transfer=row.transfer,
        error_tags=row.error_tags,
        context=row.context,
        grading_method=row.grading_method,
        grader_version=row.grader_version,
        created_at=row.created_at,
    )


def _to_evidence(row: LearningAttemptRow) -> AttemptEvidence:
    return AttemptEvidence(
        attempt_id=row.id,
        concept_id=row.concept_id,
        correct=row.correct,
        confidence=row.confidence,
        hints_used=row.hints_used,
        independent=row.independent,
        delayed=row.delayed,
        transfer=row.transfer,
        context=row.context,
    )


def _mastery_out(concept_id: str, rows: list[LearningAttemptRow]) -> MasteryOut:
    outcome = derive_mastery([_to_evidence(row) for row in rows])
    return MasteryOut(
        concept_id=concept_id,
        state=outcome.state,
        evidence_count=outcome.evidence_count,
        independent_correct=outcome.independent_correct,
        distinct_contexts=outcome.distinct_contexts,
        delayed_or_transfer_correct=outcome.delayed_or_transfer_correct,
        high_confidence_errors=outcome.high_confidence_errors,
        estimator_version=outcome.estimator_version,
    )


# ---------------------------------------------------------------- 教学微循环决策（Phase 3）


class AttemptSummaryIn(BaseModel):
    """最近作答摘要（策略决策输入）。"""

    correct: bool | None = None
    confidence: int | None = Field(default=None, ge=1, le=5)
    hints_used: int = Field(default=0, ge=0)
    error_tags: list[str] = Field(default_factory=list)


class NextStepRequest(BaseModel):
    concept_id: str = Field(min_length=1, max_length=64)
    completed_steps: list[str] = Field(default_factory=list)
    last_attempt: AttemptSummaryIn | None = None


class NextStepOut(BaseModel):
    concept_id: str
    next_step: str
    strategy: str
    rationale: str
    loop_complete: bool
    retry_after_step: str | None


class ConceptForInterleaveIn(BaseModel):
    concept_id: str = Field(min_length=1, max_length=64)
    state: str
    confusable_with: list[str] = Field(default_factory=list)


class InterleaveRequest(BaseModel):
    concepts: list[ConceptForInterleaveIn] = Field(min_length=1, max_length=50)


class InterleaveOut(BaseModel):
    order: list[str]
    skipped: list[str]
    rationale: str


@router.post("/practice/next-step", response_model=NextStepOut)
async def practice_next_step(payload: NextStepRequest, request: Request) -> NextStepOut:
    """下一步教学动作（纯函数决策：策略由域层独占，LLM 只产内容）。

    completed_steps 由调用方从 LearningEvent 恢复（可审计可恢复）。
    """
    await require_user(request)
    last = (
        AttemptSummary(
            correct=payload.last_attempt.correct,
            confidence=payload.last_attempt.confidence,
            hints_used=payload.last_attempt.hints_used,
            error_tags=tuple(payload.last_attempt.error_tags),
        )
        if payload.last_attempt is not None
        else None
    )
    try:
        decision = next_step(payload.completed_steps, last)
    except ValueError as cause:
        raise HTTPException(status_code=422, detail=str(cause)) from cause
    return NextStepOut(
        concept_id=payload.concept_id,
        next_step=decision.next_step,
        strategy=decision.strategy,
        rationale=decision.rationale,
        loop_complete=decision.loop_complete,
        retry_after_step=decision.retry_after_step,
    )


@router.post("/practice/interleave-plan", response_model=InterleaveOut)
async def practice_interleave_plan(payload: InterleaveRequest, request: Request) -> InterleaveOut:
    """交错练习序（有目的交错：达标概念按易混淆关系交替，未达标跳过）。"""
    await require_user(request)
    plan = interleave_queue(
        [
            ConceptForInterleave(
                concept_id=c.concept_id,
                state=c.state,
                confusable_with=tuple(c.confusable_with),
            )
            for c in payload.concepts
        ]
    )
    return InterleaveOut(
        order=list(plan.order), skipped=list(plan.skipped), rationale=plan.rationale
    )


# ---------------------------------------------------------------- 评估蓝图与 Sprint 前后测（Phase 4）


class BlueprintBuildRequest(BaseModel):
    """蓝图构建：候选题池限定为 owner 定制课程卷（含公共卷）的客观题。

    concepts 可选——缺省从题池聚合全部概念。
    """

    concepts: list[str] = Field(default_factory=list, max_length=50)


class BlueprintPlanOut(BaseModel):
    concept_id: str
    pretest_item_ids: list[str]
    posttest_item_ids: list[str]
    complete: bool
    note: str


class BlueprintOut(BaseModel):
    contract_id: str
    complete: bool
    gaps: list[str]
    plans: list[BlueprintPlanOut]


class AssessmentItemOut(BaseModel):
    """题目公开面——answer/explanation 绝不下发（答案隔离不变式）。"""

    item_id: str
    concept_id: str
    question_type: str
    stem: str
    options: list[dict] | None = None
    score: float = 1.0


class AssessmentOut(BaseModel):
    sprint_id: str
    phase: str  # baseline | posttest
    items: list[AssessmentItemOut]


class AssessmentSubmitItem(BaseModel):
    item_id: str
    response: str = Field(default="", max_length=4000)
    confidence: int | None = Field(default=None, ge=1, le=5)


class AssessmentSubmitRequest(BaseModel):
    responses: list[AssessmentSubmitItem] = Field(min_length=1, max_length=200)


class AssessmentResultOut(BaseModel):
    sprint_id: str
    phase: str
    score: float  # 百分比 0-100
    correct_count: int
    question_count: int
    ungraded_count: int
    per_concept: dict[str, dict[str, float | int]]
    sprint_phase: str


class ReportOut(BaseModel):
    sprint_id: str
    expired: bool
    baseline: dict[str, object] | None
    posttest: dict[str, object] | None
    gain: float | None  # posttest - baseline（百分比分）；无双侧结果为 None
    mastery_snapshot: list[dict[str, object]]
    incomplete: list[str]


#: Sprint 阶段 <-> 蓝图 phase 的映射与前置（M16-004 编排）
PHASE_TO_BLUEPRINT = {"baseline": "pretest", "posttest": "posttest"}
PHASE_REQUIRED_SPRINT_PHASE = {"baseline": "baseline_assessment", "posttest": "posttest"}
PHASE_AFTER_SUBMIT = {"baseline": "concept_map_review", "posttest": "report"}


@router.post("/contracts/{contract_id}/blueprint/build", response_model=BlueprintOut, status_code=201)
async def build_contract_blueprint(
    contract_id: str, payload: BlueprintBuildRequest, request: Request
) -> BlueprintOut:
    """从题池构建等值前后测蓝图（重复构建全量重建——幂等）。"""
    await require_user(request)
    contract = await _owned_contract(request, contract_id)
    owner = current_owner_id(request)

    async with request.app.state.sessionmaker() as session:
        paper_query = select(PaperRow)
        if owner is not None:
            paper_query = paper_query.where(
                (PaperRow.owner_id == owner) | (PaperRow.owner_id.is_(None))
            )
        papers = (await session.execute(paper_query)).scalars().all()
        if not papers:
            raise HTTPException(status_code=409, detail="题池为空：先定制课程生成练习卷")
        questions = (
            (
                await session.execute(
                    select(QuestionRow).where(QuestionRow.paper_id.in_([p.id for p in papers]))
                )
            )
            .scalars()
            .all()
        )
        concept_filter = set(payload.concepts) if payload.concepts else None
        candidates: list[CandidateItem] = []
        for q in questions:
            for concept_id in q.knowledge if isinstance(q.knowledge, list) else []:
                if not isinstance(concept_id, str):
                    continue
                if concept_filter is not None and concept_id not in concept_filter:
                    continue
                candidates.append(
                    CandidateItem(
                        item_id=q.id,
                        concept_id=concept_id,
                        difficulty=q.difficulty,
                        question_type=q.question_type,
                    )
                )
        bp = build_blueprint(candidates)
        if not bp.plans:
            raise HTTPException(status_code=409, detail="无可用于前测/后测的客观题（概念关联为空）")
        old_rows = (
            await session.execute(
                select(AssessmentBlueprintRow).where(
                    AssessmentBlueprintRow.contract_id == contract.id
                )
            )
        ).scalars().all()
        for row in old_rows:
            await session.delete(row)
        now = _now()
        for plan in bp.plans:
            session.add(
                AssessmentBlueprintRow(
                    id=f"abp_{uuid4().hex[:24]}",
                    contract_id=contract.id,
                    owner_id=contract.owner_id,
                    concept_id=plan.concept_id,
                    pretest_item_ids=list(plan.pretest_item_ids),
                    posttest_item_ids=list(plan.posttest_item_ids),
                    complete=plan.complete,
                    note=plan.note,
                    created_at=now,
                )
            )
        await session.commit()
    return BlueprintOut(
        contract_id=contract.id,
        complete=bp.complete,
        gaps=list(bp.gaps),
        plans=[
            BlueprintPlanOut(
                concept_id=p.concept_id,
                pretest_item_ids=list(p.pretest_item_ids),
                posttest_item_ids=list(p.posttest_item_ids),
                complete=p.complete,
                note=p.note,
            )
            for p in bp.plans
        ],
    )


@router.get("/contracts/{contract_id}/blueprint", response_model=BlueprintOut)
async def get_contract_blueprint(contract_id: str, request: Request) -> BlueprintOut:
    await _owned_contract(request, contract_id)
    async with request.app.state.sessionmaker() as session:
        rows = (
            (
                await session.execute(
                    select(AssessmentBlueprintRow)
                    .where(AssessmentBlueprintRow.contract_id == contract_id)
                    .order_by(AssessmentBlueprintRow.concept_id.asc())
                )
            )
            .scalars()
            .all()
        )
    if not rows:
        raise HTTPException(status_code=404, detail="蓝图未构建")
    return BlueprintOut(
        contract_id=contract_id,
        complete=all(r.complete for r in rows),
        gaps=[f"{r.concept_id}：{r.note}" for r in rows if not r.complete],
        plans=[
            BlueprintPlanOut(
                concept_id=r.concept_id,
                pretest_item_ids=r.pretest_item_ids,
                posttest_item_ids=r.posttest_item_ids,
                complete=r.complete,
                note=r.note,
            )
            for r in rows
        ],
    )


async def _sprint_assessment_items(
    request: Request, sprint: LearningSprintRow, phase: str
) -> list[tuple[QuestionRow, str]]:
    """按蓝图取某阶段题（返回 (题行, 概念) 列表；不下发答案）。"""
    blueprint_phase = PHASE_TO_BLUEPRINT[phase]
    async with request.app.state.sessionmaker() as session:
        plans = (
            (
                await session.execute(
                    select(AssessmentBlueprintRow).where(
                        AssessmentBlueprintRow.contract_id == sprint.contract_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if not plans:
            raise HTTPException(status_code=409, detail="蓝图未构建：先 /blueprint/build")
        field = "pretest_item_ids" if blueprint_phase == "pretest" else "posttest_item_ids"
        item_concepts: dict[str, str] = {}
        for plan in plans:
            for item_id in getattr(plan, field):
                item_concepts[item_id] = plan.concept_id
        if not item_concepts:
            raise HTTPException(status_code=409, detail=f"蓝图 {blueprint_phase} 题集为空")
        questions = (
            (
                await session.execute(
                    select(QuestionRow).where(QuestionRow.id.in_(list(item_concepts.keys())))
                )
            )
            .scalars()
            .all()
        )
        return [(q, item_concepts[q.id]) for q in questions]


@router.get("/sprints/{sprint_id}/assessment", response_model=AssessmentOut)
async def get_sprint_assessment(
    sprint_id: str, request: Request, phase: str = "baseline"
) -> AssessmentOut:
    """下发前测/后测题（公开面——答案隔离；阶段前置校验）。"""
    await require_user(request)
    sprint = await _owned_sprint(request, sprint_id)
    if phase not in PHASE_TO_BLUEPRINT:
        raise HTTPException(status_code=422, detail="phase 必须是 baseline/posttest")
    required = PHASE_REQUIRED_SPRINT_PHASE[phase]
    if sprint.phase != required:
        raise HTTPException(
            status_code=409, detail=f"前测/后测未在对应阶段：当前 {sprint.phase}（需 {required}）"
        )
    pairs = await _sprint_assessment_items(request, sprint, phase)
    return AssessmentOut(
        sprint_id=sprint.id,
        phase=phase,
        items=[
            AssessmentItemOut(
                item_id=q.id,
                concept_id=concept,
                question_type=q.question_type,
                stem=q.stem,
                options=q.options if isinstance(q.options, list) else None,
                score=q.score,
            )
            for q, concept in pairs
        ],
    )


@router.post("/sprints/{sprint_id}/assessment/{phase}/submit", response_model=AssessmentResultOut)
async def submit_sprint_assessment(
    sprint_id: str, phase: str, payload: AssessmentSubmitRequest, request: Request
) -> AssessmentResultOut:
    """提交前测/后测：服务端判分（三态）→ 落 Attempt 证据 → 绑定结果并推进。

    幂等边界：该阶段已有结果 → 409（重考=新 Sprint，绝不覆盖旧证据）。
    """
    await require_user(request)
    sprint = await _owned_sprint(request, sprint_id)
    if phase not in PHASE_TO_BLUEPRINT:
        raise HTTPException(status_code=422, detail="phase 必须是 baseline/posttest")
    if sprint.phase != PHASE_REQUIRED_SPRINT_PHASE[phase]:
        raise HTTPException(status_code=409, detail=f"不在 {phase} 阶段：当前 {sprint.phase}")
    result_field = "baseline_result" if phase == "baseline" else "posttest_result"
    if getattr(sprint, result_field) is not None:
        raise HTTPException(status_code=409, detail=f"{phase} 已提交（重考请新建 Sprint）")

    pairs = await _sprint_assessment_items(request, sprint, phase)
    by_item = {q.id: (q, concept) for q, concept in pairs}
    valid_ids = set(by_item.keys())
    unknown = [r.item_id for r in payload.responses if r.item_id not in valid_ids]
    if unknown:
        raise HTTPException(status_code=422, detail=f"题目不在本次蓝图题集: {unknown[:3]}")

    now = _now()
    correct_count = 0
    ungraded = 0
    per_concept: dict[str, dict[str, float | int]] = {}
    async with request.app.state.sessionmaker() as session:
        from app.domain.assessment_blueprint import is_objective_type

        for item in payload.responses:
            question, concept = by_item[item.item_id]
            if not is_objective_type(question.question_type):
                # 防御：题集被污染（不可确定性判分的题）——拒绝而非让
                # grading 兜底 return False 污染证据（M16-004b 审查修复）
                raise HTTPException(
                    status_code=422,
                    detail=f"题目 {question.id} 题型不可确定性判分（蓝图应只收客观题）",
                )
            correct = grade_answer(question.question_type, question.answer, item.response)
            if correct is None:
                ungraded += 1
            elif correct:
                correct_count += 1
            agg = per_concept.setdefault(concept, {"correct": 0, "total": 0})
            agg["total"] = int(agg["total"]) + 1
            if correct:
                agg["correct"] = int(agg["correct"]) + 1
            session.add(
                LearningAttemptRow(
                    id=f"lat_{uuid4().hex[:24]}",
                    owner_id=sprint.owner_id,
                    sprint_id=sprint.id,
                    contract_id=sprint.contract_id,
                    item_id=item.item_id,
                    concept_id=concept,
                    response=item.response,
                    correct=correct,
                    confidence=item.confidence,
                    independent=True,
                    error_tags=[],
                    context=f"{question.question_type}:{phase}",
                    grading_method="answer_key",
                    grader_version="grading-v1",
                    created_at=now,
                )
            )
        total = len(payload.responses)
        score = round(100.0 * correct_count / total, 1) if total else 0.0
        result_payload: dict[str, object] = {
            "score": score,
            "correct_count": correct_count,
            "question_count": total,
            "ungraded_count": ungraded,
            "submitted_at": now.isoformat(),
        }
        # M16-006b（安全审查修复）：条件更新原子绑定结果+推进——
        # WHERE 须仍处于该阶段且该侧结果为空。并发双提交只有一方
        # rowcount=1；期间被推进/已提交的（TOCTOU）一律 409，
        # 绝不绕过状态机硬改 phase（race-condition + logic-bypass）。
        from sqlalchemy import update

        result = await session.execute(
            update(LearningSprintRow)
            .where(
                LearningSprintRow.id == sprint.id,
                LearningSprintRow.phase == PHASE_REQUIRED_SPRINT_PHASE[phase],
                getattr(LearningSprintRow, result_field).is_(None),
            )
            .values(
                **{result_field: result_payload},
                phase=PHASE_AFTER_SUBMIT[phase],
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            await session.rollback()
            raise HTTPException(
                status_code=409,
                detail="提交冲突：Sprint 状态已变化（已提交或已推进）——刷新后重试",
            )
        await session.commit()

    await _record_event(
        request,
        owner_id=sprint.owner_id,
        contract_id=sprint.contract_id,
        sprint_id=sprint.id,
        event_type=f"sprint.{phase}_submitted",
        payload=result_payload,
        idempotency_key=f"sprint:{sprint.id}:{phase}:submitted",
    )
    return AssessmentResultOut(
        sprint_id=sprint.id,
        phase=phase,
        score=score,
        correct_count=correct_count,
        question_count=total,
        ungraded_count=ungraded,
        per_concept=per_concept,
        sprint_phase=PHASE_AFTER_SUBMIT[phase],
    )


#: 报告用完整阶段序（报告未完成项判定）
_ALL_SPRINT_PHASES = (
    "created", "contract_confirmed", "baseline_assessment", "concept_map_review",
    "plan_confirmed", "learning", "targeted_practice", "transfer_check",
    "posttest", "report", "follow_up",
)


@router.get("/sprints/{sprint_id}/report", response_model=ReportOut)
async def sprint_report(sprint_id: str, request: Request) -> ReportOut:
    """Sprint 报告：前后对比 + 掌握快照 + 未完成项（诚实汇总，不虚构掌握）。"""
    sprint = await _owned_sprint(request, sprint_id)
    if sprint.phase not in {"report", "follow_up"} and sprint.posttest_result is None:
        raise HTTPException(status_code=409, detail=f"报告未就绪：当前 {sprint.phase}")

    owner = current_owner_id(request)
    async with request.app.state.sessionmaker() as session:
        query = select(LearningAttemptRow).order_by(LearningAttemptRow.created_at.asc())
        if owner is not None:
            query = query.where(LearningAttemptRow.owner_id == owner)
        attempts = (await session.execute(query)).scalars().all()
    grouped: dict[str, list[LearningAttemptRow]] = {}
    for row in attempts:
        grouped.setdefault(row.concept_id, []).append(row)
    snapshot = [
        {
            "concept_id": concept_id,
            **{
                k: v
                for k, v in _mastery_out(concept_id, rows).model_dump().items()
                if k != "concept_id"
            },
        }
        for concept_id, rows in grouped.items()
    ]

    incomplete: list[str] = []
    current_index = (
        _ALL_SPRINT_PHASES.index(sprint.phase) if sprint.phase in _ALL_SPRINT_PHASES else 0
    )
    for expected in _ALL_SPRINT_PHASES:
        expected_index = _ALL_SPRINT_PHASES.index(expected)
        if expected_index > current_index and expected != "follow_up":
            incomplete.append(expected)

    baseline = sprint.baseline_result
    posttest = sprint.posttest_result
    gain = None
    if baseline and posttest:
        gain = round(float(posttest.get("score", 0)) - float(baseline.get("score", 0)), 1)
    return ReportOut(
        sprint_id=sprint.id,
        expired=_sprint_out(sprint).expired,
        baseline=baseline,
        posttest=posttest,
        gain=gain,
        mastery_snapshot=snapshot,
        incomplete=incomplete,
    )


# ---------------------------------------------------------------- 间隔复习队列（Phase 6）


class ReviewOut(BaseModel):
    id: str
    concept_id: str
    sprint_id: str | None
    status: str
    due_at: datetime
    interval_days: int | None
    algorithm_version: str
    overdue: bool = False


class ReviewAttemptRequest(BaseModel):
    """复习作答：item_id 取该概念的题（服务端判分——复习完成=真实作答）。"""

    item_id: str = Field(min_length=1, max_length=64)
    response: str = Field(default="", max_length=4000)
    confidence: int | None = Field(default=None, ge=1, le=5)


@router.post("/sprints/{sprint_id}/follow-up/schedule", response_model=list[ReviewOut], status_code=201)
async def schedule_follow_up(sprint_id: str, request: Request) -> list[ReviewOut]:
    """Sprint 进入 report 后：对蓝图全部概念创建初始复测队列（+1 天档）。"""
    await require_user(request)
    sprint = await _owned_sprint(request, sprint_id)
    if sprint.phase not in {"report", "follow_up"}:
        raise HTTPException(
            status_code=409, detail=f"复测队列须在 report/follow_up 阶段创建：当前 {sprint.phase}"
        )
    async with request.app.state.sessionmaker() as session:
        concepts = (
            (
                await session.execute(
                    select(AssessmentBlueprintRow.concept_id).where(
                        AssessmentBlueprintRow.contract_id == sprint.contract_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if not concepts:
            raise HTTPException(status_code=409, detail="蓝图未构建：无可复测概念")
        # 幂等（M16-005b 审查修复）：该 Sprint 已有活跃复习记录 → 返回既有
        # 队列，不重复创建（此前重复 schedule 会无限翻倍队列）
        existing = (
            (
                await session.execute(
                    select(LearningReviewRow)
                    .where(LearningReviewRow.sprint_id == sprint.id)
                    .where(LearningReviewRow.status != "completed")
                    .order_by(LearningReviewRow.due_at.asc())
                )
            )
            .scalars()
            .all()
        )
        if existing:
            return [_review_out(row) for row in existing]
        now = _now()
        rows = []
        for concept_id in concepts:
            row = LearningReviewRow(
                id=f"lrv_{uuid4().hex[:23]}",
                owner_id=sprint.owner_id,
                sprint_id=sprint.id,
                contract_id=sprint.contract_id,
                concept_id=concept_id,
                status="scheduled",
                due_at=now + timedelta(days=DEFAULT_INTERVALS_DAYS[0]),
                interval_days=DEFAULT_INTERVALS_DAYS[0],
                created_at=now,
            )
            session.add(row)
            rows.append(row)
        await session.commit()
        for row in rows:
            await session.refresh(row)
    await _record_event(
        request,
        owner_id=sprint.owner_id,
        contract_id=sprint.contract_id,
        sprint_id=sprint.id,
        event_type="review.scheduled",
        payload={"concepts": list(concepts)},
        idempotency_key=f"sprint:{sprint.id}:followup",
    )
    return [_review_out(row) for row in rows]


@router.get("/review-queue", response_model=list[ReviewOut])
async def my_review_queue(request: Request) -> list[ReviewOut]:
    """到期/进行中的复习队列（due_at <= now+24h 或已 presented/attempted）。"""
    owner = current_owner_id(request)
    async with request.app.state.sessionmaker() as session:
        query = select(LearningReviewRow).where(LearningReviewRow.status != "completed")
        if owner is not None:
            query = query.where(LearningReviewRow.owner_id == owner)
        rows = (
            (await session.execute(query.order_by(LearningReviewRow.due_at.asc()).limit(100)))
            .scalars()
            .all()
        )
    now = _now()
    window_end = now + timedelta(hours=24)
    visible: list[LearningReviewRow] = []
    for row in rows:
        due = row.due_at
        if due.tzinfo is None:  # SQLite naive -> UTC
            due = due.replace(tzinfo=timezone.utc)
        if due <= window_end or row.status != "scheduled":
            visible.append(row)
    return [_review_out(row, now=now) for row in visible]


@router.post("/review-queue/{review_id}/present", response_model=ReviewOut)
async def present_review(review_id: str, request: Request) -> ReviewOut:
    """scheduled -> presented（展示给学习者——不等于作答）。"""
    await require_user(request)
    row = await _owned_review(request, review_id)
    try:
        row_status = review_status_transition(row.status, "presented")
    except ReviewScheduleError as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    async with request.app.state.sessionmaker() as session:
        db_row = await session.get(LearningReviewRow, row.id)
        assert db_row is not None
        db_row.status = row_status
        await session.commit()
        await session.refresh(db_row)
    return _review_out(db_row)


@router.post("/review-queue/{review_id}/attempt", response_model=ReviewOut)
async def attempt_review(
    review_id: str, payload: ReviewAttemptRequest, request: Request
) -> ReviewOut:
    """复习作答：presented -> attempted（服务端判分）-> completed + 排下一轮。

    复习完成只来自真实作答（方案验收）；失败回最短档，概念掌握度由
    delayed attempt 重算自然联动 at_risk。
    """
    await require_user(request)
    row = await _owned_review(request, review_id)
    try:
        after_attempt = review_status_transition(row.status, "attempted")
    except ReviewScheduleError as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    async with request.app.state.sessionmaker() as session:
        question = (
            await session.execute(select(QuestionRow).where(QuestionRow.id == payload.item_id))
        ).scalar_one_or_none()
        if question is None:
            raise HTTPException(status_code=404, detail="复习题目不存在")
        concepts = question.knowledge if isinstance(question.knowledge, list) else []
        if row.concept_id not in concepts:
            raise HTTPException(status_code=422, detail="题目与复习概念不匹配")
        from app.domain.assessment_blueprint import is_objective_type

        if not is_objective_type(question.question_type):
            raise HTTPException(status_code=422, detail="复习题题型不可确定性判分")
        correct = grade_answer(question.question_type, question.answer, payload.response)
        if correct is None:
            raise HTTPException(status_code=422, detail="复习作答未判分（三态）——换客观题")
        now = _now()
        attempt = LearningAttemptRow(
            id=f"lat_{uuid4().hex[:24]}",
            owner_id=row.owner_id,
            sprint_id=row.sprint_id,
            contract_id=row.contract_id,
            item_id=question.id,
            concept_id=row.concept_id,
            response=payload.response,
            correct=correct,
            confidence=payload.confidence,
            independent=True,
            delayed=True,  # 复习作答 = 延迟证据（掌握度 mastered/at_risk 联动）
            error_tags=[],
            context=f"{question.question_type}:review",
            grading_method="answer_key",
            grader_version="grading-v1",
            created_at=now,
        )
        session.add(attempt)
        await session.flush()
        nxt = next_interval_days(
            row.interval_days,
            ReviewOutcome(correct=correct, confidence=payload.confidence, independent=True),
        )
        db_row = await session.get(LearningReviewRow, row.id)
        assert db_row is not None
        db_row.status = review_status_transition(after_attempt, "completed")
        db_row.attempt_id = attempt.id
        db_row.interval_days = nxt.due_in_days
        db_row.due_at = now + timedelta(days=nxt.due_in_days)
        await session.commit()
        await session.refresh(db_row)
        # completed 后创建下一轮 scheduled 记录（滚动复习）
        if db_row.status == "completed":
            session.add(
                LearningReviewRow(
                    id=f"lrv_{uuid4().hex[:23]}",
                    owner_id=db_row.owner_id,
                    sprint_id=db_row.sprint_id,
                    contract_id=db_row.contract_id,
                    concept_id=db_row.concept_id,
                    status="scheduled",
                    due_at=db_row.due_at,
                    interval_days=db_row.interval_days,
                    created_at=now,
                )
            )
            await session.commit()
    return _review_out(db_row)


def _review_out(row: LearningReviewRow, *, now: datetime | None = None) -> ReviewOut:
    current = now or _now()
    due = row.due_at
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    return ReviewOut(
        id=row.id,
        concept_id=row.concept_id,
        sprint_id=row.sprint_id,
        status=row.status,
        due_at=row.due_at,
        interval_days=row.interval_days,
        algorithm_version=row.algorithm_version,
        overdue=current > due,
    )


async def _owned_review(request: Request, review_id: str) -> LearningReviewRow:
    owner = current_owner_id(request)
    async with request.app.state.sessionmaker() as session:
        row = (
            await session.execute(
                select(LearningReviewRow).where(LearningReviewRow.id == review_id)
            )
        ).scalar_one_or_none()
    if row is None or (owner is not None and row.owner_id != owner):
        raise HTTPException(status_code=404, detail="复习记录不存在")
    return row


# ---------------------------------------------------------------- 客观题生成（M16-007：打通蓝图内容层）


class GenerateItemsRequest(BaseModel):
    """按概念生成客观题入合同题池卷（LLM 来源：BYOK active > 全局）。"""

    concepts: list[str] = Field(default_factory=list, max_length=50)  # concept_id 列表
    per_concept: int = Field(default=2, ge=1, le=6)


class GeneratedItemOut(BaseModel):
    item_id: str
    concept_id: str
    stem: str
    difficulty: int


class GenerateItemsOut(BaseModel):
    paper_id: str
    generated: int
    dropped: int
    drop_reasons: list[str]
    items: list[GeneratedItemOut]


@router.post("/contracts/{contract_id}/generate-items", response_model=GenerateItemsOut, status_code=201)
async def generate_items_for_contract(
    contract_id: str, payload: GenerateItemsRequest, request: Request
) -> GenerateItemsOut:
    """为合同生成客观题（mcq 四选一，校验后入题池卷）——蓝图的题池来源。

    题池卷「评估题池 · {合同标题}」每合同一卷，重复调用追加题目；
    LLM 不可用/全部非法 → 503/422 如实（不虚构题目）。
    """
    await require_user(request)
    contract = await _owned_contract(request, contract_id)
    if contract.confirmed_at is None:
        raise HTTPException(status_code=409, detail="合同未确认：先 /confirm")

    # 概念集：请求指定，缺省取蓝图概念
    async with request.app.state.sessionmaker() as session:
        concept_ids = set(payload.concepts)
        if not concept_ids:
            rows = (
                (
                    await session.execute(
                        select(AssessmentBlueprintRow.concept_id).where(
                            AssessmentBlueprintRow.contract_id == contract.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            concept_ids = set(rows)
        if not concept_ids:
            raise HTTPException(
                status_code=422,
                detail="无概念来源：传 concepts 或先构建蓝图",
            )
        # 显示名（供 LLM 出题语境）：concept_id 冒号前段 + 后段
        concepts = {cid.split(":", 1)[-1]: cid for cid in sorted(concept_ids)}

    # LLM 来源（BYOK active > 全局——与课程生成同款解析）
    from app.api.routes.me_ai_keys import resolve_user_llm_config

    s = get_settings()
    endpoint, api_key, model = s.llm_endpoint, s.llm_api_key, s.llm_model
    personal = await resolve_user_llm_config(request.app, current_owner_id(request))
    if personal:
        endpoint = personal["endpoint"] or endpoint
        api_key = personal["api_key"]
        model = personal["model"] or model

    # M16-008：出题附来源证据（top-2 片段进 prompt——题目可追溯；
    # 无来源时如实不带引用，不虚构）
    async with request.app.state.sessionmaker() as session:
        source_rows = (
            (
                await session.execute(
                    select(LearningSourceRefRow)
                    .where(LearningSourceRefRow.contract_id == contract.id)
                    .order_by(LearningSourceRefRow.fetched_at.desc())
                    .limit(len(concepts) * 2)
                )
            )
            .scalars()
            .all()
        )
    context_block = ""
    if source_rows:
        lines = [
            f"- {row.concept_id}：{row.title or row.url}（{row.snippet[:150]}）"
            for row in source_rows[: len(concepts) * 2]
        ]
        context_block = "\n来源片段（出题可参考，标注事实依据）：\n" + "\n".join(lines)

    try:
        # M16-008b（安全审查修复）：LlmGateway.chat 是同步 httpx——在线程池
        # 执行，绝不阻塞事件循环（出题可达数十秒，直调会饿死整个 API）
        report = await asyncio.to_thread(
            generate_objective_items,
            concepts,
            payload.per_concept,
            (endpoint, api_key, model),
            source_context=context_block,
        )
    except LlmItemGenerationUnavailable as cause:
        raise HTTPException(status_code=503, detail=str(cause)) from cause

    async with request.app.state.sessionmaker() as session:
        paper_title = f"评估题池 · {contract.title[:40]}"
        paper = (
            await session.execute(select(PaperRow).where(PaperRow.title == paper_title))
        ).scalar_one_or_none()
        if (
            paper is None
            or (contract.owner_id is not None and paper.owner_id != contract.owner_id)
        ):
            paper = PaperRow(
                id=f"pap_{uuid4().hex[:24]}",
                title=paper_title,
                subtitle=f"合同 {contract.id} 的客观题池（LLM 生成，经校验）",
                source="llm-generated",
                subject=contract.domain or "general",
                difficulty="medium",
                duration_minutes=15,
                tags=["assessment-pool"],
                license="generated",
                owner_id=contract.owner_id,
            )
            session.add(paper)
            await session.flush()
        max_order = (
            await session.execute(
                select(QuestionRow.sort_order)
                .where(QuestionRow.paper_id == paper.id)
                .order_by(QuestionRow.sort_order.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        order = (max_order or 0) + 1
        outs: list[GeneratedItemOut] = []
        for item in report.items:
            question_id = f"q_{uuid4().hex[:24]}"
            session.add(
                QuestionRow(
                    id=question_id,
                    paper_id=paper.id,
                    question_type="mcq",
                    stem=item.stem,
                    options=item.options,
                    answer=item.answer,  # 服务端权威——绝不下发
                    explanation=item.explanation,
                    knowledge=[item.concept_id],
                    difficulty=item.difficulty,
                    sort_order=order,
                    score=1.0,
                )
            )
            order += 1
            outs.append(
                GeneratedItemOut(
                    item_id=question_id,
                    concept_id=item.concept_id,
                    stem=item.stem[:80],
                    difficulty=item.difficulty,
                )
            )
        await session.commit()
    await _record_event(
        request,
        owner_id=contract.owner_id,
        contract_id=contract.id,
        event_type="items.generated",
        payload={
            "generated": len(report.items),
            "dropped": report.dropped_count,
            "concepts": sorted(concept_ids),
        },
    )
    return GenerateItemsOut(
        paper_id=paper.id,
        generated=len(report.items),
        dropped=report.dropped_count,
        drop_reasons=report.drop_reasons[:10],
        items=outs,
    )


# ---------------------------------------------------------------- 来源证据（Phase 5）


class SourceRefOut(BaseModel):
    id: str
    concept_id: str
    provider: str
    url: str
    title: str
    snippet: str
    license_hint: str
    fetched_at: datetime


class SourceSearchRequest(BaseModel):
    """按概念检索来源片段（经既有 search registry；结果入证据表）。"""

    concept_id: str = Field(min_length=1, max_length=64)
    query: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=3, ge=1, le=5)


@router.post(
    "/contracts/{contract_id}/source-refs/search", response_model=list[SourceRefOut], status_code=201
)
async def search_source_refs(
    contract_id: str, payload: SourceSearchRequest, request: Request
) -> list[SourceRefOut]:
    """检索概念的来源片段入库（SearXNG/cloud-web 经 registry；SSRF/许可
    边界沿用 provider 既有防线）。无可用 provider 503 如实。"""
    await require_user(request)
    contract = await _owned_contract(request, contract_id)
    registry = request.app.state.search_registry
    if not registry:
        raise HTTPException(status_code=503, detail="搜索不可用（无已启用 provider）")
    query = payload.query or payload.concept_id.replace(":", " ")
    collected: list[dict] = []
    for entry in registry:
        if not entry.enabled or entry.provider is None:
            continue
        try:
            found = await entry.provider.search(query, payload.limit, owner=current_owner_id(request))
        except Exception:  # noqa: BLE001,S112 - provider 失败跳过（多源容错，与 search 路由同款语义）
            continue
        collected.extend(found)
    if not collected:
        raise HTTPException(status_code=503, detail="检索无结果（provider 不可用或空结果）")
    now = _now()
    rows: list[LearningSourceRefRow] = []
    async with request.app.state.sessionmaker() as session:
        for item in collected[: payload.limit]:
            url = str(item.get("url") or "")
            snippet = str(item.get("snippet") or "")
            if not url.startswith(("http://", "https://")) or not snippet.strip():
                continue
            row = LearningSourceRefRow(
                id=f"lsr_{uuid4().hex[:23]}",
                owner_id=contract.owner_id,
                contract_id=contract.id,
                concept_id=payload.concept_id,
                provider=str(item.get("provider") or entry.name),
                url=url[:1024],
                title=str(item.get("title") or "")[:512],
                snippet=snippet[:4000],
                content_hash=hashlib.sha256(snippet.encode("utf-8")).hexdigest(),
                fetched_at=now,
            )
            session.add(row)
            rows.append(row)
        await session.commit()
        for row in rows:
            await session.refresh(row)
    return [_source_ref_out(row) for row in rows]


@router.get("/contracts/{contract_id}/source-refs", response_model=list[SourceRefOut])
async def list_source_refs(
    contract_id: str, request: Request, concept_id: str | None = None
) -> list[SourceRefOut]:
    await _owned_contract(request, contract_id)
    async with request.app.state.sessionmaker() as session:
        query = (
            select(LearningSourceRefRow)
            .where(LearningSourceRefRow.contract_id == contract_id)
            .order_by(LearningSourceRefRow.fetched_at.desc())
            .limit(100)
        )
        if concept_id is not None:
            query = query.where(LearningSourceRefRow.concept_id == concept_id)
        rows = (await session.execute(query)).scalars().all()
    return [_source_ref_out(row) for row in rows]


def _source_ref_out(row: LearningSourceRefRow) -> SourceRefOut:
    return SourceRefOut(
        id=row.id,
        concept_id=row.concept_id,
        provider=row.provider,
        url=row.url,
        title=row.title,
        snippet=row.snippet,
        license_hint=row.license_hint,
        fetched_at=row.fetched_at,
    )
