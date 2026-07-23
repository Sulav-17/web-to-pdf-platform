"""FastAPI application factory, lifespan, and request logging."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from .config import Settings, get_settings
from .db import dispose_engine
from .jobservice import EngineState
from .logging_config import (
    bind_request_context,
    clear_request_context,
    configure_logging,
    get_logger,
)
from .observability import init_posthog, init_sentry
from .renderer import BrowserPool
from .routes import drain_background_tasks, router
from .storage import build_storage_backend

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    configure_logging(settings.log_level)
    init_sentry(settings)
    init_posthog(settings)

    pool = BrowserPool(
        max_concurrent=settings.max_concurrent_renders,
        recycle_after=settings.recycle_after_jobs,
        max_output_bytes=settings.max_output_bytes,
    )
    await pool.start()
    storage = build_storage_backend(settings)
    app.state.engine = EngineState(settings=settings, pool=pool, storage=storage)
    log.info("app.started", environment=settings.environment)
    try:
        yield
    finally:
        # Jobs need the browser and DB while finishing or processing cancellation.
        await drain_background_tasks(settings.shutdown_grace_seconds)
        await pool.stop()
        await dispose_engine()
        log.info("app.stopped")


def create_app() -> FastAPI:
    configure_logging(get_settings().log_level)
    app = FastAPI(title="web-to-pdf engine", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        clear_request_context()
        bind_request_context(request_id=request_id)
        log.info("request.start", method=request.method, path=request.url.path)
        try:
            response = await call_next(request)
        finally:
            clear_request_context()
        response.headers["X-Request-Id"] = request_id
        return response

    app.include_router(router)
    return app


app = create_app()
