# Demo Script — 5-Minute Live Walkthrough
## AI-Powered Payment Gateway Platform

**Author:** Jayachandran
**Reviewer:** Siva
**Target runtime:** 5 minutes core demo + 2 minutes Q&A
**Format:** Presenter narration in *italics*; actions in plain text; commands in code blocks

---

## Pre-Demo Checklist (T-5 minutes)

Run every check. Do not skip any. A failing check at demo time is recoverable; a surprise failure mid-demo is not.

```bash
# 1. Confirm all 5 containers are up and healthy
make ps
# Expected: 5 rows, all STATUS "Up", all health checks "(healthy)"

# 2. Hit health endpoints
make health
# Expected: {"status":"ok"} × 2, "HTTP 200" for frontend

# 3. Confirm transaction data is present
curl -s "http://localhost:8000/v1/payments?limit=1" | python3 -m json.tool | head -15
# Expected: {"items": [{...}], "count": 1, "next_cursor": "..."}

# 4. Confirm Azure OpenAI is live (LLM explanations enabled)
curl -s http://localhost:8100/readyz | python3 -m json.tool
# Expected: {"status":"ready", "checks": {"azure_openai": "ok", ...}}
# NOTE: If azure_openai is "not_configured" — mention this during the demo as
#       designed-in graceful degradation. Template explanations still work.

# 5. Warm the fraud endpoint (avoids cold-start latency on first call)
curl -s -X POST http://localhost:8100/v1/fraud/score \
  -H "Content-Type: application/json" \
  -d '{"transaction_id":"00000000-0000-0000-0000-000000000099","user_id":"00000000-0000-0000-0000-000000000099","merchant_id":"m_acme","amount":"100","currency":"INR","payment_method":"upi","metadata":{}}' \
  > /dev/null
echo "Warmup done"
```

**Browser setup:**
- Tab 1: `http://localhost:3000` (frontend — primary demo screen)
- Tab 2: `http://localhost:8000/docs` (API playground — for architecture callouts)
- Tab 3: Terminal (for curl demonstrations)

---

## Segment 1 — System Architecture Overview (0:00 – 0:45)

*"Before I show the UI, let me give you a 30-second orientation of what's running."*

```bash
# In terminal — show the stack is live
make ps
```

*"Five containers. PostgreSQL 16 with pgvector for both the payment database and vector embeddings. Redis 7 for caching and idempotency. A FastAPI core-api at port 8000 handling all payment operations — creating payments, managing wallets, the double-entry ledger. A FastAPI ai-service at port 8100 handling fraud scoring and the knowledge assistant. And a React TypeScript frontend served by nginx at port 3000, acting as a reverse proxy to both backends."*

*"The production architecture — documented in ARCHITECTURE_REVIEW.md — is an 8-layer system targeting 14 microservices on Kubernetes, with active-active deployment across Azure Central India and South India. What we've built is the modular monolith form of that design: same package boundaries, same domain model, same API contracts — but extracted into two services instead of fourteen. Every architectural decision is in DECISIONS.md as a formal ADR."*

*"One command starts everything:"*

```bash
make help  # show available targets
```

---

## Segment 2 — Transaction Monitor (0:45 – 1:45)

Switch to Tab 1 — `http://localhost:3000`.

*"The dashboard is live against the real database. We seeded 10,045 synthetic transactions across 501 users — log-normal amount distribution, realistic merchant mix, 8 countries."*

**Point to the four summary cards:**
*"These four metrics pull from the transaction list we already fetched — no separate aggregation query. 8,015 successful payments, 982 failures, 711 flagged for fraud review, 337 reversed. Live numbers."*

**Point to the table rows:**
*"Notice the left border of each row — this is the risk spine. Cyan for cleared transactions, amber for flagged or medium fraud score, red for failed or automatically rejected. A fraud analyst scanning 100 rows can identify risk concentrations in seconds without reading individual cells. It's a design choice that encodes information in the structure, not the content."*

**Demonstrate filter:**
Click the status dropdown → select `flagged`.
*"Filtering instantly. The table uses keyset pagination — cursor-based, not offset-based — which means performance stays constant whether you're on page 1 or page 100,000."*

Click `flagged` → reset to `All statuses`.

**Select a transaction:**
Click any row in the table.
*"Clicking a row opens the transaction detail drawer."*

---

## Segment 3 — Transaction Detail and Fraud Scoring (1:45 – 3:30)

**With the detail panel open, point to the fields:**

*"The drawer shows the complete transaction record. ID, amount formatted correctly for the currency, status badge, payment method, merchant, settlement status. Amounts are stored as NUMERIC(18,4) in PostgreSQL and transmitted as decimal strings — never as floats. JavaScript's IEEE 754 doubles can't represent 0.1 accurately; we avoid that entire class of bug."*

*"Settlement status is tracked separately from transaction status — a payment can be status:success and settlement:disputed, which is the chargeback state."*

**Point to the Fraud Score panel at the bottom of the drawer:**

*"This panel is connected to the AI service. Right now it's idle. I'm going to call `POST /v1/fraud/score` on the ai-service — a real network call — watch the loading state."*

**Click "Score now":**

While waiting (1–3 seconds):
*"The loading skeleton tells us the call is in-flight. I want to draw attention to something: the fraud decision is already computed in under 1 millisecond by deterministic rules. The 1–3 seconds you're seeing now is the Azure OpenAI round-trip for the natural-language explanation. If OpenAI were unavailable, the rule result would still come back in under 5 milliseconds — the scoring SLA never depends on LLM availability."*

**When result appears, walk through each element:**

1. **Risk band and score meter:**
*"The risk band — LOW RISK, MEDIUM RISK, or HIGH RISK — appears above the animated score bar. The bar sweeps in from zero. This transaction scored [N]/100, which puts it in the [allow/review/reject] tier."*

2. **Decision badge:**
*"The allow/review/reject decision has fixed thresholds: below 40 is allow, 40–74 is review, 75 and above is reject. These thresholds are defined as constants in one place in the codebase — changing them recalibrates the entire pipeline."*

3. **LLM explanation:**
*"This one-sentence explanation was generated by Azure OpenAI with temperature 0.2 — low temperature for factual consistency, no hedging language. The llm_used flag in the API response tells callers whether this is an LLM explanation or the deterministic template fallback."*

4. **Rule hit cards:**
*"Each fired rule has a category pill — amount rules in sky-blue, velocity in amber, geographic in indigo, method in purple, merchant in rose, behaviour in green. The +N weight is the score contribution. Click the triangle to expand the evidence."*

Click the expand toggle on one rule:
*"The evidence dictionary shows the raw values: the actual amount and the threshold it was compared against. This is the audit trail — a fraud analyst can verify exactly why this rule fired."*

**Now demonstrate a high-risk transaction via the terminal:**

Switch to Tab 3.

```bash
curl -s -X POST http://localhost:8100/v1/fraud/score \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_id": "00000000-0000-0000-0000-000000000001",
    "user_id":        "00000000-0000-0000-0000-000000000002",
    "merchant_id":    "m_gambling_xyz",
    "amount":         "500000.00",
    "currency":       "INR",
    "payment_method": "bank_transfer",
    "metadata": {
      "prior_failures":    5,
      "country_receiver":  "KP",
      "is_new_device":     true,
      "hour_of_day":       3,
      "account_age_days":  2
    }
  }' | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'Score:    {d[\"risk_score\"]}/100')
print(f'Decision: {d[\"decision\"].upper()}')
print(f'Rules:    {len(d[\"rule_hits\"])} fired')
print(f'LLM:      {d[\"llm_used\"]}')
print()
for h in d['rule_hits']:
    print(f'  +{h[\"weight\"]:2d}  [{h[\"category\"]:10s}]  {h[\"rule_id\"]}')"
```

Expected output:
```
Score:    100/100
Decision: REJECT
Rules:    6 fired

  +35  [amount    ]  AMOUNT_LARGE_INR
  +30  [merchant  ]  MERCHANT_HIGH_RISK_CAT
  +30  [geo       ]  GEO_HIGH_RISK_COUNTRY
  +30  [behaviour ]  BEHAVIOUR_NEW_DEVICE
  +25  [velocity  ]  VELOCITY_NEW_ACCOUNT
  +20  [method    ]  METHOD_BANK_LARGE
```

*"Score 100, automatic reject. Six rules fired independently: ₹5 lakh large amount, gambling merchant, North Korea as destination, new device, 2-day-old account making a large transaction, large bank transfer. Rules are independent — each fires based solely on the request fields. Score is capped at 100."*

---

## Segment 4 — RAG Policy Assistant (3:30 – 4:30)

Switch to Tab 1. Click **Policy Assistant** in the sidebar.

*"The second major AI feature is the policy knowledge assistant. Operational support staff need fast answers about refunds, chargebacks, fraud thresholds, settlement cycles, payment errors. Instead of asking an engineer, they query the knowledge base."*

**Click the suggested query chip: "How long does a UPI refund take?"**

While waiting for results:
*"I'm calling `POST /v1/rag/query` on the ai-service. The query is embedded using text-embedding-3-small — 1,536-dimensional vectors — and retrieved via HNSW approximate nearest-neighbour search in pgvector. The same vector index that stores transaction embeddings in a production system."*

**Point to results:**

1. **Search mode badge:**
*"The badge at the top says 'vector' — embeddings are live. If Azure OpenAI were unavailable, it would say 'keyword' and use Dice-coefficient scoring instead. Both modes are transparent to the caller via the search_mode and embedding_used fields."*

2. **Chunk cards:**
*"Each retrieved chunk shows its source document, section title, full content, and a relevance score bar. The scores are cosine similarities in [0, 1] — genuinely meaningful because we fixed a bug during development where the wrong operator (<#> instead of <=>) was producing scores above 1.0 and clamping everything to 1.0. We caught it because we tested the score range systematically."*

**Type in the input field:** `"What evidence does a merchant need to submit to dispute a chargeback?"`

Press Enter.

*"Different document, different category — chargeback policy. The category filter lets support agents scope queries. We have 48 chunks across five documents: refund, chargeback, fraud, settlement, payment failure."*

---

## Segment 5 — Architecture Callouts (4:30 – 5:00)

Switch to Tab 2 — `http://localhost:8000/docs`.

*"A few quick architectural points:"*

**Double-entry ledger:**
*"Every successful payment creates exactly two ledger entries — DEBIT on the sender wallet, CREDIT on the merchant's account. The invariant sum(DEBIT) == sum(CREDIT) per transaction is enforced at the service layer. Post-seed verification: zero imbalanced transactions across 10,045 records."*

**Idempotency:**
*"The payment API uses a two-key Redis strategy — a 60-second lock key for in-flight protection, and a 24-hour response cache for replay. The PostgreSQL UNIQUE(merchant_id, idempotency_key) constraint is the durable safety net. Re-running the seeder with the same random seed produces exactly zero new inserts."*

**Graceful degradation:**
*"Every AI feature degrades gracefully. No OpenAI key: keyword search for RAG, template explanation for fraud scoring. The system is always functional. The llm_used flag and search_mode field in every AI response make the operating mode transparent — no silent degradation."*

---

## Q&A Preparation

**Q: How would you add authentication?**

JWT middleware (`Depends(require_scope("payment:write"))`) at every route. The scopes are defined in the API contract: `payment:read`, `payment:write`, `refund:write`, `wallet:read`. The route handlers accept `Depends(get_current_user)` as the injection point. The `auth.py` middleware stub exists in the codebase. It's a one-sprint task — the hard part is choosing the identity provider, not the code.

**Q: What's the throughput ceiling?**

The single-Postgres, single-Redis setup is the ceiling. `POST /v1/payments` makes 4 sequential DB round-trips. With `pool_size=10`, expect ~300–500 TPS before Postgres becomes the bottleneck. The production architecture adds read replicas, a separate ledger cluster, and moves to connection pooling via PgBouncer. The code makes zero assumptions about a single Postgres instance — the session factory URL is the only configuration change.

**Q: Why pgvector instead of Milvus or Pinecone?**

48 chunks. A dedicated vector database for 48 rows is operational complexity with zero benefit. The HNSW index query returns in under 3ms at this scale. The ADR-009 "revisit when" trigger is 1M+ embeddings or P95 > 50ms. When that trigger fires, the `RAGService._vector_search` method is the single abstraction point to swap.

**Q: What happens if Azure OpenAI goes down during a payment flow?**

Fraud scoring: the rule engine completes in ~1ms before the LLM is even called. The LLM explanation has a 3-second timeout with automatic template fallback. `llm_used: false` in the response. Payment processing is never blocked.

RAG: keyword fallback activates automatically. 80% top-1 accuracy on keyword vs 100% on vector — the degradation is acceptable for a secondary feature.

**Q: How does the ledger prevent double-charges?**

Three-layer defence:
1. Route layer: idempotency key pre-check returns cached response before calling the service
2. DB layer: `UNIQUE(merchant_id, idempotency_key)` — concurrent INSERT with same key raises `IntegrityError`, caught as 409
3. Wallet layer: optimistic concurrency (`UPDATE ... WHERE version = :expected`) — exactly one concurrent update wins; the loser retries or surfaces `ContentionExceededError`

**Q: What's in the DECISIONS.md you mentioned?**

12 Architecture Decision Records in MADR-lite format. Each ADR records: the context (what problem are we solving), the decision (what we chose), the alternatives considered, the consequences (tradeoffs accepted), and a "revisit when" trigger. Examples: ADR-008 explains why we chose a modular monolith instead of microservices for MVP (the trigger is "domain team boundaries emerge"); ADR-002 explains why we kept vectors in the main Postgres instead of a separate store (the trigger is 1M embeddings).

---

## Recovery Playbook

If something fails mid-demo:

| Failure | Recovery |
|---|---|
| Frontend not loading | Navigate to `http://localhost:8000/docs` — demonstrate API directly from Swagger UI |
| `POST /v1/fraud/score` returns 503 | *"The AI service is temporarily unavailable — this is expected in a single-node setup. The rule engine is always available; this 503 is from the FastAPI route itself."* Show the curl command directly |
| Azure OpenAI timeout | *"Template fallback — designed-in degradation."* Point to `llm_used: false` in response |
| Slow RAG query | *"Embedding API latency — this is the external network call. Keyword fallback runs in 5ms."* Change to keyword mode in UI |
| DB connection issues | `make restart` — containers reconnect in ~15 seconds. Use `make health` to verify |
| Transaction list empty | `python scripts/seed_demo_data.py --dry-run` to check DB; if empty, `python scripts/seed_demo_data.py --quiet` |

---

## Timing Reference

| Segment | Clock | Key action |
|---|---|---|
| Pre-flight | T-5 min | `make ps`, `make health`, warmup curl |
| Architecture overview | 0:00 – 0:45 | `make help`, topology orientation |
| Transaction monitor | 0:45 – 1:45 | Risk spine, filter, select transaction |
| Fraud scoring | 1:45 – 3:30 | "Score now", walk result, high-risk curl |
| RAG assistant | 3:30 – 4:30 | Suggested chip, second free-form query |
| Architecture callouts | 4:30 – 5:00 | Ledger invariant, idempotency, graceful degradation |
| Q&A | 5:00+ | Prepared answers above |

*If any segment runs long, cut the terminal curl in Segment 3 and go directly to RAG.*

---

*If any component is down and unrecoverable during the demo, the API documentation at `http://localhost:8000/docs` and `http://localhost:8100/docs` demonstrates the complete API contracts directly. The OpenAPI schemas are generated live from the running code and show all request/response models.*
