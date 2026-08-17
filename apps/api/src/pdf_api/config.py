"""Application settings loaded from the environment (pydantic-settings)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

MB = 1024 * 1024

# Unpacked Chrome extensions get a synthetic 32-char id in the alphabet a-p.
# This matches only ``chrome-extension://`` origins -- never http/https -- so
# enabling it for local testing cannot open the API to arbitrary websites.
DEV_EXTENSION_ORIGIN_REGEX = r"chrome-extension://[a-p]{32}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/web2pdf",
        description="Async SQLAlchemy DSN (asyncpg driver).",
    )

    enable_url_rendering: bool = False
    max_concurrent_renders: int = 3
    recycle_after_jobs: int = 50
    render_timeout_ms: int = 15_000
    max_html_bytes: int = 10 * MB
    max_output_bytes: int = 50 * MB
    max_network_requests: int = 150
    max_network_bytes: int = 30 * MB
    sync_wait_seconds: float = 20.0
    shutdown_grace_seconds: float = 20.0
    free_requests_per_minute: int = 10
    paid_requests_per_minute: int = 60

    output_ttl_seconds: int = 24 * 60 * 60
    storage_dir: str = "./var/storage"

    # Reading packs (Day 4).
    max_pack_items: int = 25
    pack_item_timeout_ms: int = 15_000

    # Signed output downloads. Async jobs (including packs) are otherwise
    # unretrievable. When unset a random per-process secret is used, which
    # invalidates outstanding links on restart -- set this in production.
    download_signing_secret: str | None = None
    download_url_ttl_seconds: int = 24 * 60 * 60
    public_base_url: str = "http://localhost:8000"

    # CORS for the Manifest V3 browser extension. Only chrome-extension://
    # origins are ever allowed -- never arbitrary http/https sites. In
    # development any unpacked extension id is permitted (see the regex above);
    # in production set the exact packed extension origin(s) here (comma
    # separated), e.g. "chrome-extension://<store-id>".
    cors_extension_origins: str = ""

    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str | None = None
    r2_endpoint_url: str | None = None

    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_starter_price_id: str | None = None
    stripe_pro_price_id: str | None = None
    stripe_overage_price_id: str | None = None
    billing_success_url: str = "http://localhost:8000/usage?checkout=success"
    billing_cancel_url: str = "http://localhost:8000/usage?checkout=cancelled"
    billing_portal_return_url: str = "http://localhost:8000/usage"
    outbound_webhook_timeout_seconds: float = 5.0
    webhook_signature_tolerance_seconds: int = 300

    sentry_dsn: str | None = None
    posthog_api_key: str | None = None
    posthog_host: str = "https://us.i.posthog.com"

    environment: str = "development"
    log_level: str = "INFO"

    @property
    def cors_allow_origins(self) -> list[str]:
        """Explicit chrome-extension:// origins allowed (production)."""
        return [origin.strip() for origin in self.cors_extension_origins.split(",") if origin.strip()]

    @property
    def cors_allow_origin_regex(self) -> str | None:
        """Allow unpacked extension ids only in development."""
        return DEV_EXTENSION_ORIGIN_REGEX if self.environment == "development" else None

    @property
    def r2_configured(self) -> bool:
        return all(
            [
                self.r2_account_id,
                self.r2_access_key_id,
                self.r2_secret_access_key,
                self.r2_bucket,
                self.r2_endpoint_url,
            ]
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
