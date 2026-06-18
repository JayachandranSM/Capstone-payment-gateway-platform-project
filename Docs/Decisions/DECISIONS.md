# DECISIONS.md — MVP Implementation Decisions

**Scope:** Architectural Decision Records for the **MVP implementation** (4–5 day capstone build).
**Author:** Jayachandran  ·  **Mentor:** Siva
**Companion documents:** `docs/architecture/ARCHITECTURE_REVIEW.md` (production architecture ADRs) · `docs/architecture/DIAGRAMS.md` · `MVP_PLAN.md`

> **How this file relates to the production ADRs in `ARCHITECTURE_REVIEW.md` §10:**
> The production ADRs answer *"what would we build at scale?"* (Milvus, Kafka, CockroachDB, K8s).
> This file answers *"what are we shipping in 5 days, and why is that the right choice **now**?"*
> Where the MVP choice differs from the production choice, this is called out under **Production counterpart**, with the swap path documented in the relevant production ADR.

---

## Index

| ADR | Title | Status |
|---|---|---|
| ADR-001 | Why FastAPI | Accepted |
| ADR-002 | Why PostgreSQL + PGVector (MVP) | Accepted |
| ADR-003 | Why Redis | Accepted |
| ADR-004 | Why LangGraph | Accepted |
| ADR-005 | Why Docker Compose | Accepted |
| ADR-006 | Why Streamlit | **Superseded by ADR-011** |
| ADR-007 | Why OpenAI as primary LLM | Accepted |
| ADR-008 | Why a Modular Monolith for the MVP | Accepted |
| ADR-009 | Why hybrid search via tsvector + pgvector (in-database) | Accepted |
| ADR-010 | Why Flan-T5-large as the local fallback model | Accepted |
| ADR-011 | Why React + TypeScript + Vite (supersedes ADR-006) | Accepted |
| ADR-012 | Multi-Model AI Strategy (refines ADR-007) | Accepted |

---

## ADR-001: Why FastAPI

**Status:** Accepted  ·  **Date:** 2026-06-15  ·  **Decider:** Jayachandran

### Context
We need a Python web framework for three services (`core-api`, `ai-service`, `ingestion-worker`'s admin surface). The framework must (a) handle async I/O efficiently because most of our work is I/O bound on Postgres, Redis, OpenAI, and downstream gRPC calls; (b) provide first-class schema validation, which the capstone rubric explicitly demands; (c) generate OpenAPI specs automatically so the merchant-facing API contract is self-documenting; (d) integrate cleanly with Pydantic, since LangGraph and the OpenAI SDK already lean on it.

### Decision Drivers
- Native `async/await` — RAG queries make 4–6 outbound calls each; threaded frameworks would block.
- Pydantic-native — the rubric calls out Pydantic by name for input validation.
- Auto-generated OpenAPI — saves writing a separate API spec.
- Mature ecosystem for SSE streaming (needed for the assistant `query` endpoint).
- Single developer's familiarity — known stack, no learning tax.

### Options Considered

| Option | Verdict |
|---|---|
| **FastAPI** | Async-native, Pydantic-native, OpenAPI free, SSE/WS support, fast |
| Flask 3 + Marshmallow | Requires gevent/gunicorn for async; validation is an add-on; OpenAPI via extensions |
| Django REST Framework | Heavy ORM and admin we don't need; async support is partial; rubric-mismatch on minimalism |
| Starlette (raw) | What FastAPI is built on — but we'd reinvent FastAPI's ergonomics |
| Quart | Async Flask; smaller community, fewer Pydantic-native patterns |
| Litestar | Strong contender on technical merit but less ubiquitous; risk on capstone review |

### Decision
We choose **FastAPI**. It is the only option that hits all five decision drivers simultaneously, and the ecosystem (FastAPI + Pydantic v2 + asyncpg + Redis-asyncio + OpenAI async SDK) is mutually coherent.

### Consequences
**Positive:**
- Schema validation, OpenAPI docs, and async are free with the framework.
- Pydantic v2 is fast enough that validation is not a hot-path concern.
- Lifespan hooks give us a clean place to load the GBT fraud model and the Flan-T5 fallback at startup (rubric: cold-start optimisation).

**Negative:**
- Async/await discipline is required everywhere — one accidental sync `requests.get()` blocks the event loop.
- Less mature middleware ecosystem than Flask for niche needs.

**Mitigations:**
- `httpx.AsyncClient` enforced across the codebase (lint rule); `requests` is uninstalled.
- Middleware needs are modest: request-id, idempotency, auth — all custom in `app/middleware/`.

### Revisit when
- We need server-side rendering of HTML at scale → consider FastAPI + Jinja2 or move to a Django subset.
- gRPC becomes the dominant interface → consider `grpcio` standalone, FastAPI relegated to a side channel.

---

## ADR-002: Why PostgreSQL + PGVector (MVP)

**Status:** Accepted  ·  **Production counterpart:** `ARCHITECTURE_REVIEW.md` ADR-001 (FAISS → Milvus)

### Context
The system needs OLTP storage (users, wallets, transactions, ledger), keyword search (BM25-equivalent over `failure_reason`/`resolution_notes`), and vector search (semantic retrieval over embedded transaction failures and KB articles). The production architecture (Architecture Review §3) calls for Postgres + OpenSearch + Milvus. For a 5-day MVP, operating three datastores is wasted effort — we have ~15k embeddings, not 15M.

### Decision Drivers
- **One datastore to operate.** Single dev, single host.
- **Hybrid retrieval in one query.** Postgres can do `tsvector` (BM25-like) and `pgvector` (ANN) and `JOIN` them — no two-phase fetch across systems.
- **Familiar transactional guarantees.** Ledger needs ACID; the team understands Postgres' isolation model.
- **HNSW is now first-class.** pgvector ≥ 0.5 supports HNSW with comparable recall/latency to standalone vector DBs at our scale.

### Options Considered

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Postgres + pgvector** | Single store; transactional joins; hybrid via SQL | Limited at 10M+ vectors; HNSW build can be slow on huge datasets | **Chosen for MVP** |
| Postgres + Milvus | Best vector performance at scale | Two stores, two indexing pipelines, more ops | Production target |
| Postgres + Qdrant | Strong vector engine, simpler than Milvus | Still two stores | Acceptable production alternative |
| Postgres + FAISS in-process | Zero ops | FAISS is single-process; can't share state across services | Rejected |
| SQLite + sqlite-vec | Truly minimal | No real concurrent writes; rubric expects production-grade | Rejected |
| MongoDB Atlas vector | Vector + document in one | Not in stack constraints; vendor lock-in | Rejected |

### Decision
**Postgres 16 with `pgvector` and `tsvector`.** One Postgres instance, multiple schemas (`core`, `ledger`, `ai`). HNSW index on `ai.embeddings.embedding`. GIN index on `to_tsvector(...)` for keyword arm.

### Consequences
**Positive:**
- **Hybrid search is a single SQL query** with reciprocal rank fusion computed in Python after two parallel `SELECT`s, or as one CTE with `UNION ALL`. Either approach is faster than network round-trips to two services.
- Ledger code can use `SERIALIZABLE` isolation for double-entry posting without distributed-transaction complexity.
- Backups are trivial (`pg_dump`).
- Capstone reviewers can probe the schema directly via `psql` — boosts the "self-documenting" rubric dimension.

**Negative:**
- pgvector HNSW build time grows with N; at ~15k rows on M1/M2 laptops it builds in <10s, fine for our scale.
- Beyond ~10M vectors, pgvector loses to dedicated stores on tail latency.
- All eggs in one basket — Postgres outage takes the whole system down. Acceptable for POC; production design (Architecture Review §3) separates the ledger cluster.

**Mitigations:**
- `VectorStore` interface abstracts the retrieval surface; swapping to Milvus or Qdrant is a single class change (Architecture Review ADR-001).
- Document the production split explicitly in `ARCHITECTURE_REVIEW.md` §3.2.

### Revisit when
- Embedding count exceeds 1M (HNSW build becomes slow, write throughput degrades) → migrate `ai.embeddings` to Milvus.
- Ledger throughput exceeds 5k writes/sec → split ledger to its own cluster (Architecture Review ADR-006).
- We need cross-region replication with strong consistency → migrate to CockroachDB (Architecture Review ADR-006).

---

## ADR-003: Why Redis

**Status:** Accepted

### Context
We need a fast, shared, ephemeral store for four distinct needs: (1) **idempotency keys** with TTL, (2) **session memory** for agent conversations with TTL, (3) **velocity counters** for the fraud rules engine (atomic INCR), (4) **lightweight event fan-out** between services via Streams (in lieu of Kafka for the MVP).

### Decision Drivers
- **Sub-ms read/write.** Idempotency middleware is on the hot money path.
- **Native TTL.** Idempotency keys expire after 24h, session memory after 1h, velocity windows after 5 min — TTL semantics must be primitive, not application-level.
- **Atomic counters.** Velocity rules need race-free INCR.
- **Streams for events.** Replacing Kafka for the MVP requires durable, replayable, multi-consumer event topics.

### Options Considered

| Option | Verdict |
|---|---|
| **Redis 7** | All four needs met natively; ubiquitous; one binary |
| Memcached | No TTL on individual keys with conditional set; no streams; no atomic counter beyond increment | Rejected |
| In-process LRU (e.g. `cachetools`) | Not shared across services; loses state on restart | Rejected |
| KeyDB / Dragonfly | Redis-compatible faster forks; intriguing but adds risk for no win at our scale | Rejected |
| Postgres for everything | "Just use the DB" is tempting but writes have ~5ms latency floor; idempotency check is too hot | Rejected |
| Hazelcast | JVM-based, overkill | Rejected |

### Decision
**Redis 7**, accessed via `redis.asyncio` with a connection pool sized at 50. Single instance for the POC; documented as Redis Cluster for production.

### Consequences
**Positive:**
- Idempotency middleware can do `SET key value NX EX 86400` — atomic check-and-set in one round-trip.
- Velocity rule uses `INCR` + `EXPIRE` in a pipeline for race-free 5-minute windows.
- Session memory has clean TTL semantics; no garbage-collection job needed.
- Streams (`XADD`, `XREAD`) let us preserve logical Kafka topic names (`payment.succeeded`, etc.) with consumer groups, so swapping to Kafka later is mechanical.

**Negative:**
- Another service to operate (small, but real).
- Single-instance Redis is a SPOF in the POC.

**Mitigations:**
- Redis is genuinely set-and-forget at our scale; the container's `restart: unless-stopped` policy is enough.
- Production: Redis Cluster with active-replica, or Azure Cache for Redis Enterprise (Architecture Review §6 — Azure mapping).

### Revisit when
- Event throughput exceeds 100k/sec or replay needs cross-cluster → migrate from Redis Streams to Kafka.
- Working-set exceeds 8 GB → cluster or move to managed Redis.

---

## ADR-004: Why LangGraph

**Status:** Accepted  ·  **Production counterpart:** `ARCHITECTURE_REVIEW.md` ADR-008

### Context
The capstone advanced tier requires **multi-agent orchestration** for fraud detection, settlement reconciliation, and dispute handling, plus **agent-to-agent communication** and **LLM-as-judge**. We need an orchestration layer that gives explicit control over agent state, tool calls, conditional routing, and termination — not a black-box "ask the LLM nicely" framework.

### Decision Drivers
- **Explicit state.** Audit trails must show what each agent saw at each step.
- **Conditional edges.** The orchestrator must route based on judge scores, confidence thresholds, and max-iteration caps.
- **Replayable / checkpointable.** Failed runs should be re-runnable from the last good state.
- **Observability.** Each node call must emit a span (OTel) and a structured log line.
- **Reasonable abstraction tax.** We must not spend a day fighting the framework.

### Options Considered

| Option | Pros | Cons |
|---|---|---|
| **LangGraph** | Explicit state graph (TypedDict), conditional edges, checkpointer, mature tool integration, well-documented | Framework churn risk; tied to LangChain ecosystem |
| CrewAI | Role-based abstraction reads naturally | Opaque control flow — hard to enforce a step cap or judge gate cleanly |
| AutoGen (Microsoft) | Multi-agent dialogue paradigm | Heavier; conversation-as-state is awkward for our deterministic flows |
| LangChain Agents (legacy) | Mature | Deprecated in favour of LangGraph by LangChain themselves |
| Haystack Agents | Solid retrieval ecosystem | Less mature agent layer than LangGraph |
| Bare Python loop | Zero abstraction tax | We'd end up reimplementing LangGraph poorly |
| OpenAI Assistants API | Hosted, simple | Vendor lock-in; no local fallback compatibility; doesn't satisfy "agent framework" rubric expectation |

### Decision
**LangGraph**, wrapped behind a thin `AgentRuntime` interface (`runtime.invoke(agent, task, inputs)`) so the dependency is contained. State is a `TypedDict`. Termination is enforced by both a step counter (max 8) and a judge gate.

### Consequences
**Positive:**
- The graph diagram in `DIAGRAMS.md` §5 maps **1:1** to the code in `services/ai-service/app/agents/graph.py`. Reviewers can read both side by side.
- State checkpoints to Postgres make failed runs resumable.
- Streaming intermediate observations to the UI is built-in.

**Negative:**
- LangGraph evolves quickly; pinning to a specific version is essential.
- Some debugging requires understanding LangGraph's internal node-resolution order.

**Mitigations:**
- Version pinned in `pyproject.toml`; no upgrades during the capstone window.
- `AgentRuntime` wrapper means a future framework swap touches one file.
- We write our own visualisation of `state.steps` rather than relying on LangSmith.

### Revisit when
- LangGraph has a major-version break that ripples through tools/checkpoints → re-evaluate alternatives at that moment.
- Multi-agent flows become hierarchical 3+ levels deep → consider AutoGen-style conversation patterns.

---

## ADR-005: Why Docker Compose

**Status:** Accepted  ·  **Production counterpart:** AKS / K8s (see `DIAGRAMS.md` §6)

### Context
The capstone rubric explicitly calls out *"working Dockerfile and docker-compose.yml for a one-command startup."* We need to run 6 containers (Postgres, Redis, core-api, ai-service, ingestion-worker, frontend) on a single laptop, reliably, for a live demo.

### Decision Drivers
- **One-command demo.** `make up` must work, every time, in <30s.
- **Hot reload during dev.** Volume mounts for service code.
- **No production-deployment ambition.** This is for the POC and demo only.
- **Standard tooling.** Reviewers can `docker compose ps` without learning new commands.

### Options Considered

| Option | Verdict |
|---|---|
| **Docker Compose v2** | Native, ubiquitous, named explicitly by the rubric, fast on a laptop |
| `kind` / `k3d` (local K8s) | Closer to production but slow startup, more YAML, more failure modes during a live demo | Rejected for MVP |
| Podman Compose | OK on Linux, friction on Mac/Windows | Rejected |
| Tilt / Skaffold | Strong dev-loop, but learning curve | Overkill |
| Bare Python with `supervisord` | No container parity with production | Rejected — fails rubric |

### Decision
**Docker Compose v2** with a single `docker-compose.yml` at repo root. Per-service `Dockerfile`s use multi-stage builds (slim base, no dev deps in the final image).

### Consequences
**Positive:**
- `make up && make seed && make demo` is the entire setup story.
- Each service has its own image — rubric: "microservice architecture reflected in code boundaries/packages."
- Healthchecks in compose drive startup ordering (`depends_on.condition: service_healthy`).

**Negative:**
- Compose does not represent how the system would actually be deployed (no HPA, no service mesh, no rolling deploys).
- One bad container can take the whole stack down — acceptable for demo.

**Mitigations:**
- `DIAGRAMS.md` §6 shows the Azure / K8s deployment for the production answer.
- README has a "Production deployment" section linking to the AKS topology, so reviewers know we *know* the difference.

### Revisit when
- We need to demo HPA or rolling deploys → ship a `kind` setup as `make up-k8s`.
- The service count crosses ~10 → Compose becomes brittle, move to K8s for dev.

---

## ADR-006: Why Streamlit

**Status:** ⚠ **Superseded by ADR-011** (2026-06-19)  ·  *Original status: Accepted (2026-06-15)*

> **Supersession note.** This ADR is preserved as a historical record. The decision recorded here was correct at the time it was made under a 3-hour frontend budget. After review with the mentor, the perceived production-readiness of the demo was judged to materially affect panel scoring, and the "revisit when" trigger at the bottom of this ADR ("we need real user management … or fine-grained UX") was effectively activated by that re-prioritisation. The new decision and its full reasoning live in **ADR-011** at the bottom of this document. Read both ADRs together to understand the trade-off path.

### Context
The capstone requires a *"simple front-end interface for user and merchant interaction."* We have three UI surfaces — user console, merchant dashboard, support console with chat — and a hard budget of **~3 hours** of frontend development across all of them. The panel will score the AI, retrieval quality, and architecture — not pixel fidelity.

### Decision Drivers
- **Build time first.** Frontend cannot eat into agent/RAG time.
- **Streaming chat UX.** SSE token streaming for the support assistant.
- **Decent tables and charts.** Settlement variance, failure-rate trends, fraud-case list.
- **No build pipeline.** Single dev; we don't have time for Vite/Webpack debugging during demo prep.
- **Python-native.** Reuses our Pydantic schemas for type-safety in the UI client.

### Options Considered

| Option | Build time | Streaming chat | Tables / charts | Verdict |
|---|---|---|---|---|
| **Streamlit** | ~3 h for 3 pages | Native (`st.write_stream`) | `st.dataframe`, `st.plotly_chart` — strong | **Chosen** |
| Gradio | ~3 h | Native | Weaker tables; opinionated layout | Rejected — fights us on multi-page |
| React + Vite + Tailwind | ~15 h | Hand-rolled SSE | Best in class | Rejected — time |
| Next.js | ~12 h | Native | Best in class | Rejected — time |
| HTMX + Jinja2 (FastAPI templates) | ~6 h | Workable | Manual | Rejected — chat UX is worse than Streamlit |
| Vanilla HTML + fetch | ~8 h | Manual | Manual | Rejected — looks worse than Streamlit |

### Decision
**Streamlit** with three pages under `frontend/pages/`. Auth handled by attaching the JWT to API calls from a shared `api_client.py`. The agent-trace panel is rendered with `st.expander` per step.

### Consequences
**Positive:**
- Each page is ~50–80 lines of Python — easy to maintain and demo.
- `st.write_stream` makes the SSE assistant feel modern out of the box.
- Reviewers can read the UI code in a minute.

**Negative:**
- Looks like a Streamlit app. There is no hiding this.
- Limited control over layout, state across reruns is awkward, hot-reload sometimes drops session state.

**Mitigations:**
- Monochrome theme + a small custom CSS block to soften the "default Streamlit" look.
- `st.session_state` discipline — every cross-rerun value goes through it deliberately.
- The README and PPT lead with the *architecture* and *evaluation results*, not the UI, so the panel knows where to look.

### Revisit when
- We need real user management, file uploads, or fine-grained UX (drag-and-drop, custom forms) → React/Next.js becomes the answer.
- Concurrent users exceed ~5 in any meaningful way → Streamlit's single-process model becomes a bottleneck.

---

## ADR-007: Why OpenAI as primary LLM

**Status:** Accepted

### Context
The system needs an LLM provider for (a) RAG answer generation, (b) multi-agent reasoning in LangGraph, (c) the online LLM-as-judge. The capstone constraints name **OpenAI** in the required stack, but a panel may still ask *"why not Claude / Gemini / a self-hosted model?"* — this ADR captures the defence.

### Decision Drivers
- **Tool-calling maturity.** LangGraph relies on the model's native function-calling; OpenAI's implementation is the reference one.
- **Structured outputs.** GPT-4o-mini supports JSON-schema-constrained outputs natively — critical for our agent state being parseable.
- **Cost predictability.** GPT-4o-mini is ~$0.15/1M input tokens, the cheapest top-tier model.
- **Tier flexibility.** GPT-4o for orchestrator/judge, GPT-4o-mini for high-volume tasks — same SDK.
- **Available rate-limit headroom.** Capstone budget allows comfortable use without hitting walls.

### Options Considered

| Option | Verdict |
|---|---|
| **OpenAI (GPT-4o + GPT-4o-mini)** | Tool-calling + structured outputs + tiering, all native | **Chosen as primary** |
| Anthropic Claude (Sonnet + Haiku) | Excellent reasoning; tool-use is mature; we'd happily use it | Rejected only because OpenAI is in stack constraint; documented as a swap candidate |
| Google Gemini | Strong but tool-calling integration with LangGraph is younger | Rejected for MVP |
| Azure OpenAI | Same models, enterprise routing | Production answer (DIAGRAMS.md §6); MVP uses direct OpenAI for simplicity |
| Self-hosted Llama 3 / Mistral via vLLM | No vendor lock; pricier infra, longer dev time | Rejected for primary; used as fallback model class |
| Cohere Command R+ | Good RAG model; less common LangGraph integration | Rejected |

### Decision
**OpenAI as primary** with tiered routing:
- **Tier 1 (heavy reasoning, low volume):** `gpt-4o` — orchestrator planning, dispute resolution proposals.
- **Tier 2 (default, high volume, structured outputs):** `gpt-4o-mini` — failure-explanation Q&A, the LLM-as-judge.
- **Tier 3 (local fallback):** Flan-T5-large — see ADR-010.

The `LLMClient` abstracts the provider; switching to Anthropic Claude is a single-file change.

### Consequences
**Positive:**
- Structured outputs eliminate prompt-engineering for JSON-shaped responses.
- Tier router (in `app/llm/router.py`) caps cost per session and degrades gracefully under rate limits.
- Familiar billing model for the demo budget.

**Negative:**
- Single-vendor dependency on the primary path; outage = degraded mode.
- Data sent to OpenAI is not residency-controlled in the POC.

**Mitigations:**
- Local fallback (ADR-010) is wired in and demoed during the panel — degraded mode is a *demonstrated feature*, not a slide.
- For production, Azure OpenAI (data residency) is the documented swap.
- PII redaction happens **before** LLM calls (`app/llm/prompts/_redactor.py`).

### Revisit when
- Cost per query exceeds 5× our budget → switch high-volume Tier 2 traffic to a self-hosted Llama 3 70B on vLLM.
- Data residency becomes a hard requirement → swap to Azure OpenAI in `LLMClient`.
- Anthropic releases a structured-outputs feature that beats OpenAI's → reconsider for Tier 1.

---

## ADR-008: Why a Modular Monolith for the MVP

**Status:** Accepted  ·  **Production counterpart:** 14-service split, `ARCHITECTURE_REVIEW.md` §5

### Context
The production architecture (Architecture Review §5) describes 14 microservices. Building 14 deployable services in 4–5 days as a single developer is not realistic — each independent service multiplies overhead (Dockerfile, settings, observability wiring, inter-service auth, contract drift). However, the rubric explicitly evaluates *"Microservices Representation: Is the microservice architecture reflected in code boundaries/packages?"* — so we cannot collapse to one undifferentiated codebase either.

### Decision Drivers
- **Time budget.** Single dev, ~42 working hours, demo-ready end state.
- **Boundary-preservation.** The rubric scores boundaries, not deployment count.
- **Future extraction.** We must be able to split out any package into its own service later without a rewrite.
- **Demonstrability.** A reviewer must see microservice thinking in the code layout, not just in the architecture diagrams.

### Options Considered

| Option | Time cost | Rubric fit | Verdict |
|---|---|---|---|
| **3 services + modular monolith inside `core-api`** | ~42 h achievable | Demonstrates boundaries in code; deployment shows ≥3 containers | **Chosen** |
| 14 separate services, one per domain | >80 h | Best rubric fit on paper | Rejected — undeliverable |
| Single FastAPI app, all domains in one package | ~30 h | "Spaghetti / monolith" — rubric No-verdict | Rejected |
| 2 services (`api`, `ai`) | ~38 h | Acceptable but understates boundaries | Marginal — slight underbid |
| 5 services (`identity`, `payment`, `fraud`, `ai`, `ops`) | ~55 h | Stronger but eats agent time | Rejected — risk |

### Decision
**Three deployable services:** `core-api`, `ai-service`, `ingestion-worker`. Within `core-api`, **strict package boundaries** are enforced for `identity/`, `wallet/`, `payment/`, `ledger/`, `fraud/`, `settlement/`, `dispute/`, `merchant/`. Boundary rules:
1. Packages talk to each other **only** through their `service.py` interface — never by importing internals.
2. Each package owns its tables — no cross-schema JOINs in code outside the owning package.
3. Each package has its own Pydantic models — no shared "god models" file.
4. A lint rule (or pre-commit check) flags cross-package imports of `repository.py` or `models.py`.

### Consequences
**Positive:**
- Each package is **extractable to its own service in a day** — the interface already exists.
- The folder structure (Architecture Review §5 + MVP Plan §2.1) makes microservice thinking visible at a glance.
- Database schemas are already separated (`core`, `ledger`, `ai`) — the database-level boundary is real.

**Negative:**
- A determined developer could still bypass the boundary rules; the build doesn't enforce them as strictly as separate services would.
- Some operational benefits (independent scaling, blast radius) are not realised in the POC.

**Mitigations:**
- The pre-commit hook fails on cross-package imports of internals.
- The README's "Extraction roadmap" section names the **two services we'd extract first** (`fraud-service`, `ledger-service`) and what the migration looks like.

### Revisit when
- Any single package's deploy cadence diverges meaningfully from the rest → extract.
- Any package's resource profile (CPU, memory, scale needs) diverges → extract.
- Ledger throughput or fraud scoring latency become bottlenecks → extract those first (matches Architecture Review §5 priorities).

---

## ADR-009: Why hybrid search via tsvector + pgvector (in-database)

**Status:** Accepted

### Context
The capstone rubric requires *"Hybrid search combining keyword and semantic retrieval over transaction records."* Two common patterns exist: (a) run BM25 in a dedicated search engine (OpenSearch, Elasticsearch) plus a vector DB; (b) run both arms inside Postgres using `tsvector` for the keyword side and `pgvector` for the dense side. Given ADR-002 (one datastore), we want to confirm explicitly that the in-database approach is *good enough*, not just *cheaper*.

### Decision Drivers
- **Correctness on our query mix.** Failure-reason queries mix exact strings (`"DECLINE_03"`, `"INSUFFICIENT_FUNDS"`) with paraphrase (`"the card was rejected because..."`). Pure vector misses the first; pure keyword misses the second.
- **Operational simplicity.** One index lifecycle, one set of backups, one query plane.
- **Acceptable recall.** We measure Recall@5 on our ground-truth set (50 labelled queries) and accept anything ≥ 0.85.

### Options Considered

| Option | Verdict |
|---|---|
| **Postgres `tsvector` + `pgvector`, RRF in Python** | One store, one query plane, hybrid is a single transaction | **Chosen** |
| OpenSearch + pgvector | OpenSearch BM25 is best-in-class | Rejected — second store violates ADR-002 |
| Postgres `tsvector` + Milvus | Strong vector at scale | Rejected for MVP — same reason |
| Pure pgvector (no keyword arm) | Simpler | Rejected — fails the "hybrid" rubric line |
| Pure `tsvector` (no embeddings) | Simpler | Rejected — fails the "semantic retrieval" rubric line |
| `paradedb` (BM25 extension for Postgres) | Best of both — Postgres-native BM25 | Considered; rejected for MVP because adding a Postgres extension increases build complexity for marginal gain on 15k rows |

### Decision
**Postgres `tsvector` + `pgvector` with Reciprocal Rank Fusion in the application layer.** Concretely:
1. Two parallel queries: BM25 top-30 (`ts_rank_cd` against `chunk_tsv`) and ANN top-30 (`<=>` against `embedding`).
2. RRF (`k=60`) combines the two ranked lists into a top-50.
3. Cross-encoder reranker (`bge-reranker-base`) re-scores the top-50 to top-5.
4. Recency boost: multiply score by `exp(-age_days / 30)` to favour recent incidents (rubric: "Reranking of historical incidents based on transaction recency").

### Consequences
**Positive:**
- Single query plane; no two-phase fetch.
- The keyword arm catches exact-string matches that the embedder misses (e.g., specific error codes).
- The recency boost is a one-line SQL expression — easy to demo.
- We can compute and display the *contribution* of each arm in the UI (great demo moment).

**Negative:**
- `ts_rank_cd` is not "real" BM25 — it lacks IDF saturation parameters.
- At very large scale (>1M docs), `ts_rank_cd` becomes slow vs. a dedicated engine.

**Mitigations:**
- Measure on our ground truth (`tests/eval/ground_truth.jsonl`); if Recall@5 < 0.85 we add `paradedb` (still inside Postgres) before moving to OpenSearch.
- Document the OpenSearch swap path in this ADR's "Revisit when" section.

### Revisit when
- Recall@5 on the production query log drops below 0.80 → introduce `paradedb` or move keyword arm to OpenSearch.
- Index size on `core.transactions.search_doc` exceeds 5 GB → keyword arm moves out of Postgres.

---

## ADR-010: Why Flan-T5-large as the local fallback model

**Status:** Accepted

### Context
The rubric requires *"Local Fallback: Working code for a local model (Flan-T5/Transformers) if OpenAI times out."* This is non-negotiable. The fallback model must run on the demo laptop, load at startup, and produce usable (not great) answers when the primary OpenAI path is unavailable. The choice of *which* local model matters: too small = useless output, too large = the demo laptop swaps to disk.

### Decision Drivers
- **Footprint.** Must fit comfortably in 8 GB RAM alongside Postgres, Redis, two FastAPI services, and nginx (frontend).
- **Startup time.** Must load within the FastAPI lifespan hook (rubric: cold-start optimisation), ideally < 30s.
- **Instruction-following.** It must follow our prompt structure for failure-explanation, even if shallowly.
- **CPU-runnable.** No GPU on the demo laptop; inference must complete in seconds, not minutes.

### Options Considered

| Option | Size | CPU latency (256 tok) | Verdict |
|---|---|---|---|
| **Flan-T5-large** | ~770M params, ~3 GB | 3–8 s | **Chosen** — sweet spot |
| Flan-T5-base | ~250M, ~1 GB | 1–3 s | Faster but answers are clearly worse on our prompts |
| Flan-T5-xl | ~3B, ~12 GB | 30+ s on CPU | Too slow, won't fit |
| TinyLlama 1.1B | ~1.1B, ~2.5 GB | 8–15 s | Decent but less instruction-tuned than Flan-T5; needs careful prompting |
| Phi-3-mini (3.8B, q4 quantised) | ~2.5 GB | 4–10 s | Strong contender; chosen as a *secondary* fallback if Flan-T5 underperforms |
| Llama-3-8B (quantised) | ~5 GB | 20+ s on CPU | Too slow for the demo box |
| Distilled OpenAI proxy / Ollama | Variable | Variable | Adds another service to operate; rejected |

### Decision
**Flan-T5-large**, loaded via HuggingFace Transformers in the FastAPI lifespan hook. Inference happens on CPU using `torch.compile` (if available) or eager mode. The fallback path is triggered when the OpenAI client's circuit breaker is open OR the request fails after retries.

A degraded-mode banner is **visibly** surfaced in the React UI (the `DegradedModeBanner` component, activated by an `X-LLM-Tier: fallback` header on the SSE stream) when the fallback model produced the response — this is the rubric's "Graceful Degradation" line, made obvious.

### Consequences
**Positive:**
- Recognised by name in the rubric — direct match.
- Loads in ~12 s on a modern laptop, well within lifespan budget.
- Instruction-tuned out of the box; minimal prompt-engineering needed.
- A working demo of "kill the OPENAI_API_KEY → answer still streams from local" is the strongest **single moment** of the live panel.

**Negative:**
- Answer quality is materially lower than GPT-4o-mini — must be acknowledged in the UI.
- Cannot do tool-calling — the fallback path serves *RAG only*, not the multi-agent flow. Agent paths return a static "AI service degraded — please try again" message.
- Adds ~3 GB to the `ai-service` image size.

**Mitigations:**
- Multi-stage Dockerfile downloads model weights in the build stage and caches them in a named volume so subsequent starts are fast.
- The "degraded mode" UI banner sets the right expectation for the panel — degradation is a *known* state with a *defined* contract, not a failure.
- For Tier 1 (agent) paths, we document that graceful degradation = "queue and retry" rather than "fall back to local," because Flan-T5 cannot do tool-calling reliably.

### Revisit when
- Phi-3-mini or a similar small instruct model demonstrates clearly better quality at our latency budget → swap.
- The demo machine gets a GPU → bump to a 7B-class model.
- Tool-calling becomes available in a sub-2B local model → the fallback can cover agent paths too.

---

## ADR-011: Why React + TypeScript + Vite (supersedes ADR-006)

**Status:** Accepted  ·  **Date:** 2026-06-19  ·  **Decider:** Jayachandran  ·  **Mentor consulted:** Siva
**Supersedes:** ADR-006 (Streamlit)

### Context
ADR-006 chose Streamlit on a 3-hour frontend budget. After mid-capstone review, the working assumption that *"the panel scores the AI, not the UI"* was challenged: a Streamlit-shaped interface may unintentionally signal *"this is a prototype"* and dampen the panel's read of an otherwise production-grade architecture. The advanced tier of the rubric talks about *"merchant integrations"* and *"front-end interface for user and merchant interaction"* — both read better against a real SPA. The "revisit when" trigger in ADR-006 listed *"fine-grained UX (drag-and-drop, custom forms)"* — we are activating that trigger early, on the basis of perceived-production-readiness rather than functional necessity.

### Decision Drivers
- **Perceived production-readiness.** A real SPA elevates how the architecture is *read* in 8 minutes.
- **SSE streaming UX.** Token-by-token rendering looks materially better in React than in Streamlit's re-render model.
- **Type safety end-to-end.** TypeScript interfaces mirror Pydantic models — drift caught at compile time.
- **Time tax must stay bounded.** The swap must add ≤ 10 hours; no design-system install, no SSR, no state library beyond TanStack Query.

### Options Considered

The same option set as ADR-006, re-weighted because the budget changed from 3 h to ~12 h.

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **React 18 + TypeScript + Vite** | Fast HMR, excellent ecosystem, native SSE consumption, TS contracts with backend, nginx prod serve | ~12 h build cost; SSE-through-nginx has a sharp edge (buffering) | **Chosen** |
| Streamlit | 3 h build cost; recognisably "POC" | UX ceiling; SSE re-render is choppy | Superseded |
| Next.js | Best DX, SSR + SPA both | SSR is unused; ~16 h cost; routing tax | Rejected — overspec |
| Vue 3 + Vite + Pinia | Similarly capable | Less common in the panel's likely background; less interview-leverage | Rejected on tie-breaker |
| Gradio | Native streaming | Single-page paradigm; multi-page fights the framework | Rejected |
| HTMX + Jinja2 | Server-rendered, simple | Less polished for multi-pane support console | Rejected |

### Decision
**React 18 + TypeScript + Vite + Tailwind + TanStack Query + Recharts**, served via **nginx** in production. Three pages, no SSR, no design-system install, no state library beyond TanStack Query, no frontend test suite (rubric does not require it). Full structure in `MVP_PLAN.md` §5.

### Consequences
**Positive:**
- Token streaming, agent-trace drawer, citation chips, and degraded-mode banner all *visibly* communicate production thinking.
- TypeScript types in `src/api/types.ts` mirror backend Pydantic models — drift surfaces in CI via `tsc --noEmit`.
- nginx prod serve is the same shape a production deployment would use (this is the Azure Front Door → Application Gateway termination model from `ARCHITECTURE_REVIEW.md` §3).

**Negative:**
- Eats ~9 additional hours; Day 5 buffer collapses from ~3.5 h to ~0.5 h.
- nginx **buffering** silently kills SSE if `proxy_buffering off` is omitted from the `/api/ai/` location block. This is a real footgun — see `MVP_PLAN.md` §5.3.
- React bundle size and a build step add operational surface area compared to a single Python file.

**Mitigations:**
- Strict cut order if running behind on Day 5: `UserConsole` becomes a stub; `MerchantDashboard` and `SupportConsole` are demo-critical and non-negotiable.
- nginx SSE test added to the Day 5 morning checklist; not optional.
- No fancy state library, no design-system install — Tailwind utility classes only.

### Revisit when
- Concurrent user count grows enough to justify SSR — move to Next.js.
- The team grows past one developer — introduce a real design-system (shadcn/ui, MUI) at that point, not before.
- WebSocket replaces SSE on the assistant endpoint — `useSSE` becomes `useWebSocket`; nginx config needs `proxy_set_header Upgrade $http_upgrade`.

### Lifecycle note
ADR-006 is preserved with its original reasoning intact. **Append-only ADR discipline:** decisions are not rewritten when they're reversed; the reversal gets its own record (this ADR). A panel reviewing both ADRs sees the engineering thought process, not a sanitised history.

---

## ADR-012: Multi-Model AI Strategy

**Status:** Accepted  ·  **Date:** 2026-06-19  ·  **Decider:** Jayachandran  ·  **Mentor consulted:** Siva
**Refines:** ADR-007 (Why OpenAI as primary LLM) — pins specific model versions and adds the embedding-model tier explicitly
**Related:** ADR-010 (Flan-T5 local fallback, unchanged); `ARCHITECTURE_REVIEW.md` §10 ADR-003 (production embedding model — `bge-base-en-v1.5` retained as production fallback path)

### Context
ADR-007 fixed the LLM provider (OpenAI) and the *shape* of model tiering (heavy reasoning / low-latency / local fallback) but stopped short of pinning specific model versions, and did not address the embedding model choice explicitly. With the OpenAI ecosystem now coherent across both generation and embedding, we want a *single* model-strategy record that names exact versions for the MVP, defines the production upgrade path, and makes the three-way trade-off (accuracy / cost / latency) explicit and defensible.

### Decision Drivers
- **Accuracy** — must clear the DeepEval thresholds defined in `tests/eval/` and Recall@5 ≥ 0.85 on the ground-truth set.
- **Cost** — bounded $ per session; tier router caps spend per agent run.
- **Latency** — P95 < 4 s for RAG, P95 < 8 s for multi-agent.
- **Single-provider coherence** — one SDK surface, one rate-limit pool, one billing line.
- **Reversibility** — embedding-dimension changes are invasive (schema + index rebuild); we lock the MVP dim now and document the upgrade path.

### Considered Options

**Generation models**

| Model | Strength | Weakness | Verdict |
|---|---|---|---|
| **GPT-5.4** | Best reasoning; structured outputs; mature tool-calling | Highest cost per token; higher latency | **Chosen — Tier 1** |
| **GPT-5.4-mini** | ~10× cheaper than 5.4; sub-second on short prompts | Materially weaker on multi-step reasoning | **Chosen — Tier 2** |
| GPT-4o / 4o-mini | Familiar; well-understood limits | Superseded by the 5.4 family on the same SDK | Replaced |
| Anthropic Claude (Sonnet / Haiku) | Equally defensible technical merit | Adds a second provider surface | Rejected — single-provider coherence |
| Flan-T5-large (local) | Free, offline, no rate limits | Substantially weaker on instruction following | **Retained — Tier 3** (per ADR-010, unchanged) |

**Embedding models**

| Model | Dim | Relative cost | Recall@5 (our eval set) | Verdict |
|---|---|---|---|---|
| **text-embedding-3-small** | 1536 | 1× | Clears 0.85 target | **Chosen — MVP (E1)** |
| **text-embedding-3-large** | 3072 | ~6.5× | ~5% better top-5 retrieval | **Chosen — production target (E2)** |
| bge-base-en-v1.5 (self-hosted) | 768 | $0 API / GPU $ infra | Competitive on short text | Retained as **production fallback** if residency or cost forces the issue |
| OpenAI ada-002 (legacy) | 1536 | Similar to -3-small | Inferior to -3-small | Rejected — superseded |

### Decision

| Tier | Purpose | Model |
|---|---|---|
| **T1 — Reasoning** | Orchestrator planning, dispute resolution, deep fraud investigation, multi-step agent loops | **GPT-5.4** |
| **T2 — Low-latency / high-volume** | Failure-explanation Q&A, LLM-as-judge (online), citation summarisation, intent classification | **GPT-5.4-mini** |
| **T3 — Local fallback** | Degraded-mode RAG when T1/T2 are unavailable (unchanged from ADR-010) | **Flan-T5-large** |
| **E1 — Embeddings, MVP** | All ingestion and query embedding for hybrid retrieval | **text-embedding-3-small** (1536 d) |
| **E2 — Embeddings, production target** | Re-index when accuracy headroom matters more than ingestion cost | **text-embedding-3-large** (3072 d) |

The tier router in `services/ai-service/app/llm/router.py` classifies each task by handler-declared `task_class` and dispatches to T1 / T2 / T3 accordingly. The embedder in `services/ai-service/app/retrieval/embedder.py` is dimension-agnostic — only the DB schema knows the dimension.

### Trade-off triangle — explicit

| Choice | Accuracy | Cost | Latency | Rationale for the pick |
|---|---|---|---|---|
| GPT-5.4 (T1) | ★★★★★ | ★★ | ★★ | Used sparingly — only when reasoning quality dominates |
| GPT-5.4-mini (T2) | ★★★ | ★★★★★ | ★★★★★ | Default carrier — 90%+ of traffic, well inside latency budget |
| Flan-T5-large (T3) | ★★ | ★★★★★ (free) | ★★★ (CPU-bound, ~3–8 s) | Survives a provider outage; demonstrates the "Graceful Degradation" rubric line |
| text-embedding-3-small (E1) | ★★★★ | ★★★★★ | ★★★★★ | Best $/recall for MVP scale; clears target with headroom |
| text-embedding-3-large (E2) | ★★★★★ | ★★ | ★★★ | Promote when accuracy headroom dominates cost — not before |

The triangle is read **per row**: every choice is a deliberate point on the accuracy / cost / latency surface, not a "best on everything" pick. The system is the *combination* — T1 + T2 + T3 + E1 — not any single row.

### Consequences

**Positive:**
- Explicit model pinning closes a hole in ADR-007: panel reviewers asking *"but which exact models?"* now get a single table as the answer.
- The per-session token-budget cap in the tier router enforces the cost ceiling automatically; an agent that would exceed budget halts and returns "needs human" rather than overspend.
- E1 clears Recall@5 ≥ 0.85 without paying ~6.5× for the marginal accuracy that E2 delivers. We pay for accuracy *when* it is worth paying.
- Single-provider coherence simplifies billing, monitoring, and rate-limit observation — one dashboard, not three.

**Negative:**
- **Embedding-dimension impact:** earlier schema notes assumed `vector(768)` (bge-base). E1 requires **`vector(1536)`**. The `ai.embeddings.embedding` column type and the HNSW index in `infra/postgres/init.sql` need updating. One-line schema change + one index rebuild; ~30 minutes of developer time on Day 1.
- **Single-vendor risk concentrates** — if OpenAI rate-limits *both* generation and embedding pipelines, degradation hits two places at once. Generation has T3 (Flan-T5); embeddings have no MVP-time fallback (bge-base is documented as the production fallback path, not wired in for the MVP).
- **Re-indexing cost on E1 → E2 promotion:** 1536 d → 3072 d is a full re-embed, not an incremental migration.

**Mitigations:**
- Schema fix folds into the existing Day 1 ingestion-worker scaffolding — no new task.
- `EmbeddingClient` abstraction is dimension-agnostic; the dim is a single config value the indexer reads at startup.
- Treat the E1 → E2 promotion as a *"background re-embed"* runbook entry, not a hot upgrade.

### Revisit when
- DeepEval `faithfulness` or `context_precision` falls below threshold **and** root-cause points at embedding quality → promote E1 → E2.
- OpenAI generation cost exceeds the per-session budget by 2× on a rolling 7-day average → route selected T2 traffic to a self-hosted Llama-class model at the `LLMClient` layer (does not invalidate this ADR — it adds a T2b lane).
- Data residency becomes a hard requirement → swap routing to Azure OpenAI for the same model family (no model change, just endpoint).
- OpenAI releases a successor with materially better cost/latency at GPT-5.4-equivalent accuracy → revisit Tier 1/2 picks; record as **ADR-013** to preserve the decision lineage. **Do not edit this ADR in place.**

### Relationship to other ADRs
- **ADR-007** (Why OpenAI) — foundational. This ADR pins versions and adds the embedding tier; ADR-007's reasoning about *provider* choice is unchanged and remains in force.
- **ADR-010** (Flan-T5 local fallback) — unchanged. T3 retains its role for generation-side degradation.
- **`ARCHITECTURE_REVIEW.md` §10 ADR-003** (production embedding model) — that record favours `bge-base-en-v1.5` for self-hostability and residency at scale. This MVP ADR refines for OpenAI-ecosystem coherence and **retains `bge-base` as the named production fallback** if residency or cost forces the issue. Both ADRs co-exist without contradiction.

---

## Closing notes

**What this document is for the panel.**
The capstone rubric flags *"Ad-hoc technology choice without reasoning"* as a No-verdict and *"ADRs included … clear justification of cost vs. latency vs. complexity"* as the Yes-verdict. Every ADR above names alternatives, lists negatives, and gives a concrete "revisit when" trigger. None of the choices were made because something was "popular" — each is defended on its decision drivers.

**What to read in tandem.**
- `ARCHITECTURE_REVIEW.md` §10 — production-architecture ADRs (Milvus, Kafka, CockroachDB, hybrid retrieval, ledger isolation, agent framework, tokenisation).
- `MVP_PLAN.md` §0 — the cut list that follows from these ADRs.
- `docs/evaluation/RESULTS.md` — the empirical results that validate (or challenge) ADRs 002, 009, and 010.

**Decisions deliberately deferred** (not made here, made by the implementing developer on Day N):
- Exact embedding-table chunk template for KB articles vs. transactions (Day 1).
- Whether to expose `/v1/agents/invoke` or split into one endpoint per agent (Day 4 — likely the latter for clearer demos).
- Tailwind theme palette and component-level polish (Day 5).

These are intentionally **not** ADR-worthy — they're tactical, reversible in <1 hour, and don't constrain other parts of the system.
