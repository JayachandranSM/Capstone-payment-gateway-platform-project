# Production Readiness Assessment
## AI-Powered Payment Gateway Platform

**Author:** Jayachandran
**Reviewer:** Siva
**Date:** June 2026
**Classification:** Capstone Submission — Evaluation Document
**Companion documents:** `EVALUATION_RESULTS.md` · `DEMO_SCRIPT.md` · `FINAL_CHECKLIST.md`

---

## Executive Summary

This document provides an honest, structured assessment of what has been built against what a production payment system requires. The capstone implements a fully working end-to-end system — five containers, real APIs, 10,045 synthetic transactions, live AI fraud scoring, and vector-search RAG — but it is an MVP demonstrating production *architecture*, not a production-*deployed* system. The distinction between the two is the central claim of this document.

Every gap below is **known, documented, and defensible**. The architectural skeleton is designed so that production hardening can be applied layer by layer without restructuring the codebase. The production hardening roadmap that follows describes the exact steps required to close each gap, with estimated effort appropriate for a two-engineer team.

### Summary verdict by category

| Category | MVP Status | Production Gap | Priority |
|---|---|---|---|
| **Authentication / RBAC** | ❌ None | All endpoints open | P0 — must fix before any real traffic |
| **Payment logic** | ✅ Complete | None for MVP scope | — |
| **AI fraud scoring** | ✅ Complete | Replace rules with ML model | P2 |
| **RAG knowledge base** | ✅ Complete | Expand knowledge base | P3 |
| **Observability** | ⚠️ Logging only | No metrics collection, no tracing | P1 |
| **Data persistence** | ⚠️ Single node | No replication, no backups | P1 |
| **Security** | ⚠️ Partial | No TLS between services, no WAF | P0 |
| **Deployment** | ⚠️ Local only | Single host, no HA | P1 |
| **CI/CD** | ❌ None | No automated pipeline | P2 |
| **Compliance** | ❌ Not assessed | PCI-DSS gap not closed | P1 |

---

## Part 1 — What Is Built (MVP Inventory)

### 1.1 Service Topology

| Container | Image | Technology Stack | Port | Health |
|---|---|---|---|---|
| `pg-postgres` | `pgvector/pgvector:pg16` | PostgreSQL 16 + pgvector extension | 5432 | ✅ |
| `pg-redis` | `redis:7-alpine` | Redis 7, AOF persistence enabled | 6379 | ✅ |
| `pg-core-api` | Built locally | FastAPI 0.115.5, SQLAlchemy 2.0.36 async, asyncpg 0.30 | 8000 | ✅ |
| `pg-ai-service` | Built locally | FastAPI 0.115.5, Azure OpenAI SDK 1.54.4 | 8100 | ✅ |
| `pg-frontend` | Built locally | React 18.3.1, TypeScript 5.6.3, Vite 5.4.11, nginx 1.27 | 3000 | ✅ |

All five containers start with `make up` and pass their respective health checks within 30 seconds. `depends_on` ordering in `podman-compose.yml` ensures Postgres and Redis are available before either API service begins accepting traffic.

**Network isolation:** All containers communicate on the `pg-net` bridge network. Only ports 3000, 8000, and 8100 are exposed to the host. Postgres (5432) and Redis (6379) are internal-only.

### 1.2 Database Architecture

Four schemas enforce domain separation at the PostgreSQL level:

```
postgres (pg16)
├── core          users · wallets · transactions
├── ledger        entries                          ← schema-isolated; no FK to core
├── ai            knowledge_chunks                 ← pgvector store
└── ops           health_probe · audit_log (schema only)
```

**Extensions:** `pgcrypto` (UUID generation via `gen_random_uuid()`), `vector` (pgvector, HNSW indexes for cosine similarity search).

**ORM layer:** SQLAlchemy 2.0 declarative, `Mapped[...]` typed columns throughout. Custom column types enforce financial data discipline:
- `Money` — wraps `NUMERIC(18,4)`, raises `TypeError` on `float` input
- `UTCDateTime` — wraps `TIMESTAMPTZ`, raises `ValueError` on naive datetime input

All ORM relationships use `lazy="raise_on_sql"` to surface N+1 query bugs at development time rather than in production.

**Alembic:** Migration scripts are designed (PAYMENT_DOMAIN_DESIGN.md §5, 8 migrations planned) but not yet wired to the startup sequence. Pending ADR-013. The models are autogenerate-ready.

### 1.3 Implemented API Surface

**core-api (port 8000):**

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/healthz` | None | Liveness probe — process alive |
| GET | `/readyz` | None | Readiness — Postgres + Redis check |
| GET | `/metrics` | None | Prometheus-format stub |
| POST | `/v1/payments` | None (MVP) | Create payment with idempotency |
| GET | `/v1/payments` | None (MVP) | List, filter, keyset-paginate |
| GET | `/v1/payments/{id}` | None (MVP) | Fetch single transaction |

**ai-service (port 8100):**

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/healthz` | None | Liveness |
| GET | `/readyz` | None | Readiness + Azure OpenAI check |
| POST | `/v1/fraud/score` | None (MVP) | Deterministic rules + optional LLM explanation |
| POST | `/v1/rag/query` | None (MVP) | Vector or keyword knowledge retrieval |

OpenAPI schemas are generated live from code and accessible at `/docs` on both services.

### 1.4 Payment Domain Implementation

**Application service layer (hexagonal architecture):**

```
app/
├── payment/
│   ├── api/           routes.py · schemas.py               ← HTTP boundary
│   ├── application/   service.py                           ← business rules, no I/O
│   ├── domain/        models.py · state_machine            ← pure domain
│   └── infrastructure/repository.py                        ← database adapter
├── wallet/            (same 4-layer structure)
└── ledger/            (same 4-layer structure)
```

**Transaction state machine** (enforced in `PaymentService._assert_transition`):

```
pending ──→ success ──→ reversed
        ──→ failed    (terminal)
        ──→ flagged ──→ success
                    ──→ failed
```

Illegal transitions raise `InvalidStateTransitionError` at the service layer. The database enforces only the valid *set* of status values via the `core.txn_status` PostgreSQL ENUM, not the valid *sequences* — the service is the source of truth for transition logic.

**Double-entry ledger invariant:**
Every non-failed transaction writes exactly two `ledger.entries` rows:
- `DEBIT` on the sender's `wallet_id`
- `CREDIT` on the merchant's deterministic suspense UUID

The invariant `SUM(DEBIT amount) == SUM(CREDIT amount) per transaction_id` is enforced by `LedgerService.post_payment` under SERIALIZABLE isolation. Post-seed verification confirmed 0 imbalanced transactions across 10,045 records.

**Idempotency — two-key Redis strategy:**

```
idem:lock:{merchant_id}:{key}   TTL=60s    in-flight protection (SET NX)
idem:resp:{merchant_id}:{key}   TTL=24h    response replay cache
```

DB `UNIQUE(merchant_id, idempotency_key)` on `core.transactions` is the durable safety net if Redis is evicted or restarts. The route handler performs a repository pre-check before calling `create_payment`, returning a cached 200 + `Idempotent-Replay: true` header immediately on a cache hit.

**Optimistic concurrency on wallet balance updates:**

```sql
UPDATE core.wallets
   SET balance = :new_balance, version = version + 1
 WHERE wallet_id = :id AND version = :expected_version
RETURNING version
```

Zero rows returned → `OptimisticLockError` → service retries up to 3 times. This avoids the row-level write lock that `SELECT FOR UPDATE` would impose under high-concurrency wallet operations.

### 1.5 AI Fraud Scoring

**Architecture:** Two-tier pipeline. Tier 1 is the hard correctness layer; Tier 2 is a best-effort enrichment.

```
Request ──→ [Tier 1] Rule engine (sync, ~1ms, never fails)
              ↓ raw_score + rule_hits
            [Tier 2] LLM explanation (async, 3s timeout, fallback to template)
              ↓
            FraudScoreResponse (risk_score 0-100, decision, reasons, rule_hits, explanation)
```

**15 deterministic rules** across 6 categories:

| Category | Rules | Max combined weight |
|---|---|---|
| Amount | `AMOUNT_LARGE_INR`, `AMOUNT_LARGE_USD_EUR`, `AMOUNT_ROUND_SUSPICIOUS` | 55 |
| Velocity | `VELOCITY_PRIOR_FAILURES`, `VELOCITY_HIGH_FREQ`, `VELOCITY_NEW_ACCOUNT` | 95 |
| Geo | `GEO_HIGH_RISK_COUNTRY`, `GEO_CROSS_BORDER` | 40 |
| Method | `METHOD_BANK_LARGE`, `METHOD_CARD_FOREIGN` | 35 |
| Merchant | `MERCHANT_HIGH_RISK_CAT`, `MERCHANT_NEW_NO_HISTORY` | 50 |
| Behaviour | `BEHAVIOUR_ODD_HOUR`, `BEHAVIOUR_NEW_DEVICE`, `BEHAVIOUR_METADATA_SPARSE` | 50 |

Score cap: `min(sum(weights), 100)`. Decision thresholds: `allow < 40 ≤ review < 75 ≤ reject`.

**LLM explanation path:** Azure OpenAI chat completion (`temperature=0.2`, `max_tokens=120`). Prompt instructs "exactly one factual sentence, no caveats." Three-second hard timeout. On timeout or any error: deterministic template fills in, `llm_used: false` in response.

### 1.6 RAG Knowledge Assistant

**Retrieval pipeline:**

```
Query string
  ↓
[Embedding] text-embedding-3-small (1536 dims, Azure OpenAI)   ← Tier 1
  ↓ on timeout/unavailable ↓
[Vector search] 1 - (embedding <=> query_vec)   HNSW pgvector
  ↓ fallback ↓
[Keyword search] Dice-coefficient on pre-computed keywords column
  ↓
RAGQueryResponse (chunks[], search_mode, embedding_used, scores)
```

**Knowledge base:** 5 policy documents, 48 chunks at ingest, all embedded with `text-embedding-3-small`. Index: `HNSW (m=16, ef_construction=64), vector_cosine_ops`. Idempotent seeder re-embeds only chunks where `content_hash` changed or `embedding IS NULL`.

**Bug fixed during development:** The initial `<#>` (negative inner product) operator was replaced with `<=>` (cosine distance) in both the similarity SELECT and the ORDER BY. The formula `1 - (embedding <=> query_vec)` correctly yields cosine similarity in [0, 1]. This bug was caught by a systematic test that checked score ranges — all results were being clamped to 1.0 by the score normaliser.

### 1.7 Frontend

React 18.3.1 + TypeScript 5.6.3 + Vite 5.4.11. **Zero new npm dependencies** beyond the base scaffold — avoids build-system complexity under a time constraint.

| Component | Real API call | Features |
|---|---|---|
| `SummaryCards` | No (derived from table data) | Count, volume, flagged, failed |
| `PaymentsTable` | `GET /v1/payments` | Risk-spine colour coding, keyset pagination, status filter |
| `DetailPanel` | `POST /v1/fraud/score` | Animated score meter, category-coloured rule hits, collapsible evidence, LLM explanation |
| `RAGPanel` | `POST /v1/rag/query` | Suggested queries, category filter, relevance score bars, search_mode indicator |

Build metrics: 164KB JS (52KB gzipped), 17KB CSS (4KB gzipped), 1.67 second build time.

---

## Part 2 — MVP vs Production Architecture

### 2.1 The Architectural Gap Map

This table maps every MVP design decision to its production counterpart. References indicate the ADR where the decision was recorded and its "revisit when" trigger.

| Dimension | MVP (built) | Production target | ADR | Revisit trigger |
|---|---|---|---|---|
| Orchestration | Podman Compose, single host | Kubernetes (AKS) with HPA + KEDA | ADR-005 | Team > 2 engineers or >10 RPS steady |
| Service count | 3 domain services in modular monolith | 14 domain microservices | ADR-008 | Domain team ownership boundaries emerge |
| Availability | Single node, zero HA | Active-active (Central India + South India) | ADR-005 | Any real transaction value |
| Secrets | `.env` file on disk | Azure Key Vault + CSI driver | — | Before first external user |
| TLS | HTTP on private bridge | mTLS via Istio service mesh | — | Any internet-exposed deploy |
| Auth | None | JWT RS256 + OPA RBAC | — | **Before any real traffic** |
| Postgres | Single instance | Synchronous replication, 2 standbys | ADR-002 | First paying merchant |
| Redis | AOF, single instance | Redis Cluster (3-node) + Sentinel | ADR-003 | RPM > 10k or multi-AZ |
| Ledger isolation | Separate schema, shared Postgres | Separate Postgres cluster | ADR-002 | Ledger writes > 5k/sec |
| Event streaming | Not emitted | Redis Streams → Kafka | ADR-005 | >10k events/sec |
| Fraud model | 15 deterministic rules | Rules + GBT/XGBoost + feature store | ADR-007 | Labelled fraud data available |
| Vectors | pgvector in core Postgres | Dedicated vector cluster or Milvus | ADR-009 | >1M embeddings or P95 > 50ms |

### 2.2 Authentication and Authorisation (Most Critical Gap)

**Current state:** All endpoints are unauthenticated. This is the most significant gap between the MVP and production.

**What is designed:**
- `core.merchants.api_key_hash` column exists (bcrypt placeholder)
- API contract documents scopes: `payment:write`, `payment:read`, `refund:write`, `wallet:read`, `admin:*`
- `auth.py` middleware stub exists in the codebase
- RFC 7807 error types include `forbidden` (403)

**Production path:**

```
Client ──→ JWT (RS256) from Identity Provider (Azure AD B2C)
         ──→ FastAPI middleware: verify signature, extract claims
         ──→ OPA sidecar: evaluate {user_id, scopes, resource_id} → allow/deny
         ──→ Route handler: Depends(require_scope("payment:write"))
```

Estimated: 3–4 engineer-days to implement and test with 80% coverage.

**Merchant authentication path:**
```
Merchant ──→ API key in Authorization header (Bearer m_key_xxx)
          ──→ Middleware: SHA-256 lookup → bcrypt verify against core.merchants.api_key_hash
          ──→ Inject merchant_id into request context
```

### 2.3 Security Architecture

**Threat model (abbreviated):**

| Threat | MVP mitigation | Production mitigation |
|---|---|---|
| Unauthenticated API access | Private bridge network | JWT + OPA; merchant API keys |
| Duplicate payment (double-charge) | Idempotency key + DB UNIQUE | Same, plus Redis Streams deduplication at consumer |
| SQL injection | SQLAlchemy parameterised queries | Same, plus WAF OWASP CRS 3.2 |
| Secret exfiltration | `.env` not committed (`.gitignore`) | Azure Key Vault; no secrets in images |
| Card data exposure | No card data handled | Vault Transit tokenisation for PAN/CVV |
| Audit trail tampering | `ops.audit_log` schema designed | `REVOKE UPDATE,DELETE,TRUNCATE` from app role; hash chain |
| DDoS | No protection | Azure Front Door + DDoS Standard |
| Insider threat | Not addressed | RBAC; privileged access workstations; audit log |

**PCI-DSS gap:** The system does not handle card data (PANs) — amounts and tokens only. A production deployment targeting PCI-DSS Level 1 would require:
1. Vault Transit secrets engine for PAN tokenisation
2. Scope reduction (no card data in application memory)
3. Annual penetration test
4. Quarterly ASV vulnerability scan
5. Network segmentation (CDE isolation)

This gap is acknowledged in DECISIONS.md and is a defined Phase 5 deliverable in the hardening roadmap.

### 2.4 Observability

**What is built:**
- Structured JSON logging via `structlog` on both services, with `service_name`, `trace_id`, and `timestamp` in every log line
- `X-Trace-Id` header propagated through nginx proxy to both backends
- `/metrics` endpoint on both services (stub, returns plain-text process metrics)

**What is missing:**
- No Prometheus scrape configuration
- No OpenTelemetry span export
- No Grafana dashboards
- No alerting rules

**Production observability stack:**

```
FastAPI ──→ OpenTelemetry SDK ──→ OTLP exporter ──→ Jaeger/Tempo
FastAPI ──→ prometheus_fastapi_instrumentator ──→ Prometheus ──→ Grafana
structlog ──→ stdout ──→ Fluentd/Vector ──→ Elasticsearch/Azure Monitor
```

Key metrics to instrument (RED method):
- `payment_requests_total{method, status_code}` — Rate
- `payment_duration_seconds{method, route}` — Duration (P50/P95/P99)
- `payment_errors_total{error_type}` — Errors
- `fraud_rule_fires_total{rule_id}` — Per-rule hit rate for drift detection
- `rag_retrieval_score_bucket` — RAG result quality histogram

### 2.5 Data Persistence

| Concern | MVP state | Production requirement |
|---|---|---|
| Postgres replication | None | Synchronous replication: 1 primary + 2 standbys |
| Backup | None | `pg_basebackup` hourly to Azure Blob + WAL archiving (RPO ≤ 5 min) |
| Redis persistence | AOF enabled | Redis Cluster (3 primary, 3 replica); Sentinel |
| Ledger durability | Same Postgres as core | Separate Postgres cluster; no shared transaction context |
| Retention | No policy | 7 years for audit_log; 30 days right-to-erasure for PII |

### 2.6 Rate Limiting

**Current state:** No rate limiting at any layer.

**Production design (already specified in PAYMENT_DOMAIN_DESIGN.md §6):**

```
Per-merchant:  1,000 RPM for payment creation
               100 RPM for refunds
               10,000 RPM for reads

Per-user:      60 RPM (fraud signal — unusually high is suspicious)
Per-IP:        300 RPM (DDoS proxy protection)
```

Implementation: Redis sliding window at the Kong API Gateway layer. The `rate-limited` error type is already defined in the RFC 7807 error catalogue.

### 2.7 Event Streaming

**Current state:** `ops.outbox` table schema is designed with correct columns (`event_id`, `aggregate_type`, `aggregate_id`, `event_type`, `payload`, `status`). No events are emitted.

**Production event flow:**

```
PaymentService.create_payment()
  ──→ writes ops.outbox row (same DB transaction as state change)
  ──→ Outbox poller (100ms interval): SELECT ... WHERE status='pending' FOR UPDATE SKIP LOCKED
  ──→ Redis Streams XADD payment.succeeded {payload}
  ──→ Consumer groups: settlement-service, notification-service, analytics-service
```

The transactional outbox pattern guarantees that events are never lost even if the poller crashes — the DB transaction either commits both the payment row and the outbox row, or neither.

### 2.8 CI/CD

**Current state:** No automated pipeline.

**Production pipeline:**

```
git push main
  ──→ GitHub Actions: lint (ruff) → type-check (mypy) → unit tests
  ──→ docker build + push to ACR (Azure Container Registry)
  ──→ Trivy container scan (fail on HIGH/CRITICAL CVEs)
  ──→ Alembic upgrade head (init container in Kubernetes pod spec)
  ──→ Integration tests (testcontainers-python)
  ──→ Canary deploy: 5% → 20% → 100% (automated rollback on error rate > 1%)
```

### 2.9 AI/ML Production Concerns

| Concern | MVP state | Production requirement |
|---|---|---|
| Fraud model type | 15 deterministic rules | Rules + XGBoost/LightGBM with SHAP explanations |
| Feature store | Runtime metadata only | Feast feature store: velocity windows (1h, 24h, 7d) |
| Model versioning | `model_version` field in response | MLflow experiment tracking; A/B shadow mode |
| Feedback loop | None | Analyst labels → retraining pipeline → champion/challenger |
| RAG evaluation | 10-query manual test | DeepEval: context recall, faithfulness, answer relevance |
| LLM cost tracking | None | Per-tenant token counting; tiered routing |
| LLM fallback | Template string | Flan-T5-large on-cluster (ADR-010) |
| Embedding drift | None | Monitor embedding quality over time; re-embed on drift |

---

## Part 3 — Production Hardening Roadmap

Sequenced by risk-adjusted business value. Phase 1 items are **gates** — no real transaction value should flow without them.

### Phase 1 — Security Foundation (Weeks 1–2, ~40 engineer-hours)

**Gate: Required before any external user or real transaction**

| Item | Effort | Owner | Notes |
|---|---|---|---|
| JWT middleware (RS256, scope validation) | 3d | Backend | `Depends(require_scope(...))` at every route |
| OPA sidecar for RBAC | 2d | Platform | Policy: `payment.write` requires verified KYC |
| Merchant API key provisioning | 1d | Backend | Endpoint + bcrypt storage; admin-only scope |
| Secrets migration: `.env` → Azure Key Vault | 1d | Platform | CSI driver; pod identity; no secrets in images |
| TLS termination (cert-manager + Let's Encrypt) | 1d | Platform | nginx ingress → cert-manager → ACME |
| Dependency vulnerability scan (Trivy in CI) | 0.5d | DevOps | Fail build on HIGH CVE |

### Phase 2 — Observability (Week 3, ~20 hours)

| Item | Effort | Owner |
|---|---|---|
| Prometheus exporter: RED metrics per route | 1d | Backend |
| OpenTelemetry SDK: spans for DB + Redis + OpenAI calls | 1d | Backend |
| Grafana dashboards: payment flow, fraud, RAG latency | 1d | Platform |
| PagerDuty alerting: P99 > 2s, error rate > 1% | 0.5d | Platform |
| Log centralisation (Fluentd → Azure Monitor) | 0.5d | Platform |

### Phase 3 — Reliability (Weeks 4–5, ~30 hours)

| Item | Effort | Owner |
|---|---|---|
| Transactional outbox writer + Redis Streams consumer | 2d | Backend |
| Postgres synchronous replication (1 primary, 2 standbys) | 1d | DBA |
| Automated backup: `pg_basebackup` + WAL to Azure Blob | 1d | DBA |
| Redis Cluster + Sentinel | 0.5d | Platform |
| Rate limiting middleware (Redis sliding window) | 1d | Backend |
| Circuit breakers on OpenAI calls (Tenacity) | 0.5d | Backend |

### Phase 4 — CI/CD (Weeks 5–6, ~20 hours)

| Item | Effort | Owner |
|---|---|---|
| GitHub Actions: lint → test → build → push → deploy | 1d | DevOps |
| Alembic `upgrade head` as Kubernetes init container | 0.5d | DevOps |
| Integration test suite in CI (testcontainers-python) | 2d | Backend |
| Canary deploy with automated rollback | 1d | Platform |

### Phase 5 — Compliance (Weeks 7–8, ~30 hours)

| Item | Effort | Owner |
|---|---|---|
| Audit log writer + RBAC on `ops.audit_log` | 1d | Backend |
| PCI-DSS gap analysis | 2d | Security |
| Vault Transit tokenisation for PAN | 3d | Backend + Security |
| GDPR data-subject export endpoint | 1d | Backend |
| Retention policy enforcement | 1d | DBA |

### Phase 6 — AI/ML Maturity (Weeks 9–12, ~40 hours)

| Item | Effort | Owner |
|---|---|---|
| XGBoost fraud model on synthetic corpus | 2d | ML |
| MLflow experiment tracking + model registry | 1d | ML |
| Feast feature store for velocity signals | 2d | ML |
| DeepEval RAG evaluation pipeline | 2d | ML |
| LLM cost tracking per-tenant | 1d | Backend |
| Flan-T5-large local fallback (KServe sidecar) | 1d | ML + Platform |

**Total estimated effort to production-ready:** ~180 engineer-hours (~4.5 engineer-weeks for a two-person team).

---

## Part 4 — Architecture Decision Records Summary

All decisions are recorded in `DECISIONS.md` (MADR-lite format with context, decision, consequences, and revisit triggers).

| ADR | Title | Status | Key tradeoff |
|---|---|---|---|
| ADR-001 | FastAPI as web framework | Accepted | Async-native; auto-schema; not Django |
| ADR-002 | PostgreSQL + pgvector (MVP) | Accepted | Avoids polyglot persistence at this scale |
| ADR-003 | Redis for cache/idempotency/session | Accepted | AOF persistence; single-node acceptable for MVP |
| ADR-004 | LangGraph for agent orchestration | Accepted | Graph-native; supports multi-agent handoffs |
| ADR-005 | Podman Compose for MVP deploy | Accepted | Zero infra overhead; Kubernetes on revisit trigger |
| ADR-006 | Streamlit frontend | **Superseded by ADR-011** | Replaced: insufficient for production UI requirements |
| ADR-007 | Azure OpenAI as primary LLM | Accepted | Graceful degradation when not configured |
| ADR-008 | Modular monolith for MVP | Accepted | Extraction-ready package boundaries |
| ADR-009 | Hybrid search (tsvector + pgvector) | Accepted | Dice-coefficient keyword fallback for LLM outage |
| ADR-010 | Flan-T5-large local fallback | Accepted | On-cluster inference when Azure OpenAI unavailable |
| ADR-011 | React + TypeScript + Vite | Accepted | Supersedes ADR-006; production-grade SPA |
| ADR-012 | Multi-model AI strategy | Accepted | GPT-4o / GPT-4o-mini / Flan-T5 tiered routing |

---

## Part 5 — What This Demonstrates for the Rubric

The capstone demonstrates production *architecture* rather than production *deployment*. The distinction matters:

**Architecture demonstrated:**
- Hexagonal (ports-and-adapters) package structure visible in the codebase
- Schema isolation between core, ledger, and AI domains
- Two-tier AI pipeline: deterministic scoring + LLM enrichment
- Graceful degradation at every AI integration point
- Event-driven design prepared (outbox schema, Redis Streams in ADRs)
- Multi-model AI strategy documented and partially implemented

**Not deployed in production, but designed for it:**
- 14-service decomposition documented in ARCHITECTURE_REVIEW.md
- Active-active Azure diagram in DIAGRAMS.md
- Alembic migration plan in PAYMENT_DOMAIN_DESIGN.md
- Kubernetes manifests: not present (not required for MVP scope per MVP_PLAN.md)

This is the appropriate trade-off for a capstone: demonstrate that you know *what* to build in production and *how* to build it, while keeping the scope achievable in the allotted time.

---

*This document is part of the capstone submission. The companion documents contain exact performance numbers (`EVALUATION_RESULTS.md`), the live demo script (`DEMO_SCRIPT.md`), and the complete submission checklist (`FINAL_CHECKLIST.md`).*
