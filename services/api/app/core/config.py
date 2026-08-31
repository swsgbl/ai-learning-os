from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PrivacyMode = Literal["local", "cloud", "hybrid"]


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
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None
    asr_provider: str | None = None
    tts_provider: str | None = None
    llm_provider: str | None = None
    embedding_provider: str | None = None
    search_provider: str | None = None

    # M2-10 主观题判分：keyword=内置确定性 judge；空=无 judge（essay 全部进复核）
    rubric_judge: str = "keyword"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
