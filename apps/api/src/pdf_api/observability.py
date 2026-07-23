"""Optional Sentry / PostHog wiring. Both are no-ops when unconfigured."""

from __future__ import annotations

from typing import Any

from .config import Settings
from .logging_config import get_logger

log = get_logger(__name__)

_posthog_client: Any | None = None


def init_sentry(settings: Settings) -> None:
    """Initialise Sentry only when a DSN is provided."""
    if not settings.sentry_dsn:
        log.info("sentry.disabled", reason="no DSN configured")
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.0,
    )
    log.info("sentry.enabled")


def init_posthog(settings: Settings) -> None:
    """Initialise PostHog only when an API key is provided."""
    global _posthog_client
    if not settings.posthog_api_key:
        log.info("posthog.disabled", reason="no API key configured")
        return
    from posthog import Posthog

    _posthog_client = Posthog(
        project_api_key=settings.posthog_api_key,
        host=settings.posthog_host,
    )
    log.info("posthog.enabled")


def capture_event(distinct_id: str, event: str, properties: dict[str, Any] | None = None) -> None:
    if _posthog_client is None:
        return
    _posthog_client.capture(distinct_id=distinct_id, event=event, properties=properties or {})
