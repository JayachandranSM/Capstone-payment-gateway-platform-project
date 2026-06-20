"""Reusable column mixins.

SQLAlchemy 2.0 supports ``mapped_column`` on mixins; the declarative
machinery picks them up at class-construction time and treats them as
real columns on the inheriting class.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import UTCDateTime


class TimestampMixin:
    """Adds ``created_at`` and ``updated_at``.

    Both default server-side to ``now()``; ``updated_at`` also updates
    on UPDATE via ``onupdate``. Server-side defaults are intentional —
    they apply even when a row is inserted via raw SQL (admin tools,
    Alembic data migrations).
    """

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        server_default=func.now(),
        doc="Row creation time (UTC).",
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        doc="Last update time (UTC).",
    )


__all__ = ["TimestampMixin"]
