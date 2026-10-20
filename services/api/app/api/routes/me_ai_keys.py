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


class LlmProfileOut(BaseModel):
    id: str
    label: str
    last4: str
    endpoint: str | None = None
    model: str | None = None
    active: bool


class UserAiKeysOut(BaseModel):
    keys: list[UserAiKeyOut]
    profiles: list[LlmProfileOut] = []


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
    """M15-018 多厂商密钥库：profiles 列表 + 兼容字段（keys.llm 汇总激活条）。"""
    await require_user(request)
    user_id = current_owner_id(request)
    profiles_repo = getattr(request.app.state, "llm_profiles", None)
    profiles = (
        [
            LlmProfileOut(
                id=p.id,
                label=p.label,
                last4=p.last4,
                endpoint=p.endpoint,
                model=p.model,
                active=p.is_active,
            )
            for p in await profiles_repo.list_for_user(user_id)
        ]
        if profiles_repo is not None
        else []
    )
    active = next((p for p in profiles if p.active), None)
    return UserAiKeysOut(
        keys=[
            UserAiKeyOut(
                slot="llm",
                configured=active is not None,
                last4=active.last4 if active else None,
                updated_at=None,
                effect="hot" if active else "none",
                endpoint=active.endpoint if active else None,
                model=active.model if active else None,
            )
        ],
        profiles=profiles,
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
    if endpoint is not None:
        try:
            endpoint = normalize_personal_endpoint(endpoint)
        except PersonalEndpointRejected as cause:
            raise HTTPException(status_code=422, detail=str(cause)) from cause
    # M15-018：单槽 PUT 兼容 = 覆盖当前激活 profile（无则新建「默认」）
    profiles_repo = getattr(request.app.state, "llm_profiles", None)
    if profiles_repo is not None:
        existing = next(
            (p for p in await profiles_repo.list_for_user(user_id) if p.is_active), None
        )
        if existing is not None:
            record = await profiles_repo.update_fields(
                existing.id, user_id,
                ciphertext=box.encrypt_to_b64(body.api_key),
                last4=body.api_key[-4:],
                endpoint=endpoint, model=model,
            )
        else:
            record = await profiles_repo.create(
                user_id, "默认", box.encrypt_to_b64(body.api_key), body.api_key[-4:],
                endpoint=endpoint, model=model, activate=True,
            )
        payload = await build_audit_payload(
            request, action="user_ai_key.set", target_type="user_ai_key",
            target_id=f"{user_id}:llm",
            before=None, after={"last4": record.last4},
        )
        if payload is not None:
            await request.app.state.audit.record(**payload)
        return UserAiKeyOut(
            slot="llm", configured=True, last4=record.last4, updated_at=None,
            effect="hot", endpoint=record.endpoint, model=record.model,
        )
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
    """M15-018：单槽删除兼容 = 删除当前激活 profile（剩余最早条自动顶上）。"""
    await require_user(request)
    if slot not in _SLOTS:
        raise HTTPException(status_code=422, detail=f"slot 必须是 {'/'.join(_SLOTS)} 之一")
    user_id = current_owner_id(request)
    profiles_repo = getattr(request.app.state, "llm_profiles", None)
    if profiles_repo is not None:
        active = next(
            (p for p in await profiles_repo.list_for_user(user_id) if p.is_active), None
        )
        if active is None:
            raise HTTPException(status_code=404, detail=f"slot {slot} 无个人密钥")
        await profiles_repo.delete(active.id, user_id)
        payload = await build_audit_payload(
            request,
            action="user_ai_key.clear",
            target_type="user_ai_key",
            target_id=f"{user_id}:{slot}",
            before={"last4": active.last4},
            after=None,
        )
        if payload is not None:
            await request.app.state.audit.record(**payload)
        return UserAiKeyOut(slot=slot, configured=False, effect=_effect_label(slot))
    repo = _repo(request)
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



class LlmModelsProbeIn(BaseModel):
    """模型列表探测：密钥/接口地址直接传当前输入值（未保存前即可探测）；
    省略则用已保存的个人配置。密钥只在本次请求内使用，不落库。"""

    api_key: str | None = Field(default=None, min_length=8, max_length=512)
    endpoint: str | None = Field(default=None, max_length=512)


class LlmModelsProbeOut(BaseModel):
    models: list[str]


@router.post("/llm/models", response_model=LlmModelsProbeOut)
async def probe_my_llm_models(request: Request, body: LlmModelsProbeIn) -> LlmModelsProbeOut:
    """列出用户自带 LLM 服务的模型（OpenAI 兼容 GET {endpoint}/models）。

    服务端代理探测（App 不直连）：endpoint 经同款 SSRF 校验/规范化；
    失败如实透传（密钥错 401/地址不可达等 → 4xx + 脱敏 detail）。
    """
    import httpx

    await require_user(request)
    user_id = current_owner_id(request)
    endpoint = body.endpoint
    api_key = body.api_key
    if api_key is None or endpoint is None:
        saved = await resolve_user_llm_config(request.app, user_id)
        if saved is None:
            raise HTTPException(status_code=422, detail="请先填写密钥与接口地址（或先保存）")
        api_key = api_key or saved["api_key"]
        endpoint = endpoint or saved["endpoint"] or get_settings().llm_endpoint
    if not endpoint:
        raise HTTPException(status_code=422, detail="缺少接口地址")
    try:
        base = normalize_personal_endpoint(endpoint)
    except PersonalEndpointRejected as cause:
        raise HTTPException(status_code=422, detail=str(cause)) from cause
    url = f"{base.rstrip('/')}/models"
    try:
        import os

        async with httpx.AsyncClient(
            timeout=8.0,
            follow_redirects=False,
            # M15-019c/d：强制 IPv4 + 可选出网代理（同 LlmGateway）
            transport=httpx.AsyncHTTPTransport(
                local_address="0.0.0.0",
                proxy=os.environ.get("LLM_OUTBOUND_PROXY") or None,
            ),
        ) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as cause:
        raise HTTPException(status_code=502, detail="接口地址不可达") from cause
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="密钥无效（服务返回 401）")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"模型列表接口返回 {resp.status_code}")
    try:
        data = resp.json().get("data") or []
        items = [
            (str(item["id"]), item.get("created"))
            for item in data
            if isinstance(item, dict) and item.get("id")
        ]
    except Exception as cause:
        raise HTTPException(status_code=502, detail="响应不是 OpenAI 兼容的模型列表格式") from cause
    # M15-016b：最新模型优先——带 created 时间戳的平台按时间降序；
    # 不带的保持平台返回序（主流平台本身最新在前）。保持首见去重。
    seen: set[str] = set()
    if items and all(created is not None for _, created in items):
        items.sort(key=lambda pair: pair[1] or 0, reverse=True)
    models: list[str] = []
    for model_id, _ in items:
        if model_id not in seen:
            seen.add(model_id)
            models.append(model_id)
    return LlmModelsProbeOut(models=models[:100])


class ProfileCreateIn(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    api_key: str = Field(min_length=8, max_length=512)
    endpoint: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, max_length=128)
    activate: bool = False


class ProfileUpdateIn(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=64)
    api_key: str | None = Field(default=None, min_length=8, max_length=512)
    endpoint: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, max_length=128)


def _profiles_repo(request: Request):
    repo = getattr(request.app.state, "llm_profiles", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="LLM profiles require a database")
    return repo


def _normalized_optional(body_endpoint: str | None, body_model: str | None) -> tuple[str | None, str | None]:
    endpoint = (body_endpoint or "").strip() or None
    model = (body_model or "").strip() or None
    if endpoint is not None:
        try:
            endpoint = normalize_personal_endpoint(endpoint)
        except PersonalEndpointRejected as cause:
            raise HTTPException(status_code=422, detail=str(cause)) from cause
    return endpoint, model


@router.post("/llm/profiles", response_model=LlmProfileOut, status_code=201)
async def create_llm_profile(request: Request, body: ProfileCreateIn) -> LlmProfileOut:
    """M15-018 新增厂商配置（首条自动激活；activate=true 显式切换）。"""
    await require_user(request)
    try:
        box = SecretBox(get_settings().auth_secret)
    except SecretBoxError as cause:
        raise HTTPException(status_code=503, detail=str(cause)) from cause
    endpoint, model = _normalized_optional(body.endpoint, body.model)
    user_id = current_owner_id(request)
    try:
        p = await _profiles_repo(request).create(
            user_id, body.label.strip(), box.encrypt_to_b64(body.api_key),
            body.api_key[-4:], endpoint=endpoint, model=model, activate=body.activate,
        )
    except ValueError as cause:
        raise HTTPException(status_code=409, detail=str(cause)) from cause
    payload = await build_audit_payload(
        request, action="llm_profile.create", target_type="llm_profile",
        target_id=f"{user_id}:{p.id}", before=None,
        after={"label": p.label, "last4": p.last4, "active": p.is_active},
    )
    if payload is not None:
        await request.app.state.audit.record(**payload)
    return LlmProfileOut(id=p.id, label=p.label, last4=p.last4, endpoint=p.endpoint, model=p.model, active=p.is_active)


@router.put("/llm/profiles/{profile_id}", response_model=LlmProfileOut)
async def update_llm_profile(request: Request, profile_id: str, body: ProfileUpdateIn) -> LlmProfileOut:
    await require_user(request)
    profiles = _profiles_repo(request)
    user_id = current_owner_id(request)
    existing = await profiles.get(profile_id, user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="厂商配置不存在")
    endpoint, model = _normalized_optional(body.endpoint, body.model)
    ciphertext = None
    last4 = None
    if body.api_key is not None:
        try:
            box = SecretBox(get_settings().auth_secret)
        except SecretBoxError as cause:
            raise HTTPException(status_code=503, detail=str(cause)) from cause
        ciphertext = box.encrypt_to_b64(body.api_key)
        last4 = body.api_key[-4:]
    p = await profiles.update_fields(
        profile_id, user_id, label=body.label.strip() if body.label else None,
        ciphertext=ciphertext, last4=last4, endpoint=endpoint, model=model,
    )
    return LlmProfileOut(id=p.id, label=p.label, last4=p.last4, endpoint=p.endpoint, model=p.model, active=p.is_active)


@router.post("/llm/profiles/{profile_id}/activate", response_model=LlmProfileOut)
async def activate_llm_profile(request: Request, profile_id: str) -> LlmProfileOut:
    """M15-018 切换当前生效厂商（判分即时走它）。"""
    await require_user(request)
    user_id = current_owner_id(request)
    p = await _profiles_repo(request).activate(profile_id, user_id)
    if p is None:
        raise HTTPException(status_code=404, detail="厂商配置不存在")
    payload = await build_audit_payload(
        request, action="llm_profile.activate", target_type="llm_profile",
        target_id=f"{user_id}:{p.id}", before=None, after={"label": p.label},
    )
    if payload is not None:
        await request.app.state.audit.record(**payload)
    return LlmProfileOut(id=p.id, label=p.label, last4=p.last4, endpoint=p.endpoint, model=p.model, active=True)


@router.delete("/llm/profiles/{profile_id}")
async def delete_llm_profile(request: Request, profile_id: str) -> dict:
    await require_user(request)
    user_id = current_owner_id(request)
    deleted = await _profiles_repo(request).delete(profile_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="厂商配置不存在")
    payload = await build_audit_payload(
        request, action="llm_profile.delete", target_type="llm_profile",
        target_id=f"{user_id}:{profile_id}", before=None, after=None,
    )
    if payload is not None:
        await request.app.state.audit.record(**payload)
    return {"deleted": profile_id}


async def resolve_user_llm_config(app, user_id: str | None) -> dict | None:
    """个人 LLM 判分配置（M15-018：取 active profile）——空值回落全局；
    无 profile/密文损坏 → None（用全局）。"""
    if user_id is None:
        return None
    profiles = getattr(app.state, "llm_profiles", None)
    if profiles is not None:
        active = next(
            (p for p in await profiles.list_for_user(user_id) if p.is_active), None
        )
        if active is None:
            return None
        try:
            box = SecretBox(get_settings().auth_secret)
            return {
                "api_key": box.decrypt_from_b64(active.ciphertext),
                "endpoint": active.endpoint,
                "model": active.model,
            }
        except SecretBoxError:
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


class PersonalEndpointRejected(ValueError):
    """个人 LLM endpoint 校验失败（SSRF 防线——领域级异常，web 层转 422）。"""


def normalize_personal_endpoint(endpoint: str) -> str:
    """SSRF 缓解（M15-015 安全修复）：个人 LLM endpoint 是任一登录用户
    可填、且服务端会主动向其发请求的 URL——必须拒绝内网/环回/链路本
    地/云元数据目标。非法即 422（不落库）。解析失败同样拒绝。
    """
    import ipaddress
    from urllib.parse import urlparse

    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise PersonalEndpointRejected("接口地址必须是 http/https URL")
    host = parsed.hostname
    # 恒内网域名黑名单（localhost 及容器语境里的宿主别名——解析必为环回/网关）
    if host.lower() in {"localhost", "host.docker.internal", "gateway.docker.internal"} or host.lower().endswith(".local"):
        raise PersonalEndpointRejected("接口地址不允许指向本机/内网域名")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None  # 其余公有域名：DNS 在请求时解析（残余面=DNS 重绑定，见注释）
    if addr is not None and (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or addr.is_reserved
    ):
        raise PersonalEndpointRejected("接口地址不允许指向内网/保留地址")
    # parser-differential 缓解：返回校验器重建的规范形态
    # （scheme://hostname[:port]path），丢弃 userinfo/query/fragment——
    # 实际请求使用该重建串，校验对象与请求对象严格一致。
    host_part = parsed.hostname.lower()
    if parsed.port is not None:
        host_part = f"{host_part}:{parsed.port}"
    return f"{parsed.scheme}://{host_part}{parsed.path or ''}"
