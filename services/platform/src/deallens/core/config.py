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

    # Background automation (§9.4/§9.5). Celery uses Redis as both broker and result backend
    # by default — the same instance as `redis_url` (the per-property analysis lock lives
    # there too). Split out so a production deploy can point the queue at a dedicated Redis
    # without moving the cache/lock. `celery_eager` runs tasks inline (no worker/broker) — the
    # substrate for a synchronous single-process run and for task tests.
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    celery_eager: bool = False

    # Instant buy-box alerts are gated on analysis confidence (03 §25.1 #4): a low-confidence
    # score never fires an interrupt-the-user alert — it waits for the digest. Boxes and the
    # matcher read this floor (0–1 scale) rather than hardcoding it.
    alerts_confidence_gate: float = 0.5

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

    @property
    def broker_url(self) -> str:
        """Celery broker — the dedicated `celery_broker_url` if set, else the shared Redis."""
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # populated from env/.env
