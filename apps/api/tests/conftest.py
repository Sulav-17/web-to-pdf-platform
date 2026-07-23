"""Test fixtures. Uses a real PostgreSQL instance (never SQLite).

Set TEST_DATABASE_URL to point at a disposable database. Defaults to the local
docker test instance on port 55432.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio

os.environ.setdefault(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:55432/web2pdf_test",
)
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["DB_POOL_NULL"] = "1"
os.environ["ENABLE_URL_RENDERING"] = "false"
os.environ.setdefault("STORAGE_DIR", tempfile.mkdtemp(prefix="w2p-store-"))

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402
from sqlalchemy import insert, text  # noqa: E402

from pdf_api import metering, models  # noqa: E402
from pdf_api.config import get_settings  # noqa: E402
from pdf_api.db import get_engine  # noqa: E402
from pdf_api.jobservice import EngineState  # noqa: E402
from pdf_api.keys import generate_api_key, hash_key  # noqa: E402
from pdf_api.rate_limit import SlidingWindowRateLimiter  # noqa: E402
from pdf_api.renderer import BrowserPool  # noqa: E402
from pdf_api.seed import PLAN_SEED  # noqa: E402
from pdf_api.storage import LocalStorageBackend  # noqa: E402

DATA_TABLES = "users, api_keys, subscriptions, jobs, credit_ledger, webhook_secrets"


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    """Create the schema once per session from the ORM metadata + seed plans."""

    async def setup() -> None:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(models.metadata.drop_all)
            await conn.run_sync(models.metadata.create_all)
            for plan in PLAN_SEED:
                await conn.execute(insert(models.plans).values(**plan))
        await engine.dispose()

    asyncio.run(setup())
    yield


@pytest.fixture(autouse=True)
def _clean_tables(_schema: None) -> Iterator[None]:
    """Truncate data tables before each test (plans are preserved)."""

    async def truncate() -> None:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {DATA_TABLES} RESTART IDENTITY CASCADE"))
        await engine.dispose()

    asyncio.run(truncate())
    yield


@pytest_asyncio.fixture
async def engine_state() -> AsyncIterator[EngineState]:
    """A live EngineState with a started browser pool and local storage."""
    settings = get_settings()
    pool = BrowserPool(
        max_concurrent=settings.max_concurrent_renders,
        recycle_after=settings.recycle_after_jobs,
        max_output_bytes=settings.max_output_bytes,
        max_network_requests=settings.max_network_requests,
        max_network_bytes=settings.max_network_bytes,
    )
    await pool.start()
    storage = LocalStorageBackend(tempfile.mkdtemp(prefix="w2p-test-"), ttl_seconds=settings.output_ttl_seconds)
    state = EngineState(settings=settings, pool=pool, storage=storage)
    try:
        yield state
    finally:
        await pool.stop()


@pytest_asyncio.fixture
async def client(engine_state: EngineState) -> AsyncIterator[httpx.AsyncClient]:
    from pdf_api.main import app

    app.state.engine = engine_state
    app.state.rate_limiter = SlidingWindowRateLimiter()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def create_user_with_credits(
    email: str | None = None,
    credits: int = 75,
) -> tuple[uuid.UUID, str]:
    """Insert a verified user, free subscription, key, and optional credits."""
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    raw_key = generate_api_key()
    engine = get_engine()
    async with engine.begin() as conn:
        user_id = uuid.uuid4()
        await conn.execute(
            insert(models.users).values(
                id=user_id,
                email=email,
                email_verified=True,
            )
        )
        await conn.execute(
            insert(models.subscriptions).values(
                id=uuid.uuid4(),
                user_id=user_id,
                plan_id="free",
                status="active",
            )
        )
        await conn.execute(
            insert(models.api_keys).values(
                id=uuid.uuid4(),
                user_id=user_id,
                key_hash=hash_key(raw_key),
                label="test",
                active=True,
            )
        )
        if credits:
            await metering.grant_credits(
                conn,
                user_id,
                credits,
                reason=metering.REASON_MONTHLY_GRANT,
            )
    await engine.dispose()
    return user_id, raw_key


def auth_headers(raw_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw_key}"}


async def get_balance(user_id: uuid.UUID) -> int:
    engine = get_engine()
    async with engine.connect() as conn:
        bal = await metering.get_balance(conn, user_id)
    await engine.dispose()
    return bal
