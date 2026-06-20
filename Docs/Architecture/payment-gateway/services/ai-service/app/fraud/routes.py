"""FastAPI router for the fraud-scoring endpoint.

Single endpoint: ``POST /v1/fraud/score``

The route handler is intentionally thin:
- Validates the request body via Pydantic (done automatically by FastAPI).
- Constructs ``FraudScoringService`` with the LLM client from ``app.state``.
- Delegates all business logic to the service.
- Maps service exceptions to RFC 7807 problem-details responses.

No database I/O happens here. The fraud service is stateless.

Wiring
------
This router is registered in ``main.py``::

    from app.fraud.routes import router as fraud_router
    app.include_router(fraud_router)
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from app.deps import get_llm
from app.fraud.schemas import FraudScoreRequest, FraudScoreResponse
from app.fraud.service import FraudScoringService
from app.llm.client import LLMClient

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/fraud", tags=["fraud"])

# ── Problem-detail helper ─────────────────────────────────────────────────────

_BASE_TYPE = "https://errors.paymentgateway.local/ai"


def _problem(
    status_code: int,
    slug: str,
    title: str,
    detail: str,
    *,
    instance: str | None = None,
) -> JSONResponse:
    """RFC 7807 ``application/problem+json`` response."""
    body: dict[str, Any] = {
        "type": f"{_BASE_TYPE}/{slug}",
        "title": title,
        "status": status_code,
        "detail": detail,
    }
    if instance:
        body["instance"] = instance
    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type="application/problem+json",
    )


# ── Endpoint ──────────────────────────────────────────────────────────────────


@router.post(
    "/score",
    summary="Score a transaction for fraud risk",
    status_code=status.HTTP_200_OK,
    response_model=FraudScoreResponse,
    responses={
        400: {
            "description": "Request validation failed.",
            "content": {"application/problem+json": {}},
        },
        500: {
            "description": "Unexpected scoring error.",
            "content": {"application/problem+json": {}},
        },
    },
)
async def score_transaction(
    body: FraudScoreRequest,
    request: Request,
    llm: LLMClient = Depends(get_llm),
) -> Any:
    """Score a payment transaction for fraud risk.

    Returns a ``risk_score`` (0–100), a three-tier ``decision``
    (allow / review / reject), a list of ``reasons``, and a natural-language
    ``explanation``.

    The scoring is always fully deterministic — no LLM key is required.
    When Azure OpenAI is configured, the ``explanation`` field is enriched
    by the LLM; otherwise a templated explanation is returned and
    ``llm_used`` is ``false``.

    **Decision thresholds:**
    - ``allow``   — risk_score < 40
    - ``review``  — 40 ≤ risk_score < 75
    - ``reject``  — risk_score ≥ 75
    """
    log.info(
        "fraud_score_request",
        transaction_id=str(body.transaction_id),
        user_id=str(body.user_id),
        merchant_id=body.merchant_id,
        amount=str(body.amount),
        currency=body.currency,
        payment_method=body.payment_method.value,
        llm_available=llm.is_configured,
    )

    try:
        service = FraudScoringService(llm=llm)
        result = await service.score(body)
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "fraud_score_unexpected_error",
            transaction_id=str(body.transaction_id),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return _problem(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "scoring-error",
            "Fraud Scoring Error",
            "An unexpected error occurred while scoring the transaction. "
            "The transaction should be held for manual review.",
            instance=str(request.url),
        )

    log.info(
        "fraud_score_response",
        transaction_id=str(result.transaction_id),
        risk_score=result.risk_score,
        decision=result.decision.value,
        rules_fired=len(result.rule_hits),
        llm_used=result.llm_used,
    )

    return result


__all__ = ["router"]
