"""Repository for ``ledger.entries``.

Architectural reminders:

- **The ledger lives in its own schema** (``ledger``) and, in
  production, will move to its own Postgres cluster (Architecture
  Review ADR-006). The model deliberately has no FK from
  ``ledger.entries.transaction_id`` to ``core.transactions`` — orphan
  prevention is the *application's* job, not the DB's. The integration
  test suite (PAYMENT_DOMAIN_DESIGN.md §10) verifies "no orphans"
  continuously.

- **Entries are immutable.** Reversals are inserted as *new* entries
  with ``reversal_of`` set. There is no ``update`` method here, and
  there will not be one. Ledger correctness depends on this property.

- **The "debits == credits per transaction" invariant** is enforced
  by ``app.ledger.application.service`` under SERIALIZABLE isolation
  (file not in this turn). This repository takes pre-validated
  entries and writes them; it does *not* re-check the invariant —
  that would be belt-and-braces in the wrong direction (the service
  must be the source of truth on the invariant, or it cannot reason
  about correctness).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Sequence

import structlog
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import LedgerDirection
from app.ledger.domain.models import LedgerEntry

log = structlog.get_logger(__name__)


# ── Repository-layer exceptions ──────────────────────────────────────────


class LedgerRepositoryError(Exception):
    """Base for ledger-repository errors."""


class EmptyPostingError(LedgerRepositoryError):
    """Raised when ``add_entries`` is called with an empty iterable.

    A posting with zero entries is meaningless and almost certainly a
    service-layer bug — we surface it loudly rather than silently
    no-op.
    """


# ── Repository ───────────────────────────────────────────────────────────


class LedgerRepository:
    """Data-access adapter for ``ledger.entries``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Writes ───────────────────────────────────────────────────────

    async def add_entries(
        self,
        entries: Iterable[LedgerEntry],
    ) -> Sequence[LedgerEntry]:
        """Persist a list of pre-validated ledger entries.

        The caller (typically ``LedgerService.post``) MUST have already
        verified the double-entry invariant. This method is a thin
        data-access primitive.

        Args:
            entries: Iterable of ``LedgerEntry`` instances. All entries
                     should share the same ``transaction_id`` (this is
                     the *normal* case; the method does not enforce it
                     so that net postings across multiple transactions
                     can be batched if a future use case needs it).

        Returns:
            The same list, now with server-generated values
            (``entry_id`` and ``posted_at``) populated.

        Raises:
            EmptyPostingError: if ``entries`` is empty.
        """
        entries_list = list(entries)
        if not entries_list:
            raise EmptyPostingError("add_entries called with no entries")

        self._session.add_all(entries_list)
        await self._session.flush(entries_list)

        log.info(
            "ledger_entries_posted",
            count=len(entries_list),
            # All entries normally share one transaction_id; log the first.
            transaction_id=str(entries_list[0].transaction_id),
        )
        return entries_list

    # ── Reads ────────────────────────────────────────────────────────

    async def list_by_transaction(
        self,
        transaction_id: uuid.UUID,
    ) -> Sequence[LedgerEntry]:
        """Return every entry posted under one ``transaction_id``.

        Entries are returned in (``posted_at``, ``entry_id``) order —
        deterministic and matches the natural read order.
        """
        stmt = (
            select(LedgerEntry)
            .where(LedgerEntry.transaction_id == transaction_id)
            .order_by(LedgerEntry.posted_at.asc(), LedgerEntry.entry_id.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def get_balance(
        self,
        account_id: uuid.UUID,
        currency: str,
    ) -> Decimal:
        """Compute the current balance for ``(account_id, currency)``.

        balance = SUM(CREDIT amounts) − SUM(DEBIT amounts)

        Returns ``Decimal('0')`` when the account has never been
        posted to. Computed server-side in a single round trip using
        a conditional ``SUM``.
        """
        # CASE expression projects DEBITs as negative; SUM then yields
        # the net balance in one pass.
        signed = case(
            (LedgerEntry.direction == LedgerDirection.CREDIT, LedgerEntry.amount),
            else_=-LedgerEntry.amount,
        )
        stmt = (
            select(func.coalesce(func.sum(signed), 0))
            .where(LedgerEntry.account_id == account_id)
            .where(LedgerEntry.currency == currency)
        )
        result = await self._session.execute(stmt)
        value = result.scalar_one()
        # COALESCE(..., 0) returns an int when no rows; normalise to Decimal.
        return Decimal(value) if not isinstance(value, Decimal) else value

    async def list_account_history(
        self,
        account_id: uuid.UUID,
        currency: str,
        *,
        cursor_posted_at: datetime | None = None,
        cursor_entry_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> Sequence[LedgerEntry]:
        """Return entries for one account, newest first, keyset-paginated.

        The cursor is a ``(posted_at, entry_id)`` pair. Callers fetch
        the first page with no cursor, then pass the *last* row's
        ``posted_at``/``entry_id`` as the cursor for the next page.

        ``entry_id`` is included in the cursor to break ties when two
        entries share a ``posted_at`` timestamp (rare with ``now()``
        defaults but possible under high write rates).
        """
        if limit <= 0 or limit > 500:
            raise ValueError(f"limit must be in [1, 500]; got {limit}")

        stmt = (
            select(LedgerEntry)
            .where(LedgerEntry.account_id == account_id)
            .where(LedgerEntry.currency == currency)
        )

        # Keyset pagination: rows strictly *older* than the cursor.
        if cursor_posted_at is not None and cursor_entry_id is not None:
            stmt = stmt.where(
                # (posted_at, entry_id) < (cursor_posted_at, cursor_entry_id)
                # PostgreSQL row-value comparison expresses this directly.
                (LedgerEntry.posted_at < cursor_posted_at)
                | (
                    (LedgerEntry.posted_at == cursor_posted_at)
                    & (LedgerEntry.entry_id < cursor_entry_id)
                )
            )

        stmt = (
            stmt.order_by(
                LedgerEntry.posted_at.desc(),
                LedgerEntry.entry_id.desc(),
            )
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()


__all__ = [
    "LedgerRepository",
    "LedgerRepositoryError",
    "EmptyPostingError",
]
