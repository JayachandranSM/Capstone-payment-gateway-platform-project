# Fraud Detection and Prevention Policy

## Overview

The platform operates a multi-layer fraud defence combining real-time rule evaluation, machine learning scoring, and human review. Every payment transaction is scored before funds are moved. A flagged transaction is held pending review; a rejected transaction is declined immediately.

## Risk Score Tiers

| Score Range | Decision  | Action |
|-------------|-----------|--------|
| 0–39        | Allow     | Transaction proceeds immediately |
| 40–74       | Review    | Transaction held; ops notified within 2 minutes |
| 75–100      | Reject    | Transaction declined; wallet not debited |

## Rule Categories

### Amount-Based Rules

Large transactions attract elevated scrutiny. Thresholds are currency-specific:

- **INR**: transactions above ₹1,00,000 receive a base weight of 25. Above ₹5,00,000 the weight increases to 40.
- **USD/EUR**: transactions above $10,000 / €10,000 receive a weight of 20–35.
- **Round amounts**: transactions that are exact multiples of ₹50,000, ₹1,00,000, ₹5,00,000, or ₹10,00,000 receive an additional weight of 15 (known structured-payment pattern in money laundering).

### Velocity-Based Rules

- **Prior failures**: two or more payment failures in the current session window add 10 points per failure, capped at 35.
- **High frequency**: more than 5 transactions in the last hour adds 20 points (35 for 10+).
- **New account**: accounts less than 30 days old attempting significant transactions add 15–25 points.

### Geographic Rules

- **High-risk country**: the receiver's country appears on the FATF grey/black list — adds 30 points.
- **Cross-border**: sender and receiver in different non-risk countries — adds 10 points.

### Payment Method Rules

- **Large bank transfer**: bank transfers above ₹5,00,000 or $50,000 are harder to reverse — adds 20 points.
- **Foreign card**: card issued in a country different from the merchant's country — adds 15 points.

### Merchant Rules

- **High-risk category**: merchants in gambling, cryptocurrency exchange, unregulated forex, or adult content categories — adds 30 points.
- **New merchant**: fewer than 5 prior transactions on the platform — adds 20 points.

### Behavioural Rules

- **Odd hours**: transactions between 02:00–05:00 local time — adds 12 points.
- **New device**: payment from a device not previously seen for this user — adds 20 points (30 for large amounts).
- **Sparse metadata**: fewer than 2 of the expected contextual signals present — adds 8 points.

## Flagged Transaction Handling

A transaction with score 40–74 transitions to `status=flagged`. The following applies:

1. The wallet is **not debited** until a reviewer approves the transaction.
2. The fraud analyst queue is updated within 2 minutes.
3. The analyst may approve (transition `flagged → success`, triggering wallet debit) or reject (transition `flagged → failed`).
4. If no review decision is made within 24 hours, the transaction is automatically rejected.

## Rejected Transactions

A rejected transaction (`score ≥ 75`) is declined immediately:

- No wallet debit occurs.
- No ledger entries are created.
- The transaction row is persisted with `status=failed` and `failure_reason=fraud_blocked`.
- The fraud score and all fired rule IDs are stored for audit and model improvement.

## Model Improvement Process

All scored transactions (allow, review, reject) are stored with their full rule-hit detail. A weekly evaluation job computes:

- Precision and recall per rule family.
- False positive rate (flagged transactions that were approved by reviewers).
- False negative rate (allowed transactions that later generated chargebacks).

Threshold adjustments require approval from the Risk team and are recorded as versioned model changes.

## Velocity Checks via Redis

Real-time velocity signals (transactions per hour, failed attempts per session) are maintained in Redis with TTLs matching the window size. These are computed by the calling service and passed in the `metadata` field of the fraud score request.

## PCI-DSS Alignment

Fraud controls are designed to comply with PCI-DSS Requirement 10 (audit logs), Requirement 10.6 (log review), and Requirement 11.4 (intrusion detection). All fraud decisions and the signals that drove them are stored in the `ops.audit_log` table with immutable, append-only guarantees.
