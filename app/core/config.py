from decimal import Decimal
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

    # memguard_http (production) | nanomemory_http (direct, trusted single tenant) | local (demo)
    memory_backend: str = "local"
    nanomemory_base_url: str = "http://localhost:8101"
    nanomemory_api_key: str = ""
    nanomemory_context_token_budget: int = Field(default=8000, ge=256, le=100000)

    # mem-guard gateway: the only component allowed to assert tenant scope to NanoMemory.
    memguard_base_url: str = "http://localhost:8080"
    memguard_service_key: str = ""
    memguard_timeout_seconds: float = Field(default=8.0, gt=0, le=60)

    governance_backend: str = "local"
    mem_guard_policy_version: str = "omnicare-demo-1"

    n8n_webhook_url: str = ""
    n8n_webhook_timeout_seconds: float = Field(default=5.0, gt=0, le=30)

    demo_user_id: str = "demo_user_001"
    admin_api_key: str = ""

    # Deterministic 60-second demo (app/services/demo.py). ``DEMO_MODE=true`` forces it on
    # for the public compose sandbox; local/dev/test enable it automatically.
    demo_mode: bool = False
    demo_order_id: str = "ORD-001"
    demo_order_total: Decimal = Decimal("120000.00")
    demo_refund_amount: Decimal = Decimal("100000.00")
    demo_high_value_threshold: Decimal = Decimal("10000.00")
    demo_operator_id: str = "demo_operator"

    # Authentication. ``dev`` trusts X-User-Id and is refused when APP_ENV=production.
    # ``token`` is the legacy self-issued HMAC bearer mode (refused in production).
    # ``jwt``/``oidc`` verify OIDC-style JWTs from AUTH_SECRET or a public key/JWKS.
    auth_mode: str = "dev"
    auth_secret: str = ""
    auth_token_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    mcp_auth_token: str = ""  # deprecated: MCP moves to the same JWT as the API

    # JWT/OIDC verification. Tokens are the only source of identity in jwt/oidc modes.
    auth_jwt_issuer: str = ""
    auth_jwt_audience: str = ""
    auth_jwt_algorithm: str = "HS256"
    auth_jwt_public_key: str = ""
    auth_jwt_public_key_path: str = ""
    auth_jwks_url: str = ""
    auth_jwt_leeway_seconds: int = Field(default=30, ge=0, le=300)
    default_tenant_id: str = "default"
    # CSV allowlist; empty means "any tenant claim is accepted".
    auth_allowed_tenants: str = ""
    # CSV denylist of `jti` claim values (short-lived revocation list).
    auth_revoked_jtis: str = ""
    mcp_resource_url: str = ""
    # Scopes an MCP client token must carry (space/comma separated).
    mcp_required_scopes: str = "omnicare.read"
    log_level: str = "INFO"

    @property
    def demo_enabled(self) -> bool:
        """The demo is on when forced (public sandbox) or in the local/dev/test environments."""
        return self.demo_mode or (self.app_env.strip().lower() in {"local", "dev", "test"})

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def allowed_tenants(self) -> frozenset[str]:
        return frozenset(
            item.strip() for item in self.auth_allowed_tenants.split(",") if item.strip()
        )

    @property
    def revoked_jtis(self) -> frozenset[str]:
        return frozenset(item.strip() for item in self.auth_revoked_jtis.split(",") if item.strip())

    @property
    def required_mcp_scopes(self) -> tuple[str, ...]:
        return _split_scopes(self.mcp_required_scopes)


def _split_scopes(value: str) -> tuple[str, ...]:
    return tuple(item for item in value.replace(",", " ").split() if item)


@lru_cache
def get_settings() -> Settings:
    return Settings()
