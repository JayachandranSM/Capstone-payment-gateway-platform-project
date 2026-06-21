# Refund Policy

## Overview

A refund is a reversal of a payment that credits money back to the original payer. Refunds are distinct from chargebacks — they are merchant-initiated and cooperative, whereas chargebacks are bank-initiated disputes.

## Eligibility

A transaction is eligible for refund when all of the following conditions are met:

- The transaction status is `success`. Transactions with status `failed`, `pending`, `flagged`, or `reversed` cannot be refunded.
- The refund is requested within **180 days** of the original transaction date.
- The refund amount does not exceed the original transaction amount. Partial refunds up to the remaining refundable balance are allowed.
- The sum of all prior refunds plus the new refund amount must not exceed the original transaction amount (over-refund check).

## Partial Refunds

Partial refunds are supported. Multiple partial refunds may be issued against a single transaction, provided the cumulative refunded amount does not exceed the original transaction amount. For example, a transaction of ₹10,000 may have a ₹3,000 refund followed by a ₹5,000 refund, leaving ₹2,000 still refundable.

## Refund Processing

1. The merchant initiates a refund via `POST /api/v1/refunds` with the `transaction_id`, `amount`, and an optional `reason`.
2. The system verifies eligibility: parent transaction exists, status is `success`, amount is within limits.
3. A new transaction row is created with `status=pending` and `parent_transaction` pointing to the original.
4. The sender's wallet is credited with the refund amount.
5. Ledger entries are posted in the reverse direction: the merchant suspense account is debited, the sender wallet is credited.
6. The refund transaction status transitions to `success`.

## Settlement Impact

Refunds issued against already-settled transactions update the `settlement_status` of the original transaction to `disputed` for reconciliation purposes. The merchant's payout is adjusted in the next settlement cycle.

## Refund Timeline

- Wallet-based refunds: instant (same session).
- Bank transfer refunds: 3–7 business days depending on the receiving bank.
- Card refunds: 5–10 business days depending on the card network.
- UPI refunds: 1–2 business days.

## Common Refund Rejection Reasons

- **Transaction not found**: the `transaction_id` does not exist or is not owned by the requesting merchant.
- **Wrong status**: the transaction is not in `success` state.
- **Over-refund**: the requested amount plus prior refunds exceeds the original amount.
- **Expired**: the refund window (180 days) has passed.
- **P2P transaction**: peer-to-peer transactions (no `merchant_id`) use a separate refund flow not available in the merchant API.

## Audit Trail

Every refund creates an audit log entry capturing the actor (merchant or system), the before and after states of the parent transaction, the refund amount, and the request idempotency key. Refund records are immutable once committed.
