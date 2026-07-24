# Billing

CleanPDF uses Stripe Checkout for Starter and Pro subscriptions, a Stripe
customer portal for subscription management, and one-time Checkout for
500-credit overage packs.

## Required environment variables

```dotenv
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_STARTER_PRICE_ID=price_...
STRIPE_PRO_PRICE_ID=price_...
STRIPE_OVERAGE_PRICE_ID=price_...
BILLING_SUCCESS_URL=http://localhost:8000/usage?checkout=success
BILLING_CANCEL_URL=http://localhost:8000/usage?checkout=cancelled
BILLING_PORTAL_RETURN_URL=http://localhost:8000/usage
```

Never commit real values. Stripe remains the source of truth for subscription
state. PostgreSQL's credit ledger remains the source of truth for credits.

## Checkout

```bash
curl -X POST http://localhost:8000/v1/billing/checkout \
  -H "Authorization: Bearer $CLEANPDF_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"purchase":"starter"}'
```

Valid purchases are `starter`, `pro`, and `overage_500`.

## Customer portal

```bash
curl -X POST http://localhost:8000/v1/billing/portal \
  -H "Authorization: Bearer $CLEANPDF_API_KEY"
```

## Stripe webhook

Configure Stripe to send these events to
`POST /v1/billing/stripe-webhook`:

- `checkout.session.completed`
- `customer.subscription.updated`
- `customer.subscription.deleted`

The endpoint verifies the raw request body with `Stripe-Signature`. Duplicate
monthly and overage events are idempotent. At each paid billing-period advance,
unused credits expire before the new monthly allowance is granted.

## Test-mode acceptance

Before production, use Stripe test mode and a test clock to verify:

1. Starter Checkout creates a paid subscription.
2. The webhook changes the local plan to Starter and sets 1,500 credits.
3. Advancing the test clock one billing cycle resets unused credits to 1,500.
4. Replaying the same webhook does not add credits.
5. A $4 overage Checkout adds exactly 500 credits once.
6. Portal creation works for the Stripe customer.

This live test requires your Stripe test keys and is intentionally not run by
the local pytest suite.
