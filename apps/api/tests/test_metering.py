from __future__ import annotations

import httpx
from conftest import auth_headers, create_user_with_credits, get_balance

from pdf_api.jobservice import EngineState
from pdf_api.renderer import RenderError

HTML = "<html><body>metering</body></html>"


async def test_402_when_credits_empty(client: httpx.AsyncClient) -> None:
    uid, key = await create_user_with_credits(credits=0)
    resp = await client.post("/v1/convert", json={"html": HTML}, headers=auth_headers(key))
    assert resp.status_code == 402
    assert await get_balance(uid) == 0


async def test_refund_after_forced_failure(
    client: httpx.AsyncClient, engine_state: EngineState
) -> None:
    uid, key = await create_user_with_credits(credits=75)

    async def boom(**_kwargs: object) -> None:
        raise RenderError("forced failure")

    engine_state.pool.render = boom  # type: ignore[assignment]

    resp = await client.post("/v1/convert", json={"html": HTML}, headers=auth_headers(key))
    assert resp.status_code == 502
    # Charged 1 then refunded 1 -> balance restored.
    assert await get_balance(uid) == 75


async def test_output_size_limit_refunds(
    client: httpx.AsyncClient, engine_state: EngineState
) -> None:
    uid, key = await create_user_with_credits(credits=75)
    engine_state.pool.max_output_bytes = 10  # any real PDF exceeds this

    resp = await client.post("/v1/convert", json={"html": HTML}, headers=auth_headers(key))
    assert resp.status_code == 502
    assert "output_too_large" in resp.json()["detail"]
    assert await get_balance(uid) == 75


async def test_input_size_limit_413(
    client: httpx.AsyncClient, engine_state: EngineState
) -> None:
    _uid, key = await create_user_with_credits()
    original = engine_state.settings.max_html_bytes
    engine_state.settings.max_html_bytes = 50
    try:
        big = "<html>" + ("x" * 500) + "</html>"
        resp = await client.post("/v1/convert", json={"html": big}, headers=auth_headers(key))
        assert resp.status_code == 413
    finally:
        engine_state.settings.max_html_bytes = original
