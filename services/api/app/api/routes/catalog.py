"""M18-001 课程目录 API（审计升级包 Prompt 1 / Phase 1）。

端点（审计包 03 §4 API outline 的 catalog 子集）：
- POST /api/v1/catalog/sync-runs —— 管理员触发目录同步（Mode A：元数据
  与外链，绝不下载 blob）；幂等（同 commit 重跑全 unchanged）；
- GET  /api/v1/catalog/search —— 目录搜索（q/学段/学科/年级过滤）；
- GET  /api/v1/catalog/resources/{id} —— 条目详情（含许可状态与外链）。

安全边界：
- 同步走 require_admin（目录事实源是管理操作——普通用户只读）；
- tree 抓取经 httpx（可注入 transport 打桩；GitHub API 域名 allowlist
  固定 api.github.com——SSRF 面收敛为常量，不接受任意 URL 注入）；
- 未知许可条目 permission=metadata_only（审计 02 §3）——响应不含
  任何内容承诺，仅外链。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.routes.auth import require_admin, require_user
from app.db.orm import (
    CatalogSyncRunRow,
    ContentPermissionRow,
    ResourceProviderRow,
    ResourceRecordRow,
)
from app.domain.catalog import SYNC_COUNTERS, CatalogSyncError, summarize_sync

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])

#: GitHub API 域（SSRF allowlist——不接受调用方传入的任意 base）
_GITHUB_API = "https://api.github.com"
_CHINATEXTBOOK_REPO = "TapXWorld/ChinaTextbook"
_RAW_BASE = "https://raw.githubusercontent.com/TapXWorld/ChinaTextbook"

_PROVIDER_ID = "rp_chinatextbook"
_PROVIDER_NAME = "ChinaTextbook"


def _now() -> datetime:
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------- 同步（管理员）


class SyncRunRequest(BaseModel):
    provider: str = Field(default=_PROVIDER_NAME, max_length=64)


class SyncRunOut(BaseModel):
    id: str
    provider: str
    commit_sha: str
    status: str
    counters: dict[str, int]
    error: str = ""


def _fetch_tree(
    branch: str = "main", *, transport: httpx.BaseTransport | None = None, timeout: float = 60.0
) -> tuple[str, list[tuple[str, str, int | None]]]:
    """GitHub branch HEAD SHA + 递归 tree（blob 清单）。

    返回 (commit_sha, [(path, blob_id, size)])。HTTPS-only 固定域；
    失败抛 httpx.HTTPError（调用方转 502 如实，不虚构目录）。
    """
    with httpx.Client(
        transport=transport,
        timeout=timeout,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "aios-catalog-sync"},
        trust_env=True,
    ) as client:
        # 默认分支动态解析（live 实测：ChinaTextbook 是 master——不硬编码）
        repo_resp = client.get(f"{_GITHUB_API}/repos/{_CHINATEXTBOOK_REPO}")
        repo_resp.raise_for_status()
        effective_branch = branch if branch != "main" else repo_resp.json()["default_branch"]
        branch_resp = client.get(f"{_GITHUB_API}/repos/{_CHINATEXTBOOK_REPO}/branches/{effective_branch}")
        branch_resp.raise_for_status()
        commit_sha = branch_resp.json()["commit"]["sha"]
        tree_resp = client.get(
            f"{_GITHUB_API}/repos/{_CHINATEXTBOOK_REPO}/git/trees/{commit_sha}",
            params={"recursive": "1"},
        )
        tree_resp.raise_for_status()
        tree = tree_resp.json()
        if tree.get("truncated"):
            raise httpx.HTTPError("tree truncated（仓库过大——需分页策略，拒绝部分目录）")
        blobs = [
            (node["path"], node["sha"], node.get("size"))
            for node in tree.get("tree", [])
            if node.get("type") == "blob"
        ]
        return commit_sha, blobs


async def _ensure_provider(session) -> ResourceProviderRow:
    row = (
        await session.execute(
            select(ResourceProviderRow).where(ResourceProviderRow.name == _PROVIDER_NAME)
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    row = ResourceProviderRow(
        id=_PROVIDER_ID,
        name=_PROVIDER_NAME,
        base_url=f"https://github.com/{_CHINATEXTBOOK_REPO}",
        provider_type="git_tree",
        sync_adapter="github_tree",
        policy_default="metadata_only",
        enabled=True,
        created_at=_now(),
    )
    session.add(row)
    await session.flush()
    return row


@router.post("/sync-runs", response_model=SyncRunOut, status_code=201)
async def create_sync_run(payload: SyncRunRequest, request: Request) -> SyncRunOut:
    """目录同步（Mode A）：pin 快照 → tree 归一化 → 幂等 upsert。"""
    await require_admin(request)
    if payload.provider != _PROVIDER_NAME:
        raise HTTPException(status_code=422, detail=f"未知 provider（本期仅 {_PROVIDER_NAME}）")
    run_id = f"csr_{uuid.uuid4().hex[:22]}"
    async with request.app.state.sessionmaker() as session:
        provider = await _ensure_provider(session)
        session.add(
            CatalogSyncRunRow(
                id=run_id,
                provider_id=provider.id,
                commit_sha="",
                status="running",
                counters_json={},
                started_at=_now(),
            )
        )
        await session.commit()
    try:
        commit_sha, blobs = _fetch_tree()
    except (httpx.HTTPError, KeyError) as cause:
        detail = f"目录抓取失败：{type(cause).__name__}"
        async with request.app.state.sessionmaker() as session:
            run = await session.get(CatalogSyncRunRow, run_id)
            assert run is not None
            run.status = "failed"
            run.error = detail[:500]
            run.finished_at = _now()
            await session.commit()
        raise HTTPException(status_code=502, detail=detail) from cause

    try:
        outcome = summarize_sync(_PROVIDER_NAME, commit_sha, blobs)
    except CatalogSyncError as cause:
        raise HTTPException(status_code=422, detail=str(cause)) from cause

    counters = dict(outcome.counters)
    async with request.app.state.sessionmaker() as session:
        existing = {
            row.source_path: row
            for row in (
                await session.execute(
                    select(ResourceRecordRow).where(ResourceRecordRow.provider_id == provider.id)
                )
            ).scalars()
        }
        seen_paths: set[str] = set()
        now = _now()
        # 两阶段：records 全部落库（flush 拿到 FK 依赖行）再挂 permissions
        # ——单阶段混插会因 autoflush 时序触发 FK violation（生产实测）
        pending_permissions: list[tuple[str, dict]] = []
        for entry in outcome.entries:
            seen_paths.add(entry.source_path)
            raw_url = f"{_RAW_BASE}/{commit_sha}/{entry.source_path}"
            parts_manifest = [
                {"part_index": p.part_index, "source_path": p.source_path} for p in entry.parts
            ]
            prior = existing.get(entry.source_path)
            if prior is None:
                record_id = f"rr_{uuid.uuid4().hex[:22]}"
                session.add(
                    ResourceRecordRow(
                        id=record_id,
                        provider_id=provider.id,
                        source_path=entry.source_path,
                        source_url=raw_url,
                        title=entry.title,
                        title_normalized=entry.title.lower(),
                        education_stage=entry.education_stage,
                        subject=entry.subject,
                        grade=entry.grade,
                        semester=entry.semester,
                        volume=entry.volume,
                        edition_hint=entry.edition_hint,
                        mime_type=entry.mime_type,
                        source_commit_sha=commit_sha,
                        parts_json=parts_manifest,
                        metadata_json={"source_blob_count": len(entry.parts)},
                        created_at=now,
                        updated_at=now,
                    )
                )
                pending_permissions.append(
                    (record_id, {"status": provider.policy_default})
                )
                counters["created"] = counters.get("created", 0) + 1
            elif prior.source_commit_sha != commit_sha:
                prior.source_commit_sha = commit_sha
                prior.source_url = raw_url
                prior.parts_json = parts_manifest
                prior.updated_at = now
                counters["updated"] = counters.get("updated", 0) + 1
            else:
                counters["unchanged"] = counters.get("unchanged", 0) + 1
        await session.flush()  # records 先落库（FK 依赖就绪）
        for record_id, permission_kwargs in pending_permissions:
            session.add(
                ContentPermissionRow(
                    id=f"cp_{uuid.uuid4().hex[:22]}",
                    resource_id=record_id,
                    updated_at=now,
                    notes="目录模式默认（来源无逐资源授权清单——审计 02 §2/§3）",
                    **permission_kwargs,
                )
            )
        # 源侧消失的条目（树里已删）：unavailable 如实（不删本地记录——历史引用）
        counters["unavailable"] = counters.get("unavailable", 0) + len(
            set(existing.keys()) - seen_paths
        )
        run = await session.get(CatalogSyncRunRow, run_id)
        assert run is not None
        run.commit_sha = commit_sha
        run.status = "succeeded"
        run.counters_json = {k: counters.get(k, 0) for k in SYNC_COUNTERS}
        run.finished_at = now
        provider_row = await session.get(ResourceProviderRow, provider.id)
        assert provider_row is not None
        provider_row.last_checked_at = now
        await session.commit()
    return SyncRunOut(
        id=run_id,
        provider=_PROVIDER_NAME,
        commit_sha=commit_sha,
        status="succeeded",
        counters=run.counters_json,
    )


# ---------------------------------------------------------------- 检索（登录用户）


class ResourceOut(BaseModel):
    id: str
    provider: str
    source_path: str
    source_url: str
    title: str
    education_stage: str
    subject: str
    grade: str
    semester: str
    volume: str
    edition_hint: str
    mime_type: str
    parts_count: int
    source_commit_sha: str
    permission_status: str
    license_spdx: str
    attribution_text: str


async def _resource_out(session, row: ResourceRecordRow) -> ResourceOut:
    permission = (
        await session.execute(
            select(ContentPermissionRow).where(ContentPermissionRow.resource_id == row.id)
        )
    ).scalar_one_or_none()
    provider = await session.get(ResourceProviderRow, row.provider_id)
    return ResourceOut(
        id=row.id,
        provider=provider.name if provider else "",
        source_path=row.source_path,
        source_url=row.source_url,
        title=row.title,
        education_stage=row.education_stage,
        subject=row.subject,
        grade=row.grade,
        semester=row.semester,
        volume=row.volume,
        edition_hint=row.edition_hint,
        mime_type=row.mime_type,
        parts_count=len(row.parts_json or []),
        source_commit_sha=row.source_commit_sha,
        permission_status=permission.status if permission else "unknown",
        license_spdx=permission.license_spdx if permission else "",
        attribution_text=permission.attribution_text if permission else "",
    )


@router.get("/search", response_model=list[ResourceOut])
async def search_catalog(
    request: Request,
    q: str = Query(default="", max_length=120),
    stage: str = Query(default="", max_length=32),
    subject: str = Query(default="", max_length=64),
    grade: str = Query(default="", max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ResourceOut]:
    """目录搜索（元数据过滤 + 标题模糊；未同步时返回空列表如实）。"""
    await require_user(request)
    async with request.app.state.sessionmaker() as session:
        query = select(ResourceRecordRow)
        if q:
            query = query.where(
                func.lower(ResourceRecordRow.title).contains(q.lower())
                | func.lower(ResourceRecordRow.source_path).contains(q.lower())
            )
        if stage:
            query = query.where(ResourceRecordRow.education_stage == stage)
        if subject:
            query = query.where(ResourceRecordRow.subject == subject)
        if grade:
            query = query.where(ResourceRecordRow.grade.contains(grade))
        rows = (
            (await session.execute(query.order_by(ResourceRecordRow.source_path).limit(limit)))
            .scalars()
            .all()
        )
        return [await _resource_out(session, row) for row in rows]


@router.get("/resources/{resource_id}", response_model=ResourceOut)
async def get_resource(resource_id: str, request: Request) -> ResourceOut:
    await require_user(request)
    async with request.app.state.sessionmaker() as session:
        row = await session.get(ResourceRecordRow, resource_id)
        if row is None:
            raise HTTPException(status_code=404, detail="目录条目不存在")
        return await _resource_out(session, row)

