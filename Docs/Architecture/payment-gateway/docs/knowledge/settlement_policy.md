# Settlement Policy

## Overview

Settlement is the process by which funds collected from payment transactions are transferred to merchant bank accounts. The platform operates a T+1 or T+2 settlement cycle depending on the merchant tier and payment method mix.

## Settlement Cycle

| Payment Method   | Settlement Cycle | Notes |
|------------------|-----------------|-------|
| UPI              | T+1 (next business day) | Initiated via NPCI IMPS rails |
| Debit card       | T+1             | Direct debit settlement |
| Credit card      | T+2             | Interchange and network processing |
| Bank transfer    | T+2 to T+3      | Depends on beneficiary bank |
| Wallet           | T+0 (same day)  | Internal ledger movement |

*T = transaction date. Business days only; weekends and public holidays extend the cycle.*

## Settlement Status Lifecycle

A transaction's `settlement_status` field tracks its settlement state independently of `transaction_status`:

- `pending` — transaction has succeeded but settlement has not been initiated.
- `settled` — funds have been transferred to the merchant account.
- `disputed` — chargeback or refund has been issued; settlement is paused.
- `reversed` — settlement was completed but subsequently reversed (chargeback won by cardholder).

## Merchant Settlement Account

Each merchant has a settlement account identified by a stable UUID derived from their `merchant_id`. Settlement batches aggregate all successful, non-disputed transactions for a given merchant across the settlement window and credit the total to the merchant's registered bank account.

## Minimum Settlement Amount

Settlements are not initiated below ₹100 (or equivalent in other currencies). Amounts below this threshold accumulate and are included in the next cycle's batch once the threshold is reached.

## Holds and Reserves

The platform may hold a rolling reserve for high-risk merchants or those with elevated chargeback ratios:

- **Standard reserve**: 5% of gross transaction volume, held for 90 days.
- **Enhanced reserve**: 10% of gross volume, held for 180 days (triggered by chargeback ratio > 1%).
- **Freeze**: full account freeze if chargeback ratio exceeds 2% or if fraud investigation is ongoing.

Reserves are released automatically at the end of the holding period unless a dispute is in progress.

## Settlement Reconciliation

The settlement batch process:

1. Selects all transactions with `settlement_status = pending` and `status = success` in the settlement window.
2. Groups by merchant and currency.
3. Deducts any refunds or chargebacks processed in the same window.
4. Deducts platform fees (MDR — Merchant Discount Rate) per the merchant's contract.
5. Initiates the bank transfer to the merchant's nominated account.
6. Updates `settlement_status` to `settled` and records the settlement batch ID.

## Merchant Discount Rate (MDR)

Platform fees applied before settlement:

- UPI: 0% (regulatory mandate, zero MDR on UPI P2M).
- Debit card (domestic): 0.4% of transaction value.
- Credit card: 1.0–2.5% depending on card type and merchant category.
- International cards: 2.5–3.5% plus currency conversion spread.
- Bank transfer: flat ₹10–₹25 per transaction.

## Dispute Impact on Settlement

Chargebacks and refunds affect settlement as follows:

- A refund issued after settlement causes a deduction in the next settlement cycle.
- A chargeback received after settlement triggers an immediate hold on the merchant's next payout equal to the chargeback amount plus fees.
- If the merchant wins the chargeback, the held amount is released in the following cycle.

## Tax Withholding (TDS)

For qualifying merchant categories, Tax Deducted at Source (TDS) of 1% is withheld from settlement amounts and remitted to the Income Tax Department under Section 194-O of the Income Tax Act. A TDS certificate is issued to the merchant quarterly.
