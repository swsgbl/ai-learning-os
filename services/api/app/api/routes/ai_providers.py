"""M15-008 AI 能力密钥治理（admin-only）：在线查看状态 / 填写 / 删除。

定位：AI 密钥（LLM / 云 ASR / 云 TTS）是服务端部署配置，非学习者功能。
安全边界：
- 密钥**永不回显全文**：状态视图只有 configured/source/尾 4 位/更新时间；
- 写入（PUT/DELETE）走 require_admin 门禁 + 审计（审计载荷只含脱敏
  before/after 状态，不含明文密钥或密文）；
- 删除 = 置空 DB 槽位，回退 env（部署事实仍在 env 层）；
- AUTH_SECRET 缺失 → 加密盒不可用 → 503 fail-closed（不降级明文）。
生效语义：
- asr_cloud / tts_cloud：voice 各端点按请求装配，写入即热生效；
- llm：rubric_judge 在应用启动时装配，写入后需重启 api 容器（状态页如实标注）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.routes.auth import build_audit_payload, require_admin
from app.core.config import get_settings
from app.core.secrets import SecretBox, SecretBoxError

router = APIRouter(prefix="/api/v1/admin/ai-providers", tags=["admin"])

# 槽位白名单：slot → (env 变量名, 生效语义)
_SLOTS: dict[str, tuple[str, str]] = {
    "llm": ("AIOS_LLM_API_KEY", "restart"),
    "asr_cloud": ("AIOS_ASR_CLOUD_API_KEY", "hot"),
    "tts_cloud": ("AIOS_TTS_CLOUD_API_KEY", "hot"),
}


class ProviderSlotOut(BaseModel):
    slot: str
    env_var: str
    configured: bool
    source: str | None = Field(description="db=在线填写（优先生效）/ env=部署注入 / null=未配置")
    last4: str | None = None
    updated_at: str | None = None
    effect: str = Field(description="hot=写入即生效 / restart=需重启 api 容器")


class AiProvidersOut(BaseModel):
    slots: list[ProviderSlotOut]


class ApiKeyIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=512, description="明文密钥——只进不出")


def _secret_box() -> SecretBox:
    try:
        return SecretBox(get_settings().auth_secret)
    except SecretBoxError as cause:
        raise HTTPException(status_code=503, detail=str(cause)) from cause


def _repo(request: Request):
    repo = getattr(request.app.state, "ai_provider_secrets", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Provider secrets require a database")
    return repo


@router.get("", response_model=AiProvidersOut)
async def list_ai_providers(request: Request) -> AiProvidersOut:
    await require_admin(request)
    repo = _repo(request)
    settings = get_settings()
    env_values = {
        "llm": settings.llm_api_key,
        "asr_cloud": settings.asr_cloud_api_key,
        "tts_cloud": settings.tts_cloud_api_key,
    }
    rows = {r.slot: r for r in await repo.list_all()}
    slots = []
    for slot, (env_var, effect) in _SLOTS.items():
        row = rows.get(slot)
        if row is not None:
            slots.append(
                ProviderSlotOut(
                    slot=slot,
                    env_var=env_var,
                    configured=True,
                    source="db",
                    last4=row.last4,
                    updated_at=row.updated_at.isoformat(),
                    effect=effect,
                )
            )
        elif env_values.get(slot):
            slots.append(
                ProviderSlotOut(slot=slot, env_var=env_var, configured=True, source="env", effect=effect)
            )
        else:
            slots.append(ProviderSlotOut(slot=slot, env_var=env_var, configured=False, source=None, effect=effect))
    return AiProvidersOut(slots=slots)


@router.put("/{slot}", response_model=ProviderSlotOut)
async def put_ai_provider_key(
    request: Request,
    slot: str,
    body: ApiKeyIn,
) -> ProviderSlotOut:
    await require_admin(request)
    if slot not in _SLOTS:
        raise HTTPException(status_code=422, detail=f"slot 必须是 {'/'.join(_SLOTS)} 之一")
    repo = _repo(request)
    box = _secret_box()
    env_var, effect = _SLOTS[slot]
    before = await repo.get(slot)
    record = await repo.upsert(
        slot, box.encrypt_to_b64(body.api_key), body.api_key[-4:]
    )
    # 审计只记脱敏状态（无明文/无密文）；写入失败随仓储异常整体失败
    payload = await build_audit_payload(
        request,
        action="ai_provider_key.set",
        target_type="ai_provider",
        target_id=slot,
        before={"source": "db" if before else None, "last4": before.last4 if before else None},
        after={"source": "db", "last4": record.last4},
    )
    if payload is not None:
        await request.app.state.audit.record(**payload)
    return ProviderSlotOut(
        slot=slot,
        env_var=env_var,
        configured=True,
        source="db",
        last4=record.last4,
        updated_at=record.updated_at.isoformat(),
        effect=effect,
    )


@router.delete("/{slot}", response_model=ProviderSlotOut)
async def delete_ai_provider_key(
    request: Request,
    slot: str,
) -> ProviderSlotOut:
    await require_admin(request)
    if slot not in _SLOTS:
        raise HTTPException(status_code=422, detail=f"slot 必须是 {'/'.join(_SLOTS)} 之一")
    repo = _repo(request)
    env_var, effect = _SLOTS[slot]
    settings = get_settings()
    env_value = {"llm": settings.llm_api_key, "asr_cloud": settings.asr_cloud_api_key, "tts_cloud": settings.tts_cloud_api_key}.get(slot)
    before = await repo.get(slot)
    deleted = await repo.clear(slot)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"slot {slot} 无在线密钥（DB 未配置）")
    payload = await build_audit_payload(
        request,
        action="ai_provider_key.clear",
        target_type="ai_provider",
        target_id=slot,
        before={"source": "db", "last4": before.last4 if before else None},
        after={"source": "env" if env_value else None, "last4": None},
    )
    if payload is not None:
        await request.app.state.audit.record(**payload)
    configured = bool(env_value)
    return ProviderSlotOut(
        slot=slot,
        env_var=env_var,
        configured=configured,
        source="env" if configured else None,
        effect=effect,
    )


async def resolve_provider_key(app, slot: str, user_id: str | None = None) -> str | None:
    """装配 resolver（M15-014 扩展个人优先维度）。

    优先级：个人密钥（BYOK，user_id 非空时）> 全局在线槽位 > 部署 env。
    其余语义（槽位白名单 fail-fast / 密文损坏回退 env）不变。
    """
    if user_id is not None:
        if slot == "llm":
            # M15-018：llm 槽位走多厂商 profile（active 条）语义
            from app.api.routes.me_ai_keys import resolve_user_llm_config

            personal = await resolve_user_llm_config(app, user_id)
            if personal is not None:
                return personal["api_key"]
        else:
            from app.api.routes.me_ai_keys import resolve_user_provider_key

            personal = await resolve_user_provider_key(app, slot, user_id)
            if personal is not None:
                return personal
    """装配 resolver：DB 在线密钥优先，回退 env（None=两端都未配置）。

    槽位白名单外的 slot 一律返回 env 语义不可达（编程错误，fail-fast）。
    密文不可解（AUTH_SECRET 变更）→ 抛 SecretBoxError，由调用方按该槽位
    未配置处理并记日志（fail-open 到 env——在线密钥已损坏时不应静默使用
    明文 env 之外的任何东西；env 回退是部署层的明确事实）。
    """
    if slot not in _SLOTS:
        raise ValueError(f"未知 provider 槽位: {slot}")
    repo = getattr(app.state, "ai_provider_secrets", None)
    if repo is not None:
        row = await repo.get(slot)
        if row is not None:
            try:
                box = SecretBox(get_settings().auth_secret)
            except SecretBoxError:
                return _env_fallback(slot)
            try:
                return box.decrypt_from_b64(row.ciphertext)
            except SecretBoxError:
                # 密文损坏：不静默弃用（审计面可见），回退 env
                return _env_fallback(slot)
    return _env_fallback(slot)


def _env_fallback(slot: str) -> str | None:
    settings = get_settings()
    return {
        "llm": settings.llm_api_key,
        "asr_cloud": settings.asr_cloud_api_key,
        "tts_cloud": settings.tts_cloud_api_key,
    }.get(slot)
