"""CORS boundary for the Manifest V3 browser extension.

The extension calls the API cross-origin from a ``chrome-extension://`` origin.
Only those origins may pass; arbitrary websites must be rejected. Cookies are
never allowed (the extension authenticates with a bearer key, not a session).
"""

from __future__ import annotations

import httpx

# A well-formed unpacked extension origin (32 chars in the a-p alphabet).
EXTENSION_ORIGIN = "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
WEBSITE_ORIGIN = "https://evil.example.com"


async def test_preflight_allows_extension_origin(client: httpx.AsyncClient) -> None:
    resp = await client.options(
        "/v1/packs",
        headers={
            "Origin": EXTENSION_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    # Never leak a credentialed CORS response.
    assert resp.headers.get("access-control-allow-credentials") != "true"


async def test_preflight_allows_authorization_and_content_type(client: httpx.AsyncClient) -> None:
    resp = await client.options(
        "/v1/packs",
        headers={
            "Origin": EXTENSION_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert resp.status_code == 200
    allowed = resp.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed
    assert "content-type" in allowed


async def test_preflight_allows_get_and_post(client: httpx.AsyncClient) -> None:
    for method in ("GET", "POST"):
        resp = await client.options(
            "/v1/usage",
            headers={
                "Origin": EXTENSION_ORIGIN,
                "Access-Control-Request-Method": method,
            },
        )
        assert resp.status_code == 200, method
        assert resp.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
        allowed = resp.headers["access-control-allow-methods"].upper()
        assert method in allowed


async def test_simple_request_echoes_extension_origin(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz", headers={"Origin": EXTENSION_ORIGIN})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == EXTENSION_ORIGIN


async def test_preflight_rejects_arbitrary_website_origin(client: httpx.AsyncClient) -> None:
    resp = await client.options(
        "/v1/packs",
        headers={
            "Origin": WEBSITE_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )
    # Starlette returns 400 for a disallowed preflight and sets no allow-origin.
    assert resp.status_code == 400
    assert "access-control-allow-origin" not in resp.headers


async def test_simple_request_from_website_gets_no_cors_header(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz", headers={"Origin": WEBSITE_ORIGIN})
    # The endpoint still answers, but without CORS headers the browser blocks it.
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers
