"""payment domain package.

Public surface is intentionally narrow. Other packages must import only
from this module — never from payment.domain.models or
payment.infrastructure.* directly. This rule is enforced by code
review (and by the pre-commit hook documented in DECISIONS.md ADR-008).
"""
