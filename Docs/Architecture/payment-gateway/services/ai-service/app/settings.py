"""ai-service/app/settings.py — service-specific configuration.

Adds Azure OpenAI configuration on top of the shared base. Endpoint and key
are intentionally Optional — the service must boot successfully even when
they're absent, so a fresh-clone developer experience is smooth.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field

from shared.config import BaseAppSettings


class Settings(BaseAppSettings):
    """ai-service settings."""

    service_name: str = "ai-service"
    port: int = Field(default=8100, ge=1024, le=65535)

    # ---- Azure OpenAI ----------------------------------------------------
    # All four fields together constitute "configured". If endpoint or key
    # is missing, the service runs in degraded mode (no LLM calls; /readyz
    # reports azure_openai="not_configured" but stays 200 because Azure
    # OpenAI is not a hard dependency at the platform layer).
    azure_openai_endpoint: str | None = Field(default=None)
    azure_openai_api_key: str | None = Field(default=None)
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_openai_chat_deployment: str = "gpt-4o-mini"
    azure_openai_embed_deployment: str = "text-embedding-3-small"

    @property
    def azure_openai_configured(self) -> bool:
        """True only when both endpoint and key are present."""
        return bool(self.azure_openai_endpoint) and bool(self.azure_openai_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


__all__ = ["Settings", "get_settings"]
