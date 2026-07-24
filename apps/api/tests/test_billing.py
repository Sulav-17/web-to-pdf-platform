from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from conftest import auth_headers, create_user_with_credits, get_balance
from sqlalchemy import select

from pdf_api import billing, models
from pdf_api.auth import Principal
from pdf_api.config import Settings
from pdf_api.db import get_engine
from pdf_api.jobservice import EngineState
from pdf_api.routes import _render_options
from pdf_api.schemas import ConvertRequest


def subscription_object(
    *,
    user_id: uuid.UUID,
    period_end: datetime,
    plan: str = "starter",
    status: str = "active",
) -> dict[str, Any]:
    price = "price_starter" if plan == "starter" else "price_pro"
    return {
        "id": "sub_test",
        "customer": "cus_test",
        "status": status,
        "metadata": {"user_id": str(user_id), "purchase": plan},
        "items": {
            "data": [
                {
                    "current_period_end": int(period_end.timestamp()),
                    "price": {"id": price},
                }
            ]
        },
    }


def billing_settings() -> Settings:
    return Settings(
        stripe_secret_key="sk_test_fake",
        stripe_webhook_secret="whsec_fake",
        stripe_starter_price_id="price_starter",
        stripe_pro_price_id="price_pro",
        stripe_overage_price_id="price_overage",
    )


def test_free_plan_gets_watermark_and_paid_plan_does_not() -> None:
    request = ConvertRequest(html="<p>hello</p>")
    assert "Made with CleanPDF" in (_render_options(request, "free").footer_template or "")
    assert _render_options(request, "starter").footer_template is None


async def test_subscription_checkout_uses_configured_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, _key = await create_user_with_credits()
    captured: dict[str, Any] = {}

    class Sessions:
        async def create_async(self, params: dict[str, Any]) -> Any:
            captured.update(params)
            return SimpleNamespace(url="https://checkout.stripe.test/session")

    fake = SimpleNamespace(v1=SimpleNamespace(checkout=SimpleNamespace(sessions=Sessions())))
    monkeypatch.setattr(billing, "stripe_client", lambda _settings: fake)
    url = await billing.create_checkout_session(
        billing_settings(),
        Principal(
            user_id=user_id,
            api_key_id=uuid.uuid4(),
            email="buyer@example.com",
            plan_id="free",
        ),
        "starter",
    )
    assert url == "https://checkout.stripe.test/session"
    assert captured["mode"] == "subscription"
    assert captured["line_items"] == [{"price": "price_starter", "quantity": 1}]
    assert captured["metadata"]["user_id"] == str(user_id)


async def test_checkout_requires_configuration(
    client: httpx.AsyncClient,
    engine_state: EngineState,
) -> None:
    engine_state.settings.stripe_secret_key = None
    engine_state.settings.stripe_starter_price_id = None
    _user_id, key = await create_user_with_credits()
    response = await client.post(
        "/v1/billing/checkout",
        json={"purchase": "starter"},
        headers=auth_headers(key),
    )
    assert response.status_code == 503


async def test_subscription_grant_is_idempotent_and_non_rolling() -> None:
    user_id, _key = await create_user_with_credits(credits=42)
    settings = billing_settings()
    period_end = datetime.now(UTC) + timedelta(days=30)
    subscription = subscription_object(user_id=user_id, period_end=period_end)

    assert await billing.apply_subscription(settings, subscription) is True
    assert await get_balance(user_id) == 1500
    assert await billing.apply_subscription(settings, subscription) is False
    assert await get_balance(user_id) == 1500


async def test_subscription_renewal_resets_unused_credits() -> None:
    user_id, _key = await create_user_with_credits(credits=75)
    settings = billing_settings()
    first_end = datetime.now(UTC) + timedelta(days=30)
    first = subscription_object(user_id=user_id, period_end=first_end)
    await billing.apply_subscription(settings, first)

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            models.credit_ledger.insert().values(
                user_id=user_id,
                delta=-200,
                reason="conversion",
            )
        )
    await engine.dispose()
    assert await get_balance(user_id) == 1300

    renewal = subscription_object(
        user_id=user_id,
        period_end=first_end + timedelta(days=30),
    )
    assert await billing.apply_subscription(settings, renewal) is True
    assert await get_balance(user_id) == 1500


async def test_overage_checkout_grants_once() -> None:
    user_id, _key = await create_user_with_credits(credits=10)
    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_overage_1",
                "payment_status": "paid",
                "metadata": {
                    "user_id": str(user_id),
                    "purchase": "overage_500",
                },
            }
        },
    }
    await billing.handle_stripe_event(billing_settings(), event)
    await billing.handle_stripe_event(billing_settings(), event)
    assert await get_balance(user_id) == 510


async def test_deleted_subscription_downgrades_to_free() -> None:
    user_id, _key = await create_user_with_credits()
    settings = billing_settings()
    subscription = subscription_object(
        user_id=user_id,
        period_end=datetime.now(UTC) + timedelta(days=30),
    )
    await billing.apply_subscription(settings, subscription)
    await billing.handle_stripe_event(
        settings,
        {
            "type": "customer.subscription.deleted",
            "data": {"object": subscription},
        },
    )
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(
                    models.subscriptions.c.plan_id,
                    models.subscriptions.c.status,
                ).where(models.subscriptions.c.user_id == user_id)
            )
        ).one()
    await engine.dispose()
    assert row.plan_id == "free"
    assert row.status == "canceled"


async def test_checkout_completed_retrieves_subscription() -> None:
    user_id, _key = await create_user_with_credits(credits=0)
    subscription = subscription_object(
        user_id=user_id,
        period_end=datetime.now(UTC) + timedelta(days=30),
    )

    class Subscriptions:
        async def retrieve_async(self, _sub_id: str) -> Any:
            return SimpleNamespace(to_dict_recursive=lambda: subscription)

    client = SimpleNamespace(
        v1=SimpleNamespace(subscriptions=Subscriptions()),
    )
    await billing.handle_stripe_event(
        billing_settings(),
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "subscription": "sub_test",
                    "metadata": {
                        "user_id": str(user_id),
                        "purchase": "starter",
                    },
                }
            },
        },
        client=client,
    )
    assert await get_balance(user_id) == 1500
