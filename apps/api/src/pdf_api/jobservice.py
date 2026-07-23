"""Job execution: render, persist output, update lifecycle, refund on failure."""

from __future__ import annotations

import asyncio
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


async def _claim_job(job_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Atomically move one queued job to rendering.

    Only the worker that performs this transition may render the job. Duplicate
    executions observe a non-queued status and return without doing work.
    """
    async with transaction() as conn:
        claimed = (
            await conn.execute(
                update(models.jobs)
                .where(
                    models.jobs.c.id == job_id,
                    models.jobs.c.user_id == user_id,
                    models.jobs.c.status == "queued",
                )
                .values(status="rendering", updated_at=datetime.now(UTC))
                .returning(models.jobs.c.id)
            )
        ).first()
    return claimed is not None


async def _delete_partial_output(engine: EngineState, storage_key: str) -> None:
    try:
        await engine.storage.delete(storage_key)
    except Exception as exc:  # pragma: no cover - best-effort cleanup
        log.warning("job.output_cleanup_failed", key=storage_key, error=str(exc))


async def execute_job(
    engine: EngineState,
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: str,
    source: str,
    options: RenderOptions,
) -> None:
    """Claim and execute a job once, or fail it and refund exactly once."""
    bind_request_context(job_id=str(job_id))
    claimed = False
    stage = "claim"
    storage_key = f"jobs/{job_id}.pdf"

    try:
        claimed = await _claim_job(job_id, user_id)
        if not claimed:
            log.info("job.duplicate_execution_skipped", job_id=str(job_id))
            return

        stage = "render"
        result = await engine.pool.render(kind=kind, source=source, options=options)

        stage = "storage"
        await engine.storage.put(storage_key, result.pdf, content_type="application/pdf")

        stage = "completion"
        await _set_status(
            job_id,
            status="completed",
            output_r2_key=storage_key,
            output_bytes=len(result.pdf),
            duration_ms=result.duration_ms,
            failure_reason=None,
        )
    except asyncio.CancelledError:
        if claimed:
            await _delete_partial_output(engine, storage_key)
            cleanup = asyncio.create_task(
                _fail_and_refund(job_id, user_id, reason=f"cancelled_during_{stage}")
            )
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
        raise
    except OutputTooLargeError as exc:
        await _delete_partial_output(engine, storage_key)
        await _fail_and_refund(job_id, user_id, reason=f"output_too_large: {exc}")
        return
    except RenderError as exc:
        await _delete_partial_output(engine, storage_key)
        await _fail_and_refund(job_id, user_id, reason=f"render_error: {exc}")
        return
    except Exception as exc:
        log.exception("job.unexpected_error", job_id=str(job_id), stage=stage)
        if claimed:
            await _delete_partial_output(engine, storage_key)
            await _fail_and_refund(job_id, user_id, reason=f"{stage}_error: {exc}")
        return

    log.info("job.completed", job_id=str(job_id), bytes=len(result.pdf))


async def _fail_and_refund(job_id: uuid.UUID, user_id: uuid.UUID, *, reason: str) -> None:
    async with transaction() as conn:
        row = (
            await conn.execute(
                select(models.jobs.c.status, models.jobs.c.credits_charged)
                .where(models.jobs.c.id == job_id, models.jobs.c.user_id == user_id)
                .with_for_update()
            )
        ).first()
        if row is None or row.status == "completed":
            return

        charged = int(row.credits_charged)
        values: dict[str, object] = {
            "status": "failed",
            "updated_at": datetime.now(UTC),
        }
        if row.status != "failed":
            values["failure_reason"] = reason
        await conn.execute(
            update(models.jobs).where(models.jobs.c.id == job_id).values(**values)
        )
        refunded = charged > 0 and await metering.refund_job(
            conn, user_id, job_id, charged
        )

    log.warning(
        "job.failed",
        job_id=str(job_id),
        reason=reason,
        refunded=charged if refunded else 0,
    )


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
