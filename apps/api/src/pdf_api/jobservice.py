"""Job execution: render, persist output, update lifecycle, refund on failure."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, update

from . import metering, models
from .config import Settings
from .db import connection, transaction
from .logging_config import bind_request_context, get_logger
from .renderer import BrowserPool, OutputTooLargeError, RenderError
from .schemas import RenderOptions
from .storage import StorageBackend

log = get_logger(__name__)


@dataclass
class EngineState:
    """Shared runtime wiring stored on ``app.state.engine``."""

    settings: Settings
    pool: BrowserPool
    storage: StorageBackend


async def _set_status(job_id: uuid.UUID, **values: object) -> None:
    async with transaction() as conn:
        await conn.execute(
            update(models.jobs)
            .where(models.jobs.c.id == job_id)
            .values(updated_at=datetime.now(UTC), **values)
        )


async def execute_job(
    engine: EngineState,
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: str,
    source: str,
    options: RenderOptions,
) -> None:
    """Render a queued job to completion (or failure + refund)."""
    bind_request_context(job_id=str(job_id))
    await _set_status(job_id, status="rendering")
    try:
        result = await engine.pool.render(kind=kind, source=source, options=options)
    except OutputTooLargeError as exc:
        await _fail_and_refund(job_id, user_id, reason=f"output_too_large: {exc}")
        return
    except RenderError as exc:
        await _fail_and_refund(job_id, user_id, reason=f"render_error: {exc}")
        return
    except Exception as exc:  # defensive: never leak a charge on a crash
        log.exception("job.unexpected_error", job_id=str(job_id))
        await _fail_and_refund(job_id, user_id, reason=f"internal_error: {exc}")
        return

    storage_key = f"jobs/{job_id}.pdf"
    await engine.storage.put(storage_key, result.pdf, content_type="application/pdf")
    await _set_status(
        job_id,
        status="completed",
        output_r2_key=storage_key,
        output_bytes=len(result.pdf),
        duration_ms=result.duration_ms,
    )
    log.info("job.completed", job_id=str(job_id), bytes=len(result.pdf))


async def _fail_and_refund(job_id: uuid.UUID, user_id: uuid.UUID, *, reason: str) -> None:
    async with transaction() as conn:
        row = (
            await conn.execute(
                select(models.jobs.c.credits_charged).where(models.jobs.c.id == job_id)
            )
        ).first()
        charged = int(row.credits_charged) if row else 0
        await conn.execute(
            update(models.jobs)
            .where(models.jobs.c.id == job_id)
            .values(status="failed", failure_reason=reason, updated_at=datetime.now(UTC))
        )
        if charged > 0:
            await metering.refund_job(conn, user_id, job_id, charged)
    log.warning("job.failed", job_id=str(job_id), reason=reason, refunded=charged)


async def load_job(job_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, object] | None:
    async with connection() as conn:
        row = (
            await conn.execute(
                select(models.jobs).where(
                    models.jobs.c.id == job_id, models.jobs.c.user_id == user_id
                )
            )
        ).first()
    return dict(row._mapping) if row is not None else None


async def usage_snapshot(user_id: uuid.UUID, email: str) -> dict[str, object]:
    async with connection() as conn:
        balance = await metering.get_balance(conn, user_id)
        jobs_total = int(
            (
                await conn.execute(
                    select(func.count()).select_from(models.jobs).where(
                        models.jobs.c.user_id == user_id
                    )
                )
            ).scalar_one()
        )
        plan_row = (
            await conn.execute(
                select(models.plans.c.id, models.plans.c.monthly_credits)
                .select_from(
                    models.subscriptions.join(
                        models.plans, models.subscriptions.c.plan_id == models.plans.c.id
                    )
                )
                .where(models.subscriptions.c.user_id == user_id)
                .limit(1)
            )
        ).first()
    return {
        "user_id": user_id,
        "email": email,
        "plan_id": plan_row.id if plan_row else None,
        "monthly_credits": plan_row.monthly_credits if plan_row else None,
        "balance": balance,
        "jobs_total": jobs_total,
    }
