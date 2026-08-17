"""Atomic credit charging, grants, period resets, and failure refunds."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from . import models

REASON_MONTHLY_GRANT: Final = "monthly_grant"
REASON_CONVERSION: Final = "conversion"
REASON_PACK: Final = "pack"
REASON_OVERAGE_PURCHASE: Final = "overage_purchase"
REASON_ADMIN: Final = "admin"

COST_HTML: Final = 1
COST_URL: Final = 2
COST_PACK_BASE: Final = 2
COST_PACK_PER_SOURCE: Final = 1


def cost_for_kind(kind: str) -> int:
    return COST_URL if kind == "url" else COST_HTML


def cost_for_pack(source_count: int) -> int:
    """One credit per source plus a flat two credits for the pack itself."""
    return source_count * COST_PACK_PER_SOURCE + COST_PACK_BASE


class InsufficientCreditsError(Exception):
    """Raised when the locked balance cannot cover a conversion."""

    def __init__(self, balance: int, required: int) -> None:
        super().__init__(f"insufficient credits: have {balance}, need {required}")
        self.balance = balance
        self.required = required


@dataclass
class ChargedJob:
    id: uuid.UUID
    reused: bool


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
    reason: str = REASON_MONTHLY_GRANT,
    job_id: uuid.UUID | None = None,
    external_ref: str | None = None,
) -> bool:
    """Insert a grant, deduplicating provider-backed grants by external ref."""
    values = {
        "user_id": user_id,
        "delta": amount,
        "reason": reason,
        "job_id": job_id,
        "external_ref": external_ref,
    }
    if external_ref is None:
        await conn.execute(insert(models.credit_ledger).values(**values))
        return True

    row = (
        await conn.execute(
            pg_insert(models.credit_ledger)
            .values(**values)
            .on_conflict_do_nothing(
                constraint="uq_credit_ledger_external_ref",
            )
            .returning(models.credit_ledger.c.id)
        )
    ).first()
    return row is not None


async def reset_and_grant_monthly_credits(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    amount: int,
    period_ref: str,
) -> bool:
    """Expire the prior balance and grant one non-rolling monthly allowance."""
    await conn.execute(select(models.users.c.id).where(models.users.c.id == user_id).with_for_update())
    grant_ref = f"monthly:{period_ref}"
    existing = (
        await conn.execute(
            select(models.credit_ledger.c.id)
            .where(
                models.credit_ledger.c.user_id == user_id,
                models.credit_ledger.c.reason == REASON_MONTHLY_GRANT,
                models.credit_ledger.c.external_ref == grant_ref,
            )
            .limit(1)
        )
    ).first()
    if existing is not None:
        return False

    balance = await get_balance(conn, user_id)
    if balance > 0:
        await grant_credits(
            conn,
            user_id,
            -balance,
            reason=REASON_ADMIN,
            external_ref=f"reset:{period_ref}",
        )
    return await grant_credits(
        conn,
        user_id,
        amount,
        reason=REASON_MONTHLY_GRANT,
        external_ref=grant_ref,
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
    reason: str = REASON_CONVERSION,
) -> ChargedJob:
    """Lock the user, deduplicate, check balance, insert job, and charge."""
    await conn.execute(select(models.users.c.id).where(models.users.c.id == user_id).with_for_update())

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
            user_id=user_id,
            delta=-cost,
            reason=reason,
            job_id=job_id,
        )
    )
    return ChargedJob(id=job_id, reused=False)


async def refund_job(conn: AsyncConnection, user_id: uuid.UUID, job_id: uuid.UUID, amount: int) -> bool:
    """Refund a failed job exactly once; return whether a refund was inserted."""
    if amount <= 0:
        return False

    await conn.execute(select(models.users.c.id).where(models.users.c.id == user_id).with_for_update())
    existing_refund = (
        await conn.execute(
            select(models.credit_ledger.c.id)
            .where(
                models.credit_ledger.c.user_id == user_id,
                models.credit_ledger.c.job_id == job_id,
                models.credit_ledger.c.reason == REASON_ADMIN,
                models.credit_ledger.c.delta > 0,
            )
            .limit(1)
        )
    ).first()
    if existing_refund is not None:
        return False

    await conn.execute(
        insert(models.credit_ledger).values(
            user_id=user_id,
            delta=amount,
            reason=REASON_ADMIN,
            job_id=job_id,
        )
    )
    return True


def default_expiry(ttl_seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=ttl_seconds)
