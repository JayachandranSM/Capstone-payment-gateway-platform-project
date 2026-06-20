"""PostgreSQL ENUM types and their Python counterparts.

Each ENUM is declared once, scoped to the schema that owns the related
tables (``core`` for payment-domain enums, ``ledger`` for ledger.direction).
The same SQLAlchemy ``Enum`` instance is reused across columns so that
SQLAlchemy registers the type exactly once in ``MetaData``.

Why ``native_enum=True``:
    PostgreSQL ENUMs give us type safety at the database layer — a
    rogue UPDATE that tries to set ``status = 'unknown'`` fails at the
    DB, not silently corrupts data. CHECK constraints are looser.

Why ``str, enum.Enum``:
    Members are both real ``str`` values *and* enum members, so a route
    handler can compare ``status == TxnStatus.success`` *or*
    ``status == "success"`` without a cast. Pydantic serialises them
    cleanly to JSON strings.

Why ``values_callable=...``:
    Without it, SQLAlchemy stores the enum *name* in the DB rather than
    its *value* — fine when they match, a silent bug when they don't.
    Being explicit avoids that footgun.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


# ── Python enums ─────────────────────────────────────────────────────────


class PaymentMethod(str, enum.Enum):
    """How money was moved."""

    card = "card"
    bank_transfer = "bank_transfer"
    wallet = "wallet"
    upi = "upi"


class TxnStatus(str, enum.Enum):
    """Lifecycle of a ``core.transactions`` row.

    Allowed transitions are enforced in the service layer
    (``app/payment/domain/state_machine.py`` — to be added). The DB
    only enforces the *set* of values, not the *order*.
    """

    pending = "pending"
    success = "success"
    failed = "failed"
    flagged = "flagged"   # held for fraud review
    reversed = "reversed"


class SettleStatus(str, enum.Enum):
    """Settlement state for a transaction (orthogonal to ``TxnStatus``)."""

    settled = "settled"
    pending = "pending"
    disputed = "disputed"
    reversed = "reversed"


class KycStatus(str, enum.Enum):
    """User KYC verification state."""

    verified = "verified"
    pending = "pending"
    failed = "failed"


class LedgerDirection(str, enum.Enum):
    """Sign of a ``ledger.entries`` posting."""

    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


# ── SQLAlchemy ENUM type bindings ────────────────────────────────────────
# These bind a Python enum to a PostgreSQL ENUM type declared in a
# specific schema. ``create_type=True`` lets ``Base.metadata.create_all``
# create the type during tests; Alembic migrations should set
# ``create_type=False`` and own the DDL directly.


def _enum(py_enum: type[enum.Enum], *, name: str, schema: str) -> SAEnum:
    return SAEnum(
        py_enum,
        name=name,
        schema=schema,
        native_enum=True,
        create_type=True,
        values_callable=lambda members: [m.value for m in members],
    )


payment_method_enum = _enum(PaymentMethod, name="payment_method", schema="core")
txn_status_enum = _enum(TxnStatus, name="txn_status", schema="core")
settle_status_enum = _enum(SettleStatus, name="settle_status", schema="core")
kyc_status_enum = _enum(KycStatus, name="kyc_status", schema="core")
ledger_direction_enum = _enum(LedgerDirection, name="direction", schema="ledger")


__all__ = [
    "PaymentMethod",
    "TxnStatus",
    "SettleStatus",
    "KycStatus",
    "LedgerDirection",
    "payment_method_enum",
    "txn_status_enum",
    "settle_status_enum",
    "kyc_status_enum",
    "ledger_direction_enum",
]
