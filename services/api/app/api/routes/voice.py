"""M4-01/M4-02 Voice API：房间 token 签发 + ASR/TTS adapter 端点。

- POST /token：M4-01 房间 JWT（过期和权限边界可测试）。
- GET  /providers：M4-02 provider 配置视图（VOICE_MODE 三模式切换结果，无 DB 依赖）。
- POST /transcribe：语音→文本；transcript 恒落盘，原始音频按
  PRIVACY_STORE_AUDIO 策略落对象存储或显式丢弃（runbook §3）。
- POST /synthesize：文本→WAV 音频（tone 本地合成或云端 provider）。
"""
from __future__ import annotations

import hashlib
import re

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.domain import voice_session_fsm as fsm
from app.domain.answer_normalizer import normalize_answer
from app.domain.intent_parser import INTENT_UNKNOWN, ordinal_to_letter, parse
from app.domain.models import ExamStatus
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
from app.voice.providers import (
    LOCAL_ASR,
    LOCAL_TTS,
    CloudOpenAiAsrProvider,
    CloudOpenAiTtsProvider,
    FakeAsrProvider,
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
        ws_url=settings.livekit_url or "ws://127.0.0.1:7880",
    )


def _voice_mode(settings) -> str:
    mode = settings.voice_mode
    if mode not in VOICE_MODES:
        raise HTTPException(status_code=422, detail=f"非法 VOICE_MODE: {mode}")
    return mode


@router.get("/providers", response_model=ProvidersOut)
async def list_providers() -> ProvidersOut:
    """provider 配置视图：当前 VOICE_MODE 下 ASR/TTS 的实际选择。"""
    settings = get_settings()
    mode = _voice_mode(settings)
    asr_ready = bool(settings.asr_cloud_endpoint and settings.asr_cloud_api_key)
    tts_ready = bool(settings.tts_cloud_endpoint and settings.tts_cloud_api_key)
    asr = resolve_asr(mode, settings.asr_provider, asr_ready)
    tts = resolve_tts(mode, settings.tts_provider, tts_ready)
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
    if choice_provider == "cloud-openai-tts":
        return CloudOpenAiTtsProvider(
            settings.tts_cloud_endpoint or "",
            settings.tts_cloud_api_key or "",
            settings.tts_cloud_model,
        )
    raise HTTPException(status_code=422, detail=f"未知 TTS provider: {choice_provider}")


@router.post("/transcribe", response_model=TranscriptionOut)
async def transcribe(request: Request, audio: UploadFile) -> TranscriptionOut:
    """语音→文本：transcript 恒落盘；原始音频按 PRIVACY_STORE_AUDIO 策略处理。"""
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

    choice = resolve_asr(mode, settings.asr_provider, bool(settings.asr_cloud_endpoint and settings.asr_cloud_api_key))
    provider = _build_asr(settings, choice.provider)
    try:
        result = await provider.transcribe(data, content_type=audio.content_type or "audio/wav")
    except ProviderUnavailable as cause:
        raise HTTPException(status_code=502, detail=str(cause)) from cause

    audio_object_key: str | None = None
    audio_stored = False
    if settings.privacy_store_audio:
        digest = hashlib.sha256(data).hexdigest()
        audio_object_key = f"voice/{digest[:2]}/{digest}"
        request.app.state.objects.put(audio_object_key, data, audio.content_type or "audio/wav")
        audio_stored = True
    view = await repo.save(
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
    rows = await repo.list_recent(limit=limit)
    return TranscriptsOut(
        item_count=len(rows),
        items=[TranscriptView(**row) for row in rows],
    )


@router.post("/synthesize")
async def synthesize(payload: SynthesizeRequest) -> Response:
    """文本→WAV。合成不落库（派生音频无留存需求），provider 透出响应头。"""
    settings = get_settings()
    mode = _voice_mode(settings)
    choice = resolve_tts(mode, settings.tts_provider, bool(settings.tts_cloud_endpoint and settings.tts_cloud_api_key))
    provider = _build_tts(settings, choice.provider)
    try:
        result = await provider.synthesize(payload.text)
    except ProviderUnavailable as cause:
        raise HTTPException(status_code=502, detail=str(cause)) from cause
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


@router.post("/sessions", response_model=VoiceSessionOut, status_code=201)
async def create_voice_session(payload: VoiceSessionCreateRequest, request: Request) -> VoiceSessionOut:
    """为进行中的考试创建语音会话（SESSION_READY）。"""
    sessions = request.app.state.voice_sessions
    if sessions is None:
        raise HTTPException(status_code=503, detail="Voice sessions require a database")
    exam = await request.app.state.repository.get_exam(payload.exam_id)
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")
    if exam.status != ExamStatus.ACTIVE:
        raise HTTPException(status_code=409, detail=f"考试状态 {exam.status.value} 不允许语音作答")
    view = await sessions.create(payload.exam_id)
    return VoiceSessionOut(**view)


@router.get("/sessions/{session_id}", response_model=VoiceSessionOut)
async def get_voice_session(session_id: str, request: Request) -> VoiceSessionOut:
    sessions = request.app.state.voice_sessions
    if sessions is None:
        raise HTTPException(status_code=503, detail="Voice sessions require a database")
    view = await sessions.get(session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="语音会话不存在")
    return VoiceSessionOut(**view)


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
    sessions = request.app.state.voice_sessions
    if sessions is None:
        raise HTTPException(status_code=503, detail="Voice sessions require a database")
    view = await sessions.get(session_id)
    if view is None:
        raise HTTPException(status_code=404, detail="语音会话不存在")

    parsed = parse(payload.transcript)
    command = parsed.fsm_command
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
