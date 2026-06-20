"""Application service for the Payment aggregate.

This is the orchestrator. ``create_payment`` and ``refund`` are the two
public entry points; everything else is a query.

Hot-path of ``create_payment`` — narrated:

    1. Validate the request shape (currency, amount, merchant_id).
    2. Idempotency pre-check — if the (merchant_id, key) tuple has
       already produced a transaction, return it verbatim.
    3. Look up the sender wallet (must exist for the requested currency).
    4. Insert the Transaction row with ``status='pending'``.
    5. Debit the sender wallet via WalletService (handles optimistic
       retry; raises ``InsufficientFundsError`` if balance too low).
    6. Post the double-entry pair via LedgerService.
    7. Transition status ``pending → success`` (or ``→ failed`` if
       insufficient funds was caught at step 5).

State machine:
    ``_ALLOWED_TRANSITIONS`` enumerates every legal status move.
    Service-side enforcement complements the DB's ENUM-only check.

Transactional boundary:
    Same contract as the other services — **no commit**. The route
    handler (future) owns the session-level commit. An exception at
    any step leaves the session in a state the caller rolls back,
    giving us free atomicity across wallet+ledger+transaction writes.

Idempotency strategy (MVP):
    Pre-check via ``find_by_idempotency_key``. The UNIQUE constraint
    on ``(merchant_id, idempotency_key)`` is the safety net for the
    rare race window between the pre-check and the insert; in that
    case the second request's INSERT fails with ``IntegrityError``,
    the route handler returns 5xx, and the client's retry hits the
    pre-check on the now-committed row. See PAYMENT_DOMAIN_DESIGN.md §7.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import PaymentMethod, TxnStatus
from app.ledger.application.service import LedgerService
from app.payment.domain.models import Transaction
from app.payment.infrastructure.repository import (
    PaymentRepository,
    TransactionNotFoundError,
)
from app.wallet.application.service import (
    ContentionExceededError,
    InsufficientFundsError,
    WalletService,
)
from app.wallet.infrastructure.repository import (
    WalletNotFoundError,
    WalletRepository,
)

log = structlog.get_logger(__name__)


# ── Constants ────────────────────────────────────────────────────────────

_CURRENCY_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{3}$")
_MERCHANT_ID_RE: Final[re.Pattern[str]] = re.compile(r"^m_[A-Za-z0-9_]{1,32}$")
_IDEMPOTENCY_KEY_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_DECIMAL_ZERO: Final[Decimal] = Decimal("0")

# Deterministic UUID namespace for per-merchant suspense accounts.
# Until a real merchants table + per-merchant wallets land, every
# ``merchant_id`` maps to a stable system-account UUID derived via
# ``uuid.uuid5``. The ledger sums to zero for an honest merchant view.
_MERCHANT_ACCOUNT_NAMESPACE: Final[uuid.UUID] = uuid.UUID(
    "c3d4e5f6-0000-4000-8000-000000000001"
)


# State machine. Empty frozenset = terminal state.
_ALLOWED_TRANSITIONS: Final[Mapping[TxnStatus, frozenset[TxnStatus]]] = {
    TxnStatus.pending: frozenset(
        {TxnStatus.success, TxnStatus.failed, TxnStatus.flagged}
    ),
    TxnStatus.flagged: frozenset({TxnStatus.success, TxnStatus.failed}),
    TxnStatus.success: frozenset({TxnStatus.reversed}),
    TxnStatus.failed: frozenset(),
    TxnStatus.reversed: frozenset(),
}


# ── Exceptions ───────────────────────────────────────────────────────────


class PaymentServiceError(Exception):
    """Base for payment-service-level errors."""


class InvalidPaymentRequestError(PaymentServiceError):
    """Input failed validation (currency, amount, merchant_id shape, ...)."""


class InvalidStateTransitionError(PaymentServiceError):
    """A status change would violate the transition matrix."""

    def __init__(self, current: TxnStatus, target: TxnStatus) -> None:
        super().__init__(
            f"illegal transition: {current.value} -> {target.value}"
        )
        self.current = current
        self.target = target


class TransactionNotRefundableError(PaymentServiceError):
    """Parent transaction is not in a state that permits refund."""

    def __init__(self, transaction_id: uuid.UUID, status: TxnStatus) -> None:
        super().__init__(
            f"transaction {transaction_id} is {status.value}; "
            "only 'success' transactions can be refunded"
        )
        self.transaction_id = transaction_id
        self.status = status


class OverRefundError(PaymentServiceError):
    """Refund attempt would exceed the parent's remaining refundable amount."""

    def __init__(
        self,
        parent_transaction_id: uuid.UUID,
        original_amount: Decimal,
        already_refunded: Decimal,
        attempted: Decimal,
    ) -> None:
        super().__init__(
            f"cannot refund {attempted}: already refunded {already_refunded} "
            f"of {original_amount} on transaction {parent_transaction_id}"
        )
        self.parent_transaction_id = parent_transaction_id
        self.original_amount = original_amount
        self.already_refunded = already_refunded
        self.attempted = attempted


class MissingMerchantError(PaymentServiceError):
    """A refund was requested against a P2P transaction (no merchant_id)."""


# ── Service ──────────────────────────────────────────────────────────────


class PaymentService:
    """Orchestrates payment creation, refunds, and status queries."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        payment_repository: PaymentRepository | None = None,
        wallet_repository: WalletRepository | None = None,
        wallet_service: WalletService | None = None,
        ledger_service: LedgerService | None = None,
    ) -> None:
        self._session = session
        self._payment_repo = payment_repository or PaymentRepository(session)
        self._wallet_repo = wallet_repository or WalletRepository(session)
        # Share the wallet_repo instance with the wallet_service so a
        # single session has one repository instance — keeps any future
        # repository-level caching coherent.
        self._wallet_service = wallet_service or WalletService(
            session, wallet_repository=self._wallet_repo
        )
        self._ledger_service = ledger_service or LedgerService(session)

    # ── Commands ─────────────────────────────────────────────────────

    async def create_payment(
        self,
        *,
        user_id: uuid.UUID,
        merchant_id: str,
        amount: Decimal,
        currency: str,
        payment_method: PaymentMethod,
        idempotency_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Transaction:
        """Create a peer-to-merchant payment.

        Returns:
            The final ``Transaction`` — ``status='success'`` on the
            happy path, ``status='failed'`` with ``failure_reason``
            populated if funds were insufficient, or the cached prior
            transaction on idempotent replay.

        Raises:
            InvalidPaymentRequestError, WalletNotFoundError,
            ContentionExceededError (from wallet retry exhaustion)
        """
        # ─── 1. Validate ────────────────────────────────────────────
        self._validate_amount(amount)
        self._validate_currency(currency)
        self._validate_merchant_id(merchant_id)
        if idempotency_key is not None:
            self._validate_idempotency_key(idempotency_key)

        # ─── 2. Idempotency pre-check ───────────────────────────────
        if idempotency_key is not None:
            existing = await self._payment_repo.find_by_idempotency_key(
                merchant_id, idempotency_key
            )
            if existing is not None:
                log.info(
                    "payment_idempotent_replay",
                    transaction_id=str(existing.transaction_id),
                    merchant_id=merchant_id,
                    idempotency_key=idempotency_key,
                    cached_status=existing.status.value,
                )
                return existing

        # ─── 3. Look up sender wallet ───────────────────────────────
        sender_wallet = await self._wallet_repo.get_by_user_currency(
            user_id, currency
        )
        if sender_wallet is None:
            log.warning(
                "payment_sender_wallet_missing",
                user_id=str(user_id),
                currency=currency,
            )
            raise WalletNotFoundError(
                f"no wallet for user={user_id} currency={currency}"
            )

        # ─── 4. Insert pending transaction ──────────────────────────
        txn = Transaction(
            user_id=user_id,
            merchant_id=merchant_id,
            amount=amount,
            currency=currency,
            payment_method=payment_method,
            status=TxnStatus.pending,
            idempotency_key=idempotency_key,
            metadata_=dict(metadata) if metadata else {},
        )
        await self._payment_repo.add(txn)

        log.info(
            "payment_initiated",
            transaction_id=str(txn.transaction_id),
            user_id=str(user_id),
            merchant_id=merchant_id,
            amount=str(amount),
            currency=currency,
            payment_method=payment_method.value,
        )

        # ─── 5. Debit sender wallet ─────────────────────────────────
        try:
            await self._wallet_service.debit(sender_wallet.wallet_id, amount)
        except InsufficientFundsError as e:
            self._assert_transition(TxnStatus.pending, TxnStatus.failed)
            failed = await self._payment_repo.update_status(
                txn.transaction_id,
                TxnStatus.failed,
                failure_reason="insufficient_funds",
            )
            log.warning(
                "payment_failed_insufficient_funds",
                transaction_id=str(txn.transaction_id),
                wallet_id=str(sender_wallet.wallet_id),
                available=str(e.available),
                required=str(e.required),
            )
            return failed
        except ContentionExceededError:
            # Mark failed and re-raise so the route layer can surface
            # the contention to the client as 503. The pending row +
            # failed status leaves an audit trail of the contention.
            self._assert_transition(TxnStatus.pending, TxnStatus.failed)
            await self._payment_repo.update_status(
                txn.transaction_id,
                TxnStatus.failed,
                failure_reason="wallet_contention",
            )
            raise

        # ─── 6. Post ledger ─────────────────────────────────────────
        merchant_account = self._merchant_account_for(merchant_id)
        await self._ledger_service.post_payment(
            transaction_id=txn.transaction_id,
            debit_account_id=sender_wallet.wallet_id,
            credit_account_id=merchant_account,
            amount=amount,
            currency=currency,
        )

        # ─── 7. Mark success ────────────────────────────────────────
        self._assert_transition(TxnStatus.pending, TxnStatus.success)
        succeeded = await self._payment_repo.update_status(
            txn.transaction_id,
            TxnStatus.success,
        )
        log.info(
            "payment_succeeded",
            transaction_id=str(succeeded.transaction_id),
            amount=str(amount),
            currency=currency,
            merchant_id=merchant_id,
        )
        return succeeded

    async def refund(
        self,
        *,
        parent_transaction_id: uuid.UUID,
        amount: Decimal,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> Transaction:
        """Refund (full or partial) against a successful transaction.

        Posts a child ``Transaction`` with ``parent_transaction`` set
        and creates a balanced ledger pair in the reverse direction.

        Raises:
            TransactionNotFoundError, TransactionNotRefundableError,
            OverRefundError, MissingMerchantError
        """
        self._validate_amount(amount)
        if idempotency_key is not None:
            self._validate_idempotency_key(idempotency_key)

        # ─── 1. Parent fetch + state validation ─────────────────────
        parent = await self._payment_repo.get_by_id(parent_transaction_id)
        if parent is None:
            raise TransactionNotFoundError(parent_transaction_id)
        if parent.status is not TxnStatus.success:
            raise TransactionNotRefundableError(
                parent_transaction_id, parent.status
            )
        if parent.merchant_id is None:
            # P2P refund flow would credit the original sender and
            # debit the original recipient — not modelled in MVP.
            raise MissingMerchantError(
                f"transaction {parent_transaction_id} is P2P; "
                "P2P refunds are not implemented in MVP"
            )

        # ─── 2. Idempotency pre-check (now that we know merchant_id) ─
        if idempotency_key is not None:
            existing = await self._payment_repo.find_by_idempotency_key(
                parent.merchant_id, idempotency_key
            )
            if existing is not None:
                log.info(
                    "refund_idempotent_replay",
                    transaction_id=str(existing.transaction_id),
                    parent_transaction_id=str(parent_transaction_id),
                    idempotency_key=idempotency_key,
                )
                return existing

        # ─── 3. Over-refund check ───────────────────────────────────
        already_refunded = await self._payment_repo.sum_refunded_amount(
            parent_transaction_id
        )
        if already_refunded + amount > parent.amount:
            raise OverRefundError(
                parent_transaction_id,
                parent.amount,
                already_refunded,
                amount,
            )

        # ─── 4. Insert pending refund transaction ───────────────────
        refund_txn = Transaction(
            user_id=parent.user_id,
            merchant_id=parent.merchant_id,
            amount=amount,
            currency=parent.currency,
            payment_method=parent.payment_method,
            status=TxnStatus.pending,
            idempotency_key=idempotency_key,
            parent_transaction=parent_transaction_id,
            metadata_={"refund_reason": reason} if reason else {},
        )
        await self._payment_repo.add(refund_txn)
        log.info(
            "refund_initiated",
            refund_transaction_id=str(refund_txn.transaction_id),
            parent_transaction_id=str(parent_transaction_id),
            amount=str(amount),
            currency=parent.currency,
        )

        # ─── 5. Credit sender wallet (reverse of original debit) ────
        # The sender wallet must still exist — RESTRICT FK on
        # core.wallets ensures it cannot have been deleted while the
        # original transaction succeeded.
        sender_wallet = await self._wallet_repo.get_by_user_currency(
            parent.user_id, parent.currency
        )
        if sender_wallet is None:
            # Shouldn't happen given RESTRICT FK; surface loudly if it does.
            raise WalletNotFoundError(
                f"sender wallet missing for refund: user={parent.user_id} "
                f"currency={parent.currency}"
            )
        await self._wallet_service.credit(sender_wallet.wallet_id, amount)

        # ─── 6. Post reverse-direction ledger entries ───────────────
        # Note: for partial refunds we use ``post_payment`` with
        # accounts swapped, NOT ``LedgerService.reverse`` (which would
        # reverse the *original amount*, wrong for partial refunds).
        merchant_account = self._merchant_account_for(parent.merchant_id)
        await self._ledger_service.post_payment(
            transaction_id=refund_txn.transaction_id,
            debit_account_id=merchant_account,            # merchant gives money back
            credit_account_id=sender_wallet.wallet_id,    # sender receives it
            amount=amount,
            currency=parent.currency,
        )

        # ─── 7. Mark success ────────────────────────────────────────
        self._assert_transition(TxnStatus.pending, TxnStatus.success)
        succeeded = await self._payment_repo.update_status(
            refund_txn.transaction_id,
            TxnStatus.success,
        )
        log.info(
            "refund_succeeded",
            refund_transaction_id=str(succeeded.transaction_id),
            parent_transaction_id=str(parent_transaction_id),
            amount=str(amount),
        )
        return succeeded

    # ── Queries ──────────────────────────────────────────────────────

    async def find_transaction(
        self,
        transaction_id: uuid.UUID,
        *,
        with_user: bool = False,
        with_parent: bool = False,
        with_refunds: bool = False,
    ) -> Transaction | None:
        """Fetch a transaction by ID; ``None`` if absent."""
        return await self._payment_repo.get_by_id(
            transaction_id,
            with_user=with_user,
            with_parent=with_parent,
            with_refunds=with_refunds,
        )

    async def get_transaction(
        self,
        transaction_id: uuid.UUID,
        *,
        with_user: bool = False,
        with_parent: bool = False,
        with_refunds: bool = False,
    ) -> Transaction:
        """Fetch a transaction by ID; raises if absent."""
        txn = await self.find_transaction(
            transaction_id,
            with_user=with_user,
            with_parent=with_parent,
            with_refunds=with_refunds,
        )
        if txn is None:
            raise TransactionNotFoundError(transaction_id)
        return txn

    # ── Validation helpers ───────────────────────────────────────────

    @staticmethod
    def _validate_amount(amount: Decimal) -> None:
        if not isinstance(amount, Decimal):
            raise TypeError(
                f"amount must be Decimal, got {type(amount).__name__}"
            )
        if amount <= _DECIMAL_ZERO:
            raise InvalidPaymentRequestError(
                f"amount must be > 0; got {amount}"
            )

    @staticmethod
    def _validate_currency(currency: str) -> None:
        if not isinstance(currency, str) or not _CURRENCY_RE.match(currency):
            raise InvalidPaymentRequestError(
                f"currency must be 3 uppercase letters (ISO 4217); got {currency!r}"
            )

    @staticmethod
    def _validate_merchant_id(merchant_id: str) -> None:
        if not isinstance(merchant_id, str) or not _MERCHANT_ID_RE.match(
            merchant_id
        ):
            raise InvalidPaymentRequestError(
                f"merchant_id must match {_MERCHANT_ID_RE.pattern!r}; "
                f"got {merchant_id!r}"
            )

    @staticmethod
    def _validate_idempotency_key(key: str) -> None:
        if not isinstance(key, str) or not _IDEMPOTENCY_KEY_RE.match(key):
            raise InvalidPaymentRequestError(
                f"idempotency_key must match {_IDEMPOTENCY_KEY_RE.pattern!r}; "
                f"got length={len(key)}"
            )

    @staticmethod
    def _assert_transition(current: TxnStatus, target: TxnStatus) -> None:
        allowed = _ALLOWED_TRANSITIONS.get(current, frozenset())
        if target not in allowed:
            raise InvalidStateTransitionError(current, target)

    @staticmethod
    def _merchant_account_for(merchant_id: str) -> uuid.UUID:
        """Derive a stable system-account UUID from ``merchant_id``.

        MVP shortcut — until per-merchant wallets land in the merchant
        domain, every merchant has a single suspense account whose ID
        is deterministically derived from its ``merchant_id``. The
        ledger ``account_id`` field is opaque (just a UUID), so this
        works transparently.
        """
        return uuid.uuid5(_MERCHANT_ACCOUNT_NAMESPACE, merchant_id)


__all__ = [
    "PaymentService",
    "PaymentServiceError",
    "InvalidPaymentRequestError",
    "InvalidStateTransitionError",
    "TransactionNotRefundableError",
    "OverRefundError",
    "MissingMerchantError",
    # Re-exports so route handlers have one import surface.
    "TransactionNotFoundError",
    "WalletNotFoundError",
    "InsufficientFundsError",
    "ContentionExceededError",
]
