# Payment Failure Policy

## Overview

A payment failure occurs when a transaction cannot be completed successfully. Failures are recorded with a `status=failed` and a `failure_reason` field describing the cause. No ledger entries are created for failed transactions. The sender's wallet balance is not affected.

## Failure Reason Codes

| `failure_reason` | Description | Retryable? |
|---|---|---|
| `insufficient_funds` | Sender's wallet balance is below the transaction amount | Yes — after top-up |
| `card_declined` | Card issuer declined the authorisation | Possibly — depends on decline code |
| `bank_timeout` | Bank or payment rail did not respond within SLA | Yes — after brief delay |
| `fraud_blocked` | Fraud score exceeded the rejection threshold | No — requires manual review to unlock |
| `invalid_account` | The destination account is invalid or closed | No — requires updated account details |
| `daily_limit_exceeded` | User or merchant daily transaction limit reached | Yes — after 00:00 reset |
| `wallet_contention` | Optimistic lock retry budget exhausted under high concurrency | Yes — immediately |
| `provider_unavailable` | Payment provider is unreachable or returning errors | Yes — after provider recovery |
| `kyc_required` | Transaction blocked pending KYC verification | No — KYC must be completed first |
| `currency_not_supported` | The requested currency is not supported for this payment method | No |
| `amount_too_small` | Transaction amount is below the minimum (₹1 / $0.01) | No |
| `amount_too_large` | Transaction amount exceeds single-transaction limit | No — split the transaction |

## Retry Guidelines

### Client-Side Retry

Clients should retry only on transient failures:

- `bank_timeout`, `wallet_contention`, `provider_unavailable` — safe to retry after exponential backoff (first retry after 1s, then 2s, 4s, max 3 retries).
- `insufficient_funds` — not a transient failure; retrying immediately will fail again.
- `fraud_blocked`, `kyc_required` — must not retry without human intervention.

Always use an `Idempotency-Key` header on every retry to prevent duplicate transactions.

### Idempotency on Retry

The platform guarantees that retrying a request with the same `Idempotency-Key` will return the cached response for 24 hours. After 24 hours, the key expires from the fast path (Redis) but remains in the database for 7 days, still preventing double-charging.

A retry with a **new** idempotency key creates a **new** transaction attempt — this is appropriate when the client intentionally retries after a terminal failure (e.g., the customer entered new card details).

## Insufficient Funds Handling

When a wallet debit fails due to insufficient funds:

1. The transaction is created with `status=pending`, then immediately transitioned to `status=failed`.
2. `failure_reason` is set to `insufficient_funds`.
3. The wallet balance remains unchanged.
4. No ledger entries are created.
5. The failed transaction row is preserved for audit and customer support.

The API returns a 200 (not 422) with the failed transaction body — this is intentional. The payment attempt was valid; the funds were simply unavailable.

## Wallet Contention

Under high concurrent load, multiple requests may attempt to debit the same wallet simultaneously. The platform uses optimistic concurrency control (version-based UPDATE ... WHERE version = :expected). If the version check fails, the service retries up to 3 times. If all retries are exhausted:

1. The transaction is marked `status=failed`, `failure_reason=wallet_contention`.
2. The API returns 503 Service Unavailable.
3. The client should retry after a random jitter of 1–5 seconds.

## Provider Failures

When an upstream payment provider (card network, bank, UPI switch) is unavailable:

- The transaction is created with `status=pending`.
- The failure is recorded but the transaction is not immediately marked `failed`.
- A background reconciliation job retries the provider call every 30 seconds for up to 5 minutes.
- After 5 minutes without a successful provider response, the transaction is marked `failed` with `failure_reason=provider_unavailable`.
- The domain event `payment.provider_timeout` is emitted to the outbox for the operations team.

## Audit Trail for Failures

Every failed transaction is retained in the database permanently (no deletion policy for failed transactions). The audit log captures:

- The requested amount, currency, and payment method.
- The failure reason and any provider error codes.
- The fraud score at the time of the request (even for non-fraud failures).
- The user ID and merchant ID.
- The idempotency key (if provided).

This ensures customer support can reconstruct the exact failure scenario from the transaction ID alone.

## Customer Communication

The platform does not send failure notifications directly — this is the merchant's responsibility. The API response body includes a human-readable `failure_reason` that merchants should translate into appropriate customer-facing messages. Merchants should not expose raw internal codes like `wallet_contention` to end customers.

## Failure Rate Monitoring

The operations team monitors failure rates via Prometheus metrics:

- `payment_failure_total{reason="..."}` — count by failure reason.
- `payment_failure_rate_5m` — rolling 5-minute failure rate.
- Alert threshold: failure rate > 5% in a 5-minute window triggers a P1 alert.
