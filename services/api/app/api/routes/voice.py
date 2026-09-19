"""M4-01/M4-02 Voice API：房间 token 签发 + ASR/TTS adapter 端点。

- POST /token：M4-01 房间 JWT（过期和权限边界可测试）。
- GET  /providers：M4-02 provider 配置视图（VOICE_MODE 三模式切换结果，无 DB 依赖）。
- POST /transcribe：语音→文本；transcript 恒落盘，原始音频按
  PRIVACY_STORE_AUDIO 策略落对象存储或显式丢弃（runbook §3）。
- POST /synthesize：文本→WAV 音频（tone 本地合成或云端 provider）。

M14-01：local/hybrid 的 ASR 与 local 的 TTS 支持「本地真实引擎」HTTP adapter
（local-funasr / local-cosyvoice，OpenAI 兼容本机服务，部署见 tools/voice/）；
endpoint 未配置时降级零依赖替身并在 providers 视图与响应头透出 fallback。
"""
from __future__ import annotations

import hashlib
import re
import time

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from app.api.routes.auth import current_owner_id
from app.core.config import get_settings
from app.domain import voice_session_fsm as fsm
from app.domain.answer_normalizer import normalize_answer
from app.domain.intent_parser import INTENT_UNKNOWN, ordinal_to_letter, parse
from app.domain.models import ExamStatus
from app.domain.report import build_report
from app.domain.voice_report import build_voice_report
from app.domain.voice_tokens import (
    DEFAULT_TTL_SECONDS,
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    ROLE_STUDENT,
    VOICE_ROLES,
    VoiceTokenError,
    build_voice_token,
    new_identity,
)
from app.domain.voice_trace import MAX_DURATION_MS, TRACE_STAGES, summarize_spans
from app.voice.providers import (
    ASR_LOCAL_FUNASR,
    LOCAL_ASR,
    LOCAL_TTS,
    TTS_LOCAL_COSYVOICE,
    CloudOpenAiAsrProvider,
    CloudOpenAiTtsProvider,
    FakeAsrProvider,
    LocalCosyVoiceTtsProvider,
    LocalFunAsrAsrProvider,
    ProviderUnavailable,
    ToneTtsProvider,
)
from app.voice.routing import (
    VOICE_MODES,
    resolve_asr,
    resolve_tts,
)

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])

_ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")

MAX_AUDIO_BYTES = 20 * 1024 * 1024  # 20MB：单次转写输入上限


class VoiceTokenRequest(BaseModel):
    room: str
    identity: str | None = None
    ttl_seconds: int = Field(default=DEFAULT_TTL_SECONDS, ge=MIN_TTL_SECONDS, le=MAX_TTL_SECONDS)
    role: str = ROLE_STUDENT


class VoiceTokenOut(BaseModel):
    token: str
    room: str
    identity: str
    role: str
    expires_at: str
    ws_url: str


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ProviderView(BaseModel):
    """单个语音环节的 provider 解析结果（不虚报：fallback=True 表示云端未配置已降级本地）。"""

    requested: str | None
    provider: str
    fallback: bool


class ProvidersOut(BaseModel):
    voice_mode: str
    asr: ProviderView
    tts: ProviderView
    privacy_store_audio: bool
    privacy_send_context_to_cloud: bool


class TranscriptionOut(BaseModel):
    id: int
    text: str
    confidence: float
    provider: str
    latency_ms: int
    audio_bytes: int
    audio_stored: bool
    audio_object_key: str | None


class TranscriptView(BaseModel):
    id: int
    provider: str
    text: str
    confidence: float
    latency_ms: int
    audio_bytes: int
    audio_stored: bool
    audio_object_key: str | None
    exam_id: str | None
    question_id: str | None
    created_at: str


class TranscriptsOut(BaseModel):
    item_count: int
    items: list[TranscriptView]


@router.post("/token", response_model=VoiceTokenOut)
async def issue_voice_token(payload: VoiceTokenRequest, request: Request) -> VoiceTokenOut:
    settings = get_settings()
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise HTTPException(status_code=503, detail="Voice requires LiveKit configuration")
    if not _ROOM_RE.fullmatch(payload.room):
        raise HTTPException(status_code=422, detail="room 需为 3-64 位字母数字/-/_")
    if payload.role not in VOICE_ROLES:
        raise HTTPException(status_code=422, detail=f"未知角色: {payload.role}")
    identity = payload.identity or new_identity()
    try:
        token = build_voice_token(
            payload.room,
            identity,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
            ttl_seconds=payload.ttl_seconds,
            role=payload.role,
        )
    except VoiceTokenError as cause:
        raise HTTPException(status_code=422, detail=str(cause)) from cause
    return VoiceTokenOut(
        token=token.token,
        room=token.room,
        identity=token.identity,
        role=payload.role,
        expires_at=token.expires_at.isoformat(),
        # M9-08: 浏览器可达地址优先（公开模式 AIOS_PUBLIC_LIVEKIT_URL），绝不返回容器内部地址
        ws_url=settings.public_livekit_url or settings.livekit_url or "ws://127.0.0.1:7880",
    )


def _voice_mode(settings) -> str:
    mode = settings.voice_mode
    if mode not in VOICE_MODES:
        raise HTTPException(status_code=422, detail=f"非法 VOICE_MODE: {mode}")
    return mode


@router.get("/providers", response_model=ProvidersOut)
async def list_providers() -> ProvidersOut:
    """provider 配置视图：当前 VOICE_MODE 下 ASR/TTS 的实际选择（含 M14-01 本地真实引擎）。"""
    settings = get_settings()
    mode = _voice_mode(settings)
    asr_ready = bool(settings.asr_cloud_endpoint and settings.asr_cloud_api_key)
    tts_ready = bool(settings.tts_cloud_endpoint and settings.tts_cloud_api_key)
    # M14-01：本地真实引擎 readiness 只看 endpoint（key 可选——本地服务默认无鉴权）
    asr_local_ready = bool(settings.asr_local_endpoint)
    tts_local_ready = bool(settings.tts_local_endpoint)
    asr = resolve_asr(mode, settings.asr_provider, cloud_ready=asr_ready, local_ready=asr_local_ready)
    tts = resolve_tts(mode, settings.tts_provider, cloud_ready=tts_ready, local_ready=tts_local_ready)
    return ProvidersOut(
        voice_mode=mode,
        asr=ProviderView(requested=settings.asr_provider, provider=asr.provider, fallback=asr.fallback),
        tts=ProviderView(requested=settings.tts_provider, provider=tts.provider, fallback=tts.fallback),
        privacy_store_audio=settings.privacy_store_audio,
        privacy_send_context_to_cloud=settings.privacy_send_context_to_cloud,
    )


def _build_asr(settings, choice_provider: str):
    if choice_provider == LOCAL_ASR:
        return FakeAsrProvider()
    if choice_provider == ASR_LOCAL_FUNASR:
        return LocalFunAsrAsrProvider(
            settings.asr_local_endpoint or "",
            settings.asr_local_api_key or "",
            settings.asr_local_model,
            timeout=settings.asr_local_timeout_seconds,
        )
    if choice_provider == "cloud-openai":
        return CloudOpenAiAsrProvider(
            settings.asr_cloud_endpoint or "",
            settings.asr_cloud_api_key or "",
            settings.asr_cloud_model,
        )
    raise HTTPException(status_code=422, detail=f"未知 ASR provider: {choice_provider}")


def _build_tts(settings, choice_provider: str):
    if choice_provider == LOCAL_TTS:
        return ToneTtsProvider()
    if choice_provider == TTS_LOCAL_COSYVOICE:
        return LocalCosyVoiceTtsProvider(
            settings.tts_local_endpoint or "",
            settings.tts_local_api_key or "",
            settings.tts_local_model,
            timeout=settings.tts_local_timeout_seconds,
        )
    if choice_provider == "cloud-openai-tts":
        return CloudOpenAiTtsProvider(
            settings.tts_cloud_endpoint or "",
            settings.tts_cloud_api_key or "",
            settings.tts_cloud_model,
            settings.tts_cloud_voice,  # M14-65: 音色透传（默认 tongtong；空=不带 voice 字段）
        )
    raise HTTPException(status_code=422, detail=f"未知 TTS provider: {choice_provider}")


@router.post("/transcribe", response_model=TranscriptionOut)
async def transcribe(request: Request, response: Response, audio: UploadFile) -> TranscriptionOut:
    """语音→文本：transcript 恒落盘；原始音频按 PRIVACY_STORE_AUDIO 策略处理。

    provider 与 fallback 经响应头透出（X-Voice-Provider/X-Voice-Fallback，与
    /synthesize 同口径）——本地真实引擎未配置降级替身时不虚报。
    """
    settings = get_settings()
    mode = _voice_mode(settings)
    repo = request.app.state.voice_transcripts
    if repo is None:
        raise HTTPException(status_code=503, detail="Transcription requires a database")
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=422, detail="音频内容为空")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="音频超过 20MB 上限")

    choice = resolve_asr(
        mode,
        settings.asr_provider,
        cloud_ready=bool(settings.asr_cloud_endpoint and settings.asr_cloud_api_key),
        local_ready=bool(settings.asr_local_endpoint),
    )
    provider = _build_asr(settings, choice.provider)
    t0 = time.monotonic()
    try:
        result = await provider.transcribe(data, content_type=audio.content_type or "audio/wav")
    except ProviderUnavailable as cause:
        raise HTTPException(status_code=502, detail=str(cause)) from cause
    await _record_trace(request, stage="asr", duration_ms=int((time.monotonic() - t0) * 1000))
    response.headers["X-Voice-Provider"] = result.provider
    response.headers["X-Voice-Fallback"] = "1" if choice.fallback else "0"

    audio_object_key: str | None = None
    audio_stored = False
    if settings.privacy_store_audio:
        digest = hashlib.sha256(data).hexdigest()
        audio_object_key = f"voice/{digest[:2]}/{digest}"
        request.app.state.objects.put(audio_object_key, data, audio.content_type or "audio/wav")
        audio_stored = True
    view = await repo.save(
        owner_id=current_owner_id(request),
        provider=result.provider,
        text=result.text,
        confidence=result.confidence,
        latency_ms=result.latency_ms,
        audio_bytes=len(data),
        audio_object_key=audio_object_key,
        audio_stored=audio_stored,
    )
    return TranscriptionOut(
        id=view["id"],
        text=view["text"],
        confidence=view["confidence"],
        provider=view["provider"],
        latency_ms=view["latency_ms"],
        audio_bytes=view["audio_bytes"],
        audio_stored=view["audio_stored"],
        audio_object_key=view["audio_object_key"],
    )


@router.get("/transcripts", response_model=TranscriptsOut)
async def list_transcripts(request: Request, limit: int = 50) -> TranscriptsOut:
    """最近转写记录（transcript 恒存的回查面；不含音频内容本身）。"""
    repo = request.app.state.voice_transcripts
    if repo is None:
        raise HTTPException(status_code=503, detail="Transcripts require a database")
    limit = max(1, min(limit, 200))
    owner = current_owner_id(request)
    rows = (
        await repo.list_for_owner(owner, limit=limit)
        if owner is not None
        else await repo.list_recent(limit=limit)
    )
    return TranscriptsOut(
        item_count=len(rows),
        items=[TranscriptView(**row) for row in rows],
    )


@router.post("/synthesize")
async def synthesize(payload: SynthesizeRequest, request: Request) -> Response:
    """文本→WAV。合成不落库（派生音频无留存需求），provider 透出响应头。"""
    settings = get_settings()
    mode = _voice_mode(settings)
    choice = resolve_tts(
        mode,
        settings.tts_provider,
        cloud_ready=bool(settings.tts_cloud_endpoint and settings.tts_cloud_api_key),
        local_ready=bool(settings.tts_local_endpoint),
    )
    provider = _build_tts(settings, choice.provider)
    t0 = time.monotonic()
    try:
        result = await provider.synthesize(payload.text)
    except ProviderUnavailable as cause:
        raise HTTPException(status_code=502, detail=str(cause)) from cause
    await _record_trace(request, stage="tts", duration_ms=int((time.monotonic() - t0) * 1000))
    return Response(
        content=result.audio,
        media_type="audio/wav",
        headers={
            "X-Voice-Provider": result.provider,
            "X-Voice-Fallback": "1" if choice.fallback else "0",
        },
    )


# ---------- M4-03 VoiceSession FSM ----------


class VoiceSessionCreateRequest(BaseModel):
    exam_id: str


class VoiceCommandRequest(BaseModel):
    type: str
    question_id: str | None = None
    answer: str | None = None
    ambiguous: bool = False
    expected_revision: int | None = None


class VoiceSessionOut(BaseModel):
    session_id: str
    exam_id: str
    status: str
    question_index: int
    revision: int
    created_at: str
    updated_at: str


class VoiceCommandOut(BaseModel):
    applied_event: str
    from_status: str
    session: VoiceSessionOut
    clarified_question: str | None = None  # 进入 CLARIFYING 时供 TTS 播报的追问
    question_total: int | None = None  # 全卷完成时透出，供播报收尾


async def _require_owned_session(request: Request, session_id: str) -> None:
    """M9-02 语音会话读取门：session -> exam -> owner 链式校验（auth on 时 404）。"""
    owner = current_owner_id(request)
    if owner is None:
        return
    view = await request.app.state.voice_sessions.get(session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="语音会话不存在")
    exam_owner = await request.app.state.repository.get_exam_owner(view["exam_id"])
    if exam_owner != owner:
        raise HTTPException(status_code=404, detail="语音会话不存在")


@router.post("/sessions", response_model=VoiceSessionOut, status_code=201)
async def create_voice_session(payload: VoiceSessionCreateRequest, request: Request) -> VoiceSessionOut:
    """为进行中的考试创建语音会话（SESSION_READY）。"""
    sessions = request.app.state.voice_sessions
    if sessions is None:
        raise HTTPException(status_code=503, detail="Voice sessions require a database")
    exam_owner = await request.app.state.repository.get_exam_owner(payload.exam_id)
    owner = current_owner_id(request)
    if owner is not None and exam_owner != owner:
        raise HTTPException(status_code=404, detail="考试不存在")
    exam = await request.app.state.repository.get_exam(payload.exam_id)
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")
    if exam.status != ExamStatus.ACTIVE:
        raise HTTPException(status_code=409, detail=f"考试状态 {exam.status.value} 不允许语音作答")
    view = await sessions.create(payload.exam_id)
    return VoiceSessionOut(**view)


@router.get("/sessions/{session_id}", response_model=VoiceSessionOut)
async def get_voice_session(session_id: str, request: Request) -> VoiceSessionOut:
    await _require_owned_session(request, session_id)
    sessions = request.app.state.voice_sessions
    if sessions is None:
        raise HTTPException(status_code=503, detail="Voice sessions require a database")
    view = await sessions.get(session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="语音会话不存在")
    return VoiceSessionOut(**view)


# ---------- M4-07 断线恢复：重连后从当前题、当前选项和服务端状态继续 ----------


class VoiceSessionsOut(BaseModel):
    items: list[VoiceSessionOut]


class VoiceResumeOut(BaseModel):
    session: VoiceSessionOut
    question: dict | None = None  # 当前题公开字段（id/type/stem/options）——不含 answer/explanation
    committed_answer: str | None = None  # 服务端权威已提交答案（exam answer_events 最新值）
    question_total: int


@router.get("/sessions", response_model=VoiceSessionsOut)
async def list_voice_sessions(exam_id: str, request: Request) -> VoiceSessionsOut:
    """按考试列出语音会话（断线重连后客户端凭 exam_id 找回会话）。"""
    owner = current_owner_id(request)
    if owner is not None:
        exam_owner = await request.app.state.repository.get_exam_owner(exam_id)
        if exam_owner != owner:
            raise HTTPException(status_code=404, detail="考试不存在")
    sessions = request.app.state.voice_sessions
    if sessions is None:
        raise HTTPException(status_code=503, detail="Voice sessions require a database")
    views = await sessions.list_for_exam(exam_id)
    return VoiceSessionsOut(items=[VoiceSessionOut(**view) for view in views])


@router.get("/sessions/{session_id}/resume", response_model=VoiceResumeOut)
async def resume_voice_session(session_id: str, request: Request) -> VoiceResumeOut:
    await _require_owned_session(request, session_id)
    """断线恢复视图：服务端状态 + 当前题公开内容 + 已提交答案。

    只读不迁移（幂等：同状态多次恢复恒同输出）；播报期断线由客户端
    凭返回的当前题重播；已提交答案以 exam answer_events 权威值为准。
    """
    sessions = request.app.state.voice_sessions
    if sessions is None:
        raise HTTPException(status_code=503, detail="Voice sessions require a database")
    view = await sessions.get(session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="语音会话不存在")
    if fsm.is_terminal(view["status"]):
        raise HTTPException(status_code=409, detail="会话已结束，无可恢复状态")

    exam, paper = await _load_paper(request, view["exam_id"])
    questions = list(paper.questions)
    index = view["question_index"]
    question: dict | None = None
    committed_answer: str | None = None
    if index < len(questions):
        current = questions[index]
        question = {
            "id": current.id,
            "type": current.type,
            "stem": current.stem,
            "options": [{"key": option.key, "text": option.text} for option in current.options],
        }
        committed_answer = exam.answers.get(current.id)
    return VoiceResumeOut(
        session=VoiceSessionOut(**view),
        question=question,
        committed_answer=committed_answer,
        question_total=len(questions),
    )


# ---------- M4-08 Voice report：REPORT_READY 后的语音播报投影 ----------


class VoiceReportOut(BaseModel):
    session_id: str
    exam_id: str
    spoken_text: str  # 第一遍播报：短结论 + 总分 + 错题数（分层播报）
    mistake_summary: list[dict]  # 错题摘要（题号/截断题干/你的答案/正确答案/概念/错因）
    remediation_summary: list[dict]  # 补救建议（聚合到概念级）
    written_report_url: str  # 书面报告入口（深入讲解走 GET /exams/{id}/report）


@router.get("/sessions/{session_id}/report", response_model=VoiceReportOut)
async def get_voice_report(session_id: str, request: Request) -> VoiceReportOut:
    await _require_owned_session(request, session_id)
    """语音报告：REPORT_READY 终态后从判分结果投影播报视图（M4-08）。

    只投影不判定——分数/错题/补救全部来自 M2-11 build_report 判分结果；
    幂等只读，同状态多次获取恒同输出。
    """
    sessions = request.app.state.voice_sessions
    if sessions is None:
        raise HTTPException(status_code=503, detail="Voice sessions require a database")
    view = await sessions.get(session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="语音会话不存在")
    if not fsm.is_terminal(view["status"]):
        raise HTTPException(status_code=409, detail="全卷完成后才可获取报告")

    exam, paper = await _load_paper(request, view["exam_id"])
    submission = await request.app.state.repository.get_submission(view["exam_id"])
    if submission is None:
        raise HTTPException(status_code=409, detail="考试尚未提交判分，先提交后获取报告")

    report = build_report(submission, paper)
    voice_view = build_voice_report(exam.exam_id, report)
    return VoiceReportOut(
        session_id=session_id,
        exam_id=exam.exam_id,
        **voice_view,
    )


# ---------- M4-09 Latency tracing：各阶段耗时观测 ----------


async def _record_trace(
    request: Request,
    *,
    stage: str,
    duration_ms: int,
    session_id: str | None = None,
    exam_id: str | None = None,
    question_id: str | None = None,
) -> None:
    """服务端自动埋点（source=server）：观测尽力而为，失败不破坏主流程。"""
    repo = request.app.state.voice_trace
    if repo is None:
        return
    try:
        await repo.record(
            stage=stage,
            duration_ms=duration_ms,
            source="server",
            session_id=session_id,
            exam_id=exam_id,
            question_id=question_id,
        )
    except Exception:  # noqa: BLE001, S110 —— 观测埋点尽力而为，绝不影响业务主流程
        pass


class TraceSpanRequest(BaseModel):
    stage: str
    duration_ms: int = Field(ge=1, le=MAX_DURATION_MS)
    session_id: str | None = None
    exam_id: str | None = None
    question_id: str | None = None


class TraceSpanOut(BaseModel):
    id: int
    stage: str
    duration_ms: int
    source: str
    session_id: str | None
    exam_id: str | None
    question_id: str | None
    created_at: str


class TraceSummaryOut(BaseModel):
    item_count: int  # 聚合窗口内的 span 总数
    stages: dict[str, dict]  # per-stage: count/avg_ms/p50_ms/p95_ms/max_ms


@router.post("/trace", response_model=TraceSpanOut, status_code=201)
async def report_trace_span(payload: TraceSpanRequest, request: Request) -> TraceSpanOut:
    """客户端上报耗时 span（source=client）：vad/llm/first_audio 等服务端测不到的环节。

    观测只记录不判定：上报不影响任何业务状态；
    stage 白名单校验（TRACE_STAGES），duration_ms 上限 10 分钟。
    """
    repo = request.app.state.voice_trace
    if repo is None:
        raise HTTPException(status_code=503, detail="Voice trace requires a database")
    if payload.stage not in TRACE_STAGES:
        raise HTTPException(status_code=422, detail=f"未知阶段: {payload.stage}，可选: {', '.join(TRACE_STAGES)}")
    view = await repo.record(
        stage=payload.stage,
        duration_ms=payload.duration_ms,
        source="client",
        session_id=payload.session_id,
        exam_id=payload.exam_id,
        question_id=payload.question_id,
    )
    return TraceSpanOut(**view)


@router.get("/trace/summary", response_model=TraceSummaryOut)
async def get_trace_summary(request: Request) -> TraceSummaryOut:
    """各阶段耗时聚合视图（per-stage count/avg/p50/p95/max）——M4-09 验收面。"""
    repo = request.app.state.voice_trace
    if repo is None:
        raise HTTPException(status_code=503, detail="Voice trace requires a database")
    spans = await repo.list_recent()
    return TraceSummaryOut(
        item_count=len(spans),
        stages=summarize_spans(spans),
    )


async def _load_paper(request: Request, exam_id: str):
    exam = await request.app.state.repository.get_exam(exam_id)
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")
    paper = await request.app.state.repository.get_paper(exam.paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="试卷不存在")
    return exam, paper


def _event_for(payload: VoiceCommandRequest) -> str:
    if payload.type == "answer_proposed":
        return fsm.EV_ANSWER_CLARIFY if payload.ambiguous else fsm.EV_ANSWER_PROPOSED
    return payload.type


@router.post("/sessions/{session_id}/commands", response_model=VoiceCommandOut)
async def apply_voice_command(
    session_id: str, payload: VoiceCommandRequest, request: Request
) -> VoiceCommandOut:
    """应用语音命令：FSM 校验迁移 + answer_proposed 服务端确定性落库。

    - 读题/读选项播报期收到 answer_proposed → 409 拒绝（打断不提交半成品答案）；
    - ANSWER_COMMITTED 后 answer_proposed = 覆盖提交（追加覆盖事件）；
    - 响应不含对错判定（考试模式不泄露答案）。
    """
    await _require_owned_session(request, session_id)
    sessions = request.app.state.voice_sessions
    if sessions is None:
        raise HTTPException(status_code=503, detail="Voice sessions require a database")
    view = await sessions.get(session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="语音会话不存在")

    event = _event_for(payload)
    if event not in fsm.EVENTS:
        raise HTTPException(status_code=422, detail=f"未知语音命令: {payload.type}")
    if event in (fsm.EV_ANSWER_PROPOSED, fsm.EV_ANSWER_CLARIFY) and not (payload.question_id and payload.answer):
        raise HTTPException(status_code=422, detail="answer_proposed 需要 question_id 与 answer")

    t0 = time.monotonic()
    try:
        target = fsm.transition(view["status"], event)
    except ValueError as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause

    clarified_question: str | None = None
    question_index: int | None = None
    question_total: int | None = None

    if event in (fsm.EV_ANSWER_PROPOSED, fsm.EV_ANSWER_CLARIFY):
        _, paper = await _load_paper(request, view["exam_id"])
        question_ids = [q.id for q in paper.questions]
        if payload.question_id not in question_ids:
            raise HTTPException(status_code=422, detail="题目不属于该试卷")
        if event == fsm.EV_ANSWER_PROPOSED:
            record = await request.app.state.repository.get_exam(view["exam_id"])
            assert record is not None
            sequence = record.events[-1].sequence + 1 if record.events else 1
            try:
                await request.app.state.repository.save_answer(
                    view["exam_id"], sequence, payload.question_id, payload.answer
                )
            except PermissionError as cause:
                raise HTTPException(status_code=409, detail="考试已结束，不能再修改答案") from cause
            except ValueError as cause:
                raise HTTPException(status_code=409, detail=str(cause)) from cause
        else:
            clarified_question = "没有听清，请再说一遍具体选项。"

    if event == fsm.EV_START_READING and view["status"] == fsm.NEXT_QUESTION:
        _, paper = await _load_paper(request, view["exam_id"])
        question_index = view["question_index"] + 1
        if question_index >= len(paper.questions):
            raise HTTPException(
                status_code=409,
                detail="已是最后一题，请用 report_ready 结束",
            )

    if event == fsm.EV_REPORT_READY:
        _, paper = await _load_paper(request, view["exam_id"])
        question_total = len(paper.questions)

    try:
        updated = await sessions.apply(
            session_id,
            new_status=target,
            question_index=question_index,
            expected_revision=payload.expected_revision,
        )
    except KeyError as cause:
        raise HTTPException(status_code=404, detail=str(cause)) from cause
    except ValueError as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    await _record_trace(
        request, stage="fsm", duration_ms=int((time.monotonic() - t0) * 1000),
        session_id=session_id, exam_id=view["exam_id"],
    )

    return VoiceCommandOut(
        applied_event=event,
        from_status=view["status"],
        session=VoiceSessionOut(**updated),
        clarified_question=clarified_question,
        question_total=question_total,
    )


# ---------- M4-04 Intent parser：transcript → FSM 命令一体化应用 ----------


class VoiceIntentRequest(BaseModel):
    transcript: str = Field(min_length=1, max_length=500)
    expected_revision: int | None = None


class VoiceIntentOut(BaseModel):
    transcript: str
    intent: str
    letter: str | None
    ordinal: int | None
    ambiguous: bool
    fsm_command: str | None
    fsm_applied: bool  # unknown/pause/resume 不应用 FSM——不虚报理解
    applied_event: str | None = None
    session: VoiceSessionOut | None = None
    clarified_question: str | None = None


@router.post("/sessions/{session_id}/intents", response_model=VoiceIntentOut)
async def apply_voice_intent(
    session_id: str, payload: VoiceIntentRequest, request: Request
) -> VoiceIntentOut:
    """解析语音转写并直接应用到语音会话 FSM（M4-04）。

    - choose/change：槽位规范化（序号→字母需当前题选项数），自动绑定 FSM 当前题；
    - 槽位含糊或序号超界 → answer_clarify（服务端澄清，不落库半成品）；
    - unknown 不改状态——解析失败≠澄清答案；pause/resume 待 M4-06 接入。
    """
    await _require_owned_session(request, session_id)
    sessions = request.app.state.voice_sessions
    if sessions is None:
        raise HTTPException(status_code=503, detail="Voice sessions require a database")
    view = await sessions.get(session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="语音会话不存在")

    t0 = time.monotonic()
    parsed = parse(payload.transcript)
    command = parsed.fsm_command
    await _record_trace(request, stage="intent", duration_ms=int((time.monotonic() - t0) * 1000), session_id=session_id, exam_id=view["exam_id"])
    if parsed.intent == INTENT_UNKNOWN or command is None:
        return VoiceIntentOut(
            transcript=payload.transcript,
            intent=parsed.intent,
            letter=parsed.letter,
            ordinal=parsed.ordinal,
            ambiguous=parsed.ambiguous,
            fsm_command=command,
            fsm_applied=False,
        )

    question_id: str | None = None
    answer: str | None = None
    ambiguous = parsed.ambiguous
    clarified_question: str | None = None

    if command == "answer_proposed":
        _, paper = await _load_paper(request, view["exam_id"])
        questions = list(paper.questions)
        index = view["question_index"]
        if index >= len(questions):
            raise HTTPException(status_code=409, detail="当前题索引越界")
        question_id = questions[index].id
        letter = parsed.letter
        if letter is None and parsed.ordinal is not None:
            option_count = len(getattr(questions[index], "options", []) or [])
            letter = ordinal_to_letter(parsed.ordinal, option_count) if option_count else None
            if letter is None:
                ambiguous = True  # 序号超界 → 澄清，不猜答案
        if letter is not None:
            answer = letter
        else:
            # 无法从槽位得到明确选项 → 澄清（含糊答案不落库）
            ambiguous = True
        if ambiguous:
            command = fsm.EV_ANSWER_CLARIFY
            answer = payload.transcript  # 满足命令校验；澄清分支不落库答案
            clarified_question = "没有听清，请再说一遍具体选项。"

    command_out = await apply_voice_command(
        session_id,
        VoiceCommandRequest(
            type=command,
            question_id=question_id,
            answer=answer,
            ambiguous=ambiguous,
            expected_revision=payload.expected_revision,
        ),
        request,
    )
    return VoiceIntentOut(
        transcript=payload.transcript,
        intent=parsed.intent,
        letter=parsed.letter,
        ordinal=parsed.ordinal,
        ambiguous=ambiguous,
        fsm_command=parsed.fsm_command,
        fsm_applied=True,
        applied_event=command_out.applied_event,
        session=command_out.session,
        clarified_question=clarified_question or command_out.clarified_question,
    )


# ---------- M4-05 Answer normalizer：/answers 规范化提交（04 文档契约） ----------


class VoiceAnswerRequest(BaseModel):
    transcript: str = Field(min_length=1, max_length=500)
    event_id: str = Field(min_length=4, max_length=64)  # 客户端幂等键（client-uuid）
    question_id: str | None = None  # 缺省绑定 FSM 当前题
    normalized_answer: str | None = None  # 客户端参考值——服务端不信任，重新规范化
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class VoiceAnswerOut(BaseModel):
    event_id: str
    idempotent: bool  # True=同 event_id 重放，返回既有结果（不重复落库）
    accepted: bool  # False=澄清/拒绝（不含答案）
    normalized_answer: str | None = None
    intent: str
    question_id: str
    session: VoiceSessionOut | None = None
    clarified_question: str | None = None


@router.post("/sessions/{session_id}/answers", response_model=VoiceAnswerOut)
async def submit_voice_answer(
    session_id: str, payload: VoiceAnswerRequest, request: Request
) -> VoiceAnswerOut:
    """语音答案规范化提交（M4-05）：只生成规范化答案事件，判定由服务端执行。

    - 服务端从 transcript 重新解析+规范化，不信任客户端 normalized_answer/confidence；
    - event_id 幂等：重放返回既有结果，绝不重复落库；
    - 规范化失败（选项不存在/题型不支持/含糊）→ 澄清，accepted=false 不含答案。
    """
    await _require_owned_session(request, session_id)
    sessions = request.app.state.voice_sessions
    answer_events = request.app.state.voice_answer_events
    if sessions is None or answer_events is None:
        raise HTTPException(status_code=503, detail="Voice answers require a database")
    view = await sessions.get(session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="语音会话不存在")

    # 幂等重放：同 event_id 直接返回既有结果（不改状态不重复提交）；
    # event_id 按会话域隔离——他session的事件不返回（防跨会话读取与响应投毒）
    existing = await answer_events.get(payload.event_id)
    if existing is not None:
        if existing["session_id"] != session_id:
            raise HTTPException(status_code=409, detail="event_id 已被其他会话使用")
        session_out = await sessions.get(session_id)
        return VoiceAnswerOut(
            event_id=payload.event_id,
            idempotent=True,
            accepted=existing["accepted"],
            normalized_answer=existing["normalized_answer"],
            intent=existing["intent"],
            question_id=existing["question_id"],
            session=VoiceSessionOut(**session_out) if session_out else None,
        )

    _, paper = await _load_paper(request, view["exam_id"])
    questions = list(paper.questions)
    index = view["question_index"]
    if payload.question_id is not None:
        if payload.question_id not in [q.id for q in questions]:
            raise HTTPException(status_code=422, detail="题目不属于该试卷")
        question = next(q for q in questions if q.id == payload.question_id)
    else:
        if index >= len(questions):
            raise HTTPException(status_code=409, detail="当前题索引越界")
        question = questions[index]

    parsed = parse(payload.transcript)
    normalized = normalize_answer(
        question,
        letter=parsed.letter,
        ordinal=parsed.ordinal,
        transcript=payload.transcript,
        intent=parsed.intent,
        confidence=payload.confidence,
    )

    clarified_question: str | None = None
    if normalized.is_valid:
        exam_record = await request.app.state.repository.get_exam(view["exam_id"])
        assert exam_record is not None
        sequence = exam_record.events[-1].sequence + 1 if exam_record.events else 1
        try:
            await request.app.state.repository.save_answer(
                view["exam_id"], sequence, question.id, normalized.answer
            )
        except PermissionError as cause:
            raise HTTPException(status_code=409, detail="考试已结束，不能再修改答案") from cause
        except ValueError as cause:
            raise HTTPException(status_code=409, detail=str(cause)) from cause
        event = fsm.EV_ANSWER_PROPOSED
    else:
        event = fsm.EV_ANSWER_CLARIFY  # 澄清分支不落库答案，只留 accepted=false 审计
        clarified_question = f"{normalized.reason}。"

    try:
        target = fsm.transition(view["status"], event)
    except ValueError as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    updated = await sessions.apply(
        session_id, new_status=target, expected_revision=None
    )
    try:
        await answer_events.record(
            event_id=payload.event_id,
            session_id=session_id,
            exam_id=view["exam_id"],
            question_id=question.id,
            normalized_answer=normalized.answer if normalized.is_valid else None,
            intent=parsed.intent,
            transcript=payload.transcript,
            confidence=payload.confidence,
            accepted=normalized.is_valid,
        )
    except ValueError as cause:  # 并发竞态兜底：event_id 被其他会话抢占
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    return VoiceAnswerOut(
        event_id=payload.event_id,
        idempotent=False,
        accepted=normalized.is_valid,
        normalized_answer=normalized.answer,
        intent=parsed.intent,
        question_id=question.id,
        session=VoiceSessionOut(**updated),
        clarified_question=clarified_question,
    )
