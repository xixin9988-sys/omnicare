from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "OmniCare"
    app_env: str = "local"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite+aiosqlite:///./data/omnicare.db"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    llm_provider: str = "mock"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=120)

    memory_backend: str = "local"
    nanomemory_base_url: str = "http://localhost:8101"
    nanomemory_api_key: str = ""
    nanomemory_context_token_budget: int = Field(default=8000, ge=256, le=100000)

    governance_backend: str = "local"
    mem_guard_policy_version: str = "omnicare-demo-1"

    n8n_webhook_url: str = ""
    n8n_webhook_timeout_seconds: float = Field(default=5.0, gt=0, le=30)

    demo_user_id: str = "demo_user_001"
    admin_api_key: str = ""
    log_level: str = "INFO"

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
