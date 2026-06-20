"""Pydantic v2 schemas for the payment API layer.

Design principles applied here:

- **Decimal-as-string in JSON.** JavaScript's ``Number`` is IEEE 754
  double; it cannot represent ``Decimal("0.1")`` exactly. Every amount
  field uses ``Annotated[Decimal, ...]`` with a custom serialiser that
  emits a string (e.g. ``"250.0000"``) and accepts both strings and
  numbers on input. Clients must treat amounts as opaque strings and
  never do arithmetic on the JSON value.

- **ORM models are never returned directly.** ``TransactionResponse``
  is built from the ``Transaction`` ORM instance via
  ``TransactionResponse.from_orm(txn)`` (Pydantic v2:
  ``model_validate(txn, from_attributes=True)``). This firewall means
  adding a column to the ORM model does not accidentally expose it in
  the API.

- **Cursors are opaque base64 strings.** The underlying cursor is a
  ``(created_at_iso, transaction_id_hex)`` pair JSON-encoded and
  base64url-encoded. Clients must not parse or construct cursors; they
  must pass the ``next_cursor`` value verbatim on the next request.

- **RFC 7807 problem details.** Error responses use
  ``application/problem+json`` with a ``type`` URI, ``title``,
  ``status``, ``detail``, and an optional ``errors`` list for
  field-level validation failures.

- **``metadata`` (no trailing underscore).** The ORM attribute is
  ``metadata_`` to avoid collision with SQLAlchemy; the API exposes it
  as ``metadata``. Both directions of the conversion are explicit.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.db import PaymentMethod, SettleStatus, TxnStatus


# ── Shared types ─────────────────────────────────────────────────────────


def _validate_decimal_string(v: Any) -> Decimal:
    """Accept str | int | Decimal; reject float; normalise to Decimal."""
    if isinstance(v, bool):
        raise ValueError("amount must be a numeric string or Decimal, not bool")
    if isinstance(v, float):
        raise ValueError(
            "float is rejected to avoid precision loss — use a string instead, "
            'e.g. "250.00"'
        )
    if isinstance(v, Decimal):
        return v
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, str):
        try:
            return Decimal(v)
        except InvalidOperation:
            raise ValueError(f"cannot parse {v!r} as Decimal")
    raise ValueError(f"expected str, int, or Decimal; got {type(v).__name__}")


# Annotated type that validates and serialises amounts as strings.
DecimalAmount = Annotated[
    Decimal,
    Field(
        description='Monetary amount as a decimal string, e.g. "250.0000". '
        "Never use a JSON number — floating-point cannot represent money exactly.",
    ),
]

# ISO 4217 shape: exactly 3 uppercase letters.
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

# Merchant-id shape matching the service-layer regex.
_MERCHANT_ID_RE = re.compile(r"^m_[A-Za-z0-9_]{1,32}$")

# Idempotency-key shape matching the service-layer regex.
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


# ── Problem Details (RFC 7807) ────────────────────────────────────────────


class FieldError(BaseModel):
    """One field-level validation error inside a problem-details envelope."""

    field: str
    message: str


class ProblemDetail(BaseModel):
    """RFC 7807 problem details object.

    ``type`` is a URI identifying the error class. In production this
    resolves to documentation; in MVP it is a namespace placeholder.
    ``detail`` is a human-readable sentence; ``errors`` carries
    per-field breakdowns for validation failures.
    """

    model_config = ConfigDict(populate_by_name=True)

    type: str = Field(
        default="about:blank",
        description="URI identifying the error class.",
    )
    title: str
    status: int
    detail: str
    instance: str | None = Field(
        default=None,
        description="URI of the specific request that caused the error.",
    )
    errors: list[FieldError] | None = Field(
        default=None,
        description="Per-field errors for 422 validation failures.",
    )


# ── Cursor helpers ────────────────────────────────────────────────────────


class _CursorPayload(BaseModel):
    """Internal structure hidden behind an opaque base64url cursor string."""

    created_at: str   # ISO-8601 with timezone
    txn_id: str       # hex UUID


def encode_cursor(created_at: datetime, transaction_id: uuid.UUID) -> str:
    """Encode a ``(created_at, transaction_id)`` pair as a base64url string."""
    payload = _CursorPayload(
        created_at=created_at.astimezone(timezone.utc).isoformat(),
        txn_id=transaction_id.hex,
    )
    raw = json.dumps(payload.model_dump(), separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Decode a base64url cursor string.

    Returns:
        ``(created_at, transaction_id)`` ready to pass to the repository.

    Raises:
        ValueError: if the cursor is malformed.
    """
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding)
        data = json.loads(raw)
        payload = _CursorPayload.model_validate(data)
        created_at = datetime.fromisoformat(payload.created_at)
        txn_id = uuid.UUID(payload.txn_id)
        return created_at, txn_id
    except Exception as exc:
        raise ValueError(f"invalid cursor: {exc}") from exc


# ── Request schemas ───────────────────────────────────────────────────────


class CreatePaymentRequest(BaseModel):
    """Body for ``POST /v1/payments``.

    ``idempotency_key`` is also accepted as the ``Idempotency-Key``
    header (preferred) or in the request body (convenience for clients
    that cannot set custom headers). If both are present they must match.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    user_id: uuid.UUID = Field(description="Paying user.")
    merchant_id: str = Field(description="Receiving merchant, e.g. ``m_acme``.")
    amount: DecimalAmount
    currency: str = Field(
        description="ISO 4217 currency code, e.g. ``INR``.",
        min_length=3,
        max_length=3,
    )
    payment_method: PaymentMethod
    idempotency_key: str | None = Field(
        default=None,
        description="Idempotency key from request body; overridden by header if present.",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Caller-controlled key-value bag (stored as JSONB).",
    )

    # ── Validators ───────────────────────────────────────────────────

    @field_validator("amount", mode="before")
    @classmethod
    def parse_amount(cls, v: Any) -> Decimal:
        d = _validate_decimal_string(v)
        if d <= Decimal("0"):
            raise ValueError("amount must be greater than 0")
        return d

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        if not _CURRENCY_RE.match(v):
            raise ValueError("currency must be exactly 3 uppercase ASCII letters (ISO 4217)")
        return v

    @field_validator("merchant_id")
    @classmethod
    def validate_merchant_id(cls, v: str) -> str:
        if not _MERCHANT_ID_RE.match(v):
            raise ValueError(
                "merchant_id must match m_<1-32 alphanumeric/underscore> "
                f"(got {v!r})"
            )
        return v

    @field_validator("idempotency_key")
    @classmethod
    def validate_idempotency_key(cls, v: str | None) -> str | None:
        if v is not None and not _IDEMPOTENCY_KEY_RE.match(v):
            raise ValueError(
                "idempotency_key must be 8-64 alphanumeric/hyphen/underscore characters"
            )
        return v


class ListPaymentsRequest(BaseModel):
    """Validated query parameters for ``GET /v1/payments``.

    Instances are built from FastAPI ``Query`` parameters in the route
    handler; this model is used for secondary validation and
    documentation only.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    merchant_id: str | None = None
    user_id: uuid.UUID | None = None
    status: TxnStatus | None = None
    from_date: datetime | None = None
    to_date: datetime | None = None
    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


# ── Response schemas ──────────────────────────────────────────────────────


class TransactionResponse(BaseModel):
    """Full transaction representation returned by POST and GET endpoints.

    The ``metadata`` field maps from the ORM's ``metadata_`` attribute
    via the ``model_validator``.
    """

    model_config = ConfigDict(
        from_attributes=True,   # enables model_validate(orm_instance, from_attributes=True)
        populate_by_name=True,
    )

    transaction_id: uuid.UUID
    user_id: uuid.UUID | None
    merchant_id: str | None
    amount: DecimalAmount
    currency: str
    payment_method: PaymentMethod
    status: TxnStatus
    failure_reason: str | None = None
    fraud_score: DecimalAmount | None = None
    chargeback_flag: bool
    settlement_status: SettleStatus
    idempotency_key: str | None = None
    parent_transaction: uuid.UUID | None = None
    fx_quote_id: uuid.UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    # ── ORM bridge ───────────────────────────────────────────────────

    @model_validator(mode="before")
    @classmethod
    def _lift_metadata_attr(cls, data: Any) -> Any:
        """Map ``metadata_`` (ORM attribute) → ``metadata`` (API field).

        When Pydantic reads from an ORM instance, ``model_validate``
        iterates attribute names. The ORM column is named ``metadata_``
        in Python to avoid colliding with ``DeclarativeBase.metadata``.
        This validator reads ``metadata_`` and writes it as ``metadata``
        so the field mapping works transparently.
        """
        # When called with a dict (e.g. from tests) pass through as-is.
        if isinstance(data, dict):
            return data
        # ORM instance path: copy attributes we care about into a dict,
        # renaming metadata_ → metadata.
        obj: dict[str, Any] = {}
        for field_name in [
            "transaction_id", "user_id", "merchant_id", "amount",
            "currency", "payment_method", "status", "failure_reason",
            "fraud_score", "chargeback_flag", "settlement_status",
            "idempotency_key", "parent_transaction", "fx_quote_id",
            "created_at", "updated_at",
        ]:
            obj[field_name] = getattr(data, field_name, None)
        # The ORM attr is ``metadata_``; expose it as ``metadata``.
        obj["metadata"] = getattr(data, "metadata_", {}) or {}
        return obj

    # ── Amount serialisers ────────────────────────────────────────────

    @field_serializer("amount")
    def _ser_amount(self, v: Decimal) -> str:
        return str(v)

    @field_serializer("fraud_score")
    def _ser_fraud_score(self, v: Decimal | None) -> str | None:
        return str(v) if v is not None else None

    @field_validator("amount", mode="before")
    @classmethod
    def _parse_amount(cls, v: Any) -> Decimal:
        return _validate_decimal_string(v)

    @field_validator("fraud_score", mode="before")
    @classmethod
    def _parse_fraud_score(cls, v: Any) -> Decimal | None:
        if v is None:
            return None
        return _validate_decimal_string(v)

    # ── Datetime serialisers ─────────────────────────────────────────

    @field_serializer("created_at", "updated_at")
    def _ser_datetime(self, v: datetime) -> str:
        # Always emit UTC with Z suffix — no offsets in API responses.
        return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


class PagedTransactionResponse(BaseModel):
    """Envelope for ``GET /v1/payments``."""

    items: list[TransactionResponse]
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Opaque base64url cursor. Pass as ``?cursor=`` on the next request "
            "to fetch the following page. ``null`` means no further pages."
        ),
    )
    count: int = Field(description="Number of items in this page.")


__all__ = [
    # Request
    "CreatePaymentRequest",
    "ListPaymentsRequest",
    # Response
    "TransactionResponse",
    "PagedTransactionResponse",
    # Errors
    "ProblemDetail",
    "FieldError",
    # Cursor helpers
    "encode_cursor",
    "decode_cursor",
    # Shared types
    "DecimalAmount",
]
