# AI-Powered Payment Gateway Platform
## Architecture & Design Review

**Author:** Jayachandran (FDE / Solution Architect)
**Mentor:** Siva
**Document status:** Draft for Design Review
**Date:** June 2026
**Scope:** End-to-end design — production target vs. capstone POC

---

## Reviewer's Note

This document is the architectural foundation for the capstone. It deliberately separates **what the production system should look like at scale** from **what is feasible to build in the POC**, because conflating the two is the most common failure mode flagged in the assessment rubric. Every major choice carries an ADR and an explicit trade-off statement.

---

## 1. Functional Requirements

Functional requirements are grouped by domain to mirror the eventual microservice boundaries. Each requirement is tagged with its source (Basic = R1, Advanced = R2).

### FR-1 — Identity, KYC & Wallets
| ID | Requirement | Source |
|---|---|---|
| FR-1.1 | User sign-up with email/phone verification | R1 |
| FR-1.2 | Login with password + MFA (TOTP or SMS-OTP) | R1 |
| FR-1.3 | KYC submission, status lifecycle (`pending → verified / failed`) | R1 |
| FR-1.4 | Multi-currency wallet (balance per ISO 4217 currency) | R1 |
| FR-1.5 | AML/KYC re-verification triggers based on risk events | R2 |
| FR-1.6 | GDPR data-subject request handling (export, erase, rectify) | R2 |

### FR-2 — Payment Processing
| ID | Requirement | Source |
|---|---|---|
| FR-2.1 | P2P transfers between wallets | R1 |
| FR-2.2 | Merchant-acceptance payments | R1 |
| FR-2.3 | Payment methods: card, bank transfer, wallet, UPI | R1 |
| FR-2.4 | FX conversion between currencies at quoted rate | R1 |
| FR-2.5 | Idempotent payment APIs (client-supplied idempotency key) | R1 |
| FR-2.6 | Refunds (full & partial) and reversals | R1 |
| FR-2.7 | Atomic ledger postings (double-entry, zero double-charge) | NFR |
| FR-2.8 | Integration with at least one sandbox provider (Stripe / Razorpay test mode) | R2 |

### FR-3 — Transaction Management
| ID | Requirement | Source |
|---|---|---|
| FR-3.1 | Transaction history with filter (date, status, merchant, currency) | R1 |
| FR-3.2 | Status lifecycle: `pending → success / failed / flagged / reversed` | R1 |
| FR-3.3 | Dispute creation, chargeback flag, resolution notes | R1 |
| FR-3.4 | Settlement reconciliation (expected payout vs. actual) | R1 |

### FR-4 — Fraud & Risk
| ID | Requirement | Source |
|---|---|---|
| FR-4.1 | Real-time fraud score `[0.0, 1.0]` per transaction | R1 |
| FR-4.2 | Rule-based velocity, geo-mismatch, BIN-country checks | R1 |
| FR-4.3 | ML model for risk scoring (GBT baseline) | R1 |
| FR-4.4 | Card-testing pattern detection (micro-amount bursts) | R1 |
| FR-4.5 | Suspicious-activity flagging and case creation | R1 |
| FR-4.6 | Feedback loop — analyst labels feed retraining | R2 |

### FR-5 — AI Layer (RAG, Agents, Evaluation)
| ID | Requirement | Source |
|---|---|---|
| FR-5.1 | NLP failure-reason explanation in natural language | R1 |
| FR-5.2 | RAG support assistant over historical transactions & KB | R1 |
| FR-5.3 | Hybrid search: BM25 keyword + dense semantic over transaction records | R1 |
| FR-5.4 | Tool-calling: lookup payment, fraud check, settlement status | R1 |
| FR-5.5 | Session + episodic memory for merchant support sessions | R1 |
| FR-5.6 | Multi-agent orchestration: Fraud, Settlement, Dispute, Support | R2 |
| FR-5.7 | LLM-as-judge for dispute-resolution validation and root-cause ranking | R2 |
| FR-5.8 | Reranking by transaction recency and resolution effectiveness | R2 |
| FR-5.9 | Token optimisation via chunking and hierarchical summarisation | R2 |
| FR-5.10 | DeepEval-based offline evaluation with custom metrics (fraud precision/recall, failure-prediction accuracy) | R2 |

### FR-6 — Merchant Operations
| ID | Requirement | Source |
|---|---|---|
| FR-6.1 | REST + gRPC merchant APIs with schema validation | R1 |
| FR-6.2 | Idempotency enforcement on write endpoints | R1 |
| FR-6.3 | Webhook delivery with retries and signature verification | R1 |
| FR-6.4 | Settlement reports (daily / on-demand) | R1 |
| FR-6.5 | Predictive analytics dashboard — settlement forecast, chargeback probability, anomaly alerts | R2 |

### FR-7 — Operational Workflows
| ID | Requirement | Source |
|---|---|---|
| FR-7.1 | Notifications (email, SMS, push) for confirmations and alerts | R1 |
| FR-7.2 | Ticket routing for operational escalations | R1 |
| FR-7.3 | Audit log of all sensitive operations (immutable) | NFR |

### FR-8 — Front-end
| ID | Requirement | Source |
|---|---|---|
| FR-8.1 | User wallet + transaction UI | R1 |
| FR-8.2 | Merchant dashboard (payments, settlements, disputes) | R1 |
| FR-8.3 | Support-agent console with AI assistant | R1 |

---

## 2. Non-Functional Requirements

NFRs are stated as **targets** with **measurement** and **production vs. POC** distinction. The POC will not hit production targets — it must, however, *demonstrate the design accommodates them*.

| Dimension | Production Target | Measurement | POC Demonstration |
|---|---|---|---|
| **Latency — Payment** | P99 < 2 s end-to-end | Synthetic load test on `/payments` | P99 < 500 ms locally with mocked downstreams |
| **Latency — AI query** | P95 < 4 s for RAG, < 8 s for multi-agent | OpenTelemetry traces | Same with cached embeddings |
| **Throughput** | 100,000 tx/sec sustained | k6/Locust at edge | 200–500 tx/sec on `docker-compose` |
| **Concurrent users** | 100 M registered, ~1 M concurrent | Connection pool + autoscaler | Connection pools sized appropriately |
| **Availability** | 99.999% (~5 min/year) | Multi-region active-active, automated failover | Single-node; documented HA strategy |
| **Durability** | Zero financial data loss; RPO ≤ 0 for ledger | Sync replication, WAL shipping | Postgres with logical replication enabled |
| **Security** | E2E TLS, PCI-DSS L1, card tokenisation, HSM-backed keys | Pen-test, ASV scans | TLS in compose; secrets via `.env`/Vault dev mode |
| **Compliance** | PCI-DSS, GDPR, AML/KYC | Audit logs, retention policies | GDPR-style endpoints; documented retention |
| **Reliability** | Idempotent APIs, atomic ledger, retries with backoff, circuit breakers | Chaos tests | Idempotency middleware, retry decorator, basic CB |
| **Observability** | 100% trace coverage, structured JSON logs, RED + USE dashboards | Prometheus + OTel + ELK | Same stack, single-node |
| **Geo distribution** | Active-active multi-region, residency-aware routing | Region pinning by `country_sender` | Single region; design doc covers strategy |
| **Cost** | Tiered LLM routing, SLM-first | $/transaction reporting | Local model fallback when OpenAI is unavailable |

---

## 3. Production Architecture

The production architecture is organised as **eight horizontal layers**. Every concern (security, observability, MLOps) is a first-class layer rather than a bolt-on.

### 3.1 Layered View

```
┌──────────────────────────────────────────────────────────────────────┐
│ L0 — Edge: CDN, WAF, DDoS, Anycast Global LB                         │
├──────────────────────────────────────────────────────────────────────┤
│ L1 — Ingress: API Gateway (Kong/Envoy), Rate Limit, Auth, mTLS term  │
├──────────────────────────────────────────────────────────────────────┤
│ L2 — Service Mesh: Istio sidecars, mTLS, retries, traffic shaping    │
├──────────────────────────────────────────────────────────────────────┤
│ L3 — Application: Stateless microservices on K8s (HPA + KEDA)        │
│       payment │ fraud │ ledger │ settlement │ dispute │ assistant   │
├──────────────────────────────────────────────────────────────────────┤
│ L4 — Async Backbone: Kafka (event log) + DLQ + Schema Registry       │
├──────────────────────────────────────────────────────────────────────┤
│ L5 — AI/ML Plane                                                     │
│       Model serving (vLLM, KServe) │ Embeddings │ Vector DB (Milvus) │
│       Feature store (Feast) │ Reranker │ Agent runtime               │
├──────────────────────────────────────────────────────────────────────┤
│ L6 — Data: CockroachDB (or sharded PG), Redis Cluster, OpenSearch,   │
│       Milvus, TimescaleDB, S3 (audit/cold), Tokenisation Vault       │
├──────────────────────────────────────────────────────────────────────┤
│ L7 — Cross-cutting: OTel, Prometheus, Grafana, Loki/ELK, MLflow,     │
│       Evidently (drift), Vault, KMS/HSM, OPA, immutable audit store  │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.2 Topology

- **Multi-region active-active** — three regions (e.g. `ap-south-1`, `eu-west-1`, `us-east-1`) with anycast LB and residency-aware routing keyed off `country_sender`.
- **Ledger consistency** — CockroachDB (or Spanner/YugabyteDB) for financial state, providing serialisable isolation across regions. Non-ledger services use sharded Postgres or DynamoDB.
- **Async backbone** — Kafka with MirrorMaker between regions; financial events are the system of record, projections are rebuildable.
- **Tokenisation** — card PAN never enters service code; it is tokenised at the edge via a PCI-scoped vault. Only tokens flow downstream → drastically narrows the PCI compliance boundary.
- **AI plane is separate** — `ai-assistant`, `fraud`, and `evaluation` services scale independently of the transactional path. The hot money path never blocks on an LLM call.

### 3.3 Critical Path SLOs

| Path | Components touched | Target |
|---|---|---|
| Card payment | Gateway → Identity → Fraud (rules+ML) → Ledger → Provider → Webhook | P99 < 2 s |
| RAG support query | Gateway → Assistant → Vector DB → Reranker → LLM | P95 < 4 s |
| Multi-agent investigation | Orchestrator → 2–4 sub-agents → tools → LLM | P95 < 8 s |
| Fraud-only score (sync) | Fraud service in-line | P99 < 80 ms |

---

## 4. POC Architecture

The POC must **prove the design** within capstone constraints (single machine, 2 weeks, one developer). It is intentionally a vertical slice of the production architecture, not a toy.

### 4.1 What is in scope for POC
- 7 microservices on `docker-compose` (one-command startup).
- Postgres (single instance), Redis (single), Kafka (single-broker KRaft mode) or Redpanda.
- FAISS for vectors (in-process), OpenSearch for keyword (or Postgres `tsvector` if OpenSearch is too heavy).
- Synthetic dataset of 10–20k transactions generated via Faker.
- One sandbox provider integration (Stripe Test or Razorpay sandbox).
- AI: OpenAI / Claude API as primary, Flan-T5 (HuggingFace, local) as fallback.
- Frontend: React (Vite) for user + merchant + agent consoles.
- Observability: Prometheus + Grafana + OTel collector + Loki (or stdout JSON for the lightest path).

### 4.2 What is explicitly out of scope for POC (documented, not built)
- Multi-region, K8s, service mesh, HSM, tokenisation vault.
- CockroachDB → use Postgres with the *contract* that ledger code is region-agnostic.
- Real PCI compliance (we will *simulate* tokenisation by hashing card numbers — and call this out clearly).

### 4.3 POC Topology Diagram

```
                        ┌─────────────────────┐
                        │   React Frontend    │
                        │ user / merchant /   │
                        │  support console    │
                        └──────────┬──────────┘
                                   │ HTTPS
                        ┌──────────▼──────────┐
                        │   API Gateway       │   FastAPI + auth/rate-limit
                        │   (FastAPI)         │   middleware
                        └──────────┬──────────┘
                                   │
   ┌──────────┬──────────┬─────────┼──────────┬──────────┬──────────┐
   ▼          ▼          ▼         ▼          ▼          ▼          ▼
┌──────┐  ┌──────┐   ┌──────┐  ┌───────┐  ┌──────┐  ┌──────┐  ┌──────────┐
│ ident│  │wallet│   │ pay  │  │fraud  │  │settle│  │disp  │  │ai-assist │
│      │  │      │   │      │  │       │  │      │  │      │  │  (RAG +  │
│      │  │      │   │      │  │       │  │      │  │      │  │  agents) │
└──┬───┘  └──┬───┘   └──┬───┘  └───┬───┘  └──┬───┘  └──┬───┘  └────┬─────┘
   │         │          │          │         │         │           │
   └─────────┴──────────┴────┬─────┴─────────┴─────────┘           │
                             │                                      │
                  ┌──────────▼──────────┐    ┌──────────────────────┘
                  │  Kafka / Redpanda   │    │
                  │  topics: payments,  │    │
                  │  fraud, settlement, │    │
                  │  audit              │    │
                  └──────────┬──────────┘    │
                             │                │
        ┌──────────┬─────────┼───────┬────────┴──────┬──────────┐
        ▼          ▼         ▼       ▼               ▼          ▼
    ┌─────────┐ ┌──────┐ ┌──────────┐ ┌──────────┐ ┌──────┐ ┌─────────┐
    │Postgres │ │Redis │ │OpenSearch│ │  FAISS   │ │ S3   │ │Sandbox  │
    │ (OLTP + │ │cache │ │(keyword) │ │ (vectors)│ │(MinIO│ │provider │
    │  ledger)│ │      │ │          │ │          │ │ audit│ │ Stripe  │
    └─────────┘ └──────┘ └──────────┘ └──────────┘ └──────┘ └─────────┘

         OTel → Prometheus + Grafana + Loki    (observability sidecar)
```

### 4.4 POC ↔ Production Mapping

| POC component | Production replacement | Swap-in difficulty |
|---|---|---|
| FAISS in-process | Milvus / Qdrant cluster | Low — interface isolated |
| Postgres single | CockroachDB or sharded PG | Medium — ledger code is dialect-aware |
| Redpanda single | Kafka multi-broker | Low |
| Hashed PAN | HSM-backed tokenisation vault | High — but boundary is the same |
| OpenAI direct | KServe-served self-hosted Llama / Claude API via gateway | Low — abstracted behind `LLMClient` |
| FastAPI gateway | Kong / Envoy + OPA | Low |
| docker-compose | Helm charts on K8s + ArgoCD | Medium |

---

## 5. Microservice Breakdown

Boundaries follow **business capability** (Domain-Driven Design), not technical layering. Each service owns its data — no shared schemas across services.

| # | Service | Responsibility | Primary store | Sync surface | Async events emitted |
|---|---|---|---|---|---|
| 1 | `api-gateway` | Auth, rate limit, request routing, idempotency check, schema validation | Redis (idempotency keys) | REST, gRPC, WS | — |
| 2 | `identity-service` | User, MFA, KYC, GDPR DSAR | Postgres `identity` | REST | `user.created`, `kyc.updated` |
| 3 | `wallet-service` | Wallet balances, currency holdings, FX quotes | Postgres `wallet` | gRPC | `wallet.debited`, `wallet.credited` |
| 4 | `payment-service` | Payment orchestration (saga), idempotency, refund, reversal | Postgres `payment` | REST, gRPC | `payment.initiated`, `payment.succeeded`, `payment.failed` |
| 5 | `ledger-service` | Double-entry postings, atomic transfers, reconciliation source-of-truth | Postgres `ledger` (separate cluster) | gRPC | `ledger.posted` |
| 6 | `fraud-service` | Rule engine + ML scoring, card-testing detector, case creation | Postgres `fraud`, Redis (velocity) | gRPC (sync score), REST | `fraud.flagged`, `fraud.case.opened` |
| 7 | `settlement-service` | Batch netting, payout calc, settlement reports, forecasting | Postgres `settlement` | REST | `settlement.completed`, `payout.scheduled` |
| 8 | `dispute-service` | Disputes, chargebacks, resolution notes, evidence | Postgres `dispute` | REST | `dispute.opened`, `dispute.resolved` |
| 9 | `notification-service` | Email, SMS, push, webhook delivery with retries | Redis (queue), Postgres (delivery log) | — | (consumer only) |
| 10 | `merchant-service` | Merchant onboarding, API keys, webhook config | Postgres `merchant` | REST | `merchant.activated` |
| 11 | `ai-assistant-service` | RAG, hybrid search, tool-calling, multi-agent orchestration | FAISS/Milvus, Redis (session memory) | REST, WS | — |
| 12 | `ingestion-service` | Pull transactions/incidents, chunk, embed, upsert into vector + keyword indexes | (writes to vector + OpenSearch) | — | `ingestion.completed` |
| 13 | `evaluation-service` | LLM-as-judge, DeepEval runner, drift reports | Postgres `eval`, S3 (artifacts) | REST | `eval.completed` |
| 14 | `audit-service` | Immutable audit log, GDPR compliance hooks | Append-only Postgres + S3 cold | REST (read-only) | — |

### Why this split survives review
- **Ledger is its own service with its own DB cluster** — the rubric calls out "atomic transactions, zero double-charge". Co-locating ledger code with `payment` is the classic tight-coupling mistake.
- **`ai-assistant` and `ingestion` are separated** — the assessment penalises monolithic AI services. The read-path (assistant) and write-path (ingestion) have completely different scaling profiles.
- **`evaluation-service` is a separate service**, not a script — this is what enables LLM-as-judge to run continuously, not as a one-off.

---

## 6. API Contracts

A representative set of contracts. All write endpoints require an `Idempotency-Key` header; all responses include a `trace_id`; all errors follow RFC 7807 (Problem Details).

### 6.1 Payment endpoints

**POST `/v1/payments`**
- Headers: `Idempotency-Key` (required, UUID), `Authorization: Bearer <jwt>`
- Request body fields: `merchant_id`, `amount`, `currency`, `payment_method`, `source` (tokenised), `destination`, `metadata`
- Response 201: `transaction_id`, `status` (`pending|success|failed`), `fraud_score`, `created_at`
- Response 409: returned when `Idempotency-Key` matches an in-flight or completed request
- Response 422: schema validation error (Pydantic-style problem details)

**GET `/v1/transactions/{transaction_id}`**
- Response 200: full transaction record per dataset schema
- Response 404: not found / not authorised (404 not 403 to avoid enumeration)

**POST `/v1/refunds`**
- Body: `transaction_id`, `amount` (≤ original), `reason`
- Returns: refund record + new `transaction_id` (refunds are first-class transactions in the ledger)

**POST `/v1/disputes`**
- Body: `transaction_id`, `dispute_reason`, `evidence_url[]`
- Returns: `dispute_id`, `state` (`opened|under_review|resolved`)

### 6.2 AI endpoints

**POST `/v1/assistant/query`**
- Body: `session_id`, `query`, `context_filters` (date range, merchant_id, etc.)
- Returns: `answer`, `citations[]` (transaction_id or doc_id + score), `confidence`, `trace_id`
- Streamed via SSE for token-by-token rendering

**POST `/v1/agents/invoke`**
- Body: `agent` (`fraud|settlement|dispute|orchestrator`), `task`, `inputs`, `session_id`
- Returns: `plan`, `steps_taken[]` (tools called, intermediate observations), `final_answer`, `judge_score` (when LLM-as-judge runs)

**POST `/v1/eval/run`** (internal)
- Body: `eval_suite` (`fraud_detection|rag_quality|dispute_resolution`), `sample_size`
- Returns: aggregated metrics + per-sample diffs in S3

### 6.3 Merchant integration

**POST `/v1/merchant/charges`** (gRPC mirror: `Merchant.CreateCharge`)
- Same idempotency contract as `/v1/payments`
- Webhook callback to `merchant.webhook_url` on terminal state, signed with HMAC-SHA256

### 6.4 Webhook contract (outbound)
- Headers: `X-Signature`, `X-Event-Id`, `X-Delivery-Attempt`
- Body: event envelope `{event_id, event_type, occurred_at, data}`
- Retry: 5 attempts with exponential backoff (1m, 5m, 30m, 2h, 12h); DLQ thereafter

---

## 7. Database Design

Polyglot persistence — each store is chosen for what it is genuinely best at. No "one DB to rule them all."

### 7.1 Store-by-store rationale

| Store | Role | Why this store |
|---|---|---|
| **Postgres (OLTP)** | Users, wallets, payments, disputes, settlements, merchants, fraud cases | ACID, mature, JSONB for flexible fields, rich indexing. Cost-effective. |
| **Postgres (Ledger, separate cluster)** | Double-entry ledger only | Isolated blast radius; ledger has different durability and replication needs |
| **Redis Cluster** | Sessions, idempotency keys (TTL ~24h), rate limiters, velocity counters, hot-cache for `GET /transactions/{id}` | Sub-ms latency, native TTL, atomic INCR for counters |
| **Kafka / Redpanda** | Event log: payments, fraud signals, audits, ingestion triggers | Replayable, durable, multi-consumer; system of record for events |
| **OpenSearch** | BM25 keyword index over `failure_reason`, `resolution_notes`, KB articles | Battle-tested keyword retrieval; pairs with vectors for hybrid search |
| **Vector DB (FAISS for POC, Milvus for prod)** | Dense embeddings of failure descriptions, resolved-incident memory, KB chunks | Approx-NN search at scale |
| **TimescaleDB** | Time-series: tx volumes, latencies, fraud-score distributions | Window functions, continuous aggregates — feeds the forecasting model |
| **S3 / MinIO** | Cold audit logs, evaluation artifacts, model checkpoints, raw datasets | Cheap, durable, lifecycle policies for retention |
| **Tokenisation Vault** (prod) | Card PAN → token mapping only | Narrows PCI scope. POC simulates with hashing. |

### 7.2 Key OLTP tables (selected)

**`payments.transactions`**
```
transaction_id          UUID PK
user_id                 UUID FK -> identity.users
merchant_id             TEXT NULL
amount                  NUMERIC(18,4)
currency                CHAR(3)
payment_method          ENUM
status                  ENUM
failure_reason          TEXT NULL
fraud_score             NUMERIC(4,3)
chargeback_flag         BOOLEAN DEFAULT false
settlement_status       ENUM
kyc_status              ENUM
country_sender          CHAR(2)
country_receiver        CHAR(2)
idempotency_key         TEXT UNIQUE NULL
created_at              TIMESTAMPTZ
updated_at              TIMESTAMPTZ
metadata                JSONB

Indexes:
  (user_id, created_at DESC)
  (merchant_id, created_at DESC)
  (status) WHERE status IN ('flagged','failed')   -- partial
  GIN(metadata)
  GIN(to_tsvector('english', failure_reason || ' ' || resolution_notes))  -- keyword fallback
```

**`ledger.entries`** (separate cluster)
```
entry_id                UUID PK
transaction_id          UUID         -- groups entries into a posting
account_id              UUID
direction               ENUM('DEBIT','CREDIT')
amount                  NUMERIC(18,4)
currency                CHAR(3)
posted_at               TIMESTAMPTZ
posting_status          ENUM('posted','reversed')
CONSTRAINT: sum(debits) = sum(credits) per transaction_id
```

**`fraud.cases`**
```
case_id, transaction_id, score, rule_hits[], model_version, analyst_id,
labelled_outcome ENUM('fraud','legit','unclear'), created_at
```

**`disputes.disputes`**
```
dispute_id, transaction_id, reason, state, evidence_url[], resolution_notes,
opened_at, resolved_at
```

### 7.3 Vector index design

- **Chunking**: per-incident chunk = `failure_reason + resolution_notes + status + amount_bucket`, ≤ 256 tokens.
- **Embedding model**: `bge-base-en-v1.5` (768d) — see ADR-003.
- **Index type**: HNSW (`M=16, ef_construction=200`) for production-like recall/latency balance.
- **Metadata filters**: `merchant_id`, `country_sender`, `timestamp_bucket`, `status`. Pre-filter before ANN.
- **Reranker**: cross-encoder `bge-reranker-base` on top-50 → top-5.

---

## 8. Event Flow

Event flows are described as **logical sequences**, not infrastructure diagrams (that's section 3). Each flow lists the actors, the events, and the failure handling.

### 8.1 Payment happy path (synchronous critical section + async tail)

```
Client ──POST /v1/payments──> Gateway
   Gateway ── validate, check idempotency in Redis
   Gateway ──gRPC──> payment-service
      payment-service ──gRPC──> fraud-service  (sync, <80ms budget)
         fraud-service: rules → ML → score
      payment-service ──gRPC──> ledger-service (sync, atomic posting)
      payment-service ──HTTP──> provider sandbox (sync OR async confirm)
      payment-service ──emit──> Kafka: payment.succeeded
                                       │
            ┌──────────────────────────┼──────────────────────────┐
            ▼                          ▼                          ▼
   settlement-service        notification-service          audit-service
   (batch netting)           (email/SMS/webhook)           (immutable log)
                                       │
                          (also consumed by ingestion-service
                           for downstream embedding & search index)
```

**Failure handling on this path**
- Fraud service timeout → circuit breaker opens → fall back to rules-only score with `model_unavailable=true` flag; transaction continues (do not block on AI).
- Provider timeout → mark `pending`, schedule reconciliation job, idempotency guarantees no double-charge.
- Ledger posting failure → return 5xx; client retries with same `Idempotency-Key`; no partial state.

### 8.2 Fraud investigation flow (async, agent-driven)

```
fraud.flagged ──> Kafka ──> orchestrator agent
   orchestrator: "investigate transaction T"
      ├── tool: get_transaction(T)
      ├── tool: get_user_history(user_id, last 30d)
      ├── tool: get_device_fingerprint(T)
      ├── sub-agent: fraud-investigator (LLM reasoning over signals)
      ├── sub-agent: compliance (AML pattern check)
      └── synthesise → case note + recommended action
   judge agent: scores case-note quality 1–5
   if score ≥ 4 → write to fraud.cases + notify analyst
   if score < 4 → escalate to human directly
```

### 8.3 RAG support flow

```
support agent query ──> assistant-service
   1. classify intent (failure-explain / settlement / dispute / generic)
   2. hybrid retrieve:
        BM25 over OpenSearch  (top 30)
        ANN over FAISS/Milvus (top 30, filtered by merchant_id + date)
        reciprocal rank fusion → top 50
   3. cross-encoder rerank → top 5
   4. recency + resolution-effectiveness boost
   5. compose prompt with citations
   6. stream answer (SSE) + citations
   7. log query, retrieved IDs, latency, token usage
   8. async: LLM-as-judge scores the answer offline
```

### 8.4 Ingestion / indexing pipeline

```
nightly OR on payment.succeeded ──> ingestion-service
   pull new transactions (incremental, watermark on `updated_at`)
   filter: status IN ('failed','flagged','reversed','disputed')
   chunk + embed (batch of 64, parallel workers)
   upsert into Milvus + OpenSearch
   emit ingestion.completed with counts
```

### 8.5 Settlement & forecasting

```
nightly settlement job
   read posted ledger entries for cycle window
   net by merchant + currency
   write settlement record
   forecast service runs (timeseries model on TimescaleDB)
   anomaly detection (z-score on payout deltas)
   if anomaly → emit settlement.anomaly → dashboard alert
```

---

## 9. Agentic AI Design

### 9.1 Topology

A **hierarchical multi-agent system** with one orchestrator and four specialist agents, plus a horizontal evaluator (LLM-as-judge). Sub-agents communicate via a shared **agent bus** (a thin abstraction over Kafka topics with structured A2A messages).

```
                       ┌───────────────────────┐
                       │   Orchestrator Agent  │
                       │   (planner + router)  │
                       └─────┬──────────┬──────┘
            ┌────────────────┤          ├────────────────┐
            ▼                ▼          ▼                ▼
   ┌────────────────┐ ┌──────────────┐ ┌──────────────┐ ┌────────────────┐
   │ Fraud          │ │ Settlement   │ │ Dispute      │ │ Support /      │
   │ Investigator   │ │ Reconciler   │ │ Resolver     │ │ Failure Q&A    │
   └────────┬───────┘ └──────┬───────┘ └──────┬───────┘ └──────┬─────────┘
            │                │                │                │
            └────────────────┴────────┬───────┴────────────────┘
                                      ▼
                          ┌───────────────────────┐
                          │  Tool Layer           │
                          │  get_transaction      │
                          │  get_user_history     │
                          │  rules_engine_query   │
                          │  vector_search        │
                          │  keyword_search       │
                          │  settlement_expected  │
                          │  open_case / ticket   │
                          │  notify_analyst       │
                          └───────────────────────┘
                                      │
                          ┌───────────────────────┐
                          │  LLM-as-Judge         │
                          │  scores plans &       │
                          │  outputs (async)      │
                          └───────────────────────┘
```

### 9.2 Per-agent design

| Agent | Goal | Tools allowed | Memory | Termination |
|---|---|---|---|---|
| Orchestrator | Decompose a question into sub-tasks, route, synthesise | All read tools | Session (Redis) | Max 8 steps, or judge says "sufficient" |
| Fraud Investigator | Produce a ranked list of root-cause hypotheses + recommended action | `get_transaction`, `get_user_history`, `rules_engine_query`, `vector_search(fraud_kb)` | Episodic (recent investigations per user) | When top-2 hypotheses have confidence > 0.7 |
| Settlement Reconciler | Explain why a payout differs from expected | `settlement_expected`, `get_transaction*`, `keyword_search` | None (stateless per query) | Always returns a structured diff |
| Dispute Resolver | Propose resolution + evidence | `get_transaction`, `vector_search(dispute_kb)` | Episodic per merchant | Always proposes; judge validates before acting |
| Support / Failure Q&A | Plain-English answer with citations | Hybrid `search`, `get_transaction` | Session memory of conversation | Streamed final answer |

### 9.3 LLM-as-Judge

Two distinct uses:
- **Online (lightweight)** — score a single agent output 1–5 against a rubric (`grounded?`, `actionable?`, `correct_format?`). Runs on a small model (Haiku / GPT-4o-mini / Flan-T5 locally). Latency budget ~ 500 ms.
- **Offline (rigorous)** — DeepEval suite runs nightly: faithfulness, answer relevance, context precision, fraud-decision agreement with labelled ground truth. Results land in `evaluation-service` and feed a quality dashboard.

### 9.4 Memory model

- **Session memory** (Redis, TTL = 1 h after last turn) — conversation context for a single support session.
- **Episodic memory** (Postgres + vector) — past investigations and resolutions, used as additional retrieval context for similar future cases. This is the *feedback loop* — analyst-labelled outcomes get embedded and become future retrieval material.
- **No global mutable state** between agent runs — each invocation is reproducible from inputs + session ID.

### 9.5 Guardrails
- Input: PII redaction before embedding; prompt-injection detector on user-supplied free text.
- Tool use: every tool call validated against a JSON schema; outputs truncated to token budgets.
- Output: structured response schema enforced (Pydantic / Instructor / OpenAI structured outputs).
- Cost: per-session token budget; orchestrator halts and returns "needs human" if budget exceeded.

---

## 10. ADR Decisions

ADRs use the lightweight **Status / Context / Decision / Consequences** format. Eight decisions are recorded here; more may be added under `/docs/decisions/`.

---

### ADR-001 — Vector store: FAISS (POC) → Milvus (production)
- **Status:** Accepted
- **Context:** Need ANN over ~10k–100M chunks; capstone must run on a laptop; production must scale horizontally.
- **Decision:** FAISS in-process for POC behind a `VectorStore` interface; Milvus targeted for production.
- **Alternatives considered:** Qdrant (close second — simpler ops), pgvector (rejected: poor recall at scale), Pinecone (rejected for prod: vendor lock-in, $$).
- **Consequences:** + Zero-ops in POC; + Clear swap path. − FAISS lacks filtering, so metadata filters are post-filtered → wasted recall. Mitigation: keep filter selectivity low in POC, document the production behaviour.

### ADR-002 — Async backbone: Kafka (production) / Redpanda (POC)
- **Status:** Accepted
- **Context:** Need a durable, replayable event log for financial events.
- **Decision:** Kafka semantics. Redpanda for POC (single binary, Kafka-API compatible).
- **Alternatives:** RabbitMQ (rejected: lacks log semantics, weaker replay); SQS (rejected: vendor, no replay).
- **Consequences:** + Replay enables rebuilding projections, retraining streams. − Operational complexity in prod (use managed service: Confluent Cloud, MSK, Aiven).

### ADR-003 — Embedding model: `bge-base-en-v1.5`
- **Status:** Accepted
- **Context:** Need a strong, cheap, self-hostable embedder. Domain is short, structured text.
- **Decision:** `bge-base-en-v1.5` (768d). Optional upgrade to `bge-large` on ambiguous queries via reranker tier.
- **Alternatives:** `text-embedding-3-large` (rejected as default: 5–10× cost, latency tax in ingestion); `e5-base-v2` (close, slightly weaker on retrieval benchmarks).
- **Consequences:** + Free, fast, locally-hostable. − Slightly weaker on long-context than OpenAI; mitigated by hybrid + reranker.

### ADR-004 — LLM tiering with mandatory local fallback
- **Status:** Accepted
- **Context:** Capstone rubric explicitly requires *local fallback*. Cost matters at scale.
- **Decision:** Three-tier routing:
  - **Tier 1 (heavy reasoning, low volume):** Claude Sonnet 4.6 / GPT-4o — orchestrator, dispute proposals.
  - **Tier 2 (high volume, structured):** Claude Haiku / GPT-4o-mini — failure explanation, Q&A.
  - **Tier 3 (fallback):** Flan-T5-large (local, ~770M) — when Tier 1/2 are down or rate-limited.
- **Consequences:** + Resilience + cost control. − T3 quality is materially lower; UX must indicate degraded mode.

### ADR-005 — Idempotency: client-supplied key + Redis lock + DB UNIQUE
- **Status:** Accepted
- **Context:** Rubric and PCI both demand zero double-charge.
- **Decision:** Client sends `Idempotency-Key` header (UUID). Gateway acquires a Redis lock on `(merchant_id, key)` with 5-minute TTL; the key is also stored on the `transactions` row with a `UNIQUE` constraint. On retry, the prior response body is served from Redis.
- **Consequences:** + Defence in depth (Redis + DB). − Two stores must stay consistent; we treat the DB UNIQUE as the source of truth and Redis as fast-path cache.

### ADR-006 — Ledger in its own database cluster
- **Status:** Accepted
- **Context:** Ledger is the financial source of truth — its blast radius must be smaller than the rest of the system.
- **Decision:** Separate Postgres cluster (production: CockroachDB) for ledger entries only. Other services consume ledger state via gRPC, never via shared DB connection.
- **Alternatives:** Single Postgres with separate schema (rejected: shared blast radius); event-sourcing-only ledger (rejected for POC: too much complexity).
- **Consequences:** + Independent scaling, backups, and access control. − Two databases to operate; cross-DB joins forbidden — services must call ledger API.

### ADR-007 — Hybrid retrieval with reciprocal rank fusion + cross-encoder rerank
- **Status:** Accepted
- **Context:** Pure vector search misses exact-match strings ("DECLINE_03"). Pure keyword misses paraphrase.
- **Decision:** BM25 (OpenSearch) + dense (Milvus/FAISS) → RRF fusion → cross-encoder rerank.
- **Consequences:** + Materially better recall on both code-like and natural-language queries. − Three components in the read path; reranker latency must stay < 200 ms on top-50.

### ADR-008 — Agent framework: LangGraph (with thin abstraction)
- **Status:** Accepted
- **Context:** Need an opinionated multi-agent framework with control over state and tool-calls.
- **Decision:** LangGraph for orchestration, wrapped behind a thin `AgentRuntime` interface so the framework is replaceable.
- **Alternatives:** CrewAI (rejected: opaque control flow), AutoGen (rejected: heavier dependencies), bare loop (rejected: ends up reimplementing LangGraph).
- **Consequences:** + Graph-explicit state; debuggable. − Framework churn risk; mitigated by the wrapper.

---

## 11. Latency vs Cost vs Accuracy — Trade-offs

A consolidated view of the most important trade-offs. Each row names the decision lever, the three points on the triangle, and our chosen position.

| Decision lever | Latency-optimal | Cost-optimal | Accuracy-optimal | **Chosen position** |
|---|---|---|---|---|
| **Embedding model** | `bge-small` (~5 ms, 384d) | self-host `bge-small` | `text-embedding-3-large` (~150 ms, 3072d, $$) | `bge-base` (768d) — balanced; large only via offline re-index for high-value KB |
| **LLM tier for failure-explanation** | Haiku / GPT-4o-mini (~700 ms) | Flan-T5 local (free, but ~50% quality) | Claude Sonnet / GPT-4o (~3 s, $$) | Tier 2 by default; Tier 3 fallback when upstream fails |
| **Reranker** | none — pure ANN top-k | none | full cross-encoder rerank top-200 | cross-encoder on **top-50 only** — best ROI |
| **Vector index type** | flat exact (slow at scale) | IVF-PQ (compressed, lossy) | flat exact | HNSW (`M=16, efSearch=64`) — well-known sweet spot |
| **Fraud scoring** | rules-only (~5 ms) | rules-only | LLM-reasoning per tx ($$$) | rules → GBT → (async) LLM investigation only on flagged |
| **Settlement compute** | per-tx synchronous (high cost, low latency for read) | nightly batch (cheap, stale) | continuous streaming aggregations | nightly batch + on-demand recompute for queries |
| **Cache TTL on tx reads** | TTL = ∞ (fastest, stalest) | low TTL = high re-fetch cost | TTL = 0 (always-fresh) | 60 s with explicit invalidation on `payment.*` events |
| **Hybrid search fan-out** | vector-only | keyword-only | wide fan-out + rerank | BM25(30) + ANN(30) → RRF → rerank top-50 |
| **Multi-agent loop budget** | 1 step (fastest, weakest) | low step budget | unbounded ($$$, slowest) | 8 steps hard cap; judge can terminate earlier |
| **Synthetic data ingestion** | serial (slow but simple) | serial | parallel + larger batches (faster, more memory) | 8-worker parallel ingestion with batched embeds — explicitly called out by rubric |
| **Token strategy for bulk analysis** | full context (slow, expensive) | aggressive truncation (cheap, lossy) | hierarchical summarisation | map-reduce summarisation per merchant window, then synthesise |
| **PII / tokenisation** | hash in-process (fast, cheap, not PCI-compliant) | hash | HSM-backed vault (slow, expensive, compliant) | hash in POC; **document** vault as the production answer |

### Headline trade-off statements (for the design review)

1. **The hot money path never blocks on an LLM.** Fraud is rules + GBT inline; LLM reasoning runs asynchronously on flagged transactions only.
2. **Hybrid + rerank beats either pure approach** — accepted ~150 ms reranker cost on top-50 to materially improve top-3 precision on failure-reason queries.
3. **Local fallback is treated as a first-class feature, not a fire drill** — it has its own model, its own prompts, and its own quality bar; the UX surfaces "degraded mode" so users know.
4. **Ledger correctness is non-negotiable; everything else can be eventually consistent.** Projections, search indexes, analytics, vector embeddings all derive from the ledger event stream and can be rebuilt.
5. **Cost is a design constraint, not an afterthought** — three-tier LLM routing, SLM-first for high-volume tasks, and a per-session token budget on agents are explicit cost-control mechanisms.

---

## Appendix A — Open Questions for Mentor Review

1. Should the POC integrate Stripe Test or Razorpay sandbox (Indian context favours Razorpay; Stripe gives more demo polish)?
2. Is a **single** Postgres acceptable for both OLTP and Ledger in the POC, with the **architectural commitment** that they're separate clusters in prod? (Recommended yes — capstone scope.)
3. For LLM-as-judge: do we want the judge model to differ from the producer model? (Recommended yes — reduces self-grading bias.)
4. Granularity of front-end: minimum viable is user + merchant + support console. Should we cut the merchant predictive dashboard to save time?

## Appendix B — Mapping to Assessment Rubric

| Rubric dimension | Where this document addresses it |
|---|---|
| Architecture vs. data flow distinction | §3 (architecture) vs §8 (event/data flow) — explicitly separated |
| POC vs Production distinction | §3 and §4 paired, plus §4.4 mapping table |
| Observability & MLOps | §3.1 L7, §9.3 (offline eval) |
| ADRs with pros/cons | §10 (eight ADRs with alternatives and consequences) |
| Decoupling (vector DB swap) | ADR-001, §4.4, `VectorStore` interface called out |
| Latency / Cost / Accuracy trade-offs | §11 (full table + headline statements) |
| Stateless, horizontally scalable | §3.2, §5 (every service marked stateless except `ai-assistant` session, which is Redis-backed) |
| Reliability (retries, fallbacks, circuit breakers) | §8.1, ADR-004, NFR table |
| Ground truth + evaluation methodology | §9.3, FR-5.10 |
| Production-grade observability | §3.1 L7, §6 (`trace_id` in every response) |
