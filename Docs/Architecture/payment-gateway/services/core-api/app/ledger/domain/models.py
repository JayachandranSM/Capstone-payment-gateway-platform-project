"""Ledger entry ORM model — table ``ledger.entries``.

Double-entry semantics: every transaction has **at least two** ledger
entries summing to zero (within a currency). The "sum(debits) ==
sum(credits) per transaction_id" invariant is enforced by the **ledger
service** under SERIALIZABLE isolation, not by a DB constraint — there
is no SQL CHECK that spans rows in PostgreSQL.

Hypothesis property tests
(``tests/unit/ledger/test_invariants.py``) continuously verify the
invariant on randomly generated postings.

Deliberate architectural choice — **no foreign key from
``ledger.entries.transaction_id`` to ``core.transactions``**:

    Per Architecture Review ADR-006, the ledger moves to its own
    Postgres cluster in production. Cross-schema FKs would block that
    move. We trade DB-enforced referential integrity for architectural
    mobility. Orphan prevention is the ledger service's job; the
    integration test suite has a "no orphan entries" check that runs
    on every PR.

Same logic for ``account_id``: it may be a ``core.wallets.wallet_id``
or a synthetic system-account UUID (suspense, FX, fees). The ledger
domain does not know about wallets at the type level.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CHAR, CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, LedgerDirection, Money, UTCDateTime
from app.db.enums import ledger_direction_enum
from sqlalchemy import func


class LedgerEntry(Base):
    """One leg of a double-entry posting.

    Notably *not* a ``TimestampMixin`` user — ledger entries are
    append-only and have a single ``posted_at`` timestamp; an
    ``updated_at`` field would be a lie (entries are immutable).
    """

    __tablename__ = "entries"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_entries_transaction_id", "transaction_id"),
        Index(
            "ix_entries_account_id_posted_at",
            "account_id",
            "posted_at",
        ),
        {"schema": "ledger"},
    )

    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        doc=(
            "Groups entries into a single posting. NOT a FK to "
            "core.transactions — see module docstring for rationale."
        ),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        doc=(
            "Wallet UUID or system-account UUID. Not FK-constrained — the "
            "ledger domain treats accounts opaquely."
        ),
    )

    direction: Mapped[LedgerDirection] = mapped_column(
        ledger_direction_enum,
        nullable=False,
        doc="DEBIT or CREDIT — sign of the leg.",
    )

    amount: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        doc="Always positive; direction encodes the sign.",
    )

    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)

    posted_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        server_default=func.now(),
        doc="Posting time (UTC). Immutable.",
    )

    # Self-reference: a reversal entry points at the entry it reverses.
    # ``RESTRICT`` because deleting the original would orphan the
    # reversal — and we never delete ledger rows anyway.
    reversal_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ledger.entries.entry_id", ondelete="RESTRICT"),
        nullable=True,
        doc="If set, this entry reverses another.",
    )

    # Self-FK relationship (uses remote_side to disambiguate).
    reverses: Mapped["LedgerEntry | None"] = relationship(
        "LedgerEntry",
        remote_side="LedgerEntry.entry_id",
        lazy="raise_on_sql",
    )

    # ── Hygiene ──────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<LedgerEntry id={self.entry_id} "
            f"txn={self.transaction_id} "
            f"{self.direction.value} {self.amount} {self.currency}>"
        )


__all__ = ["LedgerEntry"]
