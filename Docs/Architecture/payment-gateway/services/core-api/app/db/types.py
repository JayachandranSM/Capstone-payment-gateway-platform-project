"""Custom SQLAlchemy column types enforcing financial-grade discipline.

Why custom types and not stock SQLAlchemy types:

- ``Money`` exists so that *no payment value* can ever be stored as
  ``float``. Floating-point arithmetic on money is a category error;
  this type makes the category error a runtime failure at the boundary
  rather than a bug discovered in production.

- ``UTCDateTime`` exists so that naive (no-tzinfo) datetimes cannot be
  stored. Mixing naive and aware datetimes is how every "Why is this
  off by 5h30m?" incident starts. We refuse naive at the type boundary.

Both types are ``TypeDecorator`` wrappers around stock SQL types, so
Alembic and SQLAlchemy treat them as ``NUMERIC(18, 4)`` and
``TIMESTAMP WITH TIME ZONE`` respectively at the SQL level.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Numeric
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class Money(TypeDecorator[Decimal]):
    """``NUMERIC(18, 4)`` that refuses ``float`` and ``int`` on bind.

    Accepted Python types on assignment: ``decimal.Decimal``, ``str``
    (parsed to ``Decimal``), and ``None``. Anything else raises
    ``TypeError`` before the query is sent to the database.

    On read, values are always ``Decimal`` thanks to ``asdecimal=True``.
    """

    impl = Numeric(18, 4, asdecimal=True)
    cache_ok = True
    python_type = Decimal

    def process_bind_param(self, value: Any, dialect: Dialect) -> Decimal | None:  # noqa: ARG002
        if value is None:
            return None
        if isinstance(value, bool):
            # bool is a subclass of int; reject explicitly to avoid surprise.
            raise TypeError(f"Money cannot accept bool: {value!r}")
        if isinstance(value, float):
            raise TypeError(
                f"Money values must be Decimal, not float (got {value!r}). "
                "Convert at the boundary with Decimal(str(value))."
            )
        if isinstance(value, int):
            # Integers are unambiguous; accept but normalise.
            return Decimal(value)
        if isinstance(value, str):
            return Decimal(value)
        if isinstance(value, Decimal):
            return value
        raise TypeError(
            f"Money expected Decimal/str/int/None, got {type(value).__name__}: {value!r}"
        )

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:  # noqa: ARG002
        # asdecimal=True on impl ensures asyncpg returns Decimal already.
        return value


class UTCDateTime(TypeDecorator[datetime]):
    """``TIMESTAMP WITH TIME ZONE`` that refuses naive datetimes on bind.

    On bind: rejects ``tzinfo is None``; converts to UTC if a non-UTC
    offset is supplied.

    On read: always returns a UTC-aware ``datetime`` (asyncpg returns
    tz-aware values for ``timestamptz`` already; we coerce defensively).
    """

    impl = DateTime(timezone=True)
    cache_ok = True
    python_type = datetime

    def process_bind_param(self, value: Any, dialect: Dialect) -> datetime | None:  # noqa: ARG002
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(
                f"UTCDateTime expected datetime, got {type(value).__name__}: {value!r}"
            )
        if value.tzinfo is None:
            raise ValueError(
                f"UTCDateTime refuses naive datetime {value!r}. "
                "Construct with datetime.now(timezone.utc) or attach a tzinfo first."
            )
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:  # noqa: ARG002
        if value is None:
            return None
        if value.tzinfo is None:
            # Defensive: should never happen for timestamptz, but be explicit.
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


__all__ = ["Money", "UTCDateTime"]
