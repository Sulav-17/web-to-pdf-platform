from __future__ import annotations

import asyncio
import time
import uuid

import httpx
import pytest
from conftest import auth_headers, create_user_with_credits, get_balance
from sqlalchemy import func, select

from pdf_api import jobservice, metering, models
from pdf_api.db import connection, transaction
from pdf_api.jobservice import EngineState
from pdf_api.renderer import RenderResult
from pdf_api.schemas import RenderOptions

HTML = "<html><body><h1>Hardening</h1></body></html>"


async def _charged_job(user_id: uuid.UUID) -> uuid.UUID:
    options = RenderOptions()
    async with transaction() as conn:
        charged = await metering.create_job_charged(
            conn,
            user_id=user_id,
            api_key_id=None,
            kind="html",
            input_hash=uuid.uuid4().hex,
            options=options.model_dump(),
            cost=1,
            expires_at=metering.default_expiry(3600),
        )
    return charged.id


async def _wait_for_terminal(
    client: httpx.AsyncClient, job_id: str, headers: dict[str, str]
) -> dict[str, object]:
    for _ in range(150):
        response = await client.get(f"/v1/jobs/{job_id}", headers=headers)
        assert response.status_code == 200
        body: dict[str, object] = response.json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.1)
    raise AssertionError("job did not finish in time")


async def test_storage_failure_fails_and_refunds(
    client: httpx.AsyncClient, engine_state: EngineState
) -> None:
    user_id, key = await create_user_with_credits()

    async def fail_put(*_args: object, **_kwargs: object) -> str:
        raise OSError("forced storage failure")

    engine_state.storage.put = fail_put  # type: ignore[method-assign]
    response = await client.post(
        "/v1/convert", json={"html": HTML}, headers=auth_headers(key)
    )

    assert response.status_code == 502
    assert "storage_error" in response.json()["detail"]
    assert await get_balance(user_id) == 75


async def test_repeated_failure_refunds_only_once(
    client: httpx.AsyncClient, engine_state: EngineState
) -> None:
    user_id, key = await create_user_with_credits()

    async def fail_put(*_args: object, **_kwargs: object) -> str:
        raise OSError("forced storage failure")

    engine_state.storage.put = fail_put  # type: ignore[method-assign]
    response = await client.post(
        "/v1/convert", json={"html": HTML}, headers=auth_headers(key)
    )
    assert response.status_code == 502

    async with connection() as conn:
        job_id = (
            await conn.execute(
                select(models.jobs.c.id).where(models.jobs.c.user_id == user_id)
            )
        ).scalar_one()

    await asyncio.gather(
        jobservice._fail_and_refund(job_id, user_id, reason="duplicate one"),
        jobservice._fail_and_refund(job_id, user_id, reason="duplicate two"),
    )

    async with connection() as conn:
        refund_count = int(
            (
                await conn.execute(
                    select(func.count())
                    .select_from(models.credit_ledger)
                    .where(
                        models.credit_ledger.c.user_id == user_id,
                        models.credit_ledger.c.job_id == job_id,
                        models.credit_ledger.c.reason == metering.REASON_ADMIN,
                        models.credit_ledger.c.delta > 0,
                    )
                )
            ).scalar_one()
        )
    assert refund_count == 1
    assert await get_balance(user_id) == 75


async def test_duplicate_execution_renders_once(engine_state: EngineState) -> None:
    user_id, _key = await create_user_with_credits()
    job_id = await _charged_job(user_id)
    calls = 0

    async def render_once(**_kwargs: object) -> RenderResult:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return RenderResult(pdf=b"%PDF-test", duration_ms=1)

    engine_state.pool.render = render_once  # type: ignore[method-assign]
    options = RenderOptions()
    kwargs = {
        "job_id": job_id,
        "user_id": user_id,
        "kind": "html",
        "source": HTML,
        "options": options,
    }
    await asyncio.gather(
        jobservice.execute_job(engine_state, **kwargs),
        jobservice.execute_job(engine_state, **kwargs),
    )

    job = await jobservice.load_job(job_id, user_id)
    assert calls == 1
    assert job is not None and job["status"] == "completed"
    assert await get_balance(user_id) == 74


async def test_cancelled_execution_fails_and_refunds(engine_state: EngineState) -> None:
    user_id, _key = await create_user_with_credits()
    job_id = await _charged_job(user_id)
    started = asyncio.Event()
    never = asyncio.Event()

    async def wait_forever(**_kwargs: object) -> RenderResult:
        started.set()
        await never.wait()
        raise AssertionError("unreachable")

    engine_state.pool.render = wait_forever  # type: ignore[method-assign]
    task = asyncio.create_task(
        jobservice.execute_job(
            engine_state,
            job_id=job_id,
            user_id=user_id,
            kind="html",
            source=HTML,
            options=RenderOptions(),
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    job = await jobservice.load_job(job_id, user_id)
    assert job is not None and job["status"] == "failed"
    assert str(job["failure_reason"]).startswith("cancelled_during_")
    assert await get_balance(user_id) == 75


async def test_convert_falls_back_to_202_without_cancelling_job(
    client: httpx.AsyncClient, engine_state: EngineState
) -> None:
    user_id, key = await create_user_with_credits()
    original_render = engine_state.pool.render
    original_wait = engine_state.settings.sync_wait_seconds

    async def slow_render(**kwargs: object) -> RenderResult:
        await asyncio.sleep(0.05)
        return await original_render(**kwargs)  # type: ignore[arg-type]

    engine_state.pool.render = slow_render  # type: ignore[method-assign]
    engine_state.settings.sync_wait_seconds = 0.01
    try:
        response = await client.post(
            "/v1/convert", json={"html": HTML}, headers=auth_headers(key)
        )
        assert response.status_code == 202
        job_id = response.json()["id"]
        final = await _wait_for_terminal(client, job_id, auth_headers(key))
        assert final["status"] == "completed"
        assert await get_balance(user_id) == 74
    finally:
        engine_state.settings.sync_wait_seconds = original_wait


async def test_recycled_browser_closes_after_inflight_release(
    engine_state: EngineState,
) -> None:
    pool = engine_state.pool
    async with pool._lock:
        old = pool._current
        assert old is not None
        old.inflight += 1
        await pool._recycle_locked()
        assert old.retiring is True

    await pool._release(old)
    assert old.browser.is_connected() is False


async def test_html_subresources_are_denied(engine_state: EngineState) -> None:
    hits = 0

    async def handler(
        _reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        nonlocal hits
        hits += 1
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    try:
        assert server.sockets
        port = server.sockets[0].getsockname()[1]
        html = f'<html><body><img src="http://127.0.0.1:{port}/x.png"></body></html>'
        await engine_state.pool.render(
            kind="html", source=html, options=RenderOptions()
        )
        await asyncio.sleep(0.05)
    finally:
        server.close()
        await server.wait_closed()
    assert hits == 0


async def test_warm_html_render_under_three_seconds(
    client: httpx.AsyncClient,
) -> None:
    _user_id, key = await create_user_with_credits()
    headers = auth_headers(key)
    warmup = await client.post("/v1/convert", json={"html": HTML}, headers=headers)
    assert warmup.status_code == 200

    started = time.monotonic()
    response = await client.post("/v1/convert", json={"html": HTML}, headers=headers)
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < 3.0, f"warm conversion took {elapsed:.3f}s"
