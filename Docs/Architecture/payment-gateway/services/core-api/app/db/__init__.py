"""Database infrastructure shared across domain packages.

Public surface:
    Base                 — DeclarativeBase to inherit ORM models from
    Money, UTCDateTime   — column types enforcing financial-grade discipline
    TimestampMixin       — created_at / updated_at mixin
    PaymentMethod, TxnStatus, SettleStatus, KycStatus, LedgerDirection
                         — PostgreSQL ENUM-backed Python enums
    create_engine_and_sessionmaker, get_session
                         — async engine factory + FastAPI dependency
"""

from app.db.base import Base, metadata
from app.db.enums import (
    KycStatus,
    LedgerDirection,
    PaymentMethod,
    SettleStatus,
    TxnStatus,
)
from app.db.mixins import TimestampMixin
from app.db.session import (
    create_engine_and_sessionmaker,
    dispose_engine,
    get_session,
)
from app.db.types import Money, UTCDateTime

__all__ = [
    "Base",
    "metadata",
    "Money",
    "UTCDateTime",
    "TimestampMixin",
    "PaymentMethod",
    "TxnStatus",
    "SettleStatus",
    "KycStatus",
    "LedgerDirection",
    "create_engine_and_sessionmaker",
    "dispose_engine",
    "get_session",
]

# Import ORM models so SQLAlchemy relationship strings resolve.
# These imports are intentionally unused.
from app.identity.domain.models import User  # noqa: F401
from app.wallet.domain.models import Wallet  # noqa: F401
from app.payment.domain.models import Transaction  # noqa: F401
from app.ledger.domain.models import LedgerEntry  # noqa: F401
