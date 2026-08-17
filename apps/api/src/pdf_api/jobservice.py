"""Job execution, persistence, lifecycle, refunds, usage, and callbacks."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, update

from . import metering, models, packs
from .config import Settings
from .db import connection, transaction
from .logging_config import bind_request_context, get_logger
from .renderer import BrowserPool, OutputTooLargeError, RenderError, RenderResult
from .schemas import PackItem, RenderOptions
from .storage import StorageBackend
from .webhooks import deliver_job_webhook

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
            update(models.jobs).where(models.jobs.c.id == job_id).values(updated_at=datetime.now(UTC), **values)
        )


async def _claim_job(job_id: uuid.UUID, user_id: uuid.UUID) -> bool:
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
    except Exception as exc:  # pragma: no cover
        log.warning("job.output_cleanup_failed", key=storage_key, error=str(exc))


async def _deliver_callback(engine: EngineState, job_id: uuid.UUID, user_id: uuid.UUID) -> None:
    try:
        await deliver_job_webhook(engine.settings, job_id, user_id)
    except Exception as exc:  # pragma: no cover - callback failure must not change job state
        log.warning("job.webhook_delivery_failed", job_id=str(job_id), error=str(exc))


async def _run_job(
    engine: EngineState,
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    produce: Callable[[], Awaitable[RenderResult]],
) -> None:
    """Claim and execute a job once, or fail it and refund exactly once.

    ``produce`` renders the finished PDF. Single conversions and reading packs
    share this one lifecycle so there is exactly one refund and status path.
    """
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
        result = await produce()
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
            cleanup = asyncio.create_task(_fail_and_refund(job_id, user_id, reason=f"cancelled_during_{stage}"))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
            await _deliver_callback(engine, job_id, user_id)
        raise
    except OutputTooLargeError as exc:
        await _delete_partial_output(engine, storage_key)
        await _fail_and_refund(job_id, user_id, reason=f"output_too_large: {exc}")
        await _deliver_callback(engine, job_id, user_id)
        return
    except RenderError as exc:
        await _delete_partial_output(engine, storage_key)
        await _fail_and_refund(job_id, user_id, reason=f"render_error: {exc}")
        await _deliver_callback(engine, job_id, user_id)
        return
    except Exception as exc:
        log.exception("job.unexpected_error", job_id=str(job_id), stage=stage)
        if claimed:
            await _delete_partial_output(engine, storage_key)
            await _fail_and_refund(job_id, user_id, reason=f"{stage}_error: {exc}")
            await _deliver_callback(engine, job_id, user_id)
        return

    await _deliver_callback(engine, job_id, user_id)
    log.info("job.completed", job_id=str(job_id), bytes=len(result.pdf))


async def execute_job(
    engine: EngineState,
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: str,
    source: str,
    options: RenderOptions,
) -> None:
    """Render one HTML/URL source into a single-document job."""

    async def produce() -> RenderResult:
        return await engine.pool.render(kind=kind, source=source, options=options)

    await _run_job(engine, job_id=job_id, user_id=user_id, produce=produce)


async def execute_pack_job(
    engine: EngineState,
    *,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
    items: list[PackItem],
    options: RenderOptions,
    pack_title: str,
    toc: bool,
    title_page: bool,
) -> None:
    """Render every pack source in order and merge into one bookmarked PDF."""

    async def produce() -> RenderResult:
        started = time.monotonic()
        try:
            result = await packs.build_pack(
                engine.pool,
                items=items,
                options=options,
                pack_title=pack_title,
                toc=toc,
                title_page=title_page,
                max_output_bytes=engine.settings.max_output_bytes,
            )
        except packs.PackTooLargeError as exc:
            # Reuse the existing oversize failure/refund path.
            raise OutputTooLargeError(str(exc)) from exc
        except packs.PackError as exc:
            raise RenderError(str(exc)) from exc
        log.info(
            "pack.built",
            job_id=str(job_id),
            items=result.item_count,
            pages=result.total_pages,
        )
        return RenderResult(pdf=result.pdf, duration_ms=int((time.monotonic() - started) * 1000))

    await _run_job(engine, job_id=job_id, user_id=user_id, produce=produce)


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
        await conn.execute(update(models.jobs).where(models.jobs.c.id == job_id).values(**values))
        refunded = charged > 0 and await metering.refund_job(conn, user_id, job_id, charged)

    log.warning(
        "job.failed",
        job_id=str(job_id),
        reason=reason,
        refunded=charged if refunded else 0,
    )


async def load_job_any_owner(job_id: uuid.UUID) -> dict[str, object] | None:
    """Load a job without an ownership filter.

    Only for the signed-download route, where a valid, unexpired HMAC over the
    job id *is* the authorisation. Every API-key path uses :func:`load_job`.
    """
    async with connection() as conn:
        row = (await conn.execute(select(models.jobs).where(models.jobs.c.id == job_id))).first()
    return dict(row._mapping) if row is not None else None


async def load_job(job_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, object] | None:
    async with connection() as conn:
        row = (
            await conn.execute(select(models.jobs).where(models.jobs.c.id == job_id, models.jobs.c.user_id == user_id))
        ).first()
    return dict(row._mapping) if row is not None else None


async def usage_snapshot(user_id: uuid.UUID, email: str) -> dict[str, object]:
    async with connection() as conn:
        balance = await metering.get_balance(conn, user_id)
        jobs_total = int(
            (
                await conn.execute(
                    select(func.count()).select_from(models.jobs).where(models.jobs.c.user_id == user_id)
                )
            ).scalar_one()
        )
        plan_row = (
            await conn.execute(
                select(
                    models.plans.c.id,
                    models.plans.c.monthly_credits,
                    models.subscriptions.c.status,
                    models.subscriptions.c.current_period_end,
                )
                .select_from(
                    models.subscriptions.join(
                        models.plans,
                        models.subscriptions.c.plan_id == models.plans.c.id,
                    )
                )
                .where(models.subscriptions.c.user_id == user_id)
                .limit(1)
            )
        ).first()
        period_started_at = (
            await conn.execute(
                select(func.max(models.credit_ledger.c.created_at)).where(
                    models.credit_ledger.c.user_id == user_id,
                    models.credit_ledger.c.reason == metering.REASON_MONTHLY_GRANT,
                )
            )
        ).scalar_one_or_none()
        period_filter = (
            models.credit_ledger.c.created_at >= period_started_at
            if period_started_at is not None
            else models.credit_ledger.c.user_id == user_id
        )

        async def ledger_sum(*, reason: str, positive: bool) -> int:
            direction = models.credit_ledger.c.delta > 0 if positive else models.credit_ledger.c.delta < 0
            value = (
                await conn.execute(
                    select(func.coalesce(func.sum(models.credit_ledger.c.delta), 0)).where(
                        models.credit_ledger.c.user_id == user_id,
                        models.credit_ledger.c.reason == reason,
                        direction,
                        period_filter,
                    )
                )
            ).scalar_one()
            return abs(int(value))

        credits_granted = await ledger_sum(
            reason=metering.REASON_MONTHLY_GRANT,
            positive=True,
        )
        credits_purchased = await ledger_sum(
            reason=metering.REASON_OVERAGE_PURCHASE,
            positive=True,
        )
        conversion_spent = await ledger_sum(
            reason=metering.REASON_CONVERSION,
            positive=False,
        )
        pack_spent = await ledger_sum(reason=metering.REASON_PACK, positive=False)
        credits_refunded = int(
            (
                await conn.execute(
                    select(func.coalesce(func.sum(models.credit_ledger.c.delta), 0)).where(
                        models.credit_ledger.c.user_id == user_id,
                        models.credit_ledger.c.reason == metering.REASON_ADMIN,
                        models.credit_ledger.c.delta > 0,
                        models.credit_ledger.c.job_id.is_not(None),
                        period_filter,
                    )
                )
            ).scalar_one()
        )
        recent = (
            (
                await conn.execute(
                    select(
                        models.jobs.c.id,
                        models.jobs.c.kind,
                        models.jobs.c.status,
                        models.jobs.c.credits_charged,
                        models.jobs.c.created_at,
                    )
                    .where(models.jobs.c.user_id == user_id)
                    .order_by(models.jobs.c.created_at.desc())
                    .limit(10)
                )
            )
            .mappings()
            .all()
        )
    return {
        "user_id": user_id,
        "email": email,
        "plan_id": plan_row.id if plan_row else None,
        "subscription_status": plan_row.status if plan_row else None,
        "current_period_end": plan_row.current_period_end if plan_row else None,
        "monthly_credits": plan_row.monthly_credits if plan_row else None,
        "balance": balance,
        "jobs_total": jobs_total,
        "period_started_at": period_started_at,
        "credits_granted": credits_granted,
        "credits_purchased": credits_purchased,
        "credits_spent": conversion_spent + pack_spent,
        "credits_refunded": credits_refunded,
        "credits_remaining": balance,
        "recent_jobs": [dict(row) for row in recent],
    }
