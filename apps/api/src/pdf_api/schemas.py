"""Pydantic v2 request/response models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

PageSize = Literal["A4", "Letter"]
WaitUntil = Literal["load", "networkidle"]
JobKind = Literal["html", "url", "pack_merge"]
JobStatus = Literal["queued", "rendering", "completed", "failed"]
BillingPurchase = Literal["starter", "pro", "overage_500"]

MAX_HTML_CHARS = 10 * 1024 * 1024
MAX_TITLE_CHARS = 300


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

    idempotency_key: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    webhook_url: str | None = None


class PackItem(BaseModel):
    """One pack source: either a ``url``, or ``html`` together with a ``title``."""

    url: str | None = None
    html: Annotated[str, Field(max_length=MAX_HTML_CHARS)] | None = None
    title: Annotated[str, Field(min_length=1, max_length=MAX_TITLE_CHARS)] | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> PackItem:
        if bool(self.html) == bool(self.url):
            raise ValueError("Each item needs exactly one of 'url' or 'html'.")
        if self.html and not self.title:
            raise ValueError("Items supplying 'html' must also supply 'title'.")
        return self

    @property
    def kind(self) -> Literal["html", "url"]:
        return "html" if self.html else "url"

    @property
    def source(self) -> str:
        value = self.html if self.html is not None else self.url
        assert value is not None
        return value

    def display_title(self, index: int) -> str:
        return self.title or self.url or f"Item {index + 1}"


class PackRequest(RenderOptions):
    """Reading-pack input. Item order is the rendered and merged order."""

    items: Annotated[list[PackItem], Field(min_length=1)]
    pack_title: Annotated[str, Field(min_length=1, max_length=MAX_TITLE_CHARS)]
    toc: bool = True
    title_page: bool = True
    idempotency_key: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    webhook_url: str | None = None


class JobResponse(BaseModel):
    id: uuid.UUID
    kind: JobKind
    status: JobStatus
    credits_charged: int
    output_bytes: int | None = None
    duration_ms: int | None = None
    failure_reason: str | None = None
    webhook_delivered: bool = False
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    # Additive: signed link, populated only once the job has completed.
    download_url: str | None = None


class UsageJob(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    credits_charged: int
    created_at: datetime


class UsageResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    plan_id: str | None
    subscription_status: str | None
    current_period_end: datetime | None
    monthly_credits: int | None
    balance: int
    jobs_total: int
    period_started_at: datetime | None
    credits_granted: int
    credits_purchased: int
    credits_spent: int
    credits_refunded: int
    credits_remaining: int
    recent_jobs: list[UsageJob]


class CheckoutRequest(BaseModel):
    purchase: BillingPurchase


class BillingLinkResponse(BaseModel):
    url: str


class WebhookSecretResponse(BaseModel):
    secret: str
