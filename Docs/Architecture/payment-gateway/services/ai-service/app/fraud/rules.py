"""Deterministic fraud rule engine.

Architecture
------------
Every rule is a pure function with the signature::

    def rule_<NAME>(req: FraudScoreRequest) -> RuleHit | None

A ``None`` return means the rule did not fire. A ``RuleHit`` carries the
rule's weight (score contribution) and human-readable reason.

``evaluate(req)`` runs all rules, sums their weights (capped at 100),
and returns the list of hits. The caller (``FraudScoringService``)
converts the total into a decision.

Score thresholds
----------------
These three constants are the *only* place where thresholds live.
Changing them recalibrates the entire pipeline instantly.

    THRESHOLD_REVIEW  = 40   # score >= this → review instead of allow
    THRESHOLD_REJECT  = 75   # score >= this → reject instead of review

Rule design principles
----------------------
1. **Independent**: each rule fires or not based solely on the request; no
   rule reads the output of another.
2. **Weighted, not binary**: weights reflect severity. A single high-weight
   rule can push a transaction into review; several low-weight ones combine
   to do the same, mimicking real fraud signals being correlated.
3. **Evidence captured**: every hit records the raw value that triggered it
   so the audit log and review UI can show "amount was ₹2 40 000" rather
   than just "large amount".
4. **Tunable without code review**: weights and thresholds are module-level
   constants, not magic numbers scattered across if-statements.
5. **No external I/O**: rules are synchronous and pure. Async context
   (velocity checks against Redis, etc.) belongs in ``service.py``.

Coverage — 15 rules across 6 categories
----------------------------------------
Amount (3):
    AMOUNT_LARGE_INR          High-value INR transaction
    AMOUNT_LARGE_USD_EUR      High-value USD/EUR transaction
    AMOUNT_ROUND_SUSPICIOUS   Psychologically round amounts (known mule pattern)

Velocity (3) — based on metadata hints from caller:
    VELOCITY_PRIOR_FAILURES   User has recent payment failures
    VELOCITY_HIGH_FREQ        Unusually high transaction frequency
    VELOCITY_NEW_ACCOUNT      Very new account transacting large amounts

Geo (2):
    GEO_HIGH_RISK_COUNTRY     Receiver country on elevated-risk list
    GEO_CROSS_BORDER          International transaction (mild signal)

Method (2):
    METHOD_BANK_LARGE         Large bank-transfer (higher irreversibility risk)
    METHOD_CARD_FOREIGN       Card payment with foreign country signal

Merchant (2):
    MERCHANT_HIGH_RISK_CAT    Merchant category known for chargebacks
    MERCHANT_NEW_NO_HISTORY   First-seen merchant with no transaction history

Behaviour (3):
    BEHAVIOUR_ODD_HOUR        Transaction at unusual hour (02:00–05:00 local)
    BEHAVIOUR_NEW_DEVICE      Payment from a device the user has never used
    BEHAVIOUR_METADATA_SPARSE Missing contextual signals (device, IP, etc.)
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Final

from app.fraud.schemas import FraudScoreRequest, PaymentMethod, RuleCategory, RuleHit

# ── Decision thresholds ───────────────────────────────────────────────────────

THRESHOLD_REVIEW: Final[int] = 40
THRESHOLD_REJECT: Final[int] = 75

# ── Amount thresholds (currency-specific) ─────────────────────────────────────

_LARGE_INR: Final[Decimal] = Decimal("100_000")    # ₹1 lakh
_LARGE_USD: Final[Decimal] = Decimal("10_000")     # $10 000
_LARGE_EUR: Final[Decimal] = Decimal("10_000")     # €10 000
_LARGE_DEFAULT: Final[Decimal] = Decimal("50_000") # generic

# Round-number multiples that often indicate structured cash placement.
_ROUND_MULTIPLES: Final[tuple[Decimal, ...]] = (
    Decimal("50000"), Decimal("100000"), Decimal("500000"), Decimal("1000000"),
)
_ROUND_USD_EUR: Final[tuple[Decimal, ...]] = (
    Decimal("5000"), Decimal("10000"), Decimal("50000"), Decimal("100000"),
)

# ── High-risk countries (ISO 3166-1 alpha-2, FATF grey/black list proxies) ───

_HIGH_RISK_COUNTRIES: Final[frozenset[str]] = frozenset({
    "AF", "BY", "CF", "CD", "CU", "ER", "ET", "GN", "GW", "HT", "IR",
    "IQ", "KP", "LB", "LY", "ML", "MM", "NI", "PK", "RU", "SO", "SS",
    "SD", "SY", "VE", "YE", "ZW",
})

# ── Merchant categories known for elevated chargeback rates ──────────────────

_HIGH_RISK_MERCHANT_PREFIXES: Final[tuple[str, ...]] = (
    "m_gambling", "m_crypto", "m_forex", "m_casino",
    "m_adult", "m_travel_unregulated",
)

# ── Odd-hour window (local-hour hints, if caller provides hour_of_day) ───────

_ODD_HOUR_START: Final[int] = 2    # 02:00 inclusive
_ODD_HOUR_END: Final[int] = 5      # 05:00 exclusive

# ── Rule registry ─────────────────────────────────────────────────────────────

# Each entry is a zero-arg Callable[[FraudScoreRequest], RuleHit | None].
# Rules are appended below as they are defined.
_RULES: list[Callable[[FraudScoreRequest], RuleHit | None]] = []


def _rule(fn: Callable[[FraudScoreRequest], RuleHit | None]):
    """Decorator that registers a rule function into ``_RULES``."""
    _RULES.append(fn)
    return fn


# ═══════════════════════════════════════════════════════════════════════════
# Amount rules
# ═══════════════════════════════════════════════════════════════════════════


@_rule
def rule_AMOUNT_LARGE_INR(req: FraudScoreRequest) -> RuleHit | None:
    if req.currency != "INR":
        return None
    if req.amount < _LARGE_INR:
        return None
    # Graduated weight: 25 for 1–5 lakh, 40 above 5 lakh.
    weight = 40 if req.amount >= Decimal("500_000") else 25
    return RuleHit(
        rule_id="AMOUNT_LARGE_INR",
        category=RuleCategory.amount,
        weight=weight,
        reason=f"Transaction amount ₹{req.amount:,.2f} exceeds high-value threshold (₹{_LARGE_INR:,.0f})",
        evidence={"amount": str(req.amount), "threshold": str(_LARGE_INR), "currency": "INR"},
    )


@_rule
def rule_AMOUNT_LARGE_USD_EUR(req: FraudScoreRequest) -> RuleHit | None:
    threshold = {"USD": _LARGE_USD, "EUR": _LARGE_EUR}.get(req.currency)
    if threshold is None or req.amount < threshold:
        return None
    weight = 35 if req.amount >= threshold * 5 else 20
    symbol = "$" if req.currency == "USD" else "€"
    return RuleHit(
        rule_id="AMOUNT_LARGE_USD_EUR",
        category=RuleCategory.amount,
        weight=weight,
        reason=f"Transaction amount {symbol}{req.amount:,.2f} exceeds high-value threshold ({symbol}{threshold:,.0f})",
        evidence={"amount": str(req.amount), "threshold": str(threshold), "currency": req.currency},
    )


@_rule
def rule_AMOUNT_ROUND_SUSPICIOUS(req: FraudScoreRequest) -> RuleHit | None:
    multiples = _ROUND_USD_EUR if req.currency in ("USD", "EUR") else _ROUND_MULTIPLES
    for multiple in multiples:
        if req.amount >= multiple and req.amount % multiple == 0:
            return RuleHit(
                rule_id="AMOUNT_ROUND_SUSPICIOUS",
                category=RuleCategory.amount,
                weight=15,
                reason=(
                    f"Amount {req.amount} is a psychologically round multiple of {multiple} "
                    "— a known pattern in structured cash placement."
                ),
                evidence={"amount": str(req.amount), "matched_multiple": str(multiple)},
            )
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Velocity rules  (rely on caller-supplied metadata hints)
# ═══════════════════════════════════════════════════════════════════════════


@_rule
def rule_VELOCITY_PRIOR_FAILURES(req: FraudScoreRequest) -> RuleHit | None:
    """Caller should supply ``metadata.prior_failures`` (int) if known."""
    prior = req.metadata.get("prior_failures")
    if prior is None:
        return None
    try:
        n = int(prior)
    except (TypeError, ValueError):
        return None
    if n < 2:
        return None
    weight = min(10 * n, 35)   # caps at 35 — 3+ failures already push into review
    return RuleHit(
        rule_id="VELOCITY_PRIOR_FAILURES",
        category=RuleCategory.velocity,
        weight=weight,
        reason=f"User has {n} recent payment failure(s) in the current session window.",
        evidence={"prior_failures": n},
    )


@_rule
def rule_VELOCITY_HIGH_FREQ(req: FraudScoreRequest) -> RuleHit | None:
    """Caller supplies ``metadata.txns_last_hour`` (int) if known."""
    freq = req.metadata.get("txns_last_hour")
    if freq is None:
        return None
    try:
        n = int(freq)
    except (TypeError, ValueError):
        return None
    if n < 5:
        return None
    weight = 20 if n < 10 else 35
    return RuleHit(
        rule_id="VELOCITY_HIGH_FREQ",
        category=RuleCategory.velocity,
        weight=weight,
        reason=f"User initiated {n} transactions in the last hour (threshold: 5).",
        evidence={"txns_last_hour": n, "threshold": 5},
    )


@_rule
def rule_VELOCITY_NEW_ACCOUNT(req: FraudScoreRequest) -> RuleHit | None:
    """Caller supplies ``metadata.account_age_days`` (int) if known."""
    age = req.metadata.get("account_age_days")
    if age is None:
        return None
    try:
        age_days = int(age)
    except (TypeError, ValueError):
        return None
    if age_days > 30:
        return None
    # Only penalise if the amount is also significant.
    large = {
        "INR": Decimal("25_000"),
        "USD": Decimal("2_500"),
        "EUR": Decimal("2_500"),
    }.get(req.currency, Decimal("10_000"))
    if req.amount < large:
        return None
    weight = 25 if age_days <= 7 else 15
    return RuleHit(
        rule_id="VELOCITY_NEW_ACCOUNT",
        category=RuleCategory.velocity,
        weight=weight,
        reason=(
            f"Account is {age_days} day(s) old and is attempting a "
            f"significant transaction ({req.currency} {req.amount:,.2f})."
        ),
        evidence={"account_age_days": age_days, "amount": str(req.amount)},
    )


# ═══════════════════════════════════════════════════════════════════════════
# Geo rules
# ═══════════════════════════════════════════════════════════════════════════


@_rule
def rule_GEO_HIGH_RISK_COUNTRY(req: FraudScoreRequest) -> RuleHit | None:
    """Fires when ``metadata.country_receiver`` or ``metadata.country`` is high-risk."""
    country = (
        req.metadata.get("country_receiver")
        or req.metadata.get("country")
        or ""
    ).upper()
    if not country or country not in _HIGH_RISK_COUNTRIES:
        return None
    return RuleHit(
        rule_id="GEO_HIGH_RISK_COUNTRY",
        category=RuleCategory.geo,
        weight=30,
        reason=f"Destination country ({country}) is on the elevated-risk list (FATF-aligned).",
        evidence={"country": country},
    )


@_rule
def rule_GEO_CROSS_BORDER(req: FraudScoreRequest) -> RuleHit | None:
    """Mild signal: sender and receiver in different countries."""
    sender = (req.metadata.get("country_sender") or "").upper()
    receiver = (req.metadata.get("country_receiver") or "").upper()
    if not sender or not receiver or sender == receiver:
        return None
    if sender in _HIGH_RISK_COUNTRIES or receiver in _HIGH_RISK_COUNTRIES:
        return None   # covered by the stronger GEO_HIGH_RISK_COUNTRY rule
    return RuleHit(
        rule_id="GEO_CROSS_BORDER",
        category=RuleCategory.geo,
        weight=10,
        reason=f"Cross-border transaction: sender={sender}, receiver={receiver}.",
        evidence={"country_sender": sender, "country_receiver": receiver},
    )


# ═══════════════════════════════════════════════════════════════════════════
# Method rules
# ═══════════════════════════════════════════════════════════════════════════


@_rule
def rule_METHOD_BANK_LARGE(req: FraudScoreRequest) -> RuleHit | None:
    if req.payment_method is not PaymentMethod.bank_transfer:
        return None
    threshold = {"INR": Decimal("500_000"), "USD": Decimal("50_000")}.get(
        req.currency, Decimal("100_000")
    )
    if req.amount < threshold:
        return None
    return RuleHit(
        rule_id="METHOD_BANK_LARGE",
        category=RuleCategory.method,
        weight=20,
        reason=(
            f"Large bank transfer ({req.currency} {req.amount:,.2f}) is harder to reverse "
            "and carries elevated money-laundering risk."
        ),
        evidence={"amount": str(req.amount), "method": "bank_transfer"},
    )


@_rule
def rule_METHOD_CARD_FOREIGN(req: FraudScoreRequest) -> RuleHit | None:
    """Card transaction where the card's country differs from the merchant's country."""
    if req.payment_method is not PaymentMethod.card:
        return None
    card_country = (req.metadata.get("card_country") or "").upper()
    merchant_country = (req.metadata.get("merchant_country") or "").upper()
    if not card_country or not merchant_country or card_country == merchant_country:
        return None
    return RuleHit(
        rule_id="METHOD_CARD_FOREIGN",
        category=RuleCategory.method,
        weight=15,
        reason=f"Card issued in {card_country} used at merchant in {merchant_country}.",
        evidence={"card_country": card_country, "merchant_country": merchant_country},
    )


# ═══════════════════════════════════════════════════════════════════════════
# Merchant rules
# ═══════════════════════════════════════════════════════════════════════════


@_rule
def rule_MERCHANT_HIGH_RISK_CAT(req: FraudScoreRequest) -> RuleHit | None:
    mid = req.merchant_id.lower()
    for prefix in _HIGH_RISK_MERCHANT_PREFIXES:
        if mid.startswith(prefix):
            return RuleHit(
                rule_id="MERCHANT_HIGH_RISK_CAT",
                category=RuleCategory.merchant,
                weight=30,
                reason=f"Merchant '{req.merchant_id}' is in a category with elevated chargeback rates.",
                evidence={"merchant_id": req.merchant_id, "matched_prefix": prefix},
            )
    return None


@_rule
def rule_MERCHANT_NEW_NO_HISTORY(req: FraudScoreRequest) -> RuleHit | None:
    """Caller supplies ``metadata.merchant_txn_count`` if known (from Redis lookup)."""
    count = req.metadata.get("merchant_txn_count")
    if count is None:
        return None
    try:
        n = int(count)
    except (TypeError, ValueError):
        return None
    if n > 5:
        return None
    return RuleHit(
        rule_id="MERCHANT_NEW_NO_HISTORY",
        category=RuleCategory.merchant,
        weight=20,
        reason=f"Merchant '{req.merchant_id}' has only {n} prior transaction(s) in the system.",
        evidence={"merchant_id": req.merchant_id, "merchant_txn_count": n},
    )


# ═══════════════════════════════════════════════════════════════════════════
# Behaviour rules
# ═══════════════════════════════════════════════════════════════════════════


@_rule
def rule_BEHAVIOUR_ODD_HOUR(req: FraudScoreRequest) -> RuleHit | None:
    """Caller supplies ``metadata.hour_of_day`` (0–23, local hour) if known."""
    hour = req.metadata.get("hour_of_day")
    if hour is None:
        return None
    try:
        h = int(hour)
    except (TypeError, ValueError):
        return None
    if not (0 <= h <= 23):
        return None
    if not (_ODD_HOUR_START <= h < _ODD_HOUR_END):
        return None
    return RuleHit(
        rule_id="BEHAVIOUR_ODD_HOUR",
        category=RuleCategory.behaviour,
        weight=12,
        reason=f"Transaction initiated at {h:02d}:xx local time (unusual activity window 02:00–05:00).",
        evidence={"hour_of_day": h},
    )


@_rule
def rule_BEHAVIOUR_NEW_DEVICE(req: FraudScoreRequest) -> RuleHit | None:
    """Caller supplies ``metadata.is_new_device`` (bool) if known."""
    is_new = req.metadata.get("is_new_device")
    if is_new is None:
        return None
    if not is_new:
        return None
    weight = 20
    # Extra weight if the amount is also large.
    threshold = {"INR": Decimal("50_000"), "USD": Decimal("5_000")}.get(
        req.currency, Decimal("20_000")
    )
    if req.amount >= threshold:
        weight = 30
    return RuleHit(
        rule_id="BEHAVIOUR_NEW_DEVICE",
        category=RuleCategory.behaviour,
        weight=weight,
        reason="Payment originates from a device the user has not used before.",
        evidence={"is_new_device": True, "amount": str(req.amount)},
    )


@_rule
def rule_BEHAVIOUR_METADATA_SPARSE(req: FraudScoreRequest) -> RuleHit | None:
    """Low-weight signal: caller provided very little contextual metadata.

    A legitimate payment gateway always has device_id, IP address, and
    country. Missing all of them suggests the request may be from an
    API client bypassing normal checkout flows — mildly suspicious.
    """
    context_keys = {"device_id", "ip_address", "country", "country_sender"}
    present = context_keys & req.metadata.keys()
    if len(present) >= 2:
        return None
    return RuleHit(
        rule_id="BEHAVIOUR_METADATA_SPARSE",
        category=RuleCategory.behaviour,
        weight=8,
        reason=(
            f"Only {len(present)} of {len(context_keys)} expected contextual "
            "signals provided. Legitimate checkout flows include device and geo context."
        ),
        evidence={"provided_keys": sorted(present), "expected_keys": sorted(context_keys)},
    )


# ═══════════════════════════════════════════════════════════════════════════
# Public evaluation function
# ═══════════════════════════════════════════════════════════════════════════


def evaluate(req: FraudScoreRequest) -> tuple[int, list[RuleHit]]:
    """Run all registered rules and return ``(raw_score, hits)``.

    ``raw_score`` is the sum of all fired rule weights, capped at 100.
    The decision threshold logic lives in ``FraudScoringService`` so it
    can be tested independently of the rule firing.

    Args:
        req: Validated fraud-score request.

    Returns:
        A ``(raw_score, hits)`` tuple.
        ``hits`` is ordered: highest-weight rule first (most influential).
    """
    hits: list[RuleHit] = []
    for rule_fn in _RULES:
        hit = rule_fn(req)
        if hit is not None:
            hits.append(hit)

    # Sort descending by weight so the most impactful reasons come first.
    hits.sort(key=lambda h: h.weight, reverse=True)

    raw_score = min(sum(h.weight for h in hits), 100)
    return raw_score, hits


def rule_count() -> int:
    """Return the number of registered rules (for documentation and tests)."""
    return len(_RULES)


__all__ = [
    "THRESHOLD_REVIEW",
    "THRESHOLD_REJECT",
    "evaluate",
    "rule_count",
]
