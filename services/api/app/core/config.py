from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PrivacyMode = Literal["local", "cloud", "hybrid"]
CookieSameSite = Literal["lax", "strict", "none"]


class Settings(BaseSettings):
    app_env: str = "development"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    database_url: str | None = None
    redis_url: str | None = None

    # 隐私路由（runbook 3 节）：local/cloud/hybrid，非法值拒绝启动
    model_route: PrivacyMode = "hybrid"
    voice_mode: PrivacyMode = "local"
    search_mode: PrivacyMode = "local"
    privacy_store_audio: bool = False
    privacy_send_context_to_cloud: bool = True

    # 接入点与选型记录；真实凭据只放部署 secret 或本机 .env，不入库
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    livekit_url: str | None = None
    # M9-08: 浏览器可达的 LiveKit 地址（局域网/公开模式必配，如 ws://<LAN_IP>:7880）；
    # 未配置时 token 回退 livekit_url（容器内部地址仅容器内可用）
    public_livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None
    asr_provider: str | None = None
    tts_provider: str | None = None
    # M4-02 云端语音槽位：OpenAI 兼容端点；key 只放部署 secret/.env，不入库不入码
    asr_cloud_endpoint: str | None = None
    asr_cloud_api_key: str | None = None
    asr_cloud_model: str = "whisper-1"
    tts_cloud_endpoint: str | None = None
    tts_cloud_api_key: str | None = None
    tts_cloud_model: str = "tts-1"
    llm_provider: str | None = None
    # M10-01 LLM 槽位：OpenAI 兼容端点；key 只放部署 secret/.env，不入库不入码
    llm_endpoint: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    embedding_provider: str | None = None
    search_provider: str | None = None
    search_cloud_endpoint: str | None = None  # M5-01 cloud-web 搜索源
    search_cloud_api_key: str | None = None  # 真实 key 不入库，env 注入
    fetch_rate_limit_per_minute: int = 30  # M5-03 抓取预检频率上限（per-IP 固定窗口）

    # M2-10 主观题判分：keyword=内置确定性 judge；空=无 judge（essay 全部进复核）
    rubric_judge: str = "keyword"

    # M9-01 认证：AUTH_SECRET 未配置 = 认证关闭（status 如实透出，不虚报受保护）；
    # 生产 compose 显式注入。key 只放部署 secret/.env，不入库不入码。
    auth_secret: str | None = None
    auth_token_expire_minutes: int = 1440
    # M10-03 Web 认证 cookie：login 设置 HttpOnly cookie（页面 JS 不再读取/
    # 保存 token；body token 仍供 CLI/API）；SameSite=Lax 默认（跨站 POST
    # 不携带 cookie = CSRF 边界）；HTTPS 部署设 AUTH_COOKIE_SECURE=true
    auth_cookie_name: str = "aios_auth"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: CookieSameSite = "lax"
    # M9-06: 宿主端口绑定意图（compose 透传 AIOS_BIND_IP）——非 loopback 时启动校验升级
    host_bind_ip: str = "127.0.0.1"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @field_validator("auth_cookie_name")
    @classmethod
    def validate_auth_cookie_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("auth_cookie_name must not be blank")
        return value.strip()

    @field_validator("cors_origins")
    @classmethod
    def reject_wildcard_cors_origins(cls, value: str) -> str:
        # M10-03: CORS 开启 allow_credentials 后，"*" 会被 Starlette 反射成
        # 任意 Origin + 凭据放行（任意站点可携带登录 cookie 跨域读）。
        # 与公开暴露 fail-closed 同款语义：源必须是显式 allowlist。
        if any("*" in entry for entry in value.split(",")):
            raise ValueError(
                "CORS_ORIGINS 不允许通配符 *（allow_credentials 已启用，必须显式列出 Web origin）"
            )
        return value

    @model_validator(mode="after")
    def validate_auth_cookie(self) -> "Settings":
        if self.auth_cookie_samesite == "none" and not self.auth_cookie_secure:
            raise ValueError("AUTH_COOKIE_SAMESITE=none requires AUTH_COOKIE_SECURE=true")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
