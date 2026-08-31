from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.concepts import router as concepts_router
from app.api.routes.exams import router as exams_router
from app.api.routes.misconceptions import router as misconceptions_router
from app.api.routes.papers import router as papers_router
from app.api.routes.planner import router as planner_router
from app.api.routes.resources import router as resources_router
from app.api.routes.review import router as review_router
from app.api.routes.selection import router as selection_router
from app.api.routes.sources import router as sources_router
from app.api.routes.student import router as student_router
from app.api.routes.system import router as system_router
from app.api.routes.validate import router as validate_router
from app.api.routes.voice import router as voice_router
from app.core.config import get_settings
from app.db.session import create_engine, make_sessionmaker, prepare_database
from app.domain.rubric_grader import make_rubric_judge
from app.parsing.registry import make_default_registry
from app.parsing.worker import ParseWorker
from app.repositories.chunks import ChunkRepository
from app.repositories.concept_dag import ConceptDagRepository
from app.repositories.memory import MemoryRepository
from app.repositories.misconceptions import MisconceptionRepository
from app.repositories.parsejobs import ParseJobRepository
from app.repositories.postgres import PostgresRepository
from app.repositories.resources import ResourceRepository
from app.repositories.sources import SourceRepository
from app.repositories.student_state import StudentStateRepository
from app.storage.objectstore import make_object_store


def create_app(database_url: str | None = None) -> FastAPI:
    settings = get_settings()
    resolved_url = database_url if database_url is not None else settings.database_url
    rubric_judge = make_rubric_judge(settings.rubric_judge)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if resolved_url:
            engine = create_engine(resolved_url)
            app.state.engine = engine
            await prepare_database(engine, resolved_url)
            sessionmaker = make_sessionmaker(engine)
            repository: PostgresRepository | MemoryRepository = PostgresRepository(
                sessionmaker, rubric_judge=rubric_judge
            )
            await repository.seed_papers_if_empty()
            sources = SourceRepository(sessionmaker)
            await sources.seed_if_empty()
            concept_dag = ConceptDagRepository(sessionmaker)
            student_state = StudentStateRepository(sessionmaker)
            misconceptions = MisconceptionRepository(sessionmaker)
            resources = ResourceRepository(sessionmaker)
            objects = make_object_store(settings)
            parsers = make_default_registry()
            chunks = ChunkRepository(sessionmaker)
            parse_jobs = ParseJobRepository(sessionmaker)
            worker = ParseWorker(parse_jobs, resources, chunks, objects, parsers)
            # M1-07 可恢复：重启时把 running 任务重置 pending，再启动消费循环
            await parse_jobs.recover_stale_running()
            worker.start()
        else:
            repository = MemoryRepository(rubric_judge=rubric_judge)
        app.state.repository = repository
        app.state.rubric_judge = rubric_judge
        app.state.sessionmaker = sessionmaker if resolved_url else None
        app.state.sources = sources if resolved_url else None
        app.state.concept_dag = concept_dag if resolved_url else None
        app.state.student_state = student_state if resolved_url else None
        app.state.misconceptions = misconceptions if resolved_url else None
        app.state.resources = resources if resolved_url else None
        app.state.objects = objects if resolved_url else None
        app.state.parsers = parsers if resolved_url else None
        app.state.chunks = chunks if resolved_url else None
        app.state.parse_jobs = parse_jobs if resolved_url else None
        app.state.worker = worker if resolved_url else None
        yield
        if resolved_url:
            await worker.stop()
            await engine.dispose()

    app = FastAPI(
        title="AI Learning OS API",
        version="0.1.0",
        description="Server-authoritative exam and learning APIs.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_origin_regex=r"^https?://(?:localhost|127\.0\.0\.1)(?::\d+)?$" if settings.app_env == "development" else None,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(papers_router)
    app.include_router(exams_router)
    app.include_router(concepts_router)
    app.include_router(student_router)
    app.include_router(misconceptions_router)
    app.include_router(review_router)
    app.include_router(planner_router)
    app.include_router(selection_router)
    app.include_router(sources_router)
    app.include_router(resources_router)
    app.include_router(system_router)
    app.include_router(validate_router)
    app.include_router(voice_router)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "ai-learning-os-api"}

    return app


app = create_app()
