from __future__ import annotations

import httpx
from conftest import auth_headers, create_user_with_credits, get_balance

HTML = "<html><body><h1>Hello PDF</h1><p>Rendered by Chromium.</p></body></html>"


async def test_convert_html_returns_valid_pdf(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    resp = await client.post("/v1/convert", json={"html": HTML}, headers=auth_headers(key))
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:5] == b"%PDF-"
    assert "X-Job-Id" in resp.headers
    assert resp.headers["X-Credits-Charged"] == "1"


async def test_convert_charges_one_credit(client: httpx.AsyncClient) -> None:
    uid, key = await create_user_with_credits(credits=75)
    resp = await client.post("/v1/convert", json={"html": HTML}, headers=auth_headers(key))
    assert resp.status_code == 200
    assert await get_balance(uid) == 74


async def test_convert_options_are_honoured(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    resp = await client.post(
        "/v1/convert",
        json={
            "html": HTML,
            "page_size": "Letter",
            "landscape": True,
            "margin_mm": 20,
            "scale": 1.5,
            "wait_until": "load",
            "timeout_ms": 5000,
            "header_template": "<div>hdr</div>",
            "footer_template": "<div>ftr</div>",
        },
        headers=auth_headers(key),
    )
    assert resp.status_code == 200
    assert resp.content[:5] == b"%PDF-"
