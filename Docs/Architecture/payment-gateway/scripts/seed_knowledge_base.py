#!/usr/bin/env python3
"""scripts/seed_knowledge_base.py — Ingest policy documents into the RAG knowledge base.

Reads every Markdown file in ``docs/knowledge/``, splits it into
section-level chunks, computes embeddings (when Azure OpenAI is
configured) or stores NULL embeddings (keyword-only fallback), and
upserts the chunks into ``ai.knowledge_chunks``.

Safe to re-run — uses ``ON CONFLICT (chunk_id) DO UPDATE`` so existing
chunks are refreshed rather than duplicated. Re-running with the same
documents produces zero net changes if the content has not changed.

Usage
-----
    # From the project root (with DATABASE_URL in environment or .env):
    python scripts/seed_knowledge_base.py

    # From inside the ai-service container:
    podman exec -it pg-ai-service python scripts/seed_knowledge_base.py

    # With embeddings (requires Azure OpenAI creds in environment):
    AZURE_OPENAI_ENDPOINT=https://... AZURE_OPENAI_API_KEY=... \\
        python scripts/seed_knowledge_base.py

    # Keyword-only (no OpenAI key needed):
    python scripts/seed_knowledge_base.py --no-embeddings

    # Dry-run — show what would be ingested without writing:
    python scripts/seed_knowledge_base.py --dry-run

    # Wipe and re-seed from scratch:
    python scripts/seed_knowledge_base.py --reset

Design choices
--------------
- Section-level chunking: each ``## Heading`` becomes one chunk. This
  keeps chunks semantically coherent and avoids mid-sentence splits.
  Chunks from the document preamble (before the first ## heading) are
  labelled with section_title = "Overview".
- Chunk size target: 200–600 tokens. Markdown sections in our policy
  docs average ~300 tokens, which fits comfortably within the 8192-token
  embedding context window.
- Deterministic chunk_id: SHA-256 of (source_document, section_title,
  chunk_index) — stable across re-runs, safe to use as upsert key.
- PYTHONPATH independence: the script imports only stdlib and asyncpg
  so it can run without the full ai-service package installed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("seed_kb")

# ── Constants ─────────────────────────────────────────────────────────────────

# Path relative to *this script's* location — works from any CWD.
_SCRIPT_DIR = Path(__file__).parent
_DOCS_DIR = _SCRIPT_DIR.parent / "docs" / "knowledge"

# Mapping from filename stem → KnowledgeCategory value.
_FILENAME_TO_CATEGORY: dict[str, str] = {
    "refund_policy":          "refund",
    "chargeback_policy":      "chargeback",
    "fraud_policy":           "fraud",
    "settlement_policy":      "settlement",
    "payment_failure_policy": "payment_failure",
}

_EMBED_MODEL = "text-embedding-3-small"
_EMBED_DIM = 1536


# ── Chunking ──────────────────────────────────────────────────────────────────


def _parse_markdown_sections(text: str, source_doc: str) -> list[dict[str, Any]]:
    """Split a Markdown document into section-level chunks.

    Each ``## Heading`` becomes one chunk. Content before the first heading
    is labelled "Overview". ``### Sub-headings`` are kept inside their
    parent ``##`` chunk so they stay contextually coherent.
    """
    # Normalise line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Split on level-2 headings (##); keep the heading line.
    # We deliberately do NOT split on ### — sub-sections stay with their parent.
    parts = re.split(r"(?m)^(## .+)$", text)

    sections: list[dict[str, Any]] = []
    chunk_index = 0

    # parts = [preamble, heading1, body1, heading2, body2, ...]
    preamble = parts[0].strip()
    if preamble:
        sections.append({
            "section_title": "Overview",
            "content": preamble,
            "chunk_index": chunk_index,
        })
        chunk_index += 1

    for i in range(1, len(parts), 2):
        heading_line = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        title = heading_line.lstrip("#").strip()
        content = f"{heading_line}\n\n{body}".strip() if body else heading_line
        if content:
            sections.append({
                "section_title": title,
                "content": content,
                "chunk_index": chunk_index,
            })
            chunk_index += 1

    return sections


def _extract_keywords(content: str) -> str:
    """Return a space-separated token set for keyword search fallback."""
    stop_words = {
        "a", "an", "the", "is", "it", "in", "on", "at", "to", "for", "of",
        "and", "or", "but", "not", "with", "this", "that", "are", "was",
        "be", "by", "as", "if", "do", "its", "has", "have", "had", "been",
        "from", "when", "what", "how", "which", "will", "can", "may", "any",
        "all", "more", "also", "into", "than", "then", "they", "their",
        "would", "could", "should", "does", "did", "about", "per", "up",
    }
    tokens = re.findall(r"[a-z]+", content.lower())
    unique = {t for t in tokens if t not in stop_words and len(t) >= 3}
    return " ".join(sorted(unique))


def _chunk_id(source_document: str, section_title: str, chunk_index: int) -> str:
    raw = f"{source_document}::{section_title}::{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


# ── Embedding ─────────────────────────────────────────────────────────────────


async def _embed_batch(texts: list[str], client: Any, deployment: str) -> list[list[float]]:
    """Embed a list of texts in a single API call (max 2048 per call for text-embedding-3-small)."""
    response = await client.embeddings.create(model=deployment, input=texts)
    # Response.data is ordered the same as input
    return [item.embedding for item in response.data]


# ── Database setup ────────────────────────────────────────────────────────────


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ai.knowledge_chunks (
    chunk_id        TEXT PRIMARY KEY,
    category        TEXT NOT NULL,
    source_document TEXT NOT NULL,
    section_title   TEXT NOT NULL,
    content         TEXT NOT NULL,
    keywords        TEXT NOT NULL DEFAULT '',
    embedding       vector({dim}),
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_hash    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_category
    ON ai.knowledge_chunks (category);

CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding
    ON ai.knowledge_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
""".format(dim=_EMBED_DIM)

_UPSERT_SQL = """
INSERT INTO ai.knowledge_chunks
    (chunk_id, category, source_document, section_title, content,
     keywords, embedding, content_hash)
VALUES
    ($1, $2, $3, $4, $5, $6, $7::vector, $8)
ON CONFLICT (chunk_id) DO UPDATE SET
    category        = EXCLUDED.category,
    source_document = EXCLUDED.source_document,
    section_title   = EXCLUDED.section_title,
    content         = EXCLUDED.content,
    keywords        = EXCLUDED.keywords,
    embedding       = EXCLUDED.embedding,
    content_hash    = EXCLUDED.content_hash,
    ingested_at     = now()
WHERE ai.knowledge_chunks.content_hash != EXCLUDED.content_hash
"""

# Upsert without embedding (keyword-only mode)
_UPSERT_NO_EMBED_SQL = """
INSERT INTO ai.knowledge_chunks
    (chunk_id, category, source_document, section_title, content,
     keywords, content_hash)
VALUES
    ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (chunk_id) DO UPDATE SET
    category        = EXCLUDED.category,
    source_document = EXCLUDED.source_document,
    section_title   = EXCLUDED.section_title,
    content         = EXCLUDED.content,
    keywords        = EXCLUDED.keywords,
    content_hash    = EXCLUDED.content_hash,
    ingested_at     = now()
WHERE ai.knowledge_chunks.content_hash != EXCLUDED.content_hash
"""


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _vec_literal(embedding: list[float]) -> str:
    """Format a Python float list as a pgvector literal string."""
    return "[" + ",".join(str(f) for f in embedding) + "]"


# ── Main seeding logic ────────────────────────────────────────────────────────


async def seed(
    *,
    dry_run: bool,
    no_embeddings: bool,
    reset: bool,
    database_url: str,
) -> None:
    # ── Discover documents ────────────────────────────────────────────────
    docs = sorted(_DOCS_DIR.glob("*.md"))
    if not docs:
        log.error("No Markdown files found in %s", _DOCS_DIR)
        sys.exit(1)

    log.info("Found %d policy documents in %s", len(docs), _DOCS_DIR)

    # ── Parse all chunks ──────────────────────────────────────────────────
    all_chunks: list[dict[str, Any]] = []
    for doc_path in docs:
        stem = doc_path.stem
        category = _FILENAME_TO_CATEGORY.get(stem, "general")
        text = doc_path.read_text(encoding="utf-8")
        sections = _parse_markdown_sections(text, doc_path.name)
        for s in sections:
            s["source_document"] = doc_path.name
            s["category"] = category
            s["chunk_id"] = _chunk_id(doc_path.name, s["section_title"], s["chunk_index"])
            s["keywords"] = _extract_keywords(s["content"])
            s["content_hash"] = _content_hash(s["content"])
        all_chunks.extend(sections)
        log.info("  %s → %d sections → category=%s", doc_path.name, len(sections), category)

    log.info("Total chunks: %d", len(all_chunks))

    if dry_run:
        for c in all_chunks:
            log.info(
                "  [dry-run] %s  section=%r  words=%d",
                c["chunk_id"], c["section_title"], len(c["content"].split()),
            )
        log.info("Dry-run complete. No writes performed.")
        return

    # ── Try to build OpenAI client ────────────────────────────────────────
    openai_client = None
    embed_deployment = os.environ.get("AZURE_OPENAI_EMBED_DEPLOYMENT", "text-embedding-3-small")

    if not no_embeddings:
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
        api_key  = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
        api_ver  = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")

        if endpoint and api_key:
            try:
                from openai import AsyncAzureOpenAI  # type: ignore
                openai_client = AsyncAzureOpenAI(
                    azure_endpoint=endpoint,
                    api_key=api_key,
                    api_version=api_ver,
                )
                log.info("Azure OpenAI client ready (model=%s)", embed_deployment)
            except Exception as e:
                log.warning("Could not build OpenAI client: %s — using keyword mode", e)
        else:
            log.info("AZURE_OPENAI_ENDPOINT/KEY not set — using keyword-only mode")

    # ── Connect to Postgres ───────────────────────────────────────────────
    log.info("Connecting to Postgres…")
    pool = await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=3)

    try:
        # ── Create / verify table ─────────────────────────────────────────
        async with pool.acquire() as conn:
            await conn.execute(_CREATE_TABLE_SQL)
            log.info("ai.knowledge_chunks table ready")

            if reset:
                deleted = await conn.fetchval(
                    "DELETE FROM ai.knowledge_chunks RETURNING count(*)"
                )
                log.info("Reset: deleted %s existing chunks", deleted or 0)

        # ── Compute embeddings in batches ─────────────────────────────────
        embeddings: list[list[float] | None] = [None] * len(all_chunks)

        if openai_client is not None:
            BATCH = 100  # text-embedding-3-small supports up to 2048 inputs per call
            texts = [c["content"] for c in all_chunks]
            log.info("Embedding %d chunks in batches of %d…", len(texts), BATCH)
            t0 = time.time()
            for i in range(0, len(texts), BATCH):
                batch_texts = texts[i : i + BATCH]
                batch_embeddings = await _embed_batch(batch_texts, openai_client, embed_deployment)
                for j, emb in enumerate(batch_embeddings):
                    embeddings[i + j] = emb
                log.info(
                    "  embedded %d/%d chunks",
                    min(i + BATCH, len(texts)), len(texts),
                )
            log.info("Embedding complete in %.1fs", time.time() - t0)

        # ── Upsert all chunks ─────────────────────────────────────────────
        inserted = updated = skipped = 0
        async with pool.acquire() as conn:
            for chunk, emb in zip(all_chunks, embeddings):
                if emb is not None:
                    result = await conn.execute(
                        _UPSERT_SQL,
                        chunk["chunk_id"],
                        chunk["category"],
                        chunk["source_document"],
                        chunk["section_title"],
                        chunk["content"],
                        chunk["keywords"],
                        _vec_literal(emb),
                        chunk["content_hash"],
                    )
                else:
                    result = await conn.execute(
                        _UPSERT_NO_EMBED_SQL,
                        chunk["chunk_id"],
                        chunk["category"],
                        chunk["source_document"],
                        chunk["section_title"],
                        chunk["content"],
                        chunk["keywords"],
                        chunk["content_hash"],
                    )

                # asyncpg returns "INSERT 0 N" or "UPDATE N" as the tag
                tag = result or ""
                if "INSERT" in tag:
                    inserted += 1
                elif "UPDATE" in tag:
                    updated += 1
                else:
                    skipped += 1

        log.info(
            "Upsert complete: inserted=%d updated=%d skipped(unchanged)=%d",
            inserted, updated, skipped,
        )

        # ── Verify ────────────────────────────────────────────────────────
        async with pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM ai.knowledge_chunks")
            with_emb = await conn.fetchval(
                "SELECT COUNT(*) FROM ai.knowledge_chunks WHERE embedding IS NOT NULL"
            )
        log.info(
            "Knowledge base: %d total chunks, %d with embeddings, %d keyword-only",
            total, with_emb, total - with_emb,
        )

    finally:
        await pool.close()


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Seed the RAG knowledge base from docs/knowledge/*.md.\n\n"
            "Safe to re-run (idempotent via content-hash comparison).\n\n"
            "Requires DATABASE_URL in the environment (or .env file)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be ingested without writing to the database.",
    )
    p.add_argument(
        "--no-embeddings",
        action="store_true",
        help="Skip embedding computation; store keyword-only chunks.",
    )
    p.add_argument(
        "--reset",
        action="store_true",
        help="Delete all existing chunks before seeding (full refresh).",
    )
    return p.parse_args()


async def _main() -> None:
    args = _parse_args()

    # Resolve DATABASE_URL
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        # Try to load from .env in the project root
        env_path = _SCRIPT_DIR.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("DATABASE_URL="):
                    db_url = line.split("=", 1)[1].strip()
                    break

    if not db_url:
        log.error(
            "DATABASE_URL not set. "
            "Export it or add it to the project .env file."
        )
        sys.exit(1)

    await seed(
        dry_run=args.dry_run,
        no_embeddings=args.no_embeddings,
        reset=args.reset,
        database_url=db_url,
    )


if __name__ == "__main__":
    asyncio.run(_main())
