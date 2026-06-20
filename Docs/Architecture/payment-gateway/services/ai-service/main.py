"""ai-service/main.py — FastAPI app factory and lifespan.

Same shape as core-api with one extra resource: the Azure OpenAI client.
The LLM client is constructed unconditionally; if Azure OpenAI is not
configured the client lives in a ``not_configured`` state and ``/readyz``
reports the degradation.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI

from app.deps import (
    build_llm_client,
    close_pg_pool,
    close_redis_client,
    open_pg_pool,
    open_redis_client,
)
from app.health import router as health_router
from app.settings import get_settings
from shared.logging_config import setup_logging

_settings = get_settings()
setup_logging(service_name=_settings.service_name, log_level=_settings.log_level)
log = structlog.get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("startup_begin", port=_settings.port, environment=_settings.environment)

    app.state.pg_pool = await open_pg_pool()
    app.state.redis = open_redis_client()
    await app.state.redis.ping()

    # LLM client construction is non-fatal: it logs at WARNING if not configured.
    app.state.llm = build_llm_client()
    log.info("llm_state", configured=app.state.llm.is_configured)

    log.info("startup_complete")
    try:
        yield
    finally:
        log.info("shutdown_begin")
        await app.state.llm.aclose()
        await close_pg_pool(app.state.pg_pool)
        await close_redis_client(app.state.redis)
        log.info("shutdown_complete")


app = FastAPI(
    title="Payment Gateway — AI Service",
    version="0.1.0",
    description="RAG, multi-agent orchestration, LLM-as-judge, evaluation.",
    lifespan=lifespan,
)

app.include_router(health_router)


@app.get("/", tags=["meta"], summary="Service banner")
async def root() -> dict[str, str]:
    return {
        "service": _settings.service_name,
        "version": app.version,
        "docs": "/docs",
        "health": "/healthz",
        "ready": "/readyz",
    }
