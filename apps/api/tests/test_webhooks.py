from __future__ import annotations

import json
import time

import httpx
import pytest

from pdf_api import webhooks
from pdf_api.config import Settings


def test_outbound_signature_round_trip() -> None:
    payload = json.dumps({"event": "job.completed"}).encode()
    now = int(time.time())
    header = webhooks.signature_header("secret", payload, now)
    assert webhooks.verify_signature("secret", payload, header, now=now)
    assert not webhooks.verify_signature("wrong", payload, header, now=now)


def test_outbound_signature_rejects_clock_skew() -> None:
    payload = b"{}"
    header = webhooks.signature_header("secret", payload, 1_000)
    assert not webhooks.verify_signature(
        "secret",
        payload,
        header,
        now=1_301,
        tolerance_seconds=300,
    )


async def test_delivery_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    async def validate(_url: str) -> None:
        return None

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503 if attempts < 3 else 204)

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(webhooks, "validate_target", validate)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        delivered = await webhooks._post_with_retries(
            Settings(),
            url="https://example.com/hook",
            secret="secret",
            payload=b"{}",
            client=client,
            sleep=no_sleep,
        )
    assert delivered is True
    assert attempts == 3


async def test_usage_page_has_no_store_header(client: httpx.AsyncClient) -> None:
    response = await client.get("/usage")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert "CleanPDF usage" in response.text
