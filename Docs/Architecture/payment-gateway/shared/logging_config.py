"""shared/logging_config.py — JSON structured logging via structlog.

Design intent:

- One call to ``setup_logging(service_name, level)`` from each service's main
  module configures both structlog *and* the stdlib root logger, so that
  libraries using ``logging.getLogger(__name__)`` (uvicorn, asyncpg, redis-py,
  openai) also emit JSON lines on stdout.
- Every log line carries ``service`` so a multi-service docker/podman log
  stream can be filtered (``podman logs ... | jq 'select(.service=="core-api")'``).
- ``trace_id`` propagation uses ``structlog.contextvars`` — bind once per
  request in middleware and every nested log line picks it up.

Zero ``print()`` anywhere — capstone rubric explicitly disallows them.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def _stdlib_to_structlog_processor() -> Any:
    """Bridge stdlib LogRecord → structlog event dict."""
    return structlog.stdlib.ProcessorFormatter.wrap_for_formatter


def setup_logging(service_name: str, log_level: str = "INFO") -> structlog.stdlib.BoundLogger:
    """Configure structlog + stdlib logging for the whole process.

    Call exactly once, as early as possible in the service's ``main`` module.

    Args:
        service_name: Bound as ``service`` on every log line.
        log_level:    Stdlib level name. Levels below this are dropped.

    Returns:
        A bound structlog logger ready to use. Other modules should still
        do ``log = structlog.get_logger(__name__)`` — they will inherit
        the same configuration.
    """
    level_num: int = getattr(logging, log_level.upper(), logging.INFO)

    # ---- shared processor chain -------------------------------------------
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True, key="ts")

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,           # request_id, trace_id
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
    ]

    # ---- structlog (native callers) ---------------------------------------
    structlog.configure(
        processors=shared_processors
        + [
            # When emitted *through* stdlib (uvicorn etc.), let the formatter render.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_num),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # ---- stdlib bridge ----------------------------------------------------
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            # Strip the wrapping marker added above before rendering JSON.
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(sort_keys=False),
        ],
    )

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Replace existing handlers (uvicorn installs its own by default).
    root.handlers = [handler]
    root.setLevel(level_num)

    # Quiet noisy libraries to WARNING; raise if user wants DEBUG.
    for noisy in ("uvicorn.access", "asyncio", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(level_num, logging.WARNING))

    # ---- per-process context binding --------------------------------------
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(service=service_name)

    log = structlog.get_logger("bootstrap")
    log.info("logging_configured", level=log_level.upper(), service=service_name)
    return log


__all__ = ["setup_logging"]
