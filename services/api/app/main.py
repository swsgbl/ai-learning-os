from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.exams import router as exams_router
from app.api.routes.papers import router as papers_router
from app.core.config import get_settings
from app.db.session import create_engine, make_sessionmaker, prepare_database
from app.repositories.memory import MemoryRepository
from app.repositories.postgres import PostgresRepository


def create_app(database_url: str | None = None) -> FastAPI:
    settings = get_settings()
    resolved_url = database_url if database_url is not None else settings.database_url

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if resolved_url:
            engine = create_engine(resolved_url)
            app.state.engine = engine
            await prepare_database(engine, resolved_url)
            repository: PostgresRepository | MemoryRepository = PostgresRepository(
                make_sessionmaker(engine)
            )
            await repository.seed_papers_if_empty()
        else:
            repository = MemoryRepository()
        app.state.repository = repository
        yield
        if resolved_url:
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

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "ai-learning-os-api"}

    return app


app = create_app()
