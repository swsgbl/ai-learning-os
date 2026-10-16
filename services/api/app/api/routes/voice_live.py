"""M14-272d Voice live 会话路由：服务端语音管线的 HTTP 面。

把 M14-272a/c 管线（``app.state.voice_pipeline``——仅语音部署
入口安装）暴露为**服务端权威**的 live 会话端点——open/事件/
VAD/close 全部只经服务端状态迁移，客户端只消费状态投影与
transcript（M14-271 契约）；订阅与转写在服务端内部发生：

- ``POST /voice/live-sessions/{session_id}/open`` {room}——打开
  会话（identity 服务端生成 ``server-sub-*``，绝不接受客户端
  指定；room 走既有公开边界校验）；同会话幂等（已存在 200）；
- ``GET  /voice/live-sessions/{session_id}``——组合状态投影
  （FSM state/revision + ASR 状态 + 通道计数）；
- ``POST /voice/live-sessions/{session_id}/events`` {event,
  revision?}——FSM 事件（revision 乐观并发：不匹配/非法迁移
  409——客户端刷新状态重试）；
- ``POST /voice/live-sessions/{session_id}/vad`` {signal}——VAD
  信号映射（M14-271：speech_start 播报期等价 barge_in）；
- ``GET  /voice/live-sessions/{session_id}/transcript``——服务端
  权威 transcript（M14-270：seq 单调有界净化）；
- ``DELETE /voice/live-sessions/{session_id}``——确定性关停
  （M14-272a 四段序：停帧源→消费残余→ASR 冲刷→终态审计面），
  响应携带 final transcript。

错误面：管线未安装（默认入口）503；未知/已终结会话 404；非法
迁移/revision 冲突/未知事件 409；非法 session_id/room 422（路由
参数与请求体模式校验）。全部响应无凭据/token（域层净化白名单
透传）。本切片不做后台 pump 循环（close 确定性冲刷已覆盖批式
转写路径）、不改 Web/Android/Harmony（三端逐步对齐此契约）。
"""
from __future__ import annotations

import asyncio
import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.domain.voice_pipeline import VoicePipelineError
from app.domain.voice_session_control import VoiceSessionControlError
from app.domain.voice_session_subscriptions import validate_session_id

router = APIRouter(prefix="/api/v1/voice/live-sessions", tags=["voice-live"])

_MESSAGE_PIPELINE_UNAVAILABLE = "Voice pipeline requires the voice entrypoint"
_MESSAGE_SESSION_NOT_FOUND = "live 会话不存在"

#: M14-276: 后台 pump 周期（秒）——帧通道 256 帧 ≈ 25.6s（10ms/帧），
#: 长语音作答需周期性 drain 到 FunAsrBatchAdapter 8 MiB 缓冲，否则
#: 通道溢出丢最旧音频导致 close 冲刷转写不完整。
PUMP_INTERVAL_SECONDS = 2.0


def _pump_registry(request: Request) -> dict:
    """per-app pump 任务注册表（app.state 惰性建——零模块级可变全局）。"""
    registry = getattr(request.app.state, "voice_pump_tasks", None)
    if registry is None:
        registry = {}
        request.app.state.voice_pump_tasks = registry
    return registry


async def _pump_loop(pipeline, session_id: str, interval: float) -> None:
    """周期 drain 通道 → ASR adapter 缓冲；会话终结（VoicePipelineError）
    自退出（不抛出——close/终结竞态下 pump 安静退场）。"""
    while True:
        await asyncio.sleep(interval)
        try:
            pipeline.pump_session(session_id, 256)
        except VoicePipelineError:
            return  # 会话未知/已终结——自清理


def _pump_interval(request: Request) -> float:
    """周期（测试可经 app.state.voice_pump_interval 覆盖；缺省生产值）。"""
    override = getattr(request.app.state, "voice_pump_interval", None)
    if isinstance(override, (int, float)) and not isinstance(override, bool) and override > 0:
        return float(override)
    return PUMP_INTERVAL_SECONDS


def _start_pump(request: Request, pipeline, session_id: str) -> None:
    registry = _pump_registry(request)
    existing = registry.get(session_id)
    if existing is not None and not existing.done():
        return  # 幂等：open 重入不双开
    registry[session_id] = asyncio.ensure_future(
        _pump_loop(pipeline, session_id, _pump_interval(request))
    )


async def _stop_pump(request: Request, session_id: str) -> None:
    registry = _pump_registry(request)
    task = registry.pop(session_id, None)
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class LiveOpenRequest(BaseModel):
    room: str = Field(pattern=r"^[A-Za-z0-9_-]{3,64}$")


class LiveEventRequest(BaseModel):
    event: str
    revision: int | None = None


class LiveVadRequest(BaseModel):
    signal: str


def _pipeline(request: Request):
    pipeline = getattr(request.app.state, "voice_pipeline", None)
    if pipeline is None:
        raise HTTPException(status_code=503, detail=_MESSAGE_PIPELINE_UNAVAILABLE)
    return pipeline


def _validate_session(session_id: str) -> str:
    try:
        return validate_session_id(session_id)
    except Exception:  # noqa: BLE001 —— 域校验白名单文案转 422
        raise HTTPException(
            status_code=422, detail="session_id 非法：仅 [A-Za-z0-9_-]，长度 3–64"
        ) from None


def _active_state(pipeline, session_id: str) -> dict:
    """活跃会话状态投影；未知/已终结（closed）→ 404。"""
    try:
        state = pipeline.session_state(session_id)
    except VoicePipelineError:
        raise HTTPException(status_code=404, detail=_MESSAGE_SESSION_NOT_FOUND) from None
    if state.get("closed"):
        raise HTTPException(status_code=404, detail=_MESSAGE_SESSION_NOT_FOUND)
    return state


@router.post("/{session_id}/open", status_code=201)
async def open_live_session(
    session_id: str, payload: LiveOpenRequest, request: Request, response: Response
) -> dict:
    """打开服务端 live 会话（identity 服务端生成；同会话幂等 200）。"""
    pipeline = _pipeline(request)
    canonical = _validate_session(session_id)
    if canonical in pipeline.snapshot()["session_ids"]:
        response.status_code = 200  # 幂等：已打开
    identity = _IDENTITY_PREFIX + secrets.token_hex(4)
    try:
        handle = await pipeline.open_session(
            canonical, room=payload.room, identity=identity
        )
    except VoicePipelineError:
        raise HTTPException(status_code=409, detail="live 会话打开失败（通用净化消息）") from None
    except Exception:  # noqa: BLE001 —— 工厂/订阅校验失败同 409（净化）
        raise HTTPException(status_code=409, detail="live 会话打开失败（通用净化消息）") from None
    # M14-276: 长会话防溢出——周期 drain 通道到 ASR adapter 缓冲
    _start_pump(request, pipeline, canonical)
    state = pipeline.session_state(canonical)
    # 幂等 open：既有句柄（同 identity 复用）→ 200 语义由投影携带
    return {
        "session_id": state["session_id"],
        "identity": handle.identity,
        "fsm_state": state["fsm_state"],
        "fsm_revision": state["fsm_revision"],
        "asr_status": state["asr_status"],
        "buffered_frames": state["buffered_frames"],
        "buffered_bytes": state["buffered_bytes"],
        "closed": state["closed"],
    }


#: 服务端订阅者身份前缀（与 voice_tokens new_identity 风格一致）。
_IDENTITY_PREFIX = "server-sub-"


@router.get("/{session_id}")
async def get_live_session(session_id: str, request: Request) -> dict:
    pipeline = _pipeline(request)
    canonical = _validate_session(session_id)
    return _active_state(pipeline, canonical)


@router.post("/{session_id}/events")
async def submit_live_event(session_id: str, payload: LiveEventRequest, request: Request) -> dict:
    pipeline = _pipeline(request)
    canonical = _validate_session(session_id)
    _active_state(pipeline, canonical)  # 未知/已终结 404
    try:
        result = pipeline.submit_session(
            canonical, payload.event, revision=payload.revision
        )
    except VoicePipelineError:
        raise HTTPException(status_code=404, detail=_MESSAGE_SESSION_NOT_FOUND) from None
    except VoiceSessionControlError:
        raise HTTPException(status_code=409, detail="事件被拒绝（迁移/revision 非法）") from None
    state = pipeline.session_state(canonical)
    state["applied"] = result.get("applied")
    return state


@router.post("/{session_id}/vad")
async def submit_live_vad(session_id: str, payload: LiveVadRequest, request: Request) -> dict:
    pipeline = _pipeline(request)
    canonical = _validate_session(session_id)
    _active_state(pipeline, canonical)  # 未知/已终结 404
    try:
        pipeline.submit_vad_session(canonical, payload.signal)
    except VoicePipelineError:
        raise HTTPException(status_code=404, detail=_MESSAGE_SESSION_NOT_FOUND) from None
    except VoiceSessionControlError:
        raise HTTPException(status_code=409, detail="VAD 信号被拒绝") from None
    return pipeline.session_state(canonical)


@router.get("/{session_id}/transcript")
async def get_live_transcript(session_id: str, request: Request) -> dict:
    pipeline = _pipeline(request)
    canonical = _validate_session(session_id)
    _active_state(pipeline, canonical)  # 未知/已终结 404
    return pipeline.final_transcript(canonical)


@router.delete("/{session_id}")
async def close_live_session(session_id: str, request: Request) -> dict:
    """确定性关停（四段序）；响应携带 final transcript（审计面）。

    幂等：已终结会话再次 DELETE 返回同一终态投影（不 404——
    DELETE 幂等语义）。
    """
    pipeline = _pipeline(request)
    canonical = _validate_session(session_id)
    try:
        before = pipeline.session_state(canonical)
    except VoicePipelineError:
        raise HTTPException(status_code=404, detail=_MESSAGE_SESSION_NOT_FOUND) from None
    await _stop_pump(request, canonical)  # 先停 pump 再关停（无搬运竞态）
    final_state = await pipeline.close_session(canonical)
    try:
        transcript = pipeline.final_transcript(canonical)
    except VoicePipelineError:  # pragma: no cover —— 句柄保留（审计面）
        transcript = {"session_id": canonical, "segments": [], "segment_count": 0}
    return {
        "session_id": canonical,
        "fsm_state": final_state.get("fsm_state", before.get("fsm_state")),
        "closed": True,
        "transcript": transcript,
    }
