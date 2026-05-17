"""Application configuration via pydantic-settings.

All runtime configuration is loaded from environment variables (or a local
``.env`` file in development). Secret values use :class:`pydantic.SecretStr` so
they never appear in repr/log output by accident.

Usage::

    from backend.config import get_settings

    settings = get_settings()
    db_url = settings.database_url

``get_settings`` is cached so the Settings object is built exactly once per
process and can be passed as a FastAPI dependency.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    """Deployment environment name."""

    development = "development"
    staging = "staging"
    production = "production"


LogLevel = Literal["debug", "info", "warning", "error", "critical"]


class Settings(BaseSettings):
    """Runtime configuration loaded from environment / ``.env``.

    Field names are case-insensitive against env vars by default
    (pydantic-settings behavior). Use ``settings.field`` to read values from
    code instead of touching ``os.environ`` directly.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Allow values like "  development  " in .env.
        str_strip_whitespace=True,
    )

    # ── 1. App ──
    app_env: AppEnv = AppEnv.development
    frontend_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:3000"),
        description="Origin allowed by CORS middleware.",
    )
    log_level: LogLevel = "info"

    # ── 2. Supabase API keys ──
    supabase_url: AnyHttpUrl
    supabase_project_ref: str | None = None

    # Legacy keys (still issued for projects created before Nov 2025).
    supabase_anon_key: SecretStr | None = None
    supabase_service_role_key: SecretStr | None = None

    # New keys (preferred). When set they take precedence over the legacy pair.
    supabase_publishable_key: SecretStr | None = None
    supabase_secret_key: SecretStr | None = None

    # Used by the JWT verification middleware.
    supabase_jwt_secret: SecretStr

    # ── 3. Database (raw asyncpg for SQLAlchemy) ──
    database_url: SecretStr = Field(
        description=(
            "SQLAlchemy URL using the asyncpg driver. "
            "Format: postgresql+asyncpg://user:password@host:port/db"
        ),
    )

    # ── 4. Supabase Storage ──
    supabase_storage_bucket: str = "ad-images"

    # ── 5. AI providers ──
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    replicate_api_key: SecretStr | None = None

    # ── 7. Performance / hardening ──
    statement_timeout: str = "30s"
    idempotency_ttl: int = 3600

    # ── 6. Cloudflare scraping ──
    cloudflare_api_key: SecretStr | None = None
    cloudflare_account_id: str | None = None

    # ── 8. Pollinations.ai (optional — bumps you from anonymous to seed tier) ──
    pollinations_token: SecretStr | None = None

    # ── Derived helpers ─────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.production

    @property
    def public_supabase_key(self) -> SecretStr | None:
        """The key safe to expose to clients (publishable > anon)."""
        return self.supabase_publishable_key or self.supabase_anon_key

    @property
    def admin_supabase_key(self) -> SecretStr | None:
        """The key with full database access (secret > service_role)."""
        return self.supabase_secret_key or self.supabase_service_role_key

    # ── Validation ──────────────────────────────────────────────────

    @model_validator(mode="after")
    def _validate_supabase_keys(self) -> Settings:
        """At least one valid Supabase key pair must be configured."""
        if self.public_supabase_key is None:
            raise ValueError(
                "Supabase public key missing — set SUPABASE_PUBLISHABLE_KEY "
                "(new scheme) or SUPABASE_ANON_KEY (legacy) in your environment."
            )
        if self.admin_supabase_key is None:
            raise ValueError(
                "Supabase admin key missing — set SUPABASE_SECRET_KEY "
                "(new scheme) or SUPABASE_SERVICE_ROLE_KEY (legacy)."
            )
        return self

    @model_validator(mode="after")
    def _validate_database_url(self) -> Settings:
        """Confirm the database URL targets the async driver."""
        url = self.database_url.get_secret_value()
        if not url.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the asyncpg driver: postgresql+asyncpg://...")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` instance.

    Cached so configuration is parsed exactly once. Tests can call
    ``get_settings.cache_clear()`` between fixtures if needed.
    """
    return Settings()
