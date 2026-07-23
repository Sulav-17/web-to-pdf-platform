"""Bearer API-key authentication dependency."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import func, select, update

from . import models
from .db import connection
from .keys import hash_key


@dataclass(frozen=True)
class Principal:
    """The authenticated caller resolved from an API key."""

    user_id: uuid.UUID
    api_key_id: uuid.UUID
    email: str


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


async def require_api_key(authorization: str | None = Header(default=None)) -> Principal:
    token = _extract_bearer(authorization)
    key_hash = hash_key(token)

    async with connection() as conn:
        row = (
            await conn.execute(
                select(
                    models.api_keys.c.id,
                    models.api_keys.c.user_id,
                    models.api_keys.c.active,
                    models.users.c.email,
                )
                .select_from(
                    models.api_keys.join(
                        models.users, models.api_keys.c.user_id == models.users.c.id
                    )
                )
                .where(models.api_keys.c.key_hash == key_hash)
            )
        ).first()

        if row is None or not row.active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or inactive API key",
                headers={"WWW-Authenticate": "Bearer"},
            )

        await conn.execute(
            update(models.api_keys)
            .where(models.api_keys.c.id == row.id)
            .values(last_used_at=func.now())
        )
        await conn.commit()

    return Principal(user_id=row.user_id, api_key_id=row.id, email=row.email)


PrincipalDep = Depends(require_api_key)
