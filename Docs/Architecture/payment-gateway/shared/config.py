"""shared/config.py — Pydantic Settings base class.

Every service extends ``BaseAppSettings`` with its own service-specific fields.
Field values are read in this priority order (Pydantic Settings v2):

    1. Constructor kwargs
    2. Environment variables (case-insensitive)
    3. ``.env`` file (UTF-8) in the working directory
    4. Field default declared on the class

The class is intentionally minimal — only fields that *every* service needs
live here. Service-specific config goes in ``app/settings.py`` of that service.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseAppSettings(BaseSettings):
    """Common settings for all Python services."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # We share one .env across services, so unknown vars are normal.
        extra="ignore",
    )

    # ---- identity & runtime --------------------------------------------------
    service_name: str = Field(default="unknown", description="Logical service name; bound on every log line.")
    environment: str = Field(default="dev", description="dev | staging | prod")
    log_level: str = Field(default="INFO", description="DEBUG | INFO | WARNING | ERROR")

    # ---- data plane ----------------------------------------------------------
    database_url: str = Field(
        default="postgresql://postgres:postgres@postgres:5432/paymentgateway",
        description="PostgreSQL DSN. The asyncpg driver auto-detected.",
    )
    redis_url: str = Field(
        default="redis://redis:6379/0",
        description="Redis URL. Decode is enabled for string values by default.",
    )

    # ---- pool sizing ---------------------------------------------------------
    pg_pool_min_size: int = Field(default=2, ge=1)
    pg_pool_max_size: int = Field(default=10, ge=2)
    redis_max_connections: int = Field(default=50, ge=4)


__all__ = ["BaseAppSettings"]
