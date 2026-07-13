"""Centralized app configuration. Never read os.environ directly outside this module."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    # Runtime app connection — least-privilege `deallens_app` role, subject to RLS.
    database_url: str
    # Owner-role connection, used only for Alembic migrations and `system_session()`
    # bootstrap paths (Clerk webhooks creating a brand-new user/org) that must write before
    # any actor-scoped RLS GUC can exist. Never used to serve a user request.
    database_url_system: str
    database_url_sync: str

    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint_url: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_bucket: str = "deallens-dev"

    clerk_issuer_url: str
    clerk_jwks_url: str
    clerk_secret_key: str
    clerk_webhook_signing_secret: str

    session_issuer_leeway_seconds: int = 10
    account_deletion_grace_days: int = 30

    cors_allow_origins: list[str] = ["http://localhost:3000"]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # populated from env/.env
