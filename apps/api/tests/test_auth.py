from __future__ import annotations

import httpx
from conftest import auth_headers, create_user_with_credits

from pdf_api.keys import generate_api_key, hash_key, verify_key


def test_api_key_hashing_only_stores_sha256() -> None:
    raw = generate_api_key()
    assert raw.startswith("sk_live_")
    digest = hash_key(raw)
    # SHA-256 hex digest, and never equal to the raw key.
    assert len(digest) == 64
    assert digest != raw
    assert verify_key(raw, digest) is True
    assert verify_key(raw + "x", digest) is False


async def test_unauthorized_without_key(client: httpx.AsyncClient) -> None:
    resp = await client.post("/v1/convert", json={"html": "<p>x</p>"})
    assert resp.status_code == 401


async def test_unauthorized_with_bad_key(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/v1/convert", json={"html": "<p>x</p>"}, headers=auth_headers("sk_live_bogus")
    )
    assert resp.status_code == 401


async def test_inactive_key_rejected(client: httpx.AsyncClient) -> None:
    from sqlalchemy import update

    from pdf_api.db import get_engine
    from pdf_api.keys import hash_key as _hash
    from pdf_api.models import api_keys

    _uid, key = await create_user_with_credits()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            update(api_keys).where(api_keys.c.key_hash == _hash(key)).values(active=False)
        )
    await engine.dispose()

    resp = await client.post("/v1/convert", json={"html": "<p>x</p>"}, headers=auth_headers(key))
    assert resp.status_code == 401
