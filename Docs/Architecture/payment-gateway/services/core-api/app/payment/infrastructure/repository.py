"""Repository for ``core.transactions``.

Surface area:

- ``add`` — INSERT one transaction; returns the persisted instance.
- ``get_by_id`` — primary-key lookup with opt-in relationship loading.
- ``find_by_idempotency_key`` — DB fallback for the two-key idempotency
  strategy (Redis is the fast path; this is the durable source of truth).
- ``list_for_merchant`` / ``list_for_user`` — keyset-paginated history.
- ``update_status`` — partial UPDATE for state-machine transitions; the
  service is responsible for validating the transition before calling.
- ``sum_refunded_amount`` — SUM over child refunds; used to reject
  over-refund attempts at the service layer.
- ``list_refunds`` — direct children of a transaction.

What this class deliberately does *not* do:

- It does not validate state-machine transitions. The
  ``app.payment.domain.state_machine`` module (future) is the single
  source of truth there.
- It does not commit. The service layer owns the unit-of-work boundary.
- It does not call out to fraud / ledger / providers. Those are
  orchestrated by ``app.payment.application.service``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Sequence

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.db import TxnStatus
from app.payment.domain.models import Transaction

log = structlog.get_logger(__name__)


# ── Repository-layer exceptions ──────────────────────────────────────────


class PaymentRepositoryError(Exception):
    """Base for payment-repository errors."""


class TransactionNotFoundError(PaymentRepositoryError):
    """Raised when an UPDATE finds zero rows."""

    def __init__(self, transaction_id: uuid.UUID) -> None:
        super().__init__(f"transaction not found: {transaction_id}")
        self.transaction_id = transaction_id


# ── Repository ───────────────────────────────────────────────────────────


# Hard cap on pagination so a buggy/malicious caller can't ask for 10 M rows.
_MAX_PAGE_SIZE: int = 200


class PaymentRepository:
    """Data-access adapter for the ``Transaction`` aggregate."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Writes ───────────────────────────────────────────────────────

    async def add(self, transaction: Transaction) -> Transaction:
        """Stage and flush a new transaction.

        The UNIQUE constraint on ``(merchant_id, idempotency_key)``
        guarantees that two concurrent writes with the same key cannot
        both succeed. The losing INSERT surfaces as ``IntegrityError``
        with the constraint name in the message — the service layer
        translates that to a 409 / idempotency-conflict response.

        Caller must NOT pre-commit; the service owns the commit.
        """
        self._session.add(transaction)
        await self._session.flush([transaction])
        log.info(
            "transaction_added",
            transaction_id=str(transaction.transaction_id),
            merchant_id=transaction.merchant_id,
            amount=str(transaction.amount),
            currency=transaction.currency,
            idempotency_key=transaction.idempotency_key,
        )
        return transaction

    async def update_status(
        self,
        transaction_id: uuid.UUID,
        new_status: TxnStatus,
        *,
        failure_reason: str | None = None,
        fraud_score: Decimal | None = None,
        resolution_notes: str | None = None,
    ) -> Transaction:
        """Apply a state-machine transition.

        Only the columns relevant to the transition are set. Callers
        pass ``None`` for anything they don't intend to change — those
        columns are *not* overwritten (vs. the alternative of writing
        ``None`` and clobbering an existing value).

        Args:
            transaction_id:   PK of the row to update.
            new_status:       Target status. Caller must have validated
                              the transition is legal.
            failure_reason:   Set when transitioning to ``failed``.
            fraud_score:      Set when transitioning to ``flagged``.
            resolution_notes: Set on terminal transitions for audit.

        Returns:
            The refreshed ``Transaction`` instance.

        Raises:
            TransactionNotFoundError: if no row matched.
        """
        values: dict[str, object] = {"status": new_status}
        if failure_reason is not None:
            values["failure_reason"] = failure_reason
        if fraud_score is not None:
            values["fraud_score"] = fraud_score
        if resolution_notes is not None:
            values["resolution_notes"] = resolution_notes

        stmt = (
            update(Transaction)
            .where(Transaction.transaction_id == transaction_id)
            .values(**values)
            .execution_options(synchronize_session=False)
            .returning(Transaction)
        )
        result = await self._session.execute(stmt)
        # .returning(Transaction) emits a SELECT-like row materialised as
        # the ORM model; scalar_one_or_none gives us the Transaction.
        updated = result.scalar_one_or_none()
        if updated is None:
            raise TransactionNotFoundError(transaction_id)

        log.info(
            "transaction_status_updated",
            transaction_id=str(transaction_id),
            new_status=new_status.value,
        )
        return updated

    # ── Reads ────────────────────────────────────────────────────────

    async def get_by_id(
        self,
        transaction_id: uuid.UUID,
        *,
        with_user: bool = False,
        with_parent: bool = False,
        with_refunds: bool = False,
    ) -> Transaction | None:
        """Primary-key lookup; ``None`` if absent.

        Eager-load opt-ins are explicit because the model declares
        ``lazy="raise_on_sql"`` on every relationship — touching a
        relation that wasn't loaded raises at runtime. This forces
        N+1 bugs to surface during development.
        """
        stmt = select(Transaction).where(Transaction.transaction_id == transaction_id)
        if with_user:
            stmt = stmt.options(selectinload(Transaction.user))
        if with_parent:
            # joinedload for parent: it's a single row, JOIN is cheaper
            # than a separate SELECT.
            stmt = stmt.options(joinedload(Transaction.parent))
        if with_refunds:
            # selectinload for refunds: avoids row multiplication that
            # joinedload would cause with a one-to-many.
            stmt = stmt.options(selectinload(Transaction.refunds))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_idempotency_key(
        self,
        merchant_id: str,
        idempotency_key: str,
    ) -> Transaction | None:
        """Durable-fallback lookup for the two-key idempotency strategy.

        The Redis ``idem:resp:*`` cache is the fast path. When Redis
        evicts the response (e.g. after a restart), the idempotency
        middleware falls back to this lookup. Returning the existing
        transaction lets the middleware re-construct the prior
        response from the row.

        Uniqueness is scoped per merchant by the table-level
        ``UNIQUE (merchant_id, idempotency_key)`` constraint — same key
        from two different merchants is not a conflict.
        """
        stmt = (
            select(Transaction)
            .where(Transaction.merchant_id == merchant_id)
            .where(Transaction.idempotency_key == idempotency_key)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_merchant(
        self,
        merchant_id: str,
        *,
        cursor_created_at: datetime | None = None,
        cursor_transaction_id: uuid.UUID | None = None,
        limit: int = 50,
        status: TxnStatus | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> Sequence[Transaction]:
        """Merchant transaction history with optional filters and keyset pagination.

        Pagination strategy (per PAYMENT_DOMAIN_DESIGN.md §6 conventions):
        cursor is the ``(created_at, transaction_id)`` of the *last*
        row of the previous page. The next page returns rows strictly
        older. ``transaction_id`` breaks timestamp ties.
        """
        if limit <= 0 or limit > _MAX_PAGE_SIZE:
            raise ValueError(f"limit must be in [1, {_MAX_PAGE_SIZE}]; got {limit}")

        stmt = select(Transaction).where(Transaction.merchant_id == merchant_id)
        stmt = self._apply_common_filters(stmt, status, from_date, to_date)
        stmt = self._apply_keyset_cursor(stmt, cursor_created_at, cursor_transaction_id)
        stmt = (
            stmt.order_by(
                Transaction.created_at.desc(),
                Transaction.transaction_id.desc(),
            )
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        cursor_created_at: datetime | None = None,
        cursor_transaction_id: uuid.UUID | None = None,
        limit: int = 50,
        status: TxnStatus | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> Sequence[Transaction]:
        """User transaction history. Same pagination contract as ``list_for_merchant``."""
        if limit <= 0 or limit > _MAX_PAGE_SIZE:
            raise ValueError(f"limit must be in [1, {_MAX_PAGE_SIZE}]; got {limit}")

        stmt = select(Transaction).where(Transaction.user_id == user_id)
        stmt = self._apply_common_filters(stmt, status, from_date, to_date)
        stmt = self._apply_keyset_cursor(stmt, cursor_created_at, cursor_transaction_id)
        stmt = (
            stmt.order_by(
                Transaction.created_at.desc(),
                Transaction.transaction_id.desc(),
            )
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def list_refunds(self, parent_transaction_id: uuid.UUID) -> Sequence[Transaction]:
        """All refund children of ``parent_transaction_id``.

        Used by the service to render a refund timeline and to compute
        the over-refund check (also see ``sum_refunded_amount``).
        """
        stmt = (
            select(Transaction)
            .where(Transaction.parent_transaction == parent_transaction_id)
            .order_by(Transaction.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def sum_refunded_amount(self, parent_transaction_id: uuid.UUID) -> Decimal:
        """Return the total amount already refunded against a parent transaction.

        Only counts refunds that did not fail. The service uses this
        to reject over-refund attempts before issuing a new refund:

            already = await repo.sum_refunded_amount(original.transaction_id)
            if already + new_refund_amount > original.amount:
                raise OverRefundError(...)

        Returns ``Decimal('0')`` when no successful refunds exist.
        """
        stmt = (
            select(func.coalesce(func.sum(Transaction.amount), 0))
            .where(Transaction.parent_transaction == parent_transaction_id)
            .where(Transaction.status != TxnStatus.failed)
        )
        result = await self._session.execute(stmt)
        value = result.scalar_one()
        return Decimal(value) if not isinstance(value, Decimal) else value

    # ── Internal helpers ─────────────────────────────────────────────

    @staticmethod
    def _apply_common_filters(
        stmt,
        status: TxnStatus | None,
        from_date: datetime | None,
        to_date: datetime | None,
    ):
        """Common WHERE-clause additions shared by both list_* methods."""
        if status is not None:
            stmt = stmt.where(Transaction.status == status)
        if from_date is not None:
            stmt = stmt.where(Transaction.created_at >= from_date)
        if to_date is not None:
            # Exclusive upper bound — common API convention; clients
            # pass ``to_date = next_day`` for an inclusive feel.
            stmt = stmt.where(Transaction.created_at < to_date)
        return stmt

    @staticmethod
    def _apply_keyset_cursor(
        stmt,
        cursor_created_at: datetime | None,
        cursor_transaction_id: uuid.UUID | None,
    ):
        """Add the ``(created_at, transaction_id) < cursor`` predicate.

        Both cursor components must be set together; otherwise no
        cursor is applied (caller is on the first page).
        """
        if cursor_created_at is None or cursor_transaction_id is None:
            return stmt
        return stmt.where(
            (Transaction.created_at < cursor_created_at)
            | (
                (Transaction.created_at == cursor_created_at)
                & (Transaction.transaction_id < cursor_transaction_id)
            )
        )


__all__ = [
    "PaymentRepository",
    "PaymentRepositoryError",
    "TransactionNotFoundError",
]
