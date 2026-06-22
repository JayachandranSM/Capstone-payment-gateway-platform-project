"""Application service for the Wallet aggregate.

Public surface:
- ``create_wallet``      — provision a new wallet for ``(user_id, currency)``
- ``credit``             — atomically add to balance (with optimistic retry)
- ``debit``              — atomically subtract (validates sufficient funds)
- ``get_wallet``         — fetch by ``wallet_id``; raises if absent
- ``find_for_user``      — look up by ``(user_id, currency)``; ``None`` if absent
- ``get_for_user``       — same, but raises
- ``list_for_user``      — every wallet a user owns

Concurrency model:
    Balance writes go through ``WalletRepository.update_balance``, which
    uses an optimistic ``UPDATE ... WHERE version = :expected`` with
    ``RETURNING``. The service wraps this in a bounded retry loop. If
    contention exceeds ``max_retries``, ``ContentionExceededError`` is
    raised so the caller can surface a 503 to the client rather than
    spin forever.

Transactional boundary:
    This service never calls ``session.commit()``. The caller (typically
    a route handler within a per-request transaction) owns commit /
    rollback. An exception propagating out of any method leaves the
    session in a state the caller can roll back.
"""

from __future__ import annotations

import enum
import re
import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.wallet.domain.models import Wallet
from app.wallet.infrastructure.repository import (
    OptimisticLockError,
    WalletNotFoundError,
    WalletRepository,
)

log = structlog.get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────────────

# ISO 4217 codes are always three uppercase letters. We validate the
# shape; full code-list membership is checked at the API boundary.
_CURRENCY_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{3}$")

# Default optimistic-lock retry budget. Higher values mask hot-key
# contention; lower values surface it as a 503 to the client faster.
_DEFAULT_MAX_RETRIES: Final[int] = 3

_DECIMAL_ZERO: Final[Decimal] = Decimal("0")


# ── Exceptions ───────────────────────────────────────────────────────────


class WalletServiceError(Exception):
    """Base for wallet-service-level errors."""


class InvalidAmountError(WalletServiceError):
    """An amount failed positivity / non-negativity validation."""

    def __init__(self, field: str, value: Decimal) -> None:
        super().__init__(f"{field} must be > 0; got {value}")
        self.field = field
        self.value = value


class InvalidCurrencyError(WalletServiceError):
    """A currency code failed the ISO 4217 shape check."""

    def __init__(self, currency: str) -> None:
        super().__init__(
            f"currency must be 3 uppercase letters (ISO 4217); got {currency!r}"
        )
        self.currency = currency


class InsufficientFundsError(WalletServiceError):
    """A debit was attempted that would push the balance negative."""

    def __init__(
        self,
        wallet_id: uuid.UUID,
        available: Decimal,
        required: Decimal,
    ) -> None:
        super().__init__(
            f"wallet {wallet_id}: available={available} required={required}"
        )
        self.wallet_id = wallet_id
        self.available = available
        self.required = required


class WalletAlreadyExistsError(WalletServiceError):
    """A wallet already exists for the given ``(user_id, currency)``."""

    def __init__(self, user_id: uuid.UUID, currency: str) -> None:
        super().__init__(
            f"wallet already exists for user={user_id} currency={currency}"
        )
        self.user_id = user_id
        self.currency = currency


class ContentionExceededError(WalletServiceError):
    """Optimistic-lock retry budget exhausted without success.

    Indicates a hot key — multiple concurrent updates targeting the
    same wallet. The caller should surface a 503 or 409 to the client;
    a higher-level design change (account sharding, queueing) is
    needed if this fires routinely.
    """

    def __init__(self, wallet_id: uuid.UUID, attempts: int) -> None:
        super().__init__(
            f"wallet {wallet_id}: contention not resolved after {attempts} attempts"
        )
        self.wallet_id = wallet_id
        self.attempts = attempts


# ── Internal helpers ─────────────────────────────────────────────────────


class _BalanceOp(enum.Enum):
    """Direction of an atomic balance change."""

    CREDIT = "credit"
    DEBIT = "debit"


# ── Service ──────────────────────────────────────────────────────────────


class WalletService:
    """Coordinates wallet reads, creations, and balance changes."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        wallet_repository: WalletRepository | None = None,
    ) -> None:
        self._session = session
        self._wallet_repo = wallet_repository or WalletRepository(session)

    # ── Reads ────────────────────────────────────────────────────────

    async def get_wallet(self, wallet_id: uuid.UUID) -> Wallet:
        """Fetch a wallet by ID. Raises if absent."""
        wallet = await self._wallet_repo.get_by_id(wallet_id)
        if wallet is None:
            raise WalletNotFoundError(wallet_id)
        return wallet

    async def find_for_user(
        self,
        user_id: uuid.UUID,
        currency: str,
    ) -> Wallet | None:
        """Look up by ``(user_id, currency)``. Returns ``None`` if absent."""
        self._validate_currency(currency)
        return await self._wallet_repo.get_by_user_currency(user_id, currency)

    async def get_for_user(
        self,
        user_id: uuid.UUID,
        currency: str,
    ) -> Wallet:
        """Look up by ``(user_id, currency)``. Raises if absent."""
        wallet = await self.find_for_user(user_id, currency)
        if wallet is None:
            raise WalletNotFoundError(f"user={user_id} currency={currency}")
        return wallet

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[Wallet]:
        """Return every wallet a user owns (ordered by currency)."""
        return await self._wallet_repo.list_for_user(user_id)

    # ── Writes ───────────────────────────────────────────────────────

    async def create_wallet(
        self,
        *,
        user_id: uuid.UUID,
        currency: str,
        initial_balance: Decimal = _DECIMAL_ZERO,
    ) -> Wallet:
        """Provision a new wallet for ``(user_id, currency)``.

        Raises:
            InvalidCurrencyError:        bad shape
            InvalidAmountError:          ``initial_balance < 0``
            WalletAlreadyExistsError:    pair already provisioned
        """
        self._validate_currency(currency)
        if initial_balance < _DECIMAL_ZERO:
            raise InvalidAmountError("initial_balance", initial_balance)

        existing = await self._wallet_repo.get_by_user_currency(user_id, currency)
        if existing is not None:
            log.warning(
                "wallet_create_duplicate",
                user_id=str(user_id),
                currency=currency,
                existing_wallet_id=str(existing.wallet_id),
            )
            raise WalletAlreadyExistsError(user_id, currency)

        wallet = Wallet(
            user_id=user_id,
            currency=currency,
            balance=initial_balance,
        )
        await self._wallet_repo.add(wallet)
        log.info(
            "wallet_created",
            wallet_id=str(wallet.wallet_id),
            user_id=str(user_id),
            currency=currency,
            initial_balance=str(initial_balance),
        )
        return wallet

    async def credit(
        self,
        wallet_id: uuid.UUID,
        amount: Decimal,
        *,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> Wallet:
        """Atomically add ``amount`` to the wallet's balance.

        Raises:
            InvalidAmountError, WalletNotFoundError, ContentionExceededError
        """
        self._validate_positive(amount, "amount")
        return await self._update_balance(
            wallet_id, _BalanceOp.CREDIT, amount, max_retries
        )

    async def debit(
        self,
        wallet_id: uuid.UUID,
        amount: Decimal,
        *,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> Wallet:
        """Atomically subtract ``amount`` from the wallet's balance.

        Validates sufficient funds before each attempt. The DB
        ``CHECK (balance >= 0)`` is the final safety net.

        Raises:
            InvalidAmountError, WalletNotFoundError,
            InsufficientFundsError, ContentionExceededError
        """
        self._validate_positive(amount, "amount")
        return await self._update_balance(
            wallet_id, _BalanceOp.DEBIT, amount, max_retries
        )

    # ── Internal balance-change machinery ────────────────────────────

    async def _update_balance(
        self,
        wallet_id: uuid.UUID,
        operation: _BalanceOp,
        amount: Decimal,
        max_retries: int,
    ) -> Wallet:
        """Bounded optimistic-retry loop shared by credit and debit."""
        if max_retries < 1:
            raise ValueError(f"max_retries must be >= 1; got {max_retries}")

        for attempt in range(1, max_retries + 1):
            wallet = await self._wallet_repo.get_by_id(wallet_id)
            if wallet is None:
                raise WalletNotFoundError(wallet_id)

            if operation is _BalanceOp.DEBIT:
                if wallet.balance < amount:
                    log.warning(
                        "wallet_insufficient_funds",
                        wallet_id=str(wallet_id),
                        available=str(wallet.balance),
                        required=str(amount),
                    )
                    raise InsufficientFundsError(
                        wallet_id, wallet.balance, amount
                    )
                new_balance = wallet.balance - amount
            else:  # CREDIT
                new_balance = wallet.balance + amount

            try:
                new_version = await self._wallet_repo.update_balance(
                    wallet_id, new_balance, wallet.version
                )
            except OptimisticLockError:
                log.info(
                    "wallet_optimistic_retry",
                    wallet_id=str(wallet_id),
                    operation=operation.value,
                    attempt=attempt,
                    max_retries=max_retries,
                )
                continue

            # Reflect the change on the in-memory instance for the caller.
            wallet.balance = new_balance
            wallet.version = new_version

            log.info(
                "wallet_balance_changed",
                wallet_id=str(wallet_id),
                operation=operation.value,
                amount=str(amount),
                new_balance=str(new_balance),
                new_version=new_version,
                attempts=attempt,
            )
            return wallet

        log.error(
            "wallet_contention_exceeded",
            wallet_id=str(wallet_id),
            operation=operation.value,
            attempts=max_retries,
        )
        raise ContentionExceededError(wallet_id, max_retries)

    # ── Validation helpers ───────────────────────────────────────────

    @staticmethod
    def _validate_positive(amount: Decimal, field: str) -> None:
        if not isinstance(amount, Decimal):
            raise TypeError(
                f"{field} must be Decimal, got {type(amount).__name__}"
            )
        if amount <= _DECIMAL_ZERO:
            raise InvalidAmountError(field, amount)

    @staticmethod
    def _validate_currency(currency: str) -> None:
        if not isinstance(currency, str) or not _CURRENCY_RE.match(currency):
            raise InvalidCurrencyError(currency)


__all__ = [
    "WalletService",
    "WalletServiceError",
    "InvalidAmountError",
    "InvalidCurrencyError",
    "InsufficientFundsError",
    "WalletAlreadyExistsError",
    "ContentionExceededError",
    # Re-export so callers don't have to dig into the repository module.
    "WalletNotFoundError",
]
