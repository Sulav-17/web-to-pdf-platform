"""Credit metering: atomic charge on job creation, refund on render failure.

Balance is the running sum of ``credit_ledger.delta`` for a user. All mutations
happen inside a single transaction that first locks the user row, guaranteeing
that concurrent conversions cannot overspend.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from . import models

# --- Ledger reasons -------------------------------------------------------
# NOTE (contract issue): the A-ENGINE spec's ledger reason list did not include
# a value for *refunds*. To preserve the schema we reuse "admin" (with job_id)
# for failure refunds. See ORCHESTRATION.md.
REASON_RENDER: Final = "render"
REASON_GRANT: Final = "grant"
REASON_SUBSCRIPTION: Final = "subscription"
REASON_ADMIN: Final = "admin"  # also used for failure refunds

# --- Costs ----------------------------------------------------------------
COST_HTML: Final = 1
COST_URL: Final = 2


def cost_for_kind(kind: str) -> int:
    return COST_URL if kind == "url" else COST_HTML


class InsufficientCreditsError(Exception):
    """Raised when the locked balance cannot cover a conversion."""

    def __init__(self, balance: int, required: int) -> None:
        super().__init__(f"insufficient credits: have {balance}, need {required}")
        self.balance = balance
        self.required = required


@dataclass
class ChargedJob:
    id: uuid.UUID
    reused: bool  # True when returned via idempotency dedupe (no new charge)


async def get_balance(conn: AsyncConnection, user_id: uuid.UUID) -> int:
    result = await conn.execute(
        select(func.coalesce(func.sum(models.credit_ledger.c.delta), 0)).where(
            models.credit_ledger.c.user_id == user_id
        )
    )
    return int(result.scalar_one())


async def grant_credits(
    conn: AsyncConnection,
    user_id: uuid.UUID,
    amount: int,
    reason: str = REASON_GRANT,
    job_id: uuid.UUID | None = None,
) -> None:
    await conn.execute(
        insert(models.credit_ledger).values(
            user_id=user_id, delta=amount, reason=reason, job_id=job_id
        )
    )


async def create_job_charged(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    api_key_id: uuid.UUID | None,
    kind: str,
    input_hash: str,
    options: dict[str, Any],
    cost: int,
    expires_at: datetime,
    idempotency_key: str | None = None,
    webhook_url: str | None = None,
) -> ChargedJob:
    """Atomically lock the user, dedupe, check balance, insert job + charge.

    Must be called inside an open transaction (``db.transaction``).
    """
    # Serialize all of this user's credit operations.
    await conn.execute(
        select(models.users.c.id).where(models.users.c.id == user_id).with_for_update()
    )

    # Idempotency: return the original job untouched.
    if idempotency_key is not None:
        existing = (
            await conn.execute(
                select(models.jobs.c.id).where(
                    models.jobs.c.user_id == user_id,
                    models.jobs.c.idempotency_key == idempotency_key,
                )
            )
        ).first()
        if existing is not None:
            return ChargedJob(id=existing.id, reused=True)

    balance = await get_balance(conn, user_id)
    if balance < cost:
        raise InsufficientCreditsError(balance=balance, required=cost)

    job_id = uuid.uuid4()
    now = datetime.now(UTC)
    await conn.execute(
        insert(models.jobs).values(
            id=job_id,
            user_id=user_id,
            api_key_id=api_key_id,
            kind=kind,
            status="queued",
            idempotency_key=idempotency_key,
            input_hash=input_hash,
            options=options,
            credits_charged=cost,
            webhook_url=webhook_url,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
    )
    await conn.execute(
        insert(models.credit_ledger).values(
            user_id=user_id, delta=-cost, reason=REASON_RENDER, job_id=job_id
        )
    )
    return ChargedJob(id=job_id, reused=False)


async def refund_job(
    conn: AsyncConnection, user_id: uuid.UUID, job_id: uuid.UUID, amount: int
) -> None:
    """Refund a failed render. Uses reason='admin' (see contract note above)."""
    if amount <= 0:
        return
    await conn.execute(
        select(models.users.c.id).where(models.users.c.id == user_id).with_for_update()
    )
    await conn.execute(
        insert(models.credit_ledger).values(
            user_id=user_id, delta=amount, reason=REASON_ADMIN, job_id=job_id
        )
    )


def default_expiry(ttl_seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=ttl_seconds)
