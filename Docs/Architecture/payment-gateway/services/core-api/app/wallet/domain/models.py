"""Wallet ORM model — table ``core.wallets``.

A wallet is a (user, currency) pair with a non-negative balance. Per
PAYMENT_DOMAIN_DESIGN.md §11, cross-currency operations *never* move
money inside a single wallet — they post two transactions linked by an
``fx_quote_id``. This keeps wallet semantics dead simple: one currency,
one balance, one invariant.

The ``version`` column implements **optimistic concurrency control**.
The wallet service must read the current ``version``, post its update
with ``WHERE version = :read_version``, and verify ``rowcount == 1``.
If two requests race, exactly one wins; the loser sees ``rowcount == 0``
and retries. This avoids the row-level write lock that ``SELECT FOR
UPDATE`` would impose under high contention.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CHAR, CheckConstraint, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, Money, TimestampMixin

if TYPE_CHECKING:
    from app.identity.domain.models import User


class Wallet(Base, TimestampMixin):
    """Balance ledger for one (user, currency) pair."""

    __tablename__ = "wallets"
    __table_args__ = (
        UniqueConstraint("user_id", "currency", name="uq_wallets_user_id_currency"),
        CheckConstraint("balance >= 0", name="balance_non_negative"),
        {"schema": "core"},
    )

    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.users.user_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        doc="Owning user; RESTRICT prevents user deletion while wallets exist.",
    )

    currency: Mapped[str] = mapped_column(
        CHAR(3),
        nullable=False,
        doc="ISO 4217 currency code; validated at the API boundary.",
    )

    balance: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        default=Decimal("0"),
        server_default="0",
        doc="Current balance; the CHECK constraint enforces non-negativity.",
    )

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        doc=(
            "Optimistic-lock counter. Service must increment on every update "
            "and verify rowcount==1 on the conditional UPDATE."
        ),
    )

    # ── Relationships ────────────────────────────────────────────────

    user: Mapped["User"] = relationship(
        "User",
        back_populates="wallets",
        lazy="raise_on_sql",
    )

    # NOTE: There is intentionally *no* relationship to LedgerEntry.
    # Ledger entries reference ``account_id`` which may be a wallet UUID
    # or a system-account UUID (suspense, FX, fees). The ledger schema
    # also targets a separate cluster in production (Architecture Review
    # ADR-006), so ORM-level joins from core into ledger are forbidden.

    # ── Hygiene ──────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<Wallet wallet_id={self.wallet_id} "
            f"currency={self.currency} balance={self.balance} v={self.version}>"
        )


__all__ = ["Wallet"]
