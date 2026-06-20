"""ai-service/app/llm/client.py — Azure OpenAI client wrapper.

Why a wrapper and not the raw SDK:
- Centralises the "is the LLM configured?" check so route handlers don't
  have to.
- Surfaces a uniform ``ping()`` for the readiness endpoint that does not
  spend tokens (calls ``models.list`` if available, otherwise a no-op
  check on the deployment name).
- Will be the seam where tier-routing (T1 vs T2), retries, circuit-breaking
  and observability hooks live in later turns (see DECISIONS.md ADR-012).

For the bootstrap, no chat completion is performed — we only verify that
the client can be constructed and the configuration is internally consistent.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from app.settings import Settings

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class LLMHealth:
    """Result of an LLM health probe."""

    configured: bool
    reachable: bool
    detail: str  # human-friendly diagnostic


class LLMClient:
    """Lazy-initialised Azure OpenAI client with graceful degradation.

    The constructor never raises. If Azure OpenAI is not configured, the
    client exists in a ``not_configured`` state — calls to ``chat`` or
    ``embed`` will raise ``LLMNotConfiguredError`` at the call site, which
    upstream code is responsible for handling (typically by routing to the
    local Flan-T5 fallback per ADR-010).
    """

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings
        self._client: Any = None  # AsyncAzureOpenAI when configured

        if not settings.azure_openai_configured:
            log.warning(
                "llm_not_configured",
                hint="Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY in .env",
            )
            return

        # Defer the import so the service still boots if the SDK ever fails
        # to install — the bootstrap is more important than the LLM.
        try:
            from openai import AsyncAzureOpenAI

            self._client = AsyncAzureOpenAI(
                azure_endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
                api_version=settings.azure_openai_api_version,
            )
            log.info(
                "llm_configured",
                endpoint=settings.azure_openai_endpoint,
                api_version=settings.azure_openai_api_version,
                chat_deployment=settings.azure_openai_chat_deployment,
                embed_deployment=settings.azure_openai_embed_deployment,
            )
        except Exception as e:  # noqa: BLE001
            log.error("llm_init_failed", error=str(e), error_type=type(e).__name__)
            self._client = None

    # ---- introspection ----------------------------------------------------

    @property
    def is_configured(self) -> bool:
        """True if the underlying Azure OpenAI client was constructed."""
        return self._client is not None

    @property
    def settings(self) -> "Settings":
        return self._settings

    # ---- readiness probe (does not spend tokens) --------------------------

    async def health(self, *, timeout: float = 3.0) -> LLMHealth:
        """Cheap reachability check for /readyz.

        Strategy:
        - If client not configured → ``configured=False, reachable=False``.
        - Otherwise, list models with a short timeout. ``models.list`` does
          not charge tokens but does verify endpoint + key are valid.
        """
        if not self.is_configured:
            return LLMHealth(configured=False, reachable=False, detail="not_configured")

        try:
            # Models endpoint is metadata-only; no token cost.
            async def _probe() -> Any:
                return await self._client.models.list()

            await asyncio.wait_for(_probe(), timeout=timeout)
            return LLMHealth(configured=True, reachable=True, detail="ok")
        except asyncio.TimeoutError:
            return LLMHealth(configured=True, reachable=False, detail="timeout")
        except Exception as e:  # noqa: BLE001
            return LLMHealth(
                configured=True,
                reachable=False,
                detail=f"error:{type(e).__name__}",
            )

    # ---- shutdown ---------------------------------------------------------

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as e:  # noqa: BLE001
                log.warning("llm_close_error", error=str(e))


class LLMNotConfiguredError(RuntimeError):
    """Raised when the LLM is called but no Azure OpenAI credentials are set."""


__all__ = ["LLMClient", "LLMHealth", "LLMNotConfiguredError"]
