from __future__ import annotations

import asyncio

import httpx
from conftest import auth_headers, create_user_with_credits, get_balance

HTML = "<html><body><h1>Async job</h1></body></html>"


async def _wait_for(client: httpx.AsyncClient, job_id: str, headers: dict[str, str]) -> dict:
    for _ in range(150):  # up to ~15s
        resp = await client.get(f"/v1/jobs/{job_id}", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in ("completed", "failed"):
            return body
        await asyncio.sleep(0.1)
    raise AssertionError("job did not finish in time")


async def test_job_lifecycle_queued_to_completed(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    headers = auth_headers(key)

    resp = await client.post("/v1/jobs", json={"html": HTML}, headers=headers)
    assert resp.status_code == 202
    created = resp.json()
    assert created["status"] == "queued"
    assert created["kind"] == "html"
    assert created["credits_charged"] == 1

    final = await _wait_for(client, created["id"], headers)
    assert final["status"] == "completed"
    assert final["output_bytes"] > 0
    assert final["duration_ms"] is not None
    assert final["expires_at"] is not None


async def test_idempotency_key_dedupes(client: httpx.AsyncClient) -> None:
    uid, key = await create_user_with_credits(credits=75)
    headers = auth_headers(key)
    payload = {"html": HTML, "idempotency_key": "abc-123"}

    first = await client.post("/v1/jobs", json=payload, headers=headers)
    assert first.status_code == 202
    first_id = first.json()["id"]

    second = await client.post("/v1/jobs", json=payload, headers=headers)
    assert second.status_code == 200  # returns the original, not a new job
    assert second.json()["id"] == first_id

    # Only charged once.
    assert await get_balance(uid) == 74


async def test_get_unknown_job_404(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    resp = await client.get("/v1/jobs/00000000-0000-0000-0000-000000000000", headers=auth_headers(key))
    assert resp.status_code == 404


async def test_usage_endpoint(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits(credits=75)
    resp = await client.get("/v1/usage", headers=auth_headers(key))
    assert resp.status_code == 200
    body = resp.json()
    assert body["balance"] == 75
    assert body["plan_id"] == "free"
    assert body["monthly_credits"] == 75
