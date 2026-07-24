"""billing ledger idempotency

Revision ID: 0002_billing_ledger_idempotency
Revises: 0001_initial
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_billing_ledger_idempotency"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("credit_ledger", sa.Column("external_ref", sa.Text(), nullable=True))
    op.create_unique_constraint(
        "uq_credit_ledger_external_ref",
        "credit_ledger",
        ["user_id", "reason", "external_ref"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_credit_ledger_external_ref",
        "credit_ledger",
        type_="unique",
    )
    op.drop_column("credit_ledger", "external_ref")
