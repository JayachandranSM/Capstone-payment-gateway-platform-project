"""core-api/app/health.py — health, readiness, and metrics endpoints.

Contract:

- ``/healthz`` 200 = process alive. No external dependencies probed.
- ``/readyz``  200 = all critical dependencies reachable; 503 otherwise.
- ``/metrics`` plain-text process metrics; Prometheus exporter to be wired later.
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

from app.deps import get_pg_pool, get_redis
from app.settings import get_settings

log = structlog.get_logger(__name__)
router = APIRouter(tags=["health"])

# Process start time (UTC seconds). Used for /metrics uptime gauge.
_PROCESS_STARTED_AT: float = time.time()


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, Any]:
    """Liveness: do not touch the network. The runtime polls this every 10 s."""
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.service_name,
        "environment": settings.environment,
    }


@router.get("/readyz", summary="Readiness probe (DB + Redis)")
async def readyz(
    response: Response,
    pg_pool: asyncpg.Pool = Depends(get_pg_pool),
    rds: redis_async.Redis = Depends(get_redis),
) -> dict[str, Any]:
    """Readiness: probe Postgres + Redis. Returns 503 if any critical dep fails."""
    checks: dict[str, str] = {}
    overall_ok = True

    # ---- Postgres ---------------------------------------------------------
    try:
        async with pg_pool.acquire() as conn:
            value = await conn.fetchval("SELECT 1")
        checks["postgres"] = "ok" if value == 1 else f"unexpected:{value}"
    except Exception as e:  # noqa: BLE001
        checks["postgres"] = f"error:{type(e).__name__}"
        overall_ok = False
        log.warning("readyz_postgres_fail", error=str(e))

    # ---- Redis ------------------------------------------------------------
    try:
        pong = await rds.ping()
        checks["redis"] = "ok" if pong else "no_pong"
    except Exception as e:  # noqa: BLE001
        checks["redis"] = f"error:{type(e).__name__}"
        overall_ok = False
        log.warning("readyz_redis_fail", error=str(e))

    if not overall_ok:
        response.status_code = http_status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if overall_ok else "degraded",
        "service": get_settings().service_name,
        "checks": checks,
    }


@router.get("/metrics", summary="Process metrics (stub)", response_class=Response)
async def metrics() -> Response:
    """Plain-text metrics in Prometheus exposition format.

    Stub for the bootstrap. A full Prometheus exporter (prometheus-client or
    OTel) will be wired when the metrics dashboard work begins.
    """
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
