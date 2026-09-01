from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.concepts import router as concepts_router
from app.api.routes.course_workflow import router as course_workflow_router
from app.api.routes.courses import router as courses_router
from app.api.routes.exams import router as exams_router
from app.api.routes.misconceptions import router as misconceptions_router
from app.api.routes.paper_extractor import router as paper_extractor_router
from app.api.routes.papers import router as papers_router
from app.api.routes.planner import router as planner_router
from app.api.routes.resources import router as resources_router
from app.api.routes.review import router as review_router
from app.api.routes.search import router as search_router
from app.api.routes.selection import router as selection_router
from app.api.routes.sources import router as sources_router
from app.api.routes.student import router as student_router
from app.api.routes.system import router as system_router
from app.api.routes.validate import router as validate_router
from app.api.routes.variant_generator import router as variant_generator_router
from app.api.routes.voice import router as voice_router
from app.api.routes.web import router as web_router
from app.core.config import get_settings
from app.db.session import create_engine, make_sessionmaker, prepare_database
from app.domain.rubric_grader import make_rubric_judge
from app.domain.web_gate import RateLimiter
from app.parsing.registry import make_default_registry
from app.parsing.worker import ParseWorker
from app.repositories.chunks import ChunkRepository
from app.repositories.concept_dag import ConceptDagRepository
from app.repositories.course_generation_drafts import CourseGenerationDraftRepository
from app.repositories.course_import_drafts import CourseImportDraftRepository
from app.repositories.memory import MemoryRepository
from app.repositories.misconceptions import MisconceptionRepository
from app.repositories.paper_question_drafts import PaperQuestionDraftRepository
from app.repositories.parsejobs import ParseJobRepository
from app.repositories.postgres import PostgresRepository
from app.repositories.resources import ResourceRepository
from app.repositories.search_queries import SearchQueryRepository
from app.repositories.sources import SourceRepository
from app.repositories.student_state import StudentStateRepository
from app.repositories.variant_question_drafts import VariantQuestionDraftRepository
from app.repositories.voice_answer_events import VoiceAnswerEventRepository
from app.repositories.voice_sessions import VoiceSessionRepository
from app.repositories.voice_trace import VoiceTraceRepository
from app.repositories.voice_transcripts import VoiceTranscriptRepository
from app.search.providers import build_search_registry
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
            voice_transcripts = VoiceTranscriptRepository(sessionmaker)
            voice_sessions = VoiceSessionRepository(sessionmaker)
            voice_answer_events = VoiceAnswerEventRepository(sessionmaker)
            voice_trace = VoiceTraceRepository(sessionmaker)
            search_queries = SearchQueryRepository(sessionmaker)
            course_import_drafts = CourseImportDraftRepository(sessionmaker)
            course_generation_drafts = CourseGenerationDraftRepository(sessionmaker)
            variant_question_drafts = VariantQuestionDraftRepository(sessionmaker)
            paper_question_drafts = PaperQuestionDraftRepository(sessionmaker)
            resources = ResourceRepository(sessionmaker)
            objects = make_object_store(settings)
            parsers = make_default_registry()
            chunks = ChunkRepository(sessionmaker)
            # M5-01 搜索注册表：本地语料恒可用；cloud-web 未配置即 unavailable（记弃用原因）
            search_registry = build_search_registry(chunks, settings)
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
        app.state.voice_transcripts = voice_transcripts if resolved_url else None
        app.state.voice_sessions = voice_sessions if resolved_url else None
        app.state.voice_answer_events = voice_answer_events if resolved_url else None
        app.state.voice_trace = voice_trace if resolved_url else None
        app.state.search_queries = search_queries if resolved_url else None
        app.state.course_import_drafts = course_import_drafts if resolved_url else None
        app.state.course_generation_drafts = course_generation_drafts if resolved_url else None
        app.state.variant_question_drafts = variant_question_drafts if resolved_url else None
        app.state.paper_question_drafts = paper_question_drafts if resolved_url else None
        app.state.search_registry = search_registry if resolved_url else None
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
    app.include_router(paper_extractor_router)
    app.include_router(papers_router)
    app.include_router(exams_router)
    app.include_router(concepts_router)
    app.include_router(course_workflow_router)
    app.include_router(variant_generator_router)
    app.include_router(courses_router)
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
    app.include_router(search_router)
    app.include_router(web_router)
    # M5-03 抓取预检频率限制（per-IP 固定窗口，内存实现，无 DB 也可用）
    app.state.fetch_limiter = RateLimiter(settings.fetch_rate_limit_per_minute)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "ai-learning-os-api"}

    return app


app = create_app()
