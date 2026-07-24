"""Bearer API-key authentication and per-key abuse limiting."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select, update

from . import models
from .db import connection
from .keys import hash_key
from .rate_limit import SlidingWindowRateLimiter

bearer_scheme = HTTPBearer(
    bearerFormat="API key",
    scheme_name="Bearer API key",
    description=(
        "Enter the raw CleanPDF API key. "
        "Swagger sends it as Authorization: Bearer <key>."
    ),
    auto_error=False,
)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller resolved from an API key."""

    user_id: uuid.UUID
    api_key_id: uuid.UUID
    email: str
    plan_id: str


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header; expected 'Bearer <key>'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


async def require_api_key(
    request: Request,
    _credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> Principal:
    token = _extract_bearer(request.headers.get("Authorization"))
    key_hash = hash_key(token)

    async with connection() as conn:
        row = (
            await conn.execute(
                select(
                    models.api_keys.c.id,
                    models.api_keys.c.user_id,
                    models.api_keys.c.active,
                    models.users.c.email,
                    models.users.c.email_verified,
                    models.subscriptions.c.plan_id,
                )
                .select_from(
                    models.api_keys.join(
                        models.users,
                        models.api_keys.c.user_id == models.users.c.id,
                    ).outerjoin(
                        models.subscriptions,
                        models.users.c.id == models.subscriptions.c.user_id,
                    )
                )
                .where(models.api_keys.c.key_hash == key_hash)
                .limit(1)
            )
        ).first()

        if row is None or not row.active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or inactive API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not row.email_verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Email verification is required before using an API key",
            )

        plan_id = str(row.plan_id or "free")
        settings = request.app.state.engine.settings
        limit = settings.free_requests_per_minute if plan_id == "free" else settings.paid_requests_per_minute
        limiter: SlidingWindowRateLimiter = request.app.state.rate_limiter
        decision = await limiter.check(str(row.id), limit=limit)
        if not decision.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="API key rate limit exceeded",
                headers={"Retry-After": str(decision.retry_after)},
            )

        await conn.execute(
            update(models.api_keys).where(models.api_keys.c.id == row.id).values(last_used_at=func.now())
        )
        await conn.commit()

    return Principal(
        user_id=row.user_id,
        api_key_id=row.id,
        email=row.email,
        plan_id=plan_id,
    )


PrincipalDep = Depends(require_api_key)
