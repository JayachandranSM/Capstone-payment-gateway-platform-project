"""User ORM model — table ``core.users``.

A minimal identity record covering the payment domain's needs:
authentication credentials, KYC status, country (for AML decisions),
and audit timestamps. MFA, sessions, GDPR data-subject metadata etc.
will be added by the identity-domain implementation in a later phase
without touching this base shape.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CHAR, String
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, KycStatus, TimestampMixin
from app.db.enums import kyc_status_enum

if TYPE_CHECKING:
    # Avoid runtime circular imports — these are needed only for typing.
    from app.payment.domain.models import Transaction
    from app.wallet.domain.models import Wallet


class User(Base, TimestampMixin):
    """End-user account. Owns wallets; initiates transactions."""

    __tablename__ = "users"
    __table_args__ = {"schema": "core"}

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        doc="Internal user identifier; never exposed to other tenants.",
    )

    # CITEXT requires the citext extension (CREATE EXTENSION IF NOT EXISTS citext).
    # Add to infra/postgres/init.sql alongside pgcrypto and pgvector.
    email: Mapped[str] = mapped_column(
        CITEXT,
        nullable=False,
        unique=True,
        doc="Login email; case-insensitive uniqueness via CITEXT.",
    )

    password_hash: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="bcrypt or argon2 hash; never the plaintext.",
    )

    kyc_status: Mapped[KycStatus] = mapped_column(
        kyc_status_enum,
        nullable=False,
        default=KycStatus.pending,
        server_default=KycStatus.pending.value,
        doc="KYC verification state; gates higher-risk operations.",
    )

    country: Mapped[str] = mapped_column(
        CHAR(2),
        nullable=False,
        doc="ISO 3166-1 alpha-2 country code; validated at the API boundary.",
    )

    # ── Relationships ────────────────────────────────────────────────
    # lazy="raise_on_sql": accessing the attribute triggers a runtime
    # error if SQLAlchemy would have to emit a query to populate it.
    # Forces explicit eager loading at the call site (selectinload /
    # joinedload), which prevents N+1 surprises.

    wallets: Mapped[list["Wallet"]] = relationship(
        "Wallet",
        back_populates="user",
        lazy="raise_on_sql",
        cascade="save-update",  # do NOT cascade delete — financial data is immutable
        passive_deletes=True,
    )

    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction",
        back_populates="user",
        lazy="raise_on_sql",
    )

    # ── Hygiene ──────────────────────────────────────────────────────

    def __repr__(self) -> str:
        # Email is PII; never include the full value in logs/repr.
        # kyc_status may be None pre-flush (Python defaults apply on flush).
        kyc = self.kyc_status.value if self.kyc_status else "<unset>"
        return f"<User user_id={self.user_id} email=*** kyc={kyc}>"


__all__ = ["User"]
