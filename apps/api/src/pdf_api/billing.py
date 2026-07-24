"""Stripe Checkout, portal, signature verification, and ledger reconciliation."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import stripe
from sqlalchemy import insert, select, update
from stripe.params.checkout import SessionCreateParams

from . import metering, models
from .auth import Principal
from .config import Settings
from .db import connection, transaction

OVERAGE_CREDITS = 500
PAID_PLANS = frozenset({"starter", "pro"})
ACTIVE_STATUSES = frozenset({"active", "trialing"})


class BillingNotConfiguredError(RuntimeError):
    """Required Stripe settings are missing."""


class BillingStateError(RuntimeError):
    """A signed billing event could not be mapped to local state."""


def stripe_client(settings: Settings) -> stripe.StripeClient:
    if not settings.stripe_secret_key:
        raise BillingNotConfiguredError("STRIPE_SECRET_KEY is not configured")
    return stripe.StripeClient(settings.stripe_secret_key)


def _price_for_purchase(settings: Settings, purchase: str) -> str:
    prices = {
        "starter": settings.stripe_starter_price_id,
        "pro": settings.stripe_pro_price_id,
        "overage_500": settings.stripe_overage_price_id,
    }
    price_id = prices.get(purchase)
    if not price_id:
        raise BillingNotConfiguredError(f"Stripe price is not configured for {purchase}")
    return price_id


def _plan_for_price(settings: Settings, price_id: str | None) -> str | None:
    if price_id and price_id == settings.stripe_starter_price_id:
        return "starter"
    if price_id and price_id == settings.stripe_pro_price_id:
        return "pro"
    return None


async def _customer_id_for_user(user_id: uuid.UUID) -> str | None:
    async with connection() as conn:
        value = (
            await conn.execute(
                select(models.subscriptions.c.stripe_customer_id)
                .where(models.subscriptions.c.user_id == user_id)
                .limit(1)
            )
        ).scalar_one_or_none()
    return str(value) if value else None


async def create_checkout_session(
    settings: Settings,
    principal: Principal,
    purchase: str,
) -> str:
    client = stripe_client(settings)
    price_id = _price_for_purchase(settings, purchase)
    metadata = {
        "user_id": str(principal.user_id),
        "purchase": purchase,
    }
    params: SessionCreateParams = {
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": settings.billing_success_url,
        "cancel_url": settings.billing_cancel_url,
        "client_reference_id": str(principal.user_id),
        "metadata": metadata,
    }
    customer_id = await _customer_id_for_user(principal.user_id)
    if customer_id:
        params["customer"] = customer_id
    else:
        params["customer_email"] = principal.email

    if purchase in PAID_PLANS:
        params["mode"] = "subscription"
        params["subscription_data"] = {"metadata": metadata}
    else:
        params["mode"] = "payment"
        params["payment_intent_data"] = {"metadata": metadata}

    session = await client.v1.checkout.sessions.create_async(params)
    if not session.url:
        raise BillingStateError("Stripe Checkout did not return a URL")
    return str(session.url)


async def create_portal_session(settings: Settings, principal: Principal) -> str:
    customer_id = await _customer_id_for_user(principal.user_id)
    if not customer_id:
        raise BillingStateError("No Stripe customer exists for this account")
    session = await stripe_client(settings).v1.billing_portal.sessions.create_async(
        {
            "customer": customer_id,
            "return_url": settings.billing_portal_return_url,
        }
    )
    if not session.url:
        raise BillingStateError("Stripe portal did not return a URL")
    return str(session.url)


def verify_stripe_event(
    settings: Settings,
    payload: bytes,
    signature: str | None,
) -> dict[str, Any]:
    if not settings.stripe_webhook_secret:
        raise BillingNotConfiguredError("STRIPE_WEBHOOK_SECRET is not configured")
    if not signature:
        raise ValueError("Missing Stripe-Signature header")
    event = stripe.Webhook.construct_event(
        payload,
        signature,
        settings.stripe_webhook_secret,
        tolerance=settings.webhook_signature_tolerance_seconds,
    )
    return dict(event.to_dict_recursive())


def _metadata(obj: Mapping[str, Any]) -> Mapping[str, Any]:
    value = obj.get("metadata")
    return value if isinstance(value, Mapping) else {}


def _as_uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except (ValueError, TypeError):
        return None


def _subscription_item(subscription: Mapping[str, Any]) -> Mapping[str, Any]:
    items = subscription.get("items")
    if not isinstance(items, Mapping):
        return {}
    data = items.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], Mapping):
        return {}
    return data[0]


def _period_end(subscription: Mapping[str, Any]) -> datetime | None:
    item = _subscription_item(subscription)
    raw = item.get("current_period_end")
    if not isinstance(raw, (int, float)):
        return None
    return datetime.fromtimestamp(raw, tz=UTC)


def _subscription_price(subscription: Mapping[str, Any]) -> str | None:
    price = _subscription_item(subscription).get("price")
    if isinstance(price, Mapping) and price.get("id"):
        return str(price["id"])
    return None


async def _user_for_subscription(
    subscription: Mapping[str, Any],
) -> uuid.UUID | None:
    from_metadata = _as_uuid(_metadata(subscription).get("user_id"))
    if from_metadata is not None:
        return from_metadata
    sub_id = subscription.get("id")
    customer_id = subscription.get("customer")
    async with connection() as conn:
        row = (
            await conn.execute(
                select(models.subscriptions.c.user_id)
                .where(
                    (models.subscriptions.c.stripe_sub_id == str(sub_id))
                    | (models.subscriptions.c.stripe_customer_id == str(customer_id))
                )
                .limit(1)
            )
        ).first()
    return row.user_id if row else None


async def apply_subscription(
    settings: Settings,
    subscription: Mapping[str, Any],
    *,
    user_id: uuid.UUID | None = None,
) -> bool:
    user_id = user_id or await _user_for_subscription(subscription)
    if user_id is None:
        raise BillingStateError("Stripe subscription could not be mapped to a user")

    sub_id = str(subscription.get("id") or "")
    customer_id = str(subscription.get("customer") or "")
    status = str(subscription.get("status") or "incomplete")
    period_end = _period_end(subscription)
    plan_id = str(_metadata(subscription).get("purchase") or "")
    if plan_id not in PAID_PLANS:
        plan_id = _plan_for_price(settings, _subscription_price(subscription)) or ""
    if plan_id not in PAID_PLANS:
        raise BillingStateError("Stripe subscription price is not mapped to a local plan")

    async with transaction() as conn:
        row = (
            await conn.execute(
                select(models.subscriptions).where(models.subscriptions.c.user_id == user_id).with_for_update()
            )
        ).first()
        old_period_end = row.current_period_end if row else None
        values = {
            "plan_id": plan_id,
            "stripe_customer_id": customer_id or None,
            "stripe_sub_id": sub_id or None,
            "status": status,
            "current_period_end": period_end,
        }
        if row is None:
            await conn.execute(
                insert(models.subscriptions).values(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    **values,
                )
            )
        else:
            await conn.execute(
                update(models.subscriptions).where(models.subscriptions.c.user_id == user_id).values(**values)
            )

        granted = False
        if (
            status in ACTIVE_STATUSES
            and period_end is not None
            and (old_period_end is None or period_end > old_period_end)
        ):
            credits = int(
                (
                    await conn.execute(select(models.plans.c.monthly_credits).where(models.plans.c.id == plan_id))
                ).scalar_one()
            )
            granted = await metering.reset_and_grant_monthly_credits(
                conn,
                user_id=user_id,
                amount=credits,
                period_ref=f"{sub_id}:{int(period_end.timestamp())}",
            )
    return granted


async def _apply_overage(session: Mapping[str, Any]) -> bool:
    user_id = _as_uuid(_metadata(session).get("user_id"))
    session_id = str(session.get("id") or "")
    if user_id is None or not session_id:
        raise BillingStateError("Overage Checkout session is missing local metadata")
    if str(session.get("payment_status")) != "paid":
        return False
    async with transaction() as conn:
        return await metering.grant_credits(
            conn,
            user_id,
            OVERAGE_CREDITS,
            reason=metering.REASON_OVERAGE_PURCHASE,
            external_ref=f"checkout:{session_id}",
        )


async def _cancel_subscription(subscription: Mapping[str, Any]) -> None:
    user_id = await _user_for_subscription(subscription)
    if user_id is None:
        raise BillingStateError("Deleted Stripe subscription could not be mapped to a user")
    async with transaction() as conn:
        await conn.execute(
            update(models.subscriptions)
            .where(models.subscriptions.c.user_id == user_id)
            .values(
                plan_id="free",
                status="canceled",
                stripe_sub_id=str(subscription.get("id") or "") or None,
                stripe_customer_id=str(subscription.get("customer") or "") or None,
                current_period_end=_period_end(subscription),
            )
        )


async def handle_stripe_event(
    settings: Settings,
    event: Mapping[str, Any],
    *,
    client: Any | None = None,
) -> None:
    event_type = str(event.get("type") or "")
    data = event.get("data")
    obj = data.get("object") if isinstance(data, Mapping) else None
    if not isinstance(obj, Mapping):
        raise BillingStateError("Stripe event data.object is missing")

    if event_type == "checkout.session.completed":
        purchase = str(_metadata(obj).get("purchase") or "")
        if purchase == "overage_500":
            await _apply_overage(obj)
            return
        if purchase in PAID_PLANS and obj.get("subscription"):
            active_client = client or stripe_client(settings)
            subscription = await active_client.v1.subscriptions.retrieve_async(str(obj["subscription"]))
            subscription_data = (
                subscription.to_dict_recursive()
                if hasattr(subscription, "to_dict_recursive")
                else subscription
            )
            if not isinstance(subscription_data, Mapping):
                raise BillingStateError("Stripe subscription response is not a mapping")
            await apply_subscription(
                settings,
                subscription_data,
                user_id=_as_uuid(_metadata(obj).get("user_id")),
            )
        return

    if event_type == "customer.subscription.updated":
        await apply_subscription(settings, obj)
        return

    if event_type == "customer.subscription.deleted":
        await _cancel_subscription(obj)
