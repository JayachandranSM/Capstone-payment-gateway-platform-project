"""Pydantic v2 schemas for the fraud-scoring endpoint.

Kept deliberately independent of core-api's SQLAlchemy models — the
ai-service has no database ORM and must stay import-clean.

Decimal amounts are serialised as strings (JSON ``"250.00"``) matching
the convention established in core-api's payment API schemas, so
callers don't need to convert between services.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ── Enums ─────────────────────────────────────────────────────────────────────


class PaymentMethod(str, Enum):
    """Mirrors core-api PaymentMethod without importing from it."""

    card = "card"
    bank_transfer = "bank_transfer"
    wallet = "wallet"
    upi = "upi"


class FraudDecision(str, Enum):
    """Three-tier outcome returned to the caller.

    The thresholds that map ``risk_score → decision`` are:
        allow   risk_score <  40
        review  risk_score <  75
        reject  risk_score >= 75

    These values are declared in ``rules.py`` as module constants so a
    single-line config change recalibrates the entire pipeline.
    """

    allow = "allow"
    review = "review"
    reject = "reject"


class RuleCategory(str, Enum):
    """Broad category a fired rule belongs to.

    Used by the frontend to group reasons in the fraud-review UI and by
    the evaluation harness to measure rule-family precision/recall.
    """

    amount = "amount"
    velocity = "velocity"
    geo = "geo"
    method = "method"
    merchant = "merchant"
    behaviour = "behaviour"
    identity = "identity"


# ── Request ───────────────────────────────────────────────────────────────────


class FraudScoreRequest(BaseModel):
    """Body for ``POST /v1/fraud/score``.

    All fields are required except ``metadata``, which is an open
    key-value bag for caller-controlled signals (device fingerprint,
    session age, etc.).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    transaction_id: uuid.UUID = Field(
        description="UUID of the transaction being scored."
    )
    user_id: uuid.UUID = Field(
        description="UUID of the initiating user."
    )
    merchant_id: str = Field(
        description='Merchant identifier, e.g. "m_acme".',
        min_length=1,
        max_length=64,
    )
    amount: Annotated[
        Decimal,
        Field(description='Transaction amount as a decimal string, e.g. "2500.00".'),
    ]
    currency: str = Field(
        description="ISO 4217 currency code, e.g. INR.",
        min_length=3,
        max_length=3,
    )
    payment_method: PaymentMethod
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Caller-supplied context: device_id, ip_address, country, "
            "hour_of_day, is_new_device, prior_failures, etc."
        ),
    )

    # ── Validators ────────────────────────────────────────────────────────

    @field_validator("amount", mode="before")
    @classmethod
    def _parse_amount(cls, v: Any) -> Decimal:
        if isinstance(v, bool):
            raise ValueError("amount must not be bool")
        if isinstance(v, float):
            raise ValueError(
                'Use a decimal string ("250.00") instead of a float '
                "to avoid precision loss."
            )
        if isinstance(v, Decimal):
            return v
        if isinstance(v, int):
            return Decimal(v)
        if isinstance(v, str):
            try:
                return Decimal(v)
            except InvalidOperation:
                raise ValueError(f"Cannot parse {v!r} as Decimal")
        raise TypeError(f"Expected str/int/Decimal for amount, got {type(v).__name__}")

    @field_validator("amount")
    @classmethod
    def _amount_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("amount must be > 0")
        return v

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        v = v.upper()
        if not v.isalpha() or len(v) != 3:
            raise ValueError("currency must be exactly 3 ASCII letters (ISO 4217)")
        return v


# ── Internal rule-hit representation ─────────────────────────────────────────


class RuleHit(BaseModel):
    """A single rule that fired and contributed to the score."""

    rule_id: str = Field(
        description="Machine-readable rule identifier, e.g. 'AMOUNT_LARGE_INR'."
    )
    category: RuleCategory
    weight: int = Field(
        ge=0, le=100,
        description="Score points this rule added (0–100).",
    )
    reason: str = Field(
        description="Human-readable explanation shown in the review UI."
    )
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw values that triggered the rule (for audit trail).",
    )


# ── Response ──────────────────────────────────────────────────────────────────


class FraudScoreResponse(BaseModel):
    """Response body for ``POST /v1/fraud/score``."""

    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())

    transaction_id: uuid.UUID
    user_id: uuid.UUID

    # ── Core scoring output ───────────────────────────────────────────────

    risk_score: int = Field(
        ge=0, le=100,
        description=(
            "Composite fraud risk score. "
            "0 = completely clean, 100 = certain fraud. "
            "allow < 40 ≤ review < 75 ≤ reject."
        ),
    )
    decision: FraudDecision = Field(
        description="allow | review | reject based on risk_score thresholds."
    )
    reasons: list[str] = Field(
        description=(
            "Ordered list of human-readable reasons why the score is what it is. "
            "Empty list means no rules fired."
        ),
    )

    # ── Detail for review UI and audit ───────────────────────────────────

    rule_hits: list[RuleHit] = Field(
        default_factory=list,
        description="Full detail of every rule that fired.",
    )
    explanation: str = Field(
        description=(
            "One-sentence narrative summary of the scoring decision. "
            "Generated by the LLM when available; templated otherwise."
        ),
    )

    # ── Metadata ─────────────────────────────────────────────────────────

    model_version: str = Field(
        description="Fraud model version string for audit / drift tracking.",
    )
    llm_used: bool = Field(
        description="True if the explanation was generated by the LLM.",
    )
    scored_at: str = Field(
        description="ISO-8601 UTC timestamp of when the score was computed.",
    )


__all__ = [
    "PaymentMethod",
    "FraudDecision",
    "RuleCategory",
    "FraudScoreRequest",
    "RuleHit",
    "FraudScoreResponse",
]
