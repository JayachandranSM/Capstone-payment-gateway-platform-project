# MVP Implementation Plan

**Role:** Staff Engineer authoring an execution plan for a single developer
**Target:** Demo-ready system in **4–5 days (~42 hours of focused work)**
**Hard deadline:** 6 PM IST, June 24 — code freeze
**Demo:** June 25/26 — 8-minute live demo + 2-minute Q&A

---

## 0. Scope discipline — what we cut and why

The architecture review described **14 microservices** and **7 data stores**. We will not build that in 5 days. Every cut below is deliberate, defensible in the panel, and documented in `docs/decisions/DECISIONS.md`.

| Architecture component | MVP decision | Defensible position for review |
|---|---|---|
| 14 microservices | **3 services** (`core-api`, `ai-service`, `ingestion-worker`) | Service boundaries preserved as packages within `core-api`; documented as "modular monolith with extraction roadmap" |
| Polyglot persistence (7 stores) | **Postgres + Redis only** | PGVector covers vector; tsvector covers BM25. Two stores is a feature, not a bug, at this scale |
| Kafka | **Redis Streams** for events, plus FastAPI BackgroundTasks for in-process fan-out | Logical event topics preserved; documented swap to Kafka |
| Separate ledger cluster | **Separate `ledger` schema** in same Postgres | Code never crosses schemas via JOIN — only via service interface. Production swap is operational, not architectural |
| OpenSearch + Milvus | **Postgres tsvector + PGVector** | Hybrid search via SQL — one less service to operate |
| Istio / K8s | **Docker Compose** | Capstone is single-host. Production deploy is documented, not built |
| Real Stripe integration | **Stripe Test mode for one happy path**; mock client for the rest | Demonstrates integration capability without paying for sandbox flakiness during demo |
| Full enterprise frontend (Next.js SSR, auth federation, design system) | **React 18 + TypeScript + Vite** with Tailwind, TanStack Query, Recharts. Three pages, no SSR, no design-system install | Production-feel UI without the build-system tax. Adds ~9 h vs Streamlit but materially raises the perceived production-readiness of the demo |
| MFA / KYC workflows | **Stubbed** (endpoints return status, no real verification) | Documented as out-of-scope; production design retained |
| Tokenisation vault / HSM | **SHA-256 hash of PAN with documented PCI gap** | Clear callout in DECISIONS.md — this is the single biggest "MVP vs prod" gap |
| Webhooks | **Logged-only delivery** (no actual HTTP POST out) | Avoids needing a public URL during demo |

**Stack lock (per constraint):** FastAPI · PostgreSQL 16 + PGVector · Redis 7 · OpenAI API · LangGraph · Docker Compose. **Plus** React 18 + TypeScript + Vite + Tailwind + TanStack Query + Recharts (UI), nginx (UI prod serve), Flan-T5 (local fallback), Faker (synthetic data), Locust (load test), pytest (unit/integration), DeepEval (RAG metrics).

---

## 1. System layout

```
payment-gateway/                          ← repo root
├── README.md                             ← install + demo guide (assessment requirement)
├── docker-compose.yml                    ← one-command startup
├── .env.example
├── Makefile                              ← make up | seed | test | demo
├── requirements/                         ← assessment: original brief
│   └── original-brief.pdf
├── docs/
│   ├── architecture/
│   │   ├── ARCHITECTURE_REVIEW.md        ← already done
│   │   └── DIAGRAMS.md                   ← already done
│   ├── data-flow/flows.md
│   ├── decisions/DECISIONS.md            ← ADRs (already drafted in review §10)
│   └── evaluation/RESULTS.md             ← Day 5 deliverable
├── services/
│   ├── core-api/                         ← FastAPI · port 8000
│   ├── ai-service/                       ← FastAPI · port 8100
│   └── ingestion-worker/                 ← Python worker · run on demand
├── frontend/                             ← React + TS + Vite · nginx · port 3000
├── tests/
│   ├── integration/
│   ├── load/                             ← Locust
│   └── eval/                             ← LLM-as-judge + DeepEval
├── infra/
│   ├── postgres/init.sql                 ← extensions + schemas
│   └── grafana/dashboards/
└── shared/                               ← copied into each service image
    ├── logging_config.py                 ← structlog JSON
    ├── db.py                             ← asyncpg pool factory
    ├── redis_client.py
    ├── otel.py
    └── schemas/                          ← shared Pydantic models
```

---

## 2. Service 1 — `core-api`

The transactional system: identity, wallet, payment, ledger, fraud, settlement, dispute, merchant. Single FastAPI app, **strictly modular packages** so boundaries are visible to a reviewer.

### 2.1 Folder structure

```
services/core-api/
├── Dockerfile
├── pyproject.toml
├── main.py                               ← FastAPI app factory, lifespan hooks
├── settings.py                           ← Pydantic BaseSettings (env-driven)
├── app/
│   ├── __init__.py
│   ├── middleware/
│   │   ├── idempotency.py                ← Redis-backed; section 2.4
│   │   ├── request_id.py                 ← propagates trace_id
│   │   └── auth.py                       ← JWT verify
│   ├── identity/
│   │   ├── router.py
│   │   ├── service.py
│   │   ├── models.py                     ← Pydantic
│   │   └── repository.py                 ← SQL via asyncpg
│   ├── wallet/                           ← same shape
│   ├── payment/
│   │   ├── router.py
│   │   ├── service.py                    ← orchestrates fraud → ledger → provider
│   │   ├── providers/
│   │   │   ├── base.py
│   │   │   ├── stripe_sandbox.py
│   │   │   └── mock.py
│   │   ├── saga.py                       ← compensation on partial failure
│   │   └── repository.py
│   ├── ledger/                           ← double-entry; isolated package
│   ├── fraud/
│   │   ├── router.py
│   │   ├── rules.py                      ← velocity, BIN-country, micro-amount
│   │   ├── model.py                      ← scikit GBT loaded at startup
│   │   └── service.py
│   ├── settlement/
│   ├── dispute/
│   ├── merchant/
│   └── health/                           ← /healthz, /readyz, /metrics
└── tests/
    ├── unit/
    └── integration/
```

### 2.2 Database schema (Postgres — `core` schema)

```sql
-- infra/postgres/init.sql (excerpt for core-api)
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS ledger;

CREATE TABLE core.users (
  user_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email       TEXT UNIQUE NOT NULL,
  pw_hash     TEXT NOT NULL,
  kyc_status  TEXT CHECK (kyc_status IN ('verified','pending','failed')) DEFAULT 'pending',
  country     CHAR(2) NOT NULL,
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE core.wallets (
  wallet_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID REFERENCES core.users,
  currency    CHAR(3) NOT NULL,
  balance     NUMERIC(18,4) NOT NULL DEFAULT 0,
  UNIQUE (user_id, currency)
);

CREATE TABLE core.merchants (
  merchant_id     TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  api_key_hash    TEXT NOT NULL,
  webhook_url     TEXT
);

CREATE TABLE core.transactions (
  transaction_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id            UUID,
  merchant_id        TEXT,
  amount             NUMERIC(18,4) NOT NULL,
  currency           CHAR(3) NOT NULL,
  payment_method     TEXT CHECK (payment_method IN ('card','bank_transfer','wallet','upi')),
  status             TEXT CHECK (status IN ('pending','success','failed','flagged','reversed')),
  failure_reason     TEXT,
  fraud_score        NUMERIC(4,3),
  chargeback_flag    BOOLEAN DEFAULT false,
  settlement_status  TEXT CHECK (settlement_status IN ('settled','pending','disputed','reversed')) DEFAULT 'pending',
  resolution_notes   TEXT,
  kyc_status         TEXT,
  country_sender     CHAR(2),
  country_receiver   CHAR(2),
  idempotency_key    TEXT UNIQUE,
  metadata           JSONB DEFAULT '{}'::jsonb,
  created_at         TIMESTAMPTZ DEFAULT now(),
  updated_at         TIMESTAMPTZ DEFAULT now(),
  -- full-text index for keyword side of hybrid search
  search_doc         tsvector GENERATED ALWAYS AS (
    to_tsvector('english',
      coalesce(failure_reason,'') || ' ' ||
      coalesce(resolution_notes,'') || ' ' ||
      coalesce(status,'') || ' ' ||
      coalesce(payment_method,''))
  ) STORED
);

CREATE INDEX ON core.transactions (user_id, created_at DESC);
CREATE INDEX ON core.transactions (merchant_id, created_at DESC);
CREATE INDEX ON core.transactions (status) WHERE status IN ('flagged','failed');
CREATE INDEX ON core.transactions USING GIN (search_doc);

CREATE TABLE ledger.entries (
  entry_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  transaction_id  UUID NOT NULL,
  account_id      UUID NOT NULL,
  direction       TEXT CHECK (direction IN ('DEBIT','CREDIT')),
  amount          NUMERIC(18,4) NOT NULL,
  currency        CHAR(3) NOT NULL,
  posted_at       TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON ledger.entries (transaction_id);

CREATE TABLE core.fraud_cases (
  case_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  transaction_id   UUID REFERENCES core.transactions,
  score            NUMERIC(4,3),
  rule_hits        TEXT[],
  state            TEXT CHECK (state IN ('open','investigating','resolved','dismissed')) DEFAULT 'open',
  analyst_notes    TEXT,
  agent_summary    TEXT,            -- written by ai-service
  judge_score      INT,             -- 1..5 from LLM-as-judge
  created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE core.disputes (
  dispute_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  transaction_id   UUID REFERENCES core.transactions,
  reason           TEXT,
  state            TEXT CHECK (state IN ('opened','under_review','resolved')) DEFAULT 'opened',
  evidence_urls    TEXT[],
  resolution_notes TEXT,
  created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE core.settlements (
  settlement_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  merchant_id      TEXT REFERENCES core.merchants,
  cycle_date       DATE,
  expected_amount  NUMERIC(18,4),
  actual_amount    NUMERIC(18,4),
  variance         NUMERIC(18,4),
  status           TEXT,
  created_at       TIMESTAMPTZ DEFAULT now()
);
```

### 2.3 API endpoints

| Method | Path | Purpose | Auth | Idempotent? |
|---|---|---|---|---|
| POST | `/v1/auth/signup` | Create user | — | no |
| POST | `/v1/auth/login` | JWT issue | — | no |
| GET | `/v1/wallets` | List user wallets | user | — |
| POST | `/v1/payments` | Create payment | user/merchant | **yes** (Idempotency-Key) |
| GET | `/v1/transactions/{id}` | Fetch one | user/merchant | — |
| GET | `/v1/transactions` | Filter list (date, status, merchant) | user/merchant | — |
| POST | `/v1/refunds` | Refund a transaction | merchant | **yes** |
| POST | `/v1/fraud/score` | Score a candidate tx (used internally + by ai-service) | service | — |
| GET | `/v1/fraud/cases/{id}` | Fetch a case | analyst | — |
| POST | `/v1/disputes` | Open dispute | user | — |
| GET | `/v1/settlements/{merchant_id}` | Settlement report | merchant | — |
| GET | `/healthz` · `/readyz` · `/metrics` | Ops surface | — | — |

All write endpoints validate via Pydantic, return RFC 7807 problem details on error, and emit a `trace_id` header.

### 2.4 Implementation order (within Day 2)

1. App factory, settings, lifespan hooks (load GBT model, open DB + Redis pools) — **45 min**
2. Structured logging (structlog → JSON to stdout) + request-id middleware — **30 min**
3. `identity` package (signup, login, JWT) — **1.5 h**
4. `wallet` package — **45 min**
5. `payment` package — router + service + mock provider + Stripe stub — **3 h**
6. **Idempotency middleware**: Redis `SET NX` with TTL 24h on `(merchant_id, key)`; cache the full response body keyed by `idem:resp:{key}` — **1.5 h**
7. `ledger` package — atomic double-entry posting in a single DB transaction — **1.5 h**
8. `fraud` package — load pre-trained tiny GBT (`scikit-learn` on Faker data), 5 rules, sync scoring — **2 h**
9. `dispute` · `settlement` · `merchant` packages (CRUD-heavy) — **1.5 h**
10. Health, readiness (DB ping, Redis ping, model loaded check), Prometheus metrics — **30 min**
11. Connection pooling check, smoke test all endpoints via httpie — **30 min**

**Subtotal: ~14 hours** (Day 2 + spillover into Day 3 morning).

### 2.5 Testing strategy

| Test type | What | Tool | Coverage target |
|---|---|---|---|
| Unit | rules.py, idempotency.py, ledger posting invariants | pytest | 80% of `payment`, `fraud`, `ledger` |
| Integration | POST /payments end-to-end against real Postgres + Redis | pytest + testcontainers (or compose) | All write endpoints, happy + sad path |
| Property-based | Ledger: sum(debits) == sum(credits) for any transaction | hypothesis | 1 property test |
| Load | 200 RPS on /payments for 60 s | Locust | P99 < 500 ms locally |
| Contract | Each provider impl satisfies `PaymentProvider` ABC | pytest parametrize | Both Stripe and mock |

### 2.6 Demo role

- **All four demo scenarios** start with a transaction in `core.transactions`. This service owns the data the AI reasons over.
- **Scenario 3 (card-testing fraud)** is mostly here — the rules engine + GBT flag a synthetic burst of micro-payments during the demo; ai-service then explains.
- **Scenario 4 (settlement summary)** uses `core.settlements` rows generated by a small nightly-style batch job (run manually before demo).

### 2.7 Estimated hours: **14 h**

---

## 3. Service 2 — `ai-service`

The intelligence layer: hybrid retrieval, RAG, multi-agent orchestration (LangGraph), LLM-as-judge, evaluation.

### 3.1 Folder structure

```
services/ai-service/
├── Dockerfile
├── pyproject.toml
├── main.py
├── settings.py
├── app/
│   ├── __init__.py
│   ├── llm/
│   │   ├── client.py                     ← OpenAI primary
│   │   ├── fallback.py                   ← Flan-T5 local (transformers)
│   │   ├── router.py                     ← tier 2 → tier 3 with circuit breaker
│   │   └── prompts/                      ← jinja2 templates, version-stamped
│   ├── retrieval/
│   │   ├── embedder.py                   ← sentence-transformers bge-base
│   │   ├── hybrid.py                     ← tsvector + pgvector + RRF
│   │   ├── reranker.py                   ← cross-encoder bge-reranker-base
│   │   └── filters.py
│   ├── agents/
│   │   ├── graph.py                      ← LangGraph state graph definition
│   │   ├── state.py                      ← TypedDict for graph state
│   │   ├── nodes/
│   │   │   ├── orchestrator.py
│   │   │   ├── fraud_investigator.py
│   │   │   ├── settlement_reconciler.py
│   │   │   ├── dispute_resolver.py
│   │   │   └── support_qa.py
│   │   ├── tools.py                      ← @tool decorated callables
│   │   └── judge.py                      ← LLM-as-judge online (small model)
│   ├── memory/
│   │   ├── session.py                    ← Redis, TTL 1h
│   │   └── episodic.py                   ← Postgres + pgvector
│   ├── eval/
│   │   ├── deepeval_suite.py
│   │   ├── rubrics.py
│   │   └── ground_truth.py
│   ├── router.py                         ← FastAPI routes
│   └── health/
└── tests/
    ├── unit/
    └── eval/                             ← retrieval accuracy tests
```

### 3.2 Database schema (Postgres — `ai` schema)

```sql
CREATE SCHEMA IF NOT EXISTS ai;

CREATE TABLE ai.embeddings (
  embedding_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_type    TEXT CHECK (source_type IN ('transaction','incident','kb_article')),
  source_id      UUID,
  chunk_text     TEXT NOT NULL,
  embedding      vector(768),               -- bge-base-en-v1.5
  metadata       JSONB DEFAULT '{}'::jsonb,
  created_at     TIMESTAMPTZ DEFAULT now()
);

-- HNSW for ANN, partial filters via metadata + JOIN
CREATE INDEX embeddings_hnsw ON ai.embeddings
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- tsvector index on chunk_text for hybrid keyword arm
ALTER TABLE ai.embeddings ADD COLUMN chunk_tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('english', chunk_text)) STORED;
CREATE INDEX ON ai.embeddings USING GIN (chunk_tsv);

CREATE TABLE ai.episodic_memory (
  memory_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  case_id        UUID,
  agent_name     TEXT,
  query          TEXT,
  outcome        TEXT,
  embedding      vector(768),
  effectiveness  NUMERIC(3,2),               -- 0..1, set when feedback arrives
  created_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON ai.episodic_memory USING hnsw (embedding vector_cosine_ops);

CREATE TABLE ai.agent_runs (
  run_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id      TEXT,
  agent_path      TEXT[],                   -- e.g. ['orchestrator','fraud_investigator']
  input           JSONB,
  steps           JSONB,                    -- list of {node, tool, observation}
  final_output    JSONB,
  judge_score     INT,
  tokens_used     INT,
  latency_ms      INT,
  trace_id        TEXT,
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE ai.evaluations (
  eval_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  suite           TEXT,
  metrics         JSONB,                    -- {faithfulness, context_precision, ...}
  ground_truth_id TEXT,
  created_at      TIMESTAMPTZ DEFAULT now()
);
```

### 3.3 API endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/assistant/query` | RAG Q&A (streamed via SSE); supports `filters` |
| POST | `/v1/agents/invoke` | Multi-agent run; `{agent, task, inputs, session_id}` |
| POST | `/v1/agents/explain-failure` | Convenience wrapper — explain a single tx failure |
| POST | `/v1/agents/settlement-summary` | Convenience wrapper — settlement variance explanation |
| POST | `/v1/agents/investigate-fraud` | Trigger fraud investigation for a transaction |
| POST | `/v1/eval/run` | Run a DeepEval suite (`rag_quality`, `fraud_decision`) |
| GET | `/v1/eval/latest` | Latest scorecard |
| GET | `/healthz` · `/readyz` · `/metrics` | Ops |

### 3.4 LangGraph design (concrete, not abstract)

**State:**
```python
class AgentState(TypedDict):
    task: str
    inputs: dict
    plan: list[str]
    steps: list[dict]           # {node, tool, args, observation}
    candidates: list[dict]      # ranked hypotheses
    judge_score: int | None
    final_output: dict | None
    session_id: str
    trace_id: str
```

**Nodes:** `orchestrator` → routes to one of {`support_qa`, `fraud_investigator`, `settlement_reconciler`, `dispute_resolver`} → `judge` → conditional edge: `if judge_score >= 4 → END else → orchestrator` (max 8 iterations, hard cap).

**Tools** (LangGraph `@tool`):
- `get_transaction(transaction_id)` — calls `core-api`
- `get_user_history(user_id, days)` — calls `core-api`
- `hybrid_search(query, filters)` — local retrieval module
- `rules_engine_query(transaction_id)` — calls `core-api` `/fraud/score`
- `settlement_expected(merchant_id, cycle_date)` — calls `core-api`
- `open_case(transaction_id, summary)` — writes to `core.fraud_cases`

### 3.5 Implementation order (Day 3 + half of Day 4)

1. LLM client + tier router with circuit breaker (5 failures in 30 s → open for 60 s) — **1 h**
2. Local Flan-T5 fallback — load on startup, simple prompt → completion adapter — **1 h**
3. Embedder + hybrid retrieval (tsvector + pgvector + RRF) — **2 h**
4. Reranker (cross-encoder, batched) — **45 min**
5. `assistant/query` endpoint, SSE streaming — **1.5 h**
6. LangGraph state + orchestrator node — **1 h**
7. Sub-agent nodes (fraud_investigator first — covers demo) — **2 h**
8. Tools (DB-backed) — **1 h**
9. LLM-as-judge node (gpt-4o-mini scoring rubric) — **1 h**
10. Session memory (Redis) + episodic memory (Postgres) — **1 h**
11. DeepEval suite + ground-truth dataset (~50 labelled examples) — **1.5 h**
12. Smoke test all 5 endpoints + agent traces visible in logs — **30 min**

**Subtotal: ~13 hours.**

### 3.6 Testing strategy

| Test type | What | Tool |
|---|---|---|
| Unit | RRF math, prompt template rendering, circuit breaker state machine | pytest |
| Retrieval accuracy | Recall@5, MRR on 50 labelled query→ground-truth-tx-id pairs | custom + pytest |
| LLM-as-judge correlation | Judge scores on 30 hand-graded responses; report Spearman ρ | DeepEval |
| RAG quality | DeepEval `faithfulness`, `answer_relevancy`, `context_precision` on the eval set | DeepEval |
| Agent trace correctness | For 5 scripted cases, assert expected tool-call sequence | pytest |
| Resilience | Simulate OpenAI 5xx → verify Flan-T5 fallback engages and answers degrade gracefully | pytest with monkeypatch |

### 3.7 Demo role

- **Scenario 1 (15% card failures):** support agent asks via the React support console → ai-service runs orchestrator → fraud_investigator → ranked causes ("BIN issuer X declining, regional outage at provider Y") streamed token-by-token; citations render as clickable chips; agent trace drawer slides in showing each step.
- **Scenario 2 (international decline):** RAG-only path; hybrid retrieval surfaces the failure_reason field plus similar resolved cases.
- **Scenario 3 (card-testing):** triggered by `/agents/investigate-fraud` after core-api flags the burst.
- **Scenario 4 (settlement summary):** `/agents/settlement-summary` produces a plain-English variance explanation with line items.
- **LLM-as-judge:** show the judge score in the UI for each agent response — proves online evaluation is live.
- **Local fallback:** during demo, kill OPENAI_API_KEY env var on one query → Flan-T5 answers with a "degraded mode" banner.

### 3.8 Estimated hours: **13 h**

---

## 4. Service 3 — `ingestion-worker`

Generates synthetic data, chunks, embeds, upserts into `ai.embeddings`. Runs **on demand** (not always-on) — invoked at setup and after new data lands.

### 4.1 Folder structure

```
services/ingestion-worker/
├── Dockerfile
├── pyproject.toml
├── run.py                                ← CLI entrypoint
├── app/
│   ├── generators/
│   │   ├── transactions.py               ← Faker-based, 15k records
│   │   ├── fraud_scenarios.py            ← card testing, geo-mismatch, velocity bursts
│   │   └── kb_articles.py                ← 30 hand-written KB chunks
│   ├── pipeline/
│   │   ├── chunker.py                    ← per-incident chunk template
│   │   ├── embedder.py                   ← bge-base-en-v1.5, batched
│   │   └── loader.py                     ← upsert into ai.embeddings
│   ├── seeders/
│   │   ├── seed_core.py                  ← users, wallets, merchants, transactions
│   │   └── seed_evaluation.py            ← labelled ground-truth set
│   └── settings.py
└── tests/
    └── test_generators.py                ← sanity: distributions are reasonable
```

### 4.2 Database schema

No new tables — writes into `core.*` (seed data) and `ai.embeddings` (indexed data). Watermark via `MAX(created_at)` per source_type for incremental runs.

### 4.3 CLI commands (this service has no HTTP API)

```bash
python run.py generate --count 15000 --output data/synthetic.jsonl
python run.py seed-core --input data/synthetic.jsonl
python run.py embed --source transactions --where "status IN ('failed','flagged','reversed')"
python run.py embed --source kb_articles
python run.py seed-evaluation --output tests/eval/ground_truth.jsonl
```

### 4.4 Implementation order (Day 1)

1. Faker generator for 15k transactions with realistic distributions (95% success, 3% failed, 1.5% flagged, 0.5% reversed; geo-mix; method-mix) — **1.5 h**
2. Inject ~30 card-testing scenarios (5 tx within 60 s under same user, all sub-$5) — **30 min**
3. Inject ~50 cross-border declines with realistic `failure_reason` strings — **30 min**
4. 30 hand-written KB chunks covering common payment issues — **30 min**
5. Chunker (single chunk per incident, format defined in architecture review §7.3) — **30 min**
6. Batched embedder (batch 64, COPY into table) — **45 min**
7. Ground-truth dataset: 50 query→relevant-tx-id pairs across the 4 demo scenarios — **1 h**

**Subtotal: ~5 hours** (Day 1).

### 4.5 Testing strategy

| Test type | What |
|---|---|
| Smoke | After generation, distributions match design (use pandas asserts) |
| Smoke | After embedding, table count == expected and pgvector index queryable |
| Idempotency | Running `embed` twice does not duplicate rows (upsert via `ON CONFLICT (source_type, source_id) DO NOTHING`) |

### 4.6 Demo role

- Loaded **before** demo, not during. The first 30 seconds of demo includes `make seed` shown in the recording or pre-baked into volumes — choice depends on time budget.
- Provides the synthetic transactions that **all four demo scenarios** query against.

### 4.7 Estimated hours: **5 h**

---

## 5. Frontend — React 18 + TypeScript + Vite (3 views)

Three pages, served via nginx in prod, no SSR, no state-management library beyond TanStack Query. **No over-engineering rule still applies inside React:** no Zustand/Redux, no component library install, no form library, no frontend test suite (rubric does not require it; API integration tests cover the same paths).

| View | Purpose | Demo scenario it serves |
|---|---|---|
| `UserConsole.tsx` | View transactions, open dispute | UX context only |
| `MerchantDashboard.tsx` | Settlement view, failure-rate chart, "ask AI" sidebar | Scenarios 1, 4 |
| `SupportConsole.tsx` | Chat with assistant (SSE), agent trace drawer, judge score badge | Scenarios 1, 2, 3 |

### 5.1 Folder structure

```
frontend/
├── Dockerfile                          ← multi-stage: node build → nginx serve
├── nginx.conf                          ← SPA fallback + SSE-safe proxy
├── package.json
├── tsconfig.json
├── tsconfig.node.json
├── vite.config.ts                      ← path alias @/* and dev proxy
├── tailwind.config.js
├── postcss.config.js
├── index.html
├── .env.example                        ← VITE_CORE_API_URL, VITE_AI_API_URL
└── src/
    ├── main.tsx                        ← ReactDOM + Router + QueryClient
    ├── App.tsx                         ← shell layout + nav
    ├── routes.tsx
    ├── api/
    │   ├── client.ts                   ← fetch wrapper, JWT injection, RFC-7807 parsing
    │   ├── core.ts                     ← typed calls to core-api
    │   ├── ai.ts                       ← assistant SSE, agents invoke
    │   └── types.ts                    ← TS mirrors of Pydantic models
    ├── pages/
    │   ├── UserConsole.tsx
    │   ├── MerchantDashboard.tsx
    │   └── SupportConsole.tsx
    ├── components/
    │   ├── ChatStream.tsx              ← SSE token rendering
    │   ├── AgentTracePanel.tsx         ← collapsible right drawer
    │   ├── TransactionTable.tsx
    │   ├── FailureRateChart.tsx        ← Recharts line chart
    │   ├── DegradedModeBanner.tsx      ← sticky banner when fallback model answered
    │   ├── JudgeScoreBadge.tsx         ← 1–5 colour badge
    │   ├── CitationList.tsx
    │   └── ui/                         ← Button, Card, Badge, Skeleton (plain Tailwind)
    ├── hooks/
    │   ├── useSSE.ts                   ← EventSource wrapper with reconnection
    │   ├── useAuth.ts                  ← JWT in localStorage
    │   └── useTransactions.ts          ← TanStack Query
    ├── lib/
    │   ├── format.ts                   ← currency, date, duration helpers
    │   └── cn.ts                       ← tailwind class merger
    └── styles/
        └── globals.css                 ← tailwind directives + monochrome theme
```

### 5.2 Implementation tasks

| # | Task | Hours |
|---|---|---|
| 1 | Scaffolding — `npm create vite@latest`, deps (`react-router-dom`, `@tanstack/react-query`, `tailwindcss`, `recharts`, `clsx`), tsconfig path alias, env vars | 1.0 |
| 2 | `api/client.ts` + `api/core.ts` + `api/ai.ts` + `types.ts` — typed surface; SSE returns `AsyncIterable<Token>` | 1.5 |
| 3 | App shell — sidebar nav, routes, `useAuth`, login form, protected routes | 1.0 |
| 4 | `UserConsole.tsx` — TransactionTable, status filter, empty/loading states | 1.0 |
| 5 | `MerchantDashboard.tsx` — `FailureRateChart`, settlement card, fraud-cases table, "Ask AI" sidebar | 2.5 |
| 6 | `SupportConsole.tsx` — chat UI, SSE via `useSSE`, citations click-through, `JudgeScoreBadge` | 2.0 |
| 7 | `AgentTracePanel.tsx` — right drawer rendering `steps[]`: plan, tool calls, observations | 1.0 |
| 8 | `DegradedModeBanner.tsx` — activated by `X-LLM-Tier: fallback` SSE header | 0.5 |
| 9 | Dockerfile (multi-stage) + nginx.conf (**SSE-safe:** `proxy_buffering off`) | 1.0 |
| 10 | Theme pass — monochrome palette, skeletons, route-level error boundary, error toast | 1.0 |
| 11 | End-to-end smoke — click through all four demo scenarios | 0.5 |

**Hours: ~12 h** (Day 4 afternoon + Day 5 morning — see updated §6).

### 5.3 nginx must be SSE-safe

The single sharpest edge in this swap: nginx **buffers by default**, which silently destroys token-by-token streaming. The `/api/ai/` location block needs `proxy_buffering off`, `proxy_cache off`, `chunked_transfer_encoding off`, and `proxy_read_timeout 120s` (agent runs are long). Test SSE end-to-end on Day 5 morning before the dry run.

---

## 6. Day-by-day plan (the actual schedule)

Total budget: **~51 hours** across 5 working days. The React frontend adds ~9 h vs. the original Streamlit plan; Day 4 and Day 5 are re-allocated to absorb it.

### Day 1 (Monday) — Foundations, 8 h
- 0.5 h — Repo scaffolding, `pyproject.toml` per service, pre-commit (ruff, black, mypy)
- 1.5 h — `docker-compose.yml`: postgres (with pgvector + tsearch), redis, three Python services as build stubs returning `{ok:true}`
- 1 h — `infra/postgres/init.sql` (schemas + tables + indexes from §2.2 and §3.2)
- 0.5 h — Shared logging, db pool, redis client modules
- 5 h — Ingestion-worker complete (per §4.4), seed runs end-to-end, embeddings populated
- **Checkpoint:** `make up && make seed && psql -c 'select count(*) from ai.embeddings'` returns ~3000

### Day 2 (Tuesday) — core-api, 8 h
- 8 h — `core-api` per §2.4 steps 1–8 (identity, wallet, payment, idempotency, ledger, fraud baseline)
- **Checkpoint:** Can create a payment via curl with Idempotency-Key, see ledger entries, fraud_score assigned, retry returns cached response

### Day 3 (Wednesday) — core-api finish + ai-service start, 9 h
- 3 h — core-api remaining (`dispute`, `settlement`, `merchant`, health endpoints, structured logging audit)
- 1 h — Unit tests for `idempotency` and `ledger.posting_invariant`
- 5 h — ai-service §3.5 steps 1–6 (LLM client, fallback, retrieval, RAG endpoint, LangGraph skeleton)
- **Checkpoint:** `POST /v1/assistant/query` returns a streamed answer with citations

### Day 4 (Thursday) — Agents, eval, frontend kickoff, 12 h
- 4 h — ai-service §3.5 steps 7–10 (sub-agent nodes, tools, judge, memory)
- 1.5 h — DeepEval suite + ground-truth evaluation; record metrics to `docs/evaluation/RESULTS.md`
- 0.5 h — Locust load test script + 2-minute run; record P50/P99 in RESULTS.md
- 6 h — **Frontend part 1**: scaffolding + API layer + `UserConsole` + `MerchantDashboard` (tasks 1–5 from §5.2)
- **Checkpoint:** All four demo scenarios runnable end-to-end via API; merchant dashboard renders failure-rate chart and streams AI replies

### Day 5 (Friday) — Frontend finish, polish, demo prep, 10 h
- 6 h — **Frontend part 2**: `SupportConsole` + `AgentTracePanel` + `DegradedModeBanner` + Dockerfile/nginx + theme pass + smoke (tasks 6–11 from §5.2)
- 1 h — Integration test pass (pytest -m integration), fix flakes
- 0.5 h — README.md walkthrough with exact commands + screenshots
- 1 h — PPT touch-up (10 slides — see §8)
- 1 h — Demo dry-run end-to-end (record video as backup)
- 0.5 h — Buffer

**Total: ~51 h. Buffer collapses to ~0.5 h on Day 5 — see Risk register.**

---

## 7. Demo script (8 minutes)

Open `http://localhost:3000` in the browser — login as analyst → support console. Agent trace drawer toggleable on the right.

| Minute | Action | What the panel sees |
|---|---|---|
| 0:00–0:30 | One-line intro + show `docker-compose ps` (10 containers up) | "One command, everything up" |
| 0:30–2:30 | **Scenario 1** — type *"15% of card payments are failing this afternoon for merchant M_042 — what's the root cause?"* Show: hybrid retrieval (citations), agent steps, judge score 5/5, ranked hypotheses | Hybrid search + multi-agent + judge |
| 2:30–3:45 | **Scenario 3** — flip to merchant dashboard, click "Show recent flagged" → 8 card-testing transactions surface. Click "Investigate" → fraud_investigator agent produces report and opens a case | Async fraud + tool use |
| 3:45–5:00 | **Scenario 4** — merchant dashboard → "Explain my settlement variance for July 23" → settlement_reconciler agent walks through expected vs actual with line items | Settlement agent |
| 5:00–6:00 | **Scenario 2** — back to support console, single decline question → pure RAG path, fast streamed response | RAG + recency boost |
| 6:00–6:45 | **Resilience demo** — `docker exec` to unset OPENAI_API_KEY → re-run same query → answer comes from Flan-T5 with "degraded mode" banner; judge runs locally | Local fallback live |
| 6:45–7:30 | **Evaluation** — open `RESULTS.md` in browser: Recall@5, P50/P99 latency, DeepEval scores, load test summary | Evaluation rigour |
| 7:30–8:00 | **Wrap** — open ARCHITECTURE_REVIEW.md briefly, point to 8 ADRs in DECISIONS.md, hand back to panel | Production-mindedness |

**Q&A (2 minutes)** — likely questions and prepared answers:
- *"How does this scale to 100k tx/sec?"* → Point to architecture review §3, Azure deployment diagram, K8s + Event Hubs + Cosmos for PG.
- *"What happens when OpenAI is rate-limited?"* → Already demoed (Flan-T5 fallback).
- *"How do you measure RAG quality?"* → DeepEval suite, ground-truth file in `tests/eval/`, results in RESULTS.md.
- *"Why pgvector and not Milvus?"* → ADR-001 (FAISS/pgvector for POC, Milvus for production; clean swap behind `VectorStore` interface).

---

## 8. Stakeholder PPT (10 slides, ~1.5 h to build)

1. **Problem & scope** (2 sentences) + dataset stats (15k tx, distributions)
2. **Architecture overview** — embed the high-level diagram
3. **POC vs Production** — the swap-table from architecture review §4.4
4. **Microservice breakdown** — the table from §5
5. **Data flow** — payment + RAG diagrams side-by-side
6. **Agentic AI design** — LangGraph topology + LLM-as-judge
7. **Trade-offs** — three picks from §11 of architecture review (LLM tiering, hybrid+rerank, local fallback)
8. **Evaluation results** — Recall@5, MRR, faithfulness, latency P50/P99, load test summary
9. **What I cut and why** — the table from §0 above
10. **What ships next** — top 3 items: real Stripe, Kafka, K8s

---

## 9. Risk register

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| OpenAI quota / rate limit during demo | Medium | High | Flan-T5 fallback already in scope; record video as backup |
| Local Flan-T5 too slow on demo laptop | Medium | Medium | Pre-warm on container start; show on a smaller prompt |
| PGVector HNSW build slow on 15k rows | Low | Low | `ef_construction=64` is fast; tested on Day 1 |
| LangGraph version churn | Medium | Medium | Pin to a specific version on Day 1, no upgrades |
| Time overrun on agents (Day 4) | High | High | **Cut order if behind:** dispute_resolver → settlement_reconciler agent stays minimal → fraud_investigator + support_qa are non-negotiable |
| Docker on demo laptop misbehaving | Low | High | Record full demo video on Day 5 as fallback |
| React frontend time-overrun (Day 4–5) | High | High | **Cut order if behind:** drop `UserConsole` to a stub; `MerchantDashboard` and `SupportConsole` are the two that demo |
| nginx buffering breaks SSE streaming | Medium | High | Explicit `proxy_buffering off` on `/api/ai/` location; test SSE end-to-end on Day 5 morning before dry run |

---

## 10. Definition of done (assessment self-check)

Mapped to the rubric in your assignment doc.

| Rubric item | Where it lives |
|---|---|
| Clear folder structure (`/requirements`, `/docs/architecture`, `/docs/data-flow`, `/src`, `/tests`, README.md) | §1 — exact structure |
| Stakeholder PPT | §8 |
| Architecture vs Data Flow distinction | Architecture review §3 + DIAGRAMS.md |
| API Gateway, LB, K8s in production diagram | Architecture review §3, DIAGRAMS.md #1 and #6 |
| POC vs Production clearly distinguished | Architecture review §4 + §0 above |
| Observability + MLOps in architecture | Architecture review §3.1 L7 + §3.5 step 11 |
| ADRs with pros/cons | DECISIONS.md (8 ADRs from architecture review §10) |
| Trade-offs explicit | Architecture review §11 |
| Decoupling (swap Vector DB without rewrite) | `VectorStore` interface in `app/retrieval/` |
| Zero print() — structured logs | `shared/logging_config.py` with structlog |
| No hardcoded secrets | `.env.example`, all settings via Pydantic BaseSettings |
| Microservices in code boundaries | 3 services + strict package boundaries within `core-api` |
| Connection pooling | asyncpg pool + redis.asyncio pool, both lifespan-managed |
| Pydantic input validation | Every router endpoint |
| Working docker-compose | §1 |
| Cold start optimisation | Models + GBT loaded in FastAPI lifespan, not per request |
| Error handling | RFC 7807, retries with tenacity, circuit breaker in LLM router |
| API testing | Integration tests in `tests/integration/` |
| Performance measurement | Locust + RESULTS.md |
| Accuracy validation methodology | DeepEval suite + ground-truth file |
| Ground-truth dataset documented | `tests/eval/ground_truth.jsonl` + RESULTS.md |
| Metrics summary | RESULTS.md |
| Local fallback (Flan-T5) | `services/ai-service/app/llm/fallback.py` |
| Graceful degradation | Hybrid retrieval falls back to keyword-only if pgvector index is missing |

---

## Final note from the Staff Engineer

The single biggest risk to this plan is **scope creep on Day 4 agents**. The fraud_investigator and the support_qa agent are the *only* two that must work flawlessly in the demo. Settlement and dispute agents can be thinner. Card the work in that order. If you find yourself 2 hours into a tool refactor on Day 4, stop, commit what works, and move on.

The second biggest risk is **starting the PPT too late**. Block 90 minutes for it on Day 5 morning, not afternoon — by afternoon you should be doing dry runs, not designing slides.

Ship it.
