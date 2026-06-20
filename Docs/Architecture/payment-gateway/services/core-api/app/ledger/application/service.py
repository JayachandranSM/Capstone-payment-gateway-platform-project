"""Application service for double-entry ledger postings.

Responsibilities:
- Validate the invariant ``sum(debits) == sum(credits)`` per
  ``(transaction_id, currency)`` *before* writing entries.
- Validate each entry's amount is positive (the DB ``CHECK`` is the
  safety net; this is the early failure path).
- Produce reversal entries for full transaction voids.

Non-responsibilities (deliberate):
- This service does **not** know about wallets, payments, or users.
  ``account_id`` is opaque — it may be a wallet UUID or a system
  account UUID. This is the contract that keeps ``ledger`` extractable
  to its own Postgres cluster (Architecture Review ADR-006).
- This service does **not** commit. The caller (typically
  ``PaymentService.create_payment``) owns the transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import LedgerDirection
from app.ledger.domain.models import LedgerEntry
from app.ledger.infrastructure.repository import (
    EmptyPostingError,
    LedgerRepository,
)

log = structlog.get_logger(__name__)

_DECIMAL_ZERO: Final[Decimal] = Decimal("0")


# ── Exceptions ───────────────────────────────────────────────────────────


class LedgerServiceError(Exception):
    """Base for ledger-service-level errors."""


class InvalidLedgerAmountError(LedgerServiceError):
    """A ledger entry amount failed positivity validation."""

    def __init__(self, value: Decimal) -> None:
        super().__init__(f"ledger amount must be > 0; got {value}")
        self.value = value


class UnbalancedPostingError(LedgerServiceError):
    """The double-entry invariant would be violated by this posting.

    Always raised *before* any rows are written.
    """

    def __init__(
        self,
        transaction_id: uuid.UUID,
        currency: str,
        debits: Decimal,
        credits: Decimal,
    ) -> None:
        super().__init__(
            f"unbalanced posting transaction={transaction_id} "
            f"currency={currency}: debits={debits} != credits={credits}"
        )
        self.transaction_id = transaction_id
        self.currency = currency
        self.debits = debits
        self.credits = credits


class MixedTransactionPostingError(LedgerServiceError):
    """One posting batch must reference exactly one ``transaction_id``.

    Cross-transaction batches would obscure the per-transaction
    invariant; we forbid them.
    """


class NoOriginalEntriesError(LedgerServiceError):
    """A reversal was requested but no original entries were found."""

    def __init__(self, original_transaction_id: uuid.UUID) -> None:
        super().__init__(
            f"no ledger entries to reverse for transaction {original_transaction_id}"
        )
        self.original_transaction_id = original_transaction_id


# ── Service ──────────────────────────────────────────────────────────────


class LedgerService:
    """Posts and queries the double-entry ledger."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        ledger_repository: LedgerRepository | None = None,
    ) -> None:
        self._session = session
        self._ledger_repo = ledger_repository or LedgerRepository(session)

    # ── Writes ───────────────────────────────────────────────────────

    async def post_payment(
        self,
        *,
        transaction_id: uuid.UUID,
        debit_account_id: uuid.UUID,
        credit_account_id: uuid.UUID,
        amount: Decimal,
        currency: str,
    ) -> tuple[LedgerEntry, LedgerEntry]:
        """Post a balanced double-entry pair.

        Used by both forward payments (DEBIT sender, CREDIT merchant)
        and partial refunds (DEBIT merchant, CREDIT sender) — the
        direction is encoded by the caller's choice of accounts.

        Returns:
            ``(debit_entry, credit_entry)`` with server-populated IDs
            and ``posted_at``.

        Raises:
            InvalidLedgerAmountError, UnbalancedPostingError
                (the latter cannot fire on the two-entry case but the
                validator is shared with the multi-entry reversal path)
        """
        self._validate_amount(amount)
        if debit_account_id == credit_account_id:
            # A self-transfer would balance trivially but accomplish
            # nothing. Almost certainly a caller bug; refuse it.
            raise LedgerServiceError(
                f"debit and credit accounts must differ; got {debit_account_id}"
            )

        debit = LedgerEntry(
            transaction_id=transaction_id,
            account_id=debit_account_id,
            direction=LedgerDirection.DEBIT,
            amount=amount,
            currency=currency,
        )
        credit = LedgerEntry(
            transaction_id=transaction_id,
            account_id=credit_account_id,
            direction=LedgerDirection.CREDIT,
            amount=amount,
            currency=currency,
        )

        self._validate_balanced([debit, credit])

        entries = await self._ledger_repo.add_entries([debit, credit])
        log.info(
            "ledger_payment_posted",
            transaction_id=str(transaction_id),
            amount=str(amount),
            currency=currency,
            debit_account=str(debit_account_id),
            credit_account=str(credit_account_id),
        )
        return entries[0], entries[1]

    async def reverse(
        self,
        *,
        original_transaction_id: uuid.UUID,
        reversal_transaction_id: uuid.UUID,
    ) -> Sequence[LedgerEntry]:
        """Post compensating entries that exactly reverse the original posting.

        For each original entry, posts a flip-direction entry with
        ``reversal_of`` referencing the original. The new entries are
        grouped under ``reversal_transaction_id``, not the original —
        so the audit trail shows the reversal as its own posting and
        the ledger view of the original transaction stays unchanged.

        Used for full transaction voids (chargebacks, fraud reversals).
        For *partial* refunds, the caller should use ``post_payment``
        with swapped accounts instead.

        Raises:
            NoOriginalEntriesError: original transaction has no ledger.
        """
        originals = await self._ledger_repo.list_by_transaction(
            original_transaction_id
        )
        if not originals:
            raise NoOriginalEntriesError(original_transaction_id)

        reversals: list[LedgerEntry] = []
        for orig in originals:
            opposite = (
                LedgerDirection.CREDIT
                if orig.direction is LedgerDirection.DEBIT
                else LedgerDirection.DEBIT
            )
            reversals.append(
                LedgerEntry(
                    transaction_id=reversal_transaction_id,
                    account_id=orig.account_id,
                    direction=opposite,
                    amount=orig.amount,
                    currency=orig.currency,
                    reversal_of=orig.entry_id,
                )
            )

        self._validate_balanced(reversals)
        entries = await self._ledger_repo.add_entries(reversals)
        log.info(
            "ledger_reversed",
            original_transaction_id=str(original_transaction_id),
            reversal_transaction_id=str(reversal_transaction_id),
            entry_count=len(entries),
        )
        return entries

    # ── Reads ────────────────────────────────────────────────────────

    async def get_balance(
        self,
        account_id: uuid.UUID,
        currency: str,
    ) -> Decimal:
        """Compute the current balance for ``(account_id, currency)``."""
        return await self._ledger_repo.get_balance(account_id, currency)

    async def list_for_transaction(
        self,
        transaction_id: uuid.UUID,
    ) -> Sequence[LedgerEntry]:
        """Return every entry posted under ``transaction_id``."""
        return await self._ledger_repo.list_by_transaction(transaction_id)

    # ── Validation helpers ───────────────────────────────────────────

    @staticmethod
    def _validate_amount(amount: Decimal) -> None:
        if not isinstance(amount, Decimal):
            raise TypeError(
                f"amount must be Decimal, got {type(amount).__name__}"
            )
        if amount <= _DECIMAL_ZERO:
            raise InvalidLedgerAmountError(amount)

    @staticmethod
    def _validate_balanced(entries: Sequence[LedgerEntry]) -> None:
        """Enforce sum(debits) == sum(credits) per ``(transaction_id, currency)``.

        Also enforces that all entries share one ``transaction_id``
        (cross-transaction batching is forbidden — see
        ``MixedTransactionPostingError``).
        """
        if not entries:
            raise EmptyPostingError("cannot validate empty posting")

        # All entries must share one transaction_id.
        txn_id = entries[0].transaction_id
        for e in entries:
            if e.transaction_id != txn_id:
                raise MixedTransactionPostingError(
                    f"entries reference multiple transaction_ids: "
                    f"{txn_id} and {e.transaction_id}"
                )

        # Each currency must balance independently. Cross-currency
        # postings (FX) are two linked transactions, not one — see
        # PAYMENT_DOMAIN_DESIGN.md §11.
        per_currency: dict[str, tuple[Decimal, Decimal]] = {}
        for e in entries:
            debits, credits = per_currency.get(
                e.currency, (_DECIMAL_ZERO, _DECIMAL_ZERO)
            )
            if e.direction is LedgerDirection.DEBIT:
                debits += e.amount
            else:
                credits += e.amount
            per_currency[e.currency] = (debits, credits)

        for currency, (debits, credits) in per_currency.items():
            if debits != credits:
                raise UnbalancedPostingError(txn_id, currency, debits, credits)


__all__ = [
    "LedgerService",
    "LedgerServiceError",
    "InvalidLedgerAmountError",
    "UnbalancedPostingError",
    "MixedTransactionPostingError",
    "NoOriginalEntriesError",
    # Re-export so callers don't dig into the repository module.
    "EmptyPostingError",
]
