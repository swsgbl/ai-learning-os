from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from app.api.error_handlers import register_exception_handlers
from app.api.routes.ai_providers import router as ai_providers_router
from app.api.routes.auth import require_user
from app.api.routes.catalog import router as catalog_router
from app.api.routes.citation_eval import router as citation_eval_router
from app.api.routes.concepts import router as concepts_router
from app.api.routes.course_workflow import router as course_workflow_router
from app.api.routes.courses import router as courses_router
from app.api.routes.eval import router as eval_router
from app.api.routes.exam_history import router as exam_history_router
from app.api.routes.exams import router as exams_router
from app.api.routes.learning import router as learning_router
from app.api.routes.me_ai_keys import router as me_ai_keys_router
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
from app.api.routes.study import router as study_router
from app.api.routes.system import router as system_router
from app.api.routes.validate import router as validate_router
from app.api.routes.variant_generator import router as variant_generator_router
from app.api.routes.voice import router as voice_router
from app.api.routes.voice_eval import router as voice_eval_router
from app.api.routes.voice_live import router as voice_live_router
from app.api.routes.web import router as web_router
from app.core.config import get_settings
from app.core.logging import configure_logging, request_id_var
from app.core.security import validate_auth_secret, validate_exposure
from app.db.session import create_engine, is_sqlite, make_sessionmaker, prepare_database
from app.domain.rubric_grader import make_rubric_judge
from app.domain.voice_runtime_manager import VoiceRuntimeManager
from app.domain.voice_subscriber_factory import ControlledVoiceSubscriberFactory
from app.domain.web_gate import RateLimiter
from app.ops.readiness import Check, CheckOutcome, evaluate_checks
from app.parsing.registry import make_default_registry
from app.parsing.worker import ParseWorker
from app.repositories.ai_provider_secrets import AiProviderSecretsRepository
from app.repositories.audit import AuditRepository
from app.repositories.chunks import ChunkRepository
from app.repositories.concept_dag import ConceptDagRepository
from app.repositories.course_generation_drafts import CourseGenerationDraftRepository
from app.repositories.course_import_drafts import CourseImportDraftRepository
from app.repositories.eval_runs import EvalRunRepository
from app.repositories.memory import MemoryRepository
from app.repositories.misconceptions import MisconceptionRepository
from app.repositories.paper_question_drafts import PaperQuestionDraftRepository
from app.repositories.parsejobs import ParseJobRepository
from app.repositories.postgres import PostgresRepository
from app.repositories.resources import ResourceRepository
from app.repositories.search_queries import SearchQueryRepository
from app.repositories.sources import SourceRepository
from app.repositories.student_state import StudentStateRepository
from app.repositories.user_ai_keys import UserAiKeysRepository
from app.repositories.user_llm_profiles import UserLlmProfilesRepository
from app.repositories.users import UserRepository
from app.repositories.variant_question_drafts import VariantQuestionDraftRepository
from app.repositories.voice_answer_events import VoiceAnswerEventRepository
from app.repositories.voice_sessions import VoiceSessionRepository
from app.repositories.voice_trace import VoiceTraceRepository
from app.repositories.voice_transcripts import VoiceTranscriptRepository
from app.search.providers import build_search_registry
from app.storage.objectstore import make_object_store


def create_app(
    database_url: str | None = None,
    *,
    voice_subscriber_factory: ControlledVoiceSubscriberFactory | None = None,
    voice_pipeline_factory: Callable[[VoiceRuntimeManager], object] | None = None,
) -> FastAPI:
    # M14-262：受控订阅者工厂显式可选注入——非 None 必须是
    # ControlledVoiceSubscriberFactory 实例，在返回 app 之前确定性
    # 拒绝（lifespan 绝不半启动）；None/省略 = 不注册任何工厂
    # （绝不隐式构造生产默认工厂、不读任何凭据）。
    if voice_subscriber_factory is not None and not isinstance(
        voice_subscriber_factory, ControlledVoiceSubscriberFactory
    ):
        raise ValueError("voice_subscriber_factory 必须为 ControlledVoiceSubscriberFactory 实例或 None")
    # M14-272c：语音管线工厂显式可选注入——callable(manager)→pipeline；
    # None/省略 = 不安装管线属性（M14-262 注入语义同构：显式注入、
    # 缺省零变化、注册 ≠ 启动）。
    if voice_pipeline_factory is not None and not callable(voice_pipeline_factory):
        raise ValueError("voice_pipeline_factory 必须为 callable 或 None")
    settings = get_settings()
    # M14-246 结构化 JSON 日志：bootstrap 路径幂等安装；LOG_LEVEL 未配置
    # = 不改变既有 logger 级别语义（零漂移）
    configure_logging(settings.log_level)
    validate_auth_secret(settings.auth_secret, app_env=settings.app_env)  # M9-04 fail-closed
    validate_exposure(  # M9-06/M9-07 公开暴露 fail-closed；M14-37 增媒体面独立绑定
        host_bind_ip=settings.host_bind_ip,
        livekit_bind_ip=settings.host_livekit_bind_ip,
        app_env=settings.app_env,
        auth_secret=settings.auth_secret,
        livekit_api_secret=settings.livekit_api_secret,
        cors_origins=settings.cors_origins,
        public_livekit_url=settings.public_livekit_url,
    )
    resolved_url = database_url if database_url is not None else settings.database_url

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if resolved_url:
            engine = create_engine(resolved_url)
            app.state.engine = engine
            await prepare_database(engine, resolved_url)
            sessionmaker = make_sessionmaker(engine)
            # M15-008: provider 密钥仓储先于 llm judge 构建（resolver 依赖它）
            ai_provider_secrets = AiProviderSecretsRepository(sessionmaker)
            user_ai_keys = UserAiKeysRepository(sessionmaker)
            llm_profiles = UserLlmProfilesRepository(sessionmaker)
            app.state.ai_provider_secrets = ai_provider_secrets
            # M10-01: LLM judge 经 OpenAI 兼容 gateway；槽位未配齐时 build 返回 None
            # （essay 全部进复核，fail-closed 不虚报模型能力）。
            # M15-008: llm 密钥经治理 resolver（DB 在线密钥优先，回退 env）——
            # 在线填写后重启容器即生效。
            if settings.rubric_judge == "llm":
                from app.api.routes.ai_providers import resolve_provider_key
                from app.llm.rubric_judge import UserAwareRubricJudge, build_llm_judge

                rubric_judge = UserAwareRubricJudge(
                    build_llm_judge(
                        {
                            "endpoint": settings.llm_endpoint,
                            "api_key": await resolve_provider_key(app, "llm"),
                            "model": settings.llm_model,
                            "num_ctx": settings.llm_num_ctx,
                            "timeout_seconds": settings.llm_timeout_seconds,
                        }
                    )
                )
            else:
                rubric_judge = make_rubric_judge(settings.rubric_judge)
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
            eval_runs = EvalRunRepository(sessionmaker)
            paper_question_drafts = PaperQuestionDraftRepository(sessionmaker)
            resources = ResourceRepository(sessionmaker)
            users = UserRepository(sessionmaker)
            audit = AuditRepository(sessionmaker)
            objects = make_object_store(settings)
            parsers = make_default_registry()
            chunks = ChunkRepository(sessionmaker)
            # M5-01 搜索注册表：本地语料恒可用；cloud-web 未配置即 unavailable（记弃用原因）
            search_registry = build_search_registry(chunks, settings)
            parse_jobs = ParseJobRepository(sessionmaker)
            worker = ParseWorker(parse_jobs, resources, chunks, objects, parsers)
            # M1-07 可恢复：重启时把 running 任务重置 pending，再启动消费循环
            await parse_jobs.recover_stale_running()
            # M9-01 flaky 修复：SQLite 测试替身是 StaticPool 单连接，后台 worker
            # 的事务会与请求事务在同一物理连接上交错（慢 CI runner 上随机踩踏，
            # 表现为「刚写入的数据读不到」）。后台循环只在池化后端（PG）启动；
            # worker 行为本身由 test_parse_job_queue 裸装配单元测试覆盖。
            if not is_sqlite(resolved_url):
                worker.start()
        else:
            rubric_judge = make_rubric_judge(settings.rubric_judge)
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
        app.state.eval_runs = eval_runs if resolved_url else None
        app.state.paper_question_drafts = paper_question_drafts if resolved_url else None
        app.state.search_registry = search_registry if resolved_url else None
        app.state.resources = resources if resolved_url else None
        app.state.users = users if resolved_url else None
        app.state.ai_provider_secrets = ai_provider_secrets if resolved_url else None
        app.state.user_ai_keys = user_ai_keys if resolved_url else None
        app.state.llm_profiles = llm_profiles if resolved_url else None
        app.state.audit = audit if resolved_url else None
        app.state.objects = objects if resolved_url else None
        app.state.parsers = parsers if resolved_url else None
        app.state.chunks = chunks if resolved_url else None
        app.state.parse_jobs = parse_jobs if resolved_url else None
        app.state.worker = worker if resolved_url else None
        # M14-259：语音运行时管理器——lifespan 显式拥有恰一个实例
        # （零模块级单例；快照状态经 manager 自身 snapshot() 透出，无
        # 包装状态）。当前**未注册任何生产订阅者工厂**——这是 lifespan
        # 所有权与确定性关停的 fake-only 接线切片。
        voice_runtime_manager = VoiceRuntimeManager()
        app.state.voice_runtime_manager = voice_runtime_manager
        # M14-262：显式注入的受控订阅者工厂——同一实例挂到
        # app.state（诊断面；退出后属性保留、实例不变）；未注入则
        # 不安装该属性（无隐式生产工厂）。注册 ≠ 启动：仅当调用方
        # 经管理器显式 start 才创建订阅者；管理器容量/构造/关停顺序
        # 零变化（close 即排空、先于 worker stop 与 engine dispose、
        # 幂等——M14-258/259 契约保持）。
        if voice_subscriber_factory is not None:
            app.state.voice_subscriber_factory = voice_subscriber_factory
        # M14-272c：管线工厂在 manager 建立后安装（共享同一 manager
        # ——单一管理面）；注册 ≠ 启动：安装不打开任何会话。
        voice_pipeline = None
        if voice_pipeline_factory is not None:
            voice_pipeline = voice_pipeline_factory(voice_runtime_manager)
            app.state.voice_pipeline = voice_pipeline
        try:
            yield
        finally:
            # 关停顺序（M14-258 close 即排空，绝不另调 stop_all 双排空）：
            # 先关管线会话（确定性四段关停——M14-272a），再关语音
            # 管理器（排空订阅者），最后停 parse worker、释放数据库
            # 引擎——close 幂等，应用阶段异常路径同样执行。
            if voice_pipeline is not None:
                for session_id in list(getattr(voice_pipeline, "_sessions", {})):
                    await voice_pipeline.close_session(session_id)
            await voice_runtime_manager.close()
        if resolved_url:
            await worker.stop()
            await engine.dispose()

    app = FastAPI(
        title="AI Learning OS API",
        version="0.1.0",
        description="Server-authoritative exam and learning APIs.",
        lifespan=lifespan,
        # M9-01/M10-03 认证门禁：AUTH_SECRET 配置后全业务路径要求
        # Bearer token 或 HttpOnly cookie；未配置时本地模式放行
        dependencies=[Depends(require_user)],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_origin_regex=r"^https?://(?:localhost|127\.0\.0\.1)(?::\d+)?$" if settings.app_env == "development" else None,
        # Web 与 API 端口不同，浏览器登录态依赖 HttpOnly cookie 跨端口携带。
        # CORS 源仍是精确 allowlist； SameSite=Lax 负责拦跨站非导航请求。
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        """M9-04: 每请求 request id（可透传上游 X-Request-ID），审计留痕用。

        M9-05: 客户端值只接受 ≤64 字符的 [A-Za-z0-9-_]，非法/超长一律重新生成
        （防日志注入与超大 header 滥用）。

        M14-246: request id 同步写入日志上下文（ContextVar）——请求期间的
        应用日志经 JSON formatter 自动携带同一关联 id；异常路径下仍先复位
        上下文再上抛（不吞异常、不改状态语义）。
        """
        import re as _re
        import uuid

        incoming = request.headers.get("X-Request-ID") or ""
        if _re.fullmatch(r"[A-Za-z0-9_-]{1,64}", incoming):
            request.state.request_id = incoming
        else:
            request.state.request_id = f"req-{uuid.uuid4().hex[:12]}"
        token = request_id_var.set(request.state.request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request.state.request_id
        finally:
            request_id_var.reset(token)
        return response
    app.include_router(ai_providers_router)
    app.include_router(me_ai_keys_router)
    app.include_router(paper_extractor_router)
    app.include_router(papers_router)
    app.include_router(exam_history_router)
    app.include_router(learning_router)
    app.include_router(study_router)
    app.include_router(exams_router)
    app.include_router(concepts_router)
    app.include_router(course_workflow_router)
    app.include_router(variant_generator_router)
    app.include_router(eval_router)
    app.include_router(voice_eval_router)
    app.include_router(catalog_router)
    app.include_router(citation_eval_router)
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
    app.include_router(voice_live_router)  # M14-272d：服务端 live 会话（仅语音入口安装管线时有数据面）
    app.include_router(search_router)
    from app.api.routes.license import router as license_router

    app.include_router(license_router)

    from app.api.routes.version import router as version_router

    app.include_router(version_router)
    from app.api.routes.meta import router as meta_router

    app.include_router(meta_router)  # M15-006: 应用最新版本透出（更新检查）
    from app.api.routes.auth import router as auth_router

    app.include_router(auth_router)
    from app.api.routes.audit import router as audit_router

    app.include_router(audit_router)
    app.include_router(web_router)
    # M5-03 抓取预检频率限制（per-IP 固定窗口，内存实现，无 DB 也可用）
    app.state.fetch_limiter = RateLimiter(settings.fetch_rate_limit_per_minute)
    # M14-246 全局异常处理（DomainError HTTP 映射 + 未捕获异常 500 兜底）
    register_exception_handlers(app)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "ai-learning-os-api"}

    # M14-246: /readyz readiness（与 /health liveness 语义分离）。默认检查 =
    # 启动装配完成（repository 已接线，内存/DB 模式皆可判）；部署级依赖检查
    # （DB/Redis/voice/LiveKit）经 app.state.readiness_checks 注入，单测用假
    # 检查，绝不依赖真实外部服务；reason 经 sanitize_reason 净化面——安全
    # 文案/异常类名保留，连接串/敏感标记等不安全形态固定安全回退；检查
    # 注册表为空时 fail-closed（503，绝不 all([]) 恒真虚报 ready）。
    async def _repository_wired() -> CheckOutcome:
        if getattr(app.state, "repository", None) is None:
            return CheckOutcome(ready=False, reason="startup incomplete: repository not wired")
        return CheckOutcome(ready=True)

    app.state.readiness_checks: dict[str, Check] = {"repository": _repository_wired}

    @app.get("/readyz", tags=["system"])
    async def readyz(request: Request) -> JSONResponse:
        checks: dict[str, Check] = getattr(request.app.state, "readiness_checks", {}) or {}
        results = await evaluate_checks(checks)
        if results:
            ready = all(bool(entry["ready"]) for entry in results.values())
        else:
            # fail-closed：检查注册表为空 = 无任何就绪证明，绝不因
            # all([]) 恒真而虚报 ready；诊断条目为固定安全文案。
            ready = False
            results = {"readiness": {"ready": False, "reason": "no readiness checks registered"}}
        body = {"status": "ready" if ready else "not_ready", "checks": results}
        return JSONResponse(body, status_code=200 if ready else 503)

    return app


app = create_app()
