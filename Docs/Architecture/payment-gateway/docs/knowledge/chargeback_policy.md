# Chargeback Policy

## Overview

A chargeback occurs when a cardholder disputes a charge directly with their bank or card network, bypassing the merchant. The bank reverses the transaction and initiates a dispute process. Chargebacks are distinct from merchant-initiated refunds and carry additional fees and reputational consequences.

## Chargeback Triggers

Common reasons a cardholder initiates a chargeback:

- **Fraud**: the cardholder did not authorise the transaction (true fraud or account takeover).
- **Item not received**: goods or services were not delivered.
- **Item not as described**: the delivered item differs significantly from what was advertised.
- **Duplicate charge**: the cardholder was charged twice for the same transaction.
- **Subscription cancelled**: a recurring charge occurred after the subscription was cancelled.
- **Technical error**: the cardholder was charged but the merchant did not fulfil the order due to a system error.

## Impact on Transactions

When a chargeback is received:

- The `chargeback_flag` field on the transaction is set to `true`.
- The `settlement_status` transitions to `disputed`.
- The transaction amount is reversed from the merchant's payout.
- A chargeback fee is levied against the merchant account (typically $15–$25 per dispute, network-dependent).

## Chargeback Thresholds

Card networks enforce chargeback ratio limits:

- **Visa**: chargeback ratio must stay below 0.9% (standard) or 1.8% (high-risk programme).
- **Mastercard**: excessive chargeback programme triggers at 1.5% ratio.
- Exceeding these thresholds results in higher fees, enhanced monitoring, or termination of card-acceptance privileges.

## Dispute Evidence Window

Merchants have **20 calendar days** from the chargeback notification to submit compelling evidence. Evidence types accepted:

- Signed delivery confirmation or proof of delivery.
- IP address and device fingerprint matching the billing address.
- Customer communication (emails, chat logs) confirming acceptance.
- Terms of service with customer acceptance timestamp.
- Prior refund already issued for the same transaction.

## Resolution Outcomes

- **Won (merchant)**: chargeback reversed, funds returned to merchant, no fee refund.
- **Lost (merchant)**: chargeback upheld, merchant loses the transaction amount plus fees.
- **Representment**: merchant re-presents evidence; network makes final binding decision.

## Chargeback vs Refund

If a merchant suspects a chargeback is incoming and the refund window is still open, issuing a voluntary refund is always preferable. A refund:

- Avoids the chargeback fee.
- Does not count against the chargeback ratio.
- Resolves faster for the cardholder.

The system prevents double recovery: if a refund has already been issued for a transaction, the chargeback flag triggers an alert to operations to prevent the cardholder from receiving both a refund and a chargeback credit.

## Fraud Chargebacks and Liability Shift

Under EMV (chip) card rules, liability for card-present fraud shifts to the acquirer or merchant if they did not support EMV chip authentication. For card-not-present (online) transactions, liability shifts to the issuer if the merchant used 3D Secure (3DS2) authentication and the issuer approved the transaction.
