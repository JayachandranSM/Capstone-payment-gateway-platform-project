"""Payment API layer — Pydantic schemas and FastAPI router.

Public surface:
    router      — APIRouter with all payment endpoints; mount in main.py
    schemas     — request / response models for external import if needed
"""

from app.payment.api.routes import router

__all__ = ["router"]
