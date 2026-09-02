"""M5-01 Search API：多源可插拔搜索 + 执行记录（plan/results/skipped 可回查）。

- GET  /providers：注册表视图（enabled=False 时带 unavailable_reason）；
- POST /queries：执行多源查询——unavailable/未知 provider 进 skipped 记原因，
  不虚报可用；计划、结果、弃用原因一并落 search_queries 表；
- GET  /queries/{id}：回查执行记录。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.api.routes.auth import current_is_admin, current_owner_id
from app.domain.query_planner import build_query_plan, parse_query_slots
from app.domain.result_ranker import rank_and_dedup
from app.search.providers import ProviderUnavailable

router = APIRouter(prefix="/api/v1/search", tags=["search"])

MAX_QUERY_LEN = 200
MAX_LIMIT = 50


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_LEN)
    providers: list[str] | None = None  # 缺省 = 全部已注册源按序执行
    limit: int = Field(default=10, ge=1, le=MAX_LIMIT)

    @field_validator("query")
    @classmethod
    def _reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("查询词不能为空白")
        return value


class SearchSkipped(BaseModel):
    provider: str
    reason: str


class SearchResultItem(BaseModel):
    title: str
    url: str
    snippet: str
    source: str
    provider: str
    authority: str | None = None
    rank_reason: str


class SearchOut(BaseModel):
    query_id: int
    query: str
    providers_requested: list[str]
    results: list[SearchResultItem]
    skipped: list[SearchSkipped]
    result_count: int
    duration_ms: int


class ProviderView(BaseModel):
    name: str
    kind: str
    enabled: bool
    unavailable_reason: str | None


class ProvidersOut(BaseModel):
    items: list[ProviderView]


class PlanRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_LEN)
    providers: list[str] | None = None  # 缺省 = 全部已注册源

    @field_validator("query")
    @classmethod
    def _reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("查询词不能为空白")
        return value


class PlanSlots(BaseModel):
    subject: str | None
    school: str | None
    year: str | None
    course: str | None
    question_type: str | None
    publicity: str | None


class PlanItem(BaseModel):
    provider: str
    query: str
    enabled: bool
    unavailable_reason: str | None


class PlanOut(BaseModel):
    query: str
    slots: PlanSlots
    plan: list[PlanItem]


class SearchRecordOut(BaseModel):
    id: int
    query: str
    providers_requested: list[str]
    providers_skipped: list[SearchSkipped]
    result_count: int
    duration_ms: int
    results: list[SearchResultItem]
    created_at: str


@router.get("/providers", response_model=ProvidersOut)
async def list_search_providers(request: Request) -> ProvidersOut:
    """注册表视图：可插拔搜索源及可用性（不虚报，不可用带原因）。"""
    registry = request.app.state.search_registry
    if registry is None:
        raise HTTPException(status_code=503, detail="Search requires a database")
    return ProvidersOut(
        items=[
            ProviderView(
                name=entry.name,
                kind=entry.kind,
                enabled=entry.enabled,
                unavailable_reason=entry.unavailable_reason,
            )
            for entry in registry
        ]
    )


@router.post("/plan", response_model=PlanOut)
async def build_plan(payload: PlanRequest, request: Request) -> PlanOut:
    """槽位识别 + 多源查询计划生成（只出计划不执行——预览可审计）。"""
    registry = request.app.state.search_registry
    if registry is None:
        raise HTTPException(status_code=503, detail="Search requires a database")

    entries = registry
    if payload.providers is not None:
        by_name = {e.name: e for e in registry}
        entries = []
        for name in payload.providers:
            entry = by_name.get(name)
            if entry is None:
                raise HTTPException(status_code=422, detail=f"未知搜索源: {name}")
            entries.append(entry)

    slots = parse_query_slots(payload.query)
    plan = build_query_plan(slots, payload.query, entries)
    return PlanOut(
        query=payload.query,
        slots=PlanSlots(**slots),
        plan=[PlanItem(**item) for item in plan],
    )


@router.post("/queries", response_model=SearchOut)
async def execute_search(payload: SearchRequest, request: Request) -> SearchOut:
    """执行多源查询：结果与弃用原因一并记录（M5-01 验收面）。"""
    registry = request.app.state.search_registry
    repo = request.app.state.search_queries
    if registry is None or repo is None:
        raise HTTPException(status_code=503, detail="Search requires a database")

    by_name = {entry.name: entry for entry in registry}
    skipped: list[dict] = []
    results: list[dict] = []

    requested = payload.providers if payload.providers is not None else [e.name for e in registry]
    active: list[str] = []
    for name in requested:
        entry = by_name.get(name)
        if entry is None:
            skipped.append({"provider": name, "reason": "未知搜索源"})
        elif not entry.enabled:
            skipped.append({"provider": name, "reason": entry.unavailable_reason or "不可用"})
        else:
            active.append(name)

    t0 = time.monotonic()
    for name in active:
        provider = by_name[name].provider
        assert provider is not None
        try:
            found = await provider.search(
                payload.query, payload.limit, owner=current_owner_id(request)
            )
        except ProviderUnavailable as cause:
            skipped.append({"provider": name, "reason": str(cause)})
            continue
        results.extend(found)

    # M5-04：聚合结果先去重 + 分层排序（理由可见），再截断 limit
    results = rank_and_dedup(results)
    if len(results) > payload.limit:
        results = results[: payload.limit]
    duration_ms = int((time.monotonic() - t0) * 1000)
    record = await repo.record(
        query=payload.query,
        providers_requested=list(requested),
        providers_skipped=skipped,
        result_count=len(results),
        duration_ms=duration_ms,
        results=results,
        owner_id=current_owner_id(request),  # M9-04: 搜索记录归属发起者
    )
    return SearchOut(
        query_id=record["id"],
        query=record["query"],
        providers_requested=record["providers_requested"],
        results=[SearchResultItem(**r) for r in results],
        skipped=[SearchSkipped(**s) for s in skipped],
        result_count=len(results),
        duration_ms=duration_ms,
    )


@router.get("/queries/{query_id}", response_model=SearchRecordOut)
async def get_search_record(query_id: int, request: Request) -> SearchRecordOut:
    """回查执行记录：计划/结果/弃用原因均可审计。"""
    repo = request.app.state.search_queries
    if repo is None:
        raise HTTPException(status_code=503, detail="Search requires a database")
    # M9-04 归属门：auth on 时本人可读，admin 可读全部（治理审计），他人 404
    if not await current_is_admin(request):
        owner = current_owner_id(request)
        if owner is not None and await repo.get_owner(query_id) != owner:
            raise HTTPException(status_code=404, detail="搜索记录不存在")
    record = await repo.get(query_id)
    if record is None:
        raise HTTPException(status_code=404, detail="搜索记录不存在")
    return SearchRecordOut(
        **{k: v for k, v in record.items() if k != "id"},
        id=record["id"],
    )
