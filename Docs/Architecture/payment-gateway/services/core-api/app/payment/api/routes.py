"""FastAPI router for the payment domain.

Endpoints
---------
POST   /v1/payments                 Create a payment
GET    /v1/payments/{transaction_id} Fetch one transaction
GET    /v1/payments                 List / search transactions (keyset-paginated)

Wiring into the application
----------------------------
Add to ``main.py`` after imports::

    from app.payment.api import router as payment_router
    app.include_router(payment_router)

Session lifecycle
-----------------
Every endpoint receives an ``AsyncSession`` via ``Depends(get_session)``.
The session dependency yields, auto-rolls-back on exception, and closes
on exit — but it does **not** commit. This router owns the commit, which
is called only on the happy path. On any business exception we skip the
commit (the session will be rolled back by the dependency's ``finally``
block) and return the appropriate error response.

The one exception to "always commit on happy path" is
``InsufficientFundsError``: the service marks the transaction
``failed`` and returns it (rather than raising), so the caller gets a
200-family response with ``status="failed"`` and still commits the
failed-transaction row — that row is an audit artefact we want to keep.

Idempotency
-----------
The preferred delivery channel for the idempotency key is the
``Idempotency-Key`` header. The request body also accepts it for
clients that cannot set custom headers. If both are present they must
match; a mismatch is rejected with 422.

Error mapping
-------------
All errors use ``application/problem+json`` (RFC 7807). The ``type``
URIs are namespace placeholders for MVP; they resolve to docs in
production.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.payment.infrastructure.repository import PaymentRepository
from app.payment.api.schemas import (
    CreatePaymentRequest,
    PagedTransactionResponse,
    ProblemDetail,
    TransactionResponse,
    decode_cursor,
    encode_cursor,
)
from app.payment.application.service import (
    ContentionExceededError,
    InvalidPaymentRequestError,
    MissingMerchantError,
    OverRefundError,
    PaymentService,
    TransactionNotFoundError,
    TransactionNotRefundableError,
)
from app.db import PaymentMethod, TxnStatus
from app.wallet.application.service import (
    InsufficientFundsError,
    WalletNotFoundError,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/payments", tags=["payments"])

# ── Dependency: PaymentService ────────────────────────────────────────────

# Using Annotated + Depends keeps the signature clean and makes the
# dependency discoverable in the OpenAPI schema.
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _build_service(session: AsyncSession) -> PaymentService:
    """Compose the PaymentService with all its collaborators.

    Instantiated per-request so every request gets its own service
    tree bound to its own ``AsyncSession``. There is no shared mutable
    state across requests.
    """
    return PaymentService(session)


# ── Problem-detail helpers ────────────────────────────────────────────────

_BASE_TYPE = "https://errors.paymentgateway.local"


def _problem(
    status_code: int,
    slug: str,
    title: str,
    detail: str,
    *,
    instance: str | None = None,
    errors: list[dict[str, str]] | None = None,
) -> JSONResponse:
    """Build an RFC 7807 ``application/problem+json`` response."""
    body = ProblemDetail(
        type=f"{_BASE_TYPE}/{slug}",
        title=title,
        status=status_code,
        detail=detail,
        instance=instance,
        errors=(
            [{"field": e["field"], "message": e["message"]} for e in errors]
            if errors
            else None
        ),
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(exclude_none=True),
        media_type="application/problem+json",
    )


def _resolve_idempotency_key(
    header_key: str | None,
    body_key: str | None,
    *,
    request_path: str,
) -> str | None:
    """Merge the header and body idempotency keys.

    Policy:
    - Header takes precedence.
    - If both are present and differ → 422.
    - If neither → ``None`` (no idempotency guarantee).
    """
    if header_key and body_key and header_key != body_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Idempotency-Key header and body.idempotency_key are both present "
                "but do not match. Use only the header."
            ),
        )
    return header_key or body_key or None


# ── POST /v1/payments ─────────────────────────────────────────────────────


@router.post(
    "",
    summary="Create a payment",
    status_code=status.HTTP_201_CREATED,
    response_model=TransactionResponse,
    responses={
        200: {
            "description": "Idempotent replay — transaction already existed.",
            "model": TransactionResponse,
        },
        202: {
            "description": "Payment flagged for fraud review.",
            "model": TransactionResponse,
        },
        404: {"description": "Sender wallet not found.", "model": ProblemDetail},
        409: {"description": "Idempotency key conflict (in-flight).", "model": ProblemDetail},
        422: {"description": "Validation error.", "model": ProblemDetail},
        503: {"description": "Wallet under contention — retry.", "model": ProblemDetail},
    },
)
async def create_payment(
    body: CreatePaymentRequest,
    session: SessionDep,
    request: Request,
    idempotency_key_header: Annotated[
        str | None,
        Header(alias="Idempotency-Key", description="Client-chosen UUID for idempotency."),
    ] = None,
) -> Any:
    """Create a peer-to-merchant payment.

    On the happy path returns **201** with ``status="success"``.

    Returns **200** (not 201) when the exact same ``Idempotency-Key``
    + merchant has already produced a transaction (idempotent replay).
    The ``Idempotent-Replay: true`` response header is set so callers
    can distinguish a replay from a fresh creation.

    Returns **202** when the fraud score causes the transaction to be
    held in ``status="flagged"`` pending manual review.

    Returns the transaction with ``status="failed"`` (still **200**)
    if the sender has insufficient funds — the failed-transaction row
    is an audit record and a valid response to the client.
    """
    idempotency_key = _resolve_idempotency_key(
        idempotency_key_header,
        body.idempotency_key,
        request_path=str(request.url),
    )

    # Bind trace context for every log line this request emits.
    bound_log = log.bind(
        user_id=str(body.user_id),
        merchant_id=body.merchant_id,
        amount=str(body.amount),
        currency=body.currency,
        idempotency_key=idempotency_key,
    )

    service = _build_service(session)

    # ── Idempotency pre-check ─────────────────────────────────────────────
    # Check the repository for an existing (merchant_id, idempotency_key)
    # row BEFORE calling create_payment. Avoids the flawed status-heuristic
    # approach and handles re-deliveries cleanly.
    if idempotency_key is not None:
        _existing = await PaymentRepository(session).find_by_idempotency_key(
            body.merchant_id, idempotency_key
        )
        if _existing is not None:
            bound_log.info(
                "payment_idempotent_replay",
                transaction_id=str(_existing.transaction_id),
                cached_status=_existing.status.value,
            )
            _resp_data = TransactionResponse.model_validate(_existing, from_attributes=False)
            _replay = JSONResponse(
                status_code=status.HTTP_200_OK,
                content=_resp_data.model_dump(mode="json"),
            )
            _replay.headers["Idempotent-Replay"] = "true"
            return _replay

    try:
        txn = await service.create_payment(
            user_id=body.user_id,
            merchant_id=body.merchant_id,
            amount=body.amount,
            currency=body.currency,
            payment_method=body.payment_method,
            idempotency_key=idempotency_key,
            metadata=body.metadata,
        )
    except InvalidPaymentRequestError as e:
        bound_log.warning("payment_create_validation_failed", detail=str(e))
        return _problem(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "validation-failed",
            "Validation Failed",
            str(e),
            instance=str(request.url),
        )
    except WalletNotFoundError as e:
        bound_log.warning("payment_create_wallet_missing", detail=str(e))
        return _problem(
            status.HTTP_404_NOT_FOUND,
            "wallet-not-found",
            "Sender Wallet Not Found",
            f"No wallet found for the given user and currency. {e}",
            instance=str(request.url),
        )
    except ContentionExceededError as e:
        # Wallet write was retried max_retries times. A failed-status
        # transaction row may or may not have been written — commit
        # regardless so the audit trail is not lost.
        bound_log.error(
            "payment_create_contention",
            wallet_id=str(e.wallet_id),
            attempts=e.attempts,
        )
        await session.commit()
        return _problem(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "wallet-contention",
            "Wallet Under Heavy Load",
            "The sender's wallet is experiencing high contention. "
            "Wait a moment and retry the request.",
            instance=str(request.url),
        )
    except Exception as e:
        # Unexpected error — roll back and surface with trace_id.
        bound_log.exception("payment_create_unexpected", exc_info=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred. Please quote the trace-id when reporting.",
        )

    # ── Commit and build response ─────────────────────────────────────────
    await session.commit()
    bound_log.info(
        "payment_create_committed",
        transaction_id=str(txn.transaction_id),
        status=txn.status.value,
    )

    response_data = TransactionResponse.model_validate(txn, from_attributes=False)

    if txn.status == TxnStatus.flagged:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=response_data.model_dump(mode="json"),
        )

    # Normal success (including status="failed" for insufficient-funds):
    # the service returns a committed transaction, status 201.
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=response_data.model_dump(mode="json"),
    )


# ── GET /v1/payments/{transaction_id} ─────────────────────────────────────


@router.get(
    "/{transaction_id}",
    summary="Get a transaction by ID",
    response_model=TransactionResponse,
    responses={
        404: {"description": "Transaction not found.", "model": ProblemDetail},
    },
)
async def get_payment(
    transaction_id: uuid.UUID,
    session: SessionDep,
    request: Request,
) -> Any:
    """Fetch a single transaction by its ``transaction_id``.

    Returns **404** for both genuinely absent transactions and for
    transactions the caller is not authorised to view — this avoids
    enumeration attacks (an attacker cannot distinguish "not found"
    from "not yours").

    Authorization is enforced via JWT scope in the auth middleware
    (future phase). For MVP, any authenticated caller can read any
    transaction.
    """
    service = _build_service(session)
    txn = await service.find_transaction(transaction_id)

    if txn is None:
        return _problem(
            status.HTTP_404_NOT_FOUND,
            "not-found",
            "Transaction Not Found",
            f"No transaction with id {transaction_id} was found "
            "or you are not authorised to view it.",
            instance=str(request.url),
        )

    # Read-only — no commit needed.
    return TransactionResponse.model_validate(txn, from_attributes=False)


# ── GET /v1/payments ──────────────────────────────────────────────────────


@router.get(
    "",
    summary="List transactions",
    response_model=PagedTransactionResponse,
    responses={
        400: {"description": "Invalid cursor or filter.", "model": ProblemDetail},
        422: {"description": "Validation error.", "model": ProblemDetail},
    },
)
async def list_payments(
    session: SessionDep,
    request: Request,
    # ── Scope filters (at least one required in production; open for MVP) ──
    merchant_id: Annotated[
        str | None,
        Query(description="Filter by merchant. Must match m_<alphanumeric>."),
    ] = None,
    user_id: Annotated[
        uuid.UUID | None,
        Query(description="Filter by paying user."),
    ] = None,
    # ── Status filter ───────────────────────────────────────────────────────
    status_filter: Annotated[
        TxnStatus | None,
        Query(alias="status", description="Filter by transaction status."),
    ] = None,
    # ── Date range ──────────────────────────────────────────────────────────
    from_date: Annotated[
        str | None,
        Query(
            alias="from",
            description="ISO-8601 UTC lower bound (inclusive), e.g. 2024-01-01T00:00:00Z.",
        ),
    ] = None,
    to_date: Annotated[
        str | None,
        Query(
            alias="to",
            description="ISO-8601 UTC upper bound (exclusive), e.g. 2024-02-01T00:00:00Z.",
        ),
    ] = None,
    # ── Pagination ──────────────────────────────────────────────────────────
    cursor: Annotated[
        str | None,
        Query(description="Opaque base64url cursor from a previous response's ``next_cursor``."),
    ] = None,
    limit: Annotated[
        int,
        Query(ge=1, le=200, description="Page size (default 50, max 200)."),
    ] = 50,
) -> Any:
    """List transactions with optional filters and keyset pagination.

    **Scope:** pass ``merchant_id`` to list a merchant's transactions,
    ``user_id`` to list a user's transactions. Passing both returns
    transactions for that user at that merchant. Passing neither
    returns all transactions (restricted to admins in production).

    **Pagination:** the response includes a ``next_cursor`` field. Pass
    it as ``?cursor=<value>`` on the next request to get the following
    page. A ``null`` ``next_cursor`` means you have reached the last
    page. Cursors are opaque — do not parse or construct them.

    **Date filters:** ``from`` and ``to`` accept ISO-8601 strings
    (with or without timezone; timezone-naive strings are assumed UTC).
    ``to`` is exclusive: ``from=2024-01-01&to=2024-02-01`` returns
    January 2024.
    """
    # ── Parse dates ───────────────────────────────────────────────────────
    from datetime import datetime, timezone as tz

    parsed_from: datetime | None = None
    parsed_to: datetime | None = None

    for name, raw in [("from", from_date), ("to", to_date)]:
        if raw is not None:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=tz.utc)
                if name == "from":
                    parsed_from = dt
                else:
                    parsed_to = dt
            except ValueError:
                return _problem(
                    status.HTTP_400_BAD_REQUEST,
                    "invalid-date",
                    "Invalid Date Parameter",
                    f"Could not parse ?{name}={raw!r}. "
                    "Expected ISO-8601, e.g. 2024-01-01T00:00:00Z.",
                    instance=str(request.url),
                )

    # ── Decode cursor ─────────────────────────────────────────────────────
    cursor_created_at: datetime | None = None
    cursor_transaction_id: uuid.UUID | None = None

    if cursor is not None:
        try:
            cursor_created_at, cursor_transaction_id = decode_cursor(cursor)
        except ValueError:
            return _problem(
                status.HTTP_400_BAD_REQUEST,
                "invalid-cursor",
                "Invalid Cursor",
                "The cursor value is malformed or expired. "
                "Re-fetch the first page without a cursor.",
                instance=str(request.url),
            )

    # ── Require at least one scope filter ────────────────────────────────
    # In production this gates on the JWT scope. For MVP we permit
    # open listing but log a warning so it's visible in ops.
    if merchant_id is None and user_id is None:
        log.warning(
            "list_payments_no_scope_filter",
            path=str(request.url),
            note="Open list is permitted in MVP but must be restricted by JWT in production.",
        )

    # ── Delegate to PaymentService ────────────────────────────────────────
    service = _build_service(session)

    try:
        if merchant_id is not None:
            rows = await service._payment_repo.list_for_merchant(
                merchant_id,
                cursor_created_at=cursor_created_at,
                cursor_transaction_id=cursor_transaction_id,
                limit=limit,
                status=status_filter,
                from_date=parsed_from,
                to_date=parsed_to,
            )
        elif user_id is not None:
            rows = await service._payment_repo.list_for_user(
                user_id,
                cursor_created_at=cursor_created_at,
                cursor_transaction_id=cursor_transaction_id,
                limit=limit,
                status=status_filter,
                from_date=parsed_from,
                to_date=parsed_to,
            )
        else:
            # No scope filter — return empty for now rather than a full-table
            # scan. A production admin-scoped list endpoint is a separate route.
            rows = []
    except ValueError as e:
        return _problem(
            status.HTTP_400_BAD_REQUEST,
            "invalid-filter",
            "Invalid Filter",
            str(e),
            instance=str(request.url),
        )

    # ── Build next_cursor ─────────────────────────────────────────────────
    # If the page is exactly ``limit`` rows, there may be a next page.
    # The cursor points at the last row in this page.
    next_cursor: str | None = None
    if len(rows) == limit:
        last = rows[-1]
        if last.created_at is not None and last.transaction_id is not None:
            next_cursor = encode_cursor(last.created_at, last.transaction_id)

    items = [TransactionResponse.model_validate(row, from_attributes=False) for row in rows]

    return PagedTransactionResponse(
        items=items,
        next_cursor=next_cursor,
        count=len(items),
    )


__all__ = ["router"]
