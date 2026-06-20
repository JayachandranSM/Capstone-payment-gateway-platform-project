"""ledger application layer — orchestration over the domain.

This layer:
- composes repositories (and, for the payment package, other services)
- enforces business invariants and state-machine transitions
- never commits — commit is owned by the route handler (unit of work)
- never imports FastAPI, HTTP types, or any framework primitives
"""
