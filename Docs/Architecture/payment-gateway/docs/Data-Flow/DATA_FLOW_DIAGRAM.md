# End-to-End Data Flow Diagram
## AI-Powered Payment Gateway Platform

**Companion documents:** `PAYMENT_FLOW.md` · `FRAUD_SCORING_FLOW.md` · `RAG_RETRIEVAL_FLOW.md`

---

## System Architecture Overview

Five containers on a private bridge network (`pg-net`). Only ports 3000, 8000, and 8100 are exposed to the host. Postgres and Redis are reachable only from the other containers via service-DNS names.

```
Host machine
├── :3000  pg-frontend  (React + nginx)
├── :8000  pg-core-api  (FastAPI)
├── :8100  pg-ai-service (FastAPI)
│
│   [internal — not exposed to host]
├── :5432  pg-postgres  (PostgreSQL 16 + pgvector)
└── :6379  pg-redis     (Redis 7)
```

---

## Mermaid Diagram — Full System Data Flow

```mermaid
flowchart TB
    %% ── External actors ────────────────────────────────────────────────────
    Browser(["🖥  Browser\nlocalhost:3000"])
    CurlClient(["⌨  API Client\ncurl / SDK"])
    AzureOAI(["☁  Azure OpenAI\ngpt-4o-mini\ntext-embedding-3-small"])

    %% ── Frontend ────────────────────────────────────────────────────────────
    subgraph FE["pg-frontend  :3000  nginx"]
        direction TB
        Dashboard["Transaction Monitor\nSummaryCards · PaymentsTable\nDetailPanel · FraudPanel"]
        PolicyUI["Policy Assistant\nRAGPanel · SuggestionChips"]
        Proxy["/api/core/* → core-api:8000\n/api/ai/*   → ai-service:8100\nSSE-safe: proxy_buffering off"]
    end

    %% ── Core API ────────────────────────────────────────────────────────────
    subgraph CORE["pg-core-api  :8000  FastAPI"]
        direction TB
        CoreRoutes["Routes\nPOST /v1/payments\nGET  /v1/payments\nGET  /v1/payments/{id}\n/healthz  /readyz  /metrics"]
        PaySvc["PaymentService\ncreate_payment · refund\nlist · get"]
        WalletSvc["WalletService\ncredit · debit\noptimistic retry"]
        LedgerSvc["LedgerService\npost_payment · reverse\nvalidate balance"]
        Repos["Repositories\nPaymentRepository\nWalletRepository\nLedgerRepository"]
    end

    %% ── AI Service ──────────────────────────────────────────────────────────
    subgraph AI["pg-ai-service  :8100  FastAPI"]
        direction TB
        AIRoutes["Routes\nPOST /v1/fraud/score\nPOST /v1/rag/query\n/healthz  /readyz"]
        FraudSvc["FraudScoringService\nTier 1: rules.evaluate()\nTier 2: LLM explanation"]
        RAGSvc["RAGService\nvector_search\nkeyword_search fallback"]
        LLMClient["LLMClient\nAzureOpenAI wrapper\nis_configured check"]
    end

    %% ── Data layer ──────────────────────────────────────────────────────────
    subgraph PG["pg-postgres  :5432  PostgreSQL 16 + pgvector"]
        direction TB
        CoreSchema["core schema\nusers  wallets\ntransactions"]
        LedgerSchema["ledger schema\nentries\n(no FK to core — by design)"]
        AISchema["ai schema\nknowledge_chunks\nvector(1536) · HNSW index"]
        OpsSchema["ops schema\nhealth_probe\naudit_log (schema only)"]
    end

    Redis["pg-redis  :6379  Redis 7\nAOF persistence\nidem:lock · idem:resp\nvelocity counters"]

    %% ── Connection flows ────────────────────────────────────────────────────

    Browser -- "HTTP :3000" --> FE
    CurlClient -- "HTTP :8000" --> CoreRoutes
    CurlClient -- "HTTP :8100" --> AIRoutes

    Dashboard & PolicyUI --> Proxy
    Proxy -- "/api/core/ → :8000" --> CoreRoutes
    Proxy -- "/api/ai/  → :8100" --> AIRoutes

    CoreRoutes --> PaySvc --> WalletSvc & LedgerSvc
    PaySvc & WalletSvc & LedgerSvc --> Repos

    Repos -- "asyncpg\npool_size=10" --> CoreSchema
    Repos --> LedgerSchema
    PaySvc -- "idempotency\nidem:lock · idem:resp" --> Redis

    AIRoutes --> FraudSvc & RAGSvc
    FraudSvc --> LLMClient
    RAGSvc --> LLMClient
    LLMClient -- "embeddings · chat" --> AzureOAI
    RAGSvc -- "asyncpg\nvector <=> query" --> AISchema
    FraudSvc -. "no DB writes\npure computation" .-> PG

    style FE fill:#0f172a,color:#cadcfc,stroke:#38bdf8
    style CORE fill:#1e2761,color:#e2e8f0,stroke:#cadcfc
    style AI fill:#1a2040,color:#e2e8f0,stroke:#94a3b8
    style PG fill:#0d1117,color:#e2e8f0,stroke:#334155
```

---

## Database Schema Boundaries

```mermaid
erDiagram
    direction LR

    %% core schema
    users {
        uuid user_id PK
        text email
        text kyc_status
        timestamptz created_at
    }
    wallets {
        uuid wallet_id PK
        uuid user_id FK
        text currency
        numeric_18_4 balance
        int version
    }
    transactions {
        uuid transaction_id PK
        uuid user_id FK
        text merchant_id
        numeric_18_4 amount
        text currency
        text payment_method
        text status
        text idempotency_key
        uuid parent_transaction FK
        jsonb metadata
        timestamptz created_at
    }

    %% ledger schema — intentionally NO FK to core.transactions
    ledger_entries {
        uuid entry_id PK
        uuid transaction_id "app-level ref only"
        uuid wallet_id "app-level ref only"
        text direction
        numeric_18_4 amount
        timestamptz posted_at
    }

    %% ai schema
    knowledge_chunks {
        text chunk_id PK
        text category
        text source_document
        text section_title
        text content
        text keywords
        vector_1536 embedding
        text content_hash
        timestamptz ingested_at
    }

    users ||--o{ wallets : "owns"
    users ||--o{ transactions : "initiates"
    transactions ||--o{ transactions : "refund parent"
```

**Schema isolation principles:**

- `ledger.entries` has **no FK to `core.transactions`** — preserves the extraction path to a dedicated ledger Postgres cluster (ADR-006)
- `ai.knowledge_chunks` has **no relation to the payment domain** — AI service reads only from the `ai` schema
- The `ops` schema is a namespace placeholder for audit logs and feature flags; no application data lives there

---

## Container Startup Order

```mermaid
graph LR
    PG["pg-postgres\nhealthcheck:\npg_isready"]
    RDS["pg-redis\nhealthcheck:\nredis-cli ping"]
    CORE["pg-core-api\nhealthcheck:\n/healthz"]
    AISC["pg-ai-service\nhealthcheck:\n/healthz"]
    FE["pg-frontend\nhealthcheck:\nwget /"]

    PG --> CORE
    RDS --> CORE
    PG --> AISC
    RDS --> AISC
    CORE --> FE
    AISC --> FE
```

`depends_on` with `condition: service_healthy` ensures:
1. Postgres and Redis are accepting connections before either API service starts
2. Both API services are returning 200 on `/healthz` before the frontend starts
3. The frontend never proxies to a not-yet-ready backend

---

## Request Routing — Detailed

### Frontend proxy (nginx)

| Browser path | nginx directive | Internal destination |
|---|---|---|
| `/` (SPA) | `try_files $uri $uri/ /index.html` | React static bundle |
| `/api/core/v1/payments` | `proxy_pass http://core-api:8000/v1/payments` | core-api payment routes |
| `/api/ai/v1/fraud/score` | `proxy_pass http://ai-service:8100/v1/fraud/score` | ai-service fraud route |
| `/api/ai/v1/rag/query` | `proxy_pass http://ai-service:8100/v1/rag/query` | ai-service RAG route |

The AI proxy adds `proxy_buffering off`, `proxy_cache off`, `Connection ''`, and `chunked_transfer_encoding off` — the four directives required for correct SSE (Server-Sent Events) streaming in future agent phases.

### Direct API access

```
curl :8000/v1/payments          # bypasses nginx; hits core-api directly
curl :8100/v1/fraud/score       # bypasses nginx; hits ai-service directly
```

Both are valid for development and demo; the frontend always uses the nginx proxy paths (`/api/core/*`, `/api/ai/*`).

---

## Data Flow — Payment Creation (condensed)

```
Browser → nginx :3000
  → proxy_pass core-api:8000
    → FastAPI route: validate Pydantic
      → Redis: GET idem:resp:{key}          # idempotency fast-path
        → PaymentService.create_payment()
          → WalletRepository: SELECT wallet
          → PaymentRepository: INSERT pending
          → WalletRepository: UPDATE balance WHERE version=N
          → LedgerRepository: INSERT 2 entries (DEBIT + CREDIT)
          → PaymentRepository: UPDATE status=success
        → AsyncSession.commit()
      → Redis: SET idem:resp:{key}          # prime replay cache
    → 201 Created { transaction_id, status: "success", … }
← 201 Created
```

---

## Data Flow — Fraud Scoring (condensed)

```
Browser (Detail drawer) → nginx :3000
  → proxy_pass ai-service:8100
    → FastAPI route: validate Pydantic
      → FraudScoringService.score()
        → rules.evaluate()                  # Tier 1: ~1ms, no I/O
          → 15 rule functions → raw_score → decision
        → FraudScoringService._explain()    # Tier 2: best-effort
          → asyncio.wait_for(LLM call, 3s)
          → on success: LLM one-sentence explanation (llm_used=true)
          → on failure: template explanation (llm_used=false)
    → 200 OK { risk_score, decision, rule_hits, explanation, … }
← 200 OK (always, regardless of LLM availability)
```

---

## Data Flow — RAG Retrieval (condensed)

```
Browser (Policy Assistant) → nginx :3000
  → proxy_pass ai-service:8100
    → FastAPI route: validate Pydantic
      → RAGService.query()
        → if llm.is_configured:
            asyncio.wait_for(embeddings.create(query), 5s)
            → vector <=> HNSW query → cosine scores [0,1]
            → search_mode=vector, embedding_used=true
          else / timeout:
            SELECT chunks + Dice-coefficient scoring
            → search_mode=keyword, embedding_used=false
    → 200 OK { chunks[], search_mode, embedding_used, … }
← 200 OK (always, regardless of embedding availability)
```

---

## Network Isolation

```
╔══════════════════════════════════════════════════════════╗
║  pg-net  (bridge)                                        ║
║                                                          ║
║  pg-postgres:5432   ←── core-api (asyncpg pool_size=10)  ║
║                     ←── ai-service (asyncpg)             ║
║                                                          ║
║  pg-redis:6379      ←── core-api (redis.asyncio)         ║
║                     ←── ai-service (redis.asyncio)       ║
║                                                          ║
║  pg-core-api:8000   ←── pg-frontend (nginx proxy)        ║
║  pg-ai-service:8100 ←── pg-frontend (nginx proxy)        ║
╚══════════════════════════════════════════════════════════╝
         ↕ exposed to host
    :3000 (frontend)
    :8000 (core-api)
    :8100 (ai-service)
```

Postgres (:5432) and Redis (:6379) are **not** exposed to the host in production configuration. They are accessible on the host in this local development setup for direct database inspection; in a production Kubernetes deployment they would be Cluster-internal services only.

---

## Production Target Architecture

The production architecture documented in `Docs/Evaluation/PRODUCTION_READINESS.md` evolves this five-container stack into a 14-service Kubernetes deployment:

```mermaid
flowchart TB
    subgraph Internet
        Client
    end
    subgraph AzureFrontDoor["Azure Front Door + DDoS Standard"]
        WAF["WAF\nOWASP CRS 3.2"]
    end
    subgraph AKS["AKS — Active-Active\nCentral India + South India"]
        Kong["Kong API Gateway\nJWT validation · rate limits"]
        subgraph Services["14 Domain Services"]
            identity-service
            payment-service
            wallet-service
            ledger-service
            fraud-service
            rag-service
            settlement-service
            notification-service
            others["…7 more"]
        end
        subgraph Mesh["Istio Service Mesh"]
            mTLS["mTLS between all services"]
        end
    end
    subgraph Data["Data Layer"]
        PGCore["PostgreSQL HA\n1 primary + 2 standbys\nPgBouncer"]
        PGLedger["Ledger Postgres\n(separate cluster)"]
        Redis["Redis Cluster\n3 primary + 3 replica"]
        VectorDB["pgvector or Milvus\n> 1M embeddings"]
    end
    Client --> AzureFrontDoor --> Kong --> Services
    Services --> Data
```

Every domain package in the current codebase corresponds to exactly one production service. The extraction path is a `git mv` + new `Containerfile` + updated `Deployment` manifest — the code does not change.
