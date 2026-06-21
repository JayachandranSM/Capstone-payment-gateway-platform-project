"""RAG retrieval service.

Two retrieval modes
--------------------

**Vector search (primary)**
    When Azure OpenAI is configured, the query is embedded with
    ``text-embedding-3-small`` (1536 dimensions). The embedding is sent
    to Postgres as a raw float list cast to ``::vector`` and compared
    via cosine distance (``<#>`` operator) against pre-computed chunk
    embeddings stored in ``ai.knowledge_chunks``. The distance is
    converted to similarity: ``similarity = 1 − cosine_distance``.

**Keyword search (fallback)**
    When embeddings are unavailable, a pure-Python scorer computes
    normalised term-frequency overlap between the query tokens and each
    chunk's ``keywords`` column (a space-separated list stored at ingest
    time). This is intentionally simple and fast — it requires no
    additional DB extension and no network call.

Database access
---------------
Uses the raw ``asyncpg.Pool`` (no SQLAlchemy — ai-service has no ORM).
All SQL is parameterised. Vectors are passed as a Python list of floats
and cast to ``::vector`` in the query string; this avoids needing the
``pgvector`` Python package.

Fallback chain
--------------
    query received
        → LLM configured? → embed query → vector search
        → LLM not configured OR embed failed → keyword search

The fallback is transparent to the caller; ``search_mode`` in the
response tells the client which path was taken.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import asyncpg
import structlog

from app.rag.schemas import (
    KnowledgeCategory,
    KnowledgeChunk,
    RAGQueryRequest,
    RAGQueryResponse,
    SearchMode,
)

if TYPE_CHECKING:
    from app.llm.client import LLMClient

log = structlog.get_logger(__name__)

# Embedding model used — must match what was used at ingest time.
_EMBED_MODEL_VERSION = "text-embedding-3-small-1536"
_KEYWORD_MODEL_VERSION = "keyword-tfidf-v1"
_EMBED_DIM = 1536
_EMBED_TIMEOUT = 5.0  # seconds for the embedding API call


class RAGService:
    """Retrieve policy knowledge chunks relevant to a query.

    Constructor takes the asyncpg pool and optionally the LLM client.
    Pass ``llm=None`` to force keyword-only mode (useful in tests and
    when the embedding endpoint is known to be unavailable).
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        llm: "LLMClient | None",
    ) -> None:
        self._pool = pool
        self._llm = llm

    # ── Public API ────────────────────────────────────────────────────────

    async def query(self, req: RAGQueryRequest) -> RAGQueryResponse:
        """Retrieve the most relevant knowledge chunks for ``req.query``.

        Tries vector search first; falls back to keyword search.
        """
        total = await self._count_chunks(req.category_filter)

        # Attempt vector retrieval
        if self._llm is not None and self._llm.is_configured:
            try:
                embedding = await asyncio.wait_for(
                    self._embed(req.query),
                    timeout=_EMBED_TIMEOUT,
                )
                chunks = await self._vector_search(req, embedding)
                log.info(
                    "rag_vector_search",
                    query_len=len(req.query),
                    top_k=req.top_k,
                    returned=len(chunks),
                    total_chunks=total,
                )
                return self._build_response(
                    req=req,
                    chunks=chunks,
                    mode=SearchMode.vector,
                    total=total,
                    embedding_used=True,
                    model_ver=_EMBED_MODEL_VERSION,
                )
            except asyncio.TimeoutError:
                log.warning("rag_embed_timeout", timeout=_EMBED_TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "rag_embed_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        # Keyword fallback
        chunks = await self._keyword_search(req)
        log.info(
            "rag_keyword_search",
            query_len=len(req.query),
            top_k=req.top_k,
            returned=len(chunks),
            total_chunks=total,
        )
        return self._build_response(
            req=req,
            chunks=chunks,
            mode=SearchMode.keyword,
            total=total,
            embedding_used=False,
            model_ver=_KEYWORD_MODEL_VERSION,
        )

    # ── Embedding ─────────────────────────────────────────────────────────

    async def _embed(self, text: str) -> list[float]:
        """Call Azure OpenAI to embed a single text string.

        Returns a list of 1536 floats.
        """
        client = self._llm._client  # noqa: SLF001
        settings = self._llm.settings
        response = await client.embeddings.create(
            model=settings.azure_openai_embed_deployment,
            input=text,
        )
        return response.data[0].embedding

    # ── Vector search (pgvector) ──────────────────────────────────────────

    async def _vector_search(
        self,
        req: RAGQueryRequest,
        embedding: list[float],
    ) -> list[KnowledgeChunk]:
        """Retrieve chunks by cosine similarity using pgvector.

        The ``<#>`` operator returns the *negative* inner product which,
        for unit-normalised vectors (as produced by text-embedding-3-small),
        equals cosine distance. We convert to similarity: 1 − distance.

        We pass the vector as a Python list; asyncpg will serialise it,
        and the ``::vector`` cast in the query tells pgvector the type.
        """
        # Build a stringified vector literal for the cast.
        vec_literal = "[" + ",".join(str(f) for f in embedding) + "]"

        base_sql = """
            SELECT
                chunk_id,
                category,
                source_document,
                section_title,
                content,
                keywords,
                1 - (embedding <#> $1::vector) AS similarity
            FROM ai.knowledge_chunks
            WHERE embedding IS NOT NULL
        """
        params: list[Any] = [vec_literal]
        param_idx = 2

        if req.category_filter is not None:
            base_sql += f" AND category = ${param_idx}"
            params.append(req.category_filter.value)
            param_idx += 1

        if req.min_score > 0.0:
            base_sql += f" AND 1 - (embedding <#> $1::vector) >= ${param_idx}"
            params.append(req.min_score)
            param_idx += 1

        base_sql += f" ORDER BY embedding <#> $1::vector LIMIT ${param_idx}"
        params.append(req.top_k)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(base_sql, *params)

        return [self._row_to_chunk(row, score_field="similarity") for row in rows]

    # ── Keyword search (pure Python fallback) ─────────────────────────────

    async def _keyword_search(self, req: RAGQueryRequest) -> list[KnowledgeChunk]:
        """Score chunks by normalised term-frequency overlap.

        Algorithm:
            1. Tokenise the query into lowercase alpha tokens.
            2. Fetch all chunks (optionally filtered by category).
            3. For each chunk, compute |query_terms ∩ chunk_keywords|
               divided by sqrt(|query_terms| * |chunk_keywords|)
               — a Dice-coefficient-like measure that doesn't penalise
               long chunks as heavily as Jaccard.
            4. Filter by min_score, sort descending, take top_k.

        The ``keywords`` column is a space-separated pre-computed token
        set stored at ingest time (stop-words removed, lower-cased).
        """
        sql = "SELECT chunk_id, category, source_document, section_title, content, keywords FROM ai.knowledge_chunks"
        params: list[Any] = []
        if req.category_filter is not None:
            sql += " WHERE category = $1"
            params.append(req.category_filter.value)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        query_tokens = _tokenise(req.query)
        if not query_tokens:
            return []

        scored: list[tuple[float, Any]] = []
        for row in rows:
            chunk_tokens = set((row["keywords"] or "").split())
            if not chunk_tokens:
                continue
            overlap = len(query_tokens & chunk_tokens)
            if overlap == 0:
                continue
            # Dice-like normalisation
            score = overlap / math.sqrt(len(query_tokens) * len(chunk_tokens))
            scored.append((score, row))

        # Normalise scores to [0, 1] relative to the best result
        if not scored:
            return []

        max_score = max(s for s, _ in scored)
        if max_score == 0:
            return []

        results = sorted(
            [(s / max_score, row) for s, row in scored],
            key=lambda x: x[0],
            reverse=True,
        )

        return [
            self._row_to_chunk(row, score=round(score, 4))
            for score, row in results
            if score >= req.min_score
        ][: req.top_k]

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _count_chunks(
        self, category_filter: KnowledgeCategory | None
    ) -> int:
        sql = "SELECT COUNT(*) FROM ai.knowledge_chunks"
        params: list[Any] = []
        if category_filter is not None:
            sql += " WHERE category = $1"
            params.append(category_filter.value)
        async with self._pool.acquire() as conn:
            return await conn.fetchval(sql, *params)

    @staticmethod
    def _row_to_chunk(
        row: Any,
        *,
        score_field: str | None = None,
        score: float | None = None,
    ) -> KnowledgeChunk:
        resolved_score = (
            float(row[score_field]) if score_field else (score or 0.0)
        )
        return KnowledgeChunk(
            chunk_id=row["chunk_id"],
            category=KnowledgeCategory(row["category"]),
            source_document=row["source_document"],
            section_title=row["section_title"],
            content=row["content"],
            score=max(0.0, min(1.0, resolved_score)),
        )

    @staticmethod
    def _build_response(
        *,
        req: RAGQueryRequest,
        chunks: list[KnowledgeChunk],
        mode: SearchMode,
        total: int,
        embedding_used: bool,
        model_ver: str,
    ) -> RAGQueryResponse:
        return RAGQueryResponse(
            query=req.query,
            chunks=chunks,
            search_mode=mode,
            total_chunks_searched=total,
            model_version=model_ver,
            embedding_used=embedding_used,
            queried_at=datetime.now(timezone.utc).isoformat(),
        )


# ── Utility ───────────────────────────────────────────────────────────────────

_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "is", "it", "in", "on", "at", "to", "for", "of",
    "and", "or", "but", "not", "with", "this", "that", "are", "was",
    "be", "by", "as", "if", "do", "its", "has", "have", "had", "been",
    "from", "when", "what", "how", "which", "will", "can", "may", "any",
    "all", "more", "also", "into", "than", "then", "they", "their",
    "would", "could", "should", "does", "did", "about", "per", "up",
})


def _tokenise(text: str) -> set[str]:
    """Lowercase, alpha-only tokens with stop-word removal."""
    tokens = re.findall(r"[a-z]+", text.lower())
    return {t for t in tokens if t not in _STOP_WORDS and len(t) >= 3}


def chunk_id_for(source_document: str, section_title: str, chunk_index: int) -> str:
    """Deterministic chunk ID — stable across re-ingestion runs."""
    raw = f"{source_document}::{section_title}::{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


__all__ = ["RAGService", "_tokenise", "chunk_id_for"]
