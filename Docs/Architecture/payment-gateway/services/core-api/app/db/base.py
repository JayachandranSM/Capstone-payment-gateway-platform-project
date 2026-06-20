"""SQLAlchemy declarative base with stable naming convention.

A consistent naming convention is mandatory because Alembic autogenerate
falls back to compiler-generated names for unnamed constraints/indexes,
and those names *change* between SQLAlchemy versions. We pin the names
once, here, so migration diffs stay readable forever.

Reference: SQLAlchemy docs — "Configuring a Naming Convention".
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Index / unique / check / FK / PK naming.
# %(column_0_label)s = first column with its schema-qualified table label.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# A single MetaData instance is shared by all models so Alembic sees one
# graph. Schemas (`core`, `ledger`, `ops`, `ai`) are declared per-table
# via __table_args__.
metadata: MetaData = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Common declarative base for all ORM models in core-api."""

    metadata = metadata


__all__ = ["Base", "metadata", "NAMING_CONVENTION"]
