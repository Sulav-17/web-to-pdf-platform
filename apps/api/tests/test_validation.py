from __future__ import annotations

import httpx
from conftest import auth_headers, create_user_with_credits


async def test_both_html_and_url_rejected(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    resp = await client.post(
        "/v1/convert",
        json={"html": "<p>x</p>", "url": "https://example.com"},
        headers=auth_headers(key),
    )
    assert resp.status_code == 422


async def test_neither_html_nor_url_rejected(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    resp = await client.post("/v1/convert", json={}, headers=auth_headers(key))
    assert resp.status_code == 422


async def test_out_of_range_options_rejected(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    # scale above 2.0 and margin above 50 are invalid.
    resp = await client.post(
        "/v1/convert",
        json={"html": "<p>x</p>", "scale": 5.0, "margin_mm": 200},
        headers=auth_headers(key),
    )
    assert resp.status_code == 422


async def test_url_rendering_disabled_503(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    resp = await client.post(
        "/v1/convert", json={"url": "https://example.com"}, headers=auth_headers(key)
    )
    assert resp.status_code == 503
    assert "disabled" in resp.json()["detail"].lower()
