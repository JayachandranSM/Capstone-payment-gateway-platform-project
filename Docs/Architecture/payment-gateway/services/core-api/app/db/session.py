"""Async engine, sessionmaker, and the ``get_session`` FastAPI dependency.

Wiring (intended; existing ``app/main.py`` to be updated in a follow-up
turn):

    # main.py lifespan
    engine, sessionmaker = create_engine_and_sessionmaker(
        settings.database_url,
        echo=False,
        pool_size=settings.pg_pool_max_size,
    )
    app.state.db_engine = engine
    app.state.db_sessionmaker = sessionmaker
    yield
    await dispose_engine(engine)

Route handlers consume the dependency:

    @router.post("/payments")
    async def create_payment(session: AsyncSession = Depends(get_session)):
        ...

Per-request lifecycle:
    - one ``AsyncSession`` is yielded per request
    - on exception → ``rollback`` and re-raise
    - on success → the session is closed but **not** auto-committed; the
      service layer commits explicitly. This makes commit boundaries a
      visible code event, not an invisible side-effect of returning.
"""

from __future__ import annotations

import re
from typing import AsyncIterator

import structlog
from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

log = structlog.get_logger(__name__)


def _ensure_async_dialect(url: str) -> str:
    """Inject the ``+asyncpg`` driver if a bare ``postgresql://`` URL is given.

    The bootstrap stores ``DATABASE_URL=postgresql://...`` so both the
    legacy asyncpg-pool code path and SQLAlchemy can read the same env
    variable. SQLAlchemy needs the dialect+driver form.
    """
    # Already has any +driver suffix → leave alone.
    if re.match(r"^postgresql\+[a-z]+://", url):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    # postgres:// is a Heroku-ism still seen in the wild.
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://"):]
    return url


def create_engine_and_sessionmaker(
    database_url: str,
    *,
    echo: bool = False,
    pool_size: int = 10,
    max_overflow: int = 5,
    pool_pre_ping: bool = True,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Build the async engine and a sessionmaker bound to it.

    Args:
        database_url:   ``postgresql://...`` or ``postgresql+asyncpg://...``.
        echo:           SQL echo for debugging; never enable in prod.
        pool_size:      Persistent connections; aligns with ``Settings.pg_pool_max_size``.
        max_overflow:   Burst capacity beyond ``pool_size``.
        pool_pre_ping:  Issue a cheap SELECT 1 before checkout; catches dropped conns.

    Returns:
        ``(engine, sessionmaker)`` pair. Store both on ``app.state``.
    """
    url = _ensure_async_dialect(database_url)
    log.info(
        "creating_async_engine",
        dialect=url.split("://", 1)[0],
        pool_size=pool_size,
        max_overflow=max_overflow,
    )

    engine = create_async_engine(
        url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=pool_pre_ping,
        future=True,
    )

    sessionmaker = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,  # let route handlers read attrs after commit
        autoflush=False,         # explicit flush makes failure modes traceable
    )
    return engine, sessionmaker


async def dispose_engine(engine: AsyncEngine) -> None:
    """Tear down the engine and its pool. Call from lifespan shutdown."""
    log.info("disposing_async_engine")
    await engine.dispose()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an ``AsyncSession`` per request.

    The service layer is responsible for committing. This dependency
    only guarantees rollback-on-exception and clean teardown.
    """
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.db_sessionmaker
    session: AsyncSession = sessionmaker()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


__all__ = [
    "create_engine_and_sessionmaker",
    "dispose_engine",
    "get_session",
]
