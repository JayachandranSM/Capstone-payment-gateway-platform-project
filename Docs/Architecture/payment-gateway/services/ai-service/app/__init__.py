"""ai-service application package.

Bootstrap stage exposes only:
- health endpoints (incl. Azure OpenAI readiness check)
- DB + Redis pool wiring
- Lazy-initialised Azure OpenAI client

LangGraph agents, RAG pipeline, evaluation suite and prompts are added by
the implementing developer in later turns.
"""
