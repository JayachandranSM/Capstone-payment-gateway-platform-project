"""rag — Knowledge-base retrieval package.

Public surface:
    router          — FastAPI APIRouter; mount in main.py
    RAGService      — retrieval orchestrator
    RAGQueryRequest, RAGQueryResponse
    KnowledgeChunk, KnowledgeCategory, SearchMode
"""

from app.rag.routes import router
from app.rag.schemas import (
    KnowledgeCategory,
    KnowledgeChunk,
    RAGQueryRequest,
    RAGQueryResponse,
    SearchMode,
)
from app.rag.service import RAGService

__all__ = [
    "router",
    "RAGService",
    "RAGQueryRequest",
    "RAGQueryResponse",
    "KnowledgeChunk",
    "KnowledgeCategory",
    "SearchMode",
]
