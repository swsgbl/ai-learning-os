"""M3-03 StudentConceptState API：概念掌握状态的全量重算与查询（pack F）。

- POST /states/recompute 从全部答案事件重建状态；幂等语义 =
  「同一 (事件集, now) 恒等输出」。now 可显式指定（确定性重算），省略则取服务器当前时刻
  （forgetting_risk 随时间自然增长——这是期望语义，不是重复应用）。
- GET /states 与 GET /states/{concept_id} 读取最近一次物化结果。
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from app.api.schemas import ConceptStateOut, StudentStatesOut
from app.domain.learning_events import LearningEventStream, derive_learning_events
from app.domain.student_state import ConceptState, derive_concept_states, weak_concepts
from app.repositories.student_state import StudentStateRepository

router = APIRouter(prefix="/api/v1/student", tags=["student"])


def _repo(request: Request) -> StudentStateRepository:
    repo = getattr(request.app.state, "student_state", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Student state requires a database")
    return repo


def _parse_now(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(raw)
    except ValueError as cause:
        raise HTTPException(status_code=422, detail=f"now 不是合法 ISO 时间: {raw}") from cause


def _state_out(state: ConceptState) -> ConceptStateOut:
    return ConceptStateOut(
        concept_id=state.concept_id,
        mastery=state.mastery,
        confidence=state.confidence,
        forgetting_risk=state.forgetting_risk,
        evidence_count=state.evidence_count,
        correct_count=state.correct_count,
        wrong_count=state.wrong_count,
        first_event_at=state.first_event_at.isoformat(),
        last_event_at=state.last_event_at.isoformat(),
        updated_at=state.updated_at.isoformat(),
    )


def _states_out(states: list[ConceptState]) -> StudentStatesOut:
    return StudentStatesOut(
        concept_count=len(states),
        states=[_state_out(state) for state in states],
        weak_concepts=weak_concepts(states),
    )


async def all_learning_streams(request: Request) -> list[LearningEventStream]:
    """全部考试的学习事件流（M3-03/04 重算共享编排：事件源读取复用既有 repository）。"""
    student_repo: StudentStateRepository = request.app.state.student_state
    rubric_judge = getattr(request.app.state, "rubric_judge", None)
    streams = []
    for exam_id in await student_repo.list_exam_ids():
        record = await request.app.state.repository.get_exam(exam_id)
        if record is None:
            continue
        paper = await request.app.state.repository.get_paper(record.paper_id)
        if paper is None:
            continue
        streams.append(derive_learning_events(record, paper, rubric_judge=rubric_judge))
    return streams


async def _recompute(request: Request, now: datetime) -> StudentStatesOut:
    """从全部考试的学习事件流重算并物化（routes 层编排：事件源读取复用既有 repository）。"""
    student_repo = _repo(request)
    states = derive_concept_states(await all_learning_streams(request), now=now)
    await student_repo.recompute(states.values())
    return _states_out(await student_repo.list_states())


@router.post("/states/recompute", response_model=StudentStatesOut)
async def recompute_states(request: Request, now: str | None = None) -> StudentStatesOut:
    """M3-03 验收入口：mastery/confidence/forgetting_risk 从事件全量重算，更新幂等。"""
    return await _recompute(request, _parse_now(now))


@router.get("/states", response_model=StudentStatesOut)
async def get_states(request: Request) -> StudentStatesOut:
    return _states_out(await _repo(request).list_states())


@router.get("/states/{concept_id}", response_model=ConceptStateOut)
async def get_state(concept_id: str, request: Request) -> ConceptStateOut:
    state = await _repo(request).get_state(concept_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"概念 {concept_id} 尚无学习状态")
    return _state_out(state)
