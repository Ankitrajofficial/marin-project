"""Async psycopg connection pool.

One pool per process, opened explicitly at startup and closed at shutdown.
Everything that touches PostgreSQL goes through get_conn().
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg_pool import AsyncConnectionPool

from app.config import settings

log = logging.getLogger(__name__)

# Constructed here but NOT opened: psycopg3 emits a RuntimeWarning if a pool
# opens in its constructor, because that starts background tasks before an
# event loop necessarily exists. open_pool() is called from the app lifespan
# (or from an ingest job's main()).
_pool = AsyncConnectionPool(
    conninfo=settings.database_url,
    min_size=settings.db_pool_min,
    max_size=settings.db_pool_max,
    open=False,
)


async def open_pool() -> None:
    """Open the pool and block until at least min_size connections are live.

    wait() surfaces a bad DATABASE_URL at startup instead of on the first
    query, which is the difference between a clear boot failure and a
    confusing request-time error.
    """
    await _pool.open()
    await _pool.wait()
    log.info("db pool open (min=%d max=%d)", settings.db_pool_min, settings.db_pool_max)


async def close_pool() -> None:
    await _pool.close()
    log.info("db pool closed")


def get_pool() -> AsyncConnectionPool:
    return _pool


@asynccontextmanager
async def get_conn() -> AsyncIterator:
    """Check out a connection. Commits on clean exit, rolls back on exception.

    psycopg3 connections are transactional by default, so the caller does not
    manage BEGIN/COMMIT.
    """
    async with _pool.connection() as conn:
        yield conn
