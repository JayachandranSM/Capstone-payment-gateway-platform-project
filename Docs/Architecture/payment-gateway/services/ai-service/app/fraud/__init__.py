"""fraud — AI-powered fraud scoring package.

Public surface:
    router      — FastAPI APIRouter; mount in main.py
    FraudScoringService
    FraudScoreRequest, FraudScoreResponse
    FraudDecision, RuleCategory
"""

from app.fraud.routes import router
from app.fraud.schemas import (
    FraudDecision,
    FraudScoreRequest,
    FraudScoreResponse,
    RuleCategory,
)
from app.fraud.service import FraudScoringService

__all__ = [
    "router",
    "FraudScoringService",
    "FraudScoreRequest",
    "FraudScoreResponse",
    "FraudDecision",
    "RuleCategory",
]
