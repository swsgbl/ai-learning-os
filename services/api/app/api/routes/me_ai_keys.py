"""M15-014 个人 AI 密钥（BYOK）：学习者自带 LLM/云 ASR/TTS 密钥。

定位（与 M15-008 全局治理页的差异）：
- 全局槽位（/api/v1/admin/ai-providers）：部署配置，admin 填，影响所有人；
- 个人密钥（本路由 /api/v1/me/ai-keys）：学习者自己填，只影响自己的
  请求装配。优先级：个人 > 全局在线槽位 > 部署 env。

安全边界与全局槽位同款：SecretBox 加密落库、明文/密文永不回显、
写入/删除走审计（载荷只含脱敏状态）。
生效语义：asr_cloud/tts_cloud 请求级装配立即生效；llm 槽位先保存
（判分器为 app 级装配，per-user 判分随下批惰性化改造生效）——响应
字段 effect 如实标注。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.routes.auth import build_audit_payload, current_owner_id, require_user
from app.core.config import get_settings
from app.core.secrets import SecretBox, SecretBoxError

router = APIRouter(prefix="/api/v1/me/ai-keys", tags=["me"])

# 槽位白名单（M15-015 收敛）：个人层只做主模型 LLM——本地语音已部署
# 无需个人云语音密钥（用户产品决策 2026-10-19）；全局层仍三槽位（治理页）。
_SLOTS: dict[str, str] = {
    "llm": "hot",
}


class UserAiKeyOut(BaseModel):
    slot: str
    configured: bool
    last4: str | None = None
    updated_at: str | None = None
    effect: str = Field(description="hot=判分请求即时生效")
    endpoint: str | None = None
    model: str | None = None


class UserAiKeysOut(BaseModel):
    keys: list[UserAiKeyOut]


class UserApiKeyIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=512)
    """可选路由（M15-015）：自带密钥通常指向自己的 OpenAI 兼容云服务；
    空 = 沿用服务端全局 LLM 网关配置（同网关不同 key 场景）。"""
    endpoint: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, max_length=128)


def _effect_label(slot: str) -> str:
    return "hot" if _SLOTS.get(slot) == "hot" else "saved"


def _repo(request: Request):
    repo = getattr(request.app.state, "user_ai_keys", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="User AI keys require a database")
    return repo


@router.get("", response_model=UserAiKeysOut)
async def list_my_ai_keys(request: Request) -> UserAiKeysOut:
    await require_user(request)
    repo = _repo(request)
    user_id = current_owner_id(request)
    rows = {r.slot: r for r in await repo.list_for_user(user_id)}
    return UserAiKeysOut(
        keys=[
            UserAiKeyOut(
                slot=slot,
                configured=slot in rows,
                last4=rows[slot].last4 if slot in rows else None,
                updated_at=rows[slot].updated_at.isoformat() if slot in rows else None,
                effect=_effect_label(slot),
                endpoint=rows[slot].endpoint if slot in rows else None,
                model=rows[slot].model if slot in rows else None,
            )
            for slot in _SLOTS
        ]
    )


@router.put("/{slot}", response_model=UserAiKeyOut)
async def put_my_ai_key(request: Request, slot: str, body: UserApiKeyIn) -> UserAiKeyOut:
    await require_user(request)
    if slot not in _SLOTS:
        raise HTTPException(status_code=422, detail=f"slot 必须是 {'/'.join(_SLOTS)} 之一")
    repo = _repo(request)
    try:
        box = SecretBox(get_settings().auth_secret)
    except SecretBoxError as cause:
        raise HTTPException(status_code=503, detail=str(cause)) from cause
    user_id = current_owner_id(request)
    before = await repo.get(user_id, slot)
    endpoint = (body.endpoint or "").strip() or None
    model = (body.model or "").strip() or None
    record = await repo.upsert(
        user_id,
        slot,
        box.encrypt_to_b64(body.api_key),
        body.api_key[-4:],
        endpoint=endpoint,
        model=model,
    )
    payload = await build_audit_payload(
        request,
        action="user_ai_key.set",
        target_type="user_ai_key",
        target_id=f"{user_id}:{slot}",
        before={"last4": before.last4} if before else None,
        after={"last4": record.last4},
    )
    if payload is not None:
        await request.app.state.audit.record(**payload)
    return UserAiKeyOut(
        slot=slot,
        configured=True,
        last4=record.last4,
        updated_at=record.updated_at.isoformat(),
        effect=_effect_label(slot),
        endpoint=record.endpoint,
        model=record.model,
    )


@router.delete("/{slot}", response_model=UserAiKeyOut)
async def delete_my_ai_key(request: Request, slot: str) -> UserAiKeyOut:
    await require_user(request)
    if slot not in _SLOTS:
        raise HTTPException(status_code=422, detail=f"slot 必须是 {'/'.join(_SLOTS)} 之一")
    repo = _repo(request)
    user_id = current_owner_id(request)
    before = await repo.get(user_id, slot)
    deleted = await repo.clear(user_id, slot)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"slot {slot} 无个人密钥")
    payload = await build_audit_payload(
        request,
        action="user_ai_key.clear",
        target_type="user_ai_key",
        target_id=f"{user_id}:{slot}",
        before={"last4": before.last4} if before else None,
        after=None,
    )
    if payload is not None:
        await request.app.state.audit.record(**payload)
    return UserAiKeyOut(slot=slot, configured=False, effect=_effect_label(slot))


async def resolve_user_llm_config(app, user_id: str | None) -> dict | None:
    """个人 LLM 判分配置：{api_key, endpoint, model}——endpoint/model 空值
    由调用方回落服务端全局配置。无个人密钥/密文损坏 → None（用全局）。"""
    if user_id is None:
        return None
    repo = getattr(app.state, "user_ai_keys", None)
    if repo is None:
        return None
    row = await repo.get(user_id, "llm")
    if row is None:
        return None
    try:
        box = SecretBox(get_settings().auth_secret)
        return {
            "api_key": box.decrypt_from_b64(row.ciphertext),
            "endpoint": row.endpoint,
            "model": row.model,
        }
    except SecretBoxError:
        return None


async def resolve_user_provider_key(app, slot: str, user_id: str | None) -> str | None:
    """个人密钥解析：该用户该槽位有个人密钥则解密返回，否则 None（调用方
    回退全局 resolver）。密文损坏 fail-open 到 None（不阻断请求）。"""
    if user_id is None or slot not in _SLOTS:
        return None
    repo = getattr(app.state, "user_ai_keys", None)
    if repo is None:
        return None
    row = await repo.get(user_id, slot)
    if row is None:
        return None
    try:
        box = SecretBox(get_settings().auth_secret)
        return box.decrypt_from_b64(row.ciphertext)
    except SecretBoxError:
        return None
