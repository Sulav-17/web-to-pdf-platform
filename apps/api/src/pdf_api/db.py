"""Async engine / connection helpers (SQLAlchemy Core, asyncpg)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from .config import get_settings

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        # Tests set DB_POOL_NULL=1 so no asyncpg connection is cached across the
        # per-test event loops (asyncpg connections are event-loop bound).
        if os.environ.get("DB_POOL_NULL") == "1":
            _engine = create_async_engine(settings.database_url, poolclass=NullPool, future=True)
        else:
            _engine = create_async_engine(settings.database_url, pool_pre_ping=True, future=True)
    return _engine


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncConnection]:
    """Open a connection inside a transaction that commits on success."""
    engine = get_engine()
    async with engine.begin() as conn:
        yield conn


@asynccontextmanager
async def connection() -> AsyncIterator[AsyncConnection]:
    """Open a plain (autocommit-per-statement) connection."""
    engine = get_engine()
    async with engine.connect() as conn:
        yield conn
