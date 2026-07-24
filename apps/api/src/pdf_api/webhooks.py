"""Signed outbound job callbacks with bounded retries."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import models
from .config import Settings
from .db import connection, transaction
from .logging_config import get_logger
from .security import SecurityBoundaryError, validate_target

log = get_logger(__name__)
Sleep = Callable[[float], Awaitable[None]]


def generate_webhook_secret() -> str:
    return secrets.token_urlsafe(32)


def signature_header(secret: str, payload: bytes, timestamp: int | None = None) -> str:
    timestamp = int(time.time()) if timestamp is None else timestamp
    signed = str(timestamp).encode("ascii") + b"." + payload
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify_signature(
    secret: str,
    payload: bytes,
    header: str,
    *,
    now: int | None = None,
    tolerance_seconds: int = 300,
) -> bool:
    parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
    try:
        timestamp = int(parts["t"])
        supplied = parts["v1"]
    except (KeyError, ValueError):
        return False
    now = int(time.time()) if now is None else now
    if abs(now - timestamp) > tolerance_seconds:
        return False
    expected = signature_header(secret, payload, timestamp).split("v1=", 1)[1]
    return hmac.compare_digest(expected, supplied)


async def rotate_secret(user_id: uuid.UUID) -> str:
    secret = generate_webhook_secret()
    async with transaction() as conn:
        stmt = pg_insert(models.webhook_secrets).values(user_id=user_id, secret=secret)
        await conn.execute(
            stmt.on_conflict_do_update(
                index_elements=[models.webhook_secrets.c.user_id],
                set_={"secret": stmt.excluded.secret},
            )
        )
    return secret


async def _post_with_retries(
    settings: Settings,
    *,
    url: str,
    secret: str,
    payload: bytes,
    client: httpx.AsyncClient,
    sleep: Sleep = asyncio.sleep,
) -> bool:
    header = signature_header(secret, payload)
    for attempt, delay in enumerate((0.0, 0.25, 1.0), start=1):
        if delay:
            await sleep(delay)
        try:
            await validate_target(url)
            response = await client.post(
                url,
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-CleanPDF-Signature": header,
                },
            )
            if 200 <= response.status_code < 300:
                return True
            log.warning(
                "webhook.non_success",
                attempt=attempt,
                status_code=response.status_code,
            )
        except (httpx.HTTPError, SecurityBoundaryError) as exc:
            log.warning("webhook.delivery_attempt_failed", attempt=attempt, error=str(exc))
    return False


def _payload(row: Mapping[Any, Any]) -> bytes:
    body = {
        "event": f"job.{row['status']}",
        "job": {
            "id": str(row["id"]),
            "kind": row["kind"],
            "status": row["status"],
            "credits_charged": int(row["credits_charged"]),
            "output_bytes": row["output_bytes"],
            "duration_ms": row["duration_ms"],
            "failure_reason": row["failure_reason"],
            "expires_at": (row["expires_at"].isoformat() if row["expires_at"] is not None else None),
        },
    }
    return json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")


async def deliver_job_webhook(
    settings: Settings,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    async with connection() as conn:
        row = (
            (
                await conn.execute(
                    select(
                        models.jobs.c.id,
                        models.jobs.c.kind,
                        models.jobs.c.status,
                        models.jobs.c.credits_charged,
                        models.jobs.c.output_bytes,
                        models.jobs.c.duration_ms,
                        models.jobs.c.failure_reason,
                        models.jobs.c.expires_at,
                        models.jobs.c.webhook_url,
                        models.jobs.c.webhook_delivered,
                        models.webhook_secrets.c.secret,
                    )
                    .select_from(
                        models.jobs.outerjoin(
                            models.webhook_secrets,
                            models.jobs.c.user_id == models.webhook_secrets.c.user_id,
                        )
                    )
                    .where(
                        models.jobs.c.id == job_id,
                        models.jobs.c.user_id == user_id,
                    )
                )
            )
            .mappings()
            .first()
        )
    if (
        row is None
        or not row["webhook_url"]
        or not row["secret"]
        or row["webhook_delivered"]
        or row["status"] not in {"completed", "failed"}
    ):
        return False

    async with httpx.AsyncClient(timeout=settings.outbound_webhook_timeout_seconds) as client:
        delivered = await _post_with_retries(
            settings,
            url=str(row["webhook_url"]),
            secret=str(row["secret"]),
            payload=_payload(row),
            client=client,
        )
    if delivered:
        async with transaction() as conn:
            await conn.execute(
                update(models.jobs)
                .where(
                    models.jobs.c.id == job_id,
                    models.jobs.c.user_id == user_id,
                )
                .values(webhook_delivered=True)
            )
    return delivered
