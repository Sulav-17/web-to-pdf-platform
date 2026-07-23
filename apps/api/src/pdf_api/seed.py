"""Canonical plan seed data, shared by the migration and bootstrap."""

from __future__ import annotations

from typing import Final

PLAN_SEED: Final[list[dict[str, object]]] = [
    {"id": "free", "monthly_credits": 75, "price_cents": 0},
    {"id": "starter", "monthly_credits": 1500, "price_cents": 1200},
    {"id": "pro", "monthly_credits": 6000, "price_cents": 3900},
]

FREE_PLAN_ID: Final = "free"
FREE_GRANT_CREDITS: Final = 75
