# Locust Performance Test Specification
## AI-Powered Payment Gateway Platform

**Author:** Jayachandran
**Date:** June 2026
**Status:** Specification — ready to execute against the running system
**Companion documents:** `EVALUATION_RESULTS.md` · `PRODUCTION_READINESS.md`

---

## Overview

The baseline performance measurements in EVALUATION_RESULTS.md §3 were taken under sequential single-request load (50 curl calls, no concurrency). Locust load testing validates three separate questions that sequential measurements cannot answer:

1. **Does latency hold** under concurrent users — does P99 stay within production SLO targets when 50+ virtual users drive simultaneous requests?
2. **Where is the ceiling** — at what request rate does the system start dropping requests or exceeding SLO thresholds?
3. **Which bottleneck breaks first** — Postgres connection pool, uvicorn worker concurrency, Redis, or the Azure OpenAI rate limit?

This document specifies the test scenarios, payload construction, expected behaviour, acceptance criteria, and instructions for running the tests against the live Podman Compose stack.

### System under test

| Service | Host | Port | Key constraints |
|---|---|---|---|
| core-api | `localhost` | `8000` | Single uvicorn process, asyncpg pool_size=10 |
| ai-service | `localhost` | `8100` | Single uvicorn process, Azure OpenAI 3s timeout |
| Postgres | `pg-postgres` (internal) | `5432` | Single node, no replication |
| Redis | `pg-redis` (internal) | `6379` | Single node, AOF persistence |

**Important constraints that shape the test design:**
- `pool_size=10`: Postgres will reject connections beyond 10 concurrent active queries. Test ramp-up must not exceed the pool before the system is ready.
- Single uvicorn worker: the `--no-access-log` flag is set but no `--workers` argument — each service runs one event loop. Concurrent request handling relies on async I/O, not parallelism.
- Azure OpenAI rate limit: `text-embedding-3-small` has a token-per-minute limit. Tests involving the RAG endpoint with embeddings should use a reduced user count to avoid exhausting the quota and making the fallback path the default.

---

## Part 1 — Test Scenarios

### Scenario A — Payment Read Load (Baseline latency under concurrency)

**Goal:** Verify that `GET /v1/payments` latency holds under concurrent read load from realistic merchant filtering.

**Traffic shape:** 50 virtual users, constant load for 5 minutes after 60-second ramp.

**User behaviour:**
1. Fetch the first page of payments for a randomly chosen merchant (from the 8 seeded merchants).
2. With 40% probability, fetch the next page using the returned `next_cursor`.
3. With 20% probability, fetch a specific transaction by ID (choose a random UUID from the first page).
4. Wait 1–3 seconds (think time).
5. Repeat.

**Expected endpoint mix:**
- `GET /v1/payments?merchant_id=<m>&limit=20` — ~65% of requests
- `GET /v1/payments?merchant_id=<m>&limit=20&cursor=<c>` — ~25% of requests
- `GET /v1/payments/{id}` — ~10% of requests

**SLO targets for pass:**

| Endpoint | P50 target | P99 target | Error rate |
|---|---|---|---|
| `GET /v1/payments` | ≤ 30ms | ≤ 120ms | < 0.1% |
| `GET /v1/payments?cursor=…` | ≤ 30ms | ≤ 120ms | < 0.1% |
| `GET /v1/payments/{id}` | ≤ 20ms | ≤ 80ms | < 0.1% |

**What to watch for:**
- If P99 climbs above 200ms, it indicates connection pool saturation (`pool_size=10` exhausted under 50 concurrent readers). Check `pg_stat_activity` for `wait_event_type = Client`.
- If the error rate exceeds 0.1%, check for asyncpg `TooManyConnections` in the core-api logs.

---

### Scenario B — Payment Creation Throughput (Write load with correctness verification)

**Goal:** Determine the maximum sustained write rate the system can handle while maintaining the double-entry ledger invariant and idempotency guarantees.

**Traffic shape:** ramp from 1 to 30 virtual users over 3 minutes, then hold at 30 for 5 minutes.

**User behaviour:**
1. Generate a unique `Idempotency-Key` (UUID4 at request time).
2. Select a random user from the 501 seeded users (`seed_user_{0..500}`).
3. Select a random merchant from the 8 seeded merchants.
4. Generate a random INR amount between ₹100 and ₹50,000.
5. Send `POST /v1/payments`.
6. Verify the response: status code must be 201, `status` field must be `success` or `failed`, `transaction_id` must be a valid UUID.
7. Wait 0.5–2 seconds.
8. Repeat.

**Why this range of amounts:** values up to ₹50,000 avoid triggering `AMOUNT_LARGE_INR` (threshold: ₹1,00,000), keeping most transactions in the `allow` decision path and preventing the fraud scoring latency from dominating the payment creation time.

**SLO targets for pass:**

| Metric | Target | Notes |
|---|---|---|
| P50 latency | ≤ 60ms | Budget: validate (2ms) + idempotency (3ms) + wallet read (6ms) + insert (8ms) + debit (7ms) + ledger (8ms) = ~34ms plus overhead |
| P99 latency | ≤ 250ms | Accounts for connection pool queueing at 30 concurrent writers |
| Throughput | ≥ 15 RPS sustained | Minimum for a useful MVP demonstration at 30 users |
| Error rate | < 0.5% | Some `OptimisticLockError` retries are expected and handled internally |
| 5xx rate | < 0.1% | Any 5xx is a correctness failure |
| Idempotency replay rate | 0% | Each request has a fresh UUID4 key; replays should not occur |

**Post-test correctness check (must pass):**
```sql
-- Run after Scenario B completes
-- All new transactions must have a balanced ledger
SELECT COUNT(*) FROM core.transactions t
WHERE t.created_at > NOW() - INTERVAL '30 minutes'
  AND t.status != 'failed'
  AND NOT EXISTS (
    SELECT 1 FROM ledger.entries e
    WHERE e.transaction_id = t.transaction_id
  );
-- Expected: 0 rows
```

**What to watch for:**
- If throughput plateaus below 15 RPS at 30 users, the Postgres pool is the bottleneck. Run `SHOW max_connections;` and compare against `pool_size=10`.
- If `OptimisticLockError` frequency is high in logs, two users chose the same wallet simultaneously. Expected at 30 concurrent writers; the service retries up to 3 times.
- If P99 exceeds 500ms, the 4-sequential-round-trip structure is queuing behind the pool. Reduce concurrency or increase `pool_size`.

---

### Scenario C — Idempotency Correctness Under Concurrency

**Goal:** Prove that concurrent duplicate requests with the same `Idempotency-Key` produce exactly one transaction, not two.

**Traffic shape:** 10 virtual users, each sending the same 5 pre-generated idempotency keys. Each user sends the same request 10 times in quick succession with 50ms delay between sends.

**User behaviour:**
1. At test start, generate 5 idempotency keys (UUIDs) shared across all 10 users.
2. For each key: send `POST /v1/payments` 10 times over 5 seconds.
3. Collect all responses. Validate:
   - All 100 responses per key have the same `transaction_id`.
   - Exactly 1 response has `status_code=201`; the remaining 99 have `status_code=200` with `Idempotent-Replay: true` header, OR all 100 have `status_code=201` with the same `transaction_id` (if the service returns 201 consistently for replay — both are acceptable).
   - Zero `IntegrityError` or `500` responses.

**SLO targets for pass:**

| Check | Target |
|---|---|
| Unique `transaction_id` per idempotency key | Exactly 1 per key (verified in DB post-test) |
| No 5xx responses | 0% |
| No duplicate ledger entries | SQL check: `SELECT COUNT(*) FROM ledger.entries GROUP BY transaction_id HAVING COUNT(*) != 2` returns 0 rows for success-status transactions |

**Post-test SQL verification:**
```sql
-- Verify idempotency correctness
SELECT idempotency_key, COUNT(*) AS txn_count
FROM core.transactions
WHERE idempotency_key IN (
  -- paste the 5 test keys here
  'key-uuid-1', 'key-uuid-2', 'key-uuid-3', 'key-uuid-4', 'key-uuid-5'
)
GROUP BY idempotency_key;
-- Expected: 5 rows, each with txn_count = 1
```

---

### Scenario D — Fraud Scoring Load (Rules-only path)

**Goal:** Validate the rules-only path (`llm_used: false`) at high concurrency. This is the critical SLO: fraud scoring must complete in under 10ms at P99 regardless of load.

**Traffic shape:** 100 virtual users, constant load for 3 minutes. Azure OpenAI is **disabled** for this test (set `AZURE_OPENAI_API_KEY=` to force the rule-only path and deterministic template explanations).

**User behaviour:**
1. Choose a random fraud scenario from the 10 defined in EVALUATION_RESULTS.md §4.3.
2. Send `POST /v1/fraud/score` with the appropriate payload.
3. Verify: `llm_used` is `false`, `decision` matches expected value for the scenario, `risk_score` is within expected range.
4. Wait 100ms.
5. Repeat.

**Why 100 users:** the rule engine is synchronous and CPU-bound within a single async event loop. With 100 concurrent requests and 1ms per request, the event loop should handle ~100 RPS. Testing this validates that the async wrapper does not introduce unexpected latency.

**SLO targets for pass:**

| Metric | Target |
|---|---|
| P50 latency | ≤ 5ms |
| P99 latency | ≤ 15ms |
| Throughput | ≥ 80 RPS |
| Error rate | 0% |
| `llm_used` = `false` in all responses | 100% |
| Decision correctness | ≥ 99% (see note) |

*Decision correctness note:* the 10 test scenarios have known expected decisions. The Locust task verifies the decision after each response. A scenario with `expected_decision=reject` that returns `review` counts as a correctness failure.

**What to watch for:**
- If P99 exceeds 50ms at 100 users, the uvicorn event loop is likely blocked by synchronous code. Profile with `py-spy` to identify the hot path.
- If throughput is below 80 RPS, check CPU usage. The rule engine does `Decimal` arithmetic which is slower than float; this is intentional but should not be the bottleneck at this scale.

---

### Scenario E — RAG Query Load (Keyword fallback path)

**Goal:** Validate keyword search performance at concurrency. Azure OpenAI is **disabled** for this test.

**Traffic shape:** 50 virtual users, constant load for 3 minutes.

**User behaviour:**
1. Choose a random query from the 30 ground-truth questions in LLM_AS_JUDGE_EVALUATION.md §2.4.
2. Send `POST /v1/rag/query` with `top_k=5`.
3. Verify: `search_mode` is `"keyword"`, `embedding_used` is `false`, at least 1 chunk returned.
4. Wait 200ms.
5. Repeat.

**SLO targets for pass:**

| Metric | Target |
|---|---|
| P50 latency | ≤ 20ms |
| P99 latency | ≤ 50ms |
| Error rate | 0% |
| `search_mode=keyword` in all responses | 100% |
| `chunks` non-empty in all responses | ≥ 95% |

---

### Scenario F — Mixed Realistic Load (Combined workload)

**Goal:** Simulate production traffic where multiple operations run concurrently. Tests inter-service interactions and whether the payment write path is degraded by concurrent fraud scoring or RAG queries.

**Traffic shape:** 60 total virtual users, ramped over 2 minutes:
- 30 payment users (Scenario A + B mix)
- 20 fraud scoring users (Scenario D)
- 10 RAG query users (Scenario E)

**User behaviour:** each user class runs its respective scenario independently. No coordination between classes.

**SLO targets for pass:** same as the individual scenarios. The key assertion is that fraud scoring latency does not degrade when payment writes are concurrent — the two services share Postgres but through separate asyncpg pools.

**What to watch for:**
- `pg_stat_activity` `wait_event_type = Lock` events: indicates write-write contention on wallet rows.
- `ai-service` latency increasing when `core-api` is under write load: indicates shared Postgres resource contention. If this occurs, it validates the ADR-002 design decision to eventually isolate the AI schema to its own Postgres instance.

---

## Part 2 — Locust Configuration

### 2.1 Installation

```bash
pip install locust==2.32.0

# Verify
locust --version
# Expected: locust 2.32.0
```

### 2.2 Shared User Fixture

The following data is pre-computed from the seeded database. User IDs and wallet IDs are deterministic (`uuid5(SEED_NS, "user", str(index))`).

```python
# locustfile_shared.py
import uuid, random
from decimal import Decimal

# Seed namespace — matches scripts/seed_demo_data.py
_SEED_NS = uuid.UUID("a1b2c3d4-e5f6-4789-abcd-ef0123456789")

MERCHANTS = [
    "m_swiggy", "m_zomato", "m_amazon", "m_flipkart",
    "m_myntra", "m_bigbasket", "m_bookmyshow", "m_phonepe",
]

def seed_user_id(index: int) -> str:
    return str(uuid.uuid5(_SEED_NS, f"user:{index}"))

def random_user_id() -> str:
    return seed_user_id(random.randint(0, 500))

def random_merchant() -> str:
    return random.choice(MERCHANTS)

def random_inr_amount() -> str:
    """Log-normal distribution, capped at ₹50,000 to avoid fraud triggers."""
    import math
    raw = math.exp(random.gauss(6.0, 1.0))
    amount = min(max(100.0, raw), 50000.0)
    return f"{amount:.2f}"
```

### 2.3 Locustfile — Scenarios A + B (Payment CRUD)

```python
# locustfile_payments.py
import uuid, random
from locust import HttpUser, task, between
from locustfile_shared import random_user_id, random_merchant, random_inr_amount

class PaymentReadUser(HttpUser):
    """Scenario A — read-heavy merchant dashboard."""
    wait_time = between(1, 3)
    host = "http://localhost:8000"

    def on_start(self):
        self.merchant = random_merchant()
        self.known_ids: list[str] = []

    @task(7)
    def list_payments(self):
        with self.client.get(
            f"/v1/payments?merchant_id={self.merchant}&limit=20",
            name="/v1/payments (list)",
            catch_response=True,
        ) as r:
            if r.status_code == 200:
                body = r.json()
                if body.get("items"):
                    for item in body["items"][:3]:
                        self.known_ids.append(item["transaction_id"])
                    # Keep list bounded
                    self.known_ids = self.known_ids[-50:]
                r.success()
            else:
                r.failure(f"status {r.status_code}")

    @task(2)
    def list_next_page(self):
        """Simulate pagination — fetch page 2 with a cursor."""
        with self.client.get(
            f"/v1/payments?merchant_id={self.merchant}&limit=20",
            name="/v1/payments (page 1 for cursor)",
            catch_response=True,
        ) as r:
            if r.status_code == 200:
                cursor = r.json().get("next_cursor")
                if cursor:
                    self.client.get(
                        f"/v1/payments?merchant_id={self.merchant}&limit=20&cursor={cursor}",
                        name="/v1/payments (page 2)",
                    )
                r.success()
            else:
                r.failure(f"status {r.status_code}")

    @task(1)
    def get_by_id(self):
        if not self.known_ids:
            return
        txn_id = random.choice(self.known_ids)
        self.client.get(
            f"/v1/payments/{txn_id}",
            name="/v1/payments/{id}",
        )


class PaymentWriteUser(HttpUser):
    """Scenario B — payment creation under concurrency."""
    wait_time = between(0.5, 2)
    host = "http://localhost:8000"

    @task
    def create_payment(self):
        idempotency_key = str(uuid.uuid4())
        payload = {
            "user_id":        random_user_id(),
            "merchant_id":    random_merchant(),
            "amount":         random_inr_amount(),
            "currency":       "INR",
            "payment_method": random.choice(["upi", "card", "bank_transfer", "wallet"]),
            "idempotency_key": idempotency_key,
            "metadata":       {"platform": "locust", "scenario": "B"},
        }
        with self.client.post(
            "/v1/payments",
            json=payload,
            headers={"Idempotency-Key": idempotency_key},
            name="POST /v1/payments",
            catch_response=True,
        ) as r:
            if r.status_code in (200, 201, 202):
                body = r.json()
                if "transaction_id" not in body:
                    r.failure("Missing transaction_id in response")
                elif body.get("status") not in ("success", "failed", "flagged", "pending"):
                    r.failure(f"Unexpected status: {body.get('status')}")
                else:
                    r.success()
            else:
                r.failure(f"status {r.status_code}: {r.text[:200]}")
```

### 2.4 Locustfile — Scenario C (Idempotency)

```python
# locustfile_idempotency.py
import uuid, time
from locust import HttpUser, task, between, events

# Shared keys created at test start — same keys used by all users
SHARED_KEYS: list[dict] = []

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    for _ in range(5):
        SHARED_KEYS.append({
            "key":        str(uuid.uuid4()),
            "user_id":    str(uuid.uuid4()),
            "merchant_id": "m_acme",
            "amount":     "500.00",
        })


class IdempotencyUser(HttpUser):
    """Scenario C — concurrent duplicate requests must produce one transaction."""
    wait_time = between(0.05, 0.1)
    host = "http://localhost:8000"

    def on_start(self):
        self.key_index = 0

    @task
    def send_duplicate(self):
        spec = SHARED_KEYS[self.key_index % len(SHARED_KEYS)]
        self.key_index += 1
        payload = {
            "user_id":         spec["user_id"],
            "merchant_id":     spec["merchant_id"],
            "amount":          spec["amount"],
            "currency":        "INR",
            "payment_method":  "upi",
            "idempotency_key": spec["key"],
        }
        with self.client.post(
            "/v1/payments",
            json=payload,
            headers={"Idempotency-Key": spec["key"]},
            name="POST /v1/payments (idempotency test)",
            catch_response=True,
        ) as r:
            if r.status_code in (200, 201):
                body = r.json()
                if "transaction_id" not in body:
                    r.failure("Missing transaction_id")
                else:
                    r.success()
            else:
                r.failure(f"status {r.status_code}")
```

### 2.5 Locustfile — Scenarios D + E (AI Services)

```python
# locustfile_ai.py
import uuid, random
from locust import HttpUser, task, between
from locustfile_shared import random_user_id, random_merchant

# 10 fraud scenarios from EVALUATION_RESULTS.md §4.3
FRAUD_SCENARIOS = [
    {"amount": "250.00",    "currency": "INR", "method": "upi",           "metadata": {"device_id": "d1", "ip_address": "10.0.0.1"}, "expected_decision": "allow",  "expected_min": 0,  "expected_max": 10},
    {"amount": "250.00",    "currency": "INR", "method": "upi",           "metadata": {},                                             "expected_decision": "allow",  "expected_min": 5,  "expected_max": 15},
    {"amount": "150000.00", "currency": "INR", "method": "bank_transfer", "metadata": {"device_id": "d1", "ip_address": "10.0.0.1"}, "expected_decision": "review", "expected_min": 30, "expected_max": 60},
    {"amount": "500.00",    "currency": "INR", "method": "upi",           "metadata": {"prior_failures": 3},                         "expected_decision": "allow",  "expected_min": 25, "expected_max": 40},
    {"amount": "100000.00", "currency": "INR", "method": "upi",           "metadata": {"country_receiver": "KP"},                    "expected_decision": "review", "expected_min": 45, "expected_max": 70},
    {"amount": "500.00",    "currency": "INR", "method": "upi",           "metadata": {"txns_last_hour": 12, "is_new_device": True},  "expected_decision": "review", "expected_min": 45, "expected_max": 65},
    {"amount": "500000.00", "currency": "INR", "method": "bank_transfer", "metadata": {"account_age_days": 2},                       "expected_decision": "reject", "expected_min": 85, "expected_max": 100},
    {"amount": "500.00",    "currency": "USD", "method": "card",          "metadata": {"card_country": "US", "merchant_country": "IN", "is_new_device": True}, "expected_decision": "review", "expected_min": 35, "expected_max": 55},
    {"amount": "1000.00",   "currency": "INR", "method": "upi",           "metadata": {"hour_of_day": 3, "device_id": "d1"},         "expected_decision": "allow",  "expected_min": 10, "expected_max": 20},
    {"amount": "500.00",    "currency": "INR", "method": "upi",           "metadata": {"device_id": "d1", "ip_address": "1.2.3.4", "country": "IN", "country_sender": "IN"}, "expected_decision": "allow", "expected_min": 0, "expected_max": 5},
]

# 10 RAG queries from the ground-truth dataset
RAG_QUERIES = [
    "How long does a UPI refund take?",
    "Can I refund a failed transaction?",
    "What triggers a chargeback?",
    "What score triggers automatic rejection?",
    "When does a UPI payment settle?",
    "How does a chargeback affect the transaction record?",
    "Can a single transaction be refunded multiple times?",
    "What is the review threshold?",
    "Why did my payment fail with insufficient_funds?",
    "What is the difference between a refund and a chargeback?",
]


class FraudScoringUser(HttpUser):
    """Scenario D — fraud rules-only path, Azure OpenAI disabled."""
    wait_time = between(0.1, 0.2)
    host = "http://localhost:8100"

    def on_start(self):
        self.scenario_index = random.randint(0, len(FRAUD_SCENARIOS) - 1)

    @task
    def score_fraud(self):
        sc = FRAUD_SCENARIOS[self.scenario_index % len(FRAUD_SCENARIOS)]
        self.scenario_index += 1
        payload = {
            "transaction_id": str(uuid.uuid4()),
            "user_id":        random_user_id(),
            "merchant_id":    random_merchant(),
            "amount":         sc["amount"],
            "currency":       sc["currency"],
            "payment_method": sc["method"],
            "metadata":       sc["metadata"],
        }
        with self.client.post(
            "/v1/fraud/score",
            json=payload,
            name="POST /v1/fraud/score",
            catch_response=True,
        ) as r:
            if r.status_code == 200:
                body = r.json()
                score = body.get("risk_score", -1)
                decision = body.get("decision", "")
                if decision != sc["expected_decision"]:
                    r.failure(
                        f"Expected {sc['expected_decision']}, got {decision} "
                        f"(score={score})"
                    )
                elif not (sc["expected_min"] <= score <= sc["expected_max"]):
                    r.failure(
                        f"Score {score} outside expected range "
                        f"[{sc['expected_min']}, {sc['expected_max']}]"
                    )
                else:
                    r.success()
            else:
                r.failure(f"status {r.status_code}")


class RAGQueryUser(HttpUser):
    """Scenario E — keyword search path, Azure OpenAI disabled."""
    wait_time = between(0.2, 0.5)
    host = "http://localhost:8100"

    def on_start(self):
        self.query_index = random.randint(0, len(RAG_QUERIES) - 1)

    @task
    def query_knowledge(self):
        query = RAG_QUERIES[self.query_index % len(RAG_QUERIES)]
        self.query_index += 1
        with self.client.post(
            "/v1/rag/query",
            json={"query": query, "top_k": 5},
            name="POST /v1/rag/query",
            catch_response=True,
        ) as r:
            if r.status_code == 200:
                body = r.json()
                if not body.get("chunks"):
                    r.failure("Empty chunks list")
                elif body.get("embedding_used"):
                    r.failure("embedding_used=true — Azure OpenAI should be disabled for this scenario")
                else:
                    r.success()
            else:
                r.failure(f"status {r.status_code}")
```

---

## Part 3 — Running the Tests

### 3.1 Pre-Test Checklist

```bash
# 1. Confirm all containers are healthy
make ps
make health

# 2. Verify seeded data
curl -s "http://localhost:8000/v1/payments?merchant_id=m_swiggy&limit=1" | python3 -m json.tool | head -5
# Expected: items with count 1 and valid transaction data

# 3. Verify fraud endpoint (rules-only)
AZURE_OPENAI_API_KEY="" curl -s -X POST http://localhost:8100/v1/fraud/score \
  -H "Content-Type: application/json" \
  -d '{"transaction_id":"00000000-0000-0000-0000-000000000001","user_id":"00000000-0000-0000-0000-000000000001","merchant_id":"m_acme","amount":"500","currency":"INR","payment_method":"upi","metadata":{}}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'llm_used={d[\"llm_used\"]} score={d[\"risk_score\"]}')"
# Expected: llm_used=False score=8

# 4. Install Locust if not present
pip install locust==2.32.0

# 5. Open the Postgres connection count baseline
psql "$DATABASE_URL" -c "SELECT count(*) FROM pg_stat_activity WHERE state='active';"
# Note this number — it should be 2-3 at idle
```

### 3.2 Running Individual Scenarios

```bash
# Scenario A — 50 read users, 5 minutes
locust -f locustfile_payments.py PaymentReadUser \
  --users 50 --spawn-rate 5 \
  --run-time 5m --headless \
  --host http://localhost:8000 \
  --csv results/scenario_a \
  --html results/scenario_a.html

# Scenario B — ramp to 30 write users, 8 minutes total
locust -f locustfile_payments.py PaymentWriteUser \
  --users 30 --spawn-rate 0.5 \
  --run-time 8m --headless \
  --host http://localhost:8000 \
  --csv results/scenario_b \
  --html results/scenario_b.html

# Scenario C — idempotency correctness, 10 users
locust -f locustfile_idempotency.py IdempotencyUser \
  --users 10 --spawn-rate 10 \
  --run-time 3m --headless \
  --host http://localhost:8000 \
  --csv results/scenario_c

# Scenario D — fraud scoring, 100 users (disable Azure OpenAI first)
# Option 1: unset the env var in the current shell
# Option 2: pass --env AZURE_OPENAI_API_KEY= when starting containers
locust -f locustfile_ai.py FraudScoringUser \
  --users 100 --spawn-rate 10 \
  --run-time 3m --headless \
  --host http://localhost:8100 \
  --csv results/scenario_d

# Scenario E — RAG keyword search, 50 users
locust -f locustfile_ai.py RAGQueryUser \
  --users 50 --spawn-rate 5 \
  --run-time 3m --headless \
  --host http://localhost:8100 \
  --csv results/scenario_e

# Scenario F — mixed realistic load
locust -f locustfile_payments.py -f locustfile_ai.py \
  PaymentReadUser PaymentWriteUser FraudScoringUser RAGQueryUser \
  --users 60 --spawn-rate 5 \
  --run-time 10m --headless \
  --host http://localhost:8000 \
  --csv results/scenario_f
```

### 3.3 Interactive Mode (with Web UI)

```bash
# Launch with the Locust web UI for real-time monitoring
locust -f locustfile_payments.py \
  --host http://localhost:8000
# Then open http://localhost:8089
# Set: Users=50, Spawn rate=5, then Start
```

### 3.4 Reading the Results

Locust generates three output files per scenario (with `--csv results/scenario_X`):
- `scenario_X_stats.csv` — per-endpoint P50, P95, P99, mean, min, max, RPS, failures
- `scenario_X_stats_history.csv` — time-series of the above (useful for plotting ramp behaviour)
- `scenario_X_failures.csv` — failure messages for debugging

**Quick pass/fail check:**

```bash
# Parse CSV for pass/fail against SLO targets
python3 - << 'EOF'
import csv

targets = {
    "GET /v1/payments (list)": {"p99": 120, "failure_rate": 0.001},
    "GET /v1/payments/{id}": {"p99": 80, "failure_rate": 0.001},
    "POST /v1/payments": {"p99": 250, "failure_rate": 0.005},
    "POST /v1/fraud/score": {"p99": 15, "failure_rate": 0.000},
    "POST /v1/rag/query": {"p99": 50, "failure_rate": 0.000},
}

import sys
scenario_file = sys.argv[1] if len(sys.argv) > 1 else "results/scenario_b_stats.csv"
with open(scenario_file) as f:
    reader = csv.DictReader(f)
    for row in reader:
        name = row["Name"]
        if name not in targets:
            continue
        p99 = float(row["99%"])
        total = int(row["Request Count"])
        failures = int(row["Failure Count"])
        failure_rate = failures / total if total > 0 else 0
        tgt = targets[name]
        p99_pass = p99 <= tgt["p99"]
        fr_pass = failure_rate <= tgt["failure_rate"]
        status = "PASS" if p99_pass and fr_pass else "FAIL"
        print(f"{status}  {name}")
        print(f"       P99={p99:.0f}ms (limit {tgt['p99']}ms)  "
              f"failures={failure_rate:.4f} (limit {tgt['failure_rate']:.4f})")
EOF python3 - results/scenario_b_stats.csv
```

---

## Part 4 — Expected Results and Bottleneck Analysis

### 4.1 Expected Results on Current Hardware (4 CPU, 16 GB RAM, NVMe SSD, Podman Compose)

These projections are based on the sequential measurements from EVALUATION_RESULTS.md §3.1-3.2 and standard asyncio concurrency scaling characteristics.

| Scenario | Expected P50 | Expected P99 | Expected RPS | Bottleneck |
|---|---|---|---|---|
| A (50 read users) | 20–35ms | 80–150ms | 40–60 RPS | asyncpg pool (10 connections) |
| B (30 write users) | 40–70ms | 150–300ms | 12–20 RPS | 4 sequential Postgres round-trips per payment |
| C (idempotency) | 30–60ms | 120–250ms | 15–25 RPS | Redis + Postgres idempotency check |
| D (100 fraud users) | 2–5ms | 8–20ms | 80–150 RPS | Single uvicorn event loop; Python Decimal arithmetic |
| E (50 RAG keyword users) | 8–15ms | 30–60ms | 60–100 RPS | Dice-coefficient over 48 chunks; no external I/O |
| F (mixed 60 users) | varies | varies | — | Multiple bottlenecks active simultaneously |

### 4.2 Bottleneck Hierarchy

The system bottlenecks in this order under increasing load:

1. **Postgres pool saturation (pool_size=10):** The first constraint hit under write load. 10 concurrent active DB connections is the ceiling. At 30 write users with ~70ms average query time, the effective Postgres utilisation is `30 × 0.070s = 2.1 connection-seconds/second`, which fits within `10 connections`. The pool saturates at roughly `10 / 0.070 = ~143 connections-worth of demand`, meaning ~45 concurrent write users before queueing dominates.

2. **Single uvicorn event loop:** Both services run without `--workers`, so all async I/O shares one Python event loop. CPU-bound operations (Decimal arithmetic in the fraud rules) block the loop briefly. Under 100+ concurrent requests this creates milliseconds of queueing even when the operations are fast.

3. **Optimistic lock contention:** At 30+ concurrent write users selecting from 501 users uniformly, the probability of two users selecting the same user for a payment approaches `1 - e^(-n²/2×501)` per batch. The service retries up to 3 times per lock conflict; above ~50 concurrent writers, contention becomes frequent enough to add latency.

4. **Azure OpenAI rate limit (not in load test scope):** If the vector embedding path is enabled during load testing, the Azure OpenAI token-per-minute limit will be exhausted. The 5-second embedding timeout will trigger keyword fallback for all requests once the limit is hit, causing a visible step-change in `search_mode` distribution in the Locust stats. This is why Scenarios D and E explicitly disable Azure OpenAI.

### 4.3 Production Sizing Implications

The load test results will reveal the specific thresholds. Based on the architecture, the following changes unlock the next tier of throughput:

| Change | Unlocks |
|---|---|
| `pool_size=20` in `shared/config.py` | 2× write throughput before pool saturation |
| `--workers 4` in uvicorn CMD | 4× concurrent event loop capacity |
| Read replica added to Postgres | Read and write loads no longer contend for the same pool |
| PgBouncer in front of Postgres | Pool size becomes a soft limit rather than a hard ceiling |
| Redis idempotency pre-check optimised | Reduces round-trip count from 4 to 3 for repeat merchants |

---

## Part 5 — Known Gaps and Future Work

| Gap | Current state | Production path |
|---|---|---|
| Load test not yet executed | Specification only | Phase 4 CI/CD work; run as part of release pipeline |
| No sustained soak test (hours) | Only 3–10 minute scenarios | Add a 2-hour Scenario B run to detect memory leaks |
| No failure injection | Clean environment only | Chaos testing: kill Postgres mid-test, verify recovery |
| Azure OpenAI load not tested | Disabled for load tests | Separate quota exhaustion test with mock LLM |
| Frontend under load not tested | API load only | Lighthouse CI + k6 for frontend rendering performance |
| Postgres `pg_stat_statements` not enabled | Cannot identify slow queries under load | Add `shared_preload_libraries='pg_stat_statements'` to Postgres config |
| No profiling during load test | Black-box latency only | `py-spy record` against the uvicorn PID during Scenario B peak |

The load test is Phase 4 work on the production hardening roadmap. The CI integration target (`github/workflows/ai-eval.yml`) will run Scenario A and B nightly and fail the build if P99 regresses by more than 50ms from the established baseline.

---

*This document specifies load test methodology. Actual results will be appended here when Scenario A–F are executed. The sequential measurements from EVALUATION_RESULTS.md §3.1 serve as the current best available performance baseline.*
