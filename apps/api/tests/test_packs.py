"""Reading-pack endpoint: validation, ordering, bookmarks, TOC, credits, refunds."""

from __future__ import annotations

import asyncio
import io

import httpx
import pytest
from conftest import auth_headers, create_user_with_credits, get_balance
from pypdf import PdfReader

from pdf_api import metering, packs
from pdf_api.jobservice import EngineState
from pdf_api.renderer import RenderError


def _item(title: str, body: str | None = None) -> dict[str, str]:
    text = body or title
    return {"html": f"<html><body><h1>{text}</h1></body></html>", "title": title}


def _pack_body(*titles: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "items": [_item(t) for t in titles],
        "pack_title": "Weekly Readings",
    }
    payload.update(overrides)
    return payload


async def _wait_for(client: httpx.AsyncClient, job_id: str, headers: dict[str, str]) -> dict:
    for _ in range(300):  # up to ~30s
        resp = await client.get(f"/v1/jobs/{job_id}", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        if body["status"] in ("completed", "failed"):
            return body
        await asyncio.sleep(0.1)
    raise AssertionError("pack job did not finish in time")


async def _build_pack(client: httpx.AsyncClient, key: str, body: dict[str, object]) -> dict:
    headers = auth_headers(key)
    resp = await client.post("/v1/packs", json=body, headers=headers)
    assert resp.status_code == 202, resp.text
    created = resp.json()
    assert created["kind"] == "pack_merge"
    assert created["status"] == "queued"
    return await _wait_for(client, created["id"], headers)


async def _download(client: httpx.AsyncClient, job: dict) -> bytes:
    assert job["download_url"], "completed job must expose a signed download URL"
    path = job["download_url"].split("http://localhost:8000", 1)[-1]
    resp = await client.get(path)
    assert resp.status_code == 200
    assert resp.content[:5] == b"%PDF-"
    return resp.content


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


async def test_item_requires_exactly_one_source(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    both = {"items": [{"url": "https://a.test", "html": "<p>x</p>", "title": "t"}], "pack_title": "P"}
    neither = {"items": [{}], "pack_title": "P"}
    for body in (both, neither):
        resp = await client.post("/v1/packs", json=body, headers=auth_headers(key))
        assert resp.status_code == 422


async def test_html_item_requires_title(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    resp = await client.post(
        "/v1/packs",
        json={"items": [{"html": "<p>x</p>"}], "pack_title": "P"},
        headers=auth_headers(key),
    )
    assert resp.status_code == 422


async def test_empty_items_rejected(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    resp = await client.post(
        "/v1/packs", json={"items": [], "pack_title": "P"}, headers=auth_headers(key)
    )
    assert resp.status_code == 422


async def test_too_many_items_rejected(client: httpx.AsyncClient, engine_state: EngineState) -> None:
    _uid, key = await create_user_with_credits()
    engine_state.settings.max_pack_items = 2
    try:
        resp = await client.post(
            "/v1/packs", json=_pack_body("a", "b", "c"), headers=auth_headers(key)
        )
        assert resp.status_code == 422
        assert "at most 2" in resp.json()["detail"]
    finally:
        engine_state.settings.max_pack_items = 25


async def test_url_items_blocked_while_url_rendering_disabled(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    resp = await client.post(
        "/v1/packs",
        json={"items": [{"url": "https://example.com"}], "pack_title": "P"},
        headers=auth_headers(key),
    )
    assert resp.status_code == 503


# --------------------------------------------------------------------------
# Authentication and ownership
# --------------------------------------------------------------------------


async def test_pack_requires_authentication(client: httpx.AsyncClient) -> None:
    resp = await client.post("/v1/packs", json=_pack_body("a"))
    assert resp.status_code == 401


async def test_pack_job_not_visible_to_other_user(client: httpx.AsyncClient) -> None:
    _uid_a, key_a = await create_user_with_credits()
    _uid_b, key_b = await create_user_with_credits()
    resp = await client.post("/v1/packs", json=_pack_body("a"), headers=auth_headers(key_a))
    assert resp.status_code == 202
    job_id = resp.json()["id"]

    other = await client.get(f"/v1/jobs/{job_id}", headers=auth_headers(key_b))
    assert other.status_code == 404


# --------------------------------------------------------------------------
# Credits
# --------------------------------------------------------------------------


def test_pack_cost_formula() -> None:
    # One credit per source plus two for the pack.
    assert metering.cost_for_pack(1) == 3
    assert metering.cost_for_pack(3) == 5
    assert metering.cost_for_pack(5) == 7


async def test_pack_charges_sources_plus_two(client: httpx.AsyncClient) -> None:
    uid, key = await create_user_with_credits(credits=75)
    resp = await client.post("/v1/packs", json=_pack_body("a", "b", "c"), headers=auth_headers(key))
    assert resp.status_code == 202
    assert resp.json()["credits_charged"] == 5
    assert await get_balance(uid) == 70


async def test_pack_uses_pack_ledger_reason(client: httpx.AsyncClient) -> None:
    from sqlalchemy import select

    from pdf_api.db import get_engine
    from pdf_api.models import credit_ledger

    uid, key = await create_user_with_credits()
    resp = await client.post("/v1/packs", json=_pack_body("a"), headers=auth_headers(key))
    assert resp.status_code == 202

    engine = get_engine()
    async with engine.connect() as conn:
        reasons = (
            await conn.execute(
                select(credit_ledger.c.reason).where(
                    credit_ledger.c.user_id == uid, credit_ledger.c.delta < 0
                )
            )
        ).scalars().all()
    await engine.dispose()
    assert reasons == [metering.REASON_PACK]


async def test_pack_402_when_credits_insufficient(client: httpx.AsyncClient) -> None:
    uid, key = await create_user_with_credits(credits=3)  # needs 4 for two sources
    resp = await client.post("/v1/packs", json=_pack_body("a", "b"), headers=auth_headers(key))
    assert resp.status_code == 402
    assert await get_balance(uid) == 3


async def test_pack_idempotency_key_dedupes(client: httpx.AsyncClient) -> None:
    uid, key = await create_user_with_credits(credits=75)
    body = _pack_body("a", idempotency_key="pack-1")
    first = await client.post("/v1/packs", json=body, headers=auth_headers(key))
    assert first.status_code == 202
    second = await client.post("/v1/packs", json=body, headers=auth_headers(key))
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert await get_balance(uid) == 72  # charged once (1 source + 2)


# --------------------------------------------------------------------------
# Lifecycle, ordering, bookmarks, TOC
# --------------------------------------------------------------------------


async def test_pack_lifecycle_and_download(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    job = await _build_pack(client, key, _pack_body("Alpha", "Beta"))
    assert job["status"] == "completed"
    assert job["output_bytes"] > 0
    assert job["duration_ms"] is not None
    pdf = await _download(client, job)
    assert len(pdf) == job["output_bytes"]


async def test_pack_preserves_selected_order_in_bookmarks(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    titles = ["Zebra", "Apple", "Monkey"]  # deliberately not alphabetical
    job = await _build_pack(client, key, _pack_body(*titles))
    pdf = await _download(client, job)

    reader = PdfReader(io.BytesIO(pdf))
    bookmarks = [item.title for item in reader.outline]  # type: ignore[union-attr]
    assert bookmarks == titles


async def test_pack_has_one_bookmark_per_item(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    job = await _build_pack(client, key, _pack_body("a", "b", "c", "d"))
    pdf = await _download(client, job)
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.outline) == 4


async def test_toc_page_numbers_match_bookmark_pages(client: httpx.AsyncClient) -> None:
    """Every TOC entry must point at the page the bookmark actually lands on."""
    _uid, key = await create_user_with_credits()
    titles = ["First", "Second", "Third"]
    job = await _build_pack(client, key, _pack_body(*titles, toc=True, title_page=True))
    pdf = await _download(client, job)
    reader = PdfReader(io.BytesIO(pdf))

    # Bookmark destinations, as 1-based printed page numbers.
    bookmark_pages = [reader.get_destination_page_number(item) + 1 for item in reader.outline]  # type: ignore[arg-type]

    # The TOC page is page 2 (after the title page); read its printed numbers.
    toc_text = reader.pages[1].extract_text()
    assert "Contents" in toc_text
    for title, page in zip(titles, bookmark_pages, strict=True):
        assert title in toc_text
        assert str(page) in toc_text


async def test_title_page_and_toc_can_be_disabled(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits(credits=75)
    with_front = await _build_pack(client, key, _pack_body("a", "b"))
    without_front = await _build_pack(
        client, key, _pack_body("a", "b", toc=False, title_page=False)
    )

    with_pdf = PdfReader(io.BytesIO(await _download(client, with_front)))
    without_pdf = PdfReader(io.BytesIO(await _download(client, without_front)))

    # Cover + contents add exactly two pages; bookmarks are unchanged.
    assert len(with_pdf.pages) == len(without_pdf.pages) + 2
    assert len(without_pdf.outline) == 2
    # Without a title page the first bookmark starts on page 1.
    assert without_pdf.get_destination_page_number(without_pdf.outline[0]) == 0  # type: ignore[arg-type]


async def test_title_page_only(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    job = await _build_pack(client, key, _pack_body("a", toc=False, title_page=True))
    reader = PdfReader(io.BytesIO(await _download(client, job)))
    assert "Weekly Readings" in reader.pages[0].extract_text()
    assert reader.get_destination_page_number(reader.outline[0]) == 1  # type: ignore[arg-type]


async def test_toc_without_title_page_has_correct_page_numbers(client: httpx.AsyncClient) -> None:
    """With no cover page the TOC is page 1 and its numbers must still match."""
    _uid, key = await create_user_with_credits()
    titles = ["First", "Second", "Third"]
    job = await _build_pack(client, key, _pack_body(*titles, toc=True, title_page=False))
    reader = PdfReader(io.BytesIO(await _download(client, job)))

    # The TOC is the very first page (no cover before it).
    toc_text = reader.pages[0].extract_text()
    assert "Contents" in toc_text

    bookmark_pages = [reader.get_destination_page_number(item) + 1 for item in reader.outline]  # type: ignore[arg-type]
    # First article starts right after the single TOC page.
    assert bookmark_pages[0] == 2
    for title, page in zip(titles, bookmark_pages, strict=True):
        assert title in toc_text
        assert str(page) in toc_text


# --------------------------------------------------------------------------
# Failure and refunds
# --------------------------------------------------------------------------


async def test_pack_failure_refunds_all_credits(
    client: httpx.AsyncClient, engine_state: EngineState
) -> None:
    uid, key = await create_user_with_credits(credits=75)

    async def boom(**_kwargs: object) -> None:
        raise RenderError("forced failure")

    engine_state.pool.render = boom  # type: ignore[assignment]

    headers = auth_headers(key)
    resp = await client.post("/v1/packs", json=_pack_body("a", "b"), headers=headers)
    assert resp.status_code == 202
    job = await _wait_for(client, resp.json()["id"], headers)

    assert job["status"] == "failed"
    assert "render_error" in job["failure_reason"]
    assert job["download_url"] is None
    # 4 credits charged (2 sources + 2) then fully refunded.
    assert await get_balance(uid) == 75


async def test_pack_oversize_output_refunds(
    client: httpx.AsyncClient, engine_state: EngineState
) -> None:
    uid, key = await create_user_with_credits(credits=75)
    engine_state.settings.max_output_bytes = 10
    try:
        headers = auth_headers(key)
        resp = await client.post("/v1/packs", json=_pack_body("a"), headers=headers)
        assert resp.status_code == 202
        job = await _wait_for(client, resp.json()["id"], headers)
        assert job["status"] == "failed"
        assert "output_too_large" in job["failure_reason"]
        assert await get_balance(uid) == 75
    finally:
        engine_state.settings.max_output_bytes = 50 * 1024 * 1024


async def test_single_failing_item_fails_whole_pack(
    client: httpx.AsyncClient, engine_state: EngineState
) -> None:
    """A partial pack must never be delivered: the job fails and refunds."""
    uid, key = await create_user_with_credits(credits=75)
    original = engine_state.pool.render
    calls = {"n": 0}

    async def flaky(**kwargs: object):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 2:  # second source fails
            raise RenderError("item 2 exploded")
        return await original(**kwargs)  # type: ignore[arg-type]

    engine_state.pool.render = flaky  # type: ignore[assignment]

    headers = auth_headers(key)
    resp = await client.post("/v1/packs", json=_pack_body("a", "b", "c"), headers=headers)
    job = await _wait_for(client, resp.json()["id"], headers)
    assert job["status"] == "failed"
    assert await get_balance(uid) == 75


# --------------------------------------------------------------------------
# Signed download links
# --------------------------------------------------------------------------


async def test_download_rejects_bad_signature(client: httpx.AsyncClient) -> None:
    _uid, key = await create_user_with_credits()
    job = await _build_pack(client, key, _pack_body("a"))
    path = job["download_url"].split("http://localhost:8000", 1)[-1]
    tampered = path.replace("sig=", "sig=0")
    resp = await client.get(tampered)
    assert resp.status_code == 403


async def test_download_rejects_expired_signature(
    client: httpx.AsyncClient, engine_state: EngineState
) -> None:
    from pdf_api import signing

    _uid, key = await create_user_with_credits()
    job = await _build_pack(client, key, _pack_body("a"))
    expires, sig = signing.sign_download(engine_state.settings, job["id"], ttl_seconds=-10)
    resp = await client.get(f"/v1/jobs/{job['id']}/download?expires={expires}&sig={sig}")
    assert resp.status_code == 403
    assert "expired" in resp.json()["detail"]


async def test_non_completed_job_cannot_be_downloaded(
    client: httpx.AsyncClient, engine_state: EngineState
) -> None:
    """A validly signed link for a failed job must not yield output."""
    from pdf_api import signing

    _uid, key = await create_user_with_credits()

    async def boom(**_kwargs: object) -> None:
        raise RenderError("forced failure")

    engine_state.pool.render = boom  # type: ignore[assignment]
    headers = auth_headers(key)
    resp = await client.post("/v1/packs", json=_pack_body("a"), headers=headers)
    job = await _wait_for(client, resp.json()["id"], headers)
    assert job["status"] == "failed"

    # A correct signature is not enough: the job never produced output.
    expires, sig = signing.sign_download(engine_state.settings, job["id"])
    resp = await client.get(f"/v1/jobs/{job['id']}/download?expires={expires}&sig={sig}")
    assert resp.status_code == 404


async def test_missing_completed_output_returns_410(
    client: httpx.AsyncClient, engine_state: EngineState
) -> None:
    """A completed job whose stored PDF has since vanished returns 410 Gone."""
    from pdf_api import jobservice, signing

    _uid, key = await create_user_with_credits()
    job = await _build_pack(client, key, _pack_body("a"))

    # Delete the stored output out from under the (still valid) signed link.
    row = await jobservice.load_job_any_owner(job["id"])
    assert row is not None and row["output_r2_key"]
    await engine_state.storage.delete(str(row["output_r2_key"]))

    expires, sig = signing.sign_download(engine_state.settings, job["id"])
    resp = await client.get(f"/v1/jobs/{job['id']}/download?expires={expires}&sig={sig}")
    assert resp.status_code == 410


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


def test_toc_html_escapes_titles() -> None:
    html = packs.toc_html([("<script>alert(1)</script>", 3)])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_title_page_html_escapes_title() -> None:
    from datetime import UTC, datetime

    html = packs.title_page_html("<img onerror=x>", 2, datetime.now(UTC))
    assert "<img onerror" not in html


def test_pack_item_error_reports_index() -> None:
    err = packs.PackItemError(2, "Broken", "timeout")
    assert err.index == 2
    assert "item 3" in str(err)


@pytest.mark.parametrize(("count", "expected"), [(1, 3), (2, 4), (10, 12)])
def test_cost_scales_with_sources(count: int, expected: int) -> None:
    assert metering.cost_for_pack(count) == expected
