# RAG Retrieval Flow
## AI-Powered Payment Gateway Platform

**Companion documents:** `PAYMENT_FLOW.md` · `FRAUD_SCORING_FLOW.md` · `DATA_FLOW_DIAGRAM.md`

---

## Overview

`POST /v1/rag/query` retrieves the most relevant policy chunks from the knowledge base to answer operational questions about refunds, chargebacks, fraud rules, settlement cycles, and payment failures.

The pipeline has two retrieval modes:

- **Vector search** — the query is embedded via `text-embedding-3-small` (1,536-dimensional unit-normalised vectors), then searched against the pgvector HNSW index using cosine similarity. Primary path when Azure OpenAI is configured.
- **Keyword search** — Dice-coefficient scoring over a pre-computed `keywords` column. Pure Python, < 5ms, no external I/O. Fallback path when embeddings are unavailable.

The mode is always reported in the response (`search_mode`, `embedding_used`). Callers never receive a silent degradation.

**Bug fixed during development:** the initial implementation used the `<#>` (negative inner product) operator instead of `<=>` (cosine distance). For unit-normalised vectors, `1 - <#>` gives values in [1, 2], not [0, 1]. The score normaliser clamped everything to 1.0, destroying relevance ranking. The fix was caught by a test that checked the output score range — a reminder that testing the output contract matters as much as testing the happy path.

---

## Mermaid Diagram — RAG Retrieval Pipeline

```mermaid
flowchart TD
    A([Client sends\nPOST /v1/rag/query]) --> B[FastAPI route\napp/rag/routes.py]

    B --> C{Pydantic v2\nvalidation\nquery ≥ 3 chars\ntop_k 1–20}
    C -- invalid --> CE([422 Unprocessable\napplication/problem+json])
    C -- valid --> D[RAGService.query&#40;req&#41;]

    D --> E{Azure OpenAI\nLLMClient\nconfigured?}

    subgraph VECTOR["Vector Search Path  ~180–450ms"]
        direction TB
        E -- yes --> F[client.embeddings.create\ntext-embedding-3-small\n1536 dims · unit-normalised]
        F -- success within 5s --> G[query_vector: list[float]]
        G --> H["pgvector HNSW query\nSELECT …,\n  1-(embedding <=> $1::vector) AS similarity\nFROM ai.knowledge_chunks\nWHERE embedding IS NOT NULL\nORDER BY embedding <=> $1::vector\nLIMIT top_k"]
        H --> I[Rows with\ncosine similarity ∈ 0, 1]
        I --> J{min_score\nfilter?}
        J -- yes --> K[WHERE similarity ≥ min_score]
        J -- no --> L[All rows returned]
        K & L --> M[search_mode = vector\nembedding_used = true]
    end

    subgraph KEYWORD["Keyword Fallback Path  &lt; 5ms"]
        direction TB
        E -- no --> N[Keyword search\npure Python · no I/O]
        F -- timeout > 5s --> N
        F -- error --> N
        N --> O["SELECT chunk_id, category, keywords,\n  content, source_document, section_title\nFROM ai.knowledge_chunks\nWHERE embedding IS NULL OR true\n+ optional category filter"]
        O --> P["Dice-coefficient scoring:\n  tokens_query = tokenise&#40;query&#41;\n  tokens_chunk = set&#40;keywords.split&#40;&#41;&#41;\n  overlap = |Q ∩ C|\n  score = overlap / √&#40;|Q| × |C|&#41;"]
        P --> Q[Normalise scores to 0, 1\nrelative to best match\nSort descending · take top_k]
        Q --> R[search_mode = keyword\nembedding_used = false]
    end

    M & R --> S[Build RAGQueryResponse\nchunks[] · search_mode · total_chunks_searched\nmodel_version · embedding_used · queried_at]

    S --> Z([200 OK\napplication/json])

    style VECTOR fill:#1e2761,color:#cadcfc,stroke:#cadcfc
    style KEYWORD fill:#1a2040,color:#94a3b8,stroke:#334155
```

---

## Knowledge Base Structure

The knowledge base is seeded by `scripts/seed_knowledge_base.py` into the `ai.knowledge_chunks` table.

### Policy documents and chunk counts

| Document | Category | Chunks | Key sections |
|---|---|---|---|
| `refund_policy.md` | `refund` | 9 | Eligibility, partial refunds, UPI-specific, international |
| `chargeback_policy.md` | `chargeback` | 9 | Triggers, merchant dispute, liability, network timelines |
| `fraud_policy.md` | `fraud` | 9 | Risk tiers, rule categories, escalation thresholds |
| `settlement_policy.md` | `settlement` | 11 | Cycles by method, fees, failed settlements, reconciliation |
| `payment_failure_policy.md` | `payment_failure` | 10 | Failure codes, retry policy, contention, provider errors |
| **Total** | | **48** | All 48 embedded (verified: `SELECT COUNT(*), COUNT(embedding) = 48, 48`) |

### Table schema (simplified)

```sql
-- ai.knowledge_chunks
chunk_id        TEXT PRIMARY KEY          -- SHA-256[:24] of (filename, section, index)
category        TEXT                      -- refund | chargeback | fraud | settlement | payment_failure
source_document TEXT                      -- e.g. refund_policy.md
section_title   TEXT                      -- markdown H2 heading
content         TEXT                      -- full section text
keywords        TEXT                      -- space-separated tokens for Dice-coefficient search
embedding       vector(1536)              -- text-embedding-3-small; NULL until seeded
content_hash    TEXT                      -- SHA-256[:16] for idempotent re-seeding
ingested_at     TIMESTAMPTZ
```

### pgvector HNSW index

```sql
CREATE INDEX ix_knowledge_chunks_embedding
    ON ai.knowledge_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```

`vector_cosine_ops` tells pgvector to use the `<=>` cosine distance operator for index lookups, which aligns with the similarity formula in the query.

---

## Step-by-Step Narrative

### Step 1 — HTTP Boundary

`POST /v1/rag/query` in `services/ai-service/app/rag/routes.py`. `RAGQueryRequest` validation:

- `query` — minimum 3 characters; maximum 500
- `top_k` — integer in [1, 20], default 5
- `min_score` — float in [0.0, 1.0], default 0.0
- `category_filter` — optional; one of `refund | chargeback | fraud | settlement | payment_failure | general`

A 422 with `application/problem+json` is returned on validation failure.

### Step 2 — Path Decision

`RAGService.query()` in `services/ai-service/app/rag/service.py` checks `llm.is_configured`. If true, it attempts the vector path inside `asyncio.wait_for(..., timeout=_EMBED_TIMEOUT)` (5-second timeout).

### Step 3a — Vector Path: Embedding

`client.embeddings.create(model=settings.azure_openai_embed_deployment, input=[query])` calls the Azure OpenAI Embeddings API. The response is a 1,536-dimensional list of floats. `text-embedding-3-small` produces unit-normalised vectors — the dot product of two vectors equals their cosine similarity.

Fallback to keyword path occurs on:
- `asyncio.TimeoutError` (> 5 seconds)
- Any `Exception` from the embeddings API

### Step 3b — Vector Path: pgvector Query

```sql
SELECT
    chunk_id, category, source_document,
    section_title, content, keywords,
    1 - (embedding <=> $1::vector) AS similarity
FROM ai.knowledge_chunks
WHERE embedding IS NOT NULL
  [AND category = $2]                        -- if category_filter provided
  [AND 1-(embedding <=> $1::vector) >= $3]   -- if min_score provided
ORDER BY embedding <=> $1::vector             -- ascending distance = descending similarity
LIMIT $4                                      -- top_k
```

**Why `<=>` not `<#>`:** `<=>` is the cosine distance operator. `1 - cosine_distance = cosine_similarity ∈ [0, 1]`. The `<#>` operator returns the *negative* inner product, which for unit vectors gives `1 - (-cosine_sim) = 1 + cosine_sim ∈ [0, 2]` — a bug that was caught by testing score ranges.

**HNSW performance:** with 48 chunks, the ANN query takes 1–3ms. At 1M+ chunks, query time is still sub-millisecond with a properly tuned `ef_search` parameter (ADR-009 revisit trigger).

### Step 4a — Keyword Path: Fetch All Chunks

When the vector path is unavailable, all chunks are fetched from Postgres (with optional category filter). At 48 chunks, this is a small result set; Python-side scoring is fast.

### Step 4b — Keyword Path: Dice-Coefficient Scoring

```python
def _dice_score(query_tokens: set[str], chunk_keywords: set[str]) -> float:
    overlap = len(query_tokens & chunk_keywords)
    if overlap == 0:
        return 0.0
    return overlap / math.sqrt(len(query_tokens) * len(chunk_keywords))
```

Scores are normalised to [0, 1] relative to the best match in the result set, so `min_score` filtering works consistently across both modes.

**Tokenisation:** stop words removed (`a`, `an`, `the`, `is`, `of`, etc.); lowercase; non-alpha stripped. "How long does a UPI refund take?" → `{"long", "upi", "refund", "take"}`.

**Known keyword fallback misses:**
1. "What is FATF?" — no literal "FATF" token in the `keywords` column; the chunk uses "Financial Action Task Force". **Mitigation:** explicit synonym expansion at ingest time.
2. "Chargeback liability rules" — "liability" has weak Dice overlap. **Mitigation:** add domain synonyms to keyword extraction.

---

## Response Schema

```json
{
  "query": "How long does a UPI refund take?",
  "chunks": [
    {
      "chunk_id": "a1b2c3d4e5f6a1b2c3d4e5f6",
      "category": "refund",
      "source_document": "refund_policy.md",
      "section_title": "Refund Processing",
      "content": "UPI refunds settle on a T+1 cycle …",
      "score": 0.87
    },
    {
      "chunk_id": "b2c3d4e5f6a1b2c3d4e5f6a1",
      "category": "refund",
      "source_document": "refund_policy.md",
      "section_title": "Eligibility",
      "content": "A transaction is eligible for refund when …",
      "score": 0.72
    }
  ],
  "search_mode": "vector",
  "total_chunks_searched": 48,
  "model_version": "text-embedding-3-small+hnsw",
  "embedding_used": true,
  "queried_at": "2026-06-22T08:20:14.567890+00:00"
}
```

**Chunks are always sorted descending by score.** The first chunk is always the most relevant.

---

## Idempotent Knowledge Base Seeder

`scripts/seed_knowledge_base.py` uses content-hash-aware upserts:

```sql
INSERT INTO ai.knowledge_chunks (chunk_id, …, content_hash, embedding)
VALUES ($1, …, $N)
ON CONFLICT (chunk_id) DO UPDATE SET
    content         = EXCLUDED.content,
    keywords        = EXCLUDED.keywords,
    embedding       = EXCLUDED.embedding,
    content_hash    = EXCLUDED.content_hash,
    ingested_at     = now()
WHERE ai.knowledge_chunks.content_hash  != EXCLUDED.content_hash
   OR ai.knowledge_chunks.embedding      IS NULL
   OR ai.knowledge_chunks.embedding     IS DISTINCT FROM EXCLUDED.embedding
```

The three-part WHERE clause ensures:
1. Content changes always trigger a re-embed
2. `NULL → value` embedding transitions always update (the original bug that was fixed — a simple `content_hash != EXCLUDED.content_hash` check silently skipped rows where content was unchanged but embeddings were NULL)
3. Embedding model changes trigger a re-embed

**Re-run behaviour on unchanged documents:**
```
Processing: refund_policy.md         9 chunks — 0 updated
Processing: chargeback_policy.md     9 chunks — 0 updated
…
Total: 48 chunks, 0 new, 0 updated
```

---

## Retrieval Quality

From `Docs/Evaluation/EVALUATION_RESULTS.md` §5:

| Metric | Vector mode | Keyword mode |
|---|---|---|
| Top-1 accuracy | **100%** (10/10) | **80%** (8/10) |
| Latency | 180–450ms | < 5ms |
| Mean cosine similarity | 0.856 | N/A |
| Score range | [0, 1] | [0, 1] (normalised) |

---

## Graceful Degradation Summary

```
Azure OpenAI available + embedding within 5s
    → Vector search → search_mode: "vector" · embedding_used: true
    → Cosine similarity scores directly comparable across queries

Azure OpenAI not configured
    → Keyword search → search_mode: "keyword" · embedding_used: false
    → Dice-coefficient scores normalised to [0,1] per query

Azure OpenAI configured but timeout / error
    → Keyword search → search_mode: "keyword" · embedding_used: false
    → Warning logged: rag_embed_timeout

In ALL cases:
    → HTTP 200 is always returned
    → chunks[] is always populated (unless query has no keyword overlap)
    → search_mode and embedding_used are always set
    → No "service unavailable" errors for the caller
```
