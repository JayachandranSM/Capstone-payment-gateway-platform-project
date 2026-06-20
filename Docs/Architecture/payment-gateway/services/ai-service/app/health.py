"""ai-service/app/health.py — health / readiness / metrics.

Differs from core-api by adding an Azure OpenAI readiness probe.
Azure OpenAI is *not* a hard dependency — if it is unconfigured or
unreachable, /readyz still returns 200 but reports the degradation in the
``checks`` map. The frontend uses that field to render the "degraded mode"
banner described in DECISIONS.md ADR-011 / ADR-012.
"""

from __future__ import annotations

import os
import time
from typing import Any

import asyncpg
import redis.asyncio as redis_async
import structlog
from fastapi import APIRouter, Depends, Response
from fastapi import status as http_status

from app.deps import get_llm, get_pg_pool, get_redis
from app.llm.client import LLMClient
from app.settings import get_settings

log = structlog.get_logger(__name__)
router = APIRouter(tags=["health"])

_PROCESS_STARTED_AT: float = time.time()


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.service_name,
        "environment": settings.environment,
    }


@router.get("/readyz", summary="Readiness probe (DB + Redis + Azure OpenAI)")
async def readyz(
    response: Response,
    pg_pool: asyncpg.Pool = Depends(get_pg_pool),
    rds: redis_async.Redis = Depends(get_redis),
    llm: LLMClient = Depends(get_llm),
) -> dict[str, Any]:
    checks: dict[str, str] = {}
    hard_ok = True  # only Postgres + Redis are hard deps for the platform layer

    # ---- Postgres (hard) --------------------------------------------------
    try:
        async with pg_pool.acquire() as conn:
            value = await conn.fetchval("SELECT 1")
        checks["postgres"] = "ok" if value == 1 else f"unexpected:{value}"
    except Exception as e:  # noqa: BLE001
        checks["postgres"] = f"error:{type(e).__name__}"
        hard_ok = False
        log.warning("readyz_postgres_fail", error=str(e))

    # ---- Redis (hard) -----------------------------------------------------
    try:
        pong = await rds.ping()
        checks["redis"] = "ok" if pong else "no_pong"
    except Exception as e:  # noqa: BLE001
        checks["redis"] = f"error:{type(e).__name__}"
        hard_ok = False
        log.warning("readyz_redis_fail", error=str(e))

    # ---- Azure OpenAI (soft — degradation, not failure) -------------------
    llm_health = await llm.health(timeout=3.0)
    if llm_health.configured and llm_health.reachable:
        checks["azure_openai"] = "ok"
    elif llm_health.configured and not llm_health.reachable:
        checks["azure_openai"] = f"unreachable:{llm_health.detail}"
    else:
        checks["azure_openai"] = "not_configured"

    if not hard_ok:
        response.status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if hard_ok else "degraded",
        "service": get_settings().service_name,
        "checks": checks,
        # Convenience flag the frontend reads to render the degraded banner.
        "llm_available": checks.get("azure_openai") == "ok",
    }


@router.get("/metrics", summary="Process metrics (stub)", response_class=Response)
async def metrics() -> Response:
    uptime = time.time() - _PROCESS_STARTED_AT
    body = (
        "# HELP process_uptime_seconds Time since process start.\n"
        "# TYPE process_uptime_seconds gauge\n"
        f"process_uptime_seconds {uptime:.3f}\n"
        "# HELP process_pid Process identifier.\n"
        "# TYPE process_pid gauge\n"
        f"process_pid {os.getpid()}\n"
    )
    return Response(content=body, media_type="text/plain; version=0.0.4")


__all__ = ["router"]
