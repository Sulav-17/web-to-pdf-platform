"""Pydantic v2 request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

PageSize = Literal["A4", "Letter"]
WaitUntil = Literal["load", "networkidle"]
JobKind = Literal["html", "url"]
JobStatus = Literal["queued", "rendering", "completed", "failed"]

# 10 MB ceiling on inbound HTML, enforced structurally by Pydantic and again
# (byte-accurate) in the route so oversize payloads return HTTP 413.
MAX_HTML_CHARS = 10 * 1024 * 1024


class RenderOptions(BaseModel):
    """Rendering knobs shared by convert + job creation."""

    page_size: PageSize = "A4"
    margin_mm: Annotated[int, Field(ge=0, le=50)] = 12
    landscape: bool = False
    scale: Annotated[float, Field(ge=0.5, le=2.0)] = 1.0
    wait_until: WaitUntil = "load"
    timeout_ms: Annotated[int, Field(ge=1, le=15_000)] = 15_000
    header_template: str | None = None
    footer_template: str | None = None


class ConvertRequest(RenderOptions):
    """Conversion input: exactly one of ``html`` or ``url``."""

    html: Annotated[str, Field(max_length=MAX_HTML_CHARS)] | None = None
    url: str | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> ConvertRequest:
        if bool(self.html) == bool(self.url):
            raise ValueError("Provide exactly one of 'html' or 'url'.")
        return self

    @property
    def kind(self) -> JobKind:
        return "html" if self.html else "url"


class JobCreateRequest(ConvertRequest):
    """Async job creation adds idempotency + webhook fields."""

    idempotency_key: str | None = None
    webhook_url: str | None = None


class JobResponse(BaseModel):
    id: uuid.UUID
    kind: JobKind
    status: JobStatus
    credits_charged: int
    output_bytes: int | None = None
    duration_ms: int | None = None
    failure_reason: str | None = None
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class UsageResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    plan_id: str | None
    monthly_credits: int | None
    balance: int
    jobs_total: int
