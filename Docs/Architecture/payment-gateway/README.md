# AI-Powered Payment Gateway Platform

> **Capstone Project** — Jayachandran · Mentor: Siva · June 2026

A production-architecture payment gateway built end-to-end: double-entry ledger, real-time AI fraud scoring, vector-search policy assistant, and a live React operations dashboard — all running in five containers from a single command.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Features Implemented](#2-features-implemented)
3. [Requirements Coverage](#3-requirements-coverage)
4. [Repository Structure](#4-repository-structure)
5. [Prerequisites](#5-prerequisites)
6. [Environment Variable Setup](#6-environment-variable-setup)
7. [One-Command Startup](#7-one-command-startup)
8. [Service URLs](#8-service-urls)
9. [Sample API Calls](#9-sample-api-calls)
10. [Demo Flow](#10-demo-flow)
11. [Evaluation Summary](#11-evaluation-summary)
12. [MVP vs Production Scope](#12-mvp-vs-production-scope)
13. [Known Gaps and Roadmap](#13-known-gaps-and-roadmap)
14. [Troubleshooting](#14-troubleshooting)
15. [Submission Checklist](#15-submission-checklist)

---

## 1. Project Overview

This capstone demonstrates how a production payment system is *architected*, not just coded. The system implements a complete payment processing stack — wallet management, double-entry ledger, fraud detection, and policy retrieval — with AI as a first-class concern rather than an afterthought.

### What runs

| Container | Image / Build | Technology | Port |
|---|---|---|---|
| `pg-postgres` | `pgvector/pgvector:pg16` | PostgreSQL 16 + pgvector extension | 5432 |
| `pg-redis` | `redis:7-alpine` | Redis 7, AOF persistence | 6379 |
| `pg-core-api` | Built locally | FastAPI 0.115 · SQLAlchemy 2.0 async · asyncpg 0.30 | 8000 |
| `pg-ai-service` | Built locally | FastAPI 0.115 · Azure OpenAI SDK 1.54 | 8100 |
| `pg-frontend` | Built locally | React 18 · TypeScript 5.6 · Vite 5.4 · nginx 1.27 | 3000 |

### What makes this different from a tutorial

- **Hexagonal architecture** — every domain package has `api/`, `application/`, `domain/`, and `infrastructure/` layers, independently testable and extractable to separate services
- **Financial-grade data discipline** — `Money` type raises `TypeError` on `float`, `UTCDateTime` rejects naive datetimes, `NUMERIC(18,4)` in Postgres, amounts as decimal strings in JSON — never floats
- **Two-tier AI** — deterministic fraud rules (~1 ms, always works) plus optional LLM explanation (Azure OpenAI, 3-second hard timeout, graceful template fallback)
- **Transparent degradation** — `llm_used: bool` in every fraud response, `search_mode: vector|keyword` in every RAG response; callers always know which path ran
- **12 Architecture Decision Records** in `DECISIONS.md` explaining *why* each choice was made, with explicit "revisit when" triggers

---

## 2. Features Implemented

### Payment Domain

| Feature | Detail |
|---|---|
| User management | Registration, KYC status (verified / pending / failed), CITEXT email |
| Multi-currency wallets | One wallet per (user, currency); INR / USD / EUR; `CHECK(balance >= 0)` at DB level |
| Payment creation | 7-step atomic flow — validate → idempotency check → wallet lookup → insert pending → debit → post ledger → update status |
| Transaction lifecycle | 5-state machine `pending → success / failed / flagged → reversed`; illegal transitions raise `InvalidStateTransitionError` at service layer |
| Double-entry ledger | Every non-failed transaction creates exactly 2 `ledger.entries` rows (DEBIT + CREDIT); invariant SQL-verified across all 10,045 records |
| Partial and full refunds | Child transaction with `parent_transaction` FK; over-refund check enforced in service |
| Optimistic concurrency | Wallet balance updates: `UPDATE … WHERE version = :expected RETURNING version`; no row-level locks |
| Two-key idempotency | Redis `idem:lock:*` (60s NX) + `idem:resp:*` (24h replay cache) + DB `UNIQUE(merchant_id, idempotency_key)` as durable fallback |
| Keyset pagination | `(created_at, transaction_id)` cursor; base64url-encoded opaque token; O(1) regardless of page depth |
| RFC 7807 errors | All errors return `application/problem+json` with `type`, `title`, `status`, `detail` |

### AI Fraud Scoring — `POST /v1/fraud/score`

| Feature | Detail |
|---|---|
| 15 deterministic rules | 6 categories: Amount, Velocity, Geo, Method, Merchant, Behaviour |
| Weighted scoring | `score = min(Σ weights, 100)`; thresholds: allow < 40, review 40–74, reject ≥ 75 |
| Rule evidence | Every `RuleHit` includes `evidence: dict` — the raw values that triggered it |
| LLM explanation | Azure OpenAI `gpt-4o-mini`, `temperature=0.2`, one-sentence factual summary, 3-second hard timeout |
| Template fallback | Deterministic explanation when LLM unavailable; `llm_used: false` in response |
| Decision transparency | `risk_score`, `decision`, `reasons[]`, `rule_hits[]` all returned — no black-box output |

### RAG Policy Assistant — `POST /v1/rag/query`

| Feature | Detail |
|---|---|
| Knowledge base | 5 policy documents (refund, chargeback, fraud, settlement, payment failure), 48 chunks |
| Vector search | `text-embedding-3-small` (1,536 dims) → pgvector HNSW (`m=16`, `ef_construction=64`), cosine similarity `1 - (A <=> B) ∈ [0,1]` |
| Keyword fallback | Dice-coefficient on pre-computed `keywords` column; < 5ms; activates automatically when embeddings unavailable |
| Transparent mode | `search_mode: "vector" | "keyword"` and `embedding_used: bool` in every response |
| Idempotent seeder | `ON CONFLICT … DO UPDATE WHERE content_hash changed OR embedding IS NULL` — zero writes on re-run without changes |

### React Dashboard

| Feature | Detail |
|---|---|
| Transaction Monitor | Live table, 30-second auto-refresh, risk-spine left border (cyan/amber/red), status filter, keyset pagination |
| Summary cards | Count, volume, flagged, failed — derived from fetched data, no extra API call |
| Fraud scoring panel | "Score now" calls live API; loading shimmer skeleton; animated 0–100 meter with LOW/MEDIUM/HIGH RISK band; category-coloured rule hit cards with collapsible evidence dict |
| Policy assistant | Suggested query chips, category filter, relevance score bars, `search_mode` indicator |
| TypeScript strict | `tsc --noEmit: 0 errors`; typed API client; zero new npm dependencies beyond React + React DOM |

### Infrastructure

| Feature | Detail |
|---|---|
| Structured JSON logging | structlog throughout; `service_name`, `trace_id`, `ts` in every line |
| Health probes | `/healthz` (liveness, no deps), `/readyz` (Postgres + Redis; ai-service also checks Azure OpenAI) |
| SSE-safe nginx | `proxy_buffering off`, `proxy_cache off`, `Connection ''` on the `/api/ai/` path |
| Shared config | `shared/config.py` (Pydantic Settings) and `shared/logging_config.py` (structlog) copied into each image at build time |

---

## 3. Requirements Coverage

| Requirement | Implementation | Status |
|---|---|---|
| Microservices / modular architecture | Hexagonal packages; 12 ADRs in `DECISIONS.md` | ✅ |
| Payment CRUD + state machine | `POST/GET /v1/payments`; 5-state enforced in service layer | ✅ |
| Double-entry ledger | `ledger.entries` schema; balanced invariant SQL-verified | ✅ |
| Idempotency | Two-key Redis + DB UNIQUE constraint | ✅ |
| Decimal precision | `NUMERIC(18,4)`, `Money` type, decimal strings in JSON | ✅ |
| AI fraud scoring | 15 rules, 0–100 score, allow / review / reject | ✅ |
| LLM integration | Azure OpenAI gpt-4o-mini with graceful degradation | ✅ |
| RAG / vector search | pgvector HNSW, text-embedding-3-small, keyword fallback | ✅ |
| Operations dashboard | React 18 + TypeScript, live APIs, risk visualisation | ✅ |
| 10,000+ synthetic transactions | 10,045 transactions, 501 users, 1,501 wallets | ✅ |
| Reproducible deployment | `make up` — one command, five containers | ✅ |
| Observability | Structured JSON logs, `/healthz`, `/readyz`, `/metrics` stub | ✅ |
| Architecture Decision Records | 12 ADRs | ✅ |
| Authentication / RBAC | Deferred — Phase 1 of hardening roadmap | ⚠️ |
| Alembic migrations | 8 migrations designed; not wired to container startup | ⚠️ |
| Transactional outbox | `ops.outbox` schema designed; writer not yet implemented | ⚠️ |

---

## 4. Repository Structure

```
payment-gateway/
├── .env.example                       # All env vars with working defaults
├── Makefile                           # Operator shortcuts
├── podman-compose.yml                 # Five-service stack
├── PPT_OUTLINE.md                     # Capstone presentation outline (17 slides)
│
├── Docs/
│   ├── Evaluation/
│   │   ├── DEMO_SCRIPT.md             # 5-minute live demo script + recovery playbook
│   │   ├── EVALUATION_RESULTS.md      # Measured results from running system
│   │   ├── FINAL_CHECKLIST.md         # Submission checklist + rubric mapping
│   │   ├── LLM_AS_JUDGE_EVALUATION.md # Judge methodology (fraud + RAG quality)
│   │   ├── LOCUST_PERFORMANCE_TEST.md # 6-scenario load test specification + code
│   │   └── PRODUCTION_READINESS.md    # Gap analysis + 6-phase hardening roadmap
│   └── Data-Flow/
│       ├── DATA_FLOW_DIAGRAM.md       # End-to-end architecture data-flow
│       ├── FRAUD_SCORING_FLOW.md      # Fraud scoring pipeline
│       ├── PAYMENT_FLOW.md            # Payment processing step-by-step
│       └── RAG_RETRIEVAL_FLOW.md      # RAG retrieval pipeline
│
├── docs/knowledge/                    # Policy documents ingested by RAG seeder
│   ├── chargeback_policy.md
│   ├── fraud_policy.md
│   ├── payment_failure_policy.md
│   ├── refund_policy.md
│   └── settlement_policy.md
│
├── frontend/                          # React 18 + TypeScript SPA
│   ├── Containerfile                  # Node build → nginx serve (multi-stage)
│   ├── nginx.conf                     # SPA fallback + SSE-safe API proxy
│   ├── src/
│   │   ├── App.tsx                    # Layout shell + sidebar nav
│   │   ├── api/client.ts              # Typed fetch client for both backends
│   │   ├── components/                # DetailPanel · PaymentsTable · RAGPanel · …
│   │   ├── hooks/                     # useAsync · usePayments
│   │   ├── types/index.ts             # Canonical TypeScript types (mirrors Pydantic schemas)
│   │   └── styles.css                 # Design tokens + all component styles
│   └── package.json
│
├── infra/
│   └── postgres/init.sql              # pgcrypto + vector extensions; core / ai / ops schemas
│
├── scripts/
│   ├── seed_demo_data.py              # Seeds 10,045 transactions (idempotent, reproducible)
│   └── seed_knowledge_base.py         # Embeds 48 policy chunks into pgvector
│
├── services/
│   ├── core-api/                      # Payment domain service
│   │   ├── Containerfile
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── app/
│   │       ├── db/                    # Base · Money/UTCDateTime types · enums · session
│   │       ├── identity/              # api / application / domain / infrastructure
│   │       ├── wallet/
│   │       ├── payment/
│   │       └── ledger/
│   │
│   └── ai-service/                    # AI fraud + RAG service
│       ├── Containerfile
│       ├── main.py
│       ├── requirements.txt
│       └── app/
│           ├── fraud/                 # Rules engine + LLM explanation
│           ├── rag/                   # Vector + keyword retrieval
│           └── llm/                   # Azure OpenAI client wrapper
│
└── shared/                            # Python modules copied into both service images
    ├── config.py                      # BaseAppSettings (Pydantic Settings)
    └── logging_config.py              # structlog JSON setup
```

---

## 5. Prerequisites

| Tool | Minimum version | Check |
|---|---|---|
| **Podman** | 4.0 | `podman --version` |
| **podman-compose** | 1.2 | `podman-compose --version` |
| **make** | any | `make --version` |
| Free ports | — | 3000, 5432, 6379, 8000, 8100 |

**Docker users:** `make up COMPOSE="docker compose"` or add `COMPOSE=docker compose` to your shell environment.

**SELinux hosts (Fedora, RHEL):** the `init.sql` bind-mount uses `:z` in `podman-compose.yml` for automatic relabelling. This is a harmless no-op on Docker or non-SELinux hosts.

Install `podman-compose` if missing:

```bash
pip install podman-compose        # or: pipx install podman-compose
```

---

## 6. Environment Variable Setup

```bash
cp .env.example .env
```

**The system runs with zero edits** — all defaults work for local development. Azure OpenAI credentials are the only optional addition.

```ini
# .env — only these four lines need editing for full AI functionality
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=your-key-here
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_EMBED_DEPLOYMENT=text-embedding-3-small
```

**Without credentials:** fraud scoring still works (deterministic rules + template explanation, `llm_used: false`). RAG still works (keyword fallback, `search_mode: "keyword"`). Both services return HTTP 200 on `/healthz`; ai-service `/readyz` reports `"azure_openai": "not_configured"`.

### Full variable reference

| Variable | Default | Used by |
|---|---|---|
| `ENVIRONMENT` | `dev` | All services (logging) |
| `LOG_LEVEL` | `INFO` | All services |
| `POSTGRES_USER` | `postgres` | postgres container |
| `POSTGRES_PASSWORD` | `postgres` | postgres container |
| `POSTGRES_DB` | `paymentgateway` | postgres container |
| `DATABASE_URL` | `postgresql://postgres:postgres@postgres:5432/paymentgateway` | core-api, ai-service |
| `REDIS_URL` | `redis://redis:6379/0` | core-api, ai-service |
| `AZURE_OPENAI_ENDPOINT` | *(blank)* | ai-service |
| `AZURE_OPENAI_API_KEY` | *(blank)* | ai-service |
| `AZURE_OPENAI_API_VERSION` | `2024-08-01-preview` | ai-service |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | `gpt-4o-mini` | ai-service |
| `AZURE_OPENAI_EMBED_DEPLOYMENT` | `text-embedding-3-small` | ai-service |
| `CORE_API_PORT` | `8000` | host port mapping |
| `AI_SERVICE_PORT` | `8100` | host port mapping |
| `FRONTEND_PORT` | `3000` | host port mapping |

---

## 7. One-Command Startup

```bash
# 1. Copy environment template (edit for Azure OpenAI if desired)
cp .env.example .env

# 2. Build images + start all five containers
make up

# 3. Wait ~30 seconds, then verify all services are healthy
make health
```

Expected `make health` output when ready:

```
core-api:   {"status":"ok","service":"core-api","environment":"dev"}
ai-service: {"status":"ok","service":"ai-service","environment":"dev"}
frontend:     HTTP 200
```

**Seed the database** (first run only — both scripts are idempotent):

```bash
# 10,045 synthetic payment transactions
podman exec -it pg-core-api python scripts/seed_demo_data.py

# 48 RAG knowledge chunks (embeds if Azure OpenAI is configured)
podman exec -it pg-ai-service python scripts/seed_knowledge_base.py
```

### All Makefile targets

```
make up           Build + start the whole stack (background)
make down         Stop containers, preserve data volumes
make clean        Stop + delete named volumes (fresh DB on next up)
make build        Rebuild images without starting
make logs         Tail all container logs
make ps           Show container status
make health       Hit /healthz on all three exposed services
make restart      Restart all services
make shell-core   bash inside pg-core-api
make shell-ai     bash inside pg-ai-service
```

---

## 8. Service URLs

| URL | Description |
|---|---|
| `http://localhost:3000` | React dashboard — Transaction Monitor + Policy Assistant |
| `http://localhost:8000/docs` | Core API — interactive Swagger UI |
| `http://localhost:8000/redoc` | Core API — ReDoc documentation |
| `http://localhost:8000/healthz` | Core API liveness (no deps) |
| `http://localhost:8000/readyz` | Core API readiness (Postgres + Redis) |
| `http://localhost:8100/docs` | AI Service — interactive Swagger UI |
| `http://localhost:8100/healthz` | AI Service liveness |
| `http://localhost:8100/readyz` | AI Service readiness (Azure OpenAI check) |

**Frontend proxy routes (nginx):**

| Frontend path | Proxied to | Notes |
|---|---|---|
| `/api/core/*` | `core-api:8000/` | Standard proxy, 30s timeout |
| `/api/ai/*` | `ai-service:8100/` | SSE-safe: `proxy_buffering off` |

---

## 9. Sample API Calls

### List recent payments

```bash
curl -s "http://localhost:8000/v1/payments?limit=5" | python3 -m json.tool
```

### Filter by status

```bash
curl -s "http://localhost:8000/v1/payments?status=flagged&limit=10" | python3 -m json.tool
```

### Fetch a single transaction

```bash
# Replace with a real UUID from the list above
curl -s "http://localhost:8000/v1/payments/YOUR-UUID-HERE" | python3 -m json.tool
```

### Create a payment

```bash
curl -s -X POST http://localhost:8000/v1/payments \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(python3 -c 'import uuid; print(uuid.uuid4())')" \
  -d '{
    "user_id":        "00000000-0000-0000-0000-000000000001",
    "merchant_id":    "m_swiggy",
    "amount":         "250.00",
    "currency":       "INR",
    "payment_method": "upi",
    "metadata":       {"order_id": "ORD-001", "platform": "mobile"}
  }' | python3 -m json.tool
```

### Score a transaction — low risk (allow)

```bash
curl -s -X POST http://localhost:8100/v1/fraud/score \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_id": "00000000-0000-0000-0000-000000000001",
    "user_id":        "00000000-0000-0000-0000-000000000002",
    "merchant_id":    "m_swiggy",
    "amount":         "250.00",
    "currency":       "INR",
    "payment_method": "upi",
    "metadata": {"device_id": "d001", "ip_address": "10.0.0.1", "country": "IN"}
  }' | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'score={d[\"risk_score\"]}  decision={d[\"decision\"]}  llm_used={d[\"llm_used\"]}')
print(f'explanation: {d[\"explanation\"]}')
"
```

### Score a transaction — high risk (reject)

```bash
curl -s -X POST http://localhost:8100/v1/fraud/score \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_id": "00000000-0000-0000-0000-000000000099",
    "user_id":        "00000000-0000-0000-0000-000000000098",
    "merchant_id":    "m_gambling_xyz",
    "amount":         "500000.00",
    "currency":       "INR",
    "payment_method": "bank_transfer",
    "metadata": {
      "country_receiver": "KP",
      "is_new_device":    true,
      "prior_failures":   5,
      "hour_of_day":      3,
      "account_age_days": 2
    }
  }' | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'score={d[\"risk_score\"]}  decision={d[\"decision\"].upper()}  rules={len(d[\"rule_hits\"])}')
for h in d['rule_hits']:
    print(f'  +{h[\"weight\"]:2d}  [{h[\"category\"]:10s}]  {h[\"rule_id\"]}')
"
# Expected: score=100  decision=REJECT  rules=6
```

### Query the RAG policy assistant

```bash
curl -s -X POST http://localhost:8100/v1/rag/query \
  -H "Content-Type: application/json" \
  -d '{"query": "How long does a UPI refund take?", "top_k": 3}' | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'mode={d[\"search_mode\"]}  embedding_used={d[\"embedding_used\"]}')
print(f'chunks returned: {len(d[\"chunks\"])} of {d[\"total_chunks_searched\"]} searched')
for c in d['chunks']:
    print(f'  [{c[\"score\"]:.2f}]  {c[\"source_document\"]}  —  {c[\"section_title\"]}')
"
```

### Readiness check

```bash
# core-api: shows postgres and redis status
curl -s http://localhost:8000/readyz | python3 -m json.tool

# ai-service: also shows azure_openai status
curl -s http://localhost:8100/readyz | python3 -m json.tool
```

---

## 10. Demo Flow

Full script with narration and recovery playbook: [`Docs/Evaluation/DEMO_SCRIPT.md`](Docs/Evaluation/DEMO_SCRIPT.md)

**5-minute summary:**

| Time | Segment | Key point |
|---|---|---|
| T+0:00 | `make ps` — 5 containers healthy | "One command, five containers" |
| T+0:45 | Transaction Monitor at `localhost:3000` | Risk spine: cyan/amber/red encodes fraud level at a glance |
| T+1:45 | Click "Score now" on any transaction | Loading skeleton → animated meter → rule hits with evidence |
| T+3:30 | Policy Assistant — click a chip or type a question | `search_mode: vector` with cosine scores; graceful keyword fallback |
| T+4:30 | Architecture callouts | "0 imbalanced ledger entries across 10,045 — SQL-verified" |

---

## 11. Evaluation Summary

All numbers from the running system. Full detail: [`Docs/Evaluation/EVALUATION_RESULTS.md`](Docs/Evaluation/EVALUATION_RESULTS.md)

### Synthetic dataset

| Entity | Count |
|---|---|
| Transactions | **10,045** |
| Users | **501** |
| Wallets | **1,501** (INR + USD + EUR per user) |
| Ledger entries | **~18,208** |
| Imbalanced transactions | **0** (full-table SQL scan) |

### Transaction distribution

| Status | Count | % |
|---|---|---|
| success | 8,015 | 79.8% |
| failed | 982 | 9.8% |
| flagged | 711 | 7.1% |
| reversed | 337 | 3.4% |

### API latency (50-call sequential baseline)

| Endpoint | P50 | P99 |
|---|---|---|
| `GET /v1/payments` | 15 ms | 45 ms |
| `POST /v1/payments` | 35 ms | 90 ms |
| `POST /v1/fraud/score` (rules only) | **< 1 ms** | **< 1 ms** |
| `POST /v1/fraud/score` (with LLM) | 350 ms | 700 ms |
| `POST /v1/rag/query` (keyword) | 5 ms | 5 ms |
| `POST /v1/rag/query` (vector) | 180 ms | 450 ms |

### AI quality

| Metric | Result |
|---|---|
| Fraud rules registered | 15 |
| Score cap verified | 0 scores > 100 across 10,000 synthetic specs |
| Decision boundary cases | 6/6 pass (0→allow, 40→review, 75→reject) |
| RAG top-1 accuracy (vector) | **10/10 (100%)** — 10-query manual evaluation |
| RAG top-1 accuracy (keyword) | **8/10 (80%)** — 2 known misses documented |
| Knowledge chunks embedded | **48/48** |

### Frontend build

| Metric | Value |
|---|---|
| JS bundle gzipped | 52 KB |
| TypeScript errors | 0 |
| New npm dependencies | 0 |
| Build time | 1.67 s |

---

## 12. MVP vs Production Scope

This system demonstrates production *architecture* running in a local five-container stack. The architectural decisions are correct for production; the infrastructure hardening is a sequenced roadmap.

**What the MVP fully delivers:**

- Complete payment domain with financial-grade data discipline and a verified double-entry ledger
- Working two-tier AI pipeline that degrades gracefully at every integration point
- Production API contracts: RFC 7807 errors, idempotency, keyset pagination, decimal-precise amounts
- 12 Architecture Decision Records with explicit "revisit when" triggers
- Modular monolith with hexagonal package structure, extraction-ready

**What requires production hardening:**

| Dimension | MVP state | Production target |
|---|---|---|
| Authentication | None — all endpoints open | JWT RS256 + OPA RBAC (ADR pending) |
| MFA | Not implemented | TOTP / WebAuthn for admin access |
| Secrets | `.env` file | Azure Key Vault + CSI driver |
| TLS | HTTP on private bridge | mTLS via Istio service mesh |
| API Gateway | None | Kong / Azure API Management — rate limits, WAF |
| Observability | Structured logs + `/metrics` stub | Prometheus + OpenTelemetry + Grafana + PagerDuty |
| Postgres HA | Single node | Sync replication + 2 standbys + WAL backup |
| Deployment | Podman Compose, single host | Kubernetes AKS, active-active multi-region |
| CI/CD | None | GitHub Actions + Trivy + canary deploy |
| PCI-DSS | Not assessed | Level 1 — Vault Transit tokenisation, network segmentation |
| AML/KYC automation | Status field only | AML screening API + KYC workflow orchestration |

Full effort estimates and phase sequencing: [`Docs/Evaluation/PRODUCTION_READINESS.md`](Docs/Evaluation/PRODUCTION_READINESS.md)

---

## 13. Known Gaps and Roadmap

### P0 — Required before any real traffic

1. **Authentication** — JWT RS256 middleware + OPA RBAC. Middleware injection point exists; ~3 engineering-days.
2. **Secrets management** — migrate `.env` to Azure Key Vault with pod identity + CSI driver.
3. **TLS** — cert-manager + Let's Encrypt for external; Istio mTLS for inter-service.

### P1 — Operational reliability

4. **Observability** — wire `/metrics` to Prometheus; OpenTelemetry spans on DB + Redis + OpenAI calls; Grafana dashboards for the RED method (Rate, Errors, Duration) per endpoint.
5. **Postgres replication** — synchronous standby + automated WAL archiving (RPO ≤ 5 min).
6. **Transactional outbox** — `ops.outbox` schema is designed; the poller → Redis Streams writer is not yet implemented.
7. **Alembic startup wiring** — 8 migrations designed in `PAYMENT_DOMAIN_DESIGN.md`; not yet run as a Kubernetes init container.

### P2 — Platform maturity

8. **CI/CD** — GitHub Actions: lint → type-check → unit tests → build → Trivy CVE scan → integration tests → canary deploy.
9. **Rate limiting** — Redis sliding window in Kong; per-merchant and per-IP limits from `PAYMENT_DOMAIN_DESIGN.md §6`.
10. **XGBoost fraud model** — train on synthetic corpus + labelled data; MLflow tracking; A/B shadow mode before promotion.
11. **DeepEval RAG evaluation** — context recall + faithfulness metrics using `Docs/Evaluation/LLM_AS_JUDGE_EVALUATION.md` methodology.

### P3 — Scale and compliance

12. **Kubernetes** — 14-service decomposition on AKS; HPA + KEDA; active-active Central India + South India.
13. **PCI-DSS Level 1** — Vault Transit tokenisation for PAN; CDE network segmentation; annual pentest.
14. **AML / KYC automation** — AML screening API integration; KYC workflow with LangGraph multi-agent (ADR-004 design complete).
15. **LangGraph multi-agent** — fraud agent + settlement agent + dispute agent sharing context via the graph state store.

---

## 14. Troubleshooting

### Containers fail to start or stay unhealthy

```bash
make logs          # inspect all container output
make ps            # see which containers are unhealthy
make down && make up   # full restart
```

### `make health` returns connection refused

Services take 15–30 seconds to reach healthy state after `make up`. The startup order is enforced by `depends_on` with `condition: service_healthy`, but the API services have their own 10-second start period. Wait and retry:

```bash
sleep 30 && make health
```

If still failing, check the database first:

```bash
podman logs pg-postgres | tail -20
podman logs pg-redis | tail -20
```

### Dashboard shows no transactions

The seed scripts are separate from container startup. Run them once:

```bash
podman exec -it pg-core-api  python scripts/seed_demo_data.py
podman exec -it pg-ai-service python scripts/seed_knowledge_base.py
```

### RAG returns `search_mode: "keyword"` when you expect `"vector"`

Azure OpenAI is not configured or credentials are wrong. Verify:

```bash
curl -s http://localhost:8100/readyz | python3 -m json.tool
# Look for "azure_openai": "ok"  vs  "not_configured"
```

If `not_configured`: edit `.env` → set `AZURE_OPENAI_ENDPOINT` and `AZURE_OPENAI_API_KEY` → `make restart`.

### Fraud scoring returns `llm_used: false`

Same cause — the LLM explanation is a separate Azure OpenAI call. The deterministic rule engine and decision are unaffected. Fix the same way as above.

### Port already in use

```bash
# Change in .env
CORE_API_PORT=8001
AI_SERVICE_PORT=8101
FRONTEND_PORT=3001
```

`DATABASE_URL` and `REDIS_URL` use internal service-DNS names (`postgres`, `redis`), not host ports, so no other changes are needed. Then `make restart`.

### `podman-compose` version errors

```bash
pip install --upgrade podman-compose   # ensure >= 1.2 for depends_on conditions
```

### Docker alternative

```bash
# All make targets work with docker compose
make up COMPOSE="docker compose"
# Or export once per session
export COMPOSE="docker compose"
make up
```

### Complete reset

```bash
make clean    # removes containers AND pg-data + redis-data volumes
make up       # rebuilds from scratch
# Re-run seed scripts after ~30 seconds
```

---

## 15. Submission Checklist

Run this block immediately before the panel session:

```bash
#!/usr/bin/env bash
set -e
echo "=== 1. All containers healthy ==="
make ps && make health

echo ""
echo "=== 2. Transaction data present ==="
curl -sf "http://localhost:8000/v1/payments?limit=1" | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(f'OK — {d[\"count\"]} items in DB')"

echo ""
echo "=== 3. Fraud scoring operational ==="
curl -sf -X POST http://localhost:8100/v1/fraud/score \
  -H "Content-Type: application/json" \
  -d '{"transaction_id":"00000000-0000-0000-0000-000000000099","user_id":"00000000-0000-0000-0000-000000000098","merchant_id":"m_gambling_xyz","amount":"500000.00","currency":"INR","payment_method":"bank_transfer","metadata":{"country_receiver":"KP","is_new_device":true,"prior_failures":5}}' | \
  python3 -c "import json,sys; d=json.load(sys.stdin); assert d['decision']=='reject', f'Got {d[\"decision\"]}'; print(f'OK — score={d[\"risk_score\"]} decision=REJECT rules={len(d[\"rule_hits\"])}')"

echo ""
echo "=== 4. RAG knowledge base operational ==="
curl -sf -X POST http://localhost:8100/v1/rag/query \
  -H "Content-Type: application/json" \
  -d '{"query":"How long does a UPI refund take?","top_k":3}' | \
  python3 -c "import json,sys; d=json.load(sys.stdin); assert d['chunks'], 'No chunks'; print(f'OK — mode={d[\"search_mode\"]} chunks={len(d[\"chunks\"])}')"

echo ""
echo "=== ALL CHECKS PASSED — DEMO READY ==="
```

### Key documentation map

| Document | Purpose |
|---|---|
| [`Docs/Evaluation/PRODUCTION_READINESS.md`](Docs/Evaluation/PRODUCTION_READINESS.md) | MVP vs production gap analysis; 6-phase roadmap |
| [`Docs/Evaluation/EVALUATION_RESULTS.md`](Docs/Evaluation/EVALUATION_RESULTS.md) | Every measured number from the running system |
| [`Docs/Evaluation/DEMO_SCRIPT.md`](Docs/Evaluation/DEMO_SCRIPT.md) | Word-for-word 5-minute script with recovery playbook |
| [`Docs/Evaluation/FINAL_CHECKLIST.md`](Docs/Evaluation/FINAL_CHECKLIST.md) | Per-item rubric mapping with evidence pointers |
| [`Docs/Evaluation/LLM_AS_JUDGE_EVALUATION.md`](Docs/Evaluation/LLM_AS_JUDGE_EVALUATION.md) | LLM-as-judge methodology; 20 fraud + 30 RAG ground-truth cases |
| [`Docs/Evaluation/LOCUST_PERFORMANCE_TEST.md`](Docs/Evaluation/LOCUST_PERFORMANCE_TEST.md) | 6-scenario load test spec with Locust code |
| [`Docs/Data-Flow/PAYMENT_FLOW.md`](Docs/Data-Flow/PAYMENT_FLOW.md) | Payment processing flow with Mermaid diagram |
| [`Docs/Data-Flow/FRAUD_SCORING_FLOW.md`](Docs/Data-Flow/FRAUD_SCORING_FLOW.md) | Two-tier fraud scoring pipeline |
| [`Docs/Data-Flow/RAG_RETRIEVAL_FLOW.md`](Docs/Data-Flow/RAG_RETRIEVAL_FLOW.md) | Vector + keyword retrieval pipeline |
| [`Docs/Data-Flow/DATA_FLOW_DIAGRAM.md`](Docs/Data-Flow/DATA_FLOW_DIAGRAM.md) | End-to-end system architecture data-flow |

---

*"The correct answer to 'is this production-ready?' is: the architecture is right, the gaps are known, and the path is clear."*
