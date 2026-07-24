"""HTTP routes for rendering, jobs, usage, billing, and webhooks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Coroutine
from typing import Any

import stripe
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from . import billing, jobservice, metering, webhooks
from .auth import Principal, PrincipalDep
from .db import transaction
from .logging_config import get_logger
from .schemas import (
    BillingLinkResponse,
    CheckoutRequest,
    ConvertRequest,
    JobCreateRequest,
    JobResponse,
    RenderOptions,
    UsageResponse,
    WebhookSecretResponse,
)
from .security import SecurityBoundaryError, validate_target
from .usage_page import response as usage_page_response

router = APIRouter()
log = get_logger(__name__)

_background_tasks: set[asyncio.Task[None]] = set()
FREE_WATERMARK = '<div style="font-size:8px;width:100%;text-align:center;color:#64748b;">Made with CleanPDF</div>'


def _task_finished(task: asyncio.Task[None]) -> None:
    _background_tasks.discard(task)
    if not task.cancelled() and (error := task.exception()) is not None:
        log.error("job.background_task_failed", error=str(error))


def _start_job_task(coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_task_finished)
    return task


async def drain_background_tasks(grace_seconds: float) -> None:
    tasks = tuple(_background_tasks)
    if not tasks:
        return
    _done, pending = await asyncio.wait(tasks, timeout=grace_seconds)
    if not pending:
        return
    log.warning("app.background_tasks_cancelling", count=len(pending))
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


def _engine(request: Request) -> jobservice.EngineState:
    engine: jobservice.EngineState = request.app.state.engine
    return engine


def _render_options(req: ConvertRequest, plan_id: str) -> RenderOptions:
    options = RenderOptions.model_validate(req.model_dump(include=set(RenderOptions.model_fields)))
    if plan_id == "free":
        options.footer_template = (
            f"{options.footer_template}{FREE_WATERMARK}" if options.footer_template else FREE_WATERMARK
        )
    return options


def _input_hash(kind: str, source: str, options: RenderOptions) -> str:
    payload = json.dumps(
        {"kind": kind, "source": source, "options": options.model_dump()},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _guard_input(req: ConvertRequest, engine: jobservice.EngineState) -> None:
    if req.html is not None and len(req.html.encode("utf-8")) > engine.settings.max_html_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"html exceeds {engine.settings.max_html_bytes} bytes",
        )
    if req.kind == "url" and not engine.settings.enable_url_rendering:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="URL rendering is disabled (ENABLE_URL_RENDERING=false).",
        )


async def _charge(
    principal: Principal,
    req: ConvertRequest,
    engine: jobservice.EngineState,
    *,
    idempotency_key: str | None = None,
    webhook_url: str | None = None,
) -> tuple[uuid.UUID, bool, RenderOptions, str]:
    options = _render_options(req, principal.plan_id)
    source = req.html if req.html is not None else req.url
    assert source is not None
    cost = metering.cost_for_kind(req.kind)
    expires_at = metering.default_expiry(engine.settings.output_ttl_seconds)
    input_hash = _input_hash(req.kind, source, options)

    if webhook_url is not None:
        try:
            await validate_target(webhook_url)
        except SecurityBoundaryError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Blocked webhook target: {exc}",
            ) from exc

    try:
        async with transaction() as conn:
            charged = await metering.create_job_charged(
                conn,
                user_id=principal.user_id,
                api_key_id=principal.api_key_id,
                kind=req.kind,
                input_hash=input_hash,
                options=options.model_dump(),
                cost=cost,
                expires_at=expires_at,
                idempotency_key=idempotency_key,
                webhook_url=webhook_url,
            )
    except metering.InsufficientCreditsError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Insufficient credits: have {exc.balance}, need {exc.required}",
        ) from exc

    return charged.id, charged.reused, options, source


def _job_response(row: dict[str, Any]) -> JobResponse:
    return JobResponse.model_validate(row)


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, Any]:
    engine = _engine(request)
    connected = engine.pool._current is not None and engine.pool._current.browser.is_connected()
    return {"status": "ok", "browser_connected": connected}


@router.get("/usage")
async def usage_page() -> Response:
    return usage_page_response()


@router.post("/v1/convert")
async def convert(
    request: Request,
    req: ConvertRequest,
    principal: Principal = PrincipalDep,
) -> Response:
    engine = _engine(request)
    _guard_input(req, engine)
    job_id, _reused, options, source = await _charge(principal, req, engine)
    task = _start_job_task(
        jobservice.execute_job(
            engine,
            job_id=job_id,
            user_id=principal.user_id,
            kind=req.kind,
            source=source,
            options=options,
        )
    )
    timed_out = False
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=engine.settings.sync_wait_seconds)
    except TimeoutError:
        timed_out = True

    job = await jobservice.load_job(job_id, principal.user_id)
    assert job is not None
    if timed_out and job["status"] in {"queued", "rendering"}:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=_job_response(job).model_dump(mode="json"),
            headers={"X-Job-Id": str(job_id)},
        )
    if job["status"] != "completed":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Render failed: {job.get('failure_reason')}",
        )
    pdf = await engine.storage.get(str(job["output_r2_key"]))
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "X-Job-Id": str(job_id),
            "X-Credits-Charged": str(job["credits_charged"]),
        },
    )


@router.post("/v1/jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    request: Request,
    req: JobCreateRequest,
    response: Response,
    principal: Principal = PrincipalDep,
) -> JobResponse:
    engine = _engine(request)
    _guard_input(req, engine)
    job_id, reused, options, source = await _charge(
        principal,
        req,
        engine,
        idempotency_key=req.idempotency_key,
        webhook_url=req.webhook_url,
    )
    if reused:
        response.status_code = status.HTTP_200_OK
        existing = await jobservice.load_job(job_id, principal.user_id)
        assert existing is not None
        return _job_response(existing)

    job = await jobservice.load_job(job_id, principal.user_id)
    assert job is not None
    _start_job_task(
        jobservice.execute_job(
            engine,
            job_id=job_id,
            user_id=principal.user_id,
            kind=req.kind,
            source=source,
            options=options,
        )
    )
    return _job_response(job)


@router.get("/v1/jobs/{job_id}")
async def get_job(job_id: uuid.UUID, principal: Principal = PrincipalDep) -> JobResponse:
    job = await jobservice.load_job(job_id, principal.user_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return _job_response(job)


@router.get("/v1/usage")
async def usage(principal: Principal = PrincipalDep) -> UsageResponse:
    snapshot = await jobservice.usage_snapshot(principal.user_id, principal.email)
    return UsageResponse.model_validate(snapshot)


@router.post("/v1/billing/checkout")
async def billing_checkout(
    request: Request,
    req: CheckoutRequest,
    principal: Principal = PrincipalDep,
) -> BillingLinkResponse:
    try:
        url = await billing.create_checkout_session(
            _engine(request).settings,
            principal,
            req.purchase,
        )
    except billing.BillingNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except (billing.BillingStateError, stripe.StripeError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return BillingLinkResponse(url=url)


@router.post("/v1/billing/portal")
async def billing_portal(
    request: Request,
    principal: Principal = PrincipalDep,
) -> BillingLinkResponse:
    try:
        url = await billing.create_portal_session(_engine(request).settings, principal)
    except billing.BillingNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except billing.BillingStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except stripe.StripeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return BillingLinkResponse(url=url)


@router.post("/v1/billing/stripe-webhook")
async def stripe_webhook(request: Request) -> dict[str, bool]:
    settings = _engine(request).settings
    try:
        event = billing.verify_stripe_event(
            settings,
            await request.body(),
            request.headers.get("Stripe-Signature"),
        )
        await billing.handle_stripe_event(settings, event)
    except billing.BillingNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe webhook signature",
        ) from exc
    except billing.BillingStateError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return {"received": True}


@router.post("/v1/webhooks/secret")
async def rotate_webhook_secret(
    principal: Principal = PrincipalDep,
) -> WebhookSecretResponse:
    return WebhookSecretResponse(secret=await webhooks.rotate_secret(principal.user_id))
