"""core-api/app/deps.py — DB + Redis lifecycle and accessors.

Both clients are stored on ``app.state`` (FastAPI's app-scoped state object).
Helpers below pull them out in a typed way for route handlers.
"""

from __future__ import annotations

import asyncpg
import redis.asyncio as redis_async
import structlog
from fastapi import Request

from app.settings import get_settings

log = structlog.get_logger(__name__)


async def open_pg_pool() -> asyncpg.Pool:
    """Create the asyncpg connection pool."""
    settings = get_settings()
    log.info(
        "opening_pg_pool",
        min_size=settings.pg_pool_min_size,
        max_size=settings.pg_pool_max_size,
    )
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=settings.pg_pool_min_size,
        max_size=settings.pg_pool_max_size,
        command_timeout=30,
    )
    # Touch the pool to fail fast if Postgres is unreachable.
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    log.info("pg_pool_ready")
    return pool


async def close_pg_pool(pool: asyncpg.Pool) -> None:
    log.info("closing_pg_pool")
    await pool.close()


def open_redis_client() -> redis_async.Redis:
    """Create the Redis client (connection pool is lazy)."""
    settings = get_settings()
    log.info("opening_redis_client", max_connections=settings.redis_max_connections)
    return redis_async.from_url(
        settings.redis_url,
        decode_responses=True,
        max_connections=settings.redis_max_connections,
    )


async def close_redis_client(client: redis_async.Redis) -> None:
    log.info("closing_redis_client")
    await client.aclose()


# ----- typed accessors for route handlers ------------------------------------


def get_pg_pool(request: Request) -> asyncpg.Pool:
    """Pull the Postgres pool from app state. Used as a FastAPI dependency."""
    return request.app.state.pg_pool


def get_redis(request: Request) -> redis_async.Redis:
    """Pull the Redis client from app state."""
    return request.app.state.redis


__all__ = [
    "open_pg_pool",
    "close_pg_pool",
    "open_redis_client",
    "close_redis_client",
    "get_pg_pool",
    "get_redis",
]
