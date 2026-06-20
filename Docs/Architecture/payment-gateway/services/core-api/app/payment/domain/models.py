"""Transaction ORM model — table ``core.transactions``.

This is the centrepiece of the payment domain. Each row represents one
movement of money: a P2P transfer, a merchant payment, or a refund
(refunds are themselves transactions with ``parent_transaction`` set).

Constraint and index choices follow PAYMENT_DOMAIN_DESIGN.md §2.3 and
are reasoned, not blanket:

- ``UNIQUE (merchant_id, idempotency_key)`` — idempotency is scoped per
  merchant; the same key from two tenants is not a conflict.
- Partial index on ``status WHERE status IN ('flagged','failed')`` —
  ops dashboards filter on these constantly; full-table index would
  bloat for the common 95%+ success rows.
- Partial index on settlement_status — same logic for settlement ops.
- GIN on ``metadata`` — supports the ``metadata @> '{"campaign_id":...}'``
  queries merchant analytics needs.
- Self-FK on ``parent_transaction`` — refunds and reversals link back to
  their original; we index only the non-null rows (the vast majority of
  transactions are not refunds).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import (
    Base,
    Money,
    PaymentMethod,
    SettleStatus,
    TimestampMixin,
    TxnStatus,
)
from app.db.enums import (
    kyc_status_enum,
    payment_method_enum,
    settle_status_enum,
    txn_status_enum,
)

if TYPE_CHECKING:
    from app.identity.domain.models import User


class Transaction(Base, TimestampMixin):
    """A single money-movement record. Immutable once terminal."""

    __tablename__ = "transactions"
    __table_args__ = (
        # Idempotency uniqueness is per-merchant — same key from two
        # merchants is not a conflict.
        UniqueConstraint(
            "merchant_id",
            "idempotency_key",
            name="uq_transactions_merchant_id_idempotency_key",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "fraud_score IS NULL OR (fraud_score >= 0 AND fraud_score <= 1)",
            name="fraud_score_range",
        ),
        # Hot-path partial indexes — small, fast, exactly what ops need.
        Index(
            "ix_transactions_status_partial",
            "status",
            "created_at",
            postgresql_where=text("status IN ('flagged', 'failed')"),
        ),
        Index(
            "ix_transactions_settlement_partial",
            "merchant_id",
            "settlement_status",
            postgresql_where=text("settlement_status IN ('pending', 'disputed')"),
        ),
        Index(
            "ix_transactions_metadata_gin",
            "metadata",
            postgresql_using="gin",
        ),
        Index(
            "ix_transactions_parent_partial",
            "parent_transaction",
            postgresql_where=text("parent_transaction IS NOT NULL"),
        ),
        {"schema": "core"},
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ── Parties ──────────────────────────────────────────────────────

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.users.user_id", ondelete="RESTRICT"),
        nullable=True,
        doc="Sender / payer. Nullable for merchant-initiated payouts.",
    )

    # No FK on merchant_id yet — the merchant table is owned by the
    # merchant package (not in scope for this turn). Plain text + index
    # is sufficient; FK is added in a follow-up migration.
    merchant_id: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Receiving merchant (NULL for P2P).",
    )

    # ── Money ────────────────────────────────────────────────────────

    amount: Mapped[Decimal] = mapped_column(
        Money,
        nullable=False,
        doc="Always positive; sign comes from ledger direction, not here.",
    )

    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)

    payment_method: Mapped[PaymentMethod] = mapped_column(
        payment_method_enum,
        nullable=False,
    )

    # ── State ────────────────────────────────────────────────────────

    status: Mapped[TxnStatus] = mapped_column(
        txn_status_enum,
        nullable=False,
        default=TxnStatus.pending,
        server_default=TxnStatus.pending.value,
        doc=(
            "Lifecycle state. Transitions are enforced in the service layer; "
            "the DB only enforces the *set* of values."
        ),
    )

    failure_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Human / provider-supplied failure description.",
    )

    fraud_score: Mapped[Decimal | None] = mapped_column(
        Money,  # Money is also a NUMERIC; precision serves us here too.
        nullable=True,
        doc="Range 0.000..1.000; CHECK constraint enforces bounds.",
    )

    chargeback_flag: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    # ── Settlement ───────────────────────────────────────────────────

    settlement_status: Mapped[SettleStatus] = mapped_column(
        settle_status_enum,
        nullable=False,
        default=SettleStatus.pending,
        server_default=SettleStatus.pending.value,
    )

    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # KYC at the moment of transaction — captured for audit. The user's
    # current kyc_status may differ; we keep both.
    kyc_status_at_time = mapped_column(
        kyc_status_enum,
        nullable=True,
        doc="Snapshot of the sender's KYC status at the time of this transaction.",
    )

    # ── Geo (for AML / FX) ───────────────────────────────────────────

    country_sender: Mapped[str | None] = mapped_column(CHAR(2), nullable=True)
    country_receiver: Mapped[str | None] = mapped_column(CHAR(2), nullable=True)

    # ── Idempotency ──────────────────────────────────────────────────

    idempotency_key: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc=(
            "Client-supplied UUID echoed back on success. Uniqueness is "
            "scoped per-merchant via the table-level UniqueConstraint."
        ),
    )

    # ── Cross-currency linkage ───────────────────────────────────────

    fx_quote_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        doc=(
            "Set when this transaction is one leg of a cross-currency pair "
            "(see PAYMENT_DOMAIN_DESIGN.md §11). Same id on both legs."
        ),
    )

    # ── Refund / reversal chain ──────────────────────────────────────

    parent_transaction: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("core.transactions.transaction_id", ondelete="RESTRICT"),
        nullable=True,
        doc="Original transaction this row refunds or reverses.",
    )

    # ── Free-form ────────────────────────────────────────────────────

    metadata_: Mapped[dict[str, Any]] = mapped_column(
        # `metadata` clashes with SQLAlchemy's Base.metadata attribute;
        # we name the Python attribute `metadata_` but the column stays
        # `metadata` in the DB via the `name=` arg.
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        doc="Caller-controlled JSON bag for analytics / correlation.",
    )

    # ── Indexed convenience columns ──────────────────────────────────
    # (`created_at` from TimestampMixin is already implicit in queries;
    # the indexes below ensure (user_id, created_at DESC) etc.)

    # Indexes defined here for clarity; partial indexes live in __table_args__.

    # ── Relationships ────────────────────────────────────────────────

    user: Mapped["User"] = relationship(
        "User",
        back_populates="transactions",
        lazy="raise_on_sql",
    )

    # Self-FK relationships for the refund chain.
    parent: Mapped["Transaction | None"] = relationship(
        "Transaction",
        remote_side="Transaction.transaction_id",
        back_populates="refunds",
        lazy="raise_on_sql",
    )

    refunds: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        back_populates="parent",
        lazy="raise_on_sql",
    )

    # ── Hybrid-style read helpers (no DB I/O) ────────────────────────

    @property
    def is_terminal(self) -> bool:
        """True once the transaction has settled into a final state."""
        return self.status in {TxnStatus.success, TxnStatus.failed, TxnStatus.reversed}

    @property
    def is_refund(self) -> bool:
        """True if this transaction is a refund of another."""
        return self.parent_transaction is not None

    # ── Hygiene ──────────────────────────────────────────────────────

    def __repr__(self) -> str:
        # status may be None pre-flush.
        status = self.status.value if self.status else "<unset>"
        return (
            f"<Transaction id={self.transaction_id} "
            f"amount={self.amount} {self.currency} "
            f"status={status}>"
        )


# Plain (non-partial) hot-path indexes are declared outside __table_args__
# to keep that block readable.
Index(
    "ix_transactions_user_id_created_at",
    Transaction.user_id,
    Transaction.created_at.desc(),
)
Index(
    "ix_transactions_merchant_id_created_at",
    Transaction.merchant_id,
    Transaction.created_at.desc(),
)


__all__ = ["Transaction"]
