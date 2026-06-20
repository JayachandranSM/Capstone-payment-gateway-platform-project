"""ai-service/app/deps.py — DB, Redis, and LLM client lifecycle + accessors."""

from __future__ import annotations

import asyncpg
import redis.asyncio as redis_async
import structlog
from fastapi import Request

from app.llm.client import LLMClient
from app.settings import get_settings

log = structlog.get_logger(__name__)


# ----- Postgres ---------------------------------------------------------------


async def open_pg_pool() -> asyncpg.Pool:
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
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    log.info("pg_pool_ready")
    return pool


async def close_pg_pool(pool: asyncpg.Pool) -> None:
    log.info("closing_pg_pool")
    await pool.close()


# ----- Redis ------------------------------------------------------------------


def open_redis_client() -> redis_async.Redis:
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


# ----- LLM --------------------------------------------------------------------


def build_llm_client() -> LLMClient:
    """Construct the LLM client. Always succeeds; LLM unavailability surfaces
    later through ``LLMClient.is_configured``."""
    return LLMClient(settings=get_settings())


# ----- accessors --------------------------------------------------------------


def get_pg_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pg_pool


def get_redis(request: Request) -> redis_async.Redis:
    return request.app.state.redis


def get_llm(request: Request) -> LLMClient:
    return request.app.state.llm


__all__ = [
    "open_pg_pool",
    "close_pg_pool",
    "open_redis_client",
    "close_redis_client",
    "build_llm_client",
    "get_pg_pool",
    "get_redis",
    "get_llm",
]
