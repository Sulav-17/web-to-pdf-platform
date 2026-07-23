"""Development bootstrap: create a verified user, free API key and 75 credits.

Usage:
    uv run python -m pdf_api.bootstrap [email]

Prints the raw API key exactly once. Only the SHA-256 hash is stored.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from . import metering, models
from .db import dispose_engine, transaction
from .keys import generate_api_key, hash_key
from .seed import FREE_GRANT_CREDITS, FREE_PLAN_ID, PLAN_SEED


async def ensure_plans(conn: AsyncConnection) -> None:
    for plan in PLAN_SEED:
        stmt = pg_insert(models.plans).values(**plan)
        stmt = stmt.on_conflict_do_update(
            index_elements=[models.plans.c.id],
            set_={
                "monthly_credits": stmt.excluded.monthly_credits,
                "price_cents": stmt.excluded.price_cents,
            },
        )
        await conn.execute(stmt)


async def bootstrap(email: str) -> tuple[uuid.UUID, str]:
    raw_key = generate_api_key()
    async with transaction() as conn:
        await ensure_plans(conn)

        existing = (
            await conn.execute(select(models.users.c.id).where(models.users.c.email == email))
        ).first()
        if existing is not None:
            user_id = existing.id
        else:
            user_id = uuid.uuid4()
            await conn.execute(
                insert(models.users).values(
                    id=user_id,
                    email=email,
                    email_verified=True,
                    created_at=datetime.now(UTC),
                )
            )
            await conn.execute(
                insert(models.subscriptions).values(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    plan_id=FREE_PLAN_ID,
                    status="active",
                )
            )
            await metering.grant_credits(
                conn, user_id, FREE_GRANT_CREDITS, reason=metering.REASON_MONTHLY_GRANT
            )

        await conn.execute(
            insert(models.api_keys).values(
                id=uuid.uuid4(),
                user_id=user_id,
                key_hash=hash_key(raw_key),
                label="bootstrap",
                active=True,
                created_at=datetime.now(UTC),
            )
        )
    return user_id, raw_key


async def _amain(email: str) -> None:
    user_id, raw_key = await bootstrap(email)
    await dispose_engine()
    print("Bootstrap complete.")
    print(f"  user_id : {user_id}")
    print(f"  email   : {email}")
    print(f"  credits : {FREE_GRANT_CREDITS}")
    print()
    print("API key (shown once, store it now):")
    print(f"  {raw_key}")


def main() -> None:
    email = sys.argv[1] if len(sys.argv) > 1 else "dev@example.com"
    asyncio.run(_amain(email))


if __name__ == "__main__":
    main()
