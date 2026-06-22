#!/usr/bin/env python3
"""scripts/seed_demo_data.py — Synthetic payment data seeder.

Produces realistic-looking users, wallets, transactions, and double-entry
ledger postings. Designed for demo, load-test baseline, and
development-environment setup.

Usage (inside the running core-api container)
----------------------------------------------
# Default: 50 users, 10 000 transactions, currency INR
    python scripts/seed_demo_data.py

# Custom counts
    python scripts/seed_demo_data.py --users 100 --transactions 50000

# Different base currency (each user always gets wallets in INR + USD + EUR)
    python scripts/seed_demo_data.py --currency USD

# Dry-run — validate DB connection and show what would be created
    python scripts/seed_demo_data.py --dry-run

# Quiet mode (suppresses per-batch progress)
    python scripts/seed_demo_data.py --quiet

From the host (Podman)
-----------------------
    podman exec -it pg-core-api \
        python scripts/seed_demo_data.py --transactions 10000

Design choices
--------------
- **Idempotent**: safe to re-run. Users are keyed by email; wallets by
  (user_id, currency); transactions by their pre-generated idempotency
  key (UUID5 of seed + sequence index). Re-running adds only the rows
  that are missing.
- **Direct repository writes, not PaymentService**: calling
  PaymentService.create_payment would debit real wallets, hit fraud
  checks, and choke on hot-key contention. The seeder bypasses the
  service layer and writes transactions + ledger entries directly via
  the repository layer, mirroring exactly what the service would do.
- **Batched commits** (default batch=500): keeps per-transaction lock
  duration short and memory bounded for very large seed counts.
- **Weighted realistic distributions**: amounts, payment methods, statuses,
  countries, and fraud scores follow weighted distributions that resemble
  production traffic rather than uniform random.
- **No schema changes**: uses only existing SQLAlchemy models. No
  migrations required.
- **PYTHONPATH=/app**: the Containerfile sets this; the script runs
  anywhere the models are importable.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Final, NamedTuple, Sequence

# ── stdlib logging so the script works before structlog is configured ────────
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("seed")

# ── All models imported so the SQLAlchemy registry is complete ───────────────
# Order matters: User must be registered before Transaction (FK resolution).
import app.db.models  # noqa: F401
from app.identity.domain.models import User                      # noqa: E402
from app.wallet.domain.models import Wallet                      # noqa: E402
from app.payment.domain.models import Transaction                # noqa: E402
from app.ledger.domain.models import LedgerEntry                 # noqa: E402

from app.db import (                                             # noqa: E402
    KycStatus,
    LedgerDirection,
    PaymentMethod,
    SettleStatus,
    TxnStatus,
)
from app.db.session import create_engine_and_sessionmaker, dispose_engine  # noqa: E402
from app.ledger.infrastructure.repository import LedgerRepository          # noqa: E402
from app.payment.infrastructure.repository import PaymentRepository        # noqa: E402
from app.wallet.application.service import WalletAlreadyExistsError        # noqa: E402
from app.wallet.application.service import WalletService                   # noqa: E402
from app.wallet.infrastructure.repository import WalletRepository          # noqa: E402
from app.settings import get_settings                                      # noqa: E402


# ── Constants ────────────────────────────────────────────────────────────────

# Seed namespace for deterministic UUID5 generation — guarantees idempotency
# across re-runs without touching the database first.
_SEED_NS: Final[uuid.UUID] = uuid.UUID("a1b2c3d4-e5f6-4789-abcd-ef0123456789")

# Currencies seeded per user (one wallet each, independent of --currency flag).
_WALLET_CURRENCIES: Final[tuple[str, ...]] = ("INR", "USD", "EUR")

# Merchant catalogue: 8 realistic merchants matching m_<alphanum> pattern.
_MERCHANTS: Final[tuple[str, ...]] = (
    "m_swiggy",
    "m_zomato",
    "m_amazon",
    "m_flipkart",
    "m_myntra",
    "m_bigbasket",
    "m_bookmyshow",
    "m_phonepe",
)

# Country codes for sender/receiver fields (ISO 3166-1 alpha-2).
_COUNTRIES: Final[tuple[str, ...]] = ("IN", "US", "GB", "SG", "AE", "DE", "AU", "CA")

# Weighted payment-method distribution (mirrors realistic UPI-heavy India market).
_METHOD_WEIGHTS: Final[dict[PaymentMethod, int]] = {
    PaymentMethod.upi:           55,
    PaymentMethod.card:          25,
    PaymentMethod.bank_transfer: 12,
    PaymentMethod.wallet:         8,
}

# Weighted transaction-status distribution.
# ~80% success, ~10% failed, ~7% flagged, ~3% reversed — realistic for a new platform.
_STATUS_WEIGHTS: Final[dict[TxnStatus, int]] = {
    TxnStatus.success:  80,
    TxnStatus.failed:   10,
    TxnStatus.flagged:   7,
    TxnStatus.reversed:  3,
}

# KYC status distribution for users.
_KYC_WEIGHTS: Final[dict[KycStatus, int]] = {
    KycStatus.verified: 75,
    KycStatus.pending:  20,
    KycStatus.failed:    5,
}

# Failure reasons for failed transactions.
_FAILURE_REASONS: Final[tuple[str, ...]] = (
    "insufficient_funds",
    "card_declined",
    "bank_timeout",
    "fraud_blocked",
    "invalid_account",
    "daily_limit_exceeded",
)

# Settlement status distribution (only meaningful on success transactions).
_SETTLE_WEIGHTS: Final[dict[SettleStatus, int]] = {
    SettleStatus.settled:  70,
    SettleStatus.pending:  22,
    SettleStatus.disputed:  5,
    SettleStatus.reversed:  3,
}

# Namespace for deterministic merchant suspense accounts
# (mirrors PaymentService._MERCHANT_ACCOUNT_NAMESPACE exactly).
_MERCHANT_ACCOUNT_NAMESPACE: Final[uuid.UUID] = uuid.UUID(
    "c3d4e5f6-0000-4000-8000-000000000001"
)

# How many rows to flush+commit per batch.
_DEFAULT_BATCH_SIZE: Final[int] = 500

# Password hash placeholder — not real bcrypt, safe for demo data only.
_DEMO_PASSWORD_HASH: Final[str] = (
    "$2b$12$DEMO_HASH_NOT_REAL_DO_NOT_USE_IN_PRODUCTION_aaaaaaaaa"
)


# ── Data-generation helpers ──────────────────────────────────────────────────


def _weighted_choice(weights: dict) -> object:
    """Pick a key from a {key: weight} dict using weighted random selection."""
    population = list(weights.keys())
    w = list(weights.values())
    return random.choices(population, weights=w, k=1)[0]


def _det_uuid(namespace: uuid.UUID, *parts: str) -> uuid.UUID:
    """Derive a deterministic UUID5 from a namespace + string parts."""
    return uuid.uuid5(namespace, ":".join(parts))


def _merchant_account_for(merchant_id: str) -> uuid.UUID:
    """Mirror PaymentService._merchant_account_for exactly."""
    return uuid.uuid5(_MERCHANT_ACCOUNT_NAMESPACE, merchant_id)


def _seed_email(index: int) -> str:
    return f"seed_user_{index:05d}@demo.paymentgateway.local"


def _seed_user_id(index: int) -> uuid.UUID:
    """Deterministic user UUID — same index → same UUID across re-runs."""
    return _det_uuid(_SEED_NS, "user", str(index))


def _seed_idempotency_key(txn_index: int) -> str:
    """Deterministic idempotency key for a transaction sequence number."""
    raw = _det_uuid(_SEED_NS, "idem", str(txn_index)).hex
    # Must match [A-Za-z0-9_-]{8,64} — UUID hex (32 chars) qualifies.
    return raw  # 32-char hex string


def _random_amount(currency: str) -> Decimal:
    """Produce a realistic transaction amount for the given currency.

    Amounts follow a log-normal distribution (few large, many small) —
    more realistic than uniform random for retail payments.
    """
    # Log-normal parameters tuned per currency.
    params: dict[str, tuple[float, float]] = {
        "INR": (6.0, 1.2),   # mean≈403, stddev broad — ₹10 to ₹50 000
        "USD": (3.5, 1.0),   # mean≈33, stddev — $5 to $500
        "EUR": (3.4, 1.0),   # similar to USD
    }
    mu, sigma = params.get(currency, (4.0, 1.0))
    raw = random.lognormvariate(mu, sigma)
    # Clamp to a sane range then round to 2 decimal places.
    raw = max(1.0, min(raw, 500_000.0))
    return Decimal(str(round(raw, 2)))


def _random_timestamp(*, days_back: int = 365) -> datetime:
    """Uniform random timestamp in the past ``days_back`` days (UTC)."""
    offset_seconds = random.randint(0, days_back * 86_400)
    return datetime.now(timezone.utc) - timedelta(seconds=offset_seconds)


class _TxnSpec(NamedTuple):
    """Pre-computed specification for one seed transaction."""
    index: int
    user_id: uuid.UUID
    wallet_id: uuid.UUID          # sender wallet
    merchant_id: str
    merchant_account: uuid.UUID   # derived system account
    idempotency_key: str
    amount: Decimal
    currency: str
    payment_method: PaymentMethod
    status: TxnStatus
    settlement_status: SettleStatus
    failure_reason: str | None
    fraud_score: Decimal | None
    chargeback_flag: bool
    country_sender: str
    country_receiver: str
    created_at: datetime
    metadata: dict


def _make_txn_spec(
    index: int,
    users: list[User],
    wallet_map: dict[tuple[uuid.UUID, str], Wallet],
    currency: str,
    rng: random.Random,
) -> _TxnSpec:
    """Pre-compute all fields for one transaction (no DB I/O)."""
    user = rng.choice(users)
    wallet = wallet_map[(user.user_id, currency)]
    merchant_id = rng.choice(_MERCHANTS)
    status: TxnStatus = _weighted_choice(_STATUS_WEIGHTS)  # type: ignore[assignment]
    method: PaymentMethod = _weighted_choice(_METHOD_WEIGHTS)  # type: ignore[assignment]

    settle = (
        _weighted_choice(_SETTLE_WEIGHTS)  # type: ignore[assignment]
        if status == TxnStatus.success
        else SettleStatus.pending
    )
    failure_reason = rng.choice(_FAILURE_REASONS) if status == TxnStatus.failed else None
    # Fraud score present on flagged (high) and ~5% of success (low-medium).
    if status == TxnStatus.flagged:
        fraud_score = Decimal(str(round(rng.uniform(0.7, 0.99), 3)))
    elif status == TxnStatus.success and rng.random() < 0.05:
        fraud_score = Decimal(str(round(rng.uniform(0.1, 0.45), 3)))
    else:
        fraud_score = None

    chargeback = (
        rng.random() < 0.02        # 2% of disputed transactions get a chargeback flag
        if settle == SettleStatus.disputed
        else False
    )

    return _TxnSpec(
        index=index,
        user_id=user.user_id,
        wallet_id=wallet.wallet_id,
        merchant_id=merchant_id,
        merchant_account=_merchant_account_for(merchant_id),
        idempotency_key=_seed_idempotency_key(index),
        amount=_random_amount(currency),
        currency=currency,
        payment_method=method,
        status=status,
        settlement_status=settle,
        failure_reason=failure_reason,
        fraud_score=fraud_score,
        chargeback_flag=chargeback,
        country_sender=rng.choice(_COUNTRIES),
        country_receiver=rng.choice(_COUNTRIES),
        created_at=_random_timestamp(),
        metadata={
            "seed": True,
            "seed_index": index,
            "campaign": rng.choice(["organic", "referral", "promo", "ads"]),
            "platform": rng.choice(["android", "ios", "web"]),
        },
    )


# ── Core seeding routines ─────────────────────────────────────────────────────


async def _seed_users(
    session_factory,
    n_users: int,
    *,
    quiet: bool,
) -> list[User]:
    """INSERT seed users; return the full list (existing + new)."""
    users: list[User] = []
    new_count = 0

    async with session_factory() as session:
        for i in range(n_users):
            user_id = _seed_user_id(i)
            email = _seed_email(i)

            # Check for existing row by email (idempotency key for users).
            from sqlalchemy import select
            result = await session.execute(
                select(User).where(User.email == email)
            )
            existing = result.scalar_one_or_none()
            if existing is not None:
                users.append(existing)
                continue

            kyc: KycStatus = _weighted_choice(_KYC_WEIGHTS)  # type: ignore[assignment]
            user = User(
                email=email,
                password_hash=_DEMO_PASSWORD_HASH,
                kyc_status=kyc,
                country=random.choice(_COUNTRIES),
            )
            # Assign the deterministic UUID so wallet and transaction fkeys
            # can be computed before the row is flushed.
            user.user_id = user_id
            session.add(user)
            users.append(user)
            new_count += 1

        await session.commit()

    if not quiet:
        log.info("users_seeded total=%d new=%d skipped=%d",
                 n_users, new_count, n_users - new_count)
    return users


async def _seed_wallets(
    session_factory,
    users: list[User],
    *,
    quiet: bool,
) -> dict[tuple[uuid.UUID, str], Wallet]:
    """Ensure every user has one wallet per currency. Return wallet_map."""
    wallet_map: dict[tuple[uuid.UUID, str], Wallet] = {}
    new_count = 0

    async with session_factory() as session:
        wallet_svc = WalletService(session)
        for user in users:
            for currency in _WALLET_CURRENCIES:
                try:
                    wallet = await wallet_svc.create_wallet(
                        user_id=user.user_id,
                        currency=currency,
                        # Generous initial balance so transactions don't fail
                        # due to actual insufficient funds in the seeder.
                        initial_balance=Decimal("1_000_000"),
                    )
                    wallet_map[(user.user_id, currency)] = wallet
                    new_count += 1
                except WalletAlreadyExistsError:
                    # Wallet exists — look it up.
                    wr = WalletRepository(session)
                    wallet = await wr.get_by_user_currency(user.user_id, currency)
                    if wallet is not None:
                        wallet_map[(user.user_id, currency)] = wallet
        await session.commit()

    if not quiet:
        log.info(
            "wallets_seeded currencies=%s total=%d new=%d",
            _WALLET_CURRENCIES, len(wallet_map), new_count,
        )
    return wallet_map


async def _seed_transactions(
    session_factory,
    specs: list[_TxnSpec],
    *,
    batch_size: int,
    quiet: bool,
) -> tuple[int, int]:
    """INSERT transactions and ledger entries in batches.

    Returns ``(inserted, skipped)`` counts.
    """
    inserted = 0
    skipped = 0
    total = len(specs)

    for batch_start in range(0, total, batch_size):
        batch = specs[batch_start : batch_start + batch_size]

        async with session_factory() as session:
            pr = PaymentRepository(session)
            lr = LedgerRepository(session)

            for spec in batch:
                # ── Idempotency check ─────────────────────────────────────
                existing = await pr.find_by_idempotency_key(
                    spec.merchant_id, spec.idempotency_key
                )
                if existing is not None:
                    skipped += 1
                    continue

                # ── Build Transaction row ─────────────────────────────────
                txn = Transaction(
                    user_id=spec.user_id,
                    merchant_id=spec.merchant_id,
                    amount=spec.amount,
                    currency=spec.currency,
                    payment_method=spec.payment_method,
                    status=spec.status,
                    failure_reason=spec.failure_reason,
                    fraud_score=spec.fraud_score,
                    chargeback_flag=spec.chargeback_flag,
                    settlement_status=spec.settlement_status,
                    idempotency_key=spec.idempotency_key,
                    country_sender=spec.country_sender,
                    country_receiver=spec.country_receiver,
                    metadata_=spec.metadata,
                )
                # Backdate created_at to simulate historical spread.
                # updated_at follows the same timestamp; realistic enough.
                txn.created_at = spec.created_at
                txn.updated_at = spec.created_at

                await pr.add(txn)

                # ── Build ledger entries (only for non-failed) ────────────
                # Failed transactions have NO ledger entries — this is the
                # exact invariant enforced in the service layer.
                if spec.status != TxnStatus.failed:
                    await lr.add_entries([
                        LedgerEntry(
                            transaction_id=txn.transaction_id,
                            account_id=spec.wallet_id,
                            direction=LedgerDirection.DEBIT,
                            amount=spec.amount,
                            currency=spec.currency,
                        ),
                        LedgerEntry(
                            transaction_id=txn.transaction_id,
                            account_id=spec.merchant_account,
                            direction=LedgerDirection.CREDIT,
                            amount=spec.amount,
                            currency=spec.currency,
                        ),
                    ])

                inserted += 1

            await session.commit()

        if not quiet:
            done = min(batch_start + batch_size, total)
            pct = done / total * 100
            log.info(
                "progress  %d/%d (%.0f%%)  inserted=%d skipped=%d",
                done, total, pct, inserted, skipped,
            )

    return inserted, skipped


# ── Verification ──────────────────────────────────────────────────────────────


async def _verify(session_factory, *, quiet: bool) -> None:
    """Run basic consistency checks on the seeded data."""
    from sqlalchemy import func, select

    async with session_factory() as session:
        txn_count = (
            await session.execute(select(func.count()).select_from(Transaction))
        ).scalar_one()
        user_count = (
            await session.execute(select(func.count()).select_from(User))
        ).scalar_one()
        wallet_count = (
            await session.execute(select(func.count()).select_from(Wallet))
        ).scalar_one()
        ledger_count = (
            await session.execute(select(func.count()).select_from(LedgerEntry))
        ).scalar_one()

        # Spot-check: every non-failed transaction should have exactly 2 ledger entries.
        non_failed = (
            await session.execute(
                select(func.count())
                .select_from(Transaction)
                .where(Transaction.status != TxnStatus.failed)
            )
        ).scalar_one()

        # Count transactions that have the right number of ledger rows.
        from sqlalchemy import case
        entry_counts = (
            await session.execute(
                select(
                    LedgerEntry.transaction_id,
                    func.count().label("n"),
                )
                .group_by(LedgerEntry.transaction_id)
                .having(func.count() != 2)
            )
        ).fetchall()
        bad_postings = len(entry_counts)

    log.info(
        "verify  users=%d  wallets=%d  transactions=%d  ledger_entries=%d",
        user_count, wallet_count, txn_count, ledger_count,
    )
    if bad_postings:
        log.warning(
            "verify  WARN: %d transactions have ledger entry count ≠ 2 "
            "(expected for failed txns, wrong for others)",
            bad_postings,
        )
    else:
        log.info("verify  ledger balanced: all non-failed transactions have 2 entries")

    # Double-entry balance check: per-transaction DEBIT == CREDIT.
    async with session_factory() as session:
        from sqlalchemy import case
        imbalanced = (
            await session.execute(
                select(LedgerEntry.transaction_id)
                .group_by(LedgerEntry.transaction_id)
                .having(
                    func.sum(
                        case(
                            (LedgerEntry.direction == LedgerDirection.DEBIT,
                             LedgerEntry.amount),
                            else_=-LedgerEntry.amount,
                        )
                    ) != 0
                )
            )
        ).fetchall()

    if imbalanced:
        log.warning(
            "verify  WARN: %d transactions have unbalanced ledger entries",
            len(imbalanced),
        )
    else:
        log.info("verify  double-entry invariant holds for all transactions")


# ── CLI entry point ───────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Seed synthetic payment data into the Payment Gateway database.\n\n"
            "Safe to re-run — existing rows are skipped (idempotent).\n\n"
            "Example:\n"
            "  python scripts/seed_demo_data.py --users 50 --transactions 10000"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--users",
        type=int,
        default=50,
        metavar="N",
        help="Number of synthetic users to create (default: 50). "
             "Each user gets wallets in INR, USD, and EUR.",
    )
    p.add_argument(
        "--transactions",
        type=int,
        default=10_000,
        metavar="N",
        help="Total number of payment transactions to seed (default: 10 000).",
    )
    p.add_argument(
        "--currency",
        type=str,
        default="INR",
        choices=list(_WALLET_CURRENCIES),
        help="Primary currency for transactions (default: INR). "
             "Users always receive wallets in all three currencies.",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=_DEFAULT_BATCH_SIZE,
        metavar="N",
        help=f"Rows committed per batch (default: {_DEFAULT_BATCH_SIZE}). "
             "Lower values reduce peak memory; higher values are faster.",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=42,
        metavar="INT",
        help="Random seed for reproducibility (default: 42).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate DB connection and print what would be created; do not write.",
    )
    p.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip post-seed consistency verification (faster for large seeds).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-batch progress output.",
    )
    return p.parse_args()


async def main() -> int:
    args = _parse_args()

    # ── Settings & engine ────────────────────────────────────────────────
    settings = get_settings()
    log.info(
        "seed_start  users=%d transactions=%d currency=%s batch=%d seed=%d dry_run=%s",
        args.users, args.transactions, args.currency,
        args.batch_size, args.seed, args.dry_run,
    )

    engine, session_factory = create_engine_and_sessionmaker(
        settings.database_url,
        pool_size=5,
        max_overflow=2,
        echo=False,
    )

    # ── Dry-run: just check connectivity ────────────────────────────────
    if args.dry_run:
        from sqlalchemy import text
        async with session_factory() as session:
            result = await session.execute(text("SELECT version()"))
            pg_version = result.scalar_one()
        log.info("dry_run  DB connection OK  pg=%s", pg_version.split(",")[0])
        log.info(
            "dry_run  would create up to %d users × %d wallets + %d transactions",
            args.users,
            args.users * len(_WALLET_CURRENCIES),
            args.transactions,
        )
        await dispose_engine(engine)
        return 0

    # ── Pre-generate all transaction specs (pure CPU, no DB I/O) ────────
    rng = random.Random(args.seed)

    t0 = asyncio.get_event_loop().time()

    # ── Phase 1: Users ───────────────────────────────────────────────────
    log.info("phase 1/4  seeding %d users …", args.users)
    users = await _seed_users(session_factory, args.users, quiet=args.quiet)
    if not users:
        log.error("No users created or found — aborting.")
        await dispose_engine(engine)
        return 1

    # ── Phase 2: Wallets ─────────────────────────────────────────────────
    log.info("phase 2/4  seeding wallets (%d users × %d currencies) …",
             len(users), len(_WALLET_CURRENCIES))
    wallet_map = await _seed_wallets(session_factory, users, quiet=args.quiet)
    if not wallet_map:
        log.error("No wallets created — aborting.")
        await dispose_engine(engine)
        return 1

    # Validate we have the primary currency wallet for every user.
    users_with_wallet = [u for u in users if (u.user_id, args.currency) in wallet_map]
    if not users_with_wallet:
        log.error(
            "No users have a wallet in %s — cannot seed transactions.", args.currency
        )
        await dispose_engine(engine)
        return 1

    # ── Phase 3: Pre-compute transaction specs ───────────────────────────
    log.info("phase 3/4  pre-computing %d transaction specs …", args.transactions)
    specs = [
        _make_txn_spec(i, users_with_wallet, wallet_map, args.currency, rng)
        for i in range(args.transactions)
    ]

    # Print a preview of the distribution.
    status_dist: dict[TxnStatus, int] = {}
    method_dist: dict[PaymentMethod, int] = {}
    for s in specs:
        status_dist[s.status] = status_dist.get(s.status, 0) + 1
        method_dist[s.payment_method] = method_dist.get(s.payment_method, 0) + 1

    log.info(
        "distribution  status=%s",
        {k.value: v for k, v in sorted(status_dist.items(), key=lambda x: -x[1])},
    )
    log.info(
        "distribution  method=%s",
        {k.value: v for k, v in sorted(method_dist.items(), key=lambda x: -x[1])},
    )

    # ── Phase 4: Write transactions + ledger entries ─────────────────────
    log.info(
        "phase 4/4  writing %d transactions (batch=%d) …",
        args.transactions, args.batch_size,
    )
    inserted, skipped = await _seed_transactions(
        session_factory, specs,
        batch_size=args.batch_size,
        quiet=args.quiet,
    )

    elapsed = asyncio.get_event_loop().time() - t0
    log.info(
        "seed_complete  inserted=%d skipped=%d elapsed=%.1fs tps=%.0f",
        inserted, skipped, elapsed,
        inserted / elapsed if elapsed > 0 else 0,
    )

    # ── Verification ─────────────────────────────────────────────────────
    if not args.skip_verify:
        log.info("verify  running consistency checks …")
        await _verify(session_factory, quiet=args.quiet)

    await dispose_engine(engine)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
