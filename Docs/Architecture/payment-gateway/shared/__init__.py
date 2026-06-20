"""Shared library copied into each Python service image at build time.

Exposes:
    shared.config       — Pydantic BaseSettings base class
    shared.logging_config — structlog JSON logger factory
"""

__all__ = ["config", "logging_config"]
