from __future__ import annotations

import httpx


async def test_healthz_ok(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # Browser pool launched during the fixture -> connected.
    assert body["browser_connected"] is True
    assert "X-Request-Id" in resp.headers
