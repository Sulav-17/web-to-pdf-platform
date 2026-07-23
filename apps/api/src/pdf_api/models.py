"""SQLAlchemy Core table definitions (schema source of truth).

The Alembic migration in ``apps/api/alembic/versions`` is authored to match
these tables exactly. Tests build the schema from this metadata against a real
PostgreSQL instance.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

metadata = MetaData()


users = Table(
    "users",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("email", Text, nullable=False, unique=True),
    Column("email_verified", Boolean, nullable=False, server_default="false"),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
)

api_keys = Table(
    "api_keys",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("key_hash", Text, nullable=False, unique=True),
    Column("label", Text, nullable=False),
    Column("active", Boolean, nullable=False, server_default="true"),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("last_used_at", TIMESTAMP(timezone=True), nullable=True),
)

plans = Table(
    "plans",
    metadata,
    Column("id", Text, primary_key=True),
    Column("monthly_credits", Integer, nullable=False),
    Column("price_cents", Integer, nullable=False),
)

subscriptions = Table(
    "subscriptions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("plan_id", Text, ForeignKey("plans.id"), nullable=False),
    Column("stripe_customer_id", Text, nullable=True),
    Column("stripe_sub_id", Text, nullable=True),
    Column("status", Text, nullable=False, server_default="active"),
    Column("current_period_end", TIMESTAMP(timezone=True), nullable=True),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("api_key_id", UUID(as_uuid=True), ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True),
    Column("kind", Text, nullable=False),  # "html" | "url"
    Column("status", Text, nullable=False),  # queued|rendering|completed|failed
    Column("idempotency_key", Text, nullable=True),
    Column("input_hash", Text, nullable=False),
    Column("options", JSONB, nullable=False),
    Column("credits_charged", Integer, nullable=False, server_default="0"),
    Column("output_r2_key", Text, nullable=True),
    Column("output_bytes", Integer, nullable=True),
    Column("duration_ms", Integer, nullable=True),
    Column("failure_reason", Text, nullable=True),
    Column("webhook_url", Text, nullable=True),
    Column("webhook_delivered", Boolean, nullable=False, server_default="false"),
    Column("expires_at", TIMESTAMP(timezone=True), nullable=True),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
)

credit_ledger = Table(
    "credit_ledger",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("delta", Integer, nullable=False),
    Column("reason", Text, nullable=False),  # see metering.LedgerReason
    Column("job_id", UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
)

webhook_secrets = Table(
    "webhook_secrets",
    metadata,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("secret", Text, nullable=False),
)

# String column type re-exported for callers that prefer VARCHAR helpers.
__all__ = [
    "metadata",
    "users",
    "api_keys",
    "plans",
    "subscriptions",
    "jobs",
    "credit_ledger",
    "webhook_secrets",
]
