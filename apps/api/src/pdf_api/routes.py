"""HTTP routes: health, conversion, jobs, and usage."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Coroutine
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from . import jobservice, metering
from .auth import Principal, PrincipalDep
from .db import transaction
from .logging_config import get_logger
from .schemas import ConvertRequest, JobCreateRequest, JobResponse, RenderOptions, UsageResponse

router = APIRouter()
log = get_logger(__name__)

_background_tasks: set[asyncio.Task[None]] = set()


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
    """Wait for active jobs, then cancel and drain any that exceed the grace period."""
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


def _render_options(req: ConvertRequest) -> RenderOptions:
    return RenderOptions.model_validate(req.model_dump(include=set(RenderOptions.model_fields)))


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
            detail=(
                "URL rendering is disabled (ENABLE_URL_RENDERING=false). "
                "Full SSRF protection is handled by Task A-SEC."
            ),
        )


async def _charge(
    principal: Principal,
    req: ConvertRequest,
    engine: jobservice.EngineState,
    *,
    idempotency_key: str | None = None,
    webhook_url: str | None = None,
) -> tuple[uuid.UUID, bool, RenderOptions, str]:
    options = _render_options(req)
    source = req.html if req.html is not None else req.url
    assert source is not None
    kind = req.kind
    cost = metering.cost_for_kind(kind)
    expires_at = metering.default_expiry(engine.settings.output_ttl_seconds)
    input_hash = _input_hash(kind, source, options)

    try:
        async with transaction() as conn:
            charged = await metering.create_job_charged(
                conn,
                user_id=principal.user_id,
                api_key_id=principal.api_key_id,
                kind=kind,
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


@router.post("/v1/convert")
async def convert(
    request: Request,
    req: ConvertRequest,
    principal: Principal = PrincipalDep,
) -> Response:
    """Return a fast render inline, or fall back to a 202 job after 20 seconds."""
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
        await asyncio.wait_for(
            asyncio.shield(task), timeout=engine.settings.sync_wait_seconds
        )
    except TimeoutError:
        timed_out = True

    job = await jobservice.load_job(job_id, principal.user_id)
    assert job is not None
    if timed_out and job["status"] in {"queued", "rendering"}:
        payload = _job_response(job).model_dump(mode="json")
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=payload,
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
