"""core-api/app/settings.py — service-specific configuration."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field

from shared.config import BaseAppSettings


class Settings(BaseAppSettings):
    """core-api settings. Reads from environment + .env."""

    service_name: str = "core-api"
    port: int = Field(default=8000, ge=1024, le=65535)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance.

    The ``@lru_cache`` makes this a process-singleton without a global module
    variable, so tests can override env and re-call ``get_settings.cache_clear()``.
    """
    return Settings()  # type: ignore[call-arg]


__all__ = ["Settings", "get_settings"]
