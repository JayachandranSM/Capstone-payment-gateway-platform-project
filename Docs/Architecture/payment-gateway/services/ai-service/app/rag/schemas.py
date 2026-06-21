"""Pydantic v2 schemas for the RAG knowledge-base query endpoint.

All schemas are self-contained — no imports from core-api or the
fraud module — keeping the rag package independently testable.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class SearchMode(str, Enum):
    """How the retrieval step was performed."""

    vector = "vector"    # pgvector cosine similarity
    keyword = "keyword"  # in-process TF-IDF term overlap (LLM unavailable)
    hybrid = "hybrid"    # vector + keyword, re-ranked by RRF (future)


class KnowledgeCategory(str, Enum):
    """Broad topic area of a knowledge chunk.

    Maps 1:1 to the source policy documents in docs/knowledge/.
    """

    refund = "refund"
    chargeback = "chargeback"
    fraud = "fraud"
    settlement = "settlement"
    payment_failure = "payment_failure"
    general = "general"


# ── Request ───────────────────────────────────────────────────────────────────


class RAGQueryRequest(BaseModel):
    """Body for ``POST /v1/rag/query``."""

    model_config = ConfigDict(str_strip_whitespace=True)

    query: Annotated[
        str,
        Field(
            min_length=3,
            max_length=1_000,
            description="Natural-language question or keyword search string.",
            examples=[
                "How long does a refund take for UPI payments?",
                "What happens when a chargeback is received after settlement?",
                "Can I refund a flagged transaction?",
            ],
        ),
    ]
    top_k: Annotated[
        int,
        Field(
            default=5,
            ge=1,
            le=20,
            description="Maximum number of knowledge chunks to return.",
        ),
    ] = 5
    category_filter: KnowledgeCategory | None = Field(
        default=None,
        description=(
            "Restrict retrieval to a single policy category. "
            "``null`` searches across all categories."
        ),
    )
    min_score: Annotated[
        float,
        Field(
            default=0.0,
            ge=0.0,
            le=1.0,
            description=(
                "Minimum similarity score (0.0–1.0) for a chunk to be returned. "
                "Chunks scoring below this threshold are excluded. "
                "Default 0.0 means no filtering."
            ),
        ),
    ] = 0.0


# ── Response building blocks ──────────────────────────────────────────────────


class KnowledgeChunk(BaseModel):
    """A single retrieved passage with its source and relevance score."""

    model_config = ConfigDict(protected_namespaces=())

    chunk_id: str = Field(description="Stable identifier for this knowledge chunk.")
    category: KnowledgeCategory
    source_document: str = Field(
        description="Filename of the source policy document, e.g. ``refund_policy.md``."
    )
    section_title: str = Field(
        description="Section heading under which this chunk appears."
    )
    content: str = Field(description="Full text of the retrieved passage.")
    score: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Relevance score in [0, 1]. "
            "For vector search this is the cosine similarity; "
            "for keyword search it is a normalised TF-IDF overlap score."
        ),
    )


# ── Response ──────────────────────────────────────────────────────────────────


class RAGQueryResponse(BaseModel):
    """Response body for ``POST /v1/rag/query``."""

    model_config = ConfigDict(protected_namespaces=())

    query: str = Field(description="The original query string, echoed back.")
    chunks: list[KnowledgeChunk] = Field(
        description="Retrieved knowledge chunks, ordered by descending relevance score."
    )
    search_mode: SearchMode = Field(
        description=(
            "Retrieval strategy used. ``vector`` when Azure OpenAI embeddings are "
            "available; ``keyword`` when they are not."
        )
    )
    total_chunks_searched: int = Field(
        description="Total number of chunks in the knowledge base that were considered."
    )
    model_version: str = Field(
        description="Embedding model or keyword scorer version, for audit."
    )
    embedding_used: bool = Field(
        description="True if vector embeddings were used for this query."
    )
    queried_at: str = Field(description="ISO-8601 UTC timestamp of the query.")


__all__ = [
    "SearchMode",
    "KnowledgeCategory",
    "RAGQueryRequest",
    "KnowledgeChunk",
    "RAGQueryResponse",
]
