"""M7-04 license report：依赖、模型、内容源与派生对象授权清单端点。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.core.config import get_settings
from app.ops.license_report import (
    build_license_report,
    collect_api_dependencies,
    collect_model_slots,
    collect_web_dependencies,
    resource_view,
    source_view,
)

router = APIRouter(prefix="/api/v1/license", tags=["license"])

# requirements.txt 与 app 包同级（services/api/requirements.txt）
_REQUIREMENTS = Path(__file__).resolve().parents[3] / "requirements.txt"


def _requirements_path() -> Path:
    return _REQUIREMENTS


@router.get("/report")
async def license_report(request: Request) -> dict:
    """聚合四区段清单：依赖（api+web）、模型槽位、内容源、派生对象。"""
    sources_repo = getattr(request.app.state, "sources", None)
    resources_repo = getattr(request.app.state, "resources", None)
    drafts_repo = getattr(request.app.state, "course_import_drafts", None)
    if sources_repo is None or resources_repo is None or drafts_repo is None:
        raise HTTPException(status_code=503, detail="License report requires a database")

    requirements_path = _requirements_path()
    requirements_text = requirements_path.read_text(encoding="utf-8")
    report = build_license_report(
        dependencies=collect_api_dependencies(requirements_text),
        web=collect_web_dependencies(getattr(request.app.state, "web_dir", None)),
        model_slots=collect_model_slots(get_settings()),
        content_sources=[source_view(s) for s in await sources_repo.list()],
        resources=[resource_view(r) for r in await resources_repo.list_all()],
        course_import_drafts=[
            {
                "id": d["id"],
                "status": d["status"],
                "source_license_state": d["source_license_state"],
                "reuse_admission": d["reuse_admission"],
            }
            for d in await drafts_repo.list_by_status(None)
        ],
    )
    return report
