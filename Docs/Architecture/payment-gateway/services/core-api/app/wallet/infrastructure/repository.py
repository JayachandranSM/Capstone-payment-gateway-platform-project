"""Repository for ``core.wallets``.

Design points worth stating up front:

- **No commits here.** Every method runs inside the ``AsyncSession``
  passed in at construction. The service layer owns commit/rollback
  boundaries (see PAYMENT_DOMAIN_DESIGN.md §1 — Unit of Work pattern).
- **Optimistic concurrency only.** Balance updates use a conditional
  ``UPDATE ... WHERE version = :expected_version`` with
  ``RETURNING version``. The DB ``CHECK (balance >= 0)`` constraint is
  the final safety net if the service ever produces a negative balance.
- **No pessimistic ``SELECT FOR UPDATE``.** Row-level locks are
  avoided to keep wallet-write contention low under bursty traffic;
  the service retries on ``OptimisticLockError`` (typically once is
  enough at our load).
- **No accidental N+1.** All relationships on ``Wallet`` use
  ``lazy="raise_on_sql"``. Callers that need the related ``User`` must
  pass ``with_user=True``.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Sequence

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.wallet.domain.models import Wallet

log = structlog.get_logger(__name__)


# ── Repository-layer exceptions ──────────────────────────────────────────


class WalletRepositoryError(Exception):
    """Base for wallet-repository errors."""


class WalletNotFoundError(WalletRepositoryError):
    """Raised when a wallet lookup must succeed but did not."""

    def __init__(self, wallet_id: uuid.UUID | str) -> None:
        super().__init__(f"wallet not found: {wallet_id}")
        self.wallet_id = wallet_id


class OptimisticLockError(WalletRepositoryError):
    """Raised when ``update_balance`` lost an optimistic-concurrency race.

    The service should re-read the wallet and retry. Hitting this more
    than 2–3 times in a row on the same wallet indicates a hot key and
    a higher-level redesign may be needed (e.g. account sharding).
    """

    def __init__(
        self,
        wallet_id: uuid.UUID,
        expected_version: int,
    ) -> None:
        super().__init__(
            f"wallet {wallet_id} version mismatch "
            f"(expected v={expected_version}); concurrent update — retry."
        )
        self.wallet_id = wallet_id
        self.expected_version = expected_version


# ── Repository ───────────────────────────────────────────────────────────


class WalletRepository:
    """Data-access adapter for the ``Wallet`` aggregate."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Writes ───────────────────────────────────────────────────────

    async def add(self, wallet: Wallet) -> Wallet:
        """Stage a new wallet for insertion and flush so server defaults populate.

        Does **not** commit. The service layer commits the unit of work.
        """
        self._session.add(wallet)
        # Flush only this one instance — minimises surprise side effects
        # if other dirty objects exist in the session.
        await self._session.flush([wallet])
        log.info(
            "wallet_added",
            wallet_id=str(wallet.wallet_id),
            user_id=str(wallet.user_id),
            currency=wallet.currency,
        )
        return wallet

    async def update_balance(
        self,
        wallet_id: uuid.UUID,
        new_balance: Decimal,
        expected_version: int,
    ) -> int:
        """Set the wallet's balance using optimistic concurrency control.

        Args:
            wallet_id:         Target wallet.
            new_balance:       Already-computed new balance. The service
                               is responsible for validating that this
                               doesn't violate non-negativity; the DB
                               ``CHECK`` is the safety net.
            expected_version:  Version observed at read time. The
                               update applies only if the row's current
                               version still matches.

        Returns:
            The new version number after a successful update.

        Raises:
            OptimisticLockError: when the version did not match.
                                 The service should re-read and retry.
        """
        if not isinstance(new_balance, Decimal):
            # Belt-and-braces — the Money column type also enforces this,
            # but failing here gives a clearer stack trace.
            raise TypeError(
                f"new_balance must be Decimal, got {type(new_balance).__name__}"
            )

        stmt = (
            update(Wallet)
            .where(Wallet.wallet_id == wallet_id)
            .where(Wallet.version == expected_version)
            .values(
                balance=new_balance,
                version=Wallet.version + 1,
            )
            # synchronize_session=False: the in-session Wallet instance
            # (if any) is not auto-refreshed. Cheaper, and we don't rely
            # on in-place mutation of the cached object.
            .execution_options(synchronize_session=False)
            .returning(Wallet.version)
        )
        result = await self._session.execute(stmt)
        new_version = result.scalar_one_or_none()
        if new_version is None:
            # Two indistinguishable cases collapse to "retry": wallet
            # absent OR version mismatch. Either way the service must
            # re-read; surfacing both as OptimisticLockError keeps the
            # caller path simple.
            log.warning(
                "wallet_update_lost_race",
                wallet_id=str(wallet_id),
                expected_version=expected_version,
            )
            raise OptimisticLockError(wallet_id, expected_version)

        log.debug(
            "wallet_balance_updated",
            wallet_id=str(wallet_id),
            new_version=new_version,
        )
        return new_version

    # ── Reads ────────────────────────────────────────────────────────

    async def get_by_id(
        self,
        wallet_id: uuid.UUID,
        *,
        with_user: bool = False,
    ) -> Wallet | None:
        """Look up by primary key; ``None`` if absent.

        Set ``with_user=True`` to eager-load the owning ``User``
        (otherwise the relationship raises on access per the model's
        ``lazy="raise_on_sql"`` policy).
        """
        stmt = select(Wallet).where(Wallet.wallet_id == wallet_id)
        if with_user:
            stmt = stmt.options(selectinload(Wallet.user))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user_currency(
        self,
        user_id: uuid.UUID,
        currency: str,
    ) -> Wallet | None:
        """Look up by the ``(user_id, currency)`` UNIQUE pair.

        Used on the hot path of payment creation to fetch the sender's
        wallet for a given currency.
        """
        stmt = (
            select(Wallet)
            .where(Wallet.user_id == user_id)
            .where(Wallet.currency == currency)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[Wallet]:
        """Return every wallet a user owns, ordered by currency code."""
        stmt = (
            select(Wallet)
            .where(Wallet.user_id == user_id)
            .order_by(Wallet.currency.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()


__all__ = [
    "WalletRepository",
    "WalletRepositoryError",
    "WalletNotFoundError",
    "OptimisticLockError",
]
