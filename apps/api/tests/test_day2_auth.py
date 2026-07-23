from __future__ import annotations

import httpx
from conftest import auth_headers, create_user_with_credits
from sqlalchemy import update

from pdf_api import models
from pdf_api.db import get_engine


async def test_unverified_email_cannot_use_api_key(
    client: httpx.AsyncClient,
) -> None:
    user_id, key = await create_user_with_credits()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(update(models.users).where(models.users.c.id == user_id).values(email_verified=False))
    await engine.dispose()

    response = await client.get("/v1/usage", headers=auth_headers(key))
    assert response.status_code == 403
    assert "verification" in response.json()["detail"].lower()


async def test_free_key_rate_limit_returns_429(
    client: httpx.AsyncClient,
) -> None:
    _user_id, key = await create_user_with_credits()
    headers = auth_headers(key)

    for _ in range(10):
        response = await client.get("/v1/usage", headers=headers)
        assert response.status_code == 200

    blocked = await client.get("/v1/usage", headers=headers)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
