"""HMAC-signed, expiring download links for finished job output.

Async jobs (``POST /v1/jobs`` and ``POST /v1/packs``) render out of band, so the
caller needs a way to fetch the resulting PDF. Signing the job id together with
an expiry lets the link be handed to a browser download without replaying the
API key, while still being unforgeable and time-limited.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from urllib.parse import urlencode

from .config import Settings
from .logging_config import get_logger

log = get_logger(__name__)

_PURPOSE = b"cleanpdf-job-download-v1"
_fallback_secret: str | None = None


class SignatureError(Exception):
    """The presented download signature is invalid or expired."""


def _secret(settings: Settings) -> str:
    """Return the configured signing secret, or a per-process fallback."""
    global _fallback_secret
    if settings.download_signing_secret:
        return settings.download_signing_secret
    if _fallback_secret is None:
        _fallback_secret = secrets.token_urlsafe(32)
        log.warning(
            "signing.ephemeral_secret",
            reason="DOWNLOAD_SIGNING_SECRET unset; links break across restarts",
        )
    return _fallback_secret


def _digest(settings: Settings, job_id: uuid.UUID, expires: int) -> str:
    message = _PURPOSE + b"|" + str(job_id).encode("ascii") + b"|" + str(expires).encode("ascii")
    return hmac.new(_secret(settings).encode("utf-8"), message, hashlib.sha256).hexdigest()


def sign_download(settings: Settings, job_id: uuid.UUID, *, ttl_seconds: int | None = None) -> tuple[int, str]:
    """Return ``(expires_epoch, signature)`` for a job download link."""
    ttl = settings.download_url_ttl_seconds if ttl_seconds is None else ttl_seconds
    expires = int(time.time()) + ttl
    return expires, _digest(settings, job_id, expires)


def verify_download(settings: Settings, job_id: uuid.UUID, expires: int, signature: str) -> None:
    """Raise :class:`SignatureError` unless the signature is valid and fresh."""
    if not hmac.compare_digest(_digest(settings, job_id, expires), signature):
        raise SignatureError("invalid signature")
    if expires < int(time.time()):
        raise SignatureError("signature expired")


def download_path(job_id: uuid.UUID, expires: int, signature: str) -> str:
    query = urlencode({"expires": expires, "sig": signature})
    return f"/v1/jobs/{job_id}/download?{query}"


def download_url(settings: Settings, job_id: uuid.UUID, *, ttl_seconds: int | None = None) -> str:
    expires, signature = sign_download(settings, job_id, ttl_seconds=ttl_seconds)
    return settings.public_base_url.rstrip("/") + download_path(job_id, expires, signature)
