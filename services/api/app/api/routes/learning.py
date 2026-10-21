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

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.routes.auth import current_owner_id, require_user
from app.db.orm import (
    LearningAttemptRow,
    LearningContractRow,
    LearningEventRow,
    LearningSprintRow,
)
from app.domain.mastery import AttemptEvidence, derive_mastery
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
