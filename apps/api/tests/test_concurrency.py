from __future__ import annotations

import asyncio

import httpx
from conftest import auth_headers, create_user_with_credits, get_balance

HTML = "<html><body><h1>Concurrent</h1></body></html>"


async def test_ten_concurrent_jobs_no_pool_corruption(client: httpx.AsyncClient) -> None:
    uid, key = await create_user_with_credits(credits=75)
    headers = auth_headers(key)

    async def one() -> httpx.Response:
        return await client.post("/v1/convert", json={"html": HTML}, headers=headers)

    responses = await asyncio.gather(*[one() for _ in range(10)])

    for resp in responses:
        assert resp.status_code == 200
        assert resp.content[:5] == b"%PDF-"

    # 10 successful HTML renders at 1 credit each.
    assert await get_balance(uid) == 65
