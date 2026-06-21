"""FastAPI router for the RAG knowledge-base query endpoint.

Single endpoint: ``POST /v1/rag/query``

The route handler:
  1. Validates the request (Pydantic, automatic).
  2. Pulls the asyncpg pool and LLM client from ``app.state``.
  3. Delegates all retrieval logic to ``RAGService``.
  4. Returns the response or an RFC 7807 problem-details error.

No business logic lives here. The handler is intentionally thin.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import structlog
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from app.deps import get_llm, get_pg_pool
from app.llm.client import LLMClient
from app.rag.schemas import RAGQueryRequest, RAGQueryResponse
from app.rag.service import RAGService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/rag", tags=["rag"])

_BASE_TYPE = "https://errors.paymentgateway.local/ai"


def _problem(
    status_code: int,
    slug: str,
    title: str,
    detail: str,
    *,
    instance: str | None = None,
) -> JSONResponse:
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


@router.post(
    "/query",
    summary="Query the payment policy knowledge base",
    status_code=status.HTTP_200_OK,
    response_model=RAGQueryResponse,
    responses={
        200: {
            "description": (
                "Relevant knowledge chunks retrieved. "
                "``search_mode`` indicates whether vector or keyword retrieval was used."
            ),
        },
        503: {
            "description": "Knowledge base is empty or unreachable.",
            "content": {"application/problem+json": {}},
        },
        500: {
            "description": "Unexpected retrieval error.",
            "content": {"application/problem+json": {}},
        },
    },
)
async def query_knowledge_base(
    body: RAGQueryRequest,
    request: Request,
    pool: asyncpg.Pool = Depends(get_pg_pool),
    llm: LLMClient = Depends(get_llm),
) -> Any:
    """Retrieve policy knowledge relevant to the query.

    Uses **vector search** (pgvector cosine similarity via
    ``text-embedding-3-small``) when Azure OpenAI is configured and
    reachable. Falls back to **keyword search** automatically when
    embeddings are unavailable — no key required for the fallback.

    The ``search_mode`` field in the response tells you which path was taken.

    **Category filtering**: pass ``category_filter`` to restrict retrieval
    to a single policy domain (refund, chargeback, fraud, settlement, or
    payment_failure).

    **Relevance threshold**: pass ``min_score`` (0.0–1.0) to exclude
    weakly-matching chunks. Useful when you need high-confidence answers
    and prefer an empty result over a low-quality one.
    """
    log.info(
        "rag_query_request",
        query_len=len(body.query),
        top_k=body.top_k,
        category_filter=body.category_filter.value if body.category_filter else None,
        min_score=body.min_score,
        llm_available=llm.is_configured,
    )

    try:
        service = RAGService(pool=pool, llm=llm)
        result = await service.query(body)
    except asyncpg.PostgresError as exc:
        log.exception(
            "rag_db_error",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return _problem(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "knowledge-base-unavailable",
            "Knowledge Base Unavailable",
            "The knowledge base could not be queried. "
            "Ensure the knowledge base has been seeded via seed_knowledge_base.py.",
            instance=str(request.url),
        )
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "rag_unexpected_error",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return _problem(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "retrieval-error",
            "Retrieval Error",
            "An unexpected error occurred during knowledge retrieval.",
            instance=str(request.url),
        )

    log.info(
        "rag_query_response",
        query_len=len(body.query),
        returned=len(result.chunks),
        search_mode=result.search_mode.value,
        embedding_used=result.embedding_used,
    )

    return result


__all__ = ["router"]
